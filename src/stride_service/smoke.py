"""The live half of the provider contract: did this vendor actually serve the graph.

:mod:`stride_service.conformance` answers what a provider *would be asked for*
— from the pinned model map, for all three vendors at once, with no credential
and no egress. That is what lets it run on every pull request, and it is also
the exact limit of what it can claim: nothing in it is evidence that any vendor
has ever served a request.

This module is the other half. It is deliberately the **cheapest** thing that
can produce that evidence: one small system, once, through the shipped graph,
on whichever pair this deployment selects. Eight answers come back — the eight
[#116](https://github.com/mstarks01/work-agent/issues/116) names as the minimum
a provider must satisfy before its coverage counts as exercised:

    model binding                    the routes each node asked for
    structured extraction            a schema-valid model came back
    analyst structured output        all six category lanes parsed
    critic structured output         the ruling parsed
    sampling parameter validation    the provider took this tier's params
    served-model capture             what actually answered
    execution fingerprint            the generation identity that implies
    provenance                       the record, recomputable from itself

**Not an eval.** Nothing here scores threat-model quality, and nothing here
fails because one model writes weaker threats than another — that split is the
whole of this repository's answer to vendor neutrality, and collapsing it is
what produced the imbalance #116 was filed about. Quality lives in ``evals/``
and is expected to differ. What this asks is whether the *application* works on
this provider, which every supported provider must answer the same way.

**The tri-state is the same one, and for the same reason.** A check whose
question the provider left unanswered — no served build on the response, so no
fingerprint to verify — reports ``unknown`` and does not fail the run. Grading
it as a failure would invent a defect; grading it as a pass would invent an
assurance. Only a check the *application* got wrong is a failure.

Runnable wherever the service is: ``python -m stride_service.smoke`` builds the
deployment from the environment like every other entry point, so an operator
with a fresh key can ask "does my provider actually work here" for the price of
one small job, and CI asks the same question with the same command.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from stride_service.deployment import Deployment
from stride_service.engine import StrideEngine
from stride_service.errors import ConfigError
from stride_service.graph import (
    ANALYZE_GRAPH_NODES,
    CRITIC_NODE,
    EXTRACT_NODE,
    RECRITIC_NODE,
    TIER_NODE_BY_GRAPH_NODE,
)
from stride_service.jobs import PipelineCompleted, PipelineRejected
from stride_service.model_tiers import TierName
from stride_service.report import NodeRun, StrideReport
from stride_service.sampling import TierSampling, sampling_fingerprint
from stride_service.sources import Source

# The fixture, and it is part of the contract rather than a sample: every
# provider is asked the same question, so a lane that swapped in its own text
# would be measuring a different one. Kept in the package next to the code that
# runs it, so a wheel-installed deployment can smoke its own provider without
# the repository.
#
# Sized down to what the eight checks actually need: three elements across two
# zones, one flow crossing between them, and two stated unknowns for the
# category agents to have something to rule on. The corpus cases run 800-2,000
# characters against a *quality* bar; this one runs against none, so every
# sentence it does not need is spend on every lane forever.
#
# Written to be quotable. The validity gate requires each element's citation to
# appear verbatim in the source, so prose that gestures at a system without
# naming its parts fails extraction on a fixture the provider handled correctly.
SMOKE_SYSTEM_NAME = "Provider smoke: notes service"

SMOKE_SOURCE_TEXT = """\
A customer writes notes in the notes web client.
The notes web client runs in the customer's own browser, outside our network.
The notes web client calls the notes API over HTTPS.

The notes API runs in our backend zone.
The notes API stores every note in the notes database.
The notes database runs in the same backend zone as the notes API.

This description does not say how the notes API authenticates a customer.
It does not say whether the notes database encrypts stored notes at rest.
"""

SMOKE_SOURCE = Source.description(SMOKE_SOURCE_TEXT, label="smoke-notes-service")

# Who this run says it is, to the ADK session and to nothing else. Named rather
# than borrowed from the engine's default so a provider-side log shows which of
# this service's callers spent the tokens.
SMOKE_CALLER = "provider-smoke"

# The LLM nodes a complete run must have executed. ``repair`` and ``recritic``
# are absent deliberately: both are re-asks the graph takes only when a first
# answer failed its mechanical check, so requiring them would fail the runs that
# went right, and forbidding them would fail the application behaving correctly.
REQUIRED_NODES: tuple[str, ...] = (EXTRACT_NODE, *ANALYZE_GRAPH_NODES, CRITIC_NODE)

# How much of a provider's error text reaches the summary. Long enough to name
# the failure, short enough that a provider echoing the request body does not
# bury the rest of the report.
MAX_FAILURE_CHARS = 2000

BINDING = "model binding"
EXTRACTION = "structured extraction"
ANALYST = "analyst structured output"
CRITIC = "critic structured output"
SAMPLING = "sampling parameter validation"
SERVED = "served-model capture"
FINGERPRINT = "execution fingerprint generation"
PROVENANCE = "provenance generation"

CHECKS: tuple[str, ...] = (
    BINDING,
    EXTRACTION,
    ANALYST,
    CRITIC,
    SAMPLING,
    SERVED,
    FINGERPRINT,
    PROVENANCE,
)


class CheckResult(StrEnum):
    """One check's answer, with the same third value the capability matrix has.

    ``UNKNOWN`` is for a question the *provider* left unanswered — a response
    carrying no served build, so there is no identity to fingerprint. It is not
    a soft failure and does not set the exit status: the application did what it
    should with what it was given, and reporting that as a defect would be
    inventing one. See :class:`~stride_service.conformance.Capability`, which
    draws the same line for the same reason.
    """

    PASSED = "passed"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Check:
    """One check, its answer, and the evidence for it.

    ``detail`` is always populated, including on a pass. A green row that says
    only "passed" cannot be audited — "8 executions, all fingerprinted" can.
    """

    name: str
    result: CheckResult
    detail: str

    def to_json(self) -> dict[str, str]:
        return {"check": self.name, "result": str(self.result), "detail": self.detail}


@dataclass(frozen=True)
class SmokeResult:
    """What one lane learned about one deployment's providers.

    ``tiers`` records the ``(vendor, model)`` each tier selected rather than a
    single vendor name: a deployment may run its two tiers on two vendors, and a
    result that flattened that would name the wrong one in half the cases it is
    most worth reading.

    ``failure`` is the run's own error, when there was one — the adapters that
    would not build, the provider that refused, the graph that raised. It is
    separate from the checks because a run that never happened has no checks to
    report, only questions it could not reach.
    """

    tiers: dict[TierName, dict[str, str]]
    checks: tuple[Check, ...]
    failure: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def exercised(self) -> bool:
        """Whether a provider actually served this run.

        False when the run never reached one — an unbuildable adapter, a
        refused request. The distinction the whole module exists for: an
        unexercised lane must never read as a passing one.
        """
        return self.failure is None

    @property
    def failed(self) -> bool:
        """Whether this lane should fail its job. ``UNKNOWN`` never does."""
        return self.failure is not None or any(
            check.result is CheckResult.FAILED for check in self.checks
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "tiers": self.tiers,
            "exercised": self.exercised,
            "failed": self.failed,
            "checks": [check.to_json() for check in self.checks],
            "failure": self.failure,
            "notes": list(self.notes),
        }


def _llm_runs(report: StrideReport) -> list[NodeRun]:
    """Every node execution that went to a model.

    Keyed on the graph's own node -> tier table rather than on "has a requested
    model", so a node that reached a provider without recording what it asked
    for is a finding here instead of a row this walk skips.
    """
    return [run for run in report.nodes if run.node in TIER_NODE_BY_GRAPH_NODE]


def _selected_routes(deployment: Deployment) -> dict[str, str]:
    """Each LLM graph node -> the route this deployment's config selects for it."""
    return {
        node: deployment.tiers.resolve_model(tier_node).route
        for node, tier_node in TIER_NODE_BY_GRAPH_NODE.items()
    }


def _check_binding(report: StrideReport, deployment: Deployment) -> Check:
    """Every node asked for the route its tier selects, and both tiers ran.

    The live counterpart of the offline binding assertions: those prove the
    adapters *build*, this proves the built adapter is the one the node reached
    a provider through.
    """
    expected = _selected_routes(deployment)
    runs = _llm_runs(report)
    wrong = [
        f"{run.node} asked for {run.requested_model!r},"
        f" config selects {expected[run.node]!r}"
        for run in runs
        if run.requested_model != expected[run.node]
    ]
    if wrong:
        return Check(BINDING, CheckResult.FAILED, "; ".join(wrong))
    # Folded to one row per tier rather than one per node: eight rows of the
    # same two routes is the same fact eight times, and the tiers are what a
    # reader is checking against their own config.
    by_tier = {
        deployment.tier_of(run.node): run.requested_model
        for run in runs
        if run.requested_model
    }
    routes = ", ".join(f"{tier}={route}" for tier, route in sorted(by_tier.items()))
    return Check(
        BINDING, CheckResult.PASSED, f"{len(runs)} node executions on {routes}"
    )


def _check_extraction(report: StrideReport, deployment: Deployment) -> Check:
    """``extract`` returned a model the shipped validity gate accepted.

    Reached at all only on a completed run, since a model the gate rejects comes
    back as a rejection rather than a report — so what is left to assert is that
    the model is not vacuous. An extraction with no elements would pass every
    schema check and describe nothing.
    """
    if not any(run.node == EXTRACT_NODE for run in report.nodes):
        return Check(EXTRACTION, CheckResult.FAILED, "extract did not run")
    elements = list(report.system_model.elements())
    if not elements:
        return Check(
            EXTRACTION, CheckResult.FAILED, "extract returned a model with no elements"
        )
    return Check(
        EXTRACTION,
        CheckResult.PASSED,
        f"elements: {len(elements)},"
        f" boundary crossings: {len(report.boundary_crossings)}",
    )


def _check_analyst(report: StrideReport, deployment: Deployment) -> Check:
    """All six category lanes produced output the service could parse.

    Presence of the six executions is the assertion, not the number of threats:
    a lane whose emission fails its schema never lands a node run at all, and a
    lane that examined the system and rightly found nothing is not a defect.
    Threat counts are model quality and belong to ``evals/``.
    """
    ran = {run.node for run in report.nodes}
    missing = [node for node in ANALYZE_GRAPH_NODES if node not in ran]
    if missing:
        return Check(ANALYST, CheckResult.FAILED, f"lanes that did not run: {missing}")
    drafts = sum(entry.drafts for entry in report.coverage)
    return Check(
        ANALYST,
        CheckResult.PASSED,
        f"all {len(ANALYZE_GRAPH_NODES)} category lanes ran; drafts merged: {drafts}",
    )


def _check_critic(report: StrideReport, deployment: Deployment) -> Check:
    """The critic ruled, and its ruling reached the report.

    A re-ask is recorded rather than penalised. ``recritic`` runs when the
    critic's first answer failed the mechanical check, which is the application
    handling a provider difference correctly — the failure it would be evidence
    of is ``critic_failed``, and that path never produces a report to inspect.
    """
    if not any(run.node == CRITIC_NODE for run in report.nodes):
        return Check(CRITIC, CheckResult.FAILED, "critic did not run")
    ruled = len(report.threats) + len(report.rejected_threats)
    re_asked = any(run.node == RECRITIC_NODE for run in report.nodes)
    suffix = " (after one re-ask)" if re_asked else ""
    return Check(
        CRITIC,
        CheckResult.PASSED,
        f"threats ruled: {ruled}, rejected: {len(report.rejected_threats)}{suffix}",
    )


def _check_sampling(report: StrideReport, deployment: Deployment) -> Check:
    """The provider accepted this deployment's resolved params, and they are recorded.

    The build-time gate asked whether the provider *would* take them; this is
    the answer from the provider itself, since an unsupported param comes back
    as a refused request rather than as a quietly dropped field. What remains to
    assert is that the values are in the report in the clear, because a
    fingerprint nobody can recompute is an assertion rather than evidence.
    """
    tiers_run = {deployment.tier_of(run.node) for run in _llm_runs(report)}
    missing = sorted(tier for tier in tiers_run if tier not in report.sampling)
    if missing:
        return Check(
            SAMPLING,
            CheckResult.FAILED,
            f"tiers that ran with no recorded sampling block: {missing}",
        )
    rendered = "; ".join(
        f"{tier}: {_render_params(report.sampling[tier])}" for tier in sorted(tiers_run)
    )
    return Check(SAMPLING, CheckResult.PASSED, rendered)


def _render_params(params: Mapping[str, float | int | None]) -> str:
    """One tier's set params, for a summary row. Unset params are omitted."""
    return ", ".join(
        f"{name}={value}" for name, value in params.items() if value is not None
    )


def _check_served(report: StrideReport, deployment: Deployment) -> Check:
    """What actually answered, per execution — where the provider said.

    ``UNKNOWN`` rather than a failure when it did not. A provider that reports
    no build on its responses is a capability difference, and the application's
    behaviour is already correct: :mod:`stride_service.execution` records no
    served model and derives no fingerprint from one, rather than attesting to
    the string that was requested.
    """
    runs = _llm_runs(report)
    served = [run for run in runs if run.model]
    if not served:
        return Check(
            SERVED,
            CheckResult.UNKNOWN,
            f"no response carried a served build; {len(runs)} executions",
        )
    if len(served) < len(runs):
        silent = sorted(run.node for run in runs if not run.model)
        return Check(
            SERVED,
            CheckResult.UNKNOWN,
            f"{len(served)}/{len(runs)} executions reported a build;"
            f" silent on: {silent}",
        )
    builds = sorted({run.model for run in served if run.model})
    return Check(
        SERVED, CheckResult.PASSED, f"executions: {len(served)}, builds: {builds}"
    )


def _check_fingerprint(report: StrideReport, deployment: Deployment) -> Check:
    """Every execution with a served build carries its generation identity.

    ``UNKNOWN`` when none did: with nothing to hash there is no fingerprint to
    require, which is the same rule
    :meth:`~stride_service.execution.GraphExecutor._fingerprint` follows. A
    served build *without* a fingerprint is the failure — that pair is the
    identity certification is computed over.
    """
    served = [run for run in _llm_runs(report) if run.model]
    if not served:
        return Check(
            FINGERPRINT,
            CheckResult.UNKNOWN,
            "no served build was reported, so no identity could be hashed",
        )
    unfingerprinted = sorted(run.node for run in served if not run.sampling_fingerprint)
    if unfingerprinted:
        return Check(
            FINGERPRINT,
            CheckResult.FAILED,
            f"served but not fingerprinted: {unfingerprinted}",
        )
    return Check(
        FINGERPRINT,
        CheckResult.PASSED,
        f"{len({run.sampling_fingerprint for run in served})} distinct identities"
        f" over {len(served)} executions",
    )


def _check_provenance(report: StrideReport, deployment: Deployment) -> Check:
    """The record is complete, and it follows from itself.

    Two things, because either alone is weak. Complete: every node the graph had
    to run appears, so a lane that vanished cannot pass by leaving no trace.
    Self-consistent: each recorded fingerprint is **recomputed** from the served
    build and the tier's own sampling block as the report carries them, so the
    stored hash is evidence rather than an assertion, and a report whose numbers
    do not follow from each other is refused (OWASP A08). It is the same
    recomputation a promotion runs before anything reaches the blessed manifest.
    """
    ran = {run.node for run in report.nodes}
    missing = [node for node in REQUIRED_NODES if node not in ran]
    if missing:
        return Check(
            PROVENANCE, CheckResult.FAILED, f"no execution recorded for: {missing}"
        )
    mismatched = _fingerprint_mismatches(report, deployment)
    if mismatched:
        return Check(PROVENANCE, CheckResult.FAILED, "; ".join(mismatched))
    return Check(
        PROVENANCE,
        CheckResult.PASSED,
        f"{len(_llm_runs(report))} executions recorded;"
        " every fingerprint recomputes from the report's own sampling block",
    )


def _fingerprint_mismatches(report: StrideReport, deployment: Deployment) -> list[str]:
    """Recorded fingerprints that do not follow from the report's own numbers."""
    mismatches = []
    for run in _llm_runs(report):
        if run.model is None or run.sampling_fingerprint is None:
            continue
        tier = deployment.tier_of(run.node)
        recorded = report.sampling.get(tier)
        if recorded is None:
            mismatches.append(f"{run.node}: tier {tier!r} has no recorded sampling")
            continue
        recomputed = sampling_fingerprint(
            run.model, TierSampling.model_validate(recorded)
        )
        if recomputed != run.sampling_fingerprint:
            mismatches.append(
                f"{run.node}: recorded {run.sampling_fingerprint} but"
                f" {run.model} on tier {tier!r} recomputes to {recomputed}"
            )
    return mismatches


_CHECKERS = (
    _check_binding,
    _check_extraction,
    _check_analyst,
    _check_critic,
    _check_sampling,
    _check_served,
    _check_fingerprint,
    _check_provenance,
)


def checks_for(report: StrideReport, deployment: Deployment) -> tuple[Check, ...]:
    """Every check, against one completed report. No provider, no network.

    Separated from :func:`run_smoke` so the checks themselves are testable
    offline: a report is data, and asserting that a truncated one fails the
    right check is not something a live lane should have to demonstrate by
    breaking a provider.
    """
    return tuple(check(report, deployment) for check in _CHECKERS)


def _unreached(reason: str) -> tuple[Check, ...]:
    """Every check, unanswered, when the run did not get far enough to ask.

    ``UNKNOWN`` rather than ``FAILED``, and the run's own ``failure`` is what
    carries the bad news. A lane that could not build its adapters has learned
    nothing about whether the critic parses, and saying it failed would be a
    finding this run did not make.
    """
    return tuple(Check(name, CheckResult.UNKNOWN, reason) for name in CHECKS)


def _tier_summary(deployment: Deployment) -> dict[TierName, dict[str, str]]:
    """What each tier selected, for the result header."""
    return {
        tier: {
            "vendor": selection.vendor,
            "model": selection.model,
            "route": selection.route,
        }
        for tier, selection in deployment.tiers.tiers.items()
    }


def _redacted(text: str, deployment: Deployment) -> str:
    """A provider's error text with any declared credential value removed.

    Errors from a provider library can echo the request that produced them, and
    this text goes into a CI job summary that outlives the run. The vendor
    registry already knows which variables hold credential material for the
    selected vendors, so the substitution is exact rather than a guess at what a
    key looks like (OWASP A09). Names survive; values never do.
    """
    for selection in deployment.tiers.tiers.values():
        for var in selection.vendor_entry.required_env_vars:
            value = deployment.env.get(var, "").strip()
            if value:
                text = text.replace(value, f"${{{var}}}")
    return text[:MAX_FAILURE_CHARS]


async def run_smoke(deployment: Deployment | None = None) -> SmokeResult:
    """Run one small job through the shipped graph and report what happened.

    Every failure mode is a result rather than an exception, because the caller
    is a CI lane whose job is to *report*: an unbuildable adapter, a refused
    request and a rejected input are three different findings, and a traceback
    flattens them into "the step failed".

    The one thing that still raises is a broken deployment — an unreadable or
    invalid config file — which is not a fact about a provider at all.
    """
    deployment = deployment or Deployment.from_env()
    tiers = _tier_summary(deployment)
    try:
        engine = StrideEngine.from_deployment(deployment)
    except ConfigError as exc:
        # The build-time gates: a missing credential or a param this pair will
        # not take. Both name the variable or the tier and never a value.
        return SmokeResult(
            tiers=tiers,
            checks=_unreached("the adapters did not build, so no provider was reached"),
            failure=_redacted(f"{type(exc).__name__}: {exc}", deployment),
        )

    try:
        outcome = await engine.analyze(
            [SMOKE_SOURCE], system_name=SMOKE_SYSTEM_NAME, caller=SMOKE_CALLER
        )
    except Exception as exc:  # noqa: BLE001 -- any provider failure is a result here
        return SmokeResult(
            tiers=tiers,
            checks=_unreached("the run did not complete"),
            failure=_redacted(f"{type(exc).__name__}: {exc}", deployment),
        )

    if isinstance(outcome, PipelineRejected):
        # The validity gate refused the extraction. Reported as an extraction
        # failure with the gate's own codes rather than as a crash: on this
        # fixture, whose elements are all named in quotable prose, a rejection
        # is a real finding about what this pair returns.
        issues = "; ".join(f"{issue.code}: {issue.message}" for issue in outcome.issues)
        checks = (
            Check(
                EXTRACTION, CheckResult.FAILED, f"the validity gate refused: {issues}"
            ),
            *(
                Check(name, CheckResult.UNKNOWN, "the run stopped at extraction")
                for name in CHECKS
                if name != EXTRACTION
            ),
        )
        return SmokeResult(tiers=tiers, checks=checks)

    assert isinstance(outcome, PipelineCompleted)
    report = outcome.report
    return SmokeResult(
        tiers=tiers,
        checks=checks_for(report, deployment),
        notes=(
            (
                f"{report.summary.threat_count} threats over"
                f" {report.summary.elements_analyzed} elements"
                " — a count, not a quality judgement"
            ),
        ),
    )


def render_markdown(result: SmokeResult) -> str:
    """The result as a Markdown section, for a CI job summary."""
    lines = ["| tier | vendor | model |", "| --- | --- | --- |"]
    for tier, selection in result.tiers.items():
        lines.append(f"| {tier} | {selection['vendor']} | `{selection['model']}` |")
    lines += ["", "| check | result | detail |", "| --- | --- | --- |"]
    for check in result.checks:
        lines.append(f"| {check.name} | {check.result} | {check.detail} |")
    if result.failure:
        lines += ["", "**The run did not complete.**", "", "```", result.failure, "```"]
    if not result.exercised:
        lines += ["", "This lane is **unexercised**: no provider served a request."]
    lines += ["", *(f"- {note}" for note in result.notes)]
    return "\n".join(lines).rstrip() + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m stride_service.smoke``. Markdown on stdout, JSON on request.

    Exits non-zero when a check failed or the run never happened, and zero on a
    run whose only unanswered questions are the provider's to answer. That is
    the same line :class:`CheckResult` draws — an ``unknown`` is a capability
    difference and must not turn a lane red, or the next person to read a red
    lane learns nothing from it.
    """
    parser = argparse.ArgumentParser(
        description="Run one small job through this deployment's providers."
    )
    parser.add_argument(
        "--out", type=Path, help="write the result as JSON to this path as well"
    )
    args = parser.parse_args(argv)

    result = asyncio.run(run_smoke())
    print(render_markdown(result))
    if args.out:
        args.out.write_text(
            json.dumps(result.to_json(), indent=2) + "\n", encoding="utf-8"
        )
    return 1 if result.failed else 0


if __name__ == "__main__":
    sys.exit(main())
