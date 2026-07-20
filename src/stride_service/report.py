"""STRIDE report: the structured JSON payload the front-end retrieves for a job.

Shape and the decisions behind it live in wayfinder ticket 005 (STRIDE report
schema and severity model); the prototype it graduates from is on branch
``prototype/report-schema``.

Severity is qualitative likelihood x impact with the band **derived by a fixed
matrix, never asserted** by a model — the critic calibrates two narrow
judgments and evals check the arithmetic. Rejected threats ride in their own
``rejected_threats`` array as an audit trail. The report embeds the full
validated System Model plus derived boundary crossings, so it is
self-contained: every element reference resolves inside one payload.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

from stride_service.system_model import BoundaryCrossing, SystemModel

SCHEMA_VERSION = "1.0"

DEFAULT_DISCLAIMER = (
    "AI-generated threat model. Not reviewed by a human security analyst."
)

StrideCategory = Literal[
    "spoofing",
    "tampering",
    "repudiation",
    "information-disclosure",
    "denial-of-service",
    "elevation-of-privilege",
]

# The six categories in canonical STRIDE order, derived from the type itself
# so the two can never drift.
STRIDE_CATEGORIES: tuple[StrideCategory, ...] = get_args(StrideCategory)

Rating = Literal["low", "medium", "high"]
SeverityLevel = Literal["low", "medium", "high", "critical"]
VerdictStatus = Literal["confirmed", "needs-info", "rejected"]

# Threat IDs are <category letter>-<per-category sequence>, e.g. "S-01".
CATEGORY_LETTERS: dict[StrideCategory, str] = {
    "spoofing": "S",
    "tampering": "T",
    "repudiation": "R",
    "information-disclosure": "I",
    "denial-of-service": "D",
    "elevation-of-privilege": "E",
}

SEVERITY_MATRIX: dict[tuple[Rating, Rating], SeverityLevel] = {
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


def derive_severity_level(likelihood: Rating, impact: Rating) -> SeverityLevel:
    """The fixed likelihood x impact matrix — the only source of a severity band."""
    return SEVERITY_MATRIX[(likelihood, impact)]


class Severity(BaseModel):
    """Likelihood x impact, with the band derived — never asserted."""

    model_config = ConfigDict(extra="forbid")

    likelihood: Rating
    impact: Rating
    level: SeverityLevel | None = None
    justification: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def _derive_level(self) -> Self:
        derived = derive_severity_level(self.likelihood, self.impact)
        if self.level is not None and self.level != derived:
            raise ValueError(
                f"severity level {self.level!r} contradicts the matrix value"
                f" {derived!r} for likelihood={self.likelihood!r},"
                f" impact={self.impact!r}"
            )
        self.level = derived
        return self


class Mitigation(BaseModel):
    """One recommended countermeasure; summary for lists, detail on expand."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=200)
    detail: str = Field(default="", max_length=2000)


class UnknownRef(BaseModel):
    """Points a needs-info verdict at the unknown attribute that caused it."""

    model_config = ConfigDict(extra="forbid")

    element_id: str = Field(max_length=300)
    attribute: str = Field(min_length=1, max_length=100)


class Verdict(BaseModel):
    """The critic's ruling on one threat.

    ``needs-info`` must name the unknown attributes that caused it;
    ``needs-info`` and ``rejected`` must state a reason.
    """

    model_config = ConfigDict(extra="forbid")

    status: VerdictStatus
    reason: str = Field(default="", max_length=1000)
    related_unknowns: list[UnknownRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_shape(self) -> Self:
        if self.status == "needs-info" and not self.related_unknowns:
            raise ValueError(
                "a needs-info verdict must reference at least one unknown"
                " attribute in related_unknowns"
            )
        if self.status != "needs-info" and self.related_unknowns:
            raise ValueError(
                f"related_unknowns is only meaningful for needs-info verdicts,"
                f" not {self.status!r}"
            )
        if self.status != "confirmed" and not self.reason:
            raise ValueError(f"a {self.status} verdict must state a reason")
        return self


class DraftThreat(BaseModel):
    """One analyst's draft finding: the seven fields an analyst owns.

    Everything a category analyst produces and nothing it may rule on —
    ``verdict`` and ``confidence`` are the critic's, and appear only once a
    draft is promoted to a :class:`Threat`. This is the shape the prompt
    exemplars are lint-parsed against (ticket 013).
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[STRIDE]-\d{2}$")
    category: StrideCategory
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    affected_element_ids: list[str] = Field(min_length=1)
    severity: Severity
    mitigations: list[Mitigation] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_id_matches_category(self) -> Self:
        letter = CATEGORY_LETTERS[self.category]
        if not self.id.startswith(f"{letter}-"):
            raise ValueError(
                f"threat ID {self.id!r} does not carry the {self.category}"
                f" category letter {letter!r}"
            )
        return self


class Threat(DraftThreat):
    """One STRIDE finding, traceable to the elements it affects.

    A draft plus the critic's two judgments; the category-letter rule is
    inherited, so a draft and the threat it becomes are checked identically.
    """

    confidence: Rating  # critic-calibrated grounding in model facts
    verdict: Verdict


class NodeRun(BaseModel):
    """Per-node execution metadata: which model ran, and for how long."""

    model_config = ConfigDict(extra="forbid")

    node: str = Field(min_length=1, max_length=100)
    model: str | None = None  # None for deterministic FunctionNodes
    duration_ms: int = Field(ge=0)


class Job(BaseModel):
    """Identity and timing of the run that produced this report.

    A report only exists for a completed job, so ``status`` admits exactly
    the ``completed`` state from the job-lifecycle contract (ticket 008).
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=100)
    status: Literal["completed"] = "completed"
    created_at: datetime
    completed_at: datetime
    revise_rounds: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _check_timeline(self) -> Self:
        if self.completed_at < self.created_at:
            raise ValueError("completed_at precedes created_at")
        return self


class InputRef(BaseModel):
    """Ties the report back to the exact submitted text."""

    model_config = ConfigDict(extra="forbid")

    system_name: str = Field(min_length=1, max_length=200)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class Summary(BaseModel):
    """Counts the front-end can render without walking the threat list."""

    model_config = ConfigDict(extra="forbid")

    threat_count: int = Field(ge=0)
    by_category: dict[StrideCategory, int] = Field(default_factory=dict)
    by_severity: dict[SeverityLevel, int] = Field(default_factory=dict)
    needs_info_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    elements_analyzed: int = Field(ge=0)


def build_summary(
    threats: list[Threat],
    rejected_threats: list[Threat],
    system_model: SystemModel,
) -> Summary:
    """Compute the summary block mechanically from the report's own contents."""
    by_category = Counter(threat.category for threat in threats)
    by_severity = Counter(threat.severity.level for threat in threats)
    needs_info = sum(1 for t in threats if t.verdict.status == "needs-info")
    return Summary(
        threat_count=len(threats),
        by_category=dict(by_category),
        by_severity=dict(by_severity),
        needs_info_count=needs_info,
        rejected_count=len(rejected_threats),
        elements_analyzed=len(system_model.elements()),
    )


class StrideReport(BaseModel):
    """The complete report payload the front-end retrieves for a finished job.

    Self-containment is enforced, not assumed: every element reference in
    threats and verdicts must resolve inside the embedded System Model, the
    boundary crossings must be exactly the derived ones, threat IDs must be
    unique, actionable and rejected threats must sit in the right array, and
    the summary must match the counts it summarizes.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    disclaimer: str = DEFAULT_DISCLAIMER
    job: Job
    input: InputRef
    nodes: list[NodeRun]
    system_model: SystemModel
    boundary_crossings: list[BoundaryCrossing]
    threats: list[Threat]  # confirmed + needs-info, severity-ordered
    rejected_threats: list[Threat] = Field(default_factory=list)
    summary: Summary

    @model_validator(mode="after")
    def _check_self_contained(self) -> Self:
        issues = self._verdict_placement_issues()
        issues += self._reference_issues()

        threat_ids = [t.id for t in (*self.threats, *self.rejected_threats)]
        issues += [
            f"threat ID {threat_id!r} appears more than once"
            for threat_id, count in Counter(threat_ids).items()
            if count > 1
        ]

        if self.boundary_crossings != self.system_model.boundary_crossings():
            issues.append(
                "boundary_crossings do not match the crossings derived from"
                " the embedded system model"
            )
        if self.summary != build_summary(
            self.threats, self.rejected_threats, self.system_model
        ):
            issues.append("summary does not match the report's own contents")

        if issues:
            raise ValueError("; ".join(issues))
        return self

    def _verdict_placement_issues(self) -> list[str]:
        issues = [
            f"threat {threat.id!r} has a rejected verdict but sits in threats;"
            " it belongs in rejected_threats"
            for threat in self.threats
            if threat.verdict.status == "rejected"
        ]
        issues += [
            f"threat {threat.id!r} sits in rejected_threats but its verdict is"
            f" {threat.verdict.status!r}"
            for threat in self.rejected_threats
            if threat.verdict.status != "rejected"
        ]
        return issues

    def _reference_issues(self) -> list[str]:
        known_ids = {element.id for element in self.system_model.elements()}
        issues = []
        for threat in (*self.threats, *self.rejected_threats):
            refs = [
                *threat.affected_element_ids,
                *(ref.element_id for ref in threat.verdict.related_unknowns),
            ]
            issues += [
                f"threat {threat.id!r} references element {ref!r}, which is"
                " not in the embedded system model"
                for ref in refs
                if ref not in known_ids
            ]
        return issues
