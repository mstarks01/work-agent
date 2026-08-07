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
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Annotated, ClassVar, Literal, Self, get_args

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from stride_service.sources import Source
from stride_service.system_model import BoundaryCrossing, SystemModel

# The payload schema readers key on. Consumers that ignore unknown fields
# tolerate a minor bump; a major bump is a breaking change to a field's
# meaning. The policy, stated once here and in docs/Report-Schema.md:
# **additive fields are a minor bump; changing the meaning or the spelling of
# an existing value is major.**
#
# 2.0 is that rule applied to the finding-attribution cutover. ``grounds``
# becoming required on every threat is additive and would have been minor on
# its own; what earns the major is that ``nodes[].node`` changed the *values*
# it carries, from ``analyst_<category>`` to ``analyze_<category>``. A consumer
# keying on ``analyst_spoofing`` does not error — it matches nothing, silently.
#
# 2.1 adds ``nodes[].usage``. Purely additive — an optional object on a record
# that already existed, no field changing meaning or spelling — so it is minor
# by the rule above, and a 2.0 consumer that ignores unknown fields reads a 2.1
# report unchanged.
#
# 2.2 adds ``unresolved_mentions``, the marks for element IDs a description
# cites in prose that the model does not contain. A new optional top-level
# list, exactly the shape ``unverified_grounds`` already had, so it is minor by
# the same rule: a 2.1 consumer that ignores unknown fields reads a 2.2 report
# unchanged, and one that renders the marks gains a signal it never had.
#
# 2.3 adds ``missing_mitigations``, on the same argument again: a third
# optional top-level list of service-owned marks, no existing field changing
# meaning or spelling.
SCHEMA_VERSION = "2.3"

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

# What a finding can cite in its own support. Spelled out rather than terse
# (``unknown`` / ``derived``) because a bare ``unknown`` collides: it is already
# a legal *attribute value* across the System Model, so ``kind="unknown"`` would
# read as "the kind is unknown" rather than "the ground is an unknown
# attribute".
GroundKind = Literal["quote", "unknown-attribute", "derived-fact"]

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


def _bare_attribute(value: str) -> str:
    """An attribute name with any JSON-pointer prefix a model wrapped it in cut.

    Providers reach for pointer syntax unprompted: a run against
    ``gpt-5.6-sol`` grounded a threat on ``"/exposure"`` where the prompt and
    every exemplar spell it ``exposure``. The referent was right — ``Process``
    has that field — and the job died at :func:`~stride_service.critic.
    join_drafts` on the slash alone, taking all six lanes' work with it.

    Which spelling of a field name arrives is mechanical, so it is settled
    here rather than argued with in a prompt. The check itself does not
    loosen: the bare name still has to be a field the element's type actually
    declares, so ``/invented`` fails exactly as ``invented`` does.
    """
    return value.lstrip("/") if isinstance(value, str) else value


# Applied before the length constraints at each use site, so what is measured
# and what is compared against the element's fields are the same string.
AttributeName = Annotated[str, BeforeValidator(_bare_attribute)]


class UnknownRef(BaseModel):
    """Points a needs-info verdict at the unknown attribute that caused it."""

    model_config = ConfigDict(extra="forbid")

    element_id: str = Field(max_length=300)
    attribute: AttributeName = Field(min_length=1, max_length=100)


class Ground(BaseModel):
    """What justifies one finding: a quote, a named unknown, or a derived fact.

    Category-agent-owned and authored, never derived from the affected
    elements' ``source_excerpt``s: a derived citation would always render *a*
    quote and frequently the wrong one. An excerpt says why an *element*
    exists; a ground says why a *threat* was raised.

    ONE FLAT MODEL, NOT A DISCRIMINATED UNION. The union is the more honest
    shape — it forbids a nonsense combination in the schema itself, so a
    constrained model cannot generate one — and it was rejected anyway on a
    measured fact rather than a preference: **provider schema compilers are the
    unpredictable part of this system.** ``config/sampling.toml`` records that
    one vendor rejects ``SystemModel``'s compiled grammar as "too large", and
    that ``constrain_output = false`` is not a working answer to a schema a
    provider will not compile — unconstrained, the model fences its JSON and
    omits required fields. So the cost of a grammar a vendor chokes on is a
    dead run, not a degraded one, and this schema rides in ``DraftThreats`` on
    the ``strong`` tier for **six** category agents whose vendor is selected
    independently. ``oneOf`` is the construct with the thinnest, least uniform
    support across those vendors; the flat object is the portable shape.

    :class:`Verdict` is already exactly this pattern, so the repo has one
    answer to "tagged variant in a provider-facing schema" rather than two.

    What is given up, stated plainly: the schema no longer prevents a mis-shaped
    ``Ground`` at generation time. It is caught on arrival, by the validator
    below, and there is no re-ask path for a category agent's drafts — a
    mis-shape fails the job at :func:`~stride_service.critic.join_drafts`.

    The branches, and why each carries what it does:

    * ``quote`` — ``text`` + ``source_label``. 1000 characters is the same
      number as an element's ``source_excerpt``: one repo answer to "how long
      is a quoted span", not two. The label is what makes the quote resolvable
      to a :class:`SourceRef`, and the text is checked against that source's
      bytes by :mod:`stride_service.grounding`.
    * ``unknown-attribute`` — ``element_id`` + ``attribute``, spelled
      identically to :class:`UnknownRef`'s two fields so the shared referent is
      obvious, but a **separate type**. A ground is *backward-looking* — the
      unknown that made the agent raise the threat. ``related_unknowns`` is
      *forward-looking* — the unknown the critic says must be answered before
      the threat can be ruled on. Different author, different moment, different
      job; when they coincide that is signal, not redundancy.
    * ``derived-fact`` — ``flow_id`` alone, a **reference and never a copy**.
      The crossing's zones are recomputed from the system model the report
      already embeds, so a renderer holding only the report resolves them; a
      copy could disagree with what it was derived from, and then neither is
      answerable as the report's answer. There is deliberately no free-text
      ``fact`` field: free text is verifiable by no gate, and it would become
      the escape hatch an agent reaches for when it has neither a quote nor an
      unknown — precisely the finding whose justification matters most.
    """

    model_config = ConfigDict(extra="forbid")

    kind: GroundKind
    text: str = Field(default="", max_length=1000)  # quote
    source_label: str = Field(default="", max_length=200)  # quote
    element_id: str = Field(default="", max_length=300)  # unknown-attribute
    attribute: AttributeName = Field(default="", max_length=100)  # unknown-attribute
    flow_id: str = Field(default="", max_length=300)  # derived-fact

    # Which fields each branch requires. Everything not listed for a branch is
    # forbidden on it — a quote carrying an element_id is a shape error, not a
    # tolerated extra, because the two readings of such a record differ and
    # nothing downstream could choose between them.
    _REQUIRED: ClassVar[dict[GroundKind, tuple[str, ...]]] = {
        "quote": ("text", "source_label"),
        "unknown-attribute": ("element_id", "attribute"),
        "derived-fact": ("flow_id",),
    }

    @model_validator(mode="after")
    def _check_shape(self) -> Self:
        required = self._REQUIRED[self.kind]
        missing = [field for field in required if not getattr(self, field)]
        if missing:
            raise ValueError(
                f"a {self.kind} ground must carry {', '.join(required)};"
                f" missing {', '.join(missing)}"
            )
        present = [
            field
            for branch, fields in self._REQUIRED.items()
            if branch != self.kind
            for field in fields
            if field not in required and getattr(self, field)
        ]
        if present:
            raise ValueError(
                f"a {self.kind} ground must not carry {', '.join(sorted(present))}"
            )
        return self


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
    """One category agent's draft finding: the eight fields the agent owns.

    Everything a category agent produces and nothing it may rule on —
    ``verdict`` and ``confidence`` are the critic's, and appear only once a
    draft is promoted to a :class:`Threat`. This is the shape the prompt
    exemplars are lint-parsed against.

    ``grounds`` is ``min_length=1`` with **no maximum**, exactly like
    ``affected_element_ids``: this model caps no list, and runaway output stays
    governed where it already is, at the tier's ``max_output_tokens``.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[STRIDE]-\d{2}$")
    category: StrideCategory
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    affected_element_ids: list[str] = Field(min_length=1)
    grounds: list[Ground] = Field(min_length=1)
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
    """What a category agent node emits: an object wrapping its list of drafts.

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


class ThreatRuling(BaseModel):
    """The critic's ruling on one draft: the fields the critic owns, and no more.

    A ruling is **not** a threat. It names the draft it rules on by ``id`` and
    carries the two judgements that are the critic's — ``verdict`` and
    ``confidence`` — leaving the agent's eight fields where they already are.
    :func:`~stride_service.critic.assemble_threats` merges a ruling onto the
    draft it names to build the :class:`Threat` the report carries, so the
    report's shape is unchanged.

    WHY THE CRITIC NO LONGER RE-EMITS THE DRAFT. Its output was every draft
    transcribed whole plus a verdict, which made the single longest call in the
    graph proportional to the category agents' combined prose rather than to the
    judgement it was asked for. The service already holds those drafts — they
    are the same bytes it put in the critic's prompt — so the transcription
    bought nothing and cost the run's largest block of output tokens. It also
    cost correctness: re-emitting a description is a chance to alter it, and
    a critic told "do not rewrite descriptions" could still do so silently.
    Under this schema it cannot. That is why the element-reference check the
    review seam used to run is gone — a ruling carries no element references
    to break.

    ``severity`` is the one draft field a ruling may replace, and only where
    the critic's severity-calibration step changed a rating. ``None`` — the
    common case — keeps the agent's rating and justification as written.
    Present, it replaces both together, which is what stops a corrected rating
    from sitting beside a justification that argues for the old one. It is a
    whole :class:`Severity` rather than loose scalars so a partial override
    cannot be expressed.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[STRIDE]-\d{2}$")
    confidence: Rating
    verdict: Verdict
    severity: Severity | None = None


class ThreatRulings(BaseModel):
    """What the critic and its re-ask emit: the wrapper over one ruling per draft.

    Separate from :class:`DraftThreats` because the element type differs. See
    that class for why the wrapper exists at all, and why its field is still
    spelled ``threats`` on both: it is the shape the provider constrains
    generation to, not the domain's word for what is inside.
    """

    model_config = ConfigDict(extra="forbid")

    threats: list[ThreatRuling]


class TokenUsage(BaseModel):
    """What one node execution spent, as the provider reported it.

    Vendor-neutral field names, on the same principle as the model tiers: the
    provider's own spellings (``prompt_token_count``,
    ``candidates_token_count``, ``thoughts_token_count``,
    ``cached_content_token_count``) are one vendor's product vocabulary, and
    this record outlives the vendor it was read from.
    ``stride_service.execution`` owns the mapping.

    NOTHING HERE IS DERIVED, and that is the point. ``total_tokens`` is
    recorded rather than summed, and no validator asserts a relationship
    between the parts, because the parts do not agree across vendors on what
    they contain: Gemini reports ``thoughts`` *outside* ``candidates``, while
    an OpenAI-family reasoning model counts them *inside* completion tokens.
    A sum check would fail honest data from one of them, and a derived total
    would silently mean two different things depending on who answered. Same
    rule NodeRun already applies to ``model`` and ``requested_model``: record
    both, compute neither.

    ``reasoning_tokens`` is the field this record was added for. It is spent
    against the tier's ``max_output_tokens`` and it is invisible in the
    output, so a node can be the run's largest consumer while looking small.
    ``cached_prompt_tokens`` is the other one: it is the only direct evidence
    of whether a prefix a prompt was laid out to cache actually cached.

    Every field defaults to 0 rather than being required — a provider that
    reports three of the five is common, and dropping the record for the two
    it withheld would lose the three it gave. A node that reported *nothing*
    carries no ``TokenUsage`` at all, so an all-zero record never stands in
    for an absent one.
    """

    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int = Field(default=0, ge=0)
    cached_prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


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

    ``usage`` is what the node execution cost, ``None`` for a deterministic
    FunctionNode and for any LLM node whose provider reported nothing. It is
    deliberately *not* coupled to ``model`` the way ``sampling_fingerprint``
    is: a fingerprint keyed on a served build is incoherent without one, but a
    token count is a fact about the call regardless of whether the provider
    also named the build that served it, and refusing to record it would
    discard a real measurement to satisfy a symmetry nobody needs.
    """

    model_config = ConfigDict(extra="forbid")

    node: str = Field(min_length=1, max_length=100)
    model: str | None = None  # served; None for deterministic FunctionNodes
    requested_model: str | None = None  # configured
    sampling_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    duration_ms: int = Field(ge=0)
    usage: TokenUsage | None = None

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


class UnverifiedGround(BaseModel):
    """A quote ground the service looked for in its named source and did not find.

    **Service-owned, and deliberately outside :class:`Ground`.** ``Ground`` is
    agent-owned and ``extra="forbid"``; a verification field on it would ride
    into the six ``strong``-tier provider schemas as a field the agent could
    set about its own honesty. The same separation keeps :class:`UnknownRef`
    critic-only.

    A reference rather than a copy: the threat's ``id`` plus the index of the
    entry in its ``grounds`` list. The entry itself still renders — dropping it
    is not available, since ``grounds`` is ``min_length=1`` and dropping the
    last one would either produce an invalid draft or delete the finding, and
    silently removing a threat is the worst outcome a security tool can have.

    Marked per entry, failed closed per threat: a threat with one bad quote
    beside good ones is still justified, and
    :func:`~stride_service.critic.join_drafts` fails the job only where *no*
    ground on a threat verifies at all.
    """

    model_config = ConfigDict(extra="forbid")

    threat_id: str = Field(pattern=r"^[STRIDE]-\d{2}$")
    index: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=500)


class UnresolvedMention(BaseModel):
    """An element ID a description names in prose that the model does not contain.

    A **mention**, never a reference: ``affected_element_ids`` is the threat's
    structural claim about what it acts on, and one that does not resolve fails
    the job at :func:`~stride_service.critic.join_drafts`. This is the softer
    thing beside it — an ID written into the argument, which the analyze prompt
    asks for ("cite element and flow IDs inline") and which nothing checked
    until now. The description is the part a reader actually reads, so an ID in
    it that names nothing is a claim about a system this report does not
    describe.

    **Marked, never fatal**, for the reason :class:`UnverifiedGround` is: the
    fan-in has no re-ask path, and discarding six lanes of analysis over a
    mistyped ID in prose trades a whole report for a typo. The mark rides
    beside the threats exactly as an unverified quote does, and the same
    argument applies to why it is service-owned rather than a field on
    :class:`DraftThreat` — an agent must not be able to report on its own
    accuracy.

    The most valuable thing it catches is not a typo. The analyze prompt is
    built around one worked exemplar system, and it spends a whole section
    telling the agent never to cite that system's IDs. A description arguing
    about ``process:web-api`` in a job whose model has no such element is that
    contamination, in the one field where it reads as ordinary analysis.
    """

    model_config = ConfigDict(extra="forbid")

    threat_id: str = Field(pattern=r"^[STRIDE]-\d{2}$")
    mention: str = Field(min_length=1, max_length=300)


class MissingMitigation(BaseModel):
    """A threat offering no countermeasure, and no reason for offering none.

    ``mitigations`` is allowed to be empty, but only for one stated reason: the
    threat is conditional on an ``unknown``, and no countermeasure can be named
    without first learning that fact. The prompt says exactly that, and the
    branch rule makes it checkable — a threat triggered by an unknown carries an
    ``unknown-attribute`` ground, because that is the branch its trigger
    dictates. So "empty for the legitimate reason" and "empty with no reason"
    are mechanically distinguishable, and only the second is recorded here.

    A completeness signal rather than a correctness one, which is why it is a
    mark and not a failure: a finding with no recommended action is still a
    finding, and a reader who can see *which* findings came with nothing to do
    about them knows something the threat list alone does not tell them. The
    same fan-in argument applies as ever — nothing here is worth six lanes of
    analysis.
    """

    model_config = ConfigDict(extra="forbid")

    threat_id: str = Field(pattern=r"^[STRIDE]-\d{2}$")


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


def usage_by_node(nodes: Iterable[NodeRun]) -> dict[str, TokenUsage]:
    """Total tokens per node name, summed across that node's executions.

    The question this exists to answer is "which node costs the most", and a
    bare ``nodes`` list does not answer it: the critic on a revise path
    contributes two records, and reading a per-node total off the list means
    every caller writing the same fold. Keyed by node name rather than by tier
    because the tier is already answerable from the node, and the interesting
    comparison — the critic against one category agent, both on ``strong`` — is
    the one a tier roll-up destroys.

    Nodes reporting no usage contribute nothing and are absent from the result,
    rather than present with a zeroed record that reads as a free call.
    """
    totals: dict[str, TokenUsage] = {}
    for node in nodes:
        if node.usage is None:
            continue
        running = totals.get(node.node)
        if running is None:
            totals[node.node] = node.usage.model_copy()
            continue
        totals[node.node] = TokenUsage(
            **{
                field: getattr(running, field) + getattr(node.usage, field)
                for field in TokenUsage.model_fields
            }
        )
    return totals


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
    # Which quote grounds did not verify against the source they name. Empty on
    # the common run, and empty too on a job whose sources were never supplied
    # to the checker — see :func:`~stride_service.critic.join_drafts`.
    unverified_grounds: list[UnverifiedGround] = Field(default_factory=list)
    unresolved_mentions: list[UnresolvedMention] = Field(default_factory=list)
    missing_mitigations: list[MissingMitigation] = Field(default_factory=list)
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
        return (
            issues
            + self._unverified_mark_issues()
            + self._threat_mark_issues(self.unresolved_mentions, "unresolved mention")
            + self._threat_mark_issues(self.missing_mitigations, "missing mitigation")
        )

    def _unverified_mark_issues(self) -> list[str]:
        """Every unverified mark points at a grounds entry this report carries.

        A mark naming a threat or an index that is not here is worse than no
        mark: the viewer drops the quotation marks off nothing, and the entry
        the service actually failed to verify renders as though it had passed.
        """
        grounds_count = {
            threat.id: len(threat.grounds)
            for threat in (*self.threats, *self.rejected_threats)
        }
        issues = []
        for mark in self.unverified_grounds:
            count = grounds_count.get(mark.threat_id)
            if count is None:
                issues.append(
                    f"unverified ground names threat {mark.threat_id!r}, which"
                    " is not in this report"
                )
            elif mark.index >= count:
                issues.append(
                    f"unverified ground names index {mark.index} on threat"
                    f" {mark.threat_id!r}, which carries {count} grounds"
                )
        return issues

    def _threat_mark_issues(
        self, marks: Iterable[UnresolvedMention | MissingMitigation], what: str
    ) -> list[str]:
        """Every threat-level mark points at a threat this report carries.

        Same rule as the unverified marks, and same reason: a mark on a threat
        that is not here annotates nothing, while the threat that really earned
        it renders as though it had checked out. Shared across both mark types
        because the rule is the mark's *shape* — a threat ID and nothing else —
        rather than anything about what either records.
        """
        threat_ids = {threat.id for threat in (*self.threats, *self.rejected_threats)}
        return [
            f"{what} names threat {mark.threat_id!r}, which is not in this report"
            for mark in marks
            if mark.threat_id not in threat_ids
        ]
