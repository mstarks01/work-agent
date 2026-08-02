"""STRIDE report: the structured JSON payload the front-end retrieves for a job.

Severity is qualitative likelihood x impact with the band **derived by a fixed
matrix, never asserted** by a model — the critic calibrates two narrow
judgments and evals check the arithmetic. Rejected threats ride in their own
``rejected_threats`` array as an audit trail. The report embeds the full
validated System Model plus derived boundary crossings, so it is
self-contained: every element reference resolves inside one payload.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from typing import Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from stride_service.sources import Source
from stride_service.system_model import BoundaryCrossing, SystemModel

# The payload schema readers key on. Consumers that ignore unknown fields
# tolerate a minor bump; a major bump is a breaking change to a field's
# meaning.
SCHEMA_VERSION = "1.1"

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
    """Likelihood x impact, with the band derived — never asserted.

    ``level`` is :class:`SkipJsonSchema`-annotated, which keeps it off the wire
    schema while leaving the field itself untouched — still validated, still
    derived below, still in the report payload. That is not a cosmetic
    trim. A node's ``output_schema`` becomes the provider's response format, and
    OpenAI's strict structured outputs require *every* property to be listed as
    required, so ADK's converter marks even an optional one as such. A ``level``
    the model must emit is a model asserting the band — against the prompt,
    which says never to state one, and against the validator below, which raises
    when the assertion contradicts the matrix. Leaving it in the schema makes a
    contradiction the model is *obliged* to risk on every threat it rules.

    So the rule "derived, never asserted" is enforced where it becomes
    unbreakable: the model is never given the field to fill in. The raise below
    stays for every other way a value can arrive.
    """

    model_config = ConfigDict(extra="forbid")

    likelihood: Rating
    impact: Rating
    level: SkipJsonSchema[SeverityLevel | None] = None
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
    exemplars are lint-parsed against.
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


class DraftThreats(BaseModel):
    """What an analyst node emits: an object wrapping its list of drafts.

    The wrapper exists for the *schema*, not for the domain. A node's
    ``output_schema`` is what the graph asks the provider to constrain
    generation to, and a bare ``list[DraftThreat]`` cannot be asked for: ADK
    cannot convert a generic alias into a response format, so it sends none and
    the node generates unconstrained — silently, with only a log line. Wrapping
    the list in a model gives the conversion something it can carry, and an
    object root at that, which is what OpenAI's structured outputs require and
    a bare array would not satisfy.

    Nothing downstream sees it: the graph unwraps at the node boundary, so the
    domain keeps working in lists.
    """

    model_config = ConfigDict(extra="forbid")

    threats: list[DraftThreat]


class ReviewedThreats(BaseModel):
    """What the critic and its re-ask emit: the same wrapper over ruled threats.

    Separate from :class:`DraftThreats` because the element type differs — a
    reviewed threat carries the critic's ``verdict`` and ``confidence``. See
    that class for why the wrapper exists at all.
    """

    model_config = ConfigDict(extra="forbid")

    threats: list[Threat]


class NodeRun(BaseModel):
    """Per-node execution metadata: which model ran, its sampling identity, timing.

    Two model fields, because they answer different questions and the report
    **records both rather than computing either**:

    * ``model`` is the **served** build, vendor-prefixed —
      ``vertex_ai/gemini-2.5-pro-002``. What actually answered, read back from
      the response.
    * ``requested_model`` is the **configured** route —
      ``vertex_ai/gemini-2.5-pro``. What the run asked for.

    Their disagreement *is* the drift signal, and it needs no comparison logic
    here: drift falls out of certification for free, because a moved build
    yields a fingerprint no manifest blessed. A served-vs-configured comparison
    would instead need a per-vendor served-id normalization table — a fourth
    mirrored vendor fact, and one that fails silently.

    ``sampling_fingerprint`` is the generation-identity hash: ``sha256(served
    route, resolved tier sampling)``, recomputable from this node's ``model``
    and the report's top-level per-tier ``sampling`` clear block. It is computed
    per node *execution*, so a build that moves mid-run gives one node two
    hashes. A deterministic FunctionNode carries none of the three.
    """

    model_config = ConfigDict(extra="forbid")

    node: str = Field(min_length=1, max_length=100)
    model: str | None = None  # served; None for deterministic FunctionNodes
    requested_model: str | None = None  # configured
    sampling_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    duration_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def _fingerprint_needs_model(self) -> Self:
        # A fingerprint keyed on the served model is incoherent without one; a
        # deterministic node has neither.
        if self.sampling_fingerprint is not None and self.model is None:
            raise ValueError(
                "sampling_fingerprint requires a served model on the same node"
            )
        return self


class Job(BaseModel):
    """Identity and timing of the run that produced this report.

    A report only exists for a completed job, so ``status`` admits exactly the
    ``completed`` state from the job-lifecycle contract.
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


class SourceRef(BaseModel):
    """One submitted source, identified without carrying its text.

    The label is the same key an element's ``source_label`` cites, so a reader
    holding only the report can tell which source a quote came from and verify
    that source's bytes against a digest — without the service ever storing the
    untrusted text.
    """

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=50)
    label: str = Field(min_length=1, max_length=200)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class InputRef(BaseModel):
    """Ties the report back to the exact submitted sources."""

    model_config = ConfigDict(extra="forbid")

    system_name: str = Field(min_length=1, max_length=200)
    sources: list[SourceRef] = Field(min_length=1)
    # Taken **over the refs**, not over the concatenated text: the refs are in
    # the report, so this stays recomputable from the report alone — which a
    # digest of bytes nobody kept would not be.
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @staticmethod
    def aggregate_digest(refs: Sequence[SourceRef]) -> str:
        """The one way the aggregate is computed, wherever a report is built.

        Order-sensitive and separator-delimited, so two jobs whose labels and
        digests merely concatenate to the same string do not collide.
        """
        joined = "\n".join(f"{ref.kind}\x1f{ref.label}\x1f{ref.sha256}" for ref in refs)
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()

    @classmethod
    def of(cls, *, system_name: str, sources: Sequence[Source]) -> Self:
        """Build the reference for one job's sources, digests and all."""
        refs = [
            SourceRef(
                kind=source.kind,
                label=source.label,
                sha256=hashlib.sha256(source.text.encode("utf-8")).hexdigest(),
            )
            for source in sources
        ]
        return cls(
            system_name=system_name,
            sources=refs,
            source_sha256=cls.aggregate_digest(refs),
        )


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
    # The resolved per-tier decoding params this run used, in the clear, once
    # per tier: tier name -> the tier's resolved sampling values (the
    # serialized ``TierSampling``). Recorded as plain scalars, not the
    # ``TierSampling`` model, so this low-level schema module stays free of the
    # sampling/model_tiers import (which cycles back through skills). Each
    # node's sampling_fingerprint is recomputable from its served model and its
    # tier's entry here. Empty only on reports with no LLM provenance at all —
    # the stub runner's. An eval report carries this block like any other: a
    # sweep's fingerprints are evidence, and evidence nobody can recompute from
    # the artifact is an assertion.
    sampling: dict[str, dict[str, float | int | None]] = Field(default_factory=dict)
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
