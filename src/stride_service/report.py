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
#
# 2.4 adds ``coverage``, the per-category account of what deterministic
# analysis put in front of each agent and how much of it the drafts came back
# citing. Optional, additive, service-owned and computed in code, so the same
# rule applies a fourth time.
#
# 2.5 adds ``shared_element_names``, the marks for elements of different types
# whose names normalize to one slug. A fifth optional top-level list of
# service-owned marks, no existing field changing meaning or spelling, so the
# rule holds a fifth time. Minor rather than major although it is the first
# mark about the *model* rather than the threats: what a consumer must do with
# an unknown field does not depend on what the field describes.
#
# 2.6 widens the *values* the ``sampling`` clear block can carry to every type
# a resolved sampling param holds — which now includes the reasoning effort's
# enum string. No field is added, removed or renamed. It is the first entry
# here that is a fix rather than an addition: the block was typed to numbers
# only, so a deployment that set ``thinking`` — an offered, documented,
# build-gated param — produced reports that could not be assembled at all, and
# failed at the end of a paid-for run rather than at startup. No report with
# such a value has ever existed, so nothing a 2.5 consumer already parses
# changes meaning; what changes is that a value it never could have seen is now
# reachable, and a consumer reading the block as numbers must widen with it.
#
# 2.7 adds ``analysis_context``: the instruction digest, the domain packs this
# job's model earned, and the deterministic rules that fired. Optional,
# service-owned and computed in code, so the additive rule holds again — and it
# is the first block recording what *informed* the analysis rather than what
# the analysis found. It is not evidence and cannot become any: nothing here
# supports a threat, and the ``grounds`` that do are untouched.
SCHEMA_VERSION = "2.7"

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

# One value in a report's per-tier sampling clear block. Wide on purpose: it is
# every scalar type a resolved sampling param can hold, and the block is a
# *record* of what a run resolved rather than a place a value is decided. The
# enumeration is `TierSampling`'s field types — number, count, flag, and the
# reasoning effort's enum string — and `tests/test_report.py` pins the two in
# step, because a param whose type this cannot carry does not fail at load
# time: it fails when the report is assembled, after the whole graph has been
# paid for.
#
# Nothing is validated by being narrow here. The values arrive from a
# `TierSampling` that already validated them — range, enum and reserved-param
# rules all live there — and this module cannot import it without cycling
# through skills. Narrowing this union would not add a check; it would only
# decide which correctly-configured deployments can produce a report.
SamplingValue = bool | int | float | str | None

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

    Chosen by a category agent, constructed by the service. An agent names the
    fact it relied on — a catalog entry, or a span and the source it came from
    — and :func:`~stride_service.evidence.resolve_proposals` builds the record.
    Never derived from the affected elements' ``source_excerpt``s: a derived
    citation would always render *a* quote and frequently the wrong one. An
    excerpt says why an *element* exists; a ground says why a *threat* was
    raised.

    NO MODEL EVER GENERATES ONE, which is what makes a flat object safe here.
    The three branches carry different required fields, and that relationship
    is not expressible in a JSON schema a provider will reliably compile —
    ``oneOf`` has the thinnest, least uniform support across the vendors a
    category agent may be routed to, and ``config/sampling.toml`` records what
    an uncompilable grammar costs: one vendor rejects ``SystemModel``'s as "too
    large", and ``constrain_output = false`` is no answer, because
    unconstrained the model fences its JSON and omits required fields. A dead
    run, not a degraded one. Since the only writer is code holding a catalog
    entry, the constraint never has to reach a schema compiler at all, and the
    validator below is checking this module's own arithmetic rather than
    refereeing a model's guess.

    :class:`Verdict` is the tagged variant that *is* generated, and it is this
    same flat shape, so the repo has one answer to "tagged variant in a
    provider-facing schema" rather than two.

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


class ProposedVerdict(BaseModel):
    """The critic's ruling on one threat, as the critic emits it.

    The three fields and **no rule between them**, which is what makes this the
    shape a provider can be asked to generate: nothing a critic writes here is
    a shape error, so nothing it writes can fail the node on the way into
    state. Whether the combination is *coherent* — a ``needs-info`` that names
    what must be answered, a non-``confirmed`` that says why — is asked at the
    review seam, which can send it back.

    Every field's own constraint still applies. ``status`` is a closed
    vocabulary and a ``reason`` still has a maximum length; what moved is only
    the part that depends on another field's value.
    """

    model_config = ConfigDict(extra="forbid")

    status: VerdictStatus
    reason: str = Field(default="", max_length=1000)
    related_unknowns: list[UnknownRef] = Field(default_factory=list)


class Verdict(ProposedVerdict):
    """The critic's ruling on one threat, as the report carries it.

    ``needs-info`` must name the unknown attributes that caused it;
    ``needs-info`` and ``rejected`` must state a reason.

    THREE RULES BETWEEN FIELDS, AND NOT ONE OF THEM IS IN THE SCHEMA. Which
    fields a verdict must and must not carry depends on its own ``status``, and
    that dependency is not expressible in a JSON schema a provider will
    reliably compile — the same constraint :class:`Ground` documents at length.

    So this class is **not what the critic emits**. It emits
    :class:`ProposedVerdict`, which is these fields with no rule between them,
    and :func:`~stride_service.critic.review_issues` checks the three at the
    seam that owns "is this critic output well-formed" — where a failure routes
    to the bounded ``recritic`` re-ask. Raising here instead would kill the node
    on the way into state, taking the whole job with it, and the re-ask built
    for exactly this would never run.

    What survives here is the invariant on the *report*: a ``Verdict`` is
    constructed only by :func:`~stride_service.critic.assemble_threats`, out of
    a proposal the review seam has already passed, so the check below is this
    service auditing its own construction rather than refereeing a model's.
    """

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


def _check_category_letter(threat_id: str, category: StrideCategory) -> None:
    """Raise unless a threat ID carries its category's letter.

    Shared by the shape a category agent emits and the shape the service
    resolves it into, so the rule is stated once. A proposal that fails it is
    rejected at the node boundary, which is the earliest point either shape
    exists.
    """
    letter = CATEGORY_LETTERS[category]
    if not threat_id.startswith(f"{letter}-"):
        raise ValueError(
            f"threat ID {threat_id!r} does not carry the {category}"
            f" category letter {letter!r}"
        )


class DraftThreat(BaseModel):
    """One draft finding: the eight fields a category agent's answer becomes.

    Everything a category agent's proposal establishes and nothing it may rule
    on — ``verdict`` and ``confidence`` are the critic's, and appear only once a
    draft is promoted to a :class:`Threat`. This is the shape the prompt
    exemplars are lint-parsed against.

    **Built by the service, never emitted by an agent.** An agent answers in
    :class:`ThreatProposal`, which names its evidence rather than serializing
    it, and :func:`~stride_service.evidence.resolve_proposals` constructs this
    from that answer. So a ``grounds`` list here is code's own output: every
    entry either came out of the evidence catalog whole or is a quote assembled
    from the two fields an agent supplied, and neither route can express a
    mis-shaped :class:`Ground`.

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
        _check_category_letter(self.id, self.category)
        return self


class Threat(DraftThreat):
    """One STRIDE finding, traceable to the elements it affects.

    A draft plus the critic's two judgments; the category-letter rule is
    inherited, so a draft and the threat it becomes are checked identically.
    """

    confidence: Rating  # critic-calibrated grounding in model facts
    verdict: Verdict


class QuoteCandidate(BaseModel):
    """A span an agent claims is in one of the job's sources, and which source.

    The two fields a ``quote`` :class:`Ground` carries, and deliberately not
    that ground itself: an agent proposes the span, and
    :func:`~stride_service.evidence.resolve_proposals` builds the ground. Both
    fields are required here where the ground defaults them to the empty
    string, because a candidate exists only to be checked and neither half of
    the check has anything to run against alone — a labelless quote names no
    source to search, and a textless label cites nothing.

    The lengths are the ground's own, so a candidate that validates always
    resolves into a ground that validates.
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=1000)
    source_label: str = Field(min_length=1, max_length=200)


class ThreatProposal(BaseModel):
    """What a category agent actually emits: a finding that *names* its evidence.

    THE FIELD A MODEL NO LONGER SERIALIZES. A :class:`Ground` is a flat object
    whose legal field combination depends on its own ``kind``, and that
    relationship is unrepresentable in the JSON schema a provider compiles —
    the reasons are :class:`Ground`'s own docstring's. It was therefore carried
    by prompt instruction, which makes a mis-shaped ground an expected
    stochastic outcome rather than a defect: an agent that picked the right
    fact and spelled it into the wrong branch killed the node, and with it all
    six lanes, at a seam with no re-ask path.

    So the agent stops spelling it. ``evidence_refs`` holds IDs copied from the
    evidence catalog the service derived from the validated System Model, and
    ``quotes`` holds spans plus the source each came from. Both are flat lists
    of a single shape, which a schema compiler can express exactly; nothing an
    agent can put in either is a shape error, and a reference naming nothing
    fails deterministically against the catalog rather than probabilistically
    against a validator.

    What is given up: the branch is no longer the agent's to state. That is the
    point — the branch was always dictated by the trigger, so an agent choosing
    it was an agent given a mechanical job to get wrong. The catalog entry
    carries the branch, and picking the entry picks it.

    At least one entry across the two lists, which is ``grounds``'
    ``min_length=1`` expressed over the pair: a finding with no justification
    at all is the one thing neither list may say. Which list carries it is free
    — a threat triggered by a crossing or an unknown legitimately quotes
    nothing.
    """

    model_config = ConfigDict(extra="forbid")

    # A number, not an ID, and no category beside it. There is one node per
    # STRIDE category and ``analyze_instruction`` fills ``{category}`` at build
    # time, so the lane is the graph's fact: an agent restating it is an agent
    # given a constant to contradict, and the ID's letter is a pure function of
    # what it would be restating. :func:`~stride_service.evidence.resolve_proposals`
    # composes both from the lane it is resolving.
    #
    # An integer rather than a two-digit string, because a string reintroduces
    # what this removes: ``"1"`` against a ``^\d{2}$`` pattern is a spelling
    # error that fails the node, and a sequence has no spelling.
    sequence: int = Field(ge=1, le=99)
    # THE LENGTH AND CARDINALITY CAPS BELOW ARE FATAL, AND THAT IS ACCEPTED
    # RATHER THAN OVERLOOKED. Unlike the rules this class exists to remove, they
    # *are* expressible in a JSON schema — but providers enforce ``maxLength``
    # and ``minItems`` no more reliably than ``pattern``, so an agent can still
    # exceed one, and the raise lands at the node boundary where it costs the
    # lane and its five siblings.
    #
    # Neither remedy that worked elsewhere applies. There is no "select rather
    # than construct" form of an over-long description, so it cannot be made
    # unrepresentable; and the analyst path has no re-ask to relocate the check
    # to — ``repair`` is extraction-only and ``recritic`` critic-only — so it
    # cannot be made recoverable either. What is left is to truncate, which
    # infers what the agent meant and is refused on principle, or to size the
    # caps so the ceiling is not one a model reaches.
    #
    # Sized against measured output: the 18 exemplar descriptions run 524-811
    # characters, median 702, against 4000. Roughly 5x headroom, and the risk
    # is a filed decision rather than an accident of where a constraint sits.
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    affected_element_ids: list[str] = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)
    quotes: list[QuoteCandidate] = Field(default_factory=list)
    severity: Severity
    mitigations: list[Mitigation] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_shape(self) -> Self:
        if not self.evidence_refs and not self.quotes:
            raise ValueError(
                f"the draft numbered {self.sequence} justifies itself with"
                " nothing: name at least one evidence reference or quote"
            )
        return self


class ThreatProposals(BaseModel):
    """What a category agent node emits: an object wrapping its list of proposals.

    The wrapper exists for the *schema*, not for the domain. A node's
    ``output_schema`` is what the graph asks the provider to constrain
    generation to, and a bare ``list[ThreatProposal]`` cannot be asked for: ADK
    cannot convert a generic alias into a response format, so it sends none and
    the node generates unconstrained — silently, with only a log line. Wrapping
    the list in a model gives the conversion something it can carry, and an
    object root at that, which is what OpenAI's structured outputs require and
    a bare array would not satisfy.

    Nothing downstream sees it: the graph unwraps at the node boundary, so the
    domain keeps working in lists.

    ``threats`` rather than ``proposals`` because the field name is the agent's
    output contract and the prompt has always spelled it that way. What changed
    is the shape of an entry, not what an agent is being asked for.
    """

    model_config = ConfigDict(extra="forbid")

    threats: list[ThreatProposal]


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

    # NO PATTERN, DELIBERATELY, AND NOTHING IS GIVEN UP BY DROPPING IT. The
    # critic does not compose this ID — it copies one off the roster it was
    # handed — and :func:`~stride_service.critic.review_issues` already requires
    # the ruled set to equal the drafted set exactly. That is a *stronger*
    # constraint than any pattern: an ID matching a drafted one is well-formed
    # by construction, because the draft's own ``id`` carries the pattern.
    #
    # So a pattern here could only ever fire on an ID the reconciliation was
    # about to reject anyway — and it fired earlier and fatally, at the node
    # boundary, where a raise kills the critic's single pass over every draft
    # and the bounded re-ask never runs. Without it, ``"S-1"`` arrives as two
    # precise re-askable problems: one draft dropped, one threat returned that
    # nobody drafted.
    #
    # The length bound stays because an unbounded string from a model is worth
    # capping on principle (OWASP LLM10), and 300 is the same number every other
    # ID field here carries. It is not the well-formedness check and cannot be
    # read as one — a real ID is four characters.
    id: str = Field(max_length=300)
    confidence: Rating
    # The unruled shape: a verdict whose fields disagree with each other is a
    # problem for the review seam to report and the re-ask to fix, not a reason
    # to fail the node. assemble_threats builds the canonical Verdict.
    verdict: ProposedVerdict
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
    per node *execution*, so a build that moves partway through an eval sweep
    gives one node two hashes. A deterministic FunctionNode carries none of the
    three.

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


class AnalysisContext(BaseModel):
    """What informed the analysis, as distinct from what proves a finding.

    The report already records the two ends of a run: the served build and
    sampling each node ran on (:class:`NodeRun`), and the facts each finding
    rests on (``grounds``). Between them sits everything the service *put in
    front of* the category agents — the instruction text they were given, the
    reference packs this model earned, the deterministic rules that fired — and
    none of it was recorded anywhere. Two runs of the same model on the same
    input could differ because a pack selection flipped or a skill was edited,
    and the report showed nothing.

    **This is context, never evidence, and the separation is the whole design.**
    A pack named here did not ground anything; a rule named here did not find
    anything. What grounds a finding is its ``grounds``, unchanged. This block
    answers a different question — *what was on the desk* — and a reader who
    treated an entry here as support for a threat would be reading it exactly
    backwards.

    ``instruction_sha256`` digests the *composed instruction* of every LLM node
    in the built graph, with the job-varying placeholders still unexpanded. So
    it identifies the repo-authored text — prompts, category skills, the shared
    rubric — and carries no submitter bytes at all, which is what makes it
    publishable beside a report. The generation-identity fingerprint attests to
    the model and the decoding params; it says nothing about the instructions,
    so two runs with identical fingerprints and completely different prompts
    are indistinguishable without this.

    ``domain_packs`` is a fact about *this job's* model rather than the
    deployment: selection is per-job (:mod:`stride_service.domains`), so the
    same service gives two submissions different reference material, and the
    names are the only record of which.

    ``fired_rules`` names the deterministic triggers that matched, where
    ``coverage`` counts them. The count says how much attention was directed;
    the IDs say where — which is what an eval measuring candidate usefulness
    needs, and what a reader asking "why did the agent look there" reads.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    instruction_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    domain_packs: list[str] = Field(default_factory=list)
    fired_rules: list[str] = Field(default_factory=list)


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


class SharedElementName(BaseModel):
    """Elements of different types whose names normalize to one slug.

    ``extract.md`` tells the transcriber "nothing gets two types", and the
    failure it has in mind is one real thing recorded twice — a `process:` and
    a `store:` for the same web app. The validity gate cannot catch it: an ID
    carries its type, so the two IDs differ and ``duplicate-id`` compares whole
    IDs.

    **THE GATE HAS ONE SEVERITY, AND THIS IS NOT IT.** Every
    :class:`~stride_service.validation.ValidationIssue` is fatal — "returns an
    empty list" *is* the definition of ready-for-analysis that
    :func:`~stride_service.graph.validate_extraction` routes on — and a shared
    name does not deserve that, because it is not always wrong. A system can
    legitimately run a process and keep a store of the same name, and failing
    extraction on the pair would reject a valid model *and* spend ``repair``'s
    single pass telling a transcriber to rename something correct. So the
    finding lands here instead, beside the other service-owned marks, and the
    gate is left with the one severity it has.

    A mark for a human, and pointedly not for ``repair``: that prompt is told
    to change nothing the issues do not cite, and a maybe is the worst thing to
    spend its one pass on. This is the first mark about the *model* rather than
    about the threats — :class:`UnverifiedGround`, :class:`UnresolvedMention`
    and :class:`MissingMitigation` all annotate findings — which is why it
    holds element IDs rather than a ``threat_id``.

    Service-owned and computed in code from the embedded model
    (:meth:`~stride_service.system_model.SystemModel.shared_names`), so a
    reader can check it against the very model the report carries.
    """

    model_config = ConfigDict(extra="forbid")

    name_slug: str = Field(min_length=1, max_length=200)
    # Two or more, always: a slug held by one element is not a collision, and
    # the grouping never emits a singleton.
    element_ids: list[str] = Field(min_length=2)


class Summary(BaseModel):
    """Counts the front-end can render without walking the threat list."""

    model_config = ConfigDict(extra="forbid")

    threat_count: int = Field(ge=0)
    by_category: dict[StrideCategory, int] = Field(default_factory=dict)
    by_severity: dict[SeverityLevel, int] = Field(default_factory=dict)
    needs_info_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    elements_analyzed: int = Field(ge=0)


class CategoryCoverage(BaseModel):
    """What one category agent was offered, and how much of it its drafts cite.

    The question this answers is the one a bare threat count cannot: whether
    "no threat here" means *examined and cleared* or *never looked at*. Every
    number is computed in code from the System Model, the deterministic
    candidate triggers and the agent's own drafts — none of it is asserted by
    a model, which is the whole reason it is worth recording.

    **Read ``*_cited`` as cited, not as considered.** An agent that examined a
    flow and correctly concluded there was no threat cites nothing, and is
    indistinguishable here from one that never read it. The fields are named
    for what they measure rather than for what a reader would like them to
    mean; the honest use is the aggregate — a category citing two of forty
    structural leads across a corpus is a coverage signal, one agent's zero on
    one case is not.

    ``elements`` is the whole model rather than the subset a category's
    ``## Applicability`` scopes: applicability lives in the skill text, and a
    second copy of it in code would be a second definition to drift.
    """

    model_config = ConfigDict(extra="forbid")

    category: StrideCategory
    drafts: int = Field(ge=0)
    rules: int = Field(ge=0)  # rules in this lane
    rules_fired: int = Field(ge=0)  # of those, how many produced a candidate
    candidates: int = Field(ge=0)
    candidates_cited: int = Field(ge=0)
    elements: int = Field(ge=0)
    elements_cited: int = Field(ge=0)
    boundary_crossings: int = Field(ge=0)
    boundary_crossings_cited: int = Field(ge=0)
    unknown_controls: int = Field(ge=0)
    unknown_controls_cited: int = Field(ge=0)


def build_summary(
    threats: list[Threat],
    rejected_threats: list[Threat],
    system_model: SystemModel,
) -> Summary:
    """Compute the summary block mechanically from the report's own contents."""
    by_category = Counter(threat.category for threat in threats)
    by_severity = Counter(
        derive_severity_level(threat.severity.likelihood, threat.severity.impact)
        for threat in threats
    )
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
    bare ``nodes`` list does not answer it: a sweep hands over every case's
    executions at once, so one node contributes one record per case, and
    reading a per-node total off the list means every caller writing the same
    fold. Keyed by node name rather than by tier
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


class NodeLatency(BaseModel):
    """What one node's executions cost in wall-clock, folded across a sweep.

    The counterpart to :class:`TokenUsage`'s fold, and it differs in one way
    that matters: **every** execution carries a ``duration_ms``, so a
    deterministic FunctionNode appears here where it is absent from the usage
    totals. That is the point — the deterministic derivations are the half of
    the graph nobody bills for, and the only way to know they stay cheap is to
    see them beside the nodes that do cost money.

    ``slowest_ms`` is kept alongside the total because the mean is the wrong
    number for the question latency is usually asked for: a retry budget and a
    request timeout are both set from the tail, and a node whose mean is
    comfortable can still be the one that trips them.
    """

    model_config = ConfigDict(extra="forbid")

    executions: int = Field(ge=1)
    total_ms: int = Field(ge=0)
    slowest_ms: int = Field(ge=0)

    @property
    def mean_ms(self) -> float:
        return self.total_ms / self.executions


def latency_by_node(nodes: Iterable[NodeRun]) -> dict[str, NodeLatency]:
    """Wall-clock per node name, folded across that node's executions.

    Same fold as :func:`usage_by_node`, over the measurement the report keeps
    and nothing downstream has ever read back: a sweep's node runs carry
    ``duration_ms`` per execution, and without this the whole latency record
    dies with the process that measured it.

    A node absent from the result ran zero times in the sweep, which is a
    different fact from running fast — hence ``executions`` on every row.
    """
    totals: dict[str, NodeLatency] = {}
    for node in nodes:
        running = totals.get(node.node)
        if running is None:
            totals[node.node] = NodeLatency(
                executions=1,
                total_ms=node.duration_ms,
                slowest_ms=node.duration_ms,
            )
            continue
        totals[node.node] = NodeLatency(
            executions=running.executions + 1,
            total_ms=running.total_ms + node.duration_ms,
            slowest_ms=max(running.slowest_ms, node.duration_ms),
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
    sampling: dict[str, dict[str, SamplingValue]] = Field(default_factory=dict)
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
    # Elements of different types sharing one name slug — a suspicion about the
    # model rather than a fault in it, which is why it rides here and not in
    # the validity gate. Recomputable from ``system_model`` by design.
    shared_element_names: list[SharedElementName] = Field(default_factory=list)
    # Per-category coverage accounting, computed at the fan-in over the drafts
    # the critic was handed — before any verdict, because coverage is a fact
    # about what the six agents did with the system, not about what survived
    # review. Empty on a report built without it (the stub runner's).
    coverage: list[CategoryCoverage] = Field(default_factory=list)
    # What was in front of the agents: the instruction text, the reference
    # packs this model earned, the rules that fired. Context, not evidence —
    # see :class:`AnalysisContext`. ``None`` on a report built without it (the
    # stub runner's), which is the same absence an empty ``coverage`` records
    # rather than a claim that nothing informed the run.
    analysis_context: AnalysisContext | None = None
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
