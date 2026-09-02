"""The report: the structured JSON payload the front-end retrieves for a job.

There is one envelope and many frameworks. A :class:`Report` carries the facts
about one job — its identity, its inputs, the nodes that ran, and the single
**Valid System Model** every framework analysed — plus one
:class:`FrameworkAnalysis` block per framework the job selected. A field sits
where the thing it describes sits. Nine fields describe the job or the shared
model and stay on the envelope, and everything one framework produced rides in
that framework's own block.

The neutral shape every block's checks read is :class:`Claim`. It holds an ID,
the ``(framework, version)`` pair that names what the conclusion is of, a title
and description, the elements it affects, and the **Grounds** that justify it.
It carries no judgement. A severity, a mitigation and a ruling all belong to the
framework that makes them, and STRIDE's live on
:class:`~analysis_service.frameworks.stride.record.Threat`.

Severity is qualitative likelihood times impact. A fixed matrix derives the
band, and a model never asserts it: the critic calibrates two narrow judgements,
and evals check the arithmetic. Rejected claims ride in their own
``rejected_claims`` array, as an audit trail.

The report embeds the full validated System Model plus derived boundary
crossings once, so it is self-contained: every element reference in every block
resolves inside one payload.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from datetime import datetime
from types import MappingProxyType
from typing import Annotated, Any, ClassVar, Literal, Self, get_args

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    SerializeAsAny,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema

from analysis_service.actions import ActionVerb
from analysis_service.sources import Source
from analysis_service.system_model import BoundaryCrossing, SystemModel

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
#
# 2.8 adds ``knowledge_docs`` to that same block: the local-corpus documents
# the fired rules retrieved for the agents. Additive and service-owned like the
# rest of the block, and under the same rule — a document informed the
# analysis, and no consumer may read one as support for a threat.
#
# 2.9 adds ``unresolved_evidence``, a sixth optional list of service-owned
# marks: evidence references a threat cited that its job's catalog did not
# hold. Additive by the same rule as the four mark lists before it.
#
# What moved beside it is a *behaviour*, not a field. Such a reference used to
# fail the whole job; it is now dropped and marked, and only a threat left with
# no grounds at all still fails (#138). No existing field changes meaning or
# spelling, so this stays minor — but a consumer that treated a returned report
# as "every citation resolved" was relying on an absence rather than on a
# field, and this list is where that guarantee now lives.
# 2.10 corrects what ``coverage[].elements_cited`` counts, and holds every
# ``*_cited`` half to the total beside it. The field's *definition* is unchanged
# — the docs always read it as "of the elements in the model, how many the
# drafts cite" — but the computation counted prose citations raw, so an ID a
# description named that the model does not contain was counted as a cited
# element. That put the numerator above its denominator, and it did so hardest
# on the runs ``unresolved_mentions`` exists to flag.
#
# The second entry here that is a fix rather than an addition, and unlike 2.6 a
# value this schema *did* emit is now refused: a stored row with more cited than
# offered no longer re-validates. Minor by the rule above, because no field is
# added, removed or renamed and none changes meaning — what changes is that the
# number finally means what the field always said it did. A consumer that
# computed a citation rate off an affected row read a rate over 1.0 and will now
# read a smaller, correct one.
#
# 3.0 is the framework cutover, and it is major on every count the rule names:
# fields move, a field changes its spelling, and one changes what it carries.
# ``threats`` and seven other top-level fields become ``analyses[].claims`` and
# their per-framework siblings; the four mark classes rename ``threat_id`` to
# ``claim_id``; ``coverage[].category`` becomes ``coverage[].lane``; and every
# claim gains the required ``(framework, framework_version)`` pair.
#
# **There is no version gate and none is needed.** ``Report`` keeps
# ``extra="forbid"``, so a 2.10 payload carrying ``threats`` at the top level is
# refused by this model, and a 3.0 payload carrying ``analyses`` is refused by
# the old one. The no-shim behaviour falls out of the shapes rather than out of
# anything reading ``schema_version``.
#
# 3.0 also carries a fourth ``GroundKind``, ``absent-attribute`` (#171), which
# would have earned a major bump of its own had it arrived separately: a
# consumer switching over the three kinds it knew now meets a fourth. It rides
# this one instead because 3.0 has never shipped, and two hard cutovers for one
# release is a cost paid twice for nothing.
# 3.0 also carries ``nodes[].attempts``, the provider-call count the retry
# driver stamps on each LLM node. Optional with a default of 1, and it rides
# the unshipped major for the same reason.
# 3.0 also carries ``unknown_claim_identities``, a seventh list of
# service-owned marks: a claim naming an identifier its framework's own catalog
# does not hold. It rides 3.0 for the same reason ``absent-attribute`` does —
# 3.0 has never shipped — and it would have been additive and minor on its own.
#
# What moves beside it is again a *behaviour*. Such a claim used to reach the
# report and cite the standard's version-safe reference format for a
# requirement the standard does not contain; it is now dropped and marked, on
# the rule 2.9 already set for a citation that resolves to nothing. Only a
# framework carrying a catalog it did not author can produce one, so the list
# is empty for STRIDE by construction rather than by accident.
#
# 3.0 also carries ``dropped_claims``, an eighth list of service-owned
# marks: a claim that lost every ground it cited, at evidence resolution or at
# the quote check. It rides 3.0 for the same reason the two before it do.
#
# What moves beside it is every whole-job failure one entry of one claim could
# cause: a proposal that fails its own schema, a claim that lost every ground,
# every element it named, or its ID to an earlier draft. Each is now dropped
# and marked with its reason. ``unresolved_references``, a tenth list, records
# an element ID dropped from ``affected_element_ids`` the way
# ``unresolved_mentions`` records one dropped from prose.
#
# 3.0 also carries ``repaired_quotes``, a ninth list: a quote ground the
# ladder refused and the service rewrote to the source's own nearest span. The
# ground carries the submitter's words after that, and the mark carries the
# agent's, so a consumer reading a quote as "what the agent wrote" must read
# this list too.
# 3.0 also carries ``unreconciled_rulings``, an eleventh list: how the *first*
# critic pass failed to reconcile with its drafts, before the bounded re-ask
# repaired it.
#
# 3.0 also carries a fifth ``GroundKind``, ``absent-element``: a term no element
# of the model names, which is the only branch whose referent is the whole model
# rather than a part of it. A consumer switching over the four kinds it knew now
# meets a fifth whose ``element_id`` and ``flow_id`` are both empty. It rides 3.0
# for the reason ``absent-attribute`` does — 3.0 has never shipped — and it would
# have been major on its own. The reason is a property rather than a package's
# name: **a framework may need to justify a claim by the absence of a thing from
# the model, and every other branch can only name something present.**
#
# 3.0 also carries ``rejected_because`` on a verdict: which of the critic's
# three checks — the draft's own substance, the lane it was filed in, or another
# draft already covering it — ended a rejected draft. A consumer reading the
# rejected array as an audit trail had to parse the reason prose for this and can
# now read a field. It rides 3.0 because 3.0 has never shipped, and it is
# additive: ``None`` is the honest answer for a rejection recorded before the
# field, so a report written then still validates and still reads.
#
# A ``needs-info`` verdict names what has to be answered in one of two
# spellings: an element and one of its attributes, or a ``subject`` — a question
# with no place in the System Model at all. A consumer switching on the element
# pair alone meets an entry where both halves are empty and ``subject`` carries
# the whole of the question, which is why the second spelling is a 3.0 change
# rather than a minor one. The reason is a property rather than a package's
# name: **a framework may need a fact the system model has no slot for.**
SCHEMA_VERSION = "3.0"

# The envelope's disclaimer, which is about the *service* rather than about any
# one framework. It no longer says "threat model": that is false of a report
# whose blocks include a framework that rules on requirement applicability
# rather than on attacks, and a sentence that is false of half a payload is
# worse than a general one. Each package carries its own, from
# ``frameworks/<name>/disclaimer.md``, saying what its own claims assert.
DEFAULT_DISCLAIMER = (
    "AI-generated security analysis. Not reviewed by a human security analyst."
)

# What this repo can spell. Three sets must agree: this ``Literal`` names what
# the code can name, :data:`~analysis_service.frameworks.PACKAGES` names what this
# repo carries, and ``config/frameworks.toml`` names what this install runs. The
# first two agree at import; the third agrees at the gate.
#
# Two members. The names are alphabetical, which is the rule the vendor registry
# already follows wherever a reader could infer a ranking: this repo carries no
# default framework and no primary one.
FrameworkName = Literal["asvs", "stride"]

FRAMEWORK_NAMES: tuple[FrameworkName, ...] = get_args(FrameworkName)

# Severity vocabulary. **Shared value types, whose placement is a package's
# choice.** #163 kept the severity *field* off :class:`Claim` because a
# framework that grades nothing has no use for it; the types themselves are the
# service's, so two packages that do grade harm spell a band the same way and
# the matrix arithmetic is written once. A package declares the field, and the
# gate then requires the ``severity_rubric.md`` that explains how it is read.
Rating = Literal["low", "medium", "high"]
SeverityLevel = Literal["low", "medium", "high", "critical"]
VerdictStatus = Literal["confirmed", "needs-info", "rejected"]

# Which of the critic's three checks killed a rejected draft. The rejected array
# is an audit trail, and a reader has to be able to tell which step ended a
# draft; the reason says it in prose, and this says it in a field the code reads.
#
# **The steps, not a package's reasons.** ``prompts/critic.md`` numbers three
# checks and every package's own critic text names the same three in its own
# words, because the property is neutral: a draft can fail on its own substance,
# on where it was filed, or on another draft already covering it. A framework
# that ruled a unit out of scope and one that rejected an attacker action both
# answer ``evidence``; the vocabulary describes the check rather than what the
# check was about, so it answers for a package nobody has written.
RejectionStep = Literal["evidence", "lane", "duplicate"]

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
# attribute". ``absent-attribute`` follows that spelling for the same reason and
# for one more: the two attribute branches carry identical fields, so the kind
# is the only place their difference can live.
GroundKind = Literal[
    "quote",
    "unknown-attribute",
    "absent-attribute",
    "derived-fact",
    "absent-element",
]

# How long a claim ID may be. **Not a grammar**: #163 ruled that ``id`` has no
# shared one, because each package composes its own from its own ``id_format``
# and per-lane prefix. STRIDE's ``S-01`` and an ASVS requirement ID are both
# legal here and neither is spelled by this module.
#
# What survives is the bound, for the reason every ID field here carries one: a
# string a package composed from a model's own value is still worth capping
# (OWASP LLM10). Uniqueness within a block is the real check, and it lives on
# :class:`FrameworkAnalysis`.
CLAIM_ID_MAX_CHARS = 300

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
    has that field — and the job died at :func:`~analysis_service.critic.
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
    """What a ``needs-info`` verdict says has to be answered.

    Two spellings in one flat shape, which is the answer :class:`Ground` and
    :class:`Verdict` already give to "tagged variant in a provider-facing
    schema". A union here would compile to ``oneOf``, and ``Ground`` records at
    length what an uncompilable grammar costs.

    * **An unknown in the model** — ``element_id`` with ``attribute``. The fact
      has a place in the **System Model**, so naming it tells the submitter
      which field to fill in.
    * **A question with no place in the model** — ``subject``. Not every fact a
      framework needs is a property of a system's structure. Whether a
      documented authorization policy exists, or whether queries are
      parameterized, is a question about a codebase rather than about an
      element, and no representation of a running system holds the answer.

    **Why the second spelling exists.** A framework whose claims rule on
    requirements answers the second kind by construction — ASVS's own output
    contract says most of its requirements "address a coding practice with no
    position in the System Model". A question of that kind pointed at whichever
    attribute resolves on whichever element is nearest passes the check and
    tells a reader nothing, which is worse than a refusal.

    Stated as a property rather than as a package's name, because it holds for
    a framework nobody has written yet: **a framework may need a fact the
    system model has no slot for.**

    Nothing here is a shape error, deliberately, exactly as
    :class:`ProposedVerdict` carries no rule between its own fields. Which
    spelling a ruling used, and whether it says anything, is asked at the review
    seam — where a failure routes to the bounded re-ask rather than killing the
    node.
    """

    model_config = ConfigDict(extra="forbid")

    element_id: str = Field(default="", max_length=300)
    attribute: AttributeName = Field(default="", max_length=100)
    subject: str = Field(default="", max_length=300)

    @property
    def names_an_element(self) -> bool:
        """Is this the model-reference spelling? Asked in three places."""
        return bool(self.element_id or self.attribute)


# How long an absent-element term may be. Exported because the producer must
# check it: ``absent_elements`` is a free list the model fills, and the prompt
# asks for one lowercase term, so an agent writing a sentence instead would fail
# validation here and cost the job the whole report. One name, so the bound the
# producer tests and the bound this field accepts cannot drift apart.
GROUND_TERM_MAX_CHARS = 100


# How long the lists a model fills may get (OWASP LLM10). Every scalar a lane
# emits already carries a bound; these are the counts, and without them the only
# limit on an emission is the tier's output ceiling — tens of thousands of
# tokens, every one of which the service then does deterministic work over. The
# grounding rung is the expensive one: it searches the submitted source once per
# quote a claim carries, so the product of these two numbers is the work one
# lane can buy.
#
# The figures are the observed maxima across the 77 corpus and sweep artifacts
# in the tree, with headroom: 84 claims in a batch, 13 grounds on a claim and 14
# affected elements. They bound an emission that is not a lane's work at all,
# and none of them constrains what the service has actually produced.
MAX_CLAIMS_PER_BATCH = 400
MAX_GROUNDS_PER_CLAIM = 60
MAX_QUOTES_PER_PROPOSAL = 20
MAX_REFS_PER_PROPOSAL = 20
MAX_ABSENT_PER_PROPOSAL = 20
MAX_ELEMENTS_PER_PROPOSAL = 60


class Ground(BaseModel):
    """What justifies one finding: a quote, an attribute's state, or a derived fact.

    Chosen by a category agent, constructed by the service. An agent names the
    fact it relied on — a catalog entry, or a span and the source it came from
    — and :func:`~analysis_service.evidence.resolve_proposals` builds the record.
    Never derived from the affected elements' ``source_excerpt``s: a derived
    citation would always render *a* quote and frequently the wrong one. An
    excerpt says why an *element* exists; a ground says why a *threat* was
    raised.

    NO MODEL EVER GENERATES ONE, which is what makes a flat object safe here.
    The five branches carry different required fields, and that relationship
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
      bytes by :mod:`analysis_service.grounding`.
    * ``unknown-attribute`` — ``element_id`` + ``attribute``, spelled
      identically to :class:`UnknownRef`'s two fields so the shared referent is
      obvious, but a **separate type**. A ground is *backward-looking* — the
      unknown that made the agent raise the threat. ``related_unknowns`` is
      *forward-looking* — the unknown the critic says must be answered before
      the threat can be ruled on. Different author, different moment, different
      job; when they coincide that is signal, not redundancy.
    * ``absent-attribute`` — the same two fields, for the attribute whose value
      *states its own absence*: ``authentication: "none; accepted by network
      position"`` is a control the submitter said is not there. **The fields are
      identical and the fact is not**, which is the whole reason this is a kind
      rather than a flag on the one above. An unknown is a question — it makes a
      threat conditional and routes it to needs-info — while a stated absence is
      an answer, and a threat resting on one is confirmed rather than pending.
      A consumer that folded the two would report a control the input described
      as missing as a gap in the *description*. Both branches say only what the
      model carries; neither says the control is inadequate, which stays the
      agent's argument and the critic's to rule on.
    * ``absent-element`` — ``term`` alone: a word a submitter writes for a
      thing, which no element's free text names. **The only branch whose
      referent is the whole model rather than a part of it**, and the one a
      framework needs to justify a claim about what a system does not have. A
      requirement about LDAP injection does not apply to a system that never
      mentions a directory service, and the fact that rules it out is the
      absence itself; the four branches above can each only name something
      present, so a claim about an absence had no honest ground to cite.
      Verified like a quote rather than trusted like prose: the service checks
      the term against every element's text
      (:func:`~analysis_service.analysis.names_term`) and drops the ground where
      the model does name it, so the branch cannot assert an absence that is
      not there. What it may not do is prove the *system* lacks the thing —
      only that the description never mentions it, which is all this service
      ever has.
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
    element_id: str = Field(default="", max_length=300)  # both attribute branches
    attribute: AttributeName = Field(
        default="", max_length=100
    )  # both attribute branches
    flow_id: str = Field(default="", max_length=300)  # derived-fact
    term: str = Field(default="", max_length=GROUND_TERM_MAX_CHARS)  # absent-element

    # Which fields each branch requires. Everything not listed for a branch is
    # forbidden on it — a quote carrying an element_id is a shape error, not a
    # tolerated extra, because the two readings of such a record differ and
    # nothing downstream could choose between them. The two attribute branches
    # share a row's worth of fields, so neither forbids the other's: what
    # separates them is the kind, and there is nothing else to check.
    _REQUIRED: ClassVar[dict[GroundKind, tuple[str, ...]]] = {
        "quote": ("text", "source_label"),
        "unknown-attribute": ("element_id", "attribute"),
        "absent-attribute": ("element_id", "attribute"),
        "derived-fact": ("flow_id",),
        "absent-element": ("term",),
    }

    @property
    def place(self) -> str:
        """The model element this ground points at, or ``""`` where it points at none.

        Two branches name no place and they name none for different reasons. A
        ``quote`` is a span of the submitter's words, which belongs to a source
        rather than to an element. An ``absent-element`` is about the model as a
        whole, and its ``term`` is a word rather than an ID — there is no element
        to point at, which is the fact it exists to state.

        Read wherever a caller needs "which part of the model is this about",
        so a sixth branch answers here once instead of in each caller's own
        ``or`` chain.
        """
        return self.element_id or self.flow_id

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


# The bound on a verdict's ``reason``, named because code writes one too.
# :meth:`Claim.settled_by_grounds` composes a sentence from a draft's unknown
# grounds, and a draft can cite more pairs than a sentence of this length names.
# A writer that cannot read the bound writes a string the schema refuses, and
# that fails the node rather than the claim.
REASON_MAX_CHARS = 1000


class ProposedVerdict(BaseModel):
    """The critic's ruling on one threat, as the critic emits it.

    The four fields and **no rule between them**, which is what makes this the
    shape a provider can be asked to generate: nothing a critic writes here is
    a shape error, so nothing it writes can fail the node on the way into
    state. Whether the combination is *coherent* — a ``needs-info`` that names
    what must be answered, a non-``confirmed`` that says why, a ``rejected``
    that names the check it failed — is asked at the review seam, which can
    send it back.

    Every field's own constraint still applies. ``status`` is a closed
    vocabulary and a ``reason`` still has a maximum length; what moved is only
    the part that depends on another field's value.
    """

    model_config = ConfigDict(extra="forbid")

    status: VerdictStatus
    reason: str = Field(default="", max_length=REASON_MAX_CHARS)
    related_unknowns: list[UnknownRef] = Field(default_factory=list)
    rejected_because: RejectionStep | None = None


class Verdict(ProposedVerdict):
    """The critic's ruling on one threat, as the report carries it.

    ``needs-info`` must name the unknown attributes that caused it;
    ``needs-info`` and ``rejected`` must state a reason.

    **``rejected_because`` is required of the critic and not of this record**,
    and the asymmetry is deliberate. :func:`~analysis_service.critic.review_issues`
    refuses a rejection that names no check, so nothing this service builds ever
    lacks one. A report *read back* is a different thing: one written before the
    field existed carries no answer, and ``None`` says so truthfully. Requiring
    it here would assert that every rejection ever recorded named its check,
    which is false, and the only way to make it true is to invent a
    classification the critic never made. So the archive stays readable and the
    live path stays checked.

    FOUR RULES BETWEEN FIELDS, AND NOT ONE OF THEM IS IN THE SCHEMA. Which
    fields a verdict must and must not carry depends on its own ``status``, and
    that dependency is not expressible in a JSON schema a provider will
    reliably compile — the same constraint :class:`Ground` documents at length.

    So this class is **not what the critic emits**. It emits
    :class:`ProposedVerdict`, which is these fields with no rule between them,
    and :func:`~analysis_service.critic.review_issues` checks the three at the
    seam that owns "is this critic output well-formed" — where a failure routes
    to the bounded ``recritic`` re-ask. Raising here instead would kill the node
    on the way into state, taking the whole job with it, and the re-ask built
    for exactly this would never run.

    What survives here is the invariant on the *report*: a ``Verdict`` is
    constructed only by :func:`~analysis_service.critic.assemble_claims`, out of
    a proposal the review seam has already passed, so the check below is this
    service auditing its own construction rather than refereeing a model's.
    """

    @model_validator(mode="after")
    def _check_shape(self) -> Self:
        if self.status == "needs-info" and not self.related_unknowns:
            raise ValueError(
                "a needs-info verdict must say what has to be answered, in at"
                " least one related_unknowns entry"
            )
        if self.status != "needs-info" and self.related_unknowns:
            raise ValueError(
                f"related_unknowns is only meaningful for needs-info verdicts,"
                f" not {self.status!r}"
            )
        if self.status != "confirmed" and not self.reason:
            raise ValueError(f"a {self.status} verdict must state a reason")
        if self.status != "rejected" and self.rejected_because is not None:
            raise ValueError(
                f"rejected_because is only meaningful for rejected verdicts,"
                f" not {self.status!r}"
            )
        return self


class Claim(BaseModel):
    """One framework's conclusion about the **Valid System Model**.

    THE ONLY SHAPE THE REPORT'S NEUTRAL CHECKS READ. Seven fields, and the test
    that produced exactly these seven: three rules on the report read nothing
    framework-specific — element IDs must resolve in the embedded model, claim
    IDs must be unique within their block, and every mark must name a claim its
    block carries. Those checks have to run over a second framework's output, so
    what they read is what the base holds. A field they do not read has no claim
    on the shared shape.

    **Chosen by an agent, constructed by the service.** One resolver builds
    every claim of every framework from an agent's selections
    (:func:`~analysis_service.evidence.resolve_proposals`), which is what keeps
    "code and not an agent builds a :class:`Ground`" a construction rather than
    a convention each package could break.

    It carries **no judgement**. A severity, a mitigation and a ruling all
    belong to the framework that makes them: STRIDE's live on
    :class:`~analysis_service.frameworks.stride.record.DraftThreat`, and a
    framework that grades nothing declares none of them.

    ``(framework, framework_version)`` is one pair and both halves are required.
    A framework identifier with no version is uninterpretable one release later
    — ASVS 5.0.0 kept 11 of 4.0.3's 286 requirements and renumbered every
    survivor, and its own citation format is ``v5.0.0-1.2.5`` for exactly that
    reason. An optional field was rejected because an empty version cannot be
    told apart from "not versioned", which is the failure the pair exists to
    prevent. STRIDE is not a published standard, so its version names **this
    repo's own ruleset** rather than anyone else's release.

    ``affected_element_ids`` **may be empty here**, where STRIDE narrows it to
    ``min_length=1`` in its own record. A framework whose requirements address a
    coding practice rather than anything in the graph has nothing legal to put
    there, and the alternative — a whole-application pseudo-element — buys a
    legal value by putting a lie in the model. The referential check still runs
    over whatever IDs are present.

    ``grounds`` is ``min_length=1`` and **no framework opts out**: a framework
    exempt from finding-level attribution would gut ADR 0002. It has no maximum,
    exactly like ``affected_element_ids``: this model caps no list, and runaway
    output stays governed where it already is, at the tier's
    ``max_output_tokens``.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=CLAIM_ID_MAX_CHARS)
    framework: FrameworkName
    framework_version: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    affected_element_ids: list[str] = Field(default_factory=list)
    # What the attacker does, from :mod:`analysis_service.actions`. Optional here
    # and narrowed by the packages that need it, exactly as
    # ``affected_element_ids`` is: a package whose claims carry a catalog
    # identifier already has an identity and composes none, so demanding a verb
    # of it would be demanding a field nothing reads. A package with an open
    # claim set narrows this to required, because for those claims the action is
    # half of what makes two of them the same finding.
    verb: ActionVerb | None = None
    grounds: list[Ground] = Field(min_length=1, max_length=MAX_GROUNDS_PER_CLAIM)

    @classmethod
    def claim_marks(cls, drafts: Sequence[Claim]) -> AnalysisMarks:
        """The marks this framework's *own* judgement fields earn. None, here.

        The neutral marks are the service's and are produced at the seam that
        can see them: a quote absent from the source it named, a prose citation
        naming no element, a reference resolving to nothing. This hook is for the
        rest — a mark about a field only one framework declares.

        STRIDE's is the only one today: a threat offering no countermeasure and
        no reason for offering none. ``mitigations`` is
        :class:`~analysis_service.frameworks.stride.record.DraftThreat`'s, and a
        framework that recommends nothing has no such mark to carry — which is
        also why ``missing_mitigations`` sits on STRIDE's block rather than on
        :class:`FrameworkAnalysis`.

        Same standing as every other mark: recorded beside the findings, never
        fatal, and never something an agent asserts about its own accuracy.
        """
        del drafts
        return AnalysisMarks()

    @classmethod
    def partition_proposals(
        cls, proposals: Sequence[Any], lane: str, carried: Collection[str]
    ) -> tuple[list[Any], dict[str, str]]:
        """Split a lane's proposals into the ones this job can settle, and the rest.

        Returns the kept proposals and, keyed by the unit the package answers
        in, the reason each deferred one was set aside. One call rather than
        two, so the neutral fan-in needs no way of composing a package's unit
        from a package's proposal.

        Keyed by the unit the package answers in — a requirement, a lane — with
        the reason a reader gets, so the caller needs no second lookup and no
        knowledge of what the package's units are.

        **Stated as a property, because it holds for a package nobody has
        written: a framework may need a fact of a kind a job does not carry.**
        A framework whose claims all rest on the system's own shape defers
        nothing, and inherits this. One ruling on requirements defers most of
        what it looks at, because most of what it looks at is settled by source
        code, by a deployed setting, or by a person.

        A deferred proposal never becomes a draft, so it never reaches the
        **Critic** and never becomes a **Claim**. It becomes a **Scope Entry**
        in the ``needs-other-evidence`` state, which is why the reason returned
        here has to name the kind of evidence that would settle it rather than
        merely say no.

        ``carried`` is what the job's input actually holds. It is an argument
        rather than a constant so that the answer changes when the service
        accepts a second kind of input, with no edit to any package.
        """
        del lane, carried
        return list(proposals), {}

    @classmethod
    def ruled_out(
        cls, model: SystemModel, options: Mapping[str, Any], lane: str
    ) -> dict[str, str]:
        """Units of ``lane`` this framework's own rules rule out of a model, with why.

        A framework whose units presuppose a thing — a chapter on file uploads
        for a model naming no upload — can say from its rules, before any agent
        runs, that the unit does not apply (#443). Each is keyed by the unit the
        package answers in against the reason a reader gets; the lane agent is
        told not to rule on them, and they reach the block's scope as
        ``not-applicable``. The neutral answer rules out nothing, which is what a
        framework whose claims rest on the system's own shape inherits.
        """
        del model, options, lane
        return {}

    @classmethod
    def misfiled(cls, draft: Claim) -> str:
        """Why this draft cannot belong to the lane it was filed in, or ``""``.

        A framework whose claims carry an action verb can say from a table
        which lanes that action may be filed in; a draft outside them is a lane
        error the review seam rejects before any judgement is spent on it
        (#442). The neutral answer is that every lane is legal, which is what a
        framework with no verb, or no lane grammar over its verbs, inherits.
        """
        del draft
        return ""

    def unknown_grounds(self) -> list[UnknownRef]:
        """The element/attribute pairs this claim's own grounds say the input left open.

        Every ``unknown-attribute`` ground is one, in ground order and without
        repeats. The pairs come from the evidence catalog, so they resolve by
        construction and nothing has to re-derive them from prose (#439).
        """
        seen: dict[tuple[str, str], UnknownRef] = {}
        for ground in self.grounds:
            if ground.kind == "unknown-attribute":
                seen.setdefault(
                    (ground.element_id, ground.attribute),
                    UnknownRef(
                        element_id=ground.element_id, attribute=ground.attribute
                    ),
                )
        return list(seen.values())

    @classmethod
    def settled_by_grounds(cls, draft: Claim) -> Ruling | None:
        """The ruling a draft's own grounds settle before any critic reads it.

        A draft citing an ``unknown-attribute`` ground rests on a control the
        input never stated, and the prompts already say what that makes it: a
        conditional claim, ruled ``needs-info`` on exactly those pairs. Code
        rules it here, and the critic never sees it (#439). ``None`` for a
        draft the grounds do not settle. A package whose ruling carries fields
        beyond the neutral shape overrides this to fill them.

        The sentence names each pair, and names the count instead when the
        pairs overrun ``REASON_MAX_CHARS``. ``related_unknowns`` carries every
        pair either way, so the prose is the only thing that shortens.
        """
        unknowns = draft.unknown_grounds()
        if not unknowns:
            return None
        named = ", ".join(
            f"`{ref.attribute}` on `{ref.element_id}`" for ref in unknowns
        )
        reason = f"The claim rests on {named}, which the input never stated."
        if len(reason) > REASON_MAX_CHARS:
            reason = (
                f"The claim rests on {len(unknowns)} attributes the input never stated."
            )
        verdict = ProposedVerdict(
            status="needs-info",
            reason=reason,
            related_unknowns=unknowns,
        )
        return Ruling(id=draft.id, verdict=verdict)

    @classmethod
    def unit_of(cls, draft: Claim) -> str:
        """The unit of this framework's scope list a draft rules on, or ``""``.

        A package that answers in its own units — a requirement — names the
        one a draft is about, so the fan-in can refuse a draft on a unit
        :meth:`ruled_out` already settled. The neutral answer names none: a
        framework whose claims are not a catalog has no unit a draft could be
        refused on.
        """
        del draft
        return ""

    @classmethod
    def rating_of(cls, draft: Claim) -> tuple[str, str] | None:
        """The two ratings a draft carries, where this framework grades harm.

        ``None`` for a framework that grades nothing, which is what the
        neutral shape says; a package with a ``severity`` returns its
        ``likelihood`` and ``impact`` so the review seam can compare two drafts
        of one fact pattern without reading a package's field (#444).
        """
        del draft
        return None

    @classmethod
    def lane_diagnostics(cls, drafts: Sequence[Claim]) -> list[str]:
        """What this framework wants *logged* about one job's drafts. Nothing, here.

        The one hook a package has into the fan-in, and it is deliberately
        write-only: the fan-in logs whatever comes back and nothing else reads
        it, so a package cannot reach a report or a verdict through this.

        It exists because the observations worth making about an agent's output
        are framework-specific in a way the neutral layer cannot reach. STRIDE's
        is that a lane numbered its drafts ``01, 02, 05`` — a statement about its
        own ``id_format``, which :class:`Claim` has none of, since ``id`` has no
        shared grammar. A framework with nothing to say about its own IDs
        inherits this and says nothing.
        """
        del drafts
        return []


class RuledClaim(Claim):
    """A :class:`Claim` a critic has ruled on: what a report carries.

    The layer exists because ``verdict`` cannot sit on :class:`Claim` itself. A
    draft *is* a claim — the resolver builds one before any critic sees it — and
    a draft has no verdict. So the neutral side carries two classes rather than
    one, mirroring the two pairs this module already has
    (:class:`ProposedVerdict` → :class:`Verdict`, and a package's own draft →
    ruled pair).

    The **service** constructs a :class:`Verdict` for every claim of every
    framework: each package brings its own critic, but the three states, the
    rules binding the fields to the status, and the review seam that checks them
    all stay here. So the shape is neutral and the *question* is the framework's
    — a STRIDE ``confirmed`` says an attack is credible, and another
    framework's says its requirement applies — which is why the package's own
    critic text says what its states assert and this class says nothing about
    it.
    """

    verdict: Verdict


class QuoteCandidate(BaseModel):
    """A span an agent claims is in one of the job's sources, and which source.

    The two fields a ``quote`` :class:`Ground` carries, and deliberately not
    that ground itself: an agent proposes the span, and
    :func:`~analysis_service.evidence.resolve_proposals` builds the ground. Both
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


class Proposal(BaseModel):
    """What a lane agent actually emits: a finding that *names* its evidence.

    The neutral base, shared by every framework. It carries exactly what
    :func:`~analysis_service.evidence.resolve_proposals` reads — the element refs,
    the two evidence lists, the title and the description — and each framework
    adds its own judgement fields and its own ID key on top. The reason this
    shape exists at all, that a provider's schema compiler cannot express a
    :class:`Ground`'s branch relationship, is identical for any framework.

    **The ID key is not shared.** STRIDE names an integer ``sequence``, and a
    requirement-shaped framework names a requirement. That follows from ``id``
    having no shared grammar: a package supplies the ``id_format`` and the
    per-lane prefix, and the resolver composes the ID from its own key.

    THE FIELD A MODEL NO LONGER SERIALIZES. A :class:`Ground` is a flat object
    whose legal field combination depends on its own ``kind``, and that
    relationship is unrepresentable in the JSON schema a provider compiles —
    the reasons are :class:`Ground`'s own docstring's. It was therefore carried
    by prompt instruction, which makes a mis-shaped ground an expected
    stochastic outcome rather than a defect: an agent that picked the right
    fact and spelled it into the wrong branch killed the node, and with it all
    six lanes, at a seam with no re-ask path.

    So the agent stops spelling it. ``evidence_refs`` holds IDs copied from the
    evidence catalog the service derived from the validated System Model,
    ``quotes`` holds spans plus the source each came from, and
    ``absent_elements`` holds terms the model names nowhere. All three are flat
    lists of a single shape, which a schema compiler can express exactly;
    nothing an agent can put in any of them is a shape error, and an entry
    naming nothing fails deterministically against the model rather than
    probabilistically against a validator.

    ``absent_elements`` is named rather than selected because a catalog can
    enumerate what a model holds and never what it lacks. It is checked rather
    than trusted: the service drops a term the model does in fact name.

    What is given up: the branch is no longer the agent's to state. That is the
    point — the branch was always dictated by the trigger, so an agent choosing
    it was an agent given a mechanical job to get wrong. The catalog entry
    carries the branch, and picking the entry picks it.

    At least one entry across the three lists, which is ``grounds``'
    ``min_length=1`` expressed over them: a finding with no justification at
    all is the one thing none of them may say. Which list carries it is free —
    a threat triggered by a crossing or an unknown legitimately quotes nothing,
    and a requirement ruled out for a component the system never had cites only
    an absence.
    """

    model_config = ConfigDict(extra="forbid")

    # THE LENGTH AND CARDINALITY CAPS BELOW ARE FATAL, AND THAT IS ACCEPTED
    # RATHER THAN OVERLOOKED. Unlike the rules this class exists to remove, they
    # *are* expressible in a JSON schema — but providers enforce ``maxLength``
    # and ``minItems`` no more reliably than ``pattern``, so an agent can still
    # exceed one, and the raise lands at the node boundary where it costs the
    # lane and its siblings.
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
    # Unconstrained here and narrowed by the packages that need it, exactly as
    # :class:`Claim`'s own is: a proposal that validates must be resolvable into
    # a claim that validates, so the two sides of that pair move together.
    affected_element_ids: list[str] = Field(
        default_factory=list, max_length=MAX_ELEMENTS_PER_PROPOSAL
    )
    # Unconstrained here and narrowed by the packages that need it, for the
    # reason the line above gives: a proposal that validates must resolve into a
    # claim that validates, so the two sides of that pair move together.
    verb: ActionVerb | None = None
    evidence_refs: list[str] = Field(
        default_factory=list, max_length=MAX_REFS_PER_PROPOSAL
    )
    quotes: list[QuoteCandidate] = Field(
        default_factory=list, max_length=MAX_QUOTES_PER_PROPOSAL
    )
    # The third list, and the one whose referent is the model as a whole. A
    # catalog can enumerate what a model contains; it cannot enumerate what a
    # model lacks, so an absence is named rather than selected. Each entry is
    # one lowercase term, and the service checks it against every element's
    # text before building the ground.
    absent_elements: list[str] = Field(
        default_factory=list, max_length=MAX_ABSENT_PER_PROPOSAL
    )

    @model_validator(mode="after")
    def _check_shape(self) -> Self:
        if not self.evidence_refs and not self.quotes and not self.absent_elements:
            raise ValueError(
                f"the draft titled {self.title!r} justifies itself with"
                " nothing: name at least one evidence reference, quote or"
                " absent element"
            )
        return self


# How many top-level fields an invalid proposal keeps as text. A proposal has
# under ten; the bound is against an emission that is not a proposal at all.
SCALARS_MAX = 20


class InvalidProposal(BaseModel):
    """One proposal a lane emitted that failed its own schema, kept by the batch.

    Enough to name the claim the agent meant and say what was wrong, and no
    more: the item itself is agent text of unbounded size and is not carried.
    ``scalars`` holds the item's top-level scalar fields, strings bounded and
    numbers as they came, so the resolver can read whichever field its
    package's ID rule keys on — this model names no package's key, and a key
    keeps the type the rule composes from — and the title; ``error`` is the
    first fault pydantic reported, in its own words.
    """

    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0)
    scalars: dict[str, str | int | float | bool] = Field(
        default_factory=dict, max_length=SCALARS_MAX
    )
    error: str = Field(min_length=1, max_length=500)

    @classmethod
    def of(cls, index: int, item: Any, error: ValidationError) -> InvalidProposal:
        fields = item if isinstance(item, dict) else {}
        scalars = {
            str(name)[:100]: value[:200] if isinstance(value, str) else value
            for name, value in list(fields.items())[:SCALARS_MAX]
            if isinstance(value, str | int | float | bool)
        }
        first = error.errors()[0]
        location = ".".join(str(loc) for loc in first["loc"])
        message = f"{location}: {first['msg']}" if location else first["msg"]
        return cls(index=index, scalars=scalars, error=message[:500])


class ProposalBatch(BaseModel):
    """What a lane agent node emits: an object wrapping its list of proposals.

    The wrapper exists for the *schema*, not for the domain. A node's
    ``output_schema`` is what the graph asks the provider to constrain generation
    to, and a bare ``list[Proposal]`` cannot be asked for: ADK cannot convert a
    generic alias into a response format, so it sends none and the node generates
    unconstrained — silently, with only a log line. Wrapping the list in a model
    gives the conversion something it can carry, and an object root at that, which
    is what OpenAI's structured outputs require and a bare array would not
    satisfy.

    **The field is ``claims`` and the name is neutral because the prompt is.**
    ``prompts/analyze.md`` is one shared body serving every registered framework's
    lane agents — a package brings its lane skill, its exemplars and its critic
    text, not a copy of the output contract — so the word the prompt spells and
    the word the graph unwraps has to be one a second framework can read without
    lying. A package narrows the element type; nobody renames the field.

    Nothing downstream sees the wrapper: the graph unwraps at the node boundary,
    so the domain keeps working in lists.

    **A batch validates by salvaging.** The node validates its emission against
    this model before anything else runs, so one proposal carrying a verb
    outside the closed set, a severity value the enum does not hold, or
    neither a reference nor a quote would otherwise cost the node and the job.
    The wrap validator validates each item on its own: the ones that pass fill
    ``claims``, and the ones that fail fill ``invalid``, keyed enough to name
    the claim the agent meant. ``invalid`` is kept out of the JSON schema, so
    the provider is asked for exactly the strict shape and never told there is
    a slot for a bad one. A payload that already carries ``invalid`` is a
    batch read back from state and is validated as it stands.
    """

    model_config = ConfigDict(extra="forbid")

    claims: list[Proposal] = Field(max_length=MAX_CLAIMS_PER_BATCH)
    invalid: SkipJsonSchema[list[InvalidProposal]] = Field(default_factory=list)

    @model_validator(mode="wrap")
    @classmethod
    def _salvage(cls, data: Any, handler: ModelWrapValidatorHandler[Any]) -> Any:
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("claims"), list)
            or "invalid" in data
        ):
            return handler(data)
        (item_type,) = get_args(cls.model_fields["claims"].annotation)
        kept: list[Any] = []
        invalid: list[InvalidProposal] = []
        for index, item in enumerate(data["claims"]):
            try:
                item_type.model_validate(item)
            except ValidationError as error:
                invalid.append(InvalidProposal.of(index, item, error))
            else:
                kept.append(item)
        return handler({**data, "claims": kept, "invalid": invalid})


class Ruling(BaseModel):
    """The critic's ruling on one draft: the fields the critic owns, and no more.

    A ruling is **not** a claim. It names the draft it rules on by ``id`` and
    carries the judgement that is the critic's — the ``verdict`` — leaving the
    agent's own fields where they already are.
    :func:`~analysis_service.critic.assemble_claims` merges a ruling onto the
    draft it names to build the :class:`RuledClaim` the report carries, so the
    report's shape is unchanged.

    **Neutral, because every package has a critic and the seam is shared.** Each
    framework brings its own critic text and its own question, but the three
    verdict states, the rules binding the fields to the status, and the review
    seam that checks them are the service's. A package adds its own judgement
    fields on top — STRIDE's ``confidence`` and its severity override are on
    :class:`~analysis_service.frameworks.stride.record.ThreatRuling`.

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

    A package's own subclass may add one more thing: a draft field the ruling
    is allowed to *replace*. STRIDE's severity override is the only one today.
    """

    model_config = ConfigDict(extra="forbid")

    # NO PATTERN, DELIBERATELY, AND NOTHING IS GIVEN UP BY DROPPING IT. The
    # critic does not compose this ID — it copies one off the roster it was
    # handed — and :func:`~analysis_service.critic.review_issues` already requires
    # the ruled set to equal the drafted set exactly. That is a *stronger*
    # constraint than any pattern: an ID matching a drafted one is well-formed
    # by construction, because the draft's own ``id`` was composed by the
    # service from its package's own ``id_format``.
    #
    # So a pattern here could only ever fire on an ID the reconciliation was
    # about to reject anyway — and it fired earlier and fatally, at the node
    # boundary, where a raise kills the critic's single pass over every draft
    # and the bounded re-ask never runs. Without it, ``"S-1"`` arrives as two
    # precise re-askable problems: one draft dropped, one claim returned that
    # nobody drafted.
    #
    # The length bound stays because an unbounded string from a model is worth
    # capping on principle (OWASP LLM10), and it is the same number every other
    # ID field here carries. It is not the well-formedness check and cannot be
    # read as one.
    id: str = Field(max_length=CLAIM_ID_MAX_CHARS)
    # The unruled shape: a verdict whose fields disagree with each other is a
    # problem for the review seam to report and the re-ask to fix, not a reason
    # to fail the node. assemble_claims builds the canonical Verdict.
    verdict: ProposedVerdict


class RulingBatch(BaseModel):
    """What a critic node and its re-ask emit: one ruling per draft, wrapped.

    Separate from :class:`ProposalBatch` because the element type differs; see
    that class for why the wrapper exists at all and why its field is spelled
    ``claims`` on both.
    """

    model_config = ConfigDict(extra="forbid")

    claims: list[Ruling]


class TokenUsage(BaseModel):
    """What one node execution spent, as the provider reported it.

    Vendor-neutral field names, on the same principle as the model tiers: the
    provider's own spellings (``prompt_token_count``,
    ``candidates_token_count``, ``thoughts_token_count``,
    ``cached_content_token_count``) are one vendor's product vocabulary, and
    this record outlives the vendor it was read from.
    ``analysis_service.execution`` owns the mapping.

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

    **The served build is what the provider said, and nothing here verifies
    it.** ``model`` is read off the provider's own event stream, so a
    compromised translator can put any string in it. That is why the two fields
    are both recorded and why ``execution_fingerprint`` binds both: a manifest
    blesses the *pair*, and the requested half comes from the deployment's
    configuration rather than from the provider. The report states the trust
    level once, on the envelope's ``execution`` block, rather than repeating it
    on every node.

    ``execution_fingerprint`` is the identity hash a deployment's manifest
    blesses: ``sha256`` of the versioned execution identity — both routes, the
    resolved tier sampling, the built graph's instruction digest, and the
    versions of the distributions that sit between the node and its provider.
    See :mod:`analysis_service.identity`. Computed per node *execution*, so a
    build that moves partway through an eval sweep gives one node two hashes.

    ``instruction_sha256`` is the digest of the graph this node ran in, and on a
    report it repeats what ``analysis_context`` already says. **The repetition is
    load-bearing.** A report holds one graph, so the two always agree there — but
    a :class:`NodeRun` also travels alone: an eval sweep folds one flat list of
    them across several graphs, because a case declares which frameworks it
    carries and each distinct selection builds its own. In that list the report
    block is gone and the node's own digest is the only thing that says which
    instruction set produced its fingerprint. A row carrying a hash it cannot
    account for is a row nobody can verify.

    A deterministic FunctionNode carries none of these four.

    ``usage`` is what the node execution cost, ``None`` for a deterministic
    FunctionNode and for any LLM node whose provider reported nothing. It is
    deliberately *not* coupled to ``model`` the way ``execution_fingerprint``
    is: a fingerprint keyed on a served build is incoherent without one, but a
    token count is a fact about the call regardless of whether the provider
    also named the build that served it, and refusing to record it would
    discard a real measurement to satisfy a symmetry nobody needs.

    ``attempts`` is how many provider calls the execution took, counted by the
    retry driver. ``usage`` meters the one that answered; a failed attempt
    reports nothing, so the count is the only trace the prompt bytes it sent
    leave. A settlement charges them from it (see
    :func:`analysis_service.budgets.measured_tokens`).
    """

    model_config = ConfigDict(extra="forbid")

    node: str = Field(min_length=1, max_length=100)
    model: str | None = None  # served; None for deterministic FunctionNodes
    requested_model: str | None = None  # configured
    instruction_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    execution_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    duration_ms: int = Field(ge=0)
    usage: TokenUsage | None = None
    attempts: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _fingerprint_needs_its_inputs(self) -> Self:
        # Every per-node input to the identity has to be here beside the hash,
        # or the row states a fingerprint nobody can rebuild. A deterministic
        # node has none of them and no fingerprint, which is the consistent
        # absence.
        if self.execution_fingerprint is None:
            return self
        missing = [
            name
            for name, value in (
                ("a served model", self.model),
                ("a requested model", self.requested_model),
                ("an instruction digest", self.instruction_sha256),
            )
            if value is None
        ]
        if missing:
            raise ValueError(
                f"execution_fingerprint requires {' and '.join(missing)}"
                " on the same node"
            )
        return self


class ExecutionEnvelope(BaseModel):
    """What executed this run, and how much its self-report is worth.

    One block per report rather than one per node, because every node in a
    drive ran on the same install and asked the same graph. Repeating it would
    be the same fact ten times over, free to drift.

    ``identity_version`` is the schema of the payload each node's
    ``execution_fingerprint`` hashes. A reader recomputing a fingerprint needs
    it, and certification refuses a manifest written for a different one rather
    than comparing across them.

    ``served_model_trust`` states plainly what the served build on each node is
    worth. ``provider_reported`` means the provider named it on its own event
    stream and nothing independent confirmed it. It is recorded rather than
    assumed because the difference between "the provider said Opus" and "Opus
    answered" is exactly the difference a compromised translator lives in, and
    a report that omits the distinction reads as the stronger claim.

    ``build`` is the installed version of every distribution whose code sits
    between a node and its provider — this service, the agent runtime and the
    model translator. It is here rather than in a deployment note because it is
    inside the fingerprint: a ``litellm`` bump moves every hash, and a reader
    who cannot see which version ran cannot tell a sanctioned run from a
    silently upgraded one.

    ``review_independence`` is how far this deployment required each framework's
    criticism to sit from its own analysis. It is a **statement, never a
    warning**: a deployment that asked for an independent reviewer and could not
    have one fails to load, so no report exists to warn on. What this answers is
    the other direction — a reader of a ``shared`` run can see the review was
    same-domain rather than infer it from two node rows naming one model. The
    detail behind it is already on :class:`NodeRun`: the ``analyze/<name>`` and
    ``critic/<name>`` rows each carry their own requested route, served build
    and fingerprint.

    Deliberately **not** in the fingerprint. The policy decides nothing at run
    time — the loader has already enforced it — so hashing it would re-baseline
    every blessed identity on a policy edit that moved no model.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    identity_version: int = Field(ge=1)
    served_model_trust: Literal["provider_reported"] = "provider_reported"
    build: dict[str, str] = Field(default_factory=dict)
    review_independence: Literal["shared", "distinct_model", "distinct_provider"] = (
        "shared"
    )


class AnalysisContext(BaseModel):
    """What informed the analysis, as distinct from what proves a finding.

    The report already records the two ends of a run: the served build and
    sampling each node ran on (:class:`NodeRun`), and the facts each finding
    rests on (``grounds``). Between them sits everything the service *put in
    front of* the lane agents — the instruction text they were given and the
    reference packs this model earned — and none of it was recorded anywhere. Two runs of the same model on the same
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
    it identifies the repo-authored text — the shared prompts and every carried
    package's lane skills, exemplars, critic text and rubric — and carries no
    submitter bytes at all, which is what makes it
    publishable beside a report. The digest is also one of the seven parts of
    the **Execution Identity**, so two runs told different things cannot share
    a fingerprint, and this field is what lets a reader recompute one and see
    which instruction set stood behind it.

    ``domain_packs`` is a fact about *this job's* model rather than the
    deployment: selection is per-job (:mod:`analysis_service.domains`), so the
    same service gives two submissions different reference material, and the
    names are the only record of which.

    **``fired_rules`` and ``knowledge_docs`` are not here**, and the rule that
    moved them is the one that sorted every field of the flat schema this
    replaced: a field sits where the thing it describes sits. A candidate rule
    belongs to the package that declared it and a retrieved document to the
    package that selected it, so both name *one framework's* material and both
    sit on :class:`FrameworkAnalysis`. The two that stayed describe the built
    graph and the shared model, of which a report has one each.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    instruction_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    domain_packs: list[str] = Field(default_factory=list)


class FrameworkSelection(BaseModel):
    """One framework a job asked for, and the options it asked for it with.

    ``options`` defaults to ``{}`` **on the envelope only**. The package's own
    options model then rejects a submission that left out a value it needs, and
    names the field it wants. No package field carries a default, so no
    submission means two different things on two installs.

    Recorded on the :class:`Job` exactly as the input ladder resolved it,
    because a report that omits the options does not say what was analysed — a
    framework whose options select which requirements apply produces a different
    answer under different ones.
    """

    model_config = ConfigDict(extra="forbid")

    name: FrameworkName
    options: dict[str, Any] = Field(default_factory=dict)


class Job(BaseModel):
    """Identity and timing of the run that produced this report.

    A report only exists for a completed job, so ``status`` admits exactly the
    ``completed`` state from the job-lifecycle contract.

    ``frameworks`` is what was *asked for*, in submission order. The envelope's
    own check reads it: the analysis blocks must answer exactly this list, in
    this order, so a framework that produced nothing cannot be dropped quietly.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=100)
    status: Literal["completed"] = "completed"
    created_at: datetime
    completed_at: datetime
    #: How many times a critic was asked again, summed across the job's
    #: frameworks. The graph re-asks a critic whose rulings do not reconcile
    #: with the drafts, once and bounded, so zero is the ordinary answer and a
    #: rate that climbs after a prompt edit says the edit made the ruling harder
    #: to give. Computed by :func:`~analysis_service.graph.revise_rounds`.
    revise_rounds: int = Field(default=0, ge=0)
    frameworks: list[FrameworkSelection] = Field(default_factory=list)

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

    Marked per entry, dropped per claim: a claim with one bad quote beside good
    ones is still justified, and :func:`~analysis_service.critic.join_drafts`
    drops a claim only where *no* ground on it verifies at all, recording it as
    a :class:`DroppedClaim`.
    """

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=CLAIM_ID_MAX_CHARS)
    index: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=500)


class RepairedQuote(BaseModel):
    """A quote ground whose text the service replaced with the source's own span.

    The ladder refused what the agent wrote, and
    :func:`~analysis_service.grounding.repair_quote` found a window of the named
    source near enough to hand back. The ground now carries that window — the
    submitter's words — and this mark carries what the agent wrote, so the
    substitution is on the record rather than silent. ``similarity`` is the
    ratio that licensed it.

    A reference like :class:`UnverifiedGround`: the claim's ``id`` plus the
    index of the entry in its ``grounds`` list, checked the same way. The two
    are exclusive for one entry — a repaired quote verifies, so it is never
    also marked unverified.

    Service-owned and outside :class:`Ground`, for the reason every mark here
    is: an agent must not report on its own accuracy, and a field that said
    "this was repaired" on the ground itself would ride into the provider
    schemas as one the agent could set.
    """

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=CLAIM_ID_MAX_CHARS)
    index: int = Field(ge=0)
    written: str = Field(min_length=1, max_length=1000)  # Ground.text's bound
    similarity: float = Field(ge=0.0, le=1.0)


# How much of a named element ID a mark carries. Exported for the reason
# :data:`REFERENCE_MAX_CHARS` gives: ``affected_element_ids`` is a free list the
# model fills, and an entry that resolves to nothing has no length of its own.
ELEMENT_REF_MAX_CHARS = 300


class UnresolvedReference(BaseModel):
    """An element ID a claim named in ``affected_element_ids`` and lost.

    Either the model does not contain it, or it lies beyond the reach of the
    claim's own grounds; ``reason`` says which.

    The structural twin of :class:`UnresolvedMention`. That mark is an ID in
    prose; this is an ID in the field the claim's identity is computed from.
    The reference is dropped from the claim and recorded here, and the claim
    stands on the elements that did resolve — the rule every other citation
    already had. A claim that named elements and lost every one is dropped as
    a :class:`DroppedClaim`, because a claim about nothing is not a finding.

    A :data:`ClaimMark`: it names a claim the block carries.
    """

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=CLAIM_ID_MAX_CHARS)
    element_id: str = Field(min_length=1, max_length=ELEMENT_REF_MAX_CHARS)
    #: Why the reference was dropped. Empty means the model does not contain
    #: it; :data:`BEYOND_GROUNDS` means it exists and the claim's own grounds
    #: do not reach it (#441).
    reason: str = Field(default="", max_length=200)


#: The reason on an :class:`UnresolvedReference` an element the model holds
#: earns when it sits more than one hop from every place the claim's grounds
#: name. Reach belongs in the description; ``affected_element_ids`` is what the
#: action lands on.
BEYOND_GROUNDS = "more than one hop from every place the claim's grounds name"


# How much of a cited ID a mark carries. Exported for the reason
# :data:`REFERENCE_MAX_CHARS` gives: the producer reads a run of ID characters
# out of a description the model wrote, and that run has no length of its own.
MENTION_MAX_CHARS = 300


class UnresolvedMention(BaseModel):
    """An element ID a description names in prose that the model does not contain.

    A **mention**, never a reference: ``affected_element_ids`` is the threat's
    structural claim about what it acts on, and one that does not resolve fails
    the job at :func:`~analysis_service.critic.join_drafts`. This is the softer
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

    claim_id: str = Field(min_length=1, max_length=CLAIM_ID_MAX_CHARS)
    mention: str = Field(min_length=1, max_length=MENTION_MAX_CHARS)


# How much of an agent-supplied evidence reference a mark carries. Exported
# because the producer must truncate to it: `evidence_refs` is a free string
# the model fills, so an over-long one would fail validation here and cost the
# job the very report this mark exists to preserve. One name, so the bound the
# producer cuts at and the bound this field accepts cannot drift apart.
REFERENCE_MAX_CHARS = 300


class UnresolvedEvidence(BaseModel):
    """An evidence reference a threat cited that its job's catalog does not hold.

    The catalog is closed and derived, so a reference outside it names no fact
    and **no ground can be built from it** — which is what separates this from
    :class:`UnverifiedGround`. An unverified quote is a real ground whose text
    the service could not find; this is a ground that never existed. There is
    nothing to render, so the entry is dropped and the mark records what was
    asked for — verbatim up to ``REFERENCE_MAX_CHARS``, past which a reference
    is long enough that it is evidence of a malfunctioning agent rather than of
    a fact anyone meant to cite.

    **Marked per reference, dropped per claim**, exactly as an unverified
    quote is. A threat citing three facts, one of them composed, is still
    justified by the two that resolve; a threat whose evidence resolves to
    nothing at all has no justification left, and
    :func:`~analysis_service.evidence.resolve_proposals` drops it as a
    :class:`DroppedClaim`, because a finding with no grounds is the one
    thing this schema does not permit.

    This replaced a whole-job failure (#138). Agents compose well-formed
    references — correct grammar, plausible element IDs, absent from the set —
    and a live sweep lost 2 of 12 jobs that way. Discarding six lanes of
    analysis because one threat named one fact that did not exist trades a
    report for a citation error, which is the trade
    :class:`UnresolvedMention` already refused to make.

    Service-owned rather than a field on :class:`DraftThreat`, for the reason
    every mark here is: an agent must not report on its own accuracy.
    """

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=CLAIM_ID_MAX_CHARS)
    reference: str = Field(min_length=1, max_length=REFERENCE_MAX_CHARS)


class UnknownClaimIdentity(BaseModel):
    """A claim naming an identifier its own framework does not recognise.

    The sibling of :class:`UnresolvedEvidence`, one level up. That mark records
    a claim citing a *fact* the job's catalog does not hold; this one records a
    claim naming a *requirement* its framework's catalog does not hold. Both are
    the same failure — an agent composing a well-formed reference to something
    that does not exist — and #138 already settled what it costs: the entry, not
    the run.

    **Only a framework that owns a catalog can produce one.** A package whose
    identifiers are its own to mint — STRIDE's ``S-01`` is a lane and a counter
    — recognises every well-formed key by construction, so its
    :class:`~analysis_service.frameworks.IdRule` declares no
    :attr:`~analysis_service.frameworks.IdRule.known` predicate and this list stays
    empty. That is a written statement rather than an omission, exactly as the
    empty knowledge tables are.

    ASVS is the case it exists for. A lane agent supplies a
    ``<section>.<requirement>`` key, the service composes
    ``v5.0.0-<chapter>.<key>`` from it, and nothing in the shape of ``99.99``
    marks it as absent from the 345 the standard publishes. Left unchecked, the
    report cites the standard's own version-safe reference format for a
    requirement the standard does not contain — which is worse than a missing
    finding, because the citation reads as verifiable.

    **Not a** :data:`ClaimMark`. Those three name a claim that *survived*, so
    one check confirms each names a real one. This names a claim that was
    dropped, so its ``claim_id`` is deliberately absent from the block's claims
    and the same check would invert. ``title`` carries what the agent called the
    finding, because unlike an unresolved reference there is no surviving claim
    for a reader to look at.

    Service-owned rather than a field on the record, for the reason every mark
    here is: an agent must not report on its own accuracy.
    """

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=CLAIM_ID_MAX_CHARS)
    title: str = Field(min_length=1, max_length=200)


# How much of a lost quote a groundless mark carries. A quote is agent text
# bounded only by its own field, so the reason that repeats it must cut it.
DROPPED_REASON_MAX_CHARS = 500


class DroppedClaim(BaseModel):
    """A claim the service dropped for a fault in one entry of it.

    The sibling of :class:`UnknownClaimIdentity`: a claim the service dropped,
    so its ``claim_id`` is deliberately absent from the block's claims and
    ``title`` is the only trace of what the agent found. One list for every
    reason, and ``reason`` says which, in the agent's own words up to
    :data:`DROPPED_REASON_MAX_CHARS`:

    * the proposal failed its own schema — a verb outside the closed set, a
      severity value the enum does not hold, neither a reference nor a quote
      (:func:`~analysis_service.evidence.validate_proposals`);
    * every reference it cited is outside the catalog
      (:func:`~analysis_service.evidence.resolve_proposals`);
    * every element it named is absent from the model, its ID duplicates an
      earlier draft's, or its only grounds are quotes the source it names does
      not contain (:func:`~analysis_service.critic.join_drafts`).

    Each of these costs one entry and never the job. The case against a drop
    is that a finding deleted for a reason recorded in a list is a silent
    removal, and that a dead job at least tells somebody. It does not hold
    here: :class:`UnknownClaimIdentity` drops a claim on the same terms, the
    viewer renders the list as a block-level note, and the alternative
    discards every lane's work over one entry's fault. The drop is visible
    where the dead job is not. Nothing else persists a draft, so the reason
    carries what was wrong.

    Service-owned rather than a field on the record, for the reason every mark
    here is: an agent must not report on its own accuracy.
    """

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=CLAIM_ID_MAX_CHARS)
    title: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=DROPPED_REASON_MAX_CHARS)


class MissingMitigation(BaseModel):
    """A threat offering no countermeasure, and no reason for offering none.

    ``mitigations`` is allowed to be empty, but only for one stated reason: the
    threat is conditional on an ``unknown``, and no countermeasure can be named
    without first learning that fact. The prompt says exactly that, and the
    branch rule makes it checkable — a threat triggered by an unknown carries an
    ``unknown-attribute`` ground, because that is the branch its trigger
    dictates. So "empty for the legitimate reason" and "empty with no reason"
    are mechanically distinguishable, and only the second is recorded here.
    ``absent-attribute`` deliberately does not license the empty list: a control
    the submitter said is *not there* is a fact already learned, and the
    countermeasure is to put the control in.

    A completeness signal rather than a correctness one, which is why it is a
    mark and not a failure: a finding with no recommended action is still a
    finding, and a reader who can see *which* findings came with nothing to do
    about them knows something the threat list alone does not tell them. The
    same fan-in argument applies as ever — nothing here is worth six lanes of
    analysis.
    """

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=CLAIM_ID_MAX_CHARS)


# The marks whose whole placement claim is the claim they name, so one check
# covers them all (:meth:`FrameworkAnalysis._claim_mark_issues`).
# :class:`UnverifiedGround` is deliberately absent: it also names an *index*
# into that claim's grounds, so it needs the stricter check of its own.
# :class:`SharedElementName` is absent because it annotates the model rather
# than a claim — which is also why it is the one mark that stays on the
# envelope while these ride in the block whose claims they annotate.
ClaimMark = (
    UnresolvedReference | UnresolvedMention | UnresolvedEvidence | MissingMitigation
)


class SharedElementName(BaseModel):
    """Elements of different types whose names normalize to one slug.

    ``extract.md`` tells the transcriber "nothing gets two types", and the
    failure it has in mind is one real thing recorded twice — a `process:` and
    a `store:` for the same web app. The validity gate cannot catch it: an ID
    carries its type, so the two IDs differ and ``duplicate-id`` compares whole
    IDs.

    **THE GATE HAS ONE SEVERITY, AND THIS IS NOT IT.** Every
    :class:`~analysis_service.validation.ValidationIssue` is fatal — "returns an
    empty list" *is* the definition of ready-for-analysis that
    :func:`~analysis_service.graph.validate_extraction` routes on — and a shared
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
    (:meth:`~analysis_service.system_model.SystemModel.shared_names`), so a
    reader can check it against the very model the report carries.
    """

    model_config = ConfigDict(extra="forbid")

    name_slug: str = Field(min_length=1, max_length=200)
    # Two or more, always: a slug held by one element is not a collision, and
    # the grouping never emits a singleton.
    element_ids: list[str] = Field(min_length=2)


class AnalysisMarks(BaseModel):
    """Every service-owned mark one run produced, as one value.

    The five lists below have one owner, one standing and one policy: the
    *service* records them, they ride beside the findings rather than on them
    (an agent must not report on its own accuracy), and none of them fails a
    job. They used to travel as five loose parallel lists — through the fan-in,
    through four session-state keys, through four parameters on the assemble
    node, through four fields on an :class:`~analysis_service.graph.Analysis` and
    its two state methods — so adding the fifth cost about fifteen edits of one
    shape. Here they are one field.

    **Not the report's wire shape.** The report keeps its five top-level
    arrays; :meth:`~analysis_service.graph.Analysis.into_report` is where this
    value becomes them. Nesting them there would break every consumer of a
    published schema to save one of those fifteen edits.

    Empty is the common case and means what it says: every quote verified,
    every mention resolved, every reference named a fact, every threat carried
    a countermeasure or the unknown that excuses carrying none, and no two
    element types share a name.
    """

    model_config = ConfigDict(extra="forbid")

    unverified_grounds: list[UnverifiedGround] = Field(default_factory=list)
    #: How the *first* critic pass failed to reconcile with its drafts, one
    #: message per problem, as the bounded re-ask was asked to fix them. Empty
    #: means the first pass was clean.
    #:
    #: **Recorded because nothing else can see it.** ``route_review`` renders
    #: these messages into the re-ask's prompt, and without this list a
    #: repaired run and a clean one read alike in every artifact the service
    #: keeps. A package whose first pass never reconciles is running on its
    #: single retry, which a report should say without a live run.
    #:
    #: A mark, not a failure: the re-ask exists for these, and a run that
    #: repaired itself is a successful run. What it is not is a *clean* one.
    unreconciled_rulings: list[str] = Field(default_factory=list)
    repaired_quotes: list[RepairedQuote] = Field(default_factory=list)
    unresolved_references: list[UnresolvedReference] = Field(default_factory=list)
    unresolved_mentions: list[UnresolvedMention] = Field(default_factory=list)
    unresolved_evidence: list[UnresolvedEvidence] = Field(default_factory=list)
    unknown_claim_identities: list[UnknownClaimIdentity] = Field(default_factory=list)
    dropped_claims: list[DroppedClaim] = Field(default_factory=list)
    missing_mitigations: list[MissingMitigation] = Field(default_factory=list)
    shared_element_names: list[SharedElementName] = Field(default_factory=list)

    def merged_with(self, other: AnalysisMarks) -> AnalysisMarks:
        """Both sets of marks, this one's entries first in every list.

        The fan-in collects marks from three producers — one per lane's
        evidence resolution, the join across all six, and the model itself —
        and this is how they become one value. It walks ``model_fields`` rather
        than naming the five lists, so a sixth mark joins by being declared
        above rather than by someone remembering this method.
        """
        return AnalysisMarks(
            **{
                name: [*getattr(self, name), *getattr(other, name)]
                for name in AnalysisMarks.model_fields
            }
        )


class BlockSummary(BaseModel):
    """Counts a front-end can render without walking one block's claim list.

    Neutral, and per block rather than per report: a report carrying two
    frameworks has two of these, because a claim count that summed across
    frameworks would add a credible attack to an unanswered requirement and call
    the total findings.

    ``elements_analyzed`` is deliberately **not** here. It is a fact about the
    shared model, so N copies would be N chances to disagree; it sits on the
    envelope as a scalar instead.

    A package narrows this with whatever its own record can be counted by —
    STRIDE adds ``by_category`` and ``by_severity``, and a framework that grades
    nothing adds neither.
    """

    model_config = ConfigDict(extra="forbid")

    claim_count: int = Field(ge=0)
    needs_info_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)


class ScopeEntry(BaseModel):
    """One unit a framework considered and raised no claim about.

    The positive half of an answer. A framework whose **Precondition** or whose
    own presence tests rule a unit out has to *say so*: dropping it leaves a
    reader unable to tell a requirement that does not apply from one nobody
    looked at, which is the same distinction **Coverage** exists to make about
    lanes.

    **Every unit appears, and the complement is not derived.** An eval scorer
    can list the exceptions and derive the rest, because it holds the package's
    own catalog; a report's reader does not. Self-containment is the property
    this payload enforces everywhere else.

    **Three states, and the third is not a weaker second.** ``not-applicable``
    says the unit does not apply to a system of this shape — a complete answer.
    ``applicable`` says the framework considered it and raised nothing.
    ``needs-other-evidence`` says the unit *does* apply and this job cannot
    settle it, because the evidence that would is not the kind the job carries.

    That third state exists because collapsing it into either of the others
    loses the distinction a reader most needs. Folded into ``not-applicable`` it
    claims the unit was ruled out, which is false. Left as a **Claim** with a
    ``needs-info`` **Verdict** it reads as *send more of the same input*, which
    will never work — and a submitter cannot tell those apart. Its reason names
    the kind of evidence that would settle it, so the answer is actionable in
    the only way it can be: by supplying a different kind of input.

    Both non-``applicable`` states must state a reason, which is the rule
    :class:`Verdict` already applies to its two non-confirmed states.

    **STRIDE's list is empty**, and that is not an omission: STRIDE has no
    precondition that refuses a lane, so nothing it can analyse is out of scope.
    """

    model_config = ConfigDict(extra="forbid")

    unit: str = Field(min_length=1, max_length=300)
    state: Literal["applicable", "not-applicable", "needs-other-evidence"]
    reason: str = Field(default="", max_length=1000)
    #: The kind of evidence that would settle this unit, when the state says
    #: none available could. A field rather than a phrase inside ``reason``
    #: because a reader groups by it — "141 need source code" is the readable
    #: form of 141 separate sentences — and grouping by parsing our own prose
    #: is a rule nothing enforces.
    needs: str = Field(default="", max_length=100)

    @model_validator(mode="after")
    def _check_shape(self) -> Self:
        if self.state != "applicable" and not self.reason:
            raise ValueError(
                f"scope entry {self.unit!r} is {self.state} and must state a reason"
            )
        if (self.state == "needs-other-evidence") != bool(self.needs):
            raise ValueError(
                f"scope entry {self.unit!r} must name the evidence it needs if and"
                " only if its state says none available could settle it"
            )
        return self


class LaneCoverage(BaseModel):
    """What one lane agent was offered, and how much of it its drafts cite.

    The question this answers is the one a bare claim count cannot: whether "no
    finding here" means *examined and cleared* or *never looked at*. Every
    number is computed in code from the System Model, the deterministic
    candidate triggers and the agent's own drafts — none of it is asserted by
    a model, which is the whole reason it is worth recording.

    **Per lane, not per category.** A lane is a **Framework Package**'s own
    unit, and the six STRIDE categories are one package's lane list rather than
    a fact about the report. The row is keyed by the lane slug the package
    declares.

    **Read ``*_cited`` as cited, not as considered.** An agent that examined a
    flow and correctly concluded there was nothing to raise cites nothing, and
    is indistinguishable here from one that never read it. The fields are named
    for what they measure rather than for what a reader would like them to
    mean; the honest use is the aggregate — a lane citing two of forty
    structural leads across a corpus is a coverage signal, one agent's zero on
    one case is not.

    ``elements`` is the whole model rather than the subset a lane's
    ``## Applicability`` scopes: applicability lives in the skill text, and a
    second copy of it in code would be a second definition to drift.
    """

    model_config = ConfigDict(extra="forbid")

    lane: str = Field(min_length=1, max_length=100)
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

    # Each ``*_cited`` half against the total it is cited out of. Every row here
    # is read as a ratio — the docs say "two of forty structural leads" — so a
    # numerator over its denominator is not a large number, it is a number with
    # no meaning. The one way to produce one is to count something outside the
    # model as cited, which is what :func:`~analysis_service.coverage.
    # cited_element_ids` resolves its references to prevent; this is that
    # guarantee stated where a reader of the schema can see it.
    _RATIOS: ClassVar[tuple[tuple[str, str], ...]] = (
        ("candidates_cited", "candidates"),
        ("elements_cited", "elements"),
        ("boundary_crossings_cited", "boundary_crossings"),
        ("unknown_controls_cited", "unknown_controls"),
        ("rules_fired", "rules"),
    )

    @model_validator(mode="after")
    def _check_ratios(self) -> Self:
        over = [
            f"{cited}={getattr(self, cited)} exceeds {total}={getattr(self, total)}"
            for cited, total in self._RATIOS
            if getattr(self, cited) > getattr(self, total)
        ]
        if over:
            raise ValueError(
                f"lane {self.lane!r} coverage counts more cited than offered:"
                f" {'; '.join(over)}"
            )
        return self


def build_block_summary(
    claims: Sequence[RuledClaim], rejected_claims: Sequence[RuledClaim]
) -> BlockSummary:
    """The neutral counts, mechanically from one block's own contents.

    A package that narrows :class:`BlockSummary` narrows this too, and the
    block's own check is what holds the two together: a summary that disagrees
    with the claims beside it fails validation rather than being served.
    """
    return BlockSummary(
        claim_count=len(claims),
        needs_info_count=sum(
            1 for claim in claims if claim.verdict.status == "needs-info"
        ),
        rejected_count=len(rejected_claims),
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


class FrameworkAnalysis(BaseModel):
    """What one framework produced, against the envelope's shared model.

    **A field sits where the thing it describes sits**, and that one rule sorted
    every field of the flat schema this replaced. Nine described the job or the
    shared model and stayed on the envelope; the eight here describe one
    framework's output and moved. ``analysis_context`` split on the same rule —
    the instruction digest describes the built graph and the domain packs
    describe the model, so both stayed, while ``fired_rules`` names *this*
    package's **Candidate** rules and ``knowledge_docs`` names what those rules
    retrieved, so both are here.

    **Self-contained against the shared model, never against a second copy of
    it.** Every element ID a claim here references resolves in the envelope's
    one ``system_model``. That is why the alternative — one whole report per
    framework — was refused: it duplicates nine fields including the largest
    block after the findings, and nothing could check that two embedded copies
    of one model agreed.

    ``disclaimer`` is the **package's**, not the service's. The envelope's says
    what the service is; this one says what this framework's claims assert, and
    the two are different sentences the moment a report carries a framework that
    rules on requirement applicability rather than on attacks. It comes from
    ``frameworks/<name>/disclaimer.md``.

    A consumer that does not know a framework reads the base claim — an ID, the
    pair, a title, a description, the elements and the grounds. That is the
    honest outcome, and it is what the viewer's fallback card renders.
    """

    model_config = ConfigDict(extra="forbid")

    framework: FrameworkName
    framework_version: str = Field(min_length=1, max_length=100)
    disclaimer: str = Field(min_length=1, max_length=2000)
    # ``SerializeAsAny`` on the three fields a package may narrow. Pydantic
    # serializes by the *declared* type, so without it a block that narrowed
    # ``claims`` to its own record would serialize back out as the neutral base
    # — dropping exactly the fields the narrowing exists for, silently, on the
    # one path that matters. See :class:`Report.analyses` for the whole of this
    # rule; it is stated in both places because either one alone would lose it.
    claims: list[SerializeAsAny[RuledClaim]] = Field(default_factory=list)
    rejected_claims: list[SerializeAsAny[RuledClaim]] = Field(default_factory=list)
    scope: list[ScopeEntry] = Field(default_factory=list)
    coverage: list[LaneCoverage] = Field(default_factory=list)
    unverified_grounds: list[UnverifiedGround] = Field(default_factory=list)
    #: See :attr:`AnalysisMarks.unreconciled_rulings`; the marks are flattened
    #: onto the block one field each.
    unreconciled_rulings: list[str] = Field(default_factory=list)
    repaired_quotes: list[RepairedQuote] = Field(default_factory=list)
    unresolved_references: list[UnresolvedReference] = Field(default_factory=list)
    unresolved_mentions: list[UnresolvedMention] = Field(default_factory=list)
    unresolved_evidence: list[UnresolvedEvidence] = Field(default_factory=list)
    unknown_claim_identities: list[UnknownClaimIdentity] = Field(default_factory=list)
    dropped_claims: list[DroppedClaim] = Field(default_factory=list)
    fired_rules: list[str] = Field(default_factory=list)
    knowledge_docs: list[str] = Field(default_factory=list)
    summary: SerializeAsAny[BlockSummary]

    def all_claims(self) -> tuple[RuledClaim, ...]:
        """Both arrays, for the checks that do not care which one a claim is in."""
        return (*self.claims, *self.rejected_claims)

    @classmethod
    def summarize(
        cls, claims: Sequence[RuledClaim], rejected_claims: Sequence[RuledClaim]
    ) -> BlockSummary:
        """This block's summary, computed from the claims that will fill it.

        On the block type rather than in a registry, because the summary a
        package builds and the summary its block *declares* are one decision: a
        package that narrows :class:`BlockSummary` overrides this beside the
        field it narrowed, so the fan-in that fills a block reaches the right
        builder by holding the block type and nothing else.
        """
        return build_block_summary(claims, rejected_claims)

    @classmethod
    def scope_entries(
        cls,
        *,
        lanes: Sequence[str],
        claims: Sequence[RuledClaim],
        options: Mapping[str, Any],
        refusal_reason: str,
        deferred: Mapping[str, str] = MappingProxyType({}),
        ruled_out: Mapping[str, str] = MappingProxyType({}),
    ) -> list[ScopeEntry]:
        """What this framework considered and raised no claim about.

        Beside :meth:`summarize` and for the same reason: a package that answers
        in its own units overrides this next to the field it fills, and the
        fan-in that fills a block reaches the right builder by holding the block
        type and nothing else.

        **The neutral unit is the lane**, because a lane is the only unit of a
        framework this service knows without reading a catalog it does not own.
        So the base answers nothing while the framework runs, and answers one
        entry per lane when its **Precondition** refuses. A package whose own
        units are finer — a requirement set — answers in those instead.

        ``refusal_reason`` is empty when the precondition let the lanes run.
        ``options`` is the job's own selection for this framework, as the input
        ladder validated it.

        ``ruled_out`` maps a unit the package's own rules ruled out of this
        model to the reason, from :meth:`Claim.ruled_out`, and each becomes a
        ``not-applicable`` entry. ``deferred`` maps a unit this job could not
        settle to the reason, from :meth:`Claim.partition_proposals`. Empty for a package that defers
        nothing, which is every package whose claims rest on the system's own
        shape. It arrives here rather than being recomputed because the fan-in
        is where the proposals were, and a second derivation could disagree with
        the first.
        """
        del claims, options
        if not refusal_reason:
            return [
                *(
                    ScopeEntry(unit=unit, state="not-applicable", reason=reason)
                    for unit, reason in ruled_out.items()
                ),
                *(
                    ScopeEntry(
                        unit=unit,
                        state="needs-other-evidence",
                        reason=f"applies, and settling it needs {kind}",
                        needs=kind,
                    )
                    for unit, kind in deferred.items()
                ),
            ]
        return [
            ScopeEntry(unit=lane, state="not-applicable", reason=refusal_reason)
            for lane in lanes
        ]

    def block_issues(self, known_element_ids: Collection[str]) -> list[str]:
        """Everything wrong with this block, given the envelope's element IDs.

        **Written once over the neutral base and run per block.** These are the
        checks a second framework's output has to satisfy too, so they read
        :class:`Claim` and :class:`Verdict` and nothing a package declares. A
        package that narrows ``claims`` narrows what these run over; it does not
        get to opt out of them, and it cannot forget to run them, because the
        envelope calls this for every block it carries.

        The model itself is not passed in — one model serves N blocks, so the
        envelope resolves it once and hands down the ID set.
        """
        return [
            *self._verdict_placement_issues(),
            *self._reference_issues(known_element_ids),
            *self._identity_issues(),
            *self._unverified_mark_issues(),
            *self._claim_mark_issues(
                self.unresolved_references, "unresolved reference"
            ),
            *self._claim_mark_issues(self.unresolved_mentions, "unresolved mention"),
            *self._claim_mark_issues(self.unresolved_evidence, "unresolved evidence"),
            *self._summary_issues(),
            *self._scope_issues(),
        ]

    def _verdict_placement_issues(self) -> list[str]:
        issues = [
            f"claim {claim.id!r} has a rejected verdict but sits in claims;"
            " it belongs in rejected_claims"
            for claim in self.claims
            if claim.verdict.status == "rejected"
        ]
        issues += [
            f"claim {claim.id!r} sits in rejected_claims but its verdict is"
            f" {claim.verdict.status!r}"
            for claim in self.rejected_claims
            if claim.verdict.status != "rejected"
        ]
        return issues

    def _reference_issues(self, known_element_ids: Collection[str]) -> list[str]:
        """Every element ID this block names resolves in the shared model."""
        known = set(known_element_ids)
        issues = []
        for claim in self.all_claims():
            refs = [
                *claim.affected_element_ids,
                # Only the model-reference spelling names an element. A
                # ``subject`` states a question about something the model has
                # no slot for, so there is nothing here to resolve and an empty
                # ``element_id`` is the shape rather than a dangling reference.
                *(
                    ref.element_id
                    for ref in claim.verdict.related_unknowns
                    if ref.names_an_element
                ),
            ]
            issues += [
                f"claim {claim.id!r} references element {ref!r}, which is"
                " not in the embedded system model"
                for ref in refs
                if ref not in known
            ]
        return issues

    def _identity_issues(self) -> list[str]:
        """IDs unique **within the block**, and every claim of this framework.

        Uniqueness stops at the block on purpose. #163 ruled that ``id`` has no
        shared grammar, so two packages composing ``1.2.5`` and ``1.2.5`` for
        unrelated things is legal — and every mark that points at a claim now
        lives in the same block as the claim it points at, so nothing resolves
        an ID across a boundary.

        The ``(framework, version)`` check is the block refusing to disagree with
        itself, and it is the per-framework equivalent of the crossings rule on
        the envelope: a claim stamped with another framework's pair reached the
        wrong block, and the pair is exactly what a reader uses to interpret the
        ID beside it.
        """
        issues = [
            f"claim ID {claim_id!r} appears more than once in the"
            f" {self.framework} analysis"
            for claim_id, count in Counter(
                claim.id for claim in self.all_claims()
            ).items()
            if count > 1
        ]
        issues += [
            f"claim {claim.id!r} carries"
            f" ({claim.framework!r}, {claim.framework_version!r}) in the"
            f" ({self.framework!r}, {self.framework_version!r}) analysis"
            for claim in self.all_claims()
            if (claim.framework, claim.framework_version)
            != (self.framework, self.framework_version)
        ]
        return issues

    def _unverified_mark_issues(self) -> list[str]:
        """Every indexed mark points at a grounds entry this block carries.

        A mark naming a claim or an index that is not here is worse than no
        mark: the viewer drops the quotation marks off nothing, and the entry
        the service actually failed to verify renders as though it had passed.
        A repaired quote is checked the same way, for the same reason: the
        agent's words would be shown beside the wrong entry.
        """
        grounds_count = {claim.id: len(claim.grounds) for claim in self.all_claims()}
        issues = []
        indexed: list[tuple[str, UnverifiedGround | RepairedQuote]] = [
            *(("unverified ground", m) for m in self.unverified_grounds),
            *(("repaired quote", m) for m in self.repaired_quotes),
        ]
        for what, mark in indexed:
            count = grounds_count.get(mark.claim_id)
            if count is None:
                issues.append(
                    f"{what} names claim {mark.claim_id!r}, which"
                    f" is not in the {self.framework} analysis"
                )
            elif mark.index >= count:
                issues.append(
                    f"{what} names index {mark.index} on claim"
                    f" {mark.claim_id!r}, which carries {count} grounds"
                )
        return issues

    def _claim_mark_issues(self, marks: Iterable[ClaimMark], what: str) -> list[str]:
        """Every claim-level mark points at a claim this block carries.

        Same rule as the unverified marks, and same reason: a mark on a claim
        that is not here annotates nothing, while the claim that really earned
        it renders as though it had checked out. Shared across every mark type
        because the rule is the mark's *shape* — it names one claim and the rest
        of its fields say nothing about placement — rather than anything about
        what each one records. A mark type added to a block joins
        :data:`ClaimMark` and this call list together, or it carries a claim
        about a claim that nothing checks.
        """
        claim_ids = {claim.id for claim in self.all_claims()}
        return [
            f"{what} names claim {mark.claim_id!r}, which is not in the"
            f" {self.framework} analysis"
            for mark in marks
            if mark.claim_id not in claim_ids
        ]

    def _summary_issues(self) -> list[str]:
        """The summary is the block's own contents, recounted.

        Compared field by field against the neutral recount rather than by
        equality of the whole object, so a package's narrowed summary is held to
        the three counts every summary carries and keeps its own extra fields to
        check for itself.
        """
        recount = build_block_summary(self.claims, self.rejected_claims)
        return [
            f"summary.{field}={getattr(self.summary, field)} does not match the"
            f" {self.framework} analysis's own contents ({getattr(recount, field)})"
            for field in BlockSummary.model_fields
            if getattr(self.summary, field) != getattr(recount, field)
        ]

    def _scope_issues(self) -> list[str]:
        """No claim names a unit this block's own scope list ruled out.

        A framework that reported a requirement non-applicable and then raised a
        claim about it has contradicted itself inside one payload, and a reader
        has no way to choose which half to believe.
        """
        excluded = {
            entry.unit for entry in self.scope if entry.state == "not-applicable"
        }
        return [
            f"claim {claim.id!r} is about a unit the {self.framework} scope list"
            " rules non-applicable"
            for claim in self.all_claims()
            if claim.id in excluded
        ]


class Report(BaseModel):
    """The complete payload the front-end retrieves for a finished job.

    One job's facts, one **Valid System Model**, and one
    :class:`FrameworkAnalysis` per framework the job selected.

    Self-containment is enforced, not assumed. On the envelope: the boundary
    crossings are exactly the ones derived from the embedded model,
    ``elements_analyzed`` is that model's own element count, and the blocks are
    the frameworks the job asked for — in order, with none dropped and none
    repeated. Inside each block, :meth:`FrameworkAnalysis.block_issues` runs the
    neutral claim checks against the shared model.

    **A list, not a map.** ``analyses`` is ordered by the job's own selection
    order. A map keyed by name gives uniqueness free, but then the key and the
    block's own ``framework`` field can disagree, and a dropped framework is
    invisible. A list plus one check catches both, and silently dropping a
    framework the caller paid for is the failure this rules out.

    **The blocks are not merged, and nothing here merges them.** The relation an
    analyst wants between two frameworks' output is *these findings touch one
    element*, and the **Element ID** is the join key both blocks already cite --
    so a reader joins on it with no model pass, which is what *deterministic
    code, models for judgement* asks for. That is why no cross-framework critic
    node exists to merge them instead.
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
    # node's execution_fingerprint is recomputable from its two routes and its
    # tier's entry here, together with ``analysis_context`` and ``execution``. Empty only on reports with no LLM provenance at all —
    # the stub runner's. An eval report carries this block like any other: a
    # sweep's fingerprints are evidence, and evidence nobody can recompute from
    # the artifact is an assertion.
    sampling: dict[str, dict[str, SamplingValue]] = Field(default_factory=dict)
    system_model: SystemModel
    boundary_crossings: list[BoundaryCrossing]
    # Elements of different types sharing one name slug — a suspicion about the
    # model rather than a fault in it, which is why it rides here and not in
    # the validity gate. The one mark that stays on the envelope: it annotates
    # the shared model rather than any framework's claims. Recomputable from
    # ``system_model`` by design.
    shared_element_names: list[SharedElementName] = Field(default_factory=list)
    # A fact about the shared model, so it is a scalar here rather than a field
    # each block copies: N copies would be N chances to disagree about one
    # number. It left ``Summary`` in this cutover for exactly that reason.
    elements_analyzed: int = Field(default=0, ge=0)
    # What was in front of the agents that is *not* one framework's: the
    # instruction digest of the built graph, and the domain packs this job's
    # model earned. Context, not evidence — see :class:`AnalysisContext`. The
    # per-framework halves live in each block. ``None`` on a report built
    # without it (the stub runner's), which is the same absence an empty
    # ``coverage`` records rather than a claim that nothing informed the run.
    analysis_context: AnalysisContext | None = None
    # What ran the graph, as distinct from what was in front of it: the identity
    # schema version, how far the served builds can be trusted, and the versions
    # of the distributions between a node and its provider. Every value here is
    # inside each node's ``execution_fingerprint``, so this block is what makes
    # a fingerprint recomputable from the artifact rather than from the machine
    # that happens to be reading it. ``None`` on a report built without LLM
    # provenance at all — the stub runner's — which is the same absence an empty
    # ``sampling`` records.
    execution: ExecutionEnvelope | None = None
    # **Declared as the base and serialized as itself.** The declaration is what
    # lets this module carry an envelope a framework it has never heard of fits
    # into; ``SerializeAsAny`` is what stops that generality from *costing* the
    # narrowing on the way out. Pydantic serializes a field by its declared
    # type, so without this a ``StrideAnalysis`` in this list would come back
    # out as a ``FrameworkAnalysis``: every severity, every category and both
    # summary breakdowns dropped, with nothing raised and nothing logged — on
    # the report route, which is the only path a caller ever sees.
    #
    # It is the exact mirror of :meth:`_dispatch_blocks`. That validator makes
    # the narrowing survive the way in; this makes it survive the way out. A
    # round trip through JSON is the property both exist for, and it is a
    # property only the pair has.
    analyses: list[SerializeAsAny[FrameworkAnalysis]] = Field(default_factory=list)

    @field_validator("analyses", mode="before")
    @classmethod
    def _dispatch_blocks(cls, value: Any) -> Any:
        """Validate each block as the type its own framework registered.

        The field is declared as the neutral base, because the envelope is what
        a second framework has to fit into without this module knowing its
        name. What makes the *narrowing* survive a round trip is this: a block
        naming ``stride`` is validated as STRIDE's own block type, so its
        severities, its categories and its two breakdowns come back typed rather
        than being refused as extra fields on the base.

        The registry import is function-local on purpose. ``frameworks`` imports
        this module for :class:`Claim`, so a module-level import would cycle;
        by the time a report is validated both modules are fully imported, and a
        build with no packages registered simply reads every block as the base.

        A block naming a framework this build does not carry also reads as the
        base, which is the honest outcome rather than a refusal: the neutral
        fields are exactly what such a reader can still be held to.
        """
        if not isinstance(value, list):
            return value
        from analysis_service.frameworks import block_type_for

        dispatched = []
        for item in value:
            if isinstance(item, FrameworkAnalysis) or not isinstance(item, dict):
                dispatched.append(item)
                continue
            block_type = block_type_for(item.get("framework", ""))
            dispatched.append(block_type.model_validate(item) if block_type else item)
        return dispatched

    @model_validator(mode="after")
    def _check_self_contained(self) -> Self:
        issues = self._envelope_issues()
        known_ids = [element.id for element in self.system_model.elements()]
        for block in self.analyses:
            issues += block.block_issues(known_ids)
        if issues:
            raise ValueError("; ".join(issues))
        return self

    def _envelope_issues(self) -> list[str]:
        issues = []
        if self.boundary_crossings != self.system_model.boundary_crossings():
            issues.append(
                "boundary_crossings do not match the crossings derived from"
                " the embedded system model"
            )
        element_count = len(self.system_model.elements())
        if self.elements_analyzed != element_count:
            issues.append(
                f"elements_analyzed={self.elements_analyzed} does not match the"
                f" embedded system model's {element_count} elements"
            )
        return issues + self._selection_issues()

    def _selection_issues(self) -> list[str]:
        """The blocks are the job's frameworks, in order, once each.

        This is the envelope's version of "no partial report": a framework that
        produced nothing cannot be dropped quietly, because a caller who named
        two and reads one has no field that says which half is missing. Order is
        checked as well as membership, since ``analyses`` is a list precisely so
        that a dropped entry is visible.
        """
        asked = [selection.name for selection in self.job.frameworks]
        answered = [block.framework for block in self.analyses]
        if answered == asked:
            return []
        return [f"analyses answer {answered!r}, but the job selected {asked!r}"]
