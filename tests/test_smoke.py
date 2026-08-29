"""The live provider smoke, checked without a provider.

The smoke's whole value is that it runs against a real vendor — and that is
exactly why its *checks* have to be exercised here. A live lane can demonstrate
that a good run passes; it cannot demonstrate that a bad one fails, because
demonstrating that would mean breaking a provider on purpose. So the report is
treated as what it is — data — and every failure the checks exist to catch is
constructed and asserted offline.

The three-way answer is the part most worth pinning. ``unknown`` must not fail a
lane and ``passed`` must not absorb a question the provider never answered; a
suite that let either slide would leave the smoke reporting assurance it does
not have, which is the specific dishonesty
[#116](https://github.com/mstarks01/work-agent/issues/116) asked to remove.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from analysis_service import smoke
from analysis_service.deployment import Deployment
from analysis_service.engine import Engine
from analysis_service.graph import (
    ASSEMBLE_NODE,
    EXTRACT_NODE,
    tier_node_by_graph_node,
)
from analysis_service.jobs import (
    JobRecord,
    NodeCallback,
    PipelineCompleted,
    PipelineOutcome,
    PipelineRejected,
)
from analysis_service.report import NodeRun, Report
from analysis_service.sampling import sampling_fingerprint
from analysis_service.smoke import (
    ANALYST,
    BINDING,
    CHECKS,
    CRITIC,
    EXTRACTION,
    FINGERPRINT,
    PROVENANCE,
    SAMPLING,
    SERVED,
    SMOKE_SOURCE,
    Check,
    CheckResult,
    SmokeResult,
    _redacted,
    analyze_nodes,
    checks_for,
    critic_nodes,
    render_markdown,
    required_nodes,
    run_smoke,
)
from analysis_service.validation import ValidationIssue
from analysis_service.vendors import join_served
from tests.factories import (
    DEFAULT_FRAMEWORKS,
    TEST_TIER_ENV,
    sample_analysis,
    sample_report,
    sample_selection,
    served_build,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# A deployment is buildable with no credentials at all: the configs load
# eagerly and the adapters do not, which is what lets every check below run in
# the offline lane. The selection is the shared test pair, deliberately
# two-vendor — a result that flattened its two tiers into one vendor name would
# name the wrong one here.
DEPLOYMENT = Deployment.from_env(env=TEST_TIER_ENV)

# What a complete run of this install's selection must have executed. A
# function of the selection rather than a constant: every framework brings its
# own lanes and its own critic, and the smoke fixture runs every carried one
# because each brings tier keys an operator may point at a different vendor.
LLM_NODES = required_nodes(DEFAULT_FRAMEWORKS)
ANALYZE_NODES = analyze_nodes(DEFAULT_FRAMEWORKS)
(ANALYSIS_CRITIC_NODE,) = critic_nodes(DEFAULT_FRAMEWORKS)
#: This graph's node -> tier map, built for the selection above.
TIER_NODES = tier_node_by_graph_node(DEFAULT_FRAMEWORKS)


def smoke_report(**overrides: object) -> Report:
    """A completed report shaped like one the smoke fixture would produce.

    Built from the shared report factory with a full LLM execution record laid
    over it: one run per node the graph must execute, each carrying the route
    its tier selects, a served build, and the fingerprint that pair implies —
    computed here by the same function the executor calls, so a test asserting
    that the recomputation agrees is not comparing a constant to itself.
    """
    report = sample_report()
    sampling = {
        tier: params.model_dump() for tier, params in DEPLOYMENT.sampling.tiers.items()
    }
    fields = {
        **report.model_dump(),
        "nodes": [node_run(node) for node in LLM_NODES]
        + [NodeRun(node=ASSEMBLE_NODE, duration_ms=20).model_dump()],
        "sampling": sampling,
        **overrides,
    }
    return Report.model_validate(fields)


def node_run(node: str, **overrides: object) -> dict[str, object]:
    """One LLM node's execution record, consistent by construction."""
    requested = DEPLOYMENT.tiers.resolve_model(TIER_NODES[node]).route
    served = join_served(requested, served_build(requested))
    tier = DEPLOYMENT.tier_of(node)
    run = {
        "node": node,
        "model": served,
        "requested_model": requested,
        "sampling_fingerprint": sampling_fingerprint(
            served, DEPLOYMENT.sampling.for_tier(tier)
        ),
        "duration_ms": 1200,
    }
    run.update(overrides)
    return run


class StubRunner:
    """A pipeline runner that answers with whatever the test handed it."""

    def __init__(self, outcome: PipelineOutcome | Exception) -> None:
        self._outcome = outcome

    async def run(self, job: JobRecord, on_node: NodeCallback) -> PipelineOutcome:
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def result_for(report: Report) -> dict[str, Check]:
    """Every check against one report, keyed by name for a targeted assertion."""
    return {check.name: check for check in checks_for(report, DEPLOYMENT)}


class TestACleanRun:
    """What a provider that works looks like, so the failures below mean something."""

    def test_every_check_passes(self):
        for check in checks_for(smoke_report(), DEPLOYMENT):
            assert check.result is CheckResult.PASSED, f"{check.name}: {check.detail}"

    def test_every_check_carries_evidence_even_when_it_passes(self):
        """A green row that says only "passed" cannot be audited."""
        for check in checks_for(smoke_report(), DEPLOYMENT):
            assert check.detail.strip()

    def test_the_result_is_exercised_and_does_not_fail(self):
        result = SmokeResult(tiers={}, checks=checks_for(smoke_report(), DEPLOYMENT))
        assert result.exercised
        assert not result.failed

    def test_the_checks_are_the_eight_the_issue_names(self):
        """The contract's extent, asserted rather than remembered."""
        assert tuple(
            check.name for check in checks_for(smoke_report(), DEPLOYMENT)
        ) == (CHECKS)


class TestTheApplicationsFailures:
    """Every check that fails does so for the application's fault, not the model's."""

    def test_a_node_routed_somewhere_the_config_did_not_select_fails_binding(self):
        """The live counterpart of the offline binding assertions.

        Those prove the adapters build; this proves the adapter a node actually
        reached a provider through is the one its tier selects. A node that
        quietly ran on another pair would otherwise leave every downstream check
        green — the report would still parse, still be fingerprinted, still be
        complete.
        """
        report = smoke_report(
            nodes=[
                node_run(EXTRACT_NODE, requested_model="openai/gpt-4o"),
                *(node_run(node) for node in LLM_NODES[1:]),
            ]
        )
        assert result_for(report)[BINDING].result is CheckResult.FAILED

    def test_a_model_with_no_elements_fails_extraction(self):
        """Schema-valid and vacuous are not the same answer.

        An empty model satisfies every schema in the service and describes no
        system at all, so the check that a model *parsed* would pass on it.
        """
        report = smoke_report(
            system_model={},
            boundary_crossings=[],
            elements_analyzed=0,
            analyses=[sample_analysis(threats=[], rejected_threats=[])],
        )
        assert result_for(report)[EXTRACTION].result is CheckResult.FAILED

    def test_a_missing_category_lane_fails_the_analyst_check(self):
        """Six lanes are what makes the output a STRIDE model.

        A lane that never ran is invisible in the threat count — a lane can
        legitimately find nothing — so absence of the execution is the only
        thing that separates "examined and clear" from "never looked".
        """
        without_one = [node for node in LLM_NODES if node != ANALYZE_NODES[0]]
        report = smoke_report(nodes=[node_run(node) for node in without_one])
        checks = result_for(report)
        assert checks[ANALYST].result is CheckResult.FAILED
        assert ANALYZE_NODES[0] in checks[ANALYST].detail

    def test_a_run_with_no_critic_fails_the_critic_check(self):
        report = smoke_report(
            nodes=[node_run(node) for node in LLM_NODES if node != ANALYSIS_CRITIC_NODE]
        )
        assert result_for(report)[CRITIC].result is CheckResult.FAILED

    def test_a_tier_that_ran_with_no_recorded_sampling_fails(self):
        """A fingerprint nobody can recompute is an assertion, not evidence."""
        report = smoke_report(sampling={})
        assert result_for(report)[SAMPLING].result is CheckResult.FAILED

    def test_a_served_build_without_a_fingerprint_fails(self):
        """The pair is the identity; half of it is not a lesser record."""
        report = smoke_report(
            nodes=[
                node_run(EXTRACT_NODE, sampling_fingerprint=None),
                *(node_run(node) for node in LLM_NODES[1:]),
            ]
        )
        assert result_for(report)[FINGERPRINT].result is CheckResult.FAILED

    def test_a_fingerprint_that_does_not_follow_from_the_report_fails_provenance(self):
        """The recomputation, which is what makes the stored hash evidence.

        A hand-edited artifact is the case this refuses (OWASP A08): every other
        check passes on it, because the numbers are individually well-formed and
        only their relationship is wrong.
        """
        report = smoke_report(
            nodes=[
                node_run(EXTRACT_NODE, sampling_fingerprint="0" * 64),
                *(node_run(node) for node in LLM_NODES[1:]),
            ]
        )
        checks = result_for(report)
        assert checks[PROVENANCE].result is CheckResult.FAILED
        assert checks[FINGERPRINT].result is CheckResult.PASSED

    def test_a_missing_execution_record_fails_provenance(self):
        report = smoke_report(
            nodes=[node_run(node) for node in LLM_NODES if node != EXTRACT_NODE]
        )
        assert result_for(report)[PROVENANCE].result is CheckResult.FAILED


class TestWhatTheProviderDidNotAnswer:
    """The third value, which must not read as either of the other two."""

    def test_no_served_build_is_unknown_rather_than_failed(self):
        """A provider that reports no build is a difference, not a defect.

        The application is already doing the right thing with it: no served
        model recorded, no fingerprint derived from one. Failing the lane would
        report a defect nobody found; passing it would report an identity nobody
        captured.
        """
        report = smoke_report(
            nodes=[
                node_run(node, model=None, sampling_fingerprint=None)
                for node in LLM_NODES
            ]
        )
        checks = result_for(report)
        assert checks[SERVED].result is CheckResult.UNKNOWN
        assert checks[FINGERPRINT].result is CheckResult.UNKNOWN

    def test_a_partly_silent_provider_is_unknown_and_names_the_silent_nodes(self):
        report = smoke_report(
            nodes=[
                node_run(EXTRACT_NODE, model=None, sampling_fingerprint=None),
                *(node_run(node) for node in LLM_NODES[1:]),
            ]
        )
        served = result_for(report)[SERVED]
        assert served.result is CheckResult.UNKNOWN
        assert EXTRACT_NODE in served.detail

    def test_unknown_alone_does_not_fail_the_lane(self):
        """The exit status follows the same rule the capability matrix does."""
        unknowns = tuple(
            Check(name, CheckResult.UNKNOWN, "the provider said nothing")
            for name in CHECKS
        )
        assert not SmokeResult(tiers={}, checks=unknowns).failed


class TestALaneThatNeverRan:
    """An unexercised lane must never be readable as a passing one."""

    def test_a_missing_credential_leaves_every_question_unanswered(self):
        """No key, no run — and no check claiming to have learned anything.

        This is the state the repository is actually in, so it is the path most
        likely to be read: zero secrets, zero variables. It reports a failure
        with the variable named, every check ``unknown``, and ``exercised``
        false.

        ``env=`` is passed explicitly so the deployment sees the *selection*
        without the placeholder credentials ``conftest`` puts in the process
        environment for everything else.
        """
        result = asyncio.run(run_smoke(Deployment.from_env(env=TEST_TIER_ENV)))

        assert not result.exercised
        assert result.failed
        assert all(check.result is CheckResult.UNKNOWN for check in result.checks)
        assert "ANALYSIS_OPENAI_API_KEY" in (result.failure or "")

    def test_the_failure_text_carries_no_credential_value(self):
        """Provider errors can echo the request; a job summary outlives the run.

        The registry already knows which variables hold credential material for
        the selected vendors, so the substitution is exact rather than a guess
        at what a key looks like (OWASP A09).
        """
        key = "sk-live-do-not-log-me"
        deployment = Deployment.from_env(
            env=TEST_TIER_ENV | {"ANALYSIS_OPENAI_API_KEY": key}
        )
        redacted = _redacted(
            f"provider rejected Authorization: Bearer {key}", deployment
        )

        assert key not in redacted
        assert "ANALYSIS_OPENAI_API_KEY" in redacted


class TestDrivingTheEngine:
    """The assembly the checks hang off: outcome in, result out.

    Three outcomes and three different reports, which is the point — a lane
    whose job is to *report* must not flatten "the provider refused", "the input
    could not be modelled" and "it worked" into one failed step. The runner is a
    stub because what is under test here is the shaping, not the graph.
    """

    def smoke_with(self, monkeypatch, outcome):
        """``run_smoke`` against a stubbed runner, with no adapters to build."""
        engine = Engine(
            StubRunner(outcome),
            limits=DEPLOYMENT.resilience.source_limits(),
            deadline_seconds=DEPLOYMENT.resilience.deadline_seconds(),
            frameworks=sample_selection(),
        )
        monkeypatch.setattr(
            smoke.Engine,
            "from_deployment",
            # The smoke run names the selection now — every carried framework —
            # so the stand-in takes it and ignores it.
            classmethod(lambda cls, _deployment, _frameworks: engine),
        )
        return asyncio.run(run_smoke(DEPLOYMENT))

    def test_an_install_it_cannot_ask_reports_rather_than_raising(self, monkeypatch):
        """Nothing selectable is a result, not a traceback.

        A framework whose options carry a required field cannot be selected here:
        a smoke run has nobody to ask what ASVS level an install wants. An
        install carrying nothing else leaves the selection empty, and the module
        contract says only a broken deployment raises — so this reports eight
        ``unknown`` checks with the reason attached. The lane still fails: no
        provider served, and a green smoke where nothing ran is the one outcome
        this module exists to rule out.
        """
        monkeypatch.setattr(smoke, "_smoke_selection", lambda _deployment: ())
        monkeypatch.setattr(
            smoke, "unexercised_frameworks", lambda _deployment: ("asvs",)
        )

        result = asyncio.run(run_smoke(DEPLOYMENT))

        assert not result.exercised
        assert all(check.result is CheckResult.UNKNOWN for check in result.checks)
        assert "needs a job option a smoke run cannot supply" in result.failure
        assert any("asvs: not exercised" in note for note in result.notes)

    def test_a_framework_it_could_not_select_is_named_on_a_completed_run(
        self, monkeypatch
    ):
        """The cost is stated rather than hidden: three tier keys stay untested."""
        monkeypatch.setattr(
            smoke, "unexercised_frameworks", lambda _deployment: ("asvs",)
        )

        result = self.smoke_with(monkeypatch, PipelineCompleted(report=smoke_report()))

        assert any("asvs: not exercised" in note for note in result.notes)

    def test_a_completed_run_passes_every_check_and_names_both_tiers(self, monkeypatch):
        result = self.smoke_with(monkeypatch, PipelineCompleted(report=smoke_report()))

        assert result.exercised
        assert not result.failed
        assert all(check.result is CheckResult.PASSED for check in result.checks)
        assert set(result.tiers) == {"base", "strong"}
        assert (
            result.tiers["base"]["vendor"]
            == TEST_TIER_ENV["ANALYSIS_MODEL_BASE_VENDOR"]
        )

    def test_a_rejected_input_is_an_extraction_finding_not_a_crash(self, monkeypatch):
        """The validity gate refusing is a fact about what this pair returned.

        On this fixture — whose every element is named in quotable prose — a
        rejection is a real finding, so it is reported against the check that
        owns it rather than as an error with no check attached. The lane was
        still exercised: a provider answered.
        """
        result = self.smoke_with(
            monkeypatch,
            PipelineRejected(
                issues=[ValidationIssue(code="no-trust-zones", message="none found")]
            ),
        )
        checks = {check.name: check for check in result.checks}

        assert result.exercised
        assert result.failed
        assert checks[EXTRACTION].result is CheckResult.FAILED
        assert "no-trust-zones" in checks[EXTRACTION].detail
        assert all(
            checks[name].result is CheckResult.UNKNOWN
            for name in CHECKS
            if name != EXTRACTION
        )

    def test_a_provider_that_raises_leaves_the_lane_unexercised(self, monkeypatch):
        """Any provider failure is a result here, never a traceback.

        The caller is a CI lane whose job is to report, and a raised exception
        reports "the step failed" — which is exactly as informative as saying
        nothing.
        """
        result = self.smoke_with(monkeypatch, RuntimeError("provider returned 503"))

        assert not result.exercised
        assert result.failed
        assert "provider returned 503" in (result.failure or "")


class TestTheRenderedSummary:
    """A CI summary is rendered from the result, so the rendering is contract."""

    def test_every_check_and_both_tiers_appear(self):
        result = SmokeResult(
            tiers={
                "base": {
                    "vendor": "openai",
                    "model": "gpt-4o",
                    "route": "openai/gpt-4o",
                }
            },
            checks=checks_for(smoke_report(), DEPLOYMENT),
        )
        rendered = render_markdown(result)

        assert "gpt-4o" in rendered
        for name in CHECKS:
            assert name in rendered

    def test_an_unexercised_lane_says_so_in_words(self):
        rendered = render_markdown(
            SmokeResult(
                tiers={},
                checks=tuple(
                    Check(name, CheckResult.UNKNOWN, "nothing ran") for name in CHECKS
                ),
                failure="ProviderAuthError: vendor 'openai' needs ANALYSIS_OPENAI_API_KEY",
            )
        )
        assert "unexercised" in rendered
        assert "ANALYSIS_OPENAI_API_KEY" in rendered


class TestTheFixture:
    """The one input every provider is asked about."""

    def test_it_is_small_enough_to_run_on_every_pull_request(self):
        """Cost is a property of this string, and it is spent on every lane.

        The bound is generous — it is the corpus's smallest case, and this
        fixture is graded against no quality bar at all — so tripping it means
        the fixture has grown into an eval case, which is a different job with a
        different budget.
        """
        assert len(SMOKE_SOURCE.text) < 800

    def test_its_prose_names_the_parts_the_gate_requires_a_citation_for(self):
        """The gate checks each element's excerpt against the source verbatim.

        A fixture that gestured at a system without naming its parts would fail
        extraction on a provider that read it correctly, and the lane would
        report a defect in the wrong place.
        """
        for phrase in ("notes web client", "notes API", "notes database"):
            assert phrase in SMOKE_SOURCE.text
