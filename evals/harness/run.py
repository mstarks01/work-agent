"""The eval CLI: run a mode over the corpus, calibrate the judge, promote a winner.

Gating is **Tier 1 structural only**. A report that does not parse, whose
references dangle, whose severity bands contradict the matrix or whose summary
disagrees with its own contents fails the run. Must-find recall is computed,
printed and written to the artifact, and deliberately **does not block** until
baseline sweeps have established its normal range: a gate that fires before
anyone knows that range trains people to bypass it.

Everything the run measured lands in one JSON artifact: every judge ruling with
its rationale, every bucket decision, the severity confusion, the near/far
exemplar delta, and the ``valid-unlisted`` threats queued for the corpus's next
blessing pass. The metrics are judge-relative — track movement with them, never
quote them as absolutes.

Everything the run *produced* lands beside it, one whole report per case, in the
directory :func:`reports_dir` names. The artifact answers the questions the
metric set anticipated; the reports answer the rest, offline and for free
([#180](https://github.com/mstarks01/work-agent/issues/180)).

``run`` and ``calibrate`` need live provider credentials, so neither runs on a
PR. The credential-free lane is ``evals/verify_corpus.py``, which is what CI
exercises; the live sweep runs on the weekly schedule in
``.github/workflows/evals-live.yml``.

``promote`` needs **no** credentials: it works from a finished artifact, whose
``provenance`` block records what each node execution actually ran on. That is
the point of recording it — the served builds are observations, made once
during the sweep, and rediscovering them afterwards is not something an
operator should have to do
([#117](https://github.com/mstarks01/work-agent/issues/117)).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.harness import modes
from evals.harness.applicability import (
    ApplicabilityScore,
    ApplicabilityYield,
    over_applied_for_promotion,
    score_applicability,
    score_yield,
)
from evals.harness.applicability import aggregate_yield as aggregate_applicability_yield
from evals.harness.applicability import exemplar_delta as applicability_exemplar_delta
from evals.harness.applicability import pooled as pooled_applicability
from evals.harness.calibration import (
    AGREEMENT_BAR,
    LabelledPair,
    compare_judges,
    load_pairs,
    measure_agreement,
)
from evals.harness.certify import PromotionPlan, plan_promotion, promote
from evals.harness.coverage import (
    CITED_PAIRS,
    LaneCoverage,
    TaggedRow,
    aggregate_coverage,
    coverage_totals,
)
from evals.harness.critic_yield import (
    CriticYield,
    aggregate_yield,
    score_case_with_yield,
)
from evals.harness.grounds import (
    CAUGHT,
    CaseGrounds,
    GroundsFailure,
    aggregate_grounds,
    classify_failure,
    measure_grounds,
)
from evals.harness.judge import Judge, PinnedJudge, load_judge_config
from evals.harness.provenance import (
    ARTIFACT_VERSION,
    UNSET,
    EvalArtifact,
    ProvenanceError,
    RunProvenance,
    load_artifact,
    provenance_of,
)
from evals.harness.reference import GoldenCase, load_corpus
from evals.harness.scorer import (
    CaseScore,
    exemplar_delta,
    unlisted_for_promotion,
)
from evals.harness.stability import (
    CaseStability,
    ScoredRun,
    aggregate_stability,
    comparability_warnings,
    compare_runs,
    load_runs,
)
from evals.harness.structural import report_issues
from stride_service.certification import CertificationError, CertifyResult, certify
from stride_service.deployment import Deployment
from stride_service.frameworks.stride.record import Threat
from stride_service.graph import Pipeline
from stride_service.report import (
    FrameworkAnalysis,
    FrameworkName,
    NodeLatency,
    NodeRun,
    Report,
    TokenUsage,
    latency_by_node,
    usage_by_node,
)

EVALS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS_DIR = EVALS_ROOT / "corpus"


def framework_block(report: Report, framework: FrameworkName) -> FrameworkAnalysis:
    """One framework's block off a report that carries one per selection.

    The grading contract is per framework (#167), so every scorer names the
    block it grades rather than assuming the report holds one. A report missing
    a block the job selected is a driver defect rather than a sweep result: the
    envelope's own check requires the blocks to answer the job's frameworks with
    none dropped.
    """
    for block in report.analyses:
        if block.framework == framework:
            return block
    raise modes.EvalRunError(f"the report carries no {framework} analysis block")


def optional_block(
    report: Report, framework: FrameworkName
) -> FrameworkAnalysis | None:
    """The same, for a framework a case may not have declared."""
    return next(
        (block for block in report.analyses if block.framework == framework), None
    )


def stride_block(report: Report) -> FrameworkAnalysis:
    """STRIDE's block, which every case declares and every mode builds for."""
    return framework_block(report, "stride")


def stride_threats(report: Report) -> list[Threat]:
    """This report's STRIDE claims, at the record type they validate as.

    ``claims`` is annotated at the neutral :class:`RuledClaim` because a block
    holds whatever its own package produced; the scorers grade ``category`` and
    ``severity``, which only STRIDE's record carries. The envelope already
    validated this block as its package's own shape, so this re-states that
    where a caller needs it and fails loudly if it ever stops being true.
    """
    claims = stride_block(report).claims
    narrowed = [claim for claim in claims if isinstance(claim, Threat)]
    if len(narrowed) != len(claims):
        raise modes.EvalRunError(
            "the stride block's claims did not load as Threat records"
        )
    return narrowed


def _live_judge(
    deployment: Deployment, config_path: Path | str | None = None
) -> PinnedJudge:
    """The pinned judge, retry-and-timeout-hardened like the graph.

    A calibration or scoring sweep is hours of paid work; without the same
    resilience config the graph carries, one 429 to the judge throws all of it
    away. It is the deployment's config, not a second read of the file, so the
    judge cannot end up hardened differently from the graph it is scoring.

    ``config_path`` selects a *candidate* judge for calibration. It is
    deliberately not offered to ``run``: a scored sweep must be measured by the
    judge its numbers will be compared against, which is the one in
    ``evals/config/judge.toml``. Pointing a sweep at some other judge produces
    numbers that look like the tracked series and are not comparable to it.
    """
    config = load_judge_config(config_path) if config_path else load_judge_config()
    return PinnedJudge(config, resilience=deployment.resilience)


def _judge_label(config_path: Path | str | None) -> str:
    """How a candidate judge is named in a comparison report.

    ``vendor/model`` rather than the file path: the path is where a candidate
    was written down, and the pair is what was actually measured. Two files
    naming the same pair would otherwise read as two candidates.
    """
    config = load_judge_config(config_path) if config_path else load_judge_config()
    return f"{config.vendor}/{config.model}"


def _select(cases: Sequence[GoldenCase], wanted: Sequence[str]) -> list[GoldenCase]:
    if not wanted:
        return list(cases)
    by_id = {case.id: case for case in cases}
    missing = [case_id for case_id in wanted if case_id not in by_id]
    if missing:
        raise SystemExit(f"unknown case(s): {', '.join(missing)}")
    return [by_id[case_id] for case_id in wanted]


#: The extension a sweep's report directory takes, replacing the artifact's.
REPORTS_SUFFIX = ".reports"


def reports_dir(out: str | Path) -> Path:
    """Where a sweep's per-case reports land, given its artifact path.

    Derived from ``--out`` rather than selected by a second flag: the reports
    and the artifact describe one sweep, and two independent paths let an
    operator point them at two different ones.
    """
    return Path(out).with_suffix(REPORTS_SUFFIX)


def _write_reports(out: str, mode: str, runs: Mapping[str, modes.AnalysisRun]) -> None:
    """Persist every finished case's whole report beside the artifact.

    A sweep is paid work and the report is the only record of what the agents
    said: what each threat cited, what the critic rejected and on what
    reasoning, and what the ``scope`` list carried. The artifact holds the
    measurements somebody thought of in advance, so without this file every
    other question about a finished sweep costs a second sweep
    ([#180](https://github.com/mstarks01/work-agent/issues/180)).

    Beside the artifact rather than inside it. A report embeds the whole
    **Valid System Model** and every claim's grounds, so folding a corpus of
    them into the artifact would bury the aggregates a reader opens it for.

    **These reports are publishable.** They carry corpus source text, which is
    in this repository, so writing them raises no disclosure question. That is
    stated rather than assumed, because the same code path carries a
    submitter's own text the moment it runs outside the corpus.
    """
    if mode not in modes.REPORTING_MODES:
        print(f"no reports written: {mode} mode produces none")
        return
    directory = reports_dir(out)
    directory.mkdir(parents=True, exist_ok=True)
    total_bytes = 0
    for case_id, run in sorted(runs.items()):
        path = directory / f"{case_id}.report.json"
        path.write_text(run.report.model_dump_json(indent=2) + "\n", "utf-8")
        total_bytes += path.stat().st_size
    print(f"{len(runs)} report(s) written to {directory} ({total_bytes / 1024:.0f} KB)")


@dataclass(frozen=True)
class ModeRun:
    """Everything one sweep of one mode produced.

    ``provenance`` is the sweep's generation identities — per node execution,
    what was requested, what answered, and the hash that pair produced. Both
    the certification verdict and the artifact are derived from it, so the
    thing a promotion reads back is the same record the verdict was computed
    from rather than a parallel one written alongside. ``expected_nodes`` is
    the *built* graph's LLM nodes, so a mode that enters at ``extract`` and
    stops is not held to the tiers it never routes through.

    ``usage`` is the sweep's token cost per node, summed across every case and
    every execution within a case. A sweep is the only place a real per-node
    number comes from — a single job's numbers are one sample of one system —
    so it is folded here rather than left to whoever reads the artifact.

    ``latency`` is the same fold over the same executions, in wall-clock. It is
    folded here for the same reason and one more: ``duration_ms`` is recorded
    per node run and read back by nothing, so a sweep that does not fold it
    measures the latency the Evaluation section of
    [#115](https://github.com/mstarks01/work-agent/issues/115) asks for and
    then throws it away.

    ``grounds`` and ``grounds_failures`` are the two halves of the same
    instrument: what the cases that finished did with ``grounds``, and what the
    cases that did not finished on. A case appears in exactly one of them.

    ``coverage`` is every case's per-lane coverage rows, unpooled, one entry per
    ``(framework, row)`` pair. One case's row is not a readable number — see
    :mod:`evals.harness.coverage` — so what rides here is the input to the
    aggregate rather than the aggregate itself, and the framework rides beside
    it because a lane slug belongs to whichever package declared it.

    ``applicability`` is the ASVS half, one row per case that declares the
    framework. It is a separate list rather than a column on ``payloads``
    because it is a different instrument: STRIDE's scorer grades an open claim
    set through a judge, and this one is a confusion matrix over a finite
    catalog with no model call anywhere (#167). Pooling them would put two
    numbers that are not comparable under one heading.

    ``extractions`` is what the extraction mode produced and every other mode
    leaves empty. It is kept beside its own payloads because the sweep prints
    an aggregate over it, and folding a printed number out of JSON already
    written is how the printed line and the artifact come to disagree.
    """

    payloads: list[dict[str, Any]]
    failures: list[str]
    runs: dict[str, modes.AnalysisRun]
    provenance: RunProvenance
    expected_nodes: list[str]
    usage: dict[str, TokenUsage]
    latency: dict[str, NodeLatency]
    grounds: list[CaseGrounds]
    grounds_failures: list[GroundsFailure]
    coverage: list[TaggedRow]
    #: The frameworks this sweep's graphs were built for, so the coverage table
    #: can report a package whose every lane went silent rather than drop it.
    frameworks: tuple[FrameworkName, ...]
    extractions: list[modes.ExtractionScore]
    applicability: list[ApplicabilityScore]
    applicability_yields: list[ApplicabilityYield]

    @property
    def observations(self) -> dict[str, frozenset[str]]:
        """The node -> fingerprint sets the certification verdict rules on."""
        return self.provenance.observations()


async def _run_mode(
    cases: Sequence[GoldenCase], mode: str, deployment: Deployment
) -> ModeRun:
    """Run one mode over the selected cases, collecting Tier 1 failures.

    The generation identities come back too, taken from the node runs rather
    than from the report: the extraction mode produces no report, and sourcing
    them from one would leave the single tier that mode exercises the only tier
    it could never certify.

    A case whose drafts the fan-in rejects is **counted and survived** rather
    than allowed to abort the sweep — a threat that loses every ground and an
    invented evidence reference are both rates somebody asked for, and a sweep
    that dies on the first one reports neither. It stays a Tier 1 failure: the case is
    recorded, its failure is listed, and the run still exits non-zero. Only the
    fan-in's own exceptions are caught (:data:`~evals.harness.grounds.CAUGHT`);
    a provider timeout is not a measurement and still ends the sweep, and
    neither is a :class:`~evals.harness.grounds.GroundMisShape`, which is this
    service assembling its own record wrongly rather than anything a model did.
    """
    # One graph per distinct framework set rather than one for the sweep. A case
    # declares the frameworks whose **Precondition** allows it and whose records
    # it carries, so building the declaration is what makes every record the
    # corpus holds reachable — and what stops a case with no ASVS reference set
    # paying for 17 ``strong``-tier lanes it has nothing to score. Two sets
    # today, so this is two builds, and building one runs the credential and
    # supported-param gates that a per-case build would re-run 13 times.
    pipelines: dict[tuple[FrameworkName, ...], Pipeline] = {}

    def pipeline_for(case: GoldenCase) -> Pipeline:
        frameworks = modes.case_frameworks(case)
        if frameworks not in pipelines:
            pipelines[frameworks] = modes.build_eval_pipeline(
                modes.MODE_ENTRIES[mode], deployment=deployment, frameworks=frameworks
            )
        return pipelines[frameworks]

    failures: list[str] = []
    payloads: list[dict[str, Any]] = []
    runs: dict[str, modes.AnalysisRun] = {}
    # Every execution the sweep performed, kept flat so the per-node totals,
    # the certification verdict and the artifact's provenance are three views
    # of one list rather than three folds that could disagree.
    executions: list[NodeRun] = []
    grounds: list[CaseGrounds] = []
    grounds_failures: list[GroundsFailure] = []
    coverage: list[TaggedRow] = []
    extractions: list[modes.ExtractionScore] = []
    applicability: list[ApplicabilityScore] = []
    applicability_yields: list[ApplicabilityYield] = []

    for case in cases:
        pipeline = pipeline_for(case)
        if mode == "extraction":
            result = await modes.run_extraction(case, pipeline)
            executions += result.node_runs
            score = modes.score_extraction(case, result)
            extractions.append(score)
            payloads.append(score.to_json())
            failures += [
                f"{case.id}: extraction is not a valid system model:"
                f" {issue.code}: {issue.message}"
                for issue in result.issues
            ]
            continue

        try:
            run = (
                await modes.run_analysis(case, pipeline)
                if mode == "analysis"
                else await modes.run_end_to_end(case, pipeline)
            )
        except CAUGHT as error:
            failure = classify_failure(case.id, error)
            grounds_failures.append(failure)
            failures.append(f"{case.id}: {failure.kind}: {failure.detail}")
            payloads.append({"case": case.id, "grounds_failure": failure.to_json()})
            continue

        runs[case.id] = run
        executions += run.report.nodes
        issues = report_issues(run.report)
        failures += [f"{case.id}: {issue}" for issue in issues]
        # Every block the job selected, not STRIDE's alone. Coverage and grounds
        # are folds over what a lane agent was offered and what its drafts cite,
        # and no package is exempt from either — ADR 0002 exempts none from
        # finding-level attribution, and Coverage is reported per lane of every
        # framework a job runs.
        measurements = []
        for block in run.report.analyses:
            coverage += [(block.framework, row) for row in block.coverage]
            measurements.append(
                measure_grounds(
                    case.id,
                    block.framework,
                    run.drafts.get(block.framework, ()),
                    block.unverified_grounds,
                )
            )
        grounds += measurements
        # The measurement rides in the artifact rather than in the report, so a
        # reader gets the number without opening a second file. The report is
        # kept too, in the directory beside the artifact, and that is what a
        # question this payload did not anticipate is answered from.
        payload: dict[str, Any] = {
            "case": case.id,
            "structural_issues": issues,
            "grounds": [entry.to_json() for entry in measurements],
        }
        # Only where the case declared the framework. A case ASVS's
        # **Precondition** refuses carries no reference set, and scoring the
        # empty block it would still produce reports zero recall for a framework
        # that correctly did nothing.
        asvs = optional_block(run.report, "asvs")
        if asvs is not None:
            applies = score_applicability(case, asvs)
            applicability.append(applies)
            payload["applicability"] = applies.to_json()
            # Both sides of this framework's critic, from the block the run
            # already produced: no judge and no second scoring pass, because
            # both sides are requirement identifiers.
            critic = score_yield(case, asvs, run.drafts.get("asvs", ()))
            applicability_yields.append(critic)
            payload["applicability_yield"] = critic.to_json()
        payloads.append(payload)

    return ModeRun(
        payloads=payloads,
        failures=failures,
        runs=runs,
        provenance=provenance_of(
            executions,
            tier_of=deployment.tier_of,
            sampling=deployment.sampling,
            tiers_config_version=deployment.tiers.version,
        ),
        # The union across the graphs the sweep built. Certification reports a
        # tier that presented no fingerprint as *unexercised*, so a node that
        # only one framework set carries has to be in the expectation or a sweep
        # that ran it would look like one that did not.
        expected_nodes=sorted(
            {node for built in pipelines.values() for node in built.node_sampling}
        ),
        usage=usage_by_node(executions),
        latency=latency_by_node(executions),
        grounds=grounds,
        grounds_failures=grounds_failures,
        coverage=coverage,
        # Read off the graphs that were built, never off the keys they were
        # requested under. The two differ wherever a caller substitutes a
        # pipeline, and the coverage table has to report the lanes that actually
        # ran rather than the ones the corpus asked for.
        frameworks=tuple(
            sorted({name for built in pipelines.values() for name in built.frameworks})
        ),
        extractions=extractions,
        applicability=applicability,
        applicability_yields=applicability_yields,
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
            case,
            runs[case.id].merged_drafts,
            stride_threats(runs[case.id].report),
            judge,
        )
        for case in cases
        if case.id in runs
    ]
    return (
        [entry.score for entry in scored],
        [entry.critic_yield for entry in scored],
    )


def _models_record(
    deployment: Deployment,
    judge: PinnedJudge | None,
    judge_config_path: Path | str | None = None,
) -> dict[str, Any]:
    """What this run asked its providers for, and what they say they served.

    The tier strings are stable GA identifiers, not immutable builds, so the
    artifact records both halves. A metric that moved between two runs with
    different ``judge_served`` values is a model change, not a regression, and
    nothing else in the artifact would show that.

    ``judge_config_path`` records the *candidate* a calibration measured, which
    is not always the shipped one. Reading the default here while the run used
    a candidate would attribute a number to the wrong judge — the exact
    mislabelling this record exists to prevent. A comparison passes neither,
    since it measured several and names them in its own payload.
    """
    tiers = deployment.tiers
    judge_config = (
        load_judge_config(judge_config_path)
        if judge_config_path
        else load_judge_config()
    )
    return {
        "tiers_config_version": tiers.version,
        # Dumped, not handed over whole: a ``TierSelection`` is a pydantic model
        # and the artifact is written with ``json.dumps``, which cannot encode
        # one. Nothing offline caught that, because the artifact is only built
        # on a live sweep.
        "tiers": {
            tier: selection.model_dump(mode="json")
            for tier, selection in tiers.tiers.items()
        },
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


def _print_extraction(scores: Sequence[modes.ExtractionScore]) -> None:
    """What an extraction sweep found, per case and then per attribute.

    The per-attribute split is the line this instrument exists for. An element
    recall of 1.00 says the extraction named the right things; it says nothing
    about whether it typed them, and a rule reads the type. So a sweep whose
    ``boundary.kind`` row reads 40% has found a real regression behind two
    perfect element numbers.

    Every number here is **non-gating**. A low agreement is a question to take
    to the source text, not a defect on its own
    ([#179](https://github.com/mstarks01/work-agent/issues/179)).
    """
    for score in scores:
        agreed = len(score.attributes) - len(score.differing)
        print(
            f"{score.case_id:<26} extraction recall {score.recall:.2f}"
            f"  precision {score.precision:.2f}"
            f"  crossings {'match' if score.crossings_match else 'DIFFER'}"
            f"  attributes {agreed}/{len(score.attributes)}"
        )
    if not scores:
        return
    totals = modes.aggregate_attributes(scores)
    print(
        f"attributes: {totals['agreed']}/{totals['compared']} agree"
        f" ({totals['agreement']:.0%}) (instrument, non-gating)"
    )
    for name, split in totals["by_attribute"].items():
        print(
            f"  {name:28} {split['agreed']:5,}/{split['compared']:<7,}"
            f" {split['agreement']:.0%}"
        )


def _print_grounds(
    measurements: Sequence[CaseGrounds], failures: Sequence[GroundsFailure]
) -> None:
    """The three measurements, printed with the branch mix beside the rates.

    ``quoteless`` is on the same line as the rates and is not a fault:
    ``analyze.md``'s branch rule predicts a real share of findings whose
    trigger was an unknown or a crossing rather than the submitter's words.
    Read low with suspicion, not high.
    """
    for entry in measurements:
        counts = entry.kind_counts
        print(
            f"{entry.case_id:<26} grounds {entry.ground_count}"
            f" on {entry.threat_count} threats ({entry.grounds_per_threat:.2f} ea)"
            f"  q/u/a/d {counts['quote']}/{counts['unknown-attribute']}"
            f"/{counts['absent-attribute']}/{counts['derived-fact']}"
            f"  quoteless {entry.quoteless_rate:.0%}"
            f"  unverified {entry.unverified_count}/{entry.quote_count}"
        )
    for failure in failures:
        scope = (
            f": {len(failure.threat_ids)}/{failure.draft_count} threats"
            if failure.kind == "fail-closed"
            else ""
        )
        print(f"{failure.case_id:<26} grounds FAILED ({failure.kind}{scope})")
    if measurements or failures:
        totals = aggregate_grounds(measurements, failures)
        print(
            f"grounds: {totals['grounds_per_threat']:.2f} per threat,"
            f" quoteless {totals['quoteless_rate']:.0%},"
            f" unverified {totals['unverified_rate']:.1%},"
            f" failed cases {totals['failed_cases']}"
            f" (fail-closed {totals['fail_closed_cases']},"
            f" other {totals['other_failed_cases']})"
            " (instrument, non-gating)"
        )


def _print_applicability(scores: Sequence[ApplicabilityScore]) -> None:
    """ASVS's rows, then the pooled figures. Never folded into STRIDE's table.

    The two scorers answer different questions over different sets — an open
    claim set through a judge, and a finite catalog by string compare — so one
    combined recall column would be an average of two things nobody asked for.
    """
    if not scores:
        return
    print("\nASVS applicability (mechanical, no judge)")
    print(
        f"{'case':<26} {'lvl':>3} {'rec':>6} {'must':>6} {'prec':>6}"
        f" {'miss':>5} {'over':>5} {'rej':>5} {'off':>4}"
    )
    for score in scores:
        print(
            f"{score.case:<26} {score.level:>3} {score.recall:>6.0%}"
            f" {score.must_find_recall:>6.0%} {score.precision:>6.0%}"
            f" {len(score.missed):>5} {len(score.over_applied):>5}"
            f" {len(score.rejected):>5} {len(score.off_catalog):>4}"
        )
    totals = pooled_applicability(scores)
    print(
        f"pooled over {totals['cases']} cases: recall {totals['recall']:.0%}"
        f" ({totals['matched']}/{totals['expected']}),"
        f" must-find {totals['must_find_recall']:.0%},"
        f" precision {totals['precision']:.0%},"
        f" off-catalog {totals['off_catalog']}"
        " (instrument, non-gating)"
    )


def _print_applicability_yield(yields: Sequence[ApplicabilityYield]) -> None:
    """This framework's critic, both sides, never one.

    ``destroyed`` is the number that can veto the pattern here — the critic
    ruling inapplicable a requirement the case says applies — and it is printed
    beside ``earned`` for the reason
    :mod:`evals.harness.critic_yield` prints its pair: a rejection count alone
    reads as the critic working or as the critic breaking things.
    """
    if not yields:
        return
    print("\nASVS critic yield (mechanical, no judge)")
    print(
        f"{'case':<26} {'drafts':>7} {'confirmed':>10} {'rejected':>9} {'earned':>7} {'destroyed':>10}"
    )
    for entry in yields:
        print(
            f"{entry.case:<26} {entry.drafts:>7} {len(entry.confirmed):>10}"
            f" {len(entry.rejected):>9} {len(entry.earned):>7}"
            f" {len(entry.destroyed):>10}"
        )
    totals = aggregate_applicability_yield(yields)
    print(
        f"pooled: rejected {totals['rejected']}/{totals['drafts']}"
        f" ({totals['rejection_rate']:.0%}),"
        f" earned {totals['earned']}, destroyed {totals['destroyed']}"
        f" ({totals['destroyed_rate']:.0%} of rejections)"
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
    _print_usage(mode_run.usage)
    _print_latency(mode_run.latency)
    # Both halves, never ``certified`` alone: it is narrow by design and is
    # vacuously true of a sweep that observed no fingerprint at all.
    trusted = certification.certified and certification.complete

    # Before the judge, and unconditional: the extraction, grounds and coverage
    # measurements cost no provider call, so ``--no-scoring`` still emits them.
    _print_extraction(mode_run.extractions)
    _print_grounds(mode_run.grounds, mode_run.grounds_failures)
    lanes = aggregate_coverage(mode_run.coverage, mode_run.frameworks)
    _print_coverage(lanes, offered=bool(mode_run.coverage))
    # Costs no provider call either: ASVS matches by requirement ID, so its
    # whole scorer is a set comparison and ``--no-scoring`` still emits it.
    _print_applicability(mode_run.applicability)
    _print_applicability_yield(mode_run.applicability_yields)

    scores: list[CaseScore] = []
    yields: list[CriticYield] = []
    judge: PinnedJudge | None = None
    if mode_run.runs and not args.no_scoring:
        judge = _live_judge(deployment)
        scores, yields = _score_runs(cases, mode_run.runs, judge)
        _print_scores(scores)
        _print_yields(yields)

    artifact = {
        "artifact_version": ARTIFACT_VERSION,
        "mode": args.mode,
        "cases": [case.id for case in cases],
        "models": _models_record(deployment, judge),
        "gating": "tier-1-structural-only",
        "certification": certification.to_json(),
        # What actually generated, per node execution — the record `promote`
        # reads back. It replaces the `node_fingerprints` map, which carried
        # the hashes without the served builds they were computed from, so a
        # promotion could not be driven from a finished sweep at all
        # ([#117](https://github.com/mstarks01/work-agent/issues/117)).
        "provenance": mode_run.provenance.to_json(),
        "node_usage": {
            node: usage.model_dump() for node, usage in mode_run.usage.items()
        },
        "node_latency": {
            node: {**latency.model_dump(), "mean_ms": round(latency.mean_ms)}
            for node, latency in mode_run.latency.items()
        },
        "structural_failures": failures,
        "mode_output": mode_run.payloads,
        # The per-case attribute numbers ride in ``mode_output`` beside the
        # element ones; this is the sweep-wide fold, which is where a value the
        # pipeline stopped producing shows up as a column rather than as one
        # line per case (#195). ``None`` outside the extraction mode, so an
        # unmeasured attribute set never reads as a fully agreeing one.
        "attribute_aggregate": (
            modes.aggregate_attributes(mode_run.extractions)
            if mode_run.extractions
            else None
        ),
        # Aggregates carry the verdict so nothing downstream folds an
        # uncertified run into a trusted number unaware.
        "scores": [score.to_json() for score in scores],
        # ASVS's own key rather than a row in ``scores``: a confusion matrix
        # over a finite catalog and a judge-relative recall figure are not the
        # same measurement, and one list would invite a reader to average them.
        "applicability": [score.to_json() for score in mode_run.applicability],
        "applicability_aggregate": (
            pooled_applicability(mode_run.applicability)
            if mode_run.applicability
            else None
        ),
        # Its own key beside STRIDE's for the reason the scores are: one column
        # pooling a judge-relative recall with a set comparison would be an
        # average of two things nobody asked for.
        "applicability_exemplar_delta": (
            applicability_exemplar_delta(mode_run.applicability)
            if mode_run.applicability
            else None
        ),
        # The corpus feedback loop's ASVS half: requirements a run ruled
        # applicable that the case did not expect, for the next reading session
        # to settle. No judge, because the set arithmetic already separated the
        # package-bug case into ``off_catalog``.
        "over_applied_for_promotion": over_applied_for_promotion(
            mode_run.applicability
        ),
        # This framework's critic, both sides. Its own keys rather than rows in
        # ``critic_yield``: STRIDE's kill count is judge-relative and this one is
        # set arithmetic, so a shared table would invite a rate across both.
        "applicability_yield": [
            entry.to_json() for entry in mode_run.applicability_yields
        ],
        "applicability_yield_aggregate": (
            aggregate_applicability_yield(mode_run.applicability_yields)
            if mode_run.applicability_yields
            else None
        ),
        "trusted": trusted,
        "exemplar_delta": exemplar_delta(scores) if scores else None,
        "critic_yield": [entry.to_json() for entry in yields],
        "critic_yield_aggregate": aggregate_yield(yields) if yields else None,
        "grounds": [entry.to_json() for entry in mode_run.grounds],
        "grounds_failures": [
            failure.to_json() for failure in mode_run.grounds_failures
        ],
        "grounds_aggregate": (
            aggregate_grounds(mode_run.grounds, mode_run.grounds_failures)
            if mode_run.grounds or mode_run.grounds_failures
            else None
        ),
        # Written whether or not anything was offered: an absent block and a
        # block of zeroes would otherwise be indistinguishable to a reader
        # comparing two sweeps.
        "coverage": [lane.to_json() for lane in lanes],
        "coverage_totals": coverage_totals(lanes),
        "unlisted_for_promotion": unlisted_for_promotion(scores),
    }
    if args.out:
        Path(args.out).write_text(json.dumps(artifact, indent=2) + "\n", "utf-8")
        print(f"artifact written to {args.out}")
        _write_reports(args.out, args.mode, mode_run.runs)

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


def _print_usage(usage: dict[str, TokenUsage]) -> None:
    """The sweep's token cost per node, dearest first.

    Printed rather than left in the artifact because "which node costs the
    most" is a question people ask far more often than they open the JSON, and
    because the answer is not guessable from the graph's shape: reasoning
    tokens are spent against the same cap as the output and appear in none of
    it, so a node emitting a few hundred visible tokens can be the sweep's
    largest consumer.

    Sorted on ``total_tokens`` because that is the column the provider bills.
    A node whose provider metered nothing is absent from ``usage`` entirely and
    so does not appear here — an unmeasured node must not read as a cheap one.
    """
    if not usage:
        print("token usage: nothing metered — no provider reported usage")
        return
    print("token usage (whole sweep, per node):")
    header = f"  {'node':34} {'prompt':>10} {'cached':>10} {'output':>10}"
    print(f"{header} {'reasoning':>10} {'total':>11}")
    dearest = sorted(usage.items(), key=lambda pair: -pair[1].total_tokens)
    for node, spent in dearest:
        print(
            f"  {node:34} {spent.prompt_tokens:10,} {spent.cached_prompt_tokens:10,}"
            f" {spent.completion_tokens:10,} {spent.reasoning_tokens:10,}"
            f" {spent.total_tokens:11,}"
        )


def _print_latency(latency: dict[str, NodeLatency]) -> None:
    """The sweep's wall-clock per node, slowest total first.

    Beside the token table because the two answer different questions about
    the same executions and get confused for each other: the dearest node is
    not the slowest one, and a deterministic derivation that costs nothing to
    bill still costs the job its seconds.

    ``slowest`` is printed next to the mean because it is the column a timeout
    is set from.
    """
    if not latency:
        print("latency: nothing ran")
        return
    print("latency (whole sweep, per node):")
    print(f"  {'node':34} {'runs':>6} {'total ms':>12} {'mean ms':>10} {'slowest':>10}")
    slowest = sorted(latency.items(), key=lambda pair: -pair[1].total_ms)
    for node, spent in slowest:
        print(
            f"  {node:34} {spent.executions:6,} {spent.total_ms:12,}"
            f" {round(spent.mean_ms):10,} {spent.slowest_ms:10,}"
        )


def _print_coverage(lanes: Sequence[LaneCoverage], offered: bool) -> None:
    """What each lane was offered and how much of it its drafts cite.

    Read as a rate over the whole sweep, never per case, and never as a score:
    an agent that examined a lead and correctly rejected it cites nothing, so a
    low rate is a question to ask rather than a failure. The number that is
    unambiguous is a lane whose rules fire nowhere at all: those rules read a
    shape this corpus does not have, or nothing at all.

    The rules column is **firings over evaluations** — one rule against one
    case — because pooling multiplies the lane's rules by the cases.
    """
    if not offered:
        print("coverage: no case produced a report to account for")
        return
    print("coverage (whole sweep, per lane — cited, not considered):")
    header = f"  {'framework':10} {'lane':30} {'drafts':>7} {'rules fired':>11}"
    print(f"{header} {'candidates':>12} {'elements':>12} {'crossings':>12}")
    for lane in lanes:
        cited = [
            f"{lane.totals[cited_field]}/{lane.totals[offered_field]}"
            for offered_field, cited_field in CITED_PAIRS[:3]
        ]
        print(
            f"  {lane.framework:10} {lane.lane:30} {lane.drafts:7,}"
            f" {lane.rules_fired:>4}/{lane.rules:<5}"
            f" {cited[0]:>12} {cited[1]:>12} {cited[2]:>12}"
        )
    totals = coverage_totals(lanes)
    print(
        f"coverage: {totals['rules_fired']}/{totals['rules']} rule evaluations fired,"
        f" candidates cited {totals['cited_rates']['candidates_cited']:.0%},"
        f" elements {totals['cited_rates']['elements_cited']:.0%},"
        f" unknown controls {totals['cited_rates']['unknown_controls_cited']:.0%}"
        " (instrument, non-gating)"
    )


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


def _served_choice(value: str) -> tuple[str, str]:
    """Parse one ``--served TIER=BUILD`` selection.

    Split on the *first* ``=`` only: a served build is a vendor-prefixed
    provider string, and nothing forbids one containing the character.
    """
    tier, sep, served = value.partition("=")
    if not sep or not tier.strip() or not served.strip():
        raise argparse.ArgumentTypeError(f"expected TIER=SERVED_BUILD, got {value!r}")
    return tier.strip(), served.strip()


def command_promote(args: argparse.Namespace) -> int:
    """Bless a finished sweep's generation identities, from the artifact alone.

    The whole point is that nothing is reconstructed by hand: the served builds
    were observed during the sweep and recorded, so promotion reads them rather
    than asking the operator to rediscover what answered
    ([#117](https://github.com/mstarks01/work-agent/issues/117)).

    A preview by default, because promotion rewrites two config files this
    deployment then runs on. ``--yes`` is the second step, and it is the same
    computed plan that gets written — the preview is not a separate rendering
    that could describe something else.
    """
    deployment = Deployment.from_env()
    try:
        artifact = load_artifact(args.artifact)
        _check_sampling_schema(artifact.provenance, deployment)
        plan = plan_promotion(artifact.provenance, dict(args.served))
    except CertificationError as error:
        print(f"refusing to promote: {error}", file=sys.stderr)
        return 1

    paths = deployment.paths
    _print_promotion(artifact, plan, paths.sampling, paths.blessed_fingerprints)
    if not args.yes:
        print(
            "\nNothing written. Re-run with --yes to re-pin the sampling file"
            " and bless the fingerprints above."
        )
        return 0

    try:
        manifest = promote(
            plan.sampling,
            plan.served_builds,
            sampling_path=paths.sampling,
            manifest_path=paths.blessed_fingerprints,
        )
    except CertificationError as error:
        print(f"promotion failed, nothing written: {error}", file=sys.stderr)
        return 1

    print(f"\nre-pinned {paths.sampling}")
    print(f"blessed into {paths.blessed_fingerprints}:")
    for tier in sorted(manifest.tiers):
        print(f"  {tier}: {len(manifest.blessed_for(tier))} fingerprint(s)")
    print("Commit both files: a blessed set is a reviewed claim about this deployment.")
    return 0


def command_stability(args: argparse.Namespace) -> int:
    """Compare two or more finished sweeps for run-to-run stability.

    Credential-free, like ``promote``: the artifacts already hold every
    reference each sweep matched, so the comparison is arithmetic over records
    rather than a re-run. Nothing here gates — the spread it prints is what a
    future threshold would have to be derived from.
    """
    try:
        runs = load_runs(args.artifact)
        stability = compare_runs(runs)
    except (ProvenanceError, ValueError) as error:
        print(f"cannot compare: {error}", file=sys.stderr)
        return 1

    _print_stability(runs, stability)
    if args.out:
        report = {
            "runs": [
                {"artifact": run.label, "mode": run.mode, "models": run.models}
                for run in runs
            ],
            "warnings": comparability_warnings(runs),
            "cases": [entry.to_json() for entry in stability],
            "aggregate": aggregate_stability(stability),
        }
        Path(args.out).write_text(json.dumps(report, indent=2) + "\n", "utf-8")
        print(f"stability report written to {args.out}")
    return 0


def _print_stability(
    runs: Sequence[ScoredRun], stability: Sequence[CaseStability]
) -> None:
    """The per-case spread, with what makes the runs incomparable printed first.

    The warnings lead because they change what every number below means: a
    spread measured across two judges is not run-to-run noise, and a reader who
    meets that caveat after the table has already read the table.
    """
    for warning in comparability_warnings(runs):
        print(f"WARNING: {warning}")
    print(f"stability over {len(runs)} runs: {', '.join(run.label for run in runs)}")
    header = f"  {'framework':10} {'case':26} {'recalls':>22}"
    print(f"{header} {'spread':>8} {'always':>16} {'jaccard':>9}")
    for entry in stability:
        recalls = " ".join(f"{recall:.2f}" for recall in entry.recalls)
        always = f"{entry.always}/{entry.references} (±{entry.sometimes})"
        print(
            f"  {entry.framework:10} {entry.case_id:26} {recalls:>22}"
            f" {entry.recall_spread:8.2f} {always:>16} {entry.mean_jaccard:9.2f}"
        )
    totals = aggregate_stability(stability)
    print(
        f"stability: {totals['always_matched']}/{totals['references']} references"
        f" matched in every run ({totals['always_rate']:.0%}),"
        f" volatile {totals['volatile_rate']:.0%},"
        f" mean jaccard {totals['mean_jaccard']:.2f},"
        f" worst case recall spread {totals['worst_case_recall_spread']:.2f}"
        " (instrument, non-gating)"
    )


def _check_sampling_schema(provenance: RunProvenance, deployment: Deployment) -> None:
    """Refuse an artifact measured against a different sampling schema.

    Promotion re-pins values *into* this deployment's file, so values measured
    under one schema written into a file on another would produce a blessed
    fingerprint describing parameters no run ever carried. There is no
    migration: the sampling file's versions are hard cutovers, and a stale
    artifact is re-measured rather than reinterpreted.
    """
    measured = provenance.sampling_config_version
    current = deployment.sampling.version
    if measured != current:
        raise ProvenanceError(
            f"the artifact was measured under sampling schema v{measured} and"
            f" this deployment reads v{current}; re-run the sweep rather than"
            " promoting values across a schema change"
        )


def _print_promotion(
    artifact: EvalArtifact,
    plan: PromotionPlan,
    sampling_path: Path,
    manifest_path: Path,
) -> None:
    """Show exactly what would be certified, before anything is written."""
    print("Configuration selected for promotion")
    print(f"  artifact:  {artifact.path}")
    print(f"  measured:  mode {artifact.mode}, {len(artifact.cases)} case(s)")
    print(f"  re-pins:   {sampling_path}")
    print(f"  blesses:   {manifest_path}")
    if artifact.structural_failures:
        print(
            f"  WARNING:   this sweep reported"
            f" {len(artifact.structural_failures)} structural failure(s)"
        )
    if not artifact.trusted:
        print(
            "  WARNING:   this sweep read as untrusted — it was not itself"
            " certified against an existing blessed set"
        )

    for entry in plan.tiers:
        print(f"\n{entry.tier.upper()}")
        print(f"  requested: {', '.join(entry.requested_models)}")
        print(f"  served:    {entry.served_model}")
        print(f"  nodes:     {', '.join(entry.nodes)}")
        not_blessed = [
            served
            for served in entry.observed_served_models
            if served != entry.served_model
        ]
        if not_blessed:
            print(f"  NOT blessed (also observed): {', '.join(not_blessed)}")
        print()
        for param, value in entry.sampling.model_dump().items():
            shown = UNSET if value is None else value
            print(f"  {param + ':':<20} {shown}")
        print("\n  fingerprint:")
        print(f"    {entry.fingerprint}")


def command_calibrate(args: argparse.Namespace) -> int:
    """The >= 90% judge-label bar; failing it blocks a judge change.

    One ``--judge-config`` (or none) measures a single judge and gates on it.
    Several put the candidates side by side over the identical pairs, which is
    the selection exercise: a production judge chosen on measured agreement
    rather than on which platform the project started on.
    """
    deployment = Deployment.from_env()
    pairs = load_pairs()
    if len(args.judge_config) > 1:
        return _calibrate_many(deployment, pairs, args)
    config_path = args.judge_config[0] if args.judge_config else None
    return _calibrate_one(deployment, pairs, config_path, args.out)


def _calibrate_one(
    deployment: Deployment,
    pairs: Sequence[LabelledPair],
    config_path: str | None,
    out: str | None,
) -> int:
    judge = _live_judge(deployment, config_path)
    result = measure_agreement(judge, pairs)
    print(
        f"judge-label agreement {result.agreement:.1%} over {result.total} pairs"
        f" (bar {AGREEMENT_BAR:.0%})"
    )
    print(
        f"  false matches {len(result.false_matches)},"
        f" false non-matches {len(result.false_non_matches)}"
    )
    if out:
        payload = {
            **result.to_json(),
            "models": _models_record(deployment, judge, config_path),
        }
        Path(out).write_text(json.dumps(payload, indent=2) + "\n", "utf-8")
    if result.meets_bar:
        return 0
    print(
        "judge does not meet the agreement bar: the judge prompt needs work,"
        " not a lowered bar",
        file=sys.stderr,
    )
    return 1


def _calibrate_many(
    deployment: Deployment, pairs: Sequence[LabelledPair], args: argparse.Namespace
) -> int:
    """Several candidate judges over the same pairs, reported side by side.

    Exits non-zero only when **no** candidate clears the bar. A single judge
    below the bar is that judge's problem; every candidate below it is the
    measurement system's, and there is no judge to select.
    """
    judges = {
        _judge_label(path): _live_judge(deployment, path) for path in args.judge_config
    }
    comparison = compare_judges(judges, pairs)

    width = max(len(label) for label in judges)
    print(f"judge-label agreement over {len(pairs)} pairs (bar {AGREEMENT_BAR:.0%})")
    for candidate in comparison.candidates:
        mark = "ok " if candidate.result.meets_bar else "BAR"
        print(
            f"  {mark} {candidate.label:<{width}}  {candidate.result.agreement:.1%}"
            f"  (false match {len(candidate.result.false_matches)},"
            f" false non-match {len(candidate.result.false_non_matches)})"
        )

    # The half a per-judge accuracy cannot show: two judges at the same
    # agreement can still disagree with each other on every pair they each got
    # wrong, and that is what decides whether a conclusion is judge-dependent.
    print("\njudge-vs-judge agreement (the recorded labels aside)")
    labels = [candidate.label for candidate in comparison.candidates]
    for index, first in enumerate(labels):
        for second in labels[index + 1 :]:
            print(
                f"  {first} vs {second}:"
                f" {comparison.agreement_between(first, second):.1%}"
            )

    divergences = comparison.divergences()
    print(f"\n{len(divergences)} of {len(pairs)} pairs are ruled differently")
    if divergences:
        print(
            "Report these as uncertainty rather than picking a winner: a"
            " conclusion that moves with the judge's vendor is not a finding"
            " about the models being compared."
        )

    if args.out:
        payload = {
            **comparison.to_json(),
            "models": _models_record(deployment, judge=None),
        }
        Path(args.out).write_text(json.dumps(payload, indent=2) + "\n", "utf-8")

    if comparison.meets_bar:
        print(f"\nhighest agreement: {comparison.best.label}")
        print(
            "Selecting it is a reviewed commit to evals/config/judge.toml with"
            " a version bump, not something this command applies — a judge"
            " change silently re-scores every historical number."
        )
        return 0
    print(
        "no candidate meets the agreement bar: the judge prompt needs work,"
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
    run_parser.add_argument(
        "--out",
        help=(
            "where to write the run artifact. Each finished case's whole report"
            " is written beside it, as <out>.reports/<case>.report.json"
        ),
    )
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
        "calibrate", help="measure judge-label agreement over the fixtures"
    )
    calibrate_parser.add_argument("--out", help="where to write the agreement report")
    calibrate_parser.add_argument(
        "--judge-config",
        action="append",
        default=[],
        metavar="PATH",
        help=(
            "a candidate judge config to measure instead of evals/config/judge.toml."
            " Repeat it to put candidates side by side over the same pairs, which"
            " also reports judge-vs-judge agreement — pass one per model family to"
            " see whether a conclusion depends on the judge's vendor."
        ),
    )
    calibrate_parser.set_defaults(func=command_calibrate)

    promote_parser = subparsers.add_parser(
        "promote",
        help="bless a finished sweep's generation identities from its artifact",
    )
    promote_parser.add_argument("artifact", help="the run artifact to promote")
    promote_parser.add_argument(
        "--served",
        action="append",
        default=[],
        type=_served_choice,
        metavar="TIER=BUILD",
        help=(
            "which served build to bless for a tier the sweep saw answered by"
            " more than one. It selects among what was observed and cannot"
            " introduce a build the sweep never saw."
        ),
    )
    promote_parser.add_argument(
        "--yes",
        action="store_true",
        help="write the files; without it the promotion is only previewed",
    )
    promote_parser.set_defaults(func=command_promote)

    stability_parser = subparsers.add_parser(
        "stability",
        help="compare finished sweeps for run-to-run stability (no credentials)",
    )
    stability_parser.add_argument(
        "artifact",
        nargs="+",
        help=(
            "two or more scored run artifacts of the same corpus. Cases scored"
            " by only some of them are excluded and named."
        ),
    )
    stability_parser.add_argument("--out", help="where to write the stability report")
    stability_parser.set_defaults(func=command_stability)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
