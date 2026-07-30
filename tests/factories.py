"""Shared factories: a small, fully valid System Model, plus threats and a
complete STRIDE report built against it.

Topology: customer (internet zone) -> web app -> orders db (both internal),
one cross-boundary flow and one intra-zone flow, one recorded assumption.

Also home to :class:`ScriptedLlm`, the offline stand-in every graph-driving
test binds its nodes to. It lives here rather than in one test module because
the service tests and the eval tests must drive the graph through the *same*
stand-in: the eval harness previously used a copy that reported no
``model_version``, which is what let a whole class of provenance defect stay
invisible to the eval lane.
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from stride_service.binding import NodeBinding
from stride_service.graph import (
    ENTRY_EXTRACT,
    EXTRACT_NODE,
    REPAIR_NODE,
    TIER_NODE_BY_GRAPH_NODE,
    Pipeline,
    build_pipeline,
)
from stride_service.markdown_loader import MarkdownLoader
from stride_service.model_tiers import load_model_tiers
from stride_service.report import (
    DraftThreat,
    InputRef,
    Job,
    Mitigation,
    NodeRun,
    Severity,
    StrideReport,
    Threat,
    Verdict,
    build_summary,
)
from stride_service.sampling import load_sampling
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


def valid_model() -> SystemModel:
    return SystemModel(
        external_entities=[
            ExternalEntity(
                id="entity:customer",
                name="Customer",
                kind="human",
                trust_zone="boundary:internet",
                source_excerpt="customers log in from the browser",
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
            ),
        ],
        trust_boundaries=[
            TrustBoundary(
                id="boundary:internet",
                name="Internet",
                kind="network",
                source_excerpt="customers log in from the browser",
            ),
            TrustBoundary(
                id="boundary:internal-network",
                name="Internal Network",
                kind="network",
                source_excerpt="the web app runs on Cloud Run",
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
    """One analyst draft against valid_model(), before the critic rules on it."""
    threat = sample_threat(threat_id, category, **overrides)
    return DraftThreat.model_validate(
        threat.model_dump(exclude={"confidence", "verdict"})
    )


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
        input=InputRef(system_name="Order Service", source_sha256="0" * 64),
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


def served_build(requested: str) -> str:
    """The build a scripted model claims answered: the request plus a suffix."""
    return f"{requested}-served"


class ScriptedLlm(BaseLlm):
    """One node's model: replays a fixed emission and records the request.

    It reports a ``model_version`` distinct from the configured ``model``, the
    way a real provider does — the pinned string names a family, the served
    build names what answered. Keeping them different is what lets a test tell
    "recorded what was asked for" apart from "recorded what ran", and it is
    what makes the generation-identity path reachable at all without a live
    provider.
    """

    reply: str
    seen: list[str] = []

    async def generate_content_async(
        self, llm_request, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self.seen.append(llm_request.config.system_instruction or "")
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=self.reply)]),
            model_version=served_build(self.model),
        )


class SilentLlm(ScriptedLlm):
    """A stand-in whose response carries no ``model_version``.

    Stands for a provider that reported no served build. Such a node must end
    up with no fingerprint rather than one keyed on what was merely requested.
    """

    async def generate_content_async(
        self, llm_request, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self.seen.append(llm_request.config.system_instruction or "")
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=self.reply)])
        )


def scripted_pipeline(
    replies: dict[str, str],
    *,
    llm_class: type[ScriptedLlm] = ScriptedLlm,
    entry: str = ENTRY_EXTRACT,
) -> tuple[Pipeline, dict[str, ScriptedLlm]]:
    """The real graph, with every LLM node bound to its scripted stand-in.

    Repo skills, repo prompts, repo tier and sampling config — only the model's
    text is faked, so the topology, the per-tier binding and the provenance
    stamping under test are the shipped ones. The tier adapters are
    short-circuited by passing ``resolve_model`` directly: building them would
    run the credential check, which an offline test has no credentials to pass.
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
            reply=replies.get(node, "[]"),
            seen=[],
        )
        return models[node]

    tiers = load_model_tiers(PROJECT_ROOT / "config" / "model_tiers.toml", env={})
    sampling = load_sampling(PROJECT_ROOT / "config" / "sampling.toml", env={})
    pipeline = build_pipeline(
        skill_loader=MarkdownLoader(PROJECT_ROOT / "skills"),
        prompt_loader=MarkdownLoader(PROJECT_ROOT / "prompts"),
        binding=NodeBinding.from_configs(tiers, sampling, resolve),
        entry=entry,
    )
    return pipeline, models
