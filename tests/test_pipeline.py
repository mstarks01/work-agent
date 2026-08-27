"""End-to-end runs of the real graph against a scripted model.

No Vertex endpoint is involved: ``build_pipeline`` takes the model resolver,
so each LLM node is bound to a fake that replays a canned emission. That
keeps the whole topology under test — routes, the fan-out, the join, the
one repair pass, and the rejection path — while the only thing faked is the
model's text.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator

import pytest
from google.adk.models import LlmResponse

from stride_service import graph
from stride_service.api import create_app
from stride_service.frameworks.asvs.record import (
    RequirementProposal,
    RequirementRulingProposal,
)
from stride_service.frameworks.stride.record import STRIDE_CATEGORIES
from stride_service.jobs import (
    InMemoryJobStore,
    JobRecord,
    PipelineCompleted,
    PipelineRejected,
    StubPipelineRunner,
)
from stride_service.pipeline import AdkPipelineRunner, PipelineError
from stride_service.report import FrameworkSelection, InputRef
from stride_service.sampling import TierSampling, load_sampling, sampling_fingerprint
from stride_service.sources import DEFAULT_DESCRIPTION_LABEL, Source
from tests.factories import (
    BASE_MODEL,
    DEFAULT_FRAMEWORKS,
    DESCRIPTION_TEXT,
    EMPTY_CLAIMS,
    PROJECT_ROOT,
    ScriptedLlm,
    claims_json,
    repo_tiers,
    sample_proposal,
    sample_ruling,
    sample_selection,
    served_build,
    valid_model,
)
from tests.factories import scripted_pipeline as build

# The shipped config selects Vertex on both tiers, and Vertex's credential mode
# is ADC — so building the real pipeline needs these three present. They are
# names of variables, never credentials: nothing here is a secret.
# This install's one package's own nodes. Every per-framework role carries its
# framework in the node name, since two packages may declare a lane of the same
# name and would otherwise fight over one state key.
STRIDE_NODES = graph.FrameworkNodes("stride")
CRITIC = STRIDE_NODES.node(graph.CRITIC_ROLE)
RECRITIC = STRIDE_NODES.node(graph.RECRITIC_ROLE)
ROUTER = STRIDE_NODES.node(graph.ROUTER_ROLE)
REREVIEW = STRIDE_NODES.node(graph.REREVIEW_ROLE)
CRITIC_FAILED = STRIDE_NODES.node(graph.CRITIC_FAILED_ROLE)
ANALYZE_NODES = tuple(lane.node_name for lane in STRIDE_NODES.lanes)
#: This graph's node -> tier map, built for the selection above.
TIER_NODES = graph.tier_node_by_graph_node(("stride",))

VERTEX_ENV = {
    "STRIDE_VERTEX_PROJECT": "test-project",
    "STRIDE_VERTEX_LOCATION": "us-central1",
    "GOOGLE_APPLICATION_CREDENTIALS": "/nonexistent/adc.json",
}


def proposal_json(threat_id: str, category: str) -> str:
    """One category agent's whole emission: the shape its node's schema names."""
    return claims_json(sample_proposal(threat_id, category))


def job(text: str = DESCRIPTION_TEXT) -> JobRecord:
    record = JobRecord.create(
        owner_subject="idp|user-1",
        sources=[Source.description(text)],
        system_name="Order Service",
        frameworks=sample_selection(),
    )
    record.transition("running")
    return record


def run(pipeline: graph.Pipeline, record: JobRecord) -> tuple[object, list[str]]:
    """Drive one job to its outcome, collecting the nodes it reported."""
    visited: list[str] = []

    async def on_node(node: str) -> None:
        visited.append(node)

    async def scenario():
        return await AdkPipelineRunner(pipeline).run(record, on_node)

    return asyncio.run(scenario()), visited


def block(report):
    """This report's one analysis block.

    Every finding-shaped field moved off the envelope and onto the block a
    framework fills, since a claim count that summed across frameworks would add
    a credible attack to an unanswered requirement and call the total findings.
    These runs select one framework, so there is exactly one.
    """
    (only,) = report.analyses
    return only


def happy_replies() -> dict[str, str]:
    """Extraction succeeds; spoofing drafts one threat; the critic confirms it."""
    return {
        "extract": valid_model().model_dump_json(),
        graph.analyze_node_name("stride", "spoofing"): proposal_json(
            "S-01", "spoofing"
        ),
        CRITIC: claims_json(sample_ruling("S-01")),
    }


def test_a_clean_run_produces_a_report():
    pipeline, _ = build(happy_replies())
    outcome, visited = run(pipeline, job())

    assert isinstance(outcome, PipelineCompleted)
    report = outcome.report
    assert [threat.id for threat in block(report).claims] == ["S-01"]
    assert block(report).summary.claim_count == 1
    assert report.input.system_name == "Order Service"
    assert report.system_model.get("process:web-app") is not None
    # Self-containment is re-checked by Report itself on construction.
    assert report.boundary_crossings == report.system_model.boundary_crossings()

    assert visited[0] == graph.EXTRACT_NODE
    assert visited[-1] == graph.ASSEMBLE_NODE
    assert set(ANALYZE_NODES) <= set(visited)
    assert graph.REPAIR_NODE not in visited
    assert graph.REJECT_NODE not in visited


def test_an_unfindable_quote_is_marked_on_the_report_and_still_renders():
    """The per-entry half of the policy, end to end.

    The job's one source says nothing about MFA, so that quote cannot verify —
    but a threat with one bad quote beside a good ground is still a justified
    finding, so it reaches the report with the failure recorded beside it
    rather than being dropped or killing the run.
    """
    proposal = sample_proposal(
        "S-01",
        "spoofing",
        quotes=[
            {
                "text": "we never got round to MFA",
                "source_label": DEFAULT_DESCRIPTION_LABEL,
            }
        ],
        evidence_refs=["crossing:flow:customer-to-web-app:login"],
    )
    replies = happy_replies() | {
        graph.analyze_node_name("stride", "spoofing"): claims_json(proposal)
    }
    pipeline, _ = build(replies)

    outcome, _ = run(pipeline, job())

    report = outcome.report
    assert [threat.id for threat in block(report).claims] == ["S-01"]
    assert len(block(report).claims[0].grounds) == 2
    assert [(m.claim_id, m.index) for m in block(report).unverified_grounds] == [
        ("S-01", 0)
    ]


def test_a_description_citing_a_missing_element_is_marked_on_the_report():
    """The prose half of the same policy, end to end.

    ``affected_element_ids`` naming a missing element kills the job; the same
    ID written into the *description* is marked instead. The fan-in has no
    re-ask path, so a mistyped ID in prose must not cost six lanes of analysis
    — but a reader still has to be told the argument cites a system this
    report does not describe.
    """
    proposal = sample_proposal(
        "S-01",
        "spoofing",
        description="The attacker pivots from process:web-app into"
        " process:web-api, which this model does not contain.",
    )
    replies = happy_replies() | {
        graph.analyze_node_name("stride", "spoofing"): claims_json(proposal)
    }
    pipeline, _ = build(replies)

    outcome, _ = run(pipeline, job())

    report = outcome.report
    assert [threat.id for threat in block(report).claims] == ["S-01"]
    assert [(m.claim_id, m.mention) for m in block(report).unresolved_mentions] == [
        ("S-01", "process:web-api")
    ]


def test_a_composed_evidence_reference_is_marked_rather_than_fatal():
    """The policy #138 narrowed, end to end.

    A reference the catalog does not hold used to fail the whole job, and
    agents compose well-formed ones — 2 of 12 jobs on a live sweep. The threat
    now stands on whatever else it cited, and the reader is told what was
    dropped. What still fails is a threat left with no grounds at all, which is
    covered where resolution decides it.
    """
    proposal = sample_proposal(
        "S-01",
        "spoofing",
        evidence_refs=[
            "crossing:flow:customer-to-web-app:login",
            "crossing:flow:ghost",
        ],
    )
    replies = happy_replies() | {
        graph.analyze_node_name("stride", "spoofing"): claims_json(proposal)
    }
    pipeline, _ = build(replies)

    outcome, _ = run(pipeline, job())

    report = outcome.report
    assert [threat.id for threat in block(report).claims] == ["S-01"]
    assert [(m.claim_id, m.reference) for m in block(report).unresolved_evidence] == [
        ("S-01", "crossing:flow:ghost")
    ]
    # The surviving reference still grounds the finding it was cited for.
    assert any(
        ground.kind == "derived-fact" for ground in block(report).claims[0].grounds
    )


def test_a_threat_with_no_countermeasure_is_marked_on_the_report():
    """A completeness signal, carried to the reader rather than costing the run."""
    proposal = sample_proposal("S-01", "spoofing", mitigations=[])
    replies = happy_replies() | {
        graph.analyze_node_name("stride", "spoofing"): claims_json(proposal)
    }
    pipeline, _ = build(replies)

    outcome, _ = run(pipeline, job())

    report = outcome.report
    assert [threat.id for threat in block(report).claims] == ["S-01"]
    assert [m.claim_id for m in block(report).missing_mitigations] == ["S-01"]


def test_one_name_on_two_types_is_marked_and_does_not_fail_the_run():
    """The suspicion the gate cannot raise, carried to the reader instead.

    The extraction names its store after its process, which the gate passes —
    two types make two IDs — so the job completes and the reader gets the mark.
    """
    model = valid_model()
    model.data_stores[0].name = "Web App"
    replies = happy_replies() | {"extract": model.model_dump_json()}
    pipeline, _ = build(replies)

    outcome, _ = run(pipeline, job())

    assert isinstance(outcome, PipelineCompleted)
    report = outcome.report
    assert [(m.name_slug, m.element_ids) for m in report.shared_element_names] == [
        ("web-app", ["process:web-app", "store:web-app"])
    ]


def test_a_clean_model_carries_no_shared_name_marks():
    pipeline, _ = build(happy_replies())

    outcome, _ = run(pipeline, job())

    assert outcome.report.shared_element_names == []


def test_a_lane_that_skips_a_number_is_logged_and_not_renumbered(caplog):
    """The numbering rule is about the agents, so it lands in the log, not the report.

    A gap breaks nothing downstream — the IDs are unique and their letters
    match — so the drafts reach the report exactly as written, and the drift is
    recorded where the run's other operational facts are.
    """
    import logging

    replies = happy_replies() | {
        graph.analyze_node_name("stride", "spoofing"): claims_json(
            sample_proposal("S-01"), sample_proposal("S-05")
        ),
        CRITIC: claims_json(sample_ruling("S-01"), sample_ruling("S-05")),
    }
    pipeline, _ = build(replies)

    with caplog.at_level(logging.WARNING, logger="stride_service.graph"):
        outcome, _ = run(pipeline, job())

    assert [threat.id for threat in block(outcome.report).claims] == ["S-01", "S-05"]
    assert any("S-01, S-05" in message for message in caplog.messages)
    assert any("not 01..02" in message for message in caplog.messages)


def test_a_threat_no_ground_supports_fails_the_job():
    """The per-threat half: nothing holds, so nothing ships."""
    proposal = sample_proposal(
        "S-01",
        "spoofing",
        quotes=[
            {
                "text": "we never got round to MFA",
                "source_label": DEFAULT_DESCRIPTION_LABEL,
            }
        ],
        evidence_refs=[],
    )
    replies = happy_replies() | {
        graph.analyze_node_name("stride", "spoofing"): claims_json(proposal)
    }
    pipeline, _ = build(replies)

    with pytest.raises(Exception, match="no ground that verifies"):
        run(pipeline, job())


def test_the_report_carries_the_graph_runs_node_stamps():
    """Assembly's job: what the executor stamped reaches the report unaltered.

    The stamping itself — served-build join, fingerprints, durations — is
    :mod:`stride_service.execution`'s and is tested at that interface.
    """
    pipeline, _ = build(happy_replies())
    outcome, visited = run(pipeline, job())

    assert [node_run.node for node_run in outcome.report.nodes] == visited
    by_node = {run_.node: run_ for run_ in outcome.report.nodes}
    assert by_node[graph.EXTRACT_NODE].model == served_build(BASE_MODEL)
    assert by_node[CRITIC].sampling_fingerprint is not None
    assert by_node[graph.ASSEMBLE_NODE].sampling_fingerprint is None


def test_report_stamps_the_per_tier_sampling_clear_block():
    """The resolved sampling is recorded in the clear, once per tier."""
    pipeline, _ = build(happy_replies())
    outcome, _ = run(pipeline, job())

    sampling = load_sampling(PROJECT_ROOT / "config" / "sampling.toml", env={})
    assert outcome.report.sampling == {
        tier: params.model_dump() for tier, params in sampling.tiers.items()
    }


def test_a_tier_running_a_reasoning_effort_still_produces_a_report():
    """The clear block records every param a tier can resolve, not only numbers.

    ``thinking`` is an offered param: the file documents it, the env overrides
    reach it, the build-time gate checks it, and it enters the fingerprint. It
    is also the only one whose resolved value is a string — and the block was
    typed to numbers, so setting it produced reports that could not be
    assembled. The cost of that shape is what makes this a regression test
    rather than a schema nicety: nothing failed at startup, and nothing failed
    at the gate. The job ran the whole graph, paid for every node, and died at
    assembly.
    """
    sampling = load_sampling(
        PROJECT_ROOT / "config" / "sampling.toml",
        env={"STRIDE_SAMPLING_STRONG_THINKING": "low"},
    )
    pipeline, _ = build(happy_replies(), sampling=sampling)

    outcome, _ = run(pipeline, job())

    assert outcome.report.sampling["strong"]["thinking"] == "low"
    # Round-trips rather than merely surviving: a value stored in a type the
    # fingerprint cannot be recomputed from would satisfy the line above and
    # leave every hash in the report unverifiable.
    assert TierSampling(**outcome.report.sampling["strong"]) == sampling.for_tier(
        "strong"
    )


def test_each_llm_node_fingerprint_recomputes_from_the_artifact():
    """The per-node hash is derivable from the clear block + served model alone.

    The report is the portable evidence, so this is asserted over the assembled
    artifact rather than over the executor's output.
    """
    pipeline, _ = build(happy_replies())
    outcome, _ = run(pipeline, job())
    tiers = repo_tiers()
    clear = outcome.report.sampling

    for node_run in outcome.report.nodes:
        canonical = TIER_NODES.get(node_run.node)
        if canonical is None:  # deterministic FunctionNode
            assert node_run.model is None
            assert node_run.sampling_fingerprint is None
            continue
        tier = tiers.resolve_tier(canonical)
        expected = sampling_fingerprint(node_run.model, TierSampling(**clear[tier]))
        assert node_run.sampling_fingerprint == expected


def test_each_agent_gets_its_own_category_and_the_shared_model():
    pipeline, models = build(happy_replies())
    run(pipeline, job())

    for category in STRIDE_CATEGORIES:
        instruction = models[graph.analyze_node_name("stride", category)].seen[0]
        assert f"**{category}** agent" in instruction
        assert "process:web-app" in instruction  # {system_model} templated in
        assert "{" not in instruction.split("## Procedure")[0].split("```")[-1]


def test_the_critic_sees_each_category_agents_drafts_once():
    replies = happy_replies()
    replies[graph.analyze_node_name("stride", "tampering")] = proposal_json(
        "T-01", "tampering"
    )
    replies[CRITIC] = claims_json(sample_ruling("S-01"), sample_ruling("T-01"))
    pipeline, models = build(replies)
    outcome, _ = run(pipeline, job())

    critic_instruction = models[CRITIC].seen[0]
    assert critic_instruction.count('"id": "S-01"') == 1
    assert critic_instruction.count('"id": "T-01"') == 1
    assert {threat.id for threat in block(outcome.report).claims} == {"S-01", "T-01"}


def test_an_invalid_extraction_is_repaired_once_and_then_analyzed():
    broken = valid_model().model_dump(mode="json")
    broken["data_flows"][0]["destination"] = "process:does-not-exist"
    replies = happy_replies() | {"extract": json.dumps(broken)}
    replies["repair"] = valid_model().model_dump_json()

    pipeline, models = build(replies)
    outcome, visited = run(pipeline, job())

    assert isinstance(outcome, PipelineCompleted)
    assert visited[:4] == [
        graph.EXTRACT_NODE,
        graph.VALIDATE_NODE,
        graph.REPAIR_NODE,
        graph.REVALIDATE_NODE,
    ]
    repair_instruction = models["repair"].seen[0]
    assert "process:does-not-exist" in repair_instruction  # the failed model
    assert DESCRIPTION_TEXT in repair_instruction  # original text


def test_a_model_that_fails_twice_is_rejected_with_its_issues():
    broken = valid_model().model_dump(mode="json")
    broken["data_flows"][0]["destination"] = "process:does-not-exist"
    replies = happy_replies() | {
        "extract": json.dumps(broken),
        "repair": json.dumps(broken),
    }

    pipeline, _ = build(replies)
    outcome, visited = run(pipeline, job())

    assert isinstance(outcome, PipelineRejected)
    assert any("process:does-not-exist" in issue.message for issue in outcome.issues)
    assert visited[-1] == graph.REJECT_NODE
    assert graph.PREPARE_NODE not in visited
    assert not set(ANALYZE_NODES) & set(visited)


def test_a_hallucinated_element_reference_fails_the_job_loudly():
    """The merge seam refuses drafts the System Model cannot account for."""
    replies = happy_replies()
    replies[graph.analyze_node_name("stride", "spoofing")] = claims_json(
        sample_proposal("S-01", affected_element_ids=["process:invented"])
    )
    pipeline, _ = build(replies)

    with pytest.raises(Exception, match="process:invented"):
        run(pipeline, job())


def test_a_malformed_critic_output_is_re_asked_once_and_then_assembled():
    """The critic drops a draft; the bounded re-ask returns the full set."""
    replies = happy_replies()
    replies[graph.analyze_node_name("stride", "tampering")] = proposal_json(
        "T-01", "tampering"
    )
    both = claims_json(sample_ruling("S-01"), sample_ruling("T-01"))
    # The critic drops T-01; the re-ask returns both drafts, reconciled.
    replies[CRITIC] = claims_json(sample_ruling("S-01"))
    replies[RECRITIC] = both

    pipeline, models = build(replies)
    outcome, visited = run(pipeline, job())

    assert isinstance(outcome, PipelineCompleted)
    assert {threat.id for threat in block(outcome.report).claims} == {"S-01", "T-01"}
    assert visited[-4:] == [
        ROUTER,
        RECRITIC,
        REREVIEW,
        graph.ASSEMBLE_NODE,
    ]
    assert CRITIC_FAILED not in visited
    # The re-ask saw the failing ruling and the problem it must fix.
    re_ask_instruction = models[RECRITIC].seen[0]
    assert "T-01" in re_ask_instruction  # named in {critic_issues}


def test_a_mis_shaped_verdict_is_re_asked_rather_than_killing_the_job():
    """The regression this seam exists for, end to end.

    A ``needs-info`` that names no unknown is a fault, and it used to be a
    *fatal* one: the rule lived in ``Verdict``'s validator, which ADK runs on
    the way into session state, so the critic node raised and took the whole
    run — six lanes of drafting and the single most expensive call in the graph
    — with it. The re-ask that exists for exactly this never got to run.
    """
    replies = happy_replies()
    replies[CRITIC] = json.dumps(
        {
            "claims": [
                {
                    "id": "S-01",
                    "confidence": "high",
                    "verdict": {"status": "needs-info", "reason": "control unverified"},
                }
            ]
        }
    )
    replies[RECRITIC] = claims_json(sample_ruling("S-01"))
    pipeline, models = build(replies)

    outcome, visited = run(pipeline, job())

    assert isinstance(outcome, PipelineCompleted)
    assert [threat.id for threat in block(outcome.report).claims] == ["S-01"]
    assert RECRITIC in visited
    assert CRITIC_FAILED not in visited
    # The re-ask was told which ruling, and what about it — and was shown the
    # draft, because naming the unknown cannot be done from an ID alone.
    re_ask = models[RECRITIC].seen[0]
    assert "names no unknown attribute" in re_ask
    assert "S-01" in re_ask


def test_a_mistyped_ruling_id_is_re_asked_rather_than_killing_the_job():
    """End to end, the third node-boundary raise removed from the critic.

    ``"S-1"`` used to fail ``ThreatRuling``'s pattern inside the node's
    output_schema, ending the run. It now reconciles as a drop plus an
    invention — two problems the re-ask is already told how to fix, with no
    prompt change needed for the new fault.
    """
    replies = happy_replies()
    replies[CRITIC] = json.dumps(
        {
            "claims": [
                {"id": "S-1", "confidence": "high", "verdict": {"status": "confirmed"}}
            ]
        }
    )
    replies[RECRITIC] = claims_json(sample_ruling("S-01"))
    pipeline, models = build(replies)

    outcome, visited = run(pipeline, job())

    assert isinstance(outcome, PipelineCompleted)
    assert [threat.id for threat in block(outcome.report).claims] == ["S-01"]
    assert RECRITIC in visited
    assert CRITIC_FAILED not in visited
    re_ask = models[RECRITIC].seen[0]
    assert "dropped draft 'S-01'" in re_ask
    assert "'S-1', which no lane agent drafted" in re_ask


def test_a_verdict_still_mis_shaped_after_the_re_ask_fails_as_critic_output():
    """Re-askable is not ignorable. The second failure is still fatal — but it
    arrives as the service's own ``CriticOutputError`` naming the fault, not as
    a schema traceback out of a cancelled node."""
    replies = happy_replies()
    unreasoned = json.dumps(
        {
            "claims": [
                {
                    "id": "S-01",
                    "confidence": "high",
                    "verdict": {"status": "rejected"},
                }
            ]
        }
    )
    replies[CRITIC] = unreasoned
    replies[RECRITIC] = unreasoned
    pipeline, _ = build(replies)

    with pytest.raises(Exception, match="states no reason"):
        run(pipeline, job())


def test_a_critic_that_will_not_reconcile_after_the_re_ask_fails_the_job_loudly():
    replies = happy_replies()
    invented = claims_json(sample_ruling("S-01"), sample_ruling("T-02"))
    # Both the critic and its re-ask return a threat no category agent drafted.
    replies[CRITIC] = invented
    replies[RECRITIC] = invented
    pipeline, _ = build(replies)

    with pytest.raises(Exception, match="T-02"):
        run(pipeline, job())


def test_the_stub_and_the_real_runner_compute_one_input_ref():
    """Two digest sites, one arithmetic.

    ``StubPipelineRunner`` builds an ``InputRef`` independently of the real
    runner. If the two ever computed it differently, every fixture and every
    offline test would assert against a reference production never emits.
    """
    record = job(DESCRIPTION_TEXT)
    pipeline, _ = build(happy_replies())

    outcome, _ = run(pipeline, record)
    stub_report = asyncio.run(StubPipelineRunner().run(job(DESCRIPTION_TEXT), _ignore))

    assert isinstance(outcome, PipelineCompleted)
    assert outcome.report.input.model_dump() == stub_report.report.input.model_dump()


async def _ignore(node: str) -> None:
    """Node callback for a run whose progress nothing is watching."""


def test_a_failed_job_logs_the_input_digest(caplog):
    """A poison input is identifiable across jobs.

    The digest is logged on failure without the service ever storing the text.
    """
    import logging

    replies = happy_replies()
    invented = claims_json(sample_ruling("S-01"), sample_ruling("T-02"))
    replies[CRITIC] = invented
    replies[RECRITIC] = invented
    pipeline, _ = build(replies)
    # Distinct from the shared text so the digest is this job's, but still
    # carrying the spans the scripted model's excerpts quote.
    record = job(f"{DESCRIPTION_TEXT} A poison description.")
    digest = InputRef.of(
        system_name=record.system_name, sources=record.sources
    ).source_sha256

    with (
        caplog.at_level(logging.WARNING, logger="stride_service.pipeline"),
        pytest.raises(Exception, match="T-02"),
    ):
        run(pipeline, record)

    assert any(digest in message for message in caplog.messages)
    # the text itself is never logged
    assert record.sources[0].text not in caplog.text


def test_the_api_runs_jobs_through_the_real_graph_by_default(monkeypatch):
    """The runner seam defaults to the real graph."""
    for var, value in VERTEX_ENV.items():
        monkeypatch.setenv(var, value)

    class NoVerifier:
        def verify(self, token: str) -> str:
            raise AssertionError("not reached")

    app = create_app(store=InMemoryJobStore(), verifier=NoVerifier())
    # A runner is built per selection, so the app holds a function from one to
    # its runner rather than a single runner built up front.
    runner = app.state.runner_for(DEFAULT_FRAMEWORKS)
    assert isinstance(runner, AdkPipelineRunner)


def test_pipeline_error_names_the_job_when_the_graph_produces_nothing():
    assert "graph produced neither" in str(
        PipelineError("job x: graph produced neither")
    )


def test_the_report_records_what_informed_the_analysis():
    """Context, beside the run rather than inside a finding.

    The report already carried the two ends — what each node ran on, and what
    each finding rests on — and nothing in between. A pack selection that
    flipped or a skill that was edited changed the analysis invisibly.
    """
    pipeline, _ = build(happy_replies())
    outcome, _ = run(pipeline, job())

    context = outcome.report.analysis_context

    assert context.instruction_sha256 == pipeline.instruction_sha256
    # The fixture runs FastAPI over HTTPS against Cloud SQL Postgres.
    assert context.domain_packs == ["http-api", "databases"]
    # ``fired_rules`` names *one package's* candidate rules, so it sits on that
    # package's block rather than on the envelope's shared context.
    assert all(rule.split("-")[0] for rule in block(outcome.report).fired_rules)


def test_two_jobs_on_one_deployment_share_an_instruction_digest():
    """It identifies the instructions, not the submission.

    Two different inputs through one built graph were told the same things, so
    the digest must not move — otherwise it would be a second, weaker copy of
    ``input.source_sha256`` and could not be compared across jobs at all.
    """
    pipeline, _ = build(happy_replies())

    first, _ = run(pipeline, job())
    second, _ = run(
        pipeline, job(f"{DESCRIPTION_TEXT} The web app also emails receipts.")
    )

    assert first.report.input.source_sha256 != second.report.input.source_sha256
    assert (
        first.report.analysis_context.instruction_sha256
        == second.report.analysis_context.instruction_sha256
    )


def test_nothing_turns_the_context_into_evidence():
    """The one rule the block exists under, asserted where it could break.

    A pack informed the analysis; it did not ground anything. The report's own
    importers are what keep that true — ``AnalysisContext`` is built in the
    graph and read by nobody, so there is no path from a pack name or a rule ID
    to a ``Ground``.
    """
    from pathlib import Path

    package = Path(__file__).resolve().parents[1] / "src" / "stride_service"
    importers = {
        path.name
        for path in package.glob("*.py")
        if "AnalysisContext" in path.read_text() and path.name != "report.py"
    }
    assert importers == {"graph.py"}


# --- Two frameworks under the real scheduler ---------------------------------

ASVS_NODES = graph.FrameworkNodes("asvs")
ASVS_CRITIC = ASVS_NODES.node(graph.CRITIC_ROLE)
ASVS_RECRITIC = ASVS_NODES.node(graph.RECRITIC_ROLE)
ASVS_CLAIM_ID = "v5.0.0-6.2.1"


class HeldLlm(ScriptedLlm):
    """A stand-in that answers only once its gate opens.

    ADK's scheduler decides the order two frameworks' nodes finish in, and
    every other stand-in replies at once, so a two-framework offline run takes
    one order and never the one a live provider produced. A gate on one node
    fixes the order a test is about: the held node answers after whatever the
    test releases it on.
    """

    gate: asyncio.Event | None = None

    async def generate_content_async(
        self, llm_request, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        if self.gate is not None:
            await self.gate.wait()
        async for response in super().generate_content_async(llm_request, stream):
            yield response


def both_frameworks_job() -> JobRecord:
    record = JobRecord.create(
        owner_subject="idp|user-1",
        sources=[Source.description(DESCRIPTION_TEXT)],
        system_name="Order Service",
        frameworks=[
            FrameworkSelection(name="asvs", options={"level": 1}),
            FrameworkSelection(name="stride"),
        ],
    )
    record.transition("running")
    return record


def asvs_proposal_json() -> str:
    return claims_json(
        RequirementProposal(
            requirement="2.1",
            title="No password length policy is stated",
            description="The requirement applies and the input does not settle it.",
            evidence_refs=["crossing:flow:customer-to-web-app:login"],
        )
    )


def asvs_ruling_json() -> str:
    return claims_json(
        RequirementRulingProposal.model_validate(
            {"id": ASVS_CLAIM_ID, "verdict": {"status": "confirmed"}}
        )
    )


@pytest.mark.parametrize(
    ("first_reply", "held_node"),
    [
        pytest.param(asvs_ruling_json(), ASVS_CRITIC, id="critic-still-running"),
        pytest.param(EMPTY_CLAIMS, ASVS_RECRITIC, id="re-ask-still-running"),
    ],
)
def test_one_framework_finishing_first_does_not_fail_the_other(first_reply, held_node):
    """The real two-framework graph, with STRIDE forced to finish first.

    ``assemble`` fires once per framework that accepts. Holding one ASVS node
    until STRIDE's ``assemble`` has run puts the early run in the window where
    ASVS's drafts are parked and its rulings are absent, or where its
    ``reviewed`` key still holds the malformed first ruling its re-ask is
    replacing. Either window used to raise ``CriticOutputError`` for every ASVS
    draft and fail the job. The last run carries both blocks.
    """
    replies = happy_replies()
    replies[graph.analyze_node_name("asvs", "authentication")] = asvs_proposal_json()
    replies[ASVS_CRITIC] = first_reply
    replies[ASVS_RECRITIC] = asvs_ruling_json()
    pipeline, models = build(replies, frameworks=("asvs", "stride"), llm_class=HeldLlm)
    gate = asyncio.Event()
    models[held_node].gate = gate
    visited: list[str] = []

    async def on_node(node: str) -> None:
        visited.append(node)
        if node == graph.ASSEMBLE_NODE:
            gate.set()

    async def scenario():
        runner = AdkPipelineRunner(pipeline)
        return await asyncio.wait_for(runner.run(both_frameworks_job(), on_node), 30)

    outcome = asyncio.run(scenario())

    assert isinstance(outcome, PipelineCompleted)
    asvs_block, stride_block = outcome.report.analyses
    assert [claim.id for claim in stride_block.claims] == ["S-01"]
    assert [claim.id for claim in asvs_block.claims] == [ASVS_CLAIM_ID]
    assert visited.count(graph.ASSEMBLE_NODE) == 2
    assert visited.index(graph.ASSEMBLE_NODE) < visited.index(held_node)
