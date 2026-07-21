"""The eval CLI: run a mode over the corpus, or calibrate the judge.

Gating is phase 1's (ticket 009 decisions 16 and 19): **Tier 1 structural
only**. A report that does not parse, whose references dangle, whose severity
bands contradict the matrix or whose summary disagrees with its own contents
fails the run. Must-find recall is computed, printed and written to the
artifact, and deliberately **does not block** — it becomes a hard per-case gate
in ticket 025, after ~5 baseline sweeps have established the normal range. A
gate that fires before anyone knows the normal range trains people to bypass
it.

Everything the run learned lands in one JSON artifact: every judge ruling with
its rationale, every bucket decision, the severity confusion, the near/far
exemplar delta, and the ``valid-unlisted`` threats queued for the SME's next
blessing pass. The metrics are judge-relative — track movement with them, never
quote them as absolutes.

``run`` needs live Vertex credentials (ADC, per decision 17 — never API keys).
``score`` is credential-free and replays a recorded artifact's produced threats
through the scorer, which is what the PR job exercises.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from evals.harness import modes
from evals.harness.calibration import AGREEMENT_BAR, load_pairs, measure_agreement
from evals.harness.judge import VertexJudge, load_judge_config
from evals.harness.reference import GoldenCase, load_corpus
from evals.harness.scorer import (
    CaseScore,
    exemplar_delta,
    score_case,
    unlisted_for_promotion,
)
from evals.harness.structural import report_issues

EVALS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS_DIR = EVALS_ROOT / "corpus"


def _select(cases: Sequence[GoldenCase], wanted: Sequence[str]) -> list[GoldenCase]:
    if not wanted:
        return list(cases)
    by_id = {case.id: case for case in cases}
    missing = [case_id for case_id in wanted if case_id not in by_id]
    if missing:
        raise SystemExit(f"unknown case(s): {', '.join(missing)}")
    return [by_id[case_id] for case_id in wanted]


async def _run_mode(
    cases: Sequence[GoldenCase], mode: str
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    """Run one mode over the selected cases, collecting Tier 1 failures."""
    pipeline = modes.build_eval_pipeline(modes.MODE_ENTRIES[mode])
    failures: list[str] = []
    payloads: list[dict[str, Any]] = []
    reports: dict[str, Any] = {}

    for case in cases:
        if mode == "extraction":
            result = await modes.run_extraction(case, pipeline)
            score = modes.score_extraction(case, result)
            payloads.append(score.to_json())
            failures += [
                f"{case.id}: extraction is not a valid system model:"
                f" {issue.code}: {issue.message}"
                for issue in result.issues
            ]
            continue

        report = (
            await modes.run_analysis(case, pipeline)
            if mode == "analysis"
            else await modes.run_end_to_end(case, pipeline)
        )
        reports[case.id] = report
        issues = report_issues(report)
        failures += [f"{case.id}: {issue}" for issue in issues]
        payloads.append({"case": case.id, "structural_issues": issues})

    return payloads, failures, reports


def _score_reports(
    cases: Sequence[GoldenCase], reports: dict[str, Any]
) -> list[CaseScore]:
    config = load_judge_config()
    judge = VertexJudge(config)
    return [
        score_case(case, reports[case.id].threats, judge)
        for case in cases
        if case.id in reports
    ]


def _print_scores(scores: Sequence[CaseScore]) -> None:
    for score in scores:
        print(
            f"{score.case_id:<26} must-find {score.must_find_matched}/"
            f"{score.must_find_total}"
            f"  recall {score.recall:.2f}"
            f"  lane {score.lane_accuracy:.2f}"
            f"  element {score.element_accuracy:.2f}"
            f"  ungrounded {score.ungrounded_rate:.2f}"
        )
    if scores:
        delta = exemplar_delta(scores)
        print(
            f"exemplar delta: near {delta['near_recall']:.2f}"
            f" vs far {delta['far_recall']:.2f}"
            f" = {delta['delta']:+.2f} (tracked, non-gating)"
        )


def command_run(args: argparse.Namespace) -> int:
    cases = _select(load_corpus(args.corpus), args.case)
    payloads, failures, reports = asyncio.run(_run_mode(cases, args.mode))

    scores: list[CaseScore] = []
    if reports and not args.no_scoring:
        scores = _score_reports(cases, reports)
        _print_scores(scores)

    artifact = {
        "mode": args.mode,
        "cases": [case.id for case in cases],
        "gating": "tier-1-structural-only",
        "structural_failures": failures,
        "mode_output": payloads,
        "scores": [score.to_json() for score in scores],
        "exemplar_delta": exemplar_delta(scores) if scores else None,
        "unlisted_for_promotion": unlisted_for_promotion(scores),
    }
    if args.out:
        Path(args.out).write_text(json.dumps(artifact, indent=2) + "\n", "utf-8")
        print(f"artifact written to {args.out}")

    for failure in failures:
        print(f"TIER 1 FAILURE: {failure}", file=sys.stderr)
    return 1 if failures else 0


def command_calibrate(args: argparse.Namespace) -> int:
    """The >= 90% judge-human bar; failing it blocks a judge change."""
    result = measure_agreement(VertexJudge(load_judge_config()), load_pairs())
    print(
        f"judge-human agreement {result.agreement:.1%} over {result.total} pairs"
        f" (bar {AGREEMENT_BAR:.0%})"
    )
    print(
        f"  false matches {len(result.false_matches)},"
        f" false non-matches {len(result.false_non_matches)}"
    )
    if args.out:
        Path(args.out).write_text(
            json.dumps(result.to_json(), indent=2) + "\n", "utf-8"
        )
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
