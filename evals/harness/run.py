"""The eval CLI: run a mode over the corpus, or calibrate the judge.

Gating is **Tier 1 structural only**. A report that does not parse, whose
references dangle, whose severity bands contradict the matrix or whose summary
disagrees with its own contents fails the run. Must-find recall is computed,
printed and written to the artifact, and deliberately **does not block** until
baseline sweeps have established its normal range: a gate that fires before
anyone knows that range trains people to bypass it.

Everything the run learned lands in one JSON artifact: every judge ruling with
its rationale, every bucket decision, the severity confusion, the near/far
exemplar delta, and the ``valid-unlisted`` threats queued for the SME's next
blessing pass. The metrics are judge-relative — track movement with them, never
quote them as absolutes.

Both subcommands — ``run`` and ``calibrate`` — need live provider credentials,
so neither runs on a PR. The credential-free lane is ``evals/verify_corpus.py``,
which is what CI exercises; the live sweep runs on the weekly schedule in
``.github/workflows/evals-live.yml``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.harness import modes
from evals.harness.calibration import AGREEMENT_BAR, load_pairs, measure_agreement
from evals.harness.critic_yield import (
    CriticYield,
    aggregate_yield,
    score_case_with_yield,
)
from evals.harness.judge import Judge, PinnedJudge, load_judge_config
from evals.harness.reference import GoldenCase, load_corpus
from evals.harness.scorer import (
    CaseScore,
    exemplar_delta,
    unlisted_for_promotion,
)
from evals.harness.structural import report_issues
from stride_service.certification import CertifyResult, certify, fingerprints_of
from stride_service.deployment import Deployment
from stride_service.report import NodeRun

EVALS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS_DIR = EVALS_ROOT / "corpus"


def _live_judge(deployment: Deployment) -> PinnedJudge:
    """The pinned judge, retry-and-timeout-hardened like the graph.

    A calibration or scoring sweep is hours of paid work; without the same
    resilience config the graph carries, one 429 to the judge throws all of it
    away. It is the deployment's config, not a second read of the file, so the
    judge cannot end up hardened differently from the graph it is scoring.
    """
    return PinnedJudge(load_judge_config(), resilience=deployment.resilience)


def _select(cases: Sequence[GoldenCase], wanted: Sequence[str]) -> list[GoldenCase]:
    if not wanted:
        return list(cases)
    by_id = {case.id: case for case in cases}
    missing = [case_id for case_id in wanted if case_id not in by_id]
    if missing:
        raise SystemExit(f"unknown case(s): {', '.join(missing)}")
    return [by_id[case_id] for case_id in wanted]


@dataclass(frozen=True)
class ModeRun:
    """Everything one sweep of one mode produced.

    ``observations`` maps a node to every fingerprint it presented across the
    whole sweep — twelve cases give one node twelve — and is what
    :func:`~stride_service.certification.certify` rules on. ``expected_nodes``
    is the *built* graph's LLM nodes, so a mode that enters at ``extract`` and
    stops is not held to the tiers it never routes through.
    """

    payloads: list[dict[str, Any]]
    failures: list[str]
    runs: dict[str, modes.AnalysisRun]
    observations: dict[str, frozenset[str]]
    expected_nodes: list[str]


def _observe(observed: dict[str, set[str]], nodes: Iterable[NodeRun]) -> None:
    """Fold one case's node executions into the sweep's observation set."""
    for node, prints in fingerprints_of(nodes).items():
        observed.setdefault(node, set()).update(prints)


async def _run_mode(
    cases: Sequence[GoldenCase], mode: str, deployment: Deployment
) -> ModeRun:
    """Run one mode over the selected cases, collecting Tier 1 failures.

    The per-node fingerprints come back too, taken from the node runs rather
    than from the report: the extraction mode produces no report, and sourcing
    observations from one would leave the single tier that mode exercises the
    only tier it could never certify.
    """
    pipeline = modes.build_eval_pipeline(
        modes.MODE_ENTRIES[mode], deployment=deployment
    )
    failures: list[str] = []
    payloads: list[dict[str, Any]] = []
    runs: dict[str, modes.AnalysisRun] = {}
    observed: dict[str, set[str]] = {}

    for case in cases:
        if mode == "extraction":
            result = await modes.run_extraction(case, pipeline)
            _observe(observed, result.node_runs)
            score = modes.score_extraction(case, result)
            payloads.append(score.to_json())
            failures += [
                f"{case.id}: extraction is not a valid system model:"
                f" {issue.code}: {issue.message}"
                for issue in result.issues
            ]
            continue

        run = (
            await modes.run_analysis(case, pipeline)
            if mode == "analysis"
            else await modes.run_end_to_end(case, pipeline)
        )
        runs[case.id] = run
        _observe(observed, run.report.nodes)
        issues = report_issues(run.report)
        failures += [f"{case.id}: {issue}" for issue in issues]
        payloads.append({"case": case.id, "structural_issues": issues})

    return ModeRun(
        payloads=payloads,
        failures=failures,
        runs=runs,
        observations={node: frozenset(prints) for node, prints in observed.items()},
        expected_nodes=list(pipeline.node_sampling),
    )


def _score_runs(
    cases: Sequence[GoldenCase],
    runs: dict[str, modes.AnalysisRun],
    judge: Judge,
) -> tuple[list[CaseScore], list[CriticYield]]:
    """Score every case on both sides of the critic.

    Yield comes out of the same pass rather than a second sweep: the pre-critic
    drafts are a superset of the report's threats, so scoring them first leaves
    the post-critic pass replaying memoized rulings. The returned ``CaseScore``
    is the post-critic one — what every metric in this harness has always
    meant.
    """
    scored = [
        score_case_with_yield(
            case, runs[case.id].merged_drafts, runs[case.id].report.threats, judge
        )
        for case in cases
        if case.id in runs
    ]
    return (
        [entry.score for entry in scored],
        [entry.critic_yield for entry in scored],
    )


def _models_record(deployment: Deployment, judge: PinnedJudge | None) -> dict[str, Any]:
    """What this run asked its providers for, and what they say they served.

    The tier strings are stable GA identifiers, not immutable builds, so the
    artifact records both halves. A metric that moved between two runs with
    different ``judge_served`` values is a model change, not a regression, and
    nothing else in the artifact would show that.
    """
    tiers = deployment.tiers
    judge_config = load_judge_config()
    return {
        "tiers_config_version": tiers.version,
        "tiers": dict(tiers.tiers),
        "judge_config_version": judge_config.version,
        "judge": judge_config.model,
        "judge_served": list(judge.served_model_versions) if judge else [],
    }


def _print_scores(scores: Sequence[CaseScore]) -> None:
    for score in scores:
        print(
            f"{score.case_id:<26} must-find {score.must_find_matched}/"
            f"{score.must_find_total}"
            f"  recall {score.recall:.2f}"
            f"  lane {score.lane_accuracy:.2f}"
            f"  element {score.element_accuracy:.2f}"
            f"  unsupported {score.unsupported_rate:.2f}"
        )
    if scores:
        delta = exemplar_delta(scores)
        print(
            f"exemplar delta: near {delta['near_recall']:.2f}"
            f" vs far {delta['far_recall']:.2f}"
            f" = {delta['delta']:+.2f} (tracked, non-gating)"
        )


def _print_yields(yields: Sequence[CriticYield]) -> None:
    """Both sides of the critic, always printed together.

    ``killed-real`` is deliberately on the same line as ``killed-unsupported``:
    a kill count read on its own says nothing about whether the critic is
    filtering noise or destroying findings.
    """
    for entry in yields:
        print(
            f"{entry.case_id:<26} critic {entry.drafts_in}->{entry.threats_out}"
            f"  killed-unsupported {entry.unsupported_killed}/{entry.unsupported_before}"
            f"  killed-real {entry.matched_killed}/{entry.matched_before}"
            f"  (must-find {entry.must_find_killed})"
        )
    if yields:
        totals = aggregate_yield(yields)
        print(
            f"critic yield: killed {totals['killed']}/{totals['drafts_in']}"
            f" ({totals['kill_rate']:.0%}),"
            f" unsupported caught {totals['unsupported_kill_rate']:.0%},"
            f" real destroyed {totals['matched_kill_rate']:.0%}"
            " (instrument, non-gating)"
        )


def command_run(args: argparse.Namespace) -> int:
    cases = _select(load_corpus(args.corpus), args.case)
    # One deployment for the whole sweep: the graph it runs, the manifest it is
    # certified against and the resilience the judge inherits are then one
    # configuration rather than four reads that could disagree.
    deployment = Deployment.from_env()
    mode_run = asyncio.run(_run_mode(cases, args.mode, deployment))
    failures = mode_run.failures

    # Never silently trust: the verdict is always computed and surfaced, so an
    # aggregate can never be read without knowing whether the generation
    # identity behind it is a blessed baseline.
    certification = certify(
        mode_run.observations,
        deployment.manifest,
        deployment.tier_of,
        mode_run.expected_nodes,
    )
    _print_certification(certification)
    # Both halves, never ``certified`` alone: it is narrow by design and is
    # vacuously true of a sweep that observed no fingerprint at all.
    trusted = certification.certified and certification.complete

    scores: list[CaseScore] = []
    yields: list[CriticYield] = []
    judge: PinnedJudge | None = None
    if mode_run.runs and not args.no_scoring:
        judge = _live_judge(deployment)
        scores, yields = _score_runs(cases, mode_run.runs, judge)
        _print_scores(scores)
        _print_yields(yields)

    artifact = {
        "mode": args.mode,
        "cases": [case.id for case in cases],
        "models": _models_record(deployment, judge),
        "gating": "tier-1-structural-only",
        "certification": certification.to_json(),
        "node_fingerprints": {
            node: sorted(prints) for node, prints in mode_run.observations.items()
        },
        "structural_failures": failures,
        "mode_output": mode_run.payloads,
        # Aggregates carry the verdict so nothing downstream folds an
        # uncertified run into a trusted number unaware.
        "scores": [score.to_json() for score in scores],
        "trusted": trusted,
        "exemplar_delta": exemplar_delta(scores) if scores else None,
        "critic_yield": [entry.to_json() for entry in yields],
        "critic_yield_aggregate": aggregate_yield(yields) if yields else None,
        "unlisted_for_promotion": unlisted_for_promotion(scores),
    }
    if args.out:
        Path(args.out).write_text(json.dumps(artifact, indent=2) + "\n", "utf-8")
        print(f"artifact written to {args.out}")

    for failure in failures:
        print(f"TIER 1 FAILURE: {failure}", file=sys.stderr)
    # Blocks on uncertified *or* unexercised: a tier that never ran means the
    # sweep did not exercise what it claims to have measured.
    uncertified_blocks = args.require_certified and (
        not certification.certified or not certification.complete
    )
    if uncertified_blocks:
        print(
            "UNCERTIFIED: --require-certified is set and the run's fingerprints"
            " are not blessed",
            file=sys.stderr,
        )
    return 1 if failures or uncertified_blocks else 0


def _print_certification(result: CertifyResult) -> None:
    """Surface the gate verdict, always — and never claim more than it checked.

    ``certified`` is narrow by design (see
    :mod:`stride_service.certification`): it means no *observed* fingerprint
    went unblessed, which is vacuously true of a sweep that observed none.
    Printing it alone is how a run that certified nothing came to announce that
    every fingerprint was blessed, so completeness is reported first and the
    green line requires both halves.
    """
    if not result.complete:
        tiers = ", ".join(result.unexercised)
        print(
            f"certification: INCOMPLETE — tier(s) {tiers} presented no"
            " fingerprint, so nothing was certified and the aggregates below"
            " are untrusted (--require-certified to hard-fail)"
        )
    if not result.certified:
        print(
            f"certification: UNCERTIFIED — {len(result.uncertified)} node(s) not"
            " blessed (aggregates are untrusted; --require-certified to hard-fail)"
        )
        for node in result.uncertified:
            print(f"  {node.node}: {node.fingerprint}")
    if result.certified and result.complete:
        print("certification: all node fingerprints blessed")


def command_calibrate(args: argparse.Namespace) -> int:
    """The >= 90% judge-human bar; failing it blocks a judge change."""
    deployment = Deployment.from_env()
    judge = _live_judge(deployment)
    result = measure_agreement(judge, load_pairs())
    print(
        f"judge-human agreement {result.agreement:.1%} over {result.total} pairs"
        f" (bar {AGREEMENT_BAR:.0%})"
    )
    print(
        f"  false matches {len(result.false_matches)},"
        f" false non-matches {len(result.false_non_matches)}"
    )
    if args.out:
        payload = {**result.to_json(), "models": _models_record(deployment, judge)}
        Path(args.out).write_text(json.dumps(payload, indent=2) + "\n", "utf-8")
    if result.meets_bar:
        return 0
    print(
        "judge does not meet the agreement bar: the judge prompt needs work,"
        " not a lowered bar",
        file=sys.stderr,
    )
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="run one eval mode over the corpus")
    run_parser.add_argument("--mode", choices=sorted(modes.MODE_ENTRIES), required=True)
    run_parser.add_argument("--case", action="append", default=[])
    run_parser.add_argument("--corpus", default=DEFAULT_CORPUS_DIR)
    run_parser.add_argument("--out", help="where to write the run artifact")
    run_parser.add_argument(
        "--no-scoring",
        action="store_true",
        help="run the graph and apply Tier 1 gates without spending judge calls",
    )
    run_parser.add_argument(
        "--require-certified",
        action="store_true",
        help="fail the run when its fingerprints are not in the blessed manifest",
    )
    run_parser.set_defaults(func=command_run)

    calibrate_parser = subparsers.add_parser(
        "calibrate", help="measure judge-human agreement over the fixtures"
    )
    calibrate_parser.add_argument("--out", help="where to write the agreement report")
    calibrate_parser.set_defaults(func=command_calibrate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
