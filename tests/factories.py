"""Shared factories: a small, fully valid System Model, plus threats and a
complete STRIDE report built against it.

Topology: customer (internet zone) -> web app -> orders db (both internal),
one cross-boundary flow and one intra-zone flow, one recorded assumption.

Also home to :class:`ScriptedLlm`, the offline stand-in every graph-driving
test binds its nodes to. It lives here rather than in one test module because
the service tests and the eval tests must drive the graph through the *same*
stand-in. A copy that reported no ``model_version`` would let a whole class of
provenance defect stay invisible to the eval lane.
"""

import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import BaseModel, Field

from stride_service.binding import NodeBinding
from stride_service.graph import (
    ENTRY_EXTRACT,
    EXTRACT_NODE,
    REPAIR_NODE,
    TIER_NODE_BY_GRAPH_NODE,
    Entry,
    Pipeline,
    build_pipeline,
)
from stride_service.markdown_loader import MarkdownLoader
from stride_service.model_tiers import ModelTierConfig, load_model_tiers
from stride_service.report import (
    DraftThreat,
    Ground,
    InputRef,
    Job,
    Mitigation,
    NodeRun,
    Severity,
    StrideReport,
    Threat,
    ThreatProposal,
    ThreatRuling,
    Verdict,
    build_summary,
)
from stride_service.sampling import SamplingConfig, load_sampling
from stride_service.sources import DEFAULT_DESCRIPTION_LABEL, Source
from stride_service.system_model import (
    Assumption,
    DataFlow,
    DataStore,
    ExternalEntity,
    Process,
    SystemModel,
    TrustBoundary,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

BASE_MODEL = "fake-base-001"
STRONG_MODEL = "fake-strong-001"

# The shipped config selects no vendor, so a test that needs a resolvable tier
# config has to choose one. Deliberately **mixed**: the two tiers select
# independently, and a same-vendor pair on both sides would leave that path
# untested everywhere but here — which is exactly what the old Vertex-on-both
# default did. Nothing downstream of this reaches a network; the vendors are
# named so the binding and the gate see a realistic pair, not so a model runs.
TEST_TIER_ENV: dict[str, str] = {
    "STRIDE_MODEL_BASE_VENDOR": "openai",
    "STRIDE_MODEL_BASE_MODEL": "gpt-4.1-mini",
    "STRIDE_MODEL_STRONG_VENDOR": "vertex",
    "STRIDE_MODEL_STRONG_MODEL": "gemini-2.5-pro",
}

# What the selection above implies: one API key and one ADC triple, because the
# two tiers sit on vendors with different credential modes. Placeholders — the
# loader checks that a variable is *declared*, never that it authenticates, and
# no test here reaches a provider. Kept beside the selection so the two cannot
# drift; a tier moved to a third vendor needs its variables added here too.
TEST_CREDENTIAL_ENV: dict[str, str] = {
    "STRIDE_OPENAI_API_KEY": "sk-not-a-real-key",
    "STRIDE_VERTEX_PROJECT": "test-project",
    "STRIDE_VERTEX_LOCATION": "us-central1",
    "GOOGLE_APPLICATION_CREDENTIALS": "/nonexistent/adc.json",
}


def repo_tiers() -> ModelTierConfig:
    """The shipped tier config, resolved under the canonical test selection.

    One place, so the selection cannot drift between the nine modules that need
    it. Tests assert about topology, binding and provenance rather than about
    which vendor answered, so *which* pair this is remains a fixture detail.
    """
    return load_model_tiers(
        PROJECT_ROOT / "config" / "model_tiers.toml", env=TEST_TIER_ENV
    )


DESCRIPTION_TEXT = (
    "Customers log in to the web app; customers log in from the browser."
    " The web app runs on Cloud Run, and orders are stored in Postgres."
)
"""The submitted text every scripted job carries.

Every ``source_excerpt`` in :func:`valid_model` and the quote ground in
:func:`sample_threat` are verbatim spans of *this string*, because the gate now
checks an excerpt against the source it cites the same way the fan-in checks a
threat's quote. A fixture citing words its own job never carried is the exact
defect that check exists to catch, and one used by 29 tests would have failed
all of them.
"""


def valid_model(source_label: str = DEFAULT_DESCRIPTION_LABEL) -> SystemModel:
    """The shared gate-passing model.

    ``source_label`` is a parameter because the citation rule checks it against
    the *job's* labels: a model whose excerpts cite a source the job did not
    carry is invalid, so a test submitting its own labels must say so here too.
    Its excerpts are spans of :data:`DESCRIPTION_TEXT`, so it also passes the
    rule that the excerpt is really *in* that source.
    """
    return SystemModel(
        external_entities=[
            ExternalEntity(
                id="entity:customer",
                name="Customer",
                kind="human",
                trust_zone="boundary:internet",
                source_excerpt="customers log in from the browser",
                source_label=source_label,
                assets=["pii"],
            )
        ],
        processes=[
            Process(
                id="process:web-app",
                name="Web App",
                technology="Python/FastAPI on Cloud Run",
                trust_zone="boundary:internal-network",
                exposure="internet-facing",
                source_excerpt="the web app runs on Cloud Run",
                source_label=source_label,
            )
        ],
        data_stores=[
            DataStore(
                id="store:orders-db",
                name="Orders DB",
                technology="Cloud SQL Postgres",
                trust_zone="boundary:internal-network",
                data_classification="confidential",
                encryption_at_rest="unknown",
                source_excerpt="orders are stored in Postgres",
                source_label=source_label,
                assets=["business-critical-data", "pii"],
            )
        ],
        data_flows=[
            DataFlow(
                id="flow:customer-to-web-app:login",
                name="Login",
                source="entity:customer",
                destination="process:web-app",
                protocol="HTTPS",
                authentication="session cookie",
                data_description="credentials in, session out",
                encryption_in_transit="TLS 1.3",
                source_excerpt="customers log in from the browser",
                source_label=source_label,
            ),
            DataFlow(
                id="flow:web-app-to-orders-db:store-order",
                name="Store Order",
                source="process:web-app",
                destination="store:orders-db",
                protocol="Postgres wire protocol",
                authentication="IAM database auth",
                data_description="order rows",
                encryption_in_transit="unknown",
                source_excerpt="orders are stored in Postgres",
                source_label=source_label,
            ),
        ],
        trust_boundaries=[
            TrustBoundary(
                id="boundary:internet",
                name="Internet",
                kind="network",
                source_excerpt="customers log in from the browser",
                source_label=source_label,
            ),
            TrustBoundary(
                id="boundary:internal-network",
                name="Internal Network",
                kind="network",
                source_excerpt="the web app runs on Cloud Run",
                source_label=source_label,
            ),
        ],
        assumptions=[
            Assumption(
                assumption="web app is internet-facing",
                element_id="process:web-app",
                basis="customers reach it directly from the browser",
            )
        ],
    )


def sample_draft(
    threat_id: str = "S-01",
    category: str = "spoofing",
    **overrides: Any,
) -> DraftThreat:
    """One category agent's draft against valid_model(), before the critic rules."""
    threat = sample_threat(threat_id, category, **overrides)
    return DraftThreat.model_validate(
        threat.model_dump(exclude={"confidence", "verdict"})
    )


def sample_proposal(
    threat_id: str = "S-01",
    category: str = "spoofing",
    **overrides: Any,
) -> ThreatProposal:
    """What a category agent emits for sample_draft(), before resolution.

    The same two facts the draft is grounded on, *named* rather than
    serialized: the quote as a candidate, the crossing as a catalog ID. So
    resolving this against ``valid_model()``'s catalog, in the lane the second
    argument names, reproduces ``sample_draft()`` exactly — the property the
    two fixtures exist together to let a test assert.
    """
    threat = sample_threat(threat_id, category)
    fields: dict[str, Any] = threat.model_dump(
        mode="json", exclude={"confidence", "verdict", "grounds", "id", "category"}
    )
    # Called with the threat ID the resolved draft will carry, because that is
    # what a test is talking about — the proposal itself names only the number,
    # and the lane supplies the letter.
    fields["sequence"] = int(threat_id.split("-")[1])
    fields["quotes"] = [
        {
            "text": "Customers log in to the web app",
            "source_label": DEFAULT_DESCRIPTION_LABEL,
        }
    ]
    fields["evidence_refs"] = ["crossing:flow:customer-to-web-app:login"]
    fields.update(overrides)
    return ThreatProposal.model_validate(fields)


def sample_ruling(threat_id: str = "S-01", **overrides: Any) -> ThreatRuling:
    """The critic's ruling on one sample_draft(), overridable per test.

    Carries no ``severity``, which is the common case: the agent's rating
    stands unless the calibration step replaced it.
    """
    fields: dict[str, Any] = {
        "id": threat_id,
        "confidence": "high",
        "verdict": Verdict(status="confirmed"),
    }
    fields.update(overrides)
    return ThreatRuling(**fields)


def sample_threat(
    threat_id: str = "S-01",
    category: str = "spoofing",
    **overrides: Any,
) -> Threat:
    """One valid confirmed threat against valid_model(), overridable per test."""
    fields: dict[str, Any] = {
        "id": threat_id,
        "category": category,
        "title": "Session cookie theft enables account takeover",
        "description": "Stolen session cookies let an attacker impersonate"
        " the customer against the web app.",
        "affected_element_ids": ["flow:customer-to-web-app:login"],
        # One quote and one derived fact, so a default draft exercises two
        # branches rather than the cheapest one. The quote is a verbatim span of
        # the source ``sample_report`` submits, so it verifies through the
        # ladder; tests that need a failing quote override this field.
        "grounds": [
            Ground(
                kind="quote",
                text="Customers log in to the web app",
                source_label=DEFAULT_DESCRIPTION_LABEL,
            ),
            Ground(kind="derived-fact", flow_id="flow:customer-to-web-app:login"),
        ],
        "severity": Severity(
            likelihood="medium",
            impact="high",
            justification="Commodity attack against exposed sessions.",
        ),
        "confidence": "high",
        "mitigations": [Mitigation(summary="Set HttpOnly and Secure on cookies")],
        "verdict": Verdict(status="confirmed"),
    }
    fields.update(overrides)
    return Threat(**fields)


def sample_report(
    threats: list[Threat] | None = None,
    rejected_threats: list[Threat] | None = None,
) -> StrideReport:
    """A complete, internally consistent report over valid_model()."""
    model = valid_model()
    if threats is None:
        threats = [sample_threat()]
    if rejected_threats is None:
        rejected_threats = []
    created = datetime(2026, 7, 18, 14, 3, 21, tzinfo=UTC)
    return StrideReport(
        job=Job(
            id="job-8f3a2c91",
            created_at=created,
            completed_at=datetime(2026, 7, 18, 14, 4, 9, tzinfo=UTC),
        ),
        input=InputRef.of(
            system_name="Order Service",
            sources=[Source.description(DESCRIPTION_TEXT)],
        ),
        nodes=[
            NodeRun(node="extract", model="gemini-2.5-flash", duration_ms=3200),
            NodeRun(node="critic", model="gemini-2.5-pro", duration_ms=14500),
            NodeRun(node="assemble", duration_ms=20),
        ],
        system_model=model,
        boundary_crossings=model.boundary_crossings(),
        threats=threats,
        rejected_threats=rejected_threats,
        summary=build_summary(threats, rejected_threats, model),
    )


EMPTY_THREATS = '{"threats": []}'


def threats_json(*threats: BaseModel) -> str:
    """A review or category-agent node's emission: the list inside its ``threats`` key.

    The wrapper is the node's output-schema shape, not the domain's, so tests
    build it here rather than each spelling it out — a bare array is what these
    nodes emitted before the schema had to be convertible, and is now invalid.
    """
    return json.dumps({"threats": [t.model_dump(mode="json") for t in threats]})


def served_build(requested: str) -> str:
    """The build a scripted model claims answered: the request plus a suffix."""
    return f"{requested}-served"


def scripted_usage() -> types.GenerateContentResponseUsageMetadata:
    """The usage block a scripted model reports, in the provider's own spelling.

    Five distinct values, none of them a sum of the others, so a test can tell
    which neutral field each provider counter landed in — a block of equal
    numbers would pass a mapping that transposed two of them. ``thoughts`` sits
    outside ``candidates`` here, which is the Gemini-family reading; the
    recorded fields are deliberately not cross-checked, so the OpenAI-family
    reading is equally recordable.
    """
    return types.GenerateContentResponseUsageMetadata(
        prompt_token_count=1100,
        cached_content_token_count=700,
        candidates_token_count=300,
        thoughts_token_count=9000,
        total_token_count=10400,
    )


class ScriptedLlm(BaseLlm):
    """One node's model: replays a fixed emission and records the request.

    It reports a ``model_version`` distinct from the configured ``model``, the
    way a real provider does — the pinned string names a family, the served
    build names what answered. Keeping them different is what lets a test tell
    "recorded what was asked for" apart from "recorded what ran", and it is
    what makes the generation-identity path reachable at all without a live
    provider.

    It reports ``usage_metadata`` for the same reason: a stand-in that
    answered for free would leave the token-accounting path unreachable
    offline, and the one number the accounting exists to surface — reasoning
    tokens — is the one no visible output would reveal.
    """

    reply: str
    seen: list[str] = Field(default_factory=list)

    async def generate_content_async(
        self, llm_request, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self.seen.append(llm_request.config.system_instruction or "")
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=self.reply)]),
            model_version=served_build(self.model),
            usage_metadata=scripted_usage(),
        )


class SlowLlm(ScriptedLlm):
    """A stand-in that takes measurable time to answer.

    Every other stand-in replies instantly, which makes a whole class of timing
    defect invisible: a model's latency can be charged to the wrong node and
    every offline duration still reads as ~0 ms either way. This one waits long
    enough to land on one side of a millisecond-resolution assertion.
    """

    delay_s: float = 0.05

    async def generate_content_async(
        self, llm_request, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        await asyncio.sleep(self.delay_s)
        async for response in super().generate_content_async(llm_request, stream):
            yield response


class SilentLlm(ScriptedLlm):
    """A stand-in whose response carries no ``model_version``.

    Stands for a provider that reported no served build. Such a node must end
    up with no fingerprint rather than one keyed on what was merely requested.

    It still reports usage, which is the point of keeping the two apart: a
    token count is a fact about the call whether or not the provider also named
    the build that served it, so the missing build must cost the fingerprint
    and nothing else.
    """

    async def generate_content_async(
        self, llm_request, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self.seen.append(llm_request.config.system_instruction or "")
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=self.reply)]),
            usage_metadata=scripted_usage(),
        )


class UnmeteredLlm(ScriptedLlm):
    """A stand-in whose response carries no usage block at all.

    The other half of :class:`SilentLlm`: a provider that named its build and
    metered nothing. Such a node must carry no ``usage`` rather than a zeroed
    one, so a roll-up cannot read an unmeasured call as a free call.
    """

    async def generate_content_async(
        self, llm_request, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self.seen.append(llm_request.config.system_instruction or "")
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=self.reply)]),
            model_version=served_build(self.model),
        )


def scripted_pipeline(
    replies: dict[str, str],
    *,
    llm_class: type[ScriptedLlm] = ScriptedLlm,
    entry: Entry = ENTRY_EXTRACT,
    sampling: SamplingConfig | None = None,
) -> tuple[Pipeline, dict[str, ScriptedLlm]]:
    """The real graph, with every LLM node bound to its scripted stand-in.

    Repo skills, repo prompts, repo tier and sampling config — only the model's
    text is faked, so the topology, the per-tier binding and the provenance
    stamping under test are the shipped ones. The tier adapters are
    short-circuited by passing ``resolve_model`` directly: building them would
    run the credential check, which an offline test has no credentials to pass.

    ``sampling`` substitutes another *legal* deployment's decoding params for
    the shipped ones. The shipped file leaves several offered params unset, so
    without this the only values any test ever stamps are the ones this
    repository happens to ship — and a param no test sets is a param whose
    journey to the report nothing checks.
    """
    models: dict[str, ScriptedLlm] = {}
    graph_node_of = {
        tier_node: graph_node
        for graph_node, tier_node in TIER_NODE_BY_GRAPH_NODE.items()
    }

    def resolve(tier_node: str) -> BaseLlm:
        # ``build_pipeline`` resolves by canonical tier name; the scripts are
        # keyed by graph node name, which is what the tests talk about.
        node = graph_node_of[tier_node]
        base = node in (EXTRACT_NODE, REPAIR_NODE)
        models[node] = llm_class(
            model=BASE_MODEL if base else STRONG_MODEL,
            reply=replies.get(node, EMPTY_THREATS),
            seen=[],
        )
        return models[node]

    tiers = repo_tiers()
    sampling = sampling or load_sampling(
        PROJECT_ROOT / "config" / "sampling.toml", env={}
    )
    pipeline = build_pipeline(
        skill_loader=MarkdownLoader(PROJECT_ROOT / "skills"),
        prompt_loader=MarkdownLoader(PROJECT_ROOT / "prompts"),
        binding=NodeBinding.from_configs(tiers, sampling, resolve),
        entry=entry,
    )
    return pipeline, models
