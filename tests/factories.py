"""Shared factories: a small, fully valid System Model, plus threats and a
complete STRIDE report built against it.

Topology: customer (internet zone) -> web app -> orders db (both internal),
one cross-boundary flow and one intra-zone flow, one recorded assumption.
"""

from datetime import UTC, datetime
from typing import Any

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
from stride_service.system_model import (
    Assumption,
    DataFlow,
    DataStore,
    ExternalEntity,
    Process,
    SystemModel,
    TrustBoundary,
)


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
