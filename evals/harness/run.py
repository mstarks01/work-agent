"""The eval CLI: run a mode over the corpus, calibrate the rule, promote a winner.

Gating is Tier 1 structural only. A report fails the run when it does not parse,
when its references dangle, when its severity bands contradict the matrix, or
when its summary disagrees with its own contents. Must-find recall is computed,
printed and written to the artifact, and it deliberately does not block until
baseline sweeps have established its normal range. A gate that fires before
anybody knows that range trains people to bypass it.

Everything the run measured lands in one JSON artifact: every matching ruling
with its rationale, every bucket decision, the severity confusion, the near-far
exemplar delta, and the ``valid-unlisted`` threats queued for the corpus's next
blessing pass. The metrics are relative to the rule and the ledger. Track
movement with them, and never quote them as absolutes.

Everything the run produced lands beside it, as one whole report per case, in
the directory :func:`reports_dir` names. The artifact answers the questions the
metric set anticipated. The reports answer the rest, offline and for free
([#180](https://github.com/mstarks01/work-agent/issues/180)).

``run`` is the one command here that needs live provider credentials, so it does
not run on a pull request. Every other command reads finished artifacts, the
corpus and the vote ledger, so ``score``, ``calibrate``, ``review``, ``rekey``,
``stability`` and ``promote`` all run offline and free. The credential-free lane
CI exercises is ``evals/verify_corpus.py``. A person dispatches the live sweep
by hand from ``.github/workflows/evals-live.yml``, which carries no schedule.

``promote`` is offline for a reason worth naming on its own. It works from a
finished artifact, whose ``provenance`` block records what each node execution
ran on. That is the point of recording it: the served builds are observations,
made once during the sweep, and rediscovering them afterwards is not something
an operator should have to do
([#117](https://github.com/mstarks01/work-agent/issues/117)).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from analysis_service.certification import CertificationError, CertifyResult, certify
from analysis_service.deployment import Deployment
from analysis_service.frameworks import PACKAGES
from analysis_service.frameworks.stride.record import Threat
from analysis_service.graph import Pipeline
from analysis_service.identity import build_identity
from analysis_service.report import (
    FrameworkAnalysis,
    FrameworkName,
    NodeLatency,
    NodeRun,
    Report,
    TokenUsage,
    latency_by_node,
    usage_by_node,
)
from evals.harness import (
    comparison,
    consent,
    envelope,
    instruction,
    instruction_delta,
    ledger,
    modes,
    pairing,
    queue,
    roster,
    standings,
    submit,
    writing,
)
from evals.harness.artifact import (
    REPO_ROOT,
    EvalArtifact,
    corpus_digest,
    load_artifact,
    repo_commit,
)
from evals.harness.artifact import build as build_artifact
from evals.harness.calibration import (
    AGREEMENT_BAR,
    IDENTITY_VALIDATION,
    load_pairs,
    measure_agreement,
    measure_merges,
)
from evals.harness.certify import PromotionPlan, plan_promotion, promote
from evals.harness.coverage import TaggedRow
from evals.harness.critic_yield import CriticYield, score_case_with_yield
from evals.harness.grounds import (
    CAUGHT,
    CaseGrounds,
    GroundsFailure,
    classify_failure,
)
from evals.harness.identity import SubsetVerbIdentity
from evals.harness.instruments import (
    INSTRUMENTS,
    ModeRun,
    Sweep,
    measure_case,
    render_all,
)
from evals.harness.provenance import (
    UNSET,
    ProvenanceError,
    RunProvenance,
    provenance_of,
)
from evals.harness.reference import GoldenCase, load_corpus
from evals.harness.scorer import CaseScore
from evals.harness.stability import (
    CaseStability,
    ScoredRun,
    aggregate_stability,
    comparability_warnings,
    compare_runs,
    load_runs,
)
from evals.harness.structural import report_issues

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
        # The drafts beside the report, because the report is what survived the
        # critic and half of what a score reads is what did not. Without them
        # ``score`` could recompute recall and not critic yield, and a command
        # that re-scores some of a sweep is worse than one that refuses.
        drafts = directory / f"{case_id}.drafts.json"
        drafts.write_text(
            json.dumps(
                {
                    framework: [claim.model_dump(mode="json") for claim in claims]
                    for framework, claims in run.drafts.items()
                },
                indent=2,
            )
            + "\n",
            "utf-8",
        )
        total_bytes += path.stat().st_size + drafts.stat().st_size
    print(f"{len(runs)} report(s) written to {directory} ({total_bytes / 1024:.0f} KB)")


async def _run_mode(
    cases: Sequence[GoldenCase],
    mode: str,
    deployment: Deployment,
    only: Sequence[FrameworkName] = (),
    accepted: float | None = None,
    ask: Callable[[str], str] | None = None,
) -> ModeRun:
    """Run one mode over the selected cases, collecting Tier 1 failures.

    The execution identities come back too, taken from the node runs rather
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

    ``accepted`` is the amount the estimate gate took consent for, and the
    hold runs **between cases, never inside one**: a case that has started has
    already committed its spend, and interrupting it would pay for a
    measurement nobody could read. ``None`` means the contributor accepted
    ``unknown``, so there is nothing to measure against and the hold never
    fires.
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
        frameworks = modes.select_frameworks(case, only)
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
    rows: dict[str, list[Any]] = {}
    skipped: list[str] = []
    stopped_before: tuple[str, ...] = ()

    for position, case in enumerate(cases):
        if position and accepted is not None:
            remaining = [later.id for later in cases[position:]]
            try:
                # `position` counts every case the loop reached; a skipped one
                # spent nothing, so counting it would divide the spend by a
                # larger number and project low. `remaining` over-counts the
                # same way and is left alone, because that pushes the figure
                # up — the safe direction for a gate somebody consents to.
                accepted = consent.hold(
                    accepted,
                    executions,
                    remaining,
                    ask,
                    ran=position - len(skipped),
                )
            except consent.Refused as refusal:
                # Not an exception out of the sweep: everything already run is
                # paid for, so the caller still writes the artifact and the
                # reports. ``stopped_before`` is what stops the partial record
                # reading as a whole one.
                print(refusal)
                stopped_before = tuple(remaining)
                break
        if not modes.select_frameworks(case, only):
            # Not a failure: --framework asked for packages this case does not
            # declare, so there is nothing here to measure. Named rather than
            # dropped, because a case absent from a sweep and a case that
            # scored nothing are different facts.
            skipped.append(case.id)
            continue
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
        except modes.EvalRunError as error:
            # The graph refused this case's model. In `end-to-end` that is
            # extraction and its one `repair` pass both failing, which is a
            # measurement — and the mode where a refused model is most expected
            # is also the most expensive per case, so losing the sweep over one
            # costs every case that already ran.
            #
            # Not routed through `classify_failure`: that reads draft-level
            # faults off the fan-in, and a refused model never produced drafts.
            # The message already carries the case id.
            failures.append(str(error))
            payloads.append({"case": case.id, "run_failure": str(error)})
            continue
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
        measured = measure_case(case, run, issues)
        grounds += measured.grounds
        coverage += measured.coverage
        for name, row in measured.rows.items():
            rows.setdefault(name, []).append(row)
        payloads.append(measured.payload)

    if skipped:
        # Printed, never silent: a narrowed sweep that quietly measured 9 of 13
        # cases reads exactly like a full one to whoever quotes its numbers.
        print(
            f"--framework {','.join(only)} skipped {len(skipped)} case(s)"
            f" declaring none of it: {', '.join(skipped)}"
        )

    return ModeRun(
        payloads=payloads,
        failures=failures,
        runs=runs,
        provenance=provenance_of(
            executions,
            tier_of=deployment.tier_of,
            sampling=deployment.sampling,
            tiers_config_version=deployment.tiers.version,
            build=build_identity(),
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
        # From the built graphs, for the same reason ``expected_nodes`` is: the
        # instruction a node actually carries is a property of what was built,
        # and recomposing it here would be a second answer to a question the
        # graph already answered.
        instructions=instruction.collect(pipelines.values()),
        # Read off the graphs that were built, never off the keys they were
        # requested under. The two differ wherever a caller substitutes a
        # pipeline, and the coverage table has to report the lanes that actually
        # ran rather than the ones the corpus asked for.
        frameworks=tuple(
            sorted({name for built in pipelines.values() for name in built.frameworks})
        ),
        extractions=extractions,
        rows={name: tuple(collected) for name, collected in rows.items()},
        stopped_before=stopped_before,
    )


def _flows_by_case(
    cases: Sequence[GoldenCase],
) -> dict[str, dict[str, tuple[str, str]]]:
    """Each case's **Data Flow** map, which the identity rule resolves against.

    Per case rather than pooled: two cases may spell one flow ID differently,
    and a shared map would resolve one case's citation against another's graph.
    """
    return {
        case.id: {
            flow.id: (flow.source, flow.destination) for flow in case.model.data_flows
        }
        for case in cases
    }


def _score_runs(
    cases: Sequence[GoldenCase],
    runs: dict[str, modes.AnalysisRun],
    matcher: SubsetVerbIdentity,
    votes: ledger.Ledger,
) -> tuple[tuple[CaseScore, ...], tuple[CriticYield, ...]]:
    """Score every case that carries a STRIDE block, on both sides of the critic.

    Yield comes out of the same pass rather than a second sweep: the pre-critic
    drafts are a superset of the report's threats, so scoring them first leaves
    the post-critic pass replaying memoized rulings. The returned ``CaseScore``
    is the post-critic one — what every metric in this harness has always
    meant.

    **A case that did not run this framework is skipped, never failed on.** The
    two instruments this pass feeds declare ``frameworks=("stride",)``, and a
    scorer that reads a package's own record has nothing to say about a case
    that ran a different package. Asking for the block regardless is what made a
    sweep of one framework die inside another framework's scorer.
    """
    scored = [
        score_case_with_yield(
            case,
            runs[case.id].merged_drafts,
            stride_threats(runs[case.id].report),
            matcher,
            votes,
        )
        for case in cases
        if case.id in runs and optional_block(runs[case.id].report, "stride")
    ]
    return (
        tuple(entry.score for entry in scored),
        tuple(entry.critic_yield for entry in scored),
    )


def _scored_sweep(
    sweep: Sweep,
    cases: Sequence[GoldenCase],
    runs: dict[str, modes.AnalysisRun],
    ledger_path: Path,
    roster_path: Path = roster.DEFAULT_ROSTER_PATH,
) -> tuple[dict[str, Sweep], ledger.Ledger]:
    """Every reading that needs the vote ledger, folded onto ``sweep``.

    One spelling for the two commands that compute these numbers: ``run``,
    which scores the sweep it just produced, and ``score``, which re-scores a
    finished one against the ledger as it stands now. Both read the same four
    inputs and fill the same three fields, so an instrument added to the scored
    half had to be found in two places. ``INSTRUMENTS`` already made the
    printing and the artifact one table; this is the computing half of the same
    argument.

    Claim matching is the identity rule and a standing comes from the vote
    ledger. Both are offline, so these readings cost no provider and a sweep is
    always scored — what nobody has voted on yet is counted, printed, and
    served by :mod:`evals.harness.queue`.

    The ledger comes back beside the sweep because it is loaded here and a
    caller reports how many votes it read. Loading it twice would let one
    command print a count the other's numbers were not computed from.

    **Every series in one pass** (#326). Standing selects which votes a series
    reads, so each entry in :data:`~evals.harness.standings.SERIES` is the
    same three readings over a narrowed ledger. The returned sweep carries the
    primary series — maintainer votes only — and the others come back beside
    it, so no flag exists that could quietly move a published number.
    """
    votes = ledger.load(ledger_path)
    table = roster.load(roster_path)
    # Named, never dropped in silence. CI refuses a ledger naming somebody the
    # roster does not (#320), so this is a mid-edit tree or a hand-written
    # row — and a vote that moves nothing should say so rather than look cast.
    unrostered = sorted({vote.voter for vote in votes if vote.voter not in table})
    if unrostered:
        print(
            f"{len(unrostered)} voter(s) have no roster line and no series"
            f" reads them: {', '.join(unrostered)}"
        )
    matcher = SubsetVerbIdentity(_flows_by_case(cases))
    reports = {case: run.report for case, run in runs.items()}

    by_series = {}
    for name, included in standings.SERIES.items():
        read = standings.narrow(votes, table, included)
        scores, yields = _score_runs(cases, runs, matcher, read)
        by_series[name] = replace(
            sweep,
            scores=scores,
            yields=yields,
            writing=writing.measure(cases, reports, read),
        )
    return by_series, votes


def _scored_keys(sweep: Sweep) -> dict[str, Any]:
    """Just the blocks that read the ledger — one series' worth."""
    keys: dict[str, Any] = {}
    for instrument in INSTRUMENTS.values():
        if instrument.scored:
            keys |= instrument.artifact(sweep)
    return keys


def _series_record(by_series: dict[str, Sweep]) -> dict[str, Any]:
    """Which standings each series reads, and the non-primary series' numbers.

    The primary series' blocks are the artifact's top-level scored keys and
    are **not** repeated here: two copies of one number is the shape where a
    reader can quote the stale one. What this adds is the statement every
    published number needs — which standings produced it — plus the second
    series, so a maintainer can see what contributor votes would move.
    """
    return {
        "primary": standings.PRIMARY,
        "standings": {
            name: list(included) for name, included in standings.SERIES.items()
        },
        "blocks": {
            name: _scored_keys(sweep)
            for name, sweep in by_series.items()
            if name != standings.PRIMARY
        },
    }


def _print_series(by_series: dict[str, Sweep]) -> None:
    """Say which standings the printed numbers read, and what the others say."""
    included = ", ".join(standings.SERIES[standings.PRIMARY])
    print(f"\nThe scored numbers above read {included} votes only.")
    for name, sweep in by_series.items():
        if name == standings.PRIMARY:
            continue
        counts: dict[str, int] = {}
        for score in sweep.scores:
            for standing, count in score.standing_counts.items():
                counts[standing] = counts.get(standing, 0) + count
        summary = ", ".join(f"{count} {standing}" for standing, count in counts.items())
        print(
            f"  series {name!r} ({', '.join(standings.SERIES[name])}):"
            f" {summary or 'nothing scored'}"
        )


def _models_record(deployment: Deployment) -> dict[str, Any]:
    """What this run asked its providers for.

    The tier strings are stable GA identifiers, not immutable builds; the
    served builds are recorded per node in the provenance block. No scorer
    appears here: claim matching is the identity rule, which is code in this
    repository and is versioned by the fingerprint version in every key it
    produces.
    """
    tiers = deployment.tiers
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
    }


def _contribution_note(commit: Any) -> tuple[str, ...]:
    """What to say about this sweep's contributability, before the money goes.

    A sweep over uncommitted edits is legitimate — it is ``TUNING.md``'s
    ordinary loop — so this informs and never refuses. What it prevents is the
    contributor discovering at ``submit``, after the spend, that a Baseline
    identity needs a clean commit and this artifact has none.
    """
    if commit.clean is True:
        return ()
    return (
        (
            "this tree does not match its commit, so this sweep can be measured"
            " but never contributed as a Baseline. Commit first if you meant to"
            " contribute it."
        ),
    )


def _prompt(question: str) -> str:
    """Ask at the terminal. Seamed so the gate's tests never block on stdin."""
    return input(question)


def _tier_routes(deployment: Deployment) -> dict[str, str]:
    """The requested route per tier — what the price map is asked about.

    Composed through ``Vendor.route``, the one join that also forms the vendor
    half of a fingerprint, rather than re-spelled here: a second spelling of a
    route is a second answer to what this run asked for.
    """
    return {
        tier: selection.vendor_entry.route(selection.model)
        for tier, selection in deployment.tiers.tiers.items()
    }


def _would_be_identity(
    cases: Sequence[GoldenCase],
    deployment: Deployment,
    commit: Any,
    corpus: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    """The Baseline identity this sweep would carry, before it runs.

    Shaped to compare equal to a merged Baseline's stored identity, so the
    estimate can tell "somebody already ran exactly this" from "somebody ran
    something like it". A dirty tree matches nothing, which is correct: no
    Baseline can carry one.
    """
    frameworks = {
        name
        for case in cases
        for name in modes.select_frameworks(case, tuple(args.framework))
    }
    return {
        "repo_commit": commit.commit,
        "corpus_digest": corpus,
        "models": _tier_routes(deployment),
        "sampling": {
            tier: block.model_dump()
            for tier, block in deployment.sampling.tiers.items()
        },
        "frameworks": sorted(frameworks),
    }


def command_run(args: argparse.Namespace) -> int:
    cases = _select(load_corpus(args.corpus), args.case)
    # Before anything is spent. Both answers are free and the sweep is not, so
    # a repository that cannot say what it is about to run stops here rather
    # than 90 minutes later holding an artifact that cannot name its prompts.
    commit, corpus = repo_commit(), corpus_digest()
    # One deployment for the whole sweep: the graph it runs and the manifest it
    # is certified against are then one configuration rather than two reads
    # that could disagree.
    deployment = Deployment.from_env()

    # Informed, affirmative consent, before the first request spends anything
    # (#334). There is no ceiling: the contributor may accept any amount, and
    # the gate's job is that they see it, see how good the number is, and
    # accept it in a way a rote hand cannot.
    ask = None if args.accept_cost is not None else _prompt
    estimate = consent.estimate(
        _would_be_identity(cases, deployment, commit, corpus, args),
        _tier_routes(deployment),
        REPO_ROOT,
    )
    estimate = replace(estimate, lines=(*estimate.lines, *_contribution_note(commit)))
    try:
        accepted = consent.gate(estimate, args.accept_cost, ask)
    except consent.Refused as refusal:
        print(refusal, file=sys.stderr)
        return 1

    mode_run = asyncio.run(
        _run_mode(cases, args.mode, deployment, tuple(args.framework), accepted, ask)
    )
    failures = mode_run.failures
    ran = [case for case in cases if case.id not in mode_run.stopped_before]

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

    # First the run-level instruments, then the scores: the split predates
    # scoring being free and is kept because the two halves read differently —
    # one is about the graph's behaviour, the other about its output.
    sweep = Sweep(run=mode_run)
    render_all(sweep, scored=False)

    # A mode that produced no run has nothing to score, and ``Sweep`` already
    # defaults every scored field to empty — which is a sweep that measured
    # nothing rather than one whose numbers were all zero.
    by_series: dict[str, Sweep] = {}
    if mode_run.runs:
        by_series, _ = _scored_sweep(
            sweep, ran, mode_run.runs, Path(args.ledger), Path(args.roster)
        )
        sweep = by_series[standings.PRIMARY]
        unvoted = sum(score.unvoted_count for score in sweep.scores)
        if unvoted:
            print(
                f"{unvoted} unlisted finding(s) have no vote; run"
                " `python -m evals.harness.run review <artifact> --voter <you>`"
                " to see them"
            )
    render_all(sweep, scored=True)
    if by_series:
        _print_series(by_series)

    artifact = build_artifact(
        mode=args.mode,
        # The cases that ran, never the ones selected: a sweep the hold
        # stopped must not claim the cases it never attempted.
        cases=[case.id for case in ran],
        models=_models_record(deployment),
        certification=certification,
        provenance=mode_run.provenance,
        usage=mode_run.usage,
        latency=mode_run.latency,
        structural_failures=failures,
        payloads=mode_run.payloads,
        trusted=trusted,
        commit=commit,
        corpus=corpus,
        sweep=sweep,
        series=_series_record(by_series) if by_series else None,
    )
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


def _print_certification(result: CertifyResult) -> None:
    """Surface the gate verdict, always — and never claim more than it checked.

    ``certified`` is narrow by design (see
    :mod:`analysis_service.certification`): it means no *observed* fingerprint
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
    """Bless a finished sweep's execution identities, from the artifact alone.

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
            plan.keys,
            build=plan.build,
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


def _runs_from_reports(artifact: Path, cases: Sequence[GoldenCase]) -> dict[str, Any]:
    """Read a finished sweep's saved reports and drafts back into runs.

    The pair is what a score needs: the report holds what survived the critic
    and the drafts hold what it was handed, and critic yield is the difference.
    A sweep whose directory carries a report and no drafts refuses here rather
    than scoring the half it can — re-run that sweep.
    """
    directory = reports_dir(artifact)
    if not directory.is_dir():
        raise modes.EvalRunError(
            f"{directory} does not exist; a score reads the reports a sweep"
            " writes beside its artifact, not the artifact alone"
        )

    runs: dict[str, Any] = {}
    for case in cases:
        report_path = directory / f"{case.id}.report.json"
        drafts_path = directory / f"{case.id}.drafts.json"
        if not report_path.exists():
            continue
        if not drafts_path.exists():
            raise modes.EvalRunError(
                f"{drafts_path} is missing; this sweep predates the drafts"
                " being written beside its report, so its critic yield cannot"
                " be recomputed. Re-run the sweep rather than scoring half of it"
            )
        report = Report.model_validate_json(report_path.read_text(encoding="utf-8"))
        raw = json.loads(drafts_path.read_text(encoding="utf-8"))
        drafts = {
            framework: tuple(
                PACKAGES[framework].record.model_validate(claim) for claim in claims
            )
            for framework, claims in raw.items()
        }
        runs[case.id] = modes.AnalysisRun(report=report, drafts=drafts)
    if not runs:
        raise modes.EvalRunError(
            f"{directory} carries no report for any case in the artifact"
        )
    return runs


def command_score(args: argparse.Namespace) -> int:
    """Re-score a finished sweep against the ledger as it stands now.

    A vote is cast after the sweep that produced the finding, so without this
    the answer reached the numbers only on the *next* sweep — and a sweep costs
    a provider. This costs nothing: the reports are on disk, the matcher is a
    rule and the standings come from the vote ledger.

    **It rewrites the readings that read the ledger, and nothing else.** The
    instruments marked ``scored`` are recomputed and their keys replaced; every
    run-level block stays exactly as the sweep wrote it, because grounds,
    coverage and provenance are facts about a run that no later vote changes.
    """
    path = Path(args.artifact)
    loaded = load_artifact(path)
    cases = [case for case in load_corpus(args.corpus) if case.id in loaded.cases]
    if not cases:
        print(f"{path}: none of its cases are in {args.corpus}", file=sys.stderr)
        return 1

    runs = _runs_from_reports(path, cases)
    frameworks = tuple(
        sorted(
            {block.framework for run in runs.values() for block in run.report.analyses}
        )
    )
    by_series, votes = _scored_sweep(
        Sweep(run=ModeRun.empty(frameworks)),
        cases,
        runs,
        Path(args.ledger),
        Path(args.roster),
    )
    sweep = by_series[standings.PRIMARY]
    render_all(sweep, scored=True)
    _print_series(by_series)

    raw = dict(loaded.raw)
    raw |= _scored_keys(sweep)
    raw["series"] = _series_record(by_series)

    out = Path(args.out) if args.out else path
    out.write_text(json.dumps(raw, indent=2) + "\n", "utf-8")
    print(f"\n{len(votes)} vote(s) read from {args.ledger}")
    print(f"{out} rewritten" if out == path else f"scored artifact written to {out}")
    return 0


def command_pairing(args: argparse.Namespace) -> int:
    """Build the reading view behind one case's applicability disagreement.

    Credential-free, like ``score`` and ``review``: the reports are on disk and
    the requirement text comes from the catalog. It answers nothing — the whole
    output is the two sides of a question only a person settles (#447).

    ``--html`` is refused inside this repository. The page carries ASVS
    sentences under CC BY-SA 4.0 and this tree is Apache-2.0, so
    :func:`~evals.harness.pairing.refuse_path_inside_repo` says so where the
    message can explain it.
    """
    path = Path(args.artifact)
    loaded = load_artifact(path)
    case = next(
        (
            entry
            for entry in load_corpus(args.corpus)
            if entry.id == args.case and entry.id in loaded.cases
        ),
        None,
    )
    if case is None:
        print(f"{path} carries no case {args.case!r}", file=sys.stderr)
        return 1

    runs = _runs_from_reports(path, [case])
    block = next(
        (
            candidate
            for candidate in runs[case.id].report.analyses
            if candidate.framework == pairing.FRAMEWORK
        ),
        None,
    )
    if block is None:
        print(
            f"{path}: {case.id} carries no {pairing.FRAMEWORK} block", file=sys.stderr
        )
        return 1

    built = pairing.pair_case(case, block)
    pairing.render(built)
    if args.html:
        out = pairing.refuse_path_inside_repo(Path(args.html))
        out.write_text(pairing.render_html(built, str(path)), "utf-8")
        print(f"\npage written to {out}")
    if args.json:
        out = Path(args.json)
        out.write_text(json.dumps(built.to_json(), indent=2) + "\n", "utf-8")
        print(f"identifiers written to {out}")
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
    spread measured across two matcher versions is not run-to-run noise, and a reader who
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
        print("\n  instruction digest(s) and the fingerprint each blesses:")
        for key, fingerprint in zip(entry.keys, entry.fingerprints, strict=True):
            print(f"    {key.instruction_sha256}  ->  {fingerprint}")


def command_calibrate(args: argparse.Namespace) -> int:
    """Both error directions, then the >= 90% admission gate for a new rule.

    Prices the shipped identity rule against the recorded labels and against
    the corpus's own distinct claims. Offline and free: the rule is code, the
    labels and the corpus are in the repository, and no provider is contacted.
    Run it on any change to the rule or to ``evals/harness/verbs.py``, because
    a matcher change silently re-scores every historical number.

    **The two directions print first, because they are the measurement.** The
    ratio prints after them as the gate a candidate rule passes before it earns
    pinned counts, and it is not matcher accuracy — see
    :mod:`evals.harness.calibration`.
    """
    pairs = load_pairs()
    corpus = load_corpus(args.corpus)
    flows = _flows_by_case(corpus)
    matcher = SubsetVerbIdentity(flows)
    result = measure_agreement(matcher, pairs)
    # Which package the labelled pairs belong to is read off the contract, never
    # named here: an entry that declares it needs candidate pairs is the one
    # that has them. A second such package would need a pair set of its own, and
    # the shared fixture cannot answer for two — so it raises rather than
    # silently scoring one package's rule against another's labels.
    paired = [
        name
        for name, entry in sorted(IDENTITY_VALIDATION.items())
        if entry.needs_candidate_pairs
    ]
    if len(paired) != 1:
        print(
            f"{len(paired)} packages declare they need candidate pairs"
            f" ({paired or 'none'}), and `load_pairs` reads one shared fixture."
            " Key the pair set by package before adding another."
        )
        return 1
    [scored_package] = paired
    merges = measure_merges(corpus, scored_package, flows)
    positives = sum(1 for pair in pairs if pair.is_scored and pair.label_match)
    negatives = result.total - positives
    print(
        f"false splits {len(result.false_non_matches)} of {positives}"
        " equivalent candidate pairs"
    )
    print(
        f"false merges {len(result.false_matches)} of {negatives} candidate"
        f" negatives, {len(merges.merges)} of {merges.within_lane_pairs}"
        " distinct reference pairs"
    )
    set_aside = ", ".join(
        f"{count} {label}" for label, count in sorted(result.set_aside.items())
    )
    print(
        f"  {result.refused} pair(s) refused by the rule"
        f"{'; set aside by label: ' + set_aside if set_aside else ''};"
        " none of them is scored"
    )
    for package, entry in sorted(IDENTITY_VALIDATION.items()):
        # The packages with no pair set, keyed by that property rather than by
        # excluding a name. It is also what makes the line below true: only an
        # entry declaring it needs none is printed as carrying none.
        if entry.needs_candidate_pairs:
            continue
        other = measure_merges(corpus, package, flows)
        print(
            f"{package}: {len(other.merges)} collisions of"
            f" {other.within_lane_pairs} distinct reference pairs"
            f" (no candidate pairs: {entry.why})"
        )
    print(
        f"admission gate: {result.agreement:.1%} label agreement over"
        f" {result.total} pairs (bar {AGREEMENT_BAR:.0%})"
    )
    if args.out:
        payload = result.to_json() | {"corpus_merges": merges.to_json()}
        Path(args.out).write_text(json.dumps(payload, indent=2) + "\n", "utf-8")
    if result.meets_bar:
        return 0
    print(
        "the rule does not meet the agreement bar: the rule needs work,"
        " not a lowered bar",
        file=sys.stderr,
    )
    return 1


@dataclass(frozen=True)
class Command:
    """One subcommand: what it is called for, what it takes, and what it runs.

    A table entry rather than three edits in :func:`main`. The harness keys
    everything else it grows an entry per — instruments, modes, package scorers
    — and a command is no different: a new one is a key here, and
    ``TestTheCommandTable`` is what stops a ``command_*`` function from existing
    that nothing can reach.
    """

    #: What ``--help`` says this command is for.
    help: str
    #: What it does, given the parsed arguments. Returns the exit code.
    run: Callable[[argparse.Namespace], int]
    #: What it takes, added to its own subparser. A command with no arguments
    #: of its own declares none.
    arguments: Callable[[argparse.ArgumentParser], None] | None = None


def _run_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mode", choices=sorted(modes.MODE_ENTRIES), required=True)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument(
        "--framework",
        action="append",
        default=[],
        choices=sorted(PACKAGES),
        help="narrow each case to these frameworks. A pure selection: it names"
        " no option and changes no reference set. Default is every framework a"
        " case declares.",
    )
    parser.add_argument("--corpus", default=DEFAULT_CORPUS_DIR)
    parser.add_argument(
        "--roster",
        default=str(roster.DEFAULT_ROSTER_PATH),
        help="the standing roster the scored series are split by",
    )
    parser.add_argument(
        "--accept-cost",
        metavar="USD|unknown",
        help="accept this many dollars without a prompt, for scripts. The run"
        " refuses when the estimate is higher. Pass 'unknown' to accept a cost"
        " nobody can state. Omit it and the terminal asks you to type the"
        " amount back — an enter never proceeds.",
    )
    parser.add_argument(
        "--ledger",
        default=str(ledger.DEFAULT_LEDGER_PATH),
        help="the vote ledger the scorer reads standings from",
    )
    parser.add_argument(
        "--out",
        help=(
            "where to write the run artifact. Each finished case's whole report"
            " is written beside it, as <out>.reports/<case>.report.json"
        ),
    )
    parser.add_argument(
        "--require-certified",
        action="store_true",
        help="fail the run when its fingerprints are not in the blessed manifest",
    )


def _calibrate_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--out", help="where to write the agreement report")
    parser.add_argument(
        "--corpus",
        default=str(DEFAULT_CORPUS_DIR),
        help="corpus root, for the flow maps the rule resolves endpoints against",
    )


def _promote_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("artifact", help="the run artifact to promote")
    parser.add_argument(
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
    parser.add_argument(
        "--yes",
        action="store_true",
        help="write the files; without it the promotion is only previewed",
    )


def _score_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("artifact", help="a sweep artifact with a .reports/ dir")
    parser.add_argument(
        "--ledger",
        default=str(ledger.DEFAULT_LEDGER_PATH),
        help="the vote ledger the standings are read from",
    )
    parser.add_argument(
        "--roster",
        default=str(roster.DEFAULT_ROSTER_PATH),
        help="the standing roster the scored series are split by",
    )
    parser.add_argument(
        "--corpus",
        default=str(DEFAULT_CORPUS_DIR),
        help="corpus root, for the reference sets and flow maps",
    )
    parser.add_argument(
        "--out",
        help="where to write the scored artifact; without it the input is"
        " rewritten in place",
    )


def _pairing_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("artifact", help="a sweep artifact with a .reports/ dir")
    parser.add_argument("--case", required=True, help="which corpus case to pair")
    parser.add_argument(
        "--corpus",
        default=str(DEFAULT_CORPUS_DIR),
        help="corpus root, for the reference set this pairs against",
    )
    parser.add_argument(
        "--html",
        help="where to write the reading page. Refused inside this repository:"
        " the page carries ASVS text under CC BY-SA 4.0",
    )
    parser.add_argument(
        "--json", help="where to write the identifiers alone, which carry no ASVS text"
    )


def _rekey_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--ledger", default=str(ledger.DEFAULT_LEDGER_PATH), help="the vote ledger"
    )
    parser.add_argument(
        "--yes", action="store_true", help="write; without it this is a preview"
    )


def _review_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "artifact",
        nargs="+",
        help="one or more sweep artifacts, each with a .reports/ dir. Several"
        " sweeps of one configuration are what make a finding's run count"
        " readable, and the queue asks first about what they disagree on",
    )
    parser.add_argument(
        "--voter",
        required=True,
        help="whose queue to report; a finding this person has answered is not"
        " waiting for them, even when somebody else has not answered it",
    )
    parser.add_argument(
        "--ledger", default=str(ledger.DEFAULT_LEDGER_PATH), help="the vote ledger"
    )


def _stability_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "artifact",
        nargs="+",
        help=(
            "two or more scored run artifacts of the same corpus. Cases scored"
            " by only some of them are excluded and named."
        ),
    )
    parser.add_argument("--out", help="where to write the stability report")


def _compare_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("before", help="the sweep from before the edit")
    parser.add_argument("after", help="the sweep from after it")
    parser.add_argument("--out", help="where to write the comparison")


def _submit_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "kind",
        choices=sorted(submit.KINDS),
        help="what this PR carries; one kind per PR",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="stop after the checklist, before any branch or PR is made."
        " Your roster line is still added if you have none, because you need"
        " it either way.",
    )
    parser.add_argument(
        "--artifact",
        action="append",
        default=[],
        help="a sweep artifact to assemble into the Baseline before the"
        " checks run (baseline kind only; repeat per sweep)",
    )


def _verify_contribution_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--author",
        required=True,
        help="the GitHub login that opened the PR",
    )


def _agreement_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--ledger", default=str(ledger.DEFAULT_LEDGER_PATH), help="the vote ledger"
    )
    parser.add_argument(
        "--roster",
        default=str(roster.DEFAULT_ROSTER_PATH),
        help="the standing roster",
    )


#: Every subcommand, in the order ``--help`` lists them.
#:
#: Each ``run`` lives in the module that owns its subject, which is where
#: ``submit`` and ``verify-contribution`` already were. What stays here is the
#: sweep and the readings taken over one: ``run`` *is* this module, and
#: ``score``, ``promote``, ``pairing``, ``stability`` and ``calibrate`` each
#: share a private helper with it, so moving one would split a pair rather than
#: place it.
COMMANDS: dict[str, Command] = {
    "run": Command(
        help="run one eval mode over the corpus",
        run=command_run,
        arguments=_run_arguments,
    ),
    "calibrate": Command(
        help="measure rule-label agreement over the fixtures",
        run=command_calibrate,
        arguments=_calibrate_arguments,
    ),
    "promote": Command(
        help="bless a finished sweep's execution identities from its artifact",
        run=command_promote,
        arguments=_promote_arguments,
    ),
    "score": Command(
        help="re-score a finished sweep against the ledger (no credentials)",
        run=command_score,
        arguments=_score_arguments,
    ),
    "pairing": Command(
        help="the two sides of one case's applicability disagreement (no credentials)",
        run=command_pairing,
        arguments=_pairing_arguments,
    ),
    "rekey": Command(
        help="recompute every vote's fingerprint under another rule",
        run=ledger.command_rekey,
        arguments=_rekey_arguments,
    ),
    "review": Command(
        help="what a reviewer has waiting over a finished sweep",
        run=queue.command_review,
        arguments=_review_arguments,
    ),
    "stability": Command(
        help="compare finished sweeps for run-to-run stability (no credentials)",
        run=command_stability,
        arguments=_stability_arguments,
    ),
    "compare": Command(
        help="what a prompt edit did: instruction delta beside score delta"
        " (no credentials)",
        run=instruction_delta.command_compare,
        arguments=_compare_arguments,
    ),
    "sitting-import": Command(
        help="apply one offline sitting envelope to this tree (no credentials)",
        run=envelope.command_import,
        arguments=envelope.import_arguments,
    ),
    "submit": Command(
        help="open a contribution PR through gh, after running its CI checks locally",
        run=submit.command_submit,
        arguments=_submit_arguments,
    ),
    "verify-contribution": Command(
        help="run a contribution PR's checks against its author (what CI runs)",
        run=submit.command_verify,
        arguments=_verify_contribution_arguments,
    ),
    "agreement": Command(
        help="how far each pair of voters agrees, for a promotion decision"
        " (no credentials)",
        run=standings.command_agreement,
        arguments=_agreement_arguments,
    ),
    "comparison": Command(
        help="rebuild evals/baselines/README.md from the merged Baselines"
        " (no credentials)",
        run=comparison.command_comparison,
    ),
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, command in COMMANDS.items():
        sub = subparsers.add_parser(name, help=command.help)
        if command.arguments is not None:
            command.arguments(sub)
        sub.set_defaults(func=command.run)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
