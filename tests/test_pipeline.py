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

import pytest

from stride_service import graph
from stride_service.api import create_app
from stride_service.jobs import (
    InMemoryJobStore,
    JobRecord,
    PipelineCompleted,
    PipelineRejected,
    StubPipelineRunner,
)
from stride_service.pipeline import AdkPipelineRunner, PipelineError
from stride_service.report import STRIDE_CATEGORIES, Ground, InputRef
from stride_service.sampling import TierSampling, load_sampling, sampling_fingerprint
from stride_service.sources import DEFAULT_DESCRIPTION_LABEL, Source
from tests.factories import (
    BASE_MODEL,
    PROJECT_ROOT,
    repo_tiers,
    sample_draft,
    sample_ruling,
    served_build,
    threats_json,
    valid_model,
)
from tests.factories import scripted_pipeline as build

# The shipped config selects Vertex on both tiers, and Vertex's credential mode
# is ADC — so building the real pipeline needs these three present. They are
# names of variables, never credentials: nothing here is a secret.
VERTEX_ENV = {
    "STRIDE_VERTEX_PROJECT": "test-project",
    "STRIDE_VERTEX_LOCATION": "us-central1",
    "GOOGLE_APPLICATION_CREDENTIALS": "/nonexistent/adc.json",
}


def draft_json(threat_id: str, category: str) -> str:
    return threats_json(sample_draft(threat_id, category))


def job(text: str = "Customers log in to the web app.") -> JobRecord:
    record = JobRecord.create(
        owner_subject="ping|user-1",
        sources=[Source.description(text)],
        system_name="Order Service",
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


def happy_replies() -> dict[str, str]:
    """Extraction succeeds; spoofing drafts one threat; the critic confirms it."""
    return {
        "extract": valid_model().model_dump_json(),
        graph.analyze_node_name("spoofing"): draft_json("S-01", "spoofing"),
        "critic": threats_json(sample_ruling("S-01")),
    }


def test_a_clean_run_produces_a_report():
    pipeline, _ = build(happy_replies())
    outcome, visited = run(pipeline, job())

    assert isinstance(outcome, PipelineCompleted)
    report = outcome.report
    assert [threat.id for threat in report.threats] == ["S-01"]
    assert report.summary.threat_count == 1
    assert report.input.system_name == "Order Service"
    assert report.system_model.get("process:web-app") is not None
    # Self-containment is re-checked by StrideReport itself on construction.
    assert report.boundary_crossings == report.system_model.boundary_crossings()

    assert visited[0] == graph.EXTRACT_NODE
    assert visited[-1] == graph.ASSEMBLE_NODE
    assert set(graph.ANALYZE_GRAPH_NODES) <= set(visited)
    assert graph.REPAIR_NODE not in visited
    assert graph.REJECT_NODE not in visited


def test_an_unfindable_quote_is_marked_on_the_report_and_still_renders():
    """The per-entry half of the policy, end to end.

    The job's one source says nothing about MFA, so that quote cannot verify —
    but a threat with one bad quote beside a good ground is still a justified
    finding, so it reaches the report with the failure recorded beside it
    rather than being dropped or killing the run.
    """
    draft = sample_draft(
        "S-01",
        "spoofing",
        grounds=[
            Ground(
                kind="quote",
                text="we never got round to MFA",
                source_label=DEFAULT_DESCRIPTION_LABEL,
            ),
            Ground(kind="derived-fact", flow_id="flow:customer-to-web-app:login"),
        ],
    )
    replies = happy_replies() | {
        graph.analyze_node_name("spoofing"): threats_json(draft)
    }
    pipeline, _ = build(replies)

    outcome, _ = run(pipeline, job())

    report = outcome.report
    assert [threat.id for threat in report.threats] == ["S-01"]
    assert len(report.threats[0].grounds) == 2
    assert [(m.threat_id, m.index) for m in report.unverified_grounds] == [("S-01", 0)]


def test_a_threat_no_ground_supports_fails_the_job():
    """The per-threat half: nothing holds, so nothing ships."""
    draft = sample_draft(
        "S-01",
        "spoofing",
        grounds=[
            Ground(
                kind="quote",
                text="we never got round to MFA",
                source_label=DEFAULT_DESCRIPTION_LABEL,
            )
        ],
    )
    replies = happy_replies() | {
        graph.analyze_node_name("spoofing"): threats_json(draft)
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
    assert by_node[graph.CRITIC_NODE].sampling_fingerprint is not None
    assert by_node[graph.ASSEMBLE_NODE].sampling_fingerprint is None


def test_report_stamps_the_per_tier_sampling_clear_block():
    """The resolved sampling is recorded in the clear, once per tier."""
    pipeline, _ = build(happy_replies())
    outcome, _ = run(pipeline, job())

    sampling = load_sampling(PROJECT_ROOT / "config" / "sampling.toml", env={})
    assert outcome.report.sampling == {
        tier: params.model_dump() for tier, params in sampling.tiers.items()
    }


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
        canonical = graph.TIER_NODE_BY_GRAPH_NODE.get(node_run.node)
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
        instruction = models[graph.analyze_node_name(category)].seen[0]
        assert f"**{category}** agent" in instruction
        assert "process:web-app" in instruction  # {system_model} templated in
        assert "{" not in instruction.split("## Procedure")[0].split("```")[-1]


def test_the_critic_sees_every_analysts_drafts_once():
    replies = happy_replies()
    replies[graph.analyze_node_name("tampering")] = draft_json("T-01", "tampering")
    replies["critic"] = threats_json(sample_ruling("S-01"), sample_ruling("T-01"))
    pipeline, models = build(replies)
    outcome, _ = run(pipeline, job())

    critic_instruction = models["critic"].seen[0]
    assert critic_instruction.count('"id": "S-01"') == 1
    assert critic_instruction.count('"id": "T-01"') == 1
    assert {threat.id for threat in outcome.report.threats} == {"S-01", "T-01"}


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
    assert "Customers log in to the web app." in repair_instruction  # original text


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
    assert not set(graph.ANALYZE_GRAPH_NODES) & set(visited)


def test_a_hallucinated_element_reference_fails_the_job_loudly():
    """The merge seam refuses drafts the System Model cannot account for."""
    replies = happy_replies()
    replies[graph.analyze_node_name("spoofing")] = threats_json(
        sample_draft("S-01", affected_element_ids=["process:invented"])
    )
    pipeline, _ = build(replies)

    with pytest.raises(Exception, match="process:invented"):
        run(pipeline, job())


def test_a_malformed_critic_output_is_re_asked_once_and_then_assembled():
    """The critic drops a draft; the bounded re-ask returns the full set."""
    replies = happy_replies()
    replies[graph.analyze_node_name("tampering")] = draft_json("T-01", "tampering")
    both = threats_json(sample_ruling("S-01"), sample_ruling("T-01"))
    # The critic drops T-01; the re-ask returns both drafts, reconciled.
    replies["critic"] = threats_json(sample_ruling("S-01"))
    replies["recritic"] = both

    pipeline, models = build(replies)
    outcome, visited = run(pipeline, job())

    assert isinstance(outcome, PipelineCompleted)
    assert {threat.id for threat in outcome.report.threats} == {"S-01", "T-01"}
    assert visited[-4:] == [
        graph.ROUTER_NODE,
        graph.RECRITIC_NODE,
        graph.REREVIEW_NODE,
        graph.ASSEMBLE_NODE,
    ]
    assert graph.CRITIC_FAILED_NODE not in visited
    # The re-ask saw the failing ruling and the problem it must fix.
    re_ask_instruction = models["recritic"].seen[0]
    assert "T-01" in re_ask_instruction  # named in {critic_issues}


def test_a_critic_that_will_not_reconcile_after_the_re_ask_fails_the_job_loudly():
    replies = happy_replies()
    invented = threats_json(sample_ruling("S-01"), sample_ruling("T-02"))
    # Both the critic and its re-ask return a threat no analyst drafted.
    replies["critic"] = invented
    replies["recritic"] = invented
    pipeline, _ = build(replies)

    with pytest.raises(Exception, match="T-02"):
        run(pipeline, job())


def test_the_stub_and_the_real_runner_compute_one_input_ref():
    """Two digest sites, one arithmetic.

    ``StubPipelineRunner`` builds an ``InputRef`` independently of the real
    runner. If the two ever computed it differently, every fixture and every
    offline test would assert against a reference production never emits.
    """
    record = job("Customers log in to the web app.")
    pipeline, _ = build(happy_replies())

    outcome, _ = run(pipeline, record)
    stub_report = asyncio.run(
        StubPipelineRunner().run(job("Customers log in to the web app."), _ignore)
    )

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
    invented = threats_json(sample_ruling("S-01"), sample_ruling("T-02"))
    replies["critic"] = invented
    replies["recritic"] = invented
    pipeline, _ = build(replies)
    record = job("A poison description.")
    digest = InputRef.of(
        system_name=record.system_name, sources=record.sources
    ).source_sha256

    with caplog.at_level(logging.WARNING, logger="stride_service.pipeline"):
        with pytest.raises(Exception, match="T-02"):
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
    assert isinstance(app.state.runner, AdkPipelineRunner)


def test_pipeline_error_names_the_job_when_the_graph_produces_nothing():
    assert "graph produced neither" in str(
        PipelineError("job x: graph produced neither")
    )
