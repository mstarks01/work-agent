"""PROTOTYPE — throwaway. Wayfinder ticket 005: STRIDE report schema.

Draft Pydantic models for the report JSON the front-end consumes, plus a
realistic sample report so the front-end team has something concrete to
react to. Not production code; the validated shape graduates into
``stride_service`` and this file dies on its throwaway branch.

Run: uv run python prototypes/report_schema_prototype.py
Prints the sample report and writes prototypes/sample_report.json.

Key decisions being prototyped (react to these):
- Severity = qualitative likelihood x impact, band DERIVED by a fixed
  matrix (never asserted directly by a model), so the critic calibrates
  two narrow judgments and evals can check the arithmetic.
- Per-threat confidence: qualitative low/medium/high, calibrated by the
  critic (how well the threat is grounded in model facts). Orthogonal to
  the verdict, which is the critic's ruling on inclusion.
- Rejected threats ride in the report in their own array — audit trail.
- The full validated System Model is embedded, so the report is
  self-contained: every threat's element refs resolve inside one payload.
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from stride_service.system_model import (
    Assumption,
    BoundaryCrossing,
    DataFlow,
    DataStore,
    ExternalEntity,
    Process,
    SystemModel,
    TrustBoundary,
)
from stride_service.validation import validate

StrideCategory = Literal[
    "spoofing",
    "tampering",
    "repudiation",
    "information-disclosure",
    "denial-of-service",
    "elevation-of-privilege",
]

Rating = Literal["low", "medium", "high"]
SeverityLevel = Literal["low", "medium", "high", "critical"]

_SEVERITY_MATRIX: dict[tuple[Rating, Rating], SeverityLevel] = {
    ("high", "high"): "critical",
    ("high", "medium"): "high",
    ("medium", "high"): "high",
    ("high", "low"): "medium",
    ("medium", "medium"): "medium",
    ("low", "high"): "medium",
    ("medium", "low"): "low",
    ("low", "medium"): "low",
    ("low", "low"): "low",
}


class Severity(BaseModel):
    """Likelihood x impact, with the band derived — never asserted."""

    model_config = ConfigDict(extra="forbid")

    likelihood: Rating
    impact: Rating
    level: SeverityLevel | None = None
    justification: str = Field(max_length=1000)

    @model_validator(mode="after")
    def _derive_level(self) -> Self:
        derived = _SEVERITY_MATRIX[(self.likelihood, self.impact)]
        if self.level is not None and self.level != derived:
            raise ValueError(f"severity level {self.level!r} contradicts matrix")
        self.level = derived
        return self


class Mitigation(BaseModel):
    """One recommended countermeasure; summary for lists, detail on expand."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(max_length=200)
    detail: str = Field(default="", max_length=2000)


class UnknownRef(BaseModel):
    """Points a needs-info verdict at the unknown attribute that caused it."""

    model_config = ConfigDict(extra="forbid")

    element_id: str
    attribute: str


class Verdict(BaseModel):
    """The critic's ruling on one threat."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["confirmed", "needs-info", "rejected"]
    reason: str = Field(default="", max_length=1000)
    related_unknowns: list[UnknownRef] = Field(default_factory=list)


class Threat(BaseModel):
    """One STRIDE finding, traceable to the elements it affects."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[STRIDE]-\d{2}$")
    category: StrideCategory
    title: str = Field(max_length=200)
    description: str = Field(max_length=4000)
    affected_element_ids: list[str] = Field(min_length=1)
    severity: Severity
    confidence: Rating  # critic-calibrated grounding in model facts
    mitigations: list[Mitigation]
    verdict: Verdict


class NodeRun(BaseModel):
    """Per-node execution metadata: which model ran, and for how long."""

    model_config = ConfigDict(extra="forbid")

    node: str
    model: str | None = None  # None for deterministic FunctionNodes
    duration_ms: int


class Job(BaseModel):
    """Identity and timing of the run that produced this report."""

    model_config = ConfigDict(extra="forbid")

    id: str
    status: Literal["complete"]
    created_at: datetime
    completed_at: datetime
    revise_rounds: int = 0


class InputRef(BaseModel):
    """Ties the report back to the exact submitted text."""

    model_config = ConfigDict(extra="forbid")

    system_name: str
    source_sha256: str


class Summary(BaseModel):
    """Counts the front-end can render without walking the threat list."""

    model_config = ConfigDict(extra="forbid")

    threat_count: int
    by_category: dict[StrideCategory, int]
    by_severity: dict[SeverityLevel, int]
    needs_info_count: int
    rejected_count: int
    elements_analyzed: int


class StrideReport(BaseModel):
    """The complete report payload the front-end retrieves for a finished job."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str
    disclaimer: str
    job: Job
    input: InputRef
    nodes: list[NodeRun]
    system_model: SystemModel
    boundary_crossings: list[BoundaryCrossing]
    threats: list[Threat]  # confirmed + needs-info, severity-ordered
    rejected_threats: list[Threat]
    summary: Summary


# --- Sample system: a small order/payments service --------------------------

ZONES = {
    "internet": "boundary:internet",
    "dmz": "boundary:dmz",
    "internal": "boundary:internal",
}


def build_system_model() -> SystemModel:
    return SystemModel(
        external_entities=[
            ExternalEntity(
                id="entity:customer",
                name="Customer",
                description="Shopper using the web storefront.",
                kind="human",
                trust_zone=ZONES["internet"],
                assets=["pii"],
                source_excerpt="customers place orders through the web store",
            ),
            ExternalEntity(
                id="entity:payment-gateway",
                name="Payment Gateway",
                description="Third-party card processor.",
                kind="external-system",
                trust_zone=ZONES["internet"],
                assets=["financial"],
                source_excerpt="we charge cards via a hosted payment gateway",
            ),
        ],
        processes=[
            Process(
                id="process:storefront-api",
                name="Storefront API",
                description="Public API serving the storefront.",
                technology="Python/FastAPI on Cloud Run",
                trust_zone=ZONES["dmz"],
                exposure="internet-facing",
                assets=["business-critical-data", "availability-critical"],
                source_excerpt="a FastAPI service handles all storefront traffic",
            ),
            Process(
                id="process:auth-service",
                name="Auth Service",
                description="Issues and verifies session tokens.",
                technology="Node.js",
                trust_zone=ZONES["internal"],
                exposure="internal",
                assets=["credentials"],
                source_excerpt="an internal auth service signs session JWTs",
            ),
        ],
        data_stores=[
            DataStore(
                id="store:orders-db",
                name="Orders DB",
                description="Orders, customer profiles, payment references.",
                technology="PostgreSQL 15",
                trust_zone=ZONES["internal"],
                data_classification="confidential",
                encryption_at_rest="unknown",
                assets=["pii", "financial", "business-critical-data"],
                source_excerpt="orders and customer data live in Postgres",
            ),
        ],
        data_flows=[
            DataFlow(
                id="flow:customer-to-storefront-api:place-order",
                name="Place Order",
                source="entity:customer",
                destination="process:storefront-api",
                protocol="HTTPS",
                authentication="session JWT cookie",
                data_description="cart contents, shipping address; order "
                "confirmation in response",
                encryption_in_transit="TLS 1.3",
            ),
            DataFlow(
                id="flow:storefront-api-to-auth-service:verify-session",
                name="Verify Session",
                source="process:storefront-api",
                destination="process:auth-service",
                protocol="gRPC",
                authentication="service account JWT",
                data_description="session token; validity claims in response",
                encryption_in_transit="TLS 1.2",
            ),
            DataFlow(
                id="flow:storefront-api-to-orders-db:read-write-orders",
                name="Read Write Orders",
                source="process:storefront-api",
                destination="store:orders-db",
                protocol="PostgreSQL wire protocol",
                authentication="database user/password",
                data_description="order rows, customer profiles",
                encryption_in_transit="none",
            ),
            DataFlow(
                id="flow:storefront-api-to-payment-gateway:charge-card",
                name="Charge Card",
                source="process:storefront-api",
                destination="entity:payment-gateway",
                protocol="HTTPS",
                authentication="API key",
                data_description="charge requests; charge results in response",
                encryption_in_transit="TLS 1.2",
            ),
            DataFlow(
                id="flow:payment-gateway-to-storefront-api:payment-webhook",
                name="Payment Webhook",
                source="entity:payment-gateway",
                destination="process:storefront-api",
                protocol="HTTPS",
                authentication="unknown",
                data_description="asynchronous payment status events",
                encryption_in_transit="TLS 1.2",
            ),
        ],
        trust_boundaries=[
            TrustBoundary(id=ZONES["internet"], name="Internet", kind="network"),
            TrustBoundary(id=ZONES["dmz"], name="DMZ", kind="network"),
            TrustBoundary(id=ZONES["internal"], name="Internal", kind="network"),
        ],
        assumptions=[
            Assumption(
                assumption="Session cookies are JWTs signed by the auth service",
                element_id="flow:customer-to-storefront-api:place-order",
                basis="input says 'auth service signs session JWTs'",
            ),
        ],
    )


def build_threats() -> tuple[list[Threat], list[Threat]]:
    """Sample threats: one per STRIDE category, all three verdict states."""
    threats = [
        Threat(
            id="S-01",
            category="spoofing",
            title="Forged payment webhook confirms unpaid orders",
            description="The payment webhook endpoint's authentication is "
            "unknown. If events are not verified, anyone who discovers the "
            "URL can post a fabricated 'payment succeeded' event and receive "
            "goods without paying.",
            affected_element_ids=[
                "flow:payment-gateway-to-storefront-api:payment-webhook",
                "process:storefront-api",
            ],
            severity=Severity(
                likelihood="high",
                impact="high",
                justification="Internet-reachable endpoint; a forged event "
                "directly converts to financial loss.",
            ),
            confidence="medium",
            mitigations=[
                Mitigation(
                    summary="Verify webhook signatures",
                    detail="Validate the gateway's event signature (e.g. HMAC "
                    "header) before acting; reject unsigned events.",
                ),
                Mitigation(
                    summary="Reconcile against the gateway API",
                    detail="Treat webhooks as hints; confirm payment status "
                    "with an authenticated API read before fulfilment.",
                ),
            ],
            verdict=Verdict(
                status="needs-info",
                reason="Webhook authentication is unknown; confirm whether "
                "signature verification exists.",
                related_unknowns=[
                    UnknownRef(
                        element_id="flow:payment-gateway-to-storefront-api:"
                        "payment-webhook",
                        attribute="authentication",
                    )
                ],
            ),
        ),
        Threat(
            id="T-01",
            category="tampering",
            title="Order rows alterable on the unencrypted database link",
            description="Traffic between the Storefront API and the Orders DB "
            "is unencrypted. An attacker on the network path can modify order "
            "rows in flight — prices, shipping addresses, payment references.",
            affected_element_ids=[
                "flow:storefront-api-to-orders-db:read-write-orders",
                "store:orders-db",
            ],
            severity=Severity(
                likelihood="medium",
                impact="high",
                justification="Requires internal network position, but "
                "business-critical order data is fully exposed to it.",
            ),
            confidence="high",
            mitigations=[
                Mitigation(
                    summary="Enforce TLS to PostgreSQL",
                    detail="Set sslmode=verify-full on clients and "
                    "ssl=on/hostssl rules on the server.",
                ),
            ],
            verdict=Verdict(status="confirmed"),
        ),
        Threat(
            id="R-01",
            category="repudiation",
            title="Order placement lacks an attributable audit trail",
            description="No audit logging is described for order placement. A "
            "customer can dispute having placed an order, and the business "
            "cannot prove otherwise; insiders can modify orders untraced.",
            affected_element_ids=[
                "process:storefront-api",
                "store:orders-db",
            ],
            severity=Severity(
                likelihood="medium",
                impact="medium",
                justification="Disputes are routine in commerce; losses are "
                "bounded per order.",
            ),
            confidence="medium",
            mitigations=[
                Mitigation(
                    summary="Append-only order audit log",
                    detail="Log actor, action, timestamp, and request ID for "
                    "every order mutation to tamper-evident storage.",
                ),
            ],
            verdict=Verdict(status="confirmed"),
        ),
        Threat(
            id="I-01",
            category="information-disclosure",
            title="Customer PII exposed if database storage is compromised",
            description="Orders DB holds PII and payment references; "
            "encryption at rest is unknown. Stolen disks, snapshots, or "
            "backups would expose customer data.",
            affected_element_ids=["store:orders-db"],
            severity=Severity(
                likelihood="low",
                impact="high",
                justification="Storage-level compromise is uncommon but "
                "exposes the full customer dataset.",
            ),
            confidence="high",
            mitigations=[
                Mitigation(
                    summary="Confirm and enforce encryption at rest",
                    detail="Verify disk/tablespace encryption and encrypted "
                    "backups; document key management.",
                ),
            ],
            verdict=Verdict(
                status="needs-info",
                reason="Encryption at rest is unknown for the Orders DB.",
                related_unknowns=[
                    UnknownRef(
                        element_id="store:orders-db",
                        attribute="encryption_at_rest",
                    )
                ],
            ),
        ),
        Threat(
            id="D-01",
            category="denial-of-service",
            title="Storefront API can be saturated from the internet",
            description="The Storefront API is internet-facing and "
            "availability-critical; no rate limiting or DDoS protection is "
            "described. Request floods take the store offline.",
            affected_element_ids=["process:storefront-api"],
            severity=Severity(
                likelihood="medium",
                impact="medium",
                justification="Commodity attack; Cloud Run absorbs some load "
                "but cost and quota limits still bite.",
            ),
            confidence="medium",
            mitigations=[
                Mitigation(
                    summary="Rate limiting at the edge",
                    detail="Put Cloud Armor (or equivalent) in front; set "
                    "per-client quotas and Cloud Run max instances.",
                ),
            ],
            verdict=Verdict(status="confirmed"),
        ),
    ]
    rejected = [
        Threat(
            id="E-01",
            category="elevation-of-privilege",
            title="SQL injection grants admin on the Orders DB",
            description="Crafted input reaches dynamic SQL in the Storefront "
            "API and escalates to database superuser.",
            affected_element_ids=[
                "process:storefront-api",
                "store:orders-db",
            ],
            severity=Severity(
                likelihood="medium",
                impact="high",
                justification="Analyst-asserted; not calibrated by critic.",
            ),
            confidence="low",
            mitigations=[
                Mitigation(summary="Parameterize all queries"),
            ],
            verdict=Verdict(
                status="rejected",
                reason="Not grounded in model facts: no evidence of dynamic "
                "SQL construction, and superuser escalation assumes "
                "privileges the model does not describe.",
            ),
        ),
    ]
    return threats, rejected


def build_report() -> StrideReport:
    system_model = build_system_model()
    issues = validate(system_model)
    if issues:
        raise SystemExit(f"sample system model is invalid: {issues}")

    threats, rejected = build_threats()
    by_category: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for threat in threats:
        by_category[threat.category] = by_category.get(threat.category, 0) + 1
        level = threat.severity.level
        by_severity[level] = by_severity.get(level, 0) + 1

    created = datetime(2026, 7, 18, 14, 3, 21, tzinfo=UTC)
    completed = datetime(2026, 7, 18, 14, 4, 9, tzinfo=UTC)
    analyst_nodes = [
        NodeRun(node=f"analyst-{cat}", model="gemini-2.5-pro", duration_ms=9000)
        for cat in (
            "spoofing",
            "tampering",
            "repudiation",
            "information-disclosure",
            "denial-of-service",
            "elevation-of-privilege",
        )
    ]
    return StrideReport(
        schema_version="0.1-prototype",
        disclaimer="AI-generated threat model. Not reviewed by a human "
        "security analyst.",
        job=Job(
            id="job-8f3a2c91",
            status="complete",
            created_at=created,
            completed_at=completed,
        ),
        input=InputRef(
            system_name="Order & Payments Service",
            source_sha256=hashlib.sha256(b"sample submission text").hexdigest(),
        ),
        nodes=[
            NodeRun(node="extract", model="gemini-2.5-flash", duration_ms=3200),
            NodeRun(node="validate", duration_ms=12),
            NodeRun(node="prepare", duration_ms=8),
            *analyst_nodes,
            NodeRun(node="join", duration_ms=5),
            NodeRun(node="critic", model="gemini-2.5-pro", duration_ms=14500),
            NodeRun(node="assemble", duration_ms=20),
        ],
        system_model=system_model,
        boundary_crossings=system_model.boundary_crossings(),
        threats=threats,
        rejected_threats=rejected,
        summary=Summary(
            threat_count=len(threats),
            by_category=by_category,
            by_severity=by_severity,
            needs_info_count=sum(
                1 for t in threats if t.verdict.status == "needs-info"
            ),
            rejected_count=len(rejected),
            elements_analyzed=len(system_model.elements()),
        ),
    )


def main() -> None:
    report = build_report()
    payload = report.model_dump_json(indent=2)
    out_path = Path(__file__).parent / "sample_report.json"
    out_path.write_text(payload + "\n")
    print(payload)
    print(f"\n-- written to {out_path}", flush=True)


if __name__ == "__main__":
    main()
