"""A source-backed assertion: one scoped statement, with the span that supports it.

The **System Model** holds one string per security-relevant attribute, and every
rule reads its leading token. `docs/research/assertion-readers.md` measures what
that costs: across the 13 blessed models, **ten of the 21 stated mechanism values
state an absence, and the evidence catalog offers nothing for any of the ten**.
"no MFA", "never rotated", "dataset-wide grant with no column-level restriction"
— each reads as a present control, because a present control is all the field
can say.

This module is the catalog those facts belong in. An **Assertion** says that a
**Source** stated something about a **Subject**, in one **Predicate**, at one
**Scope**, and it carries the span of the source that says so. It never says the
deployed system behaves that way. Source-backed is not verified.

[ADR 0034](../../docs/adr/0034-an-assertion-is-a-scoped-fact-with-a-support-span.md)
rules the contract. Five of its rules are structural here rather than written
down for somebody to remember:

**An unknown is a row.** Every predicate admits :data:`ABSENT` and
:data:`UNKNOWN` beside its own vocabulary, and an ``unknown`` carries a
:data:`UnknownReason`. So a bound that stops the work writes ``truncated``
rather than dropping a fact, and absence from the catalog means a predicate
nobody asked about — never an absent control.

**A conflict is derived.** :func:`conflicts` reads the rows. Nothing stores a
conflict record, because a stored record and the rows behind it are two readers
of one rule.

**Code locates a span.** :func:`support_span` is the only way to build one:
:mod:`analysis_service.grounding` decides whether the quote is in the source and
where, and this records the offsets it found. A model proposes words. It never
states an offset.

**A basis names what it requires.** ``stated`` needs a span, ``inferred`` needs
premises, ``derived`` needs a rule, and ``legacy`` is never support for
anything. :func:`catalog_issues` is where each is checked.

**No model judge.** :attr:`Assertion.assessment` starts at ``unchecked`` and only
a reader measured against independent labels may move it. An extractor's own
word that a value follows from a quote is the claim under test, not the answer.

Scope of this module
====================

The schema, the registry, the identity rule and the deterministic checks.
Nothing produces a catalog yet: extraction emits one in #926 Phase 3, and the
consumers migrate in Phase 4. So the checks here return their own
:class:`CatalogIssue` rather than a
:class:`~analysis_service.validation.ValidationIssue` — ``IssueCode`` is the
closed set of ways an *extraction* is refused, and
``tests/test_prompt_lints.py`` holds every member of it to a rule in
``prompts/extract.md``. A code for a fact the prompt does not yet ask for would
have to be answered by a rule nobody wrote.

Model output is untrusted input (OWASP LLM05). Every field is bounded, the
counts are bounded, no regular expression is compiled from a value, and a check
that cannot be made answers with an issue rather than with a pass.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, NamedTuple, get_args

from pydantic import BaseModel, ConfigDict, Field

from analysis_service.analysis import ABSENT_WORD, control_state
from analysis_service.grounding import (
    IndexedSource,
    fragments,
    index_source,
    locate_quote,
    normalize,
    placements,
    verify_quote,
)
from analysis_service.references import canonical
from analysis_service.sources import text_digest
from analysis_service.system_model import (
    ELEMENT_ID,
    UNKNOWN,
    Assumption,
    DataFlow,
    Element,
    SystemModel,
    TrustBoundary,
    ZonedElement,
    normalize_name,
)
from analysis_service.validation import validate

__all__ = [
    "ABSENT",
    "CATALOG_REFUSALS",
    "GATE_REFUSALS",
    "MAX_ASSERTIONS",
    "MAX_PREMISES",
    "MAX_QUALIFIERS",
    "MAX_QUOTE_CHARS",
    "MAX_SPANS",
    "MAX_SUBJECTS",
    "PROJECTION_EFFECT",
    "PROJECTION_VERSION",
    "QUALIFIED_SPELLING",
    "REGISTRY",
    "REGISTRY_VERSION",
    "REJECTING",
    "SETTLING_REASONS",
    "SPAN_REFUSALS",
    "UNIVERSAL_TERMS",
    "UNPROJECTED",
    "Answer",
    "Assertion",
    "AssertionCatalog",
    "AssertionProposal",
    "AssertionRecord",
    "Basis",
    "CatalogCoverage",
    "CatalogIssue",
    "CatalogIssueCode",
    "CatalogProposal",
    "Conflict",
    "Contradiction",
    "Predicate",
    "Projection",
    "ProjectionReason",
    "Qualifier",
    "QualifierKind",
    "Quarantined",
    "QuoteProposal",
    "SpanSource",
    "Standing",
    "Subject",
    "SubjectType",
    "SupportSpan",
    "UnknownReason",
    "admissible",
    "answer",
    "apply_projection",
    "assertion_id",
    "catalog_coverage",
    "catalog_issues",
    "conflicts",
    "contradiction_issues",
    "contradictions",
    "disputed",
    "offered",
    "project",
    "projected_attribute",
    "projection_fields",
    "qualified",
    "quarantine",
    "referent_type",
    "resolve_catalog",
    "settled",
    "snap_subject",
    "span_source",
    "spans_for",
    "subject_id",
    "support_span",
]

#: The registry's version. It keys every assertion identity, so a bump re-keys
#: the whole catalog by recomputation rather than by a migration. Bumped
#: whenever a predicate is added, removed, re-spelled, or has its value
#: vocabulary, scope requirement or multiplicity changed — each of those
#: changes what a row means, and a reader comparing two runs has to know.
#:
#: Version 3 adds ``represented-by``.
#:
#: Version 4 adds ``data-classification``. A store's classification is a
#: fact the sources state and a framework rule reads, and no predicate
#: carried it — so a route that builds its own graph left the attribute at
#: ``unknown`` and ASVS's classified-store rule had nothing to fire on.
#:
#: Version 5 adds ``origin-verification``. A maintainer sitting read a drafted
#: row as recording a fact no predicate could carry — a receiver that does not
#: check who supplied what it took — and the row was deleted rather than forced
#: into ``destination-verification``, which asks the opposite question, or into
#: ``signature-verification``, which names a signature the source never
#: mentions (#1053).
#:
#: Version 6 adds ``credential-revocation`` and ``credential-lifetime``. Case
#: 08's source says a token is "good for twelve hours and there is no way to
#: pull one back before it expires". ``credential-expiry`` is a closed term and
#: recorded only ``expires``, which reads as a control being present while the
#: two facts that matter — how long the window is, and that nobody can shorten
#: it — had nowhere to go. A maintainer sitting asked for both to be preserved
#: separately rather than folded into the categorical value (#1054).
#:
#: Version 7 makes ``data-classification`` a closed term: ``public``,
#: ``internal`` or ``confidential``, the tiers ``prompts/extract.md`` gives the
#: graph field it projects into. As free text meaning "what kind of data this
#: component holds" it drew a list of contents on every row a reviewer read,
#: which the signed reference, the extraction contract and ASVS's
#: classified-store rule all read as no classification (#926). The same version
#: gives ``network-membership`` the placement rule its graph field already has.
REGISTRY_VERSION = 7

#: The projection's version: which graph attribute each predicate is
#: authoritative for, and what :func:`project` does when the rows do not fit one
#: attribute's single unscoped string.
#:
#: **Its own number, because the projection's rules are not in the registry.**
#: :func:`projection_fields` is read off :data:`REGISTRY`, so a predicate's
#: target moves with a registry bump — but the loss rules live in :func:`project`
#: and have already changed once on their own: version 2 stopped picking between
#: two values and started writing ``unknown`` with a
#: :data:`ProjectionReason` (#937), with no registry change beside it. One
#: number for both would have called that release identical to the one before
#: it.
#:
#: Bumped whenever a predicate's ``projects_into`` moves, or when :func:`project`
#: changes which rows reach an attribute or what it writes when they do not fit.
#: Recorded on :class:`AssertionRecord`, whose docstring holds the reason it is
#: there rather than on the envelope: a job that ran no assertion pass projected
#: nothing, and a version recorded for a projection that never ran is a fact
#: with no consequence.
#:
#: Version 3 stops a declining projection from leaving a definite attribute
#: authoritative (#926). A conflict, a scoped value and a row a reviewer set
#: aside now write a qualified ``unknown`` into the attribute; a ``legacy`` row
#: no longer projects as ``stated``; and two compatible values keep the
#: attribute and are cited as rows instead. :data:`PROJECTION_EFFECT` is the
#: table.
#:
#: Version 5 keeps an unchecked row from closing a lead: where the attribute
#: reads unknown or absent, a stated value from rows nobody marked
#: ``supported`` writes a qualified ``unknown`` rather than the control
#: (:func:`_unchecked_over_lead`).
#:
#: Version 4 separates a hedge from silence. Rows that all read ``unknown``
#: left the attribute alone whatever their reason; a source that *said* it was
#: unsure now writes a qualified ``unknown`` over a definite extracted value,
#: and silence still does not (#926).
PROJECTION_VERSION = 5

#: The value that says a source stated this fact is **not there**. A positive
#: statement about an absence, which :attr:`Assertion.basis` then attributes:
#: ``stated`` is the source saying no, ``inferred`` is this service saying so.
ABSENT = "absent"

#: Every predicate admits these two beside its own vocabulary. ``UNKNOWN`` is
#: the same sentinel the System Model uses, imported rather than respelled.
UNIVERSAL_TERMS: frozenset[str] = frozenset({ABSENT, UNKNOWN})

#: What an assertion is about. ``component``, ``interaction`` and ``zone`` are
#: the graph's, and such a subject's ID **is** an **Element ID** — the binding
#: is the identity rather than a second field beside it, so the two can never
#: disagree. The other three are this layer's own, so a shopper account class
#: carries a fact without an invented Process to hold it.
SubjectType = Literal[
    "component",
    "interaction",
    "zone",
    "principal",
    "credential",
    "artifact",
]

#: What a **Scope** qualifier says. Closed, because a reader routes on it.
QualifierKind = Literal[
    "principal", "operation", "resource", "environment", "condition"
]

#: Where an assertion's value comes from. ``legacy`` is an attribute imported
#: from an artifact written before this layer: it is never support, whatever
#: quote its element carries.
Basis = Literal["stated", "inferred", "derived", "legacy"]

#: Whether anything checked that the support *supports* the value. It starts at
#: ``unchecked`` and no extractor may move it.
Assessment = Literal["unchecked", "supported", "unsupported", "unresolved"]

#: Why a value is :data:`UNKNOWN`. ``silent`` is a source that does not answer,
#: ``hedged`` is a speaker who voiced uncertainty, ``unmeasured`` is a predicate
#: nobody asked about, and ``truncated`` is a bound that stopped the work. The
#: last is what makes a bound reportable: an overflowing run writes rows it
#: could not fill rather than a model that looks complete.
UnknownReason = Literal["silent", "hedged", "unmeasured", "truncated"]

#: How a predicate's value is read.
ValueKind = Literal["text", "term", "reference"]

#: Whether two values of one predicate, at one subject and scope, can both hold.
Multiplicity = Literal["one", "many"]

#: Which element ID prefixes each graph-bound subject type accepts, read off the
#: element classes so a renamed prefix moves this with it.
SUBJECT_PREFIXES: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "component": frozenset(element.id_prefix for element in get_args(ZonedElement)),
        "interaction": frozenset({DataFlow.id_prefix}),
        "zone": frozenset({TrustBoundary.id_prefix}),
        "principal": frozenset({"principal"}),
        "credential": frozenset({"credential"}),
        "artifact": frozenset({"artifact"}),
    }
)

#: Which element class each ID prefix names, read off the five element classes.
_ELEMENT_TYPES: Mapping[str, type[BaseModel]] = MappingProxyType(
    {element_type.id_prefix: element_type for element_type in get_args(Element)}
)

#: Every prefix the element schema uses.
_ELEMENT_PREFIXES: frozenset[str] = frozenset(_ELEMENT_TYPES)

#: The subject types whose ID is an **Element ID**, and whose subject the model
#: must therefore hold. Derived rather than listed again: a type is graph-bound
#: exactly when its prefixes are the schema's.
GRAPH_BOUND: frozenset[str] = frozenset(
    subject_type
    for subject_type, prefixes in SUBJECT_PREFIXES.items()
    if prefixes <= _ELEMENT_PREFIXES
)

# --- Bounds ----------------------------------------------------------------
#
# Blast-radius guards rather than quality thresholds, in the shape
# ``validation.MAX_ELEMENTS`` already uses: every artifact downstream scales
# with these counts, and a catalog is model output. Where the model schema's
# own ``max_length`` would answer, a gate rule answers instead, so the message
# names the count a writer can act on.

#: The most assertions one catalog may hold. A model of 150 elements — the
#: element cap — at three assertions each, with room for the principals and
#: credentials the graph has no node for. Awaiting evidence, like the element
#: cap: what a real extraction emits is #926 Phase 3's measurement.
MAX_ASSERTIONS = 500

#: The most subjects one catalog may hold: the element cap, plus this layer's
#: own subjects at the same order again.
MAX_SUBJECTS = 300

#: The most support spans one assertion may carry. A discontiguous quote takes
#: one span per fragment, and a statement resting on eight separate spans of the
#: submission is a summary rather than a statement.
MAX_SPANS = 8

#: The most premises one inferred assertion may name.
MAX_PREMISES = 8

#: The most qualifiers one scope may carry, one per :data:`QualifierKind` and
#: one over.
MAX_QUALIFIERS = 6

#: What a value may run to, matching the control attributes it projects into.
MAX_VALUE_CHARS = 200

#: What a quote may run to, matching a **Source Excerpt**.
MAX_QUOTE_CHARS = 1000


@dataclass(frozen=True)
class Predicate:
    """One registered predicate: what it means, and what a legal row looks like.

    ``meaning`` is the definition, held here rather than in a prompt or a guide
    so that schema validation, the consumers and the evaluation coverage all
    read one sentence. ``projects_into`` names the graph field this predicate
    is authoritative for, or ``""`` where the graph has no field — which is the
    majority of them, and :data:`UNPROJECTED` is the set rather than a count
    written here. A count in this sentence went stale the day the registry
    grew, and nothing read it (#941).
    """

    meaning: str
    subjects: frozenset[SubjectType]
    value: ValueKind
    terms: frozenset[str] = frozenset()
    #: What a ``reference`` value names. Exactly one subject type, never two:
    #: the model writes a name and code builds the subject ID from it, so a
    #: predicate that admitted two referent types would leave code guessing
    #: which one a name meant. ``tests/test_assertions.py`` holds it to one.
    refers_to: frozenset[SubjectType] = frozenset()
    requires: tuple[str, ...] = ()
    multiplicity: Multiplicity = "one"
    projects_into: str = ""
    #: Whether the sources must *state* this predicate's value, so an inference
    #: is refused rather than recorded.
    #:
    #: **For a predicate whose value is an identification.** Most predicates
    #: describe a subject, and this service inferring one is a conclusion a
    #: reader can weigh by its ``basis``. A predicate that says *this subject
    #: is that element* is different: a wrong one does not produce a weak
    #: fact, it moves every fact about the subject onto the wrong element, and
    #: a reader has no way to see that it moved. A ``stated`` row with a real
    #: value already needs a span the gate verifies against the source text,
    #: so requiring the basis is what puts the identification behind a quote.
    stated_only: bool = False

    def admits(self, value: str, known_subjects: Collection[str]) -> bool:
        """Whether ``value`` is legal for this predicate.

        :data:`UNIVERSAL_TERMS` first, because both are legal for every
        predicate whatever its value kind. Then the kind: a ``term`` must be in
        the vocabulary, a ``reference`` must name a subject the catalog holds,
        and ``text`` is anything the field's bound admits.
        """
        if value in UNIVERSAL_TERMS:
            return True
        if self.value == "term":
            return value in self.terms
        if self.value == "reference":
            return value in known_subjects
        return bool(value.strip())


#: The predicates this service registers, and the whole of what extraction may
#: assert. A fact outside this table stays source material: extraction does not
#: invent a predicate and does not force a fact into the nearest wrong one.
#:
#: **The table is the service's, and a **Framework Package** cannot extend it**
#: (ADR 0010's three tests, applied in ADR 0034). A predicate is a fact about
#: the submitted sources rather than about a method, so every lane of every
#: framework reads the same rows.
REGISTRY: Mapping[str, Predicate] = MappingProxyType(
    {
        "authentication-mechanism": Predicate(
            meaning="how the initiator of this interaction proves who it is",
            subjects=frozenset({"interaction", "component"}),
            value="text",
            multiplicity="many",
            projects_into="authentication",
        ),
        "credential-presented": Predicate(
            meaning="what the initiator presents: a cookie, a token, a key",
            subjects=frozenset({"interaction", "principal"}),
            value="reference",
            refers_to=frozenset({"credential"}),
            multiplicity="many",
            projects_into="authentication",
        ),
        "mfa-requirement": Predicate(
            meaning="whether a second factor is required to authenticate",
            subjects=frozenset({"interaction", "principal"}),
            value="term",
            terms=frozenset({"required"}),
        ),
        "authorization-grant": Predicate(
            meaning="what this principal may do, to which resource",
            subjects=frozenset({"principal"}),
            value="text",
            requires=("resource", "operation"),
            multiplicity="many",
        ),
        "credential-sharing": Predicate(
            meaning="how many principals hold this credential",
            subjects=frozenset({"credential"}),
            value="term",
            terms=frozenset({"per-principal", "shared"}),
        ),
        "credential-rotation": Predicate(
            meaning="whether this credential is replaced after it is issued",
            subjects=frozenset({"credential"}),
            value="term",
            terms=frozenset({"rotated", "not-rotated"}),
        ),
        "credential-custody": Predicate(
            meaning="where this credential is kept",
            subjects=frozenset({"credential"}),
            value="text",
        ),
        "credential-expiry": Predicate(
            meaning="whether this credential stops working on its own",
            subjects=frozenset({"credential"}),
            value="term",
            terms=frozenset({"expires", "does-not-expire"}),
        ),
        # The two facts ``credential-expiry`` cannot hold. Its closed terms say
        # only whether the credential stops working on its own, and a source
        # that gives a window says more than that: how wide the window is, and
        # whether anybody can close it early. A twelve-hour token nobody can
        # withdraw is a twelve-hour compromise, and ``expires`` alone reads as a
        # control being present (#1054).
        "credential-lifetime": Predicate(
            meaning="how long this credential works before it expires",
            subjects=frozenset({"credential"}),
            value="text",
        ),
        "credential-revocation": Predicate(
            meaning="whether this credential can be withdrawn before it expires",
            subjects=frozenset({"credential"}),
            value="term",
            terms=frozenset({"revocable"}),
        ),
        "transport-encryption": Predicate(
            meaning="what protects this interaction's data on the wire",
            subjects=frozenset({"interaction"}),
            value="text",
            projects_into="encryption_in_transit",
        ),
        "storage-encryption": Predicate(
            meaning="what protects this component's data at rest",
            subjects=frozenset({"component"}),
            value="text",
            projects_into="encryption_at_rest",
        ),
        # The tiers ``prompts/extract.md`` defines for ``data_classification``,
        # and ``tests/test_assertions.py`` holds the two lists equal. What a
        # store holds is its description, never its tier: a source that lists
        # the contents supports a tier as an inference, and ASVS's
        # CLASSIFIED_STORE_TEST finds ``confidential`` in the projected field.
        "data-classification": Predicate(
            meaning="which sensitivity tier this component's data is in,"
            " never a list of what it holds",
            subjects=frozenset({"component"}),
            value="term",
            terms=frozenset({"public", "internal", "confidential"}),
            projects_into="data_classification",
        ),
        "signature-verification": Predicate(
            meaning="whether the receiver checks who signed the artifact it took",
            subjects=frozenset({"artifact", "interaction"}),
            value="term",
            terms=frozenset({"verified"}),
        ),
        # The mirror of ``destination-verification``, and not a synonym for
        # ``signature-verification``. A signature is one way to establish an
        # origin and the only one that predicate names; this one asks whether
        # the receiver establishes who supplied the thing at all, by any means
        # — a folder that should match a partner, an account, a source address.
        # Case 03's scheduler "does not check that a file came from the partner
        # whose folder it landed in", which names no signature and is the fact
        # neither other predicate could carry (#1053).
        "origin-verification": Predicate(
            meaning="whether the receiver checks who supplied what it took",
            subjects=frozenset({"interaction"}),
            value="term",
            terms=frozenset({"verified"}),
        ),
        "destination-verification": Predicate(
            meaning="whether the sender checks it is sending to the right place",
            subjects=frozenset({"interaction"}),
            value="term",
            terms=frozenset({"verified"}),
        ),
        "internet-exposure": Predicate(
            meaning="whether this component can be reached from the internet",
            subjects=frozenset({"component"}),
            value="term",
            terms=frozenset({"internet-facing", "internal"}),
            projects_into="exposure",
        ),
        "network-membership": Predicate(
            meaning="which zone a sentence about where this component runs"
            " puts it in; its own name, what reaches it and what it talks to"
            " or sits on place it nowhere",
            subjects=frozenset({"component"}),
            value="reference",
            refers_to=frozenset({"zone"}),
            projects_into="trust_zone",
        ),
        "administrative-authority": Predicate(
            meaning="which component this principal administers",
            subjects=frozenset({"principal"}),
            value="reference",
            refers_to=frozenset({"component"}),
            multiplicity="many",
        ),
        "tenant-ownership": Predicate(
            meaning="which party controls this component",
            subjects=frozenset({"component", "zone"}),
            value="reference",
            refers_to=frozenset({"principal"}),
        ),
        # The one predicate that says a subject *is* an element rather than
        # describing one. It is what lets a fact about a class of accounts
        # reach a rule, which reads elements and nothing else. `stated_only`,
        # because an identification this service guessed would move every fact
        # about that principal onto the wrong element with nothing to show it
        # had moved.
        "represented-by": Predicate(
            meaning="which element of the model stands for this principal,"
            " where the sources say the two are the same thing",
            subjects=frozenset({"principal"}),
            value="reference",
            refers_to=frozenset({"component"}),
            stated_only=True,
        ),
    }
)


class Qualifier(BaseModel):
    """One scope qualifier: who or what an assertion covers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: QualifierKind
    value: str = Field(min_length=1, max_length=MAX_VALUE_CHARS)


class SupportSpan(BaseModel):
    """Where a source says what an assertion says it says.

    ``start`` and ``end`` are half-open Unicode code-point offsets into the
    source's **exact retained text**, and ``source[start:end]`` is the
    submitter's own words. ``digest`` pins the text they were taken from, so a
    changed source is visible rather than silently re-read. ``quote`` is those
    words as the model spelled them: one fragment of a quote that marks a cut,
    never the whole quote, so the gate can hold each span to what its offsets
    hold.

    Built by :func:`support_span` and by nothing else.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_label: str = Field(min_length=1, max_length=200)
    digest: str = Field(min_length=64, max_length=64)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=MAX_QUOTE_CHARS)


class Subject(BaseModel):
    """What assertions are about.

    ``id`` is stable and ``label`` is what a report prints. They are separate
    fields because an artifact that renames a display label must keep its
    assertions: the label is a word somebody chose, and the ID is what every
    row points at.

    There is no alias list. The question aliases answer is whether two runs
    named one thing twice, and ADR 0034 rules that identity is local to one
    artifact and alignment across runs is an explicit step. A field for that
    step belongs with the step.

    A graph-bound subject's ``id`` is the **Element ID** itself, so the binding
    cannot drift from the thing it binds.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(max_length=300, pattern=ELEMENT_ID)
    type: SubjectType
    label: str = Field(min_length=1, max_length=200)


class Assertion(BaseModel):
    """One statement a source makes about one subject.

    It carries no ID field. :func:`assertion_id` computes one from the subject,
    the predicate, the scope and the value, so improving the rule re-keys the
    catalog by recomputation — the property a **Claim** identity already has.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: str = Field(max_length=300, pattern=ELEMENT_ID)
    predicate: str = Field(min_length=1, max_length=60)
    value: str = Field(min_length=1, max_length=MAX_VALUE_CHARS)
    scope: list[Qualifier] = Field(default_factory=list, max_length=MAX_QUALIFIERS)
    basis: Basis
    #: Required when ``value`` is :data:`UNKNOWN`, refused otherwise.
    reason: UnknownReason | None = None
    #: Bounded at :data:`MAX_SPANS` by the gate rather than here, so the rule
    #: has one reader and a row over it is a reported refusal, not a schema
    #: error the resolver would have to pre-empt with a copy of the rule.
    support: list[SupportSpan] = Field(default_factory=list)
    #: The assertion IDs an ``inferred`` value rests on.
    premises: list[str] = Field(default_factory=list, max_length=MAX_PREMISES)
    #: What an ``inferred`` value was inferred from, or what a ``derived`` rule
    #: is. One field, because both answer "why is this value here".
    explanation: str = Field(default="", max_length=1000)
    #: Whether the source states that **no other subject** holds this predicate
    #: at this value. "The only thing we expose to the internet", "the one
    #: encrypted link". It is what lets a reader close the world for one
    #: predicate: without it, every subject the catalog is silent about stays
    #: unknown, which is the honest default and also the useless one when a
    #: submitter has told you the set is complete.
    #:
    #: A claim, so it needs a source: the gate refuses it on a row that is not
    #: ``stated`` or carries no span.
    exclusive: bool = False
    #: Whether somebody checked that the spans hold what this row says, and
    #: who. :func:`settled` reads it: ``unsupported`` and ``unresolved`` set a
    #: row aside, so this field decides whether a lane may rest on the fact.
    #:
    #: **Nothing in this service writes anything but ``unchecked``.** An
    #: extractor's own confidence is not an assessment (ADR 0034), and no
    #: sitting imports one yet, so every value here is the default.
    #:
    #: **Changed text takes the row with it.** An assessment is a judgement
    #: about particular source text, and the text it was made against is pinned
    #: only on the row's ``support`` spans, by ``SupportSpan.digest``. The gate
    #: reports text that has moved as ``stale-digest`` and
    #: :meth:`AssertionRecord.over` quarantines the row, so neither an
    #: unchecked row nor an assessed one is read on evidence that no longer
    #: says it. What stays open is *keeping* an assessment across an edit that
    #: left its spans intact, which the first producer of one decides.
    assessment: Assessment = "unchecked"
    #: Who assessed the support, and which version of them. Required once
    #: ``assessment`` moves off ``unchecked``.
    assessor: str = Field(default="", max_length=200)


class AssertionCatalog(BaseModel):
    """Every subject and every assertion one extraction produced."""

    model_config = ConfigDict(extra="forbid")

    registry_version: int = REGISTRY_VERSION
    subjects: list[Subject] = Field(default_factory=list)
    entries: list[Assertion] = Field(default_factory=list)


CatalogIssueCode = Literal[
    "wrong-registry-version",
    "too-many-assertions",
    "too-many-subjects",
    "duplicate-subject",
    "duplicate-assertion",
    "subject-type-mismatch",
    "dangling-subject",
    "dangling-binding",
    "unknown-predicate",
    "wrong-subject-type",
    "illegal-value",
    "missing-reason",
    "unwanted-reason",
    "missing-scope",
    "unsupported-assertion",
    "too-many-spans",
    "legacy-with-support",
    "missing-premise",
    "dangling-premise",
    "circular-support",
    "exclusive-without-support",
    "dangling-source",
    "stale-digest",
    "unverifiable-span",
    "ambiguous-span",
    "unassessed-assessor",
    # A predicate whose value identifies rather than describes, carrying a
    # value this service inferred. See `Predicate.stated_only`.
    "inference-refused",
    # Not a refused row. The row stands and stays citable; this says the graph
    # attribute beside it states the opposite, which is the defect this layer
    # was built to make visible rather than one to drop a fact over.
    "graph-contradiction",
    # Not a refused row either. Rows merged into one identity cited more
    # passages between them than a row carries, and the row kept the first
    # :data:`MAX_SPANS`; this names how many it did not keep.
    "support-truncated",
]


#: Every code that refuses a row, which is every code but two.
#:
#: :data:`CatalogIssueCode` holds what a report's ``assertions.issues`` can say.
#: ``graph-contradiction`` is not a refusal: the row stands, stays settled and
#: stays citable, and the finding is about the graph attribute beside it.
#: ``support-truncated`` is not one either: the row stands on the spans it kept,
#: and the finding is about the ones it did not. Spelled as a set rather than
#: left to a reader to remember, because ``tests/test_assertions.py`` holds the
#: gate's fixture table to exactly these, and a new code that is a refusal must
#: fail there rather than quietly join the exception.
GATE_REFUSALS: frozenset[str] = frozenset(get_args(CatalogIssueCode)) - {
    "graph-contradiction",
    "support-truncated",
}

#: The refusals of a whole catalog rather than of a row in it: a catalog keyed
#: by another registry, or one over a count bound. Nothing in such a catalog
#: can be read, so :func:`quarantine` keeps none of it.
CATALOG_REFUSALS: frozenset[str] = frozenset(
    {"wrong-registry-version", "too-many-assertions", "too-many-subjects"}
)

#: The refusals that say a row's **span** did not hold, as against its value,
#: its shape or its subject. A quote that does not locate in the source it
#: names, one that locates in several places, a source the catalog does not
#: carry, and text that has moved under a span already taken from it.
#:
#: **Span validity is a different question from semantic support**, and #926
#: asks for the two apart: a row can cite text that locates exactly and still
#: say something the text does not support, and a reader pooling the two
#: cannot tell a run that quotes badly from one that reasons badly.
#: ``too-many-spans`` is not here — it is a bound on how many spans a row may
#: carry, not a judgement on any one of them — and neither is
#: ``unsupported-assertion``, which says a row cited nothing at all.
SPAN_REFUSALS: frozenset[str] = frozenset(
    {
        "unverifiable-span",
        "ambiguous-span",
        "dangling-source",
        "stale-digest",
    }
)


class CatalogIssue(BaseModel):
    """One structured catalog failure, addressed to the pass that can fix it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: CatalogIssueCode
    message: str
    subject: str | None = None
    assertion: str | None = None
    #: The index of the proposed row this refusal is about, set by
    #: :func:`resolve_catalog` and by nothing else. One row can draw several
    #: refusals, so **rows dropped is the count of distinct values here**, and
    #: a count of issues is a count of reasons (#961).
    row: int | None = None


class Quarantined(BaseModel):
    """One built row the gate removed, and the codes that removed it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: The row's identity, or ``""`` for a row whose predicate the registry
    #: does not hold, which has none.
    identity: str
    codes: list[CatalogIssueCode] = Field(min_length=1)


class AssertionRecord(BaseModel):
    """What one job's assertion pass produced, kept on the **Report**.

    ``catalog`` is what the resolver built and every consumer read, with each
    row's basis and assessment on it. ``issues`` is what the resolver and the
    gate refused, by row and by reason, so a reader of the report can tell a
    fact the sources never stated from one the node proposed and lost. The
    proposal itself is not kept here, for the reason the report keeps no raw
    extraction: ``issues[].row`` counts distinct refused rows without it.

    ``projection_version`` sits here rather than on the catalog, because it is
    not a property of the rows: the catalog says what the sources state, and the
    projection says what this service then wrote into the graph's attributes.
    A reader comparing two reports' projected attributes needs it, and the
    catalog's own ``registry_version`` does not answer it — :func:`project`'s
    loss rules changed in #937 with no registry change beside them. It is here
    and not on :class:`~analysis_service.report.ExecutionEnvelope` for the
    reason the registry version is not: a job that ran no assertion pass
    projected nothing, and a version recorded for a projection that never ran
    is a fact with no consequence.
    """

    model_config = ConfigDict(extra="forbid")

    proposed: int = Field(ge=0)
    catalog: AssertionCatalog
    issues: list[CatalogIssue] = Field(default_factory=list)
    #: Every built row the gate removed from ``catalog``, as :func:`quarantine`
    #: reports it. The issues name a subject where a refusal is about one, and
    #: only this list says which rows that took.
    quarantined: list[Quarantined] = Field(default_factory=list)
    projection_version: int = Field(default=PROJECTION_VERSION, ge=1)

    @classmethod
    def of(
        cls,
        proposal: CatalogProposal,
        model: SystemModel,
        sources: Mapping[str, str],
    ) -> AssertionRecord:
        """Resolve one proposal and run the gate over what was built.

        **The one reader of "what did the node's proposal come to".** The
        production ``prepare`` node, the assertion eval mode and the offline
        replay all ask it, so a resolver change moves every caller at once.
        The gate runs over the resolver's output rather than being assumed:
        the resolver's contract is that its catalog raises nothing, and here
        is where that contract meets real model output.
        """
        catalog, issues = resolve_catalog(proposal, model, sources)
        return cls.over(
            catalog,
            model,
            sources,
            proposed=len(proposal.assertions),
            issues=issues,
        )

    @classmethod
    def over(
        cls,
        catalog: AssertionCatalog,
        model: SystemModel,
        sources: Mapping[str, str],
        *,
        proposed: int,
        issues: Sequence[CatalogIssue] = (),
        quarantined: Sequence[Quarantined] = (),
    ) -> AssertionRecord:
        """One record over a catalog something else built, with the gate run on it.

        **The way a catalog nobody resolved here still answers the gate.** The
        patch applicator merges rows into a catalog and #1003's review route
        hands one to ``prepare``; each reaches a lane through this, so a catalog
        an agent selects from answered the same rules whichever code built it.

        ``issues`` is what its builder already refused, kept in front of what
        the gate says now, so a reader sees a row lost in construction apart
        from a fault in what was built. ``quarantined`` is what an earlier gate
        removed, carried the same way.

        **A refusal quarantines, it does not only report.** The catalog on the
        record is what every consumer reads, so a row the gate refuses leaves
        it here, through :func:`quarantine`, before any reader sees it. Before
        this, a row whose source text had moved under its span was recorded
        ``stale-digest`` and still projected into the graph and cited (#926).
        Dropping a row can strand another — an inference whose premise went —
        so the rows are gated again until the gate refuses nothing. The graph
        comparison runs once, over what survives, because it is not a refusal.
        """
        found: list[CatalogIssue] = []
        removed = list(quarantined)
        admitted = catalog
        while refusals := catalog_issues(admitted, model=model, sources=sources):
            found.extend(refusals)
            admitted, dropped = quarantine(admitted, refusals)
            removed.extend(dropped)
        return cls(
            proposed=proposed,
            catalog=admitted,
            issues=[*issues, *found, *contradiction_issues(admitted, model)],
            quarantined=removed,
        )

    def refused_rows(self) -> int:
        """How many rows were refused, by whichever pass refused them.

        **The one reader of "how many rows did this record lose".** The
        resolver names the proposed row it dropped (:attr:`CatalogIssue.row`),
        so each is counted once however many reasons it drew. The gate's
        removals are counted off :attr:`quarantined`, the rows
        :func:`quarantine` actually took, because a refusal of one subject
        takes every row about it. A refusal of the whole catalog loses every
        proposed row. ``support-truncated`` and ``graph-contradiction`` refuse
        nothing and are not counted.
        """
        refusals = [issue for issue in self.issues if issue.code in GATE_REFUSALS]
        if any(issue.code in CATALOG_REFUSALS for issue in refusals):
            return self.proposed
        rows = {issue.row for issue in refusals if issue.row is not None}
        return len(rows) + len(self.quarantined)


@dataclass(frozen=True)
class Conflict:
    """Assertions that disagree: one subject, one predicate, one scope.

    Derived by :func:`conflicts` and never stored. ``values`` holds what the
    rows say, sorted, so a reader sees the disagreement without resolving it.
    """

    subject: str
    predicate: str
    scope: str
    values: tuple[str, ...]


def subject_id(subject_type: str, name: str) -> str:
    """The deterministic ID a non-graph subject's type and name imply.

    Graph-bound subjects have no call here: their ID is the **Element ID** the
    model already carries, and passing a name would invite a second spelling of
    it.

    **Two things the sources name alike share this ID.** A row's scope keeps
    them apart where it names the difference, such as an environment. Unscoped
    rows that disagree become a conflict, which reaches the lanes as a
    question. Unscoped rows about different facts attach to one subject, and
    nothing can tell them apart: the extraction prompt names such things apart
    instead. ``tests/test_reassess.py`` holds all three.

    **A name may be written as the ID it implies**, ``credential:api-key`` for
    ``API key``, and it names the same subject: the type's own prefix is
    dropped before the slug, so it is never doubled. Another type's prefix is
    part of the name.
    """
    if subject_type in GRAPH_BOUND:
        raise ValueError(
            f"{subject_type!r} is graph-bound; its subject ID is the element ID"
        )
    prefixes = SUBJECT_PREFIXES.get(subject_type)
    if prefixes is None:
        raise ValueError(f"unknown subject type {subject_type!r}")
    prefix = next(iter(prefixes))
    typed, _, rest = name.partition(":")
    if rest and typed.strip().lower() == prefix:
        name = rest
    return f"{prefix}:{normalize_name(name)}"


def assertion_id(assertion: Assertion) -> str:
    """The identity this assertion's four identifying parts imply.

    ``assertion:<predicate>~<subject>~<scope>~<value>``. Two assertions that
    agree on all four are one assertion with two support spans, which is how
    two sources for one fact keep both provenances.

    **The ID is not a cross-run alignment.** Two runs that word one mechanism
    differently produce two IDs. Matching them is an explicit step whose
    unresolved cases stay visible, and no digest can stand in for it.

    A reader resolves an ID by exact lookup, never by splitting it: a subject
    ID carries colons of its own, exactly as an **Evidence Reference** does.

    Raises ``KeyError`` for a predicate the registry does not hold, because
    the value key depends on the predicate's value kind. :func:`catalog_issues`
    reports that case as ``unknown-predicate`` before anything asks for an ID.
    """
    parts = identity_parts(assertion)
    return f"assertion:{parts.predicate}~{parts.subject}~{parts.scope}~{parts.value}"


class IdentityParts(NamedTuple):
    """The four canonical parts :func:`assertion_id` composes, kept apart.

    A reader matching rows across two catalogs asks about the parts one at a
    time — the same subject and predicate at a different value is a
    disagreement, at a different scope a narrower claim — and a composed ID
    answers only whether all four agree. Read from here so the parts a match
    reads are the parts the identity hashes.
    """

    predicate: str
    subject: str
    scope: str
    value: str


def identity_parts(assertion: Assertion) -> IdentityParts:
    """One assertion's four identifying parts, each in its canonical key.

    Raises ``KeyError`` for a predicate the registry does not hold, as
    :func:`assertion_id` does and for the same reason.
    """
    predicate = REGISTRY[assertion.predicate]
    return IdentityParts(
        assertion.predicate,
        assertion.subject,
        _scope_key(assertion.scope),
        _value_key(predicate, assertion.value),
    )


def _scope_key(scope: Collection[Qualifier]) -> str:
    """One scope's canonical key: ``any`` when empty, else a digest of its rows.

    Sorted before it is hashed, so two orderings of one scope are one scope.
    Digested rather than spelled, because a qualifier holds free text a model
    wrote and an ID that carried it would be unbounded.
    """
    if not scope:
        return "any"
    rows = sorted((qualifier.kind, normalize(qualifier.value)) for qualifier in scope)
    return f"s.{_digest(json.dumps(rows, ensure_ascii=False))}"


def _value_key(predicate: Predicate, value: str) -> str:
    """One value's canonical key.

    A term and a reference are already canonical identifiers, so they are the
    key. Free text is not: it is folded through the citation ladder's own
    :func:`~analysis_service.grounding.normalize`, so ``TLS 1.3`` and ``tls
    1.3`` are one value, and then digested so the key is bounded.
    """
    if value in UNIVERSAL_TERMS or predicate.value in ("term", "reference"):
        return value
    return f"v.{_digest(normalize(value))}"


def _digest(text: str) -> str:
    """Twelve hex characters of sha256 — enough to key rows within one artifact."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class SpanSource:
    """One source folded once, ready for every quote taken from it.

    Named apart from :class:`~analysis_service.grounding.PreparedSource`, which
    is the repair scan's own preparation of a source. One word, one meaning.

    A catalog carries many quotes and a job carries few sources, so the fold
    belongs to the source rather than to the quote: :func:`index_source` reads
    every character of the submission, and doing it per quote spends the whole
    submission once for each row. A body building spans prepares each source
    once and hands this to :func:`spans_for`.

    Built by :func:`span_source`, which answers ``None`` for a source whose
    two folds disagree — see :func:`~analysis_service.grounding.index_source`.
    """

    label: str
    digest: str
    indexed: IndexedSource


def span_source(source_label: str, source_text: str) -> SpanSource | None:
    """Fold one source and digest it, once, for every span taken from it."""
    indexed = index_source(source_text)
    if indexed is None:
        return None
    return SpanSource(source_label, text_digest(source_text), indexed)


def support_span(quote: str, source_label: str, source_text: str) -> SupportSpan | None:
    """The span ``quote`` occupies in ``source_text``, or ``None`` if it is absent.

    **The only way to build a span, with :func:`spans_for`.** The offsets come
    from :func:`~analysis_service.grounding.locate_quote`, which reads the same
    matcher :func:`~analysis_service.grounding.verify_quote` reads, so a span
    and a **Ground** can never disagree about one quote.

    The convenience form, for a caller holding one quote and one text. It folds
    the whole source to answer, exactly as
    :meth:`~analysis_service.system_model.SystemModel.get` builds a whole index
    to answer one ID. A caller taking several quotes from one source calls
    :func:`span_source` first, or it spends the submission once per quote.

    A quote marking a cut with ``…`` covers several spans. This answers the
    first, and :func:`spans_for` answers each.
    """
    prepared = span_source(source_label, source_text)
    if prepared is None:
        return None
    spans = spans_for(quote, prepared)
    return spans[0] if spans else None


def spans_for(quote: str, prepared: SpanSource) -> tuple[SupportSpan, ...]:
    """Every span ``quote`` occupies, one per fragment, or empty when absent.

    Each span carries its own fragment, paired with its offsets by the one
    split :func:`~analysis_service.grounding.fragments` makes — ``strict``,
    so the two readers cannot silently drift apart.

    **A quote past :data:`MAX_QUOTE_CHARS` takes no span.** Refused rather than
    cut: a truncated quote beside the offsets of the whole one is a span that
    does not hold what it says it holds, and a citation is bounded but never
    rewritten — the rule a **Source**'s label already follows.
    """
    if len(quote) > MAX_QUOTE_CHARS:
        return ()
    located = locate_quote(quote, prepared.indexed)
    if located is None:
        return ()
    return tuple(
        SupportSpan(
            source_label=prepared.label,
            digest=prepared.digest,
            start=span.start,
            end=span.end,
            quote=fragment,
        )
        for fragment, span in zip(fragments(quote), located, strict=True)
    )


def gate_issues(
    catalog: AssertionCatalog, model: SystemModel, sources: Mapping[str, str]
) -> list[CatalogIssue]:
    """Everything the gate says about one catalog and the graph beside it.

    **The one reader of "does this catalog pass".**
    :meth:`AssertionRecord.over` runs it on what a resolver built and
    :mod:`analysis_service.patch` runs it on what a batch produced, so two
    catalogs reaching one lane answered one set of rules. The two halves are
    different questions — :func:`catalog_issues` reads the rows, and
    :func:`contradiction_issues` reads them against the graph's own attributes
    — and a caller that wants only one asks for it by name.
    """
    return [
        *catalog_issues(catalog, model=model, sources=sources),
        *contradiction_issues(catalog, model),
    ]


def quarantine(
    catalog: AssertionCatalog, refusals: Sequence[CatalogIssue]
) -> tuple[AssertionCatalog, tuple[Quarantined, ...]]:
    """The catalog without every row ``refusals`` names, and each row it removed.

    **The one reader of "which rows does a refusal remove".** A refusal names
    what it is about: a whole catalog (:data:`CATALOG_REFUSALS`), which keeps
    nothing; a row, by its identity; or a subject, which takes every row about
    it or pointing at it, because a row cannot be read without its subject. A
    row whose predicate is unregistered has no identity and goes by that.

    **Fails closed.** Refusals this cannot place on a row — nothing named, or
    naming nothing the catalog holds — would leave the catalog unchanged and
    the refusal unanswered, so the whole catalog goes instead. That also makes
    :meth:`AssertionRecord.over`'s loop end: every round either shrinks the
    catalog or empties it.

    Each removed row carries the codes that named it, or every code of the
    round where the whole catalog goes, so no reader re-derives this rule.
    """
    codes = {issue.code for issue in refusals}
    everything = tuple(
        Quarantined(identity=_identity(entry), codes=sorted(codes))
        for entry in catalog.entries
    )
    if codes & CATALOG_REFUSALS:
        return AssertionCatalog(), everything
    by_row: dict[str, set[CatalogIssueCode]] = {}
    by_subject: dict[str, set[CatalogIssueCode]] = {}
    for issue in refusals:
        if issue.assertion:
            by_row.setdefault(issue.assertion, set()).add(issue.code)
        elif issue.subject and issue.code != "unknown-predicate":
            by_subject.setdefault(issue.subject, set()).add(issue.code)
    kept = []
    removed = []
    for entry in catalog.entries:
        if entry.predicate not in REGISTRY:
            reasons: set[CatalogIssueCode] = {"unknown-predicate"}
        else:
            reasons = (
                by_row.get(assertion_id(entry), set())
                | by_subject.get(entry.subject, set())
                | by_subject.get(entry.value, set())
            )
        if reasons:
            removed.append(
                Quarantined(identity=_identity(entry), codes=sorted(reasons))
            )
        else:
            kept.append(entry)
    labels = {
        subject.id: subject
        for subject in catalog.subjects
        if subject.id not in by_subject
    }
    left = _catalog(kept, labels)
    if left == catalog:
        return AssertionCatalog(), everything
    return left, tuple(removed)


def _identity(entry: Assertion) -> str:
    """The row's identity, or ``""`` where its predicate is unregistered."""
    return assertion_id(entry) if entry.predicate in REGISTRY else ""


def merged(
    held: AssertionCatalog, found: AssertionCatalog
) -> tuple[AssertionCatalog, list[CatalogIssue]]:
    """Two catalogs as one, by the rule :func:`resolve_catalog` already merges by.

    **The one reader of "these rows and those rows are one catalog".** Rows
    that share an identity become one row carrying both spans, through
    :func:`_merge`, so two catalogs stating one fact keep both provenances
    rather than becoming a duplicate the gate refuses.

    The subject table is rebuilt from the rows that survive, never unioned
    blindly: a subject nothing names is a row the caller does not hold, and a
    table carrying it would say the catalog is about something it is silent on.
    ``held``'s spelling of a subject stands where both name one, because the
    caller's existing labels are the ones a report already printed.

    The issues are the ``support-truncated`` cuts :func:`_bounded` made, for
    the caller to record: a merge that crosses the span bound keeps the fact.
    """
    kept: dict[str, Assertion] = {}
    for entry in (*held.entries, *found.entries):
        identity = assertion_id(entry)
        standing = kept.get(identity)
        kept[identity] = entry if standing is None else _merge(standing, entry)
    entries, cut = _bounded(kept.values())
    labels = {subject.id: subject for subject in (*found.subjects, *held.subjects)}
    return _catalog(entries, labels), cut


def without(catalog: AssertionCatalog, identities: Collection[str]) -> AssertionCatalog:
    """The catalog with those rows retracted, and no subject left behind.

    A retraction is how a correction is spelled, because an identity is
    computed from the row's own parts: changing a value makes a different row,
    so a caller drops one identity and adds another rather than editing a row
    in place. Nothing here checks that ``identities`` names anything — a caller
    that wants a stale retraction reported asks :func:`answer` first.
    """
    dropped = frozenset(identities)
    kept = [entry for entry in catalog.entries if assertion_id(entry) not in dropped]
    labels = {subject.id: subject for subject in catalog.subjects}
    return _catalog(kept, labels)


def _catalog(
    entries: list[Assertion], labels: Mapping[str, Subject]
) -> AssertionCatalog:
    """One catalog over ``entries``, carrying the subjects they name.

    **The one reader of which subjects a set of rows declares.** The table is
    derived, for the reason :class:`CatalogProposal` gives: two places to spell
    one subject eventually spell it two ways.

    A row names **two** subjects where its predicate's value is a reference —
    what the row is about, and what it points at. A credential nothing is
    stated about is still a subject, because a row presenting it names it, and
    a table that dropped it would leave that row pointing at nothing.

    A subject ``labels`` does not hold stays out, which the gate then reports
    as ``dangling-subject`` rather than this inventing a label nobody wrote.
    """
    named = set()
    for entry in entries:
        named.add(entry.subject)
        predicate = REGISTRY.get(entry.predicate)
        if predicate is not None and predicate.value == "reference":
            named.add(entry.value)
    named -= UNIVERSAL_TERMS
    subjects = [labels[subject] for subject in sorted(named) if subject in labels]
    return AssertionCatalog(subjects=subjects, entries=entries)


def ambiguous_quote(quote: str, haystack: str) -> bool:
    """Whether ``haystack`` holds ``quote`` in more than one place.

    **The one reader of "does this quote name where it sits".** A submission
    that says one thing twice holds the quote in both places, and offsets into
    the first copy say nothing about which copy the row rests on. The gate asks
    it of a span's fragment and :mod:`analysis_service.factbundle` asks it of a
    mention's citation, so a repeated line is refused the same way whichever
    route proposed it.

    ``haystack`` is the folded text — :attr:`SpanSource.indexed`'s or a
    :class:`_Checked`'s — because that is what
    :func:`~analysis_service.grounding.placements` counts in.
    """
    return placements(quote, haystack) > 1


def conflicts(catalog: AssertionCatalog) -> tuple[Conflict, ...]:
    """Every group of assertions that disagree about one subject and predicate.

    **The one reader of "do these rows contradict each other".** A conflict is
    derived from the rows rather than stored beside them, so nothing can assert
    a disagreement the catalog does not hold, or hide one it does.

    An :data:`UNKNOWN` never conflicts. A row saying nobody stated a fact and a
    row saying somebody did are not two claims about the world; the second
    answers what the first leaves open.

    What counts as a disagreement is the predicate's ``multiplicity``. Where one
    value may hold, two stated values disagree. Where several may hold — a
    connection authenticated by mutual TLS *and* a token — they do not, and only
    :data:`ABSENT` beside a positive value is a contradiction.

    Only :func:`admissible` rows take part. A row a reviewer set aside supports
    nothing, so it cannot dispute a row that stands either: counted here, it
    kept the other side from settling after the review rejected it (ADR 0041).

    Sorted by subject, then predicate, then scope, so two runs over one catalog
    report the same order.
    """
    groups: dict[tuple[str, str, str], dict[str, None]] = {}
    for entry in catalog.entries:
        if not admissible(entry) or entry.value == UNKNOWN:
            continue
        key = (entry.subject, entry.predicate, _scope_key(entry.scope))
        groups.setdefault(key, {})[entry.value] = None

    found = []
    for (subject, predicate, scope), values in sorted(groups.items()):
        stated = tuple(sorted(values))
        if len(stated) < 2:
            continue
        one_only = REGISTRY[predicate].multiplicity == "one"
        if one_only or ABSENT in stated:
            found.append(Conflict(subject, predicate, scope, stated))
    return tuple(found)


def projection_fields() -> Mapping[str, str]:
    """Which graph field each projecting predicate is authoritative for.

    Read off :data:`REGISTRY` rather than listed again. ``tests/test_assertions.py``
    holds every entry to a field a real element class declares, so a predicate
    that projects into a field the schema does not have fails there.
    """
    return MappingProxyType(
        {
            name: predicate.projects_into
            for name, predicate in REGISTRY.items()
            if predicate.projects_into
        }
    )


#: The predicates the graph has no field for, on any element. Read off
#: :data:`REGISTRY`, so a predicate added tomorrow is classified by its field
#: and never listed.
#:
#: **Not the routing rule.** Whether a row reaches a graph field is a property
#: of the row — :func:`projected_attribute` — and whether it is cited as itself
#: is :func:`offered`'s. A predicate outside this set still reaches nothing on
#: an element type without its field, and ADR 0036's first reading, which
#: routed by predicate, left such a row with neither a projection nor an
#: evidence entry (#926).
UNPROJECTED: frozenset[str] = frozenset(
    name for name, predicate in REGISTRY.items() if not predicate.projects_into
)

#: What a catalog holds about one subject under one predicate. ``unasked`` is
#: no row at all; ``unknown`` is rows that all read :data:`UNKNOWN`;
#: ``conflicting`` is a disagreement and nothing settled beside it;
#: ``answered`` is at least one row a consumer may rest on.
Standing = Literal["unasked", "answered", "unknown", "conflicting"]


@dataclass(frozen=True)
class Answer:
    """What the catalog says about one subject under one predicate.

    ``rows`` is every row, whatever its scope, value or assessment.
    ``settled`` is the subset a consumer may rest a definite conclusion on,
    by :func:`settled`'s one rule. ``conflicts`` is the disagreements on this
    subject and predicate, kept visible rather than resolved.

    A scoped row is settled **for its scope** and answers nothing wider: a
    second factor required for administrators says nothing about shoppers,
    and a reader that needs the unscoped fact reads ``scope`` on each row.
    """

    subject: str
    predicate: str
    rows: tuple[Assertion, ...]
    settled: tuple[Assertion, ...]
    conflicts: tuple[Conflict, ...]

    @property
    def standing(self) -> Standing:
        if self.settled:
            return "answered"
        if self.conflicts:
            return "conflicting"
        return "unknown" if self.rows else "unasked"

    def holding(self, value: str) -> tuple[Assertion, ...]:
        """The settled rows whose value is ``value``, at any scope."""
        return tuple(entry for entry in self.settled if entry.value == value)


def disputed(catalog: AssertionCatalog) -> tuple[Assertion, ...]:
    """Every row that would be settled but for a disagreement, in catalog order.

    **The other side of** :func:`settled`: an admissible row holding a value,
    at a subject, predicate and scope where :func:`conflicts` finds another
    admissible row stating something else. Neither side is a fact, and the
    disagreement is itself a question, so the evidence catalog offers each
    side as an open one.
    """
    keys = _disputed_keys(catalog)
    return tuple(
        entry
        for entry in catalog.entries
        if admissible(entry)
        and entry.value != UNKNOWN
        and (entry.subject, entry.predicate, _scope_key(entry.scope)) in keys
    )


def _disputed_keys(catalog: AssertionCatalog) -> frozenset[tuple[str, str, str]]:
    return frozenset(
        (conflict.subject, conflict.predicate, conflict.scope)
        for conflict in conflicts(catalog)
    )


def settled(catalog: AssertionCatalog) -> tuple[Assertion, ...]:
    """Every row a consumer may rest a definite conclusion on, in catalog order.

    **The one reader of "may a consumer treat this row as a fact".** A row is
    settled when it holds a value rather than :data:`UNKNOWN`, its predicate
    is registered, its basis is support of some kind — ``legacy`` is never
    support for anything (ADR 0034 rule 6) — its assessment is one
    :data:`PROJECTS_UNDER` admits, and no other row disagrees with it at its
    subject, predicate and scope. A conflict settles nothing on either side
    until a recorded adjudication does, and an ``unsupported`` or
    ``unresolved`` row is set aside rather than read as a fact.

    ``inferred`` and ``derived`` rows are settled: they are this service's
    conclusions rather than the source's, and every reader labels them by
    ``basis`` rather than dropping them, so an inference is visible as one.
    """
    keys = _disputed_keys(catalog)
    return tuple(
        entry
        for entry in catalog.entries
        if admissible(entry)
        and entry.value != UNKNOWN
        and (entry.subject, entry.predicate, _scope_key(entry.scope)) not in keys
    )


def admissible(entry: Assertion) -> bool:
    """Whether one row, on its own, may state its value to any reader.

    **The one reader of "does this row's basis and assessment count".**
    :func:`settled` asks it before looking for disagreement, and
    :func:`project` asks it before a row may supply a graph attribute, so the
    evidence a lane cites and the attribute a rule reads cannot disagree about
    which rows count. Before this, :func:`project` filtered on the assessment
    alone and wrote a ``legacy`` row into the graph as ``stated`` while
    :func:`settled` refused the same row (#926).

    A row is admissible when its predicate is registered, its basis is support
    of some kind — ``legacy`` never is (ADR 0034 rule 6) — and its assessment is
    one :data:`PROJECTS_UNDER` admits.
    """
    return (
        entry.predicate in REGISTRY
        and entry.basis != "legacy"
        and PROJECTS_UNDER[entry.assessment]
    )


def answer(catalog: AssertionCatalog, subject: str, predicate: str) -> Answer:
    """What ``catalog`` holds about ``subject`` under ``predicate``.

    The typed query every consumer asks instead of walking the rows: a
    consumer that walked them would decide for itself which rows count, and
    two consumers would decide differently. An answer is **per subject and
    per predicate by construction**, so a control on one interaction can
    never suppress an unknown on another, and a mechanism can never imply a
    second factor, a grant or a rotation.
    """
    rows = tuple(
        entry
        for entry in catalog.entries
        if entry.subject == subject and entry.predicate == predicate
    )
    return Answer(
        subject=subject,
        predicate=predicate,
        rows=rows,
        settled=tuple(
            entry
            for entry in settled(catalog)
            if entry.subject == subject and entry.predicate == predicate
        ),
        conflicts=tuple(
            conflict
            for conflict in conflicts(catalog)
            if conflict.subject == subject and conflict.predicate == predicate
        ),
    )


def catalog_issues(
    catalog: AssertionCatalog,
    *,
    model: SystemModel | None = None,
    sources: Mapping[str, str] = MappingProxyType({}),
) -> list[CatalogIssue]:
    """Every way this catalog is malformed; an empty result means it is ready.

    ``model`` and ``sources`` are optional in the shape
    :func:`~analysis_service.validation.validate` already uses: a caller that
    has them gets the checks that need them, and a caller that does not is told
    what can be decided from the catalog alone. **A check that cannot be made
    is never a check that passed** — a graph binding goes unresolved without a
    model, and this says so in the docstring rather than in silence.

    The version and the counts are checked first and return alone. A catalog
    keyed by another registry says nothing this one can read, and a catalog too
    large to read cannot be made acceptable by fixing its rows.
    """
    if catalog.registry_version != REGISTRY_VERSION:
        return [
            CatalogIssue(
                code="wrong-registry-version",
                message=f"registry version {catalog.registry_version} keyed these"
                f" rows; this service reads version {REGISTRY_VERSION}",
            )
        ]
    if len(catalog.entries) > MAX_ASSERTIONS:
        return [
            CatalogIssue(
                code="too-many-assertions",
                message=f"{len(catalog.entries)} assertions; the cap is {MAX_ASSERTIONS}",
            )
        ]
    if len(catalog.subjects) > MAX_SUBJECTS:
        return [
            CatalogIssue(
                code="too-many-subjects",
                message=f"{len(catalog.subjects)} subjects; the cap is {MAX_SUBJECTS}",
            )
        ]

    issues = _subject_issues(catalog, model)
    by_id = {subject.id: subject for subject in catalog.subjects}
    checked = _checked(sources)

    # One identity per row, computed once. Four checks below read them, and
    # recomputing per check is how two of them would come to disagree.
    rows = tuple(
        (entry, assertion_id(entry))
        for entry in catalog.entries
        if entry.predicate in REGISTRY
    )
    identities = frozenset(identity for _, identity in rows)

    issues.extend(
        CatalogIssue(
            code="unknown-predicate",
            message=f"{entry.predicate!r} is not a registered predicate",
            subject=entry.subject,
        )
        for entry in catalog.entries
        if entry.predicate not in REGISTRY
    )
    for entry, identity in rows:
        issues.extend(_entry_issues(entry, identity, by_id, checked))
    issues.extend(_identity_issues(rows, identities))
    issues.extend(_cycle_issues(rows, identities))
    return issues


def _subject_issues(
    catalog: AssertionCatalog, model: SystemModel | None
) -> list[CatalogIssue]:
    """Repeated subjects, mistyped IDs, and bindings the model does not hold."""
    issues = []
    seen: set[str] = set()
    element_ids = {element.id for element in model.elements()} if model else None
    for subject in catalog.subjects:
        if subject.id in seen:
            issues.append(
                CatalogIssue(
                    code="duplicate-subject",
                    message=f"subject {subject.id!r} is declared more than once",
                    subject=subject.id,
                )
            )
        seen.add(subject.id)
        prefix = subject.id.split(":", 1)[0]
        if prefix not in SUBJECT_PREFIXES[subject.type]:
            issues.append(
                CatalogIssue(
                    code="subject-type-mismatch",
                    message=f"subject {subject.id!r} is typed {subject.type!r},"
                    f" whose IDs start with"
                    f" {', '.join(sorted(SUBJECT_PREFIXES[subject.type]))}",
                    subject=subject.id,
                )
            )
        elif (
            element_ids is not None
            and subject.type in GRAPH_BOUND
            and subject.id not in element_ids
        ):
            issues.append(
                CatalogIssue(
                    code="dangling-binding",
                    message=f"subject {subject.id!r} is graph-bound and the model"
                    " holds no such element",
                    subject=subject.id,
                )
            )
    return issues


def _entry_issues(
    entry: Assertion,
    identity: str,
    by_id: Mapping[str, Subject],
    checked: Mapping[str, _Checked],
) -> list[CatalogIssue]:
    """Everything decidable about one assertion on its own."""
    predicate = REGISTRY[entry.predicate]
    issues = []

    def refuse(code: CatalogIssueCode, message: str) -> None:
        issues.append(CatalogIssue(code=code, message=message, assertion=identity))

    subject = by_id.get(entry.subject)
    if subject is None:
        refuse("dangling-subject", f"no subject {entry.subject!r} is declared")
    elif subject.type not in predicate.subjects:
        refuse(
            "wrong-subject-type",
            f"{entry.predicate!r} accepts"
            f" {', '.join(sorted(predicate.subjects))}, not {subject.type!r}",
        )

    if not predicate.admits(entry.value, by_id):
        refuse("illegal-value", f"{entry.value!r} is not a legal value here")
    elif predicate.value == "reference":
        referent = by_id.get(entry.value)
        if referent is not None and referent.type not in predicate.refers_to:
            refuse(
                "illegal-value",
                f"{entry.predicate!r} refers to"
                f" {', '.join(sorted(predicate.refers_to))}, not {referent.type!r}",
            )

    if entry.value == UNKNOWN and entry.reason is None:
        refuse("missing-reason", "an unknown value says why it is unknown")
    if entry.value != UNKNOWN and entry.reason is not None:
        refuse("unwanted-reason", "only an unknown value carries a reason")

    missing = [kind for kind in predicate.requires if kind not in _kinds(entry)]
    if missing:
        refuse("missing-scope", f"{entry.predicate!r} needs {', '.join(missing)}")

    # An unknown needs no span, whatever its basis: there is nothing to quote
    # when the sources do not answer. A `hedged` unknown may still carry the
    # words somebody hedged with, which is why the span is optional rather than
    # refused.
    if predicate.stated_only and entry.basis not in ("stated", "legacy"):
        refuse(
            "inference-refused",
            f"{entry.predicate!r} says which element a subject is, so the"
            f" sources have to state it; this row's basis is {entry.basis!r}",
        )
    if entry.basis == "stated" and entry.value != UNKNOWN and not entry.support:
        refuse(
            "unsupported-assertion",
            "a stated value cites the words that state it, and a quote not"
            " found in the source it names takes no span",
        )
    if len(entry.support) > MAX_SPANS:
        # Refused rather than cut: a row that kept its first eight spans and
        # dropped the rest would claim less support than it cited and say
        # nothing about the difference.
        refuse(
            "too-many-spans",
            f"{len(entry.support)} support spans, and a row carries at most"
            f" {MAX_SPANS}; a statement resting on more is a summary",
        )
    if entry.basis == "legacy" and entry.support:
        refuse("legacy-with-support", "a legacy value is never support-backed")
    # An inference says what it rests on, in words a person reads and, where
    # code made it, in rows. **Not in rows alone**: an assertion's identity is
    # computed, so a model writing a premise ID would be writing a key it
    # cannot compute, and demanding one buys a fabricated reference rather
    # than a basis. The words are required; the rows are optional and checked
    # where they are given.
    if entry.basis in ("inferred", "derived") and not entry.explanation:
        refuse("missing-premise", "an inferred or derived value says what it rests on")
    if entry.exclusive and (entry.basis != "stated" or not entry.support):
        refuse(
            "exclusive-without-support",
            "an exclusivity claim is a source's claim, so it cites the words",
        )
    if entry.assessment != "unchecked" and not entry.assessor:
        refuse("unassessed-assessor", "an assessment names who made it")

    issues.extend(_span_issues(entry, identity, checked))
    return issues


def _kinds(entry: Assertion) -> frozenset[str]:
    """The qualifier kinds this assertion's scope carries."""
    return frozenset(qualifier.kind for qualifier in entry.scope)


@dataclass(frozen=True)
class _Checked:
    """One source digested and folded once, for every span checked against it.

    A catalog carries many spans and a job carries few sources, so the work
    belongs to the source — the argument :class:`SpanSource` already makes for
    the resolver's side. A digest recomputed per span reads the whole
    submission once per span: at :data:`MAX_ASSERTIONS` rows of
    :data:`MAX_SPANS` spans that is 4000 reads of one text, measured at 0.77 s
    against 0.00018 s for one, over the 100 KiB ``max_source_bytes`` admits.

    **Not :class:`SpanSource`, and the reason is measured rather than tidy.**
    That one folds a source so a quote can be *located* in it, which needs the
    word index :func:`~analysis_service.grounding.index_source` builds. This
    one folds a source so a recorded span can be *verified* against it, which
    needs the raw text — a span names offsets into the exact retained text —
    and never reads that index.

    Their digests and haystacks do agree and are computed twice, which is 5.9 ms
    of a 100 KiB source per job. Merging them would make the gate build the
    index it does not read: 47.1 ms in place of 5.9 ms, so one type costs 41 ms
    to save 5.9. The duplicated fold is the cheaper half by a factor of seven,
    and a reader who spots the overlap should read this before repairing it.
    """

    text: str
    digest: str
    haystack: str


def _checked(sources: Mapping[str, str]) -> Mapping[str, _Checked]:
    """Every source folded once for the span checks, keyed by label.

    Named for what it returns rather than for a check, as :func:`_prepare` is:
    it verifies nothing, and the gate is what reads it.
    """
    return {
        label: _Checked(text, text_digest(text), normalize(text))
        for label, text in sources.items()
    }


def _span_issues(
    entry: Assertion, identity: str, checked: Mapping[str, _Checked]
) -> list[CatalogIssue]:
    """Whether each span still names the words it was taken from, and only them.

    Skipped where the caller supplied no sources, which is the one case a
    reader has to know about: without the text there is nothing to check a
    quote against, and inventing a pass would be the failure this module exists
    to prevent.

    The last check is the one a reader does not expect. Offsets that hold the
    quote are not yet offsets that *name* it: a submission that says one thing
    twice holds the quote in both places, and a span into the first copy says
    nothing about which copy the row rests on. #926 asks that such a quote keep
    its ambiguity, so the gate refuses it rather than let the matcher's choice
    of the first placement stand as a citation.

    **It asks that of a span's own fragment, which is weaker than asking it of
    the quote.** A quote that marks a cut takes one span per fragment, and the
    cut's ordering — each fragment after the last — can pin a sequence whose
    fragments repeat on their own. This sees the fragment, so it refuses that
    row where the sequence had already resolved it. The stronger question
    needs the whole quote, which only the resolver still holds; asking the
    weaker one here and saying so beats a gate that passes what it cannot
    check. Measured on 2026-09-16: **no assertion quote in the corpus or the
    archive marks a cut**, over 79 signed and 829 archived, so the case is
    unobserved rather than handled.
    """
    if not checked:
        return []
    issues = []
    for span in entry.support:
        source = checked.get(span.source_label)
        if source is None:
            issues.append(
                CatalogIssue(
                    code="dangling-source",
                    message=f"no source is labelled {span.source_label!r}",
                    assertion=identity,
                )
            )
            continue
        if span.digest != source.digest:
            issues.append(
                CatalogIssue(
                    code="stale-digest",
                    message=f"source {span.source_label!r} changed since this span"
                    " was taken",
                    assertion=identity,
                )
            )
            continue
        window = source.text[span.start : span.end]
        if span.end > len(source.text) or not verify_quote(span.quote, window):
            issues.append(
                CatalogIssue(
                    code="unverifiable-span",
                    message=f"offsets {span.start}-{span.end} of"
                    f" {span.source_label!r} do not hold this quote",
                    assertion=identity,
                )
            )
            continue
        if ambiguous_quote(span.quote, source.haystack):
            issues.append(
                CatalogIssue(
                    code="ambiguous-span",
                    message=f"source {span.source_label!r} holds this quote in more"
                    " than one place, so these offsets name no one of them; quote"
                    " enough of the source to say which",
                    assertion=identity,
                )
            )
    return issues


def _identity_issues(
    rows: tuple[tuple[Assertion, str], ...], identities: Collection[str]
) -> list[CatalogIssue]:
    """Repeated identities, and premises that name no assertion here."""
    issues = []
    seen: set[str] = set()
    for entry, identity in rows:
        if identity in seen:
            issues.append(
                CatalogIssue(
                    code="duplicate-assertion",
                    message="two rows share one identity; one row carries both spans",
                    assertion=identity,
                )
            )
        seen.add(identity)
        issues.extend(
            CatalogIssue(
                code="dangling-premise",
                message=f"premise {premise!r} is no assertion here",
                assertion=identity,
            )
            for premise in entry.premises
            if premise not in identities
        )
    return issues


def _cycle_issues(
    rows: tuple[tuple[Assertion, str], ...], identities: Collection[str]
) -> list[CatalogIssue]:
    """Every assertion that supports itself, however many steps around.

    Support that loops is support that rests on nothing, and it reads as
    justified from every row on the cycle. The walk is iterative and each edge
    is followed once, so a catalog at the cap costs its own size rather than
    its depth.
    """
    edges = {
        identity: [premise for premise in entry.premises if premise in identities]
        for entry, identity in rows
    }
    settled: set[str] = set()
    on_path: set[str] = set()
    looping: set[str] = set()

    for root in edges:
        if root in settled:
            continue
        stack: list[tuple[str, int]] = [(root, 0)]
        on_path = {root}
        while stack:
            node, index = stack.pop()
            if index >= len(edges[node]):
                settled.add(node)
                on_path.discard(node)
                continue
            stack.append((node, index + 1))
            premise = edges[node][index]
            if premise in on_path:
                looping.add(premise)
            elif premise not in settled:
                stack.append((premise, 0))
                on_path.add(premise)

    return [
        CatalogIssue(
            code="circular-support",
            message="this assertion is a premise of its own support",
            assertion=identity,
        )
        for identity in sorted(looping)
    ]


# --- What a model emits, and what code builds from it ------------------------
#
# The **Proposal** shape, for the reason ``claims.Proposal`` gives: a model
# names its evidence and code constructs the record, so a support span's
# offsets and the quote beside them can never disagree. Two flat lists a
# provider's schema compiler can express exactly.


class QuoteProposal(BaseModel):
    """A span of a source a model proposes, in the model's own spelling.

    No offsets. :func:`resolve_catalog` finds where the quote sits, or refuses
    it, which is what keeps a span from claiming a position the source does not
    hold.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_label: str = Field(min_length=1, max_length=200)
    quote: str = Field(min_length=1, max_length=MAX_QUOTE_CHARS)


class AssertionProposal(BaseModel):
    """One assertion a model proposes, before code resolves it.

    ``subject`` reads one of two ways, and ``subject_type`` decides which. For
    ``component``, ``interaction`` and ``zone`` it is an **Element ID** from the
    model the node was shown, snapped against that model exactly as a **Lane
    Agent**'s element reference is. For ``principal``, ``credential`` and
    ``artifact`` it is a short name, and code slugs it into the subject ID.

    ``value`` reads by the predicate: a term from its vocabulary, free text, or
    — for a reference predicate — the *name* of what it points at, which code
    resolves the same two ways. A model never writes a subject ID it would have
    to compute.

    There is no premise list. An assertion's identity is computed, so a premise
    ID is a key a model cannot write; an inference states its basis in
    ``explanation``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject_type: SubjectType
    subject: str = Field(min_length=1, max_length=300)
    # The registry's own names, on the rule :class:`Assumption` follows: a model
    # asked for a bounded string eventually writes prose into it, and the gate
    # that reads this field checks it against ``REGISTRY`` a layer later. The
    # enum is a schema fact and not a validator, so a value outside the set
    # stays a refusal naming this row rather than a schema fault condemning the
    # whole object. The set is static, which is what puts it here: a
    # configurable one is stated in the prompt instead.
    predicate: str = Field(
        min_length=1,
        max_length=60,
        json_schema_extra={"enum": [*sorted(REGISTRY)]},
    )
    value: str = Field(min_length=1, max_length=MAX_VALUE_CHARS)
    reason: UnknownReason | None = None
    scope: list[Qualifier] = Field(default_factory=list, max_length=MAX_QUALIFIERS)
    basis: Basis
    quotes: list[QuoteProposal] = Field(default_factory=list, max_length=MAX_SPANS)
    explanation: str = Field(default="", max_length=1000)
    #: See :attr:`Assertion.exclusive`. A model sets it only where the source
    #: says the set is complete.
    exclusive: bool = False


class CatalogProposal(BaseModel):
    """What the assertion node emits: one flat list and nothing else.

    The subject table is **derived** rather than declared. A model that listed
    subjects beside the rows that name them would have two places to spell one
    subject, and the two would eventually differ.
    """

    model_config = ConfigDict(extra="forbid")

    #: **No cap on the schema, and the bound is in :func:`resolve_catalog`.**
    #: A root-level array of objects carrying ``maxItems`` is a shape Google's
    #: structured output refuses — measured, and the only feature separating
    #: this schema from the extraction schema that runs on the same route. A
    #: model's output schema is a vendor-neutral surface, so a bound that costs
    #: a vendor belongs where code enforces it rather than where a provider has
    #: to accept it.
    assertions: list[AssertionProposal] = Field(default_factory=list)


def resolve_catalog(
    proposal: CatalogProposal,
    model: SystemModel,
    sources: Mapping[str, str],
) -> tuple[AssertionCatalog, list[CatalogIssue]]:
    """Build the catalog a proposal describes, and say what it cost.

    **The resolver constructs, so the gate cannot fail.** Every row it keeps is
    one :func:`catalog_issues` raises nothing for, and every row it drops comes
    back as an issue naming why — the shape a **Proposal** already has, where
    an agent selects and the service builds.

    Five things happen to a row, in order. Its predicate is looked up, and an
    unregistered one drops it. Its subject is resolved: a graph-bound one is
    snapped against the model's element IDs, and one of this layer's own is
    slugged from the name written. Its quotes are located in the sources they
    name, and a ``stated`` row left with no span drops rather than take a
    fabricated one. **The built row is then put through the gate's own per-row
    rules**, and one the gate would refuse drops with the gate's reasons — so
    the contract above holds by construction, where a copy of the rules here
    once let a grant with no scope through to be counted and refused later
    (#961). Its identity is computed, and two rows that share one are **one
    row carrying both spans** — which is how two sources for one fact keep
    both provenances, rather than becoming a duplicate the gate refuses.

    A dropped row is not a lost fact. The issues are what the repair pass reads,
    and repair has the sources in front of it. Each issue names the proposed
    row it refused by :attr:`CatalogIssue.row`, so rows dropped is a count of
    distinct rows and never a count of issues.

    The count is checked first and returns alone, as the gate's is: this is
    where :data:`MAX_ASSERTIONS` is enforced, because the schema cannot carry
    it without costing a vendor.
    """
    if len(proposal.assertions) > MAX_ASSERTIONS:
        return AssertionCatalog(), [
            CatalogIssue(
                code="too-many-assertions",
                message=f"{len(proposal.assertions)} assertions proposed;"
                f" the cap is {MAX_ASSERTIONS}",
            )
        ]
    prepared = _prepare(sources)
    checked = _checked(sources)
    element_ids = [element.id for element in model.elements()]
    labels = {element.id: element.name for element in model.elements()}

    kept: dict[str, Assertion] = {}
    subjects: dict[str, Subject] = {}
    issues: list[CatalogIssue] = []

    for index, row in enumerate(proposal.assertions):
        resolved = _resolve_row(
            index, row, element_ids, labels, subjects, prepared, checked, issues
        )
        if resolved is None:
            continue
        identity, entry = resolved
        held = kept.get(identity)
        kept[identity] = entry if held is None else _merge(held, entry)

    entries, cut = _bounded(kept.values())
    issues.extend(cut)

    # Through :func:`_catalog`, so how a subject table is derived from rows is
    # written down once. It also drops a subject whose only row the gate then
    # refused: the subject was recorded while the row was being built, and a
    # table naming something no row is about says the catalog holds a fact it
    # does not.
    return _catalog(entries, subjects), issues


def _bounded(
    entries: Iterable[Assertion],
) -> tuple[list[Assertion], list[CatalogIssue]]:
    """Each row cut to :data:`MAX_SPANS`, and a ``support-truncated`` issue per cut.

    **The one reader of "what happens when merged rows cross the span bound".**
    Rows of one identity each passed the bound alone and may cross it
    together. The merged row keeps the first spans and the cut is recorded,
    rather than refusing a fact for being stated too often or keeping eight
    spans with nothing to say a ninth was cited. :func:`resolve_catalog` and
    :func:`merged` both call it. The proposal the emission archive keeps still
    carries every quote.
    """
    kept = []
    issues = []
    for entry in entries:
        if len(entry.support) > MAX_SPANS:
            issues.append(
                CatalogIssue(
                    code="support-truncated",
                    message=f"rows stating this fact cited {len(entry.support)}"
                    f" passages between them; the row keeps the first"
                    f" {MAX_SPANS} and drops {len(entry.support) - MAX_SPANS}",
                    subject=entry.subject,
                    assertion=assertion_id(entry),
                )
            )
            entry = entry.model_copy(update={"support": entry.support[:MAX_SPANS]})
        kept.append(entry)
    return kept, issues


def _prepare(sources: Mapping[str, str]) -> Mapping[str, SpanSource]:
    """Every source folded once, keyed by label, for every quote taken from it.

    A source whose two folds disagree is absent here rather than wrong: see
    :func:`~analysis_service.grounding.index_source`. Its quotes then find no
    span, which is the same outcome as a quote that is not in it.
    """
    built = {}
    for label, text in sources.items():
        prepared = span_source(label, text)
        if prepared is not None:
            built[label] = prepared
    return built


def _resolve_row(
    index: int,
    row: AssertionProposal,
    element_ids: Collection[str],
    labels: Mapping[str, str],
    subjects: dict[str, Subject],
    prepared: Mapping[str, SpanSource],
    checked: Mapping[str, _Checked],
    issues: list[CatalogIssue],
) -> tuple[str, Assertion] | None:
    """One proposed row as an assertion, or ``None`` with the reasons recorded.

    Only what construction needs is decided here: the predicate has to be
    registered for an identity to exist, and the subject and a referent value
    have to resolve for the row to name them. Everything else is the gate's
    call, made on the built row, so no rule is written twice.

    A subject is declared only for a row that is kept, so a dropped row leaves
    no subject behind that no row names.
    """

    def drop(code: CatalogIssueCode, message: str) -> None:
        issues.append(
            CatalogIssue(
                code=code, message=message, subject=row.subject or None, row=index
            )
        )

    predicate = REGISTRY.get(row.predicate)
    if predicate is None:
        drop("unknown-predicate", f"{row.predicate!r} is not a registered predicate")
        return None

    subject = _subject(row.subject_type, row.subject, element_ids, labels)
    if subject is None:
        drop("dangling-subject", f"{row.subject!r} names no {row.subject_type}")
        return None

    value = row.value
    referent = None
    if predicate.value == "reference" and value not in UNIVERSAL_TERMS:
        wanted = referent_type(predicate)
        referent = (
            None if wanted is None else _subject(wanted, value, element_ids, labels)
        )
        if referent is None:
            drop(
                "illegal-value",
                f"{value!r} names no {wanted} this predicate takes",
            )
            return None
        value = referent.id

    spans = [span for quote in row.quotes for span in _spans(quote, prepared)]
    entry = Assertion(
        subject=subject.id,
        predicate=row.predicate,
        value=value,
        reason=row.reason if value == UNKNOWN else None,
        scope=list(row.scope),
        basis=row.basis,
        support=spans,
        explanation=row.explanation,
        exclusive=row.exclusive,
    )
    declared = {**subjects, subject.id: subject}
    if referent is not None:
        declared[referent.id] = referent
    identity = assertion_id(entry)
    refused = _entry_issues(entry, identity, declared, checked)
    if refused:
        for issue in refused:
            drop(issue.code, issue.message)
        return None
    subjects.setdefault(subject.id, subject)
    if referent is not None:
        subjects.setdefault(referent.id, referent)
    return identity, entry


def referent_type(predicate: Predicate) -> SubjectType | None:
    """The one subject type a reference predicate points at, or ``None``.

    One, never two: ``tests/test_assertions.py`` holds the registry to it, so
    this reads the single member rather than choosing between members.

    ``None`` for a predicate that takes no reference, which is 12 of the 16 and
    the shape a second reader missed. ``evals.harness.replay`` asked
    ``next(iter(refers_to))`` of every row the gate refused as
    ``illegal-value``, and a term predicate carrying a term outside its
    vocabulary draws exactly that code — so an archived catalog holding one
    would have ended the replay with a bare ``StopIteration`` naming nothing.
    Public, and the one reader, because the two sites that spelled it again
    could not call it while it was private.
    """
    return next(iter(predicate.refers_to), None)


def _subject(
    subject_type: SubjectType,
    written: str,
    element_ids: Collection[str],
    labels: Mapping[str, str],
) -> Subject | None:
    """The subject a model's word names, or ``None`` where it names none.

    Two resolutions, because a subject type says which. A graph-bound name is
    an **Element ID** the model was shown, snapped through
    :func:`~analysis_service.references.canonical` so a near spelling resolves
    and an ambiguous one does not. One of this layer's own is a name, and its
    ID is the name's slug — the same derivation an element ID has, so two
    spellings of one principal are one subject.
    """
    prefixes = SUBJECT_PREFIXES[subject_type]
    if subject_type in GRAPH_BOUND:
        found = canonical(written, element_ids) or _named(written, labels, prefixes)
        if not found or found.split(":", 1)[0] not in prefixes:
            return None
        return Subject(id=found, type=subject_type, label=labels[found])
    try:
        identity = subject_id(subject_type, written)
    except ValueError:
        return None
    return Subject(id=identity, type=subject_type, label=written.strip())


def snap_subject(
    subject_type: SubjectType, written: str, model: SystemModel
) -> str | None:
    """The subject ID a proposal's word resolves to against ``model``, or ``None``.

    The resolver's own subject rule, exposed for a reader that asks which
    element a proposed row *would* bind to without resolving the whole
    proposal — the binding replay asks it of the blessed model to name the
    element an extracted graph refused. One reader: it calls what
    :func:`resolve_catalog` calls.
    """
    element_ids = [element.id for element in model.elements()]
    labels = {element.id: element.name for element in model.elements()}
    subject = _subject(subject_type, written, element_ids, labels)
    return None if subject is None else subject.id


def _named(written: str, labels: Mapping[str, str], prefixes: Collection[str]) -> str:
    """The element of an accepted type whose *name* ``written`` is, or ``""``.

    The second way a model names an element, and a real one: asked for the zone
    a component sits in, a live run wrote ``core services`` where the model's
    own boundary is ``boundary:core-services``. Both spellings name one thing,
    because an **Element ID** is the slug of the element's name — so comparing
    the slugs asks the same question the ID derivation already answers.

    Only elements whose ID prefix is in ``prefixes`` are candidates, because
    the subject type asked for is part of the question: a zone named after
    the entity inside it is ordinary, and asked for the *zone* ``card
    processor``, the one boundary of that name is not made ambiguous by the
    entity of that name (#961). Empty where two elements of an accepted type
    share a name slug, for the reason
    :func:`~analysis_service.references.canonical` refuses an ambiguous fold:
    guessing which one a word meant is the thing this must not do.
    """
    try:
        wanted = normalize_name(written)
    except ValueError:
        return ""
    matches = []
    for element_id, name in labels.items():
        if element_id.split(":", 1)[0] not in prefixes:
            continue
        try:
            if normalize_name(name) == wanted:
                matches.append(element_id)
        except ValueError:
            continue
    return matches[0] if len(matches) == 1 else ""


def _spans(
    quote: QuoteProposal, prepared: Mapping[str, SpanSource]
) -> tuple[SupportSpan, ...]:
    """Where one proposed quote sits, or nothing where it is not there."""
    source = prepared.get(quote.source_label)
    if source is None:
        return ()
    return spans_for(quote.quote, source)


#: Which of two rows of one identity stands when their bases differ, lowest
#: first: a source that states the value outranks a rule that derived it,
#: which outranks a reader that inferred it, which outranks an attribute
#: imported from before this layer. A table over :data:`Basis`, held to it by
#: ``tests/test_assertions.py``, so a fifth basis fails there rather than
#: raising in a merge.
BASIS_RANK: Mapping[str, int] = MappingProxyType(
    {
        basis: rank
        for rank, basis in enumerate(("stated", "derived", "inferred", "legacy"))
    }
)


def _merge(held: Assertion, found: Assertion) -> Assertion:
    """Two rows of one identity as one row, by a rule that ignores their order.

    Identity settles the subject, the predicate, the scope and the value. The
    spans join, deduplicated and bounded, and either row stating the set is
    complete states it for the merged row. Everything else — basis,
    explanation, reason — comes from **one** of the two rows, the one that
    ranks first: by :data:`BASIS_RANK`, and on a tie by the row's own
    serialized form, so two orderings of one proposal build one catalog.
    Before this rule the first row's basis stood, and one pair of rows read
    ``inferred`` or ``stated`` by arrival order (#961).

    **The joined spans are not cut here.** :data:`MAX_SPANS` is the one bound a
    merge can cross, since each row passed it alone, and a cut made here was
    silent: nine rows citing nine passages became one row citing eight and no
    issue said so (#926). Every caller cuts what it merged through
    :func:`_bounded`, which records ``support-truncated``. The first row's
    spans come first, so a cut keeps them.
    """
    first, second = sorted((held, found), key=_merge_rank)
    spans = list(first.support)
    for span in second.support:
        if span not in spans:
            spans.append(span)
    return first.model_copy(
        update={
            "support": spans,
            "exclusive": held.exclusive or found.exclusive,
        }
    )


def _merge_rank(entry: Assertion) -> tuple[int, str]:
    return BASIS_RANK[entry.basis], entry.model_dump_json()


# --- What the graph's own fields would say ----------------------------------


#: Why a projected value reads the way it does. Closed, because the whole point
#: of projecting is to say what the one string cost: a reader comparing a
#: projection to the attribute beside it needs to know whether the value is the
#: catalog's answer or the catalog refusing to answer in one word.
ProjectionReason = Literal[
    "stated",
    "absent",
    "unknown",
    "hedged",
    "scoped",
    "several-values",
    "several-predicates",
    "compatible",
    "unsupported",
    "legacy",
]

#: Whether a row under each :data:`Assessment` states its value for the graph.
#: Nobody having checked is the default, and the only state an extractor
#: leaves, so it stands; a row somebody found supported stands. A row found
#: unsupported, or left unresolved by whoever looked, states nothing: before
#: this table a row assessed ``unsupported`` projected as a definite stated
#: value (#961). Keyed by every assessment and held to the literal by
#: ``tests/test_assertions.py``, so a fifth assessment fails there rather than
#: projecting by default.
PROJECTS_UNDER: Mapping[str, bool] = MappingProxyType(
    {"unchecked": True, "supported": True, "unsupported": False, "unresolved": False}
)

#: The assessments that set a row aside: every one :data:`PROJECTS_UNDER` refuses.
REJECTING: frozenset[str] = frozenset(
    assessment for assessment, projects in PROJECTS_UNDER.items() if not projects
)


@dataclass(frozen=True)
class Projection:
    """What one element attribute would hold, built from the catalog's rows.

    ``value`` is spelled the way the **System Model** spells it: a mechanism as
    written, :data:`~analysis_service.system_model.UNKNOWN` where the catalog
    does not settle the attribute, and ``"none"`` for a stated absence, which is
    the word :func:`~analysis_service.analysis.control_state` reads as absent.

    ``rows`` names the assertions behind it, so a reader of a degraded value can
    see what would not fit.
    """

    element_id: str
    attribute: str
    value: str
    reason: ProjectionReason
    rows: tuple[str, ...]


def project(catalog: AssertionCatalog) -> tuple[Projection, ...]:
    """What each graph attribute the catalog reaches would hold.

    **Loss-aware, and it never picks.** One string holds one unscoped value, so
    the catalog's rows fit it or they do not. Where two predicates feed one
    attribute, where two values sit under one attribute, or where the source
    scoped the only value it stated, the projection writes ``unknown`` and
    ``reason`` says which of those happened. Choosing between them would drop a
    fact the catalog holds, and writing a summary of both would write a value
    the catalog does not hold.

    An attribute no row reaches is **absent from the result**, not ``unknown``:
    the catalog says nothing about it, and a projection that filled it would be
    asserting silence rather than reporting it.

    A row :func:`admissible` refuses is set aside: it neither supplies the
    value nor counts as a second one. An attribute only such rows reach writes
    ``unknown`` with the reason ``unsupported``, or ``legacy`` where every one
    of them was imported from before this layer, and the rows are still named,
    so a reader sees what was set aside. Two values that can both hold — see
    :func:`_compatible` — read ``compatible`` rather than as a disagreement.

    What each reason then does to the graph is :data:`PROJECTION_EFFECT`'s.

    Sorted by element then attribute, so two readings of one catalog agree on
    order.
    """
    subjects = {subject.id: subject for subject in catalog.subjects}
    grouped: dict[tuple[str, str], list[tuple[str, Assertion]]] = {}
    for entry in catalog.entries:
        attribute = projected_attribute(entry.predicate, entry.subject)
        held = subjects.get(entry.subject)
        if held is None or held.type not in GRAPH_BOUND:
            continue
        if attribute:
            grouped.setdefault((entry.subject, attribute), []).append(
                (assertion_id(entry), entry)
            )
    return tuple(
        _projected(element_id, attribute, rows, subjects)
        for (element_id, attribute), rows in sorted(grouped.items())
    )


@dataclass(frozen=True)
class Contradiction:
    """A graph attribute and the row authoritative for it that state opposites.

    ``projected`` is what the catalog's rows say the attribute would hold;
    ``carried`` is what the **System Model** beside them actually holds. Both
    are quoted as written, because which of the two is wrong is not decidable
    here and a reader has to see the pair.
    """

    element_id: str
    attribute: str
    projected: str
    carried: str
    rows: tuple[str, ...]


def contradictions(
    catalog: AssertionCatalog, model: SystemModel
) -> tuple[Contradiction, ...]:
    """Attributes whose graph value and whose catalog rows state opposites.

    **Opposed states only, and that is the whole rule.** A projection and an
    attribute are compared through
    :func:`~analysis_service.analysis.control_state`, and a pair is reported
    only when one reads ``stated`` and the other ``absent``. That is the defect
    this layer was built for: the audit found an explicit lack of MFA standing
    in the graph as a control, and nothing in the report said the two disagreed.

    **Not an agreement check, and it must not become one.** Two mechanisms
    worded differently are one answer spelled twice, and the catalog is
    authoritative for the wording anyway, so flagging that would report a
    disagreement on every run. ``unverified`` on either side is silence rather
    than an opposite: a graph attribute nobody stated and a row that states one
    is the projection doing its job.

    A projection that did not settle — ``unknown`` under any
    :data:`ProjectionReason` — states nothing to contradict, so it is skipped
    here and its loss is reported by :func:`project` where it happened.
    """
    carried = {element.id: element for element in model.elements()}
    found = []
    for projection in project(catalog):
        element = carried.get(projection.element_id)
        if element is None:
            continue
        held = str(getattr(element, projection.attribute, ""))
        states = {control_state(projection.value), control_state(held)}
        if states == {"stated", "absent"}:
            found.append(
                Contradiction(
                    element_id=projection.element_id,
                    attribute=projection.attribute,
                    projected=projection.value,
                    carried=held,
                    rows=projection.rows,
                )
            )
    return tuple(found)


@dataclass(frozen=True)
class CatalogCoverage:
    """What one job's assertion pass reached, and what it left untouched.

    **Derived, never stored.** ADR 0034 rejected a stored coverage record for
    the reason it rejected a stored conflict record: it is a second reader of
    the rows, and its own test would agree with it. This is computed from the
    catalog and the model every time somebody asks.

    It exists because of what the same record says about absence: *absence from
    the catalog is never an absent control — it is a predicate nobody asked
    about.* A reader with no way to see how much was asked reads a short
    catalog as a quiet system. Measured on the archived sweeps, the pass
    reproduces about half of what a reader says the sources state, and nothing
    in a report said so.

    ``reachable`` and ``settled`` are the honest denominator and numerator: the
    element attributes some predicate could speak to on *this* model, and how
    many the catalog actually settles. ``open`` counts the questions the
    sources raised and left unanswered, which is coverage stated as rows.
    ``own_subjects`` counts what the graph has no place for at all — a fact
    about a principal or a credential — because that is the part of the catalog
    a reader cannot find anywhere else.
    """

    reachable: int
    settled: int
    open: int
    own_subjects: int


def catalog_coverage(catalog: AssertionCatalog, model: SystemModel) -> CatalogCoverage:
    """How much of what this model's attributes could hold the catalog reached."""
    fields = set(projection_fields().values())
    reachable = sum(
        1
        for element in model.elements()
        for attribute in fields
        if hasattr(element, attribute)
    )
    settled_rows = settled(catalog)
    graph_bound = {
        subject.id for subject in catalog.subjects if subject.type in GRAPH_BOUND
    }
    return CatalogCoverage(
        reachable=reachable,
        settled=len(
            {
                (projection.element_id, projection.attribute)
                for projection in project(catalog)
                if projection.reason in SETTLING_REASONS
            }
        ),
        open=sum(1 for entry in catalog.entries if entry.value == UNKNOWN),
        own_subjects=sum(
            1 for entry in settled_rows if entry.subject not in graph_bound
        ),
    )


def contradiction_issues(
    catalog: AssertionCatalog, model: SystemModel
) -> list[CatalogIssue]:
    """:func:`contradictions` as the issues a report carries.

    One spelling of the message, so the record written by a job and the record
    a loaded report is re-checked against cannot word the same finding two
    ways and read as a disagreement.
    """
    return [
        CatalogIssue(
            code="graph-contradiction",
            message=f"{found.element_id}.{found.attribute} holds"
            f" {found.carried!r} and the rows behind it state"
            f" {found.projected!r}, which is the opposite state",
            assertion=found.rows[0] if found.rows else None,
        )
        for found in contradictions(catalog, model)
    ]


#: What each :data:`ProjectionReason` does to the graph attribute it names.
#:
#: ``write`` replaces the attribute with the projected value: the sources
#: stated the value, or stated that the control is not there.
#:
#: ``qualify`` replaces it with a qualified ``unknown`` that names what the
#: rows state (:func:`qualified`). The catalog holds rows about this attribute
#: and they do not settle it in one unscoped value — a conflict, a value stated
#: only for some scope, a row a reviewer set aside. **Leaving the attribute
#: alone there made the extraction's independent value authoritative over rows
#: that say it is not settled**, and the uncertainty evidence a lane would have
#: cited never appeared (#926). The rows themselves stay cited where
#: :func:`offered` finds them settled.
#:
#: ``hedged`` qualifies too: every row reads :data:`UNKNOWN` and at least one
#: says a speaker voiced the doubt, so the sources stated that the control is
#: uncertain, and a definite value extraction wrote beside that is the
#: substitution this layer exists to stop.
#:
#: ``leave`` keeps what extraction wrote. ``unknown`` is rows that all read
#: :data:`UNKNOWN` for a reason that is not a hedge — silence, a predicate
#: nobody asked about, a bound that stopped the work — which says the pass did
#: not find an answer, not that the sources gave none; ``legacy`` is rows imported from before this layer, which
#: are that attribute spelled again; ``compatible`` is several values that all
#: hold, which one string cannot carry and which :func:`offered` cites as rows.
#: Measured over the 103 archived assertion records against their blessed
#: models, the two ``several-predicates`` projections were both a mechanism and
#: the credential it presents, and are ``compatible`` here: qualifying them
#: would have turned two stated controls into questions. Of the 89
#: projections whose rows all read ``unknown``, 61 carried a hedge and 28 were
#: silent, and every one of the 89 fields already read ``unknown`` on the
#: blessed model, so ``hedged`` changes nothing there. On models extraction
#: built for itself — every archived proposal resolved against every archived
#: extracted graph of its case, 1,500 pairs, the pairing ``run.py bind`` makes
#: — 3 of 1,360 hedged projections sat over a definite value, all case 04's
#: model server read ``internal`` where the source says its network is "meant
#: to be" reachable only from inside and the blessed model reads ``unknown``.
#: Each of the 3 moves the graph to the reviewed answer.
#:
#: Keyed by every reason and held to the literal by ``tests/test_assertions.py``,
#: so a reason added tomorrow fails there rather than defaulting to either.
PROJECTION_EFFECT: Mapping[str, Literal["write", "qualify", "leave"]] = (
    MappingProxyType(
        {
            "stated": "write",
            "absent": "write",
            "unknown": "leave",
            "hedged": "qualify",
            "scoped": "qualify",
            "several-values": "qualify",
            "several-predicates": "qualify",
            "compatible": "leave",
            "unsupported": "qualify",
            "legacy": "leave",
        }
    )
)

#: The projection reasons under which the catalog answers an attribute: its
#: projected value is the attribute's value. Read off :data:`PROJECTION_EFFECT`.
SETTLING_REASONS: frozenset[str] = frozenset(
    reason for reason, effect in PROJECTION_EFFECT.items() if effect == "write"
)

#: How a qualified ``unknown`` is spelled in each attribute a predicate
#: projects into. ``prose`` fields hold a sentence, so the qualification names
#: what the rows state after the sentinel; ``bare`` fields hold a closed term or
#: an **Element ID**, and anything after the sentinel there is a value the
#: schema or the validity gate refuses. Keyed by the graph field and held to
#: :func:`projection_fields` by ``tests/test_assertions.py``.
QUALIFIED_SPELLING: Mapping[str, Literal["prose", "bare"]] = MappingProxyType(
    {
        "authentication": "prose",
        "encryption_in_transit": "prose",
        "encryption_at_rest": "prose",
        "data_classification": "prose",
        "exposure": "bare",
        "trust_zone": "bare",
    }
)

#: What a qualified ``unknown`` says happened, keyed by every reason that
#: qualifies.
_QUALIFIED_BECAUSE: Mapping[str, str] = MappingProxyType(
    {
        "hedged": "the sources voice uncertainty about this",
        "scoped": "stated only for part of what this carries",
        "several-values": "the sources state values that cannot all hold",
        "several-predicates": "the sources state facts that cannot all hold",
        "unsupported": "a reviewer set the stated value aside",
    }
)


def apply_projection(
    model: SystemModel, catalog: AssertionCatalog, *, hold_unchecked: bool = True
) -> tuple[SystemModel, tuple[Projection, ...]]:
    """The model with each projection :data:`PROJECTION_EFFECT` applies written in.

    **The migration ADR 0034 defers to Phase 4, taken one reader at a time.**
    The catalog becomes authoritative for a migrated fact and the element
    attribute becomes a value code computes. Where the catalog answers, its
    value is written; where its rows say the attribute is not settled in one
    value, a qualified ``unknown`` is written; and where it has nothing to say
    beyond what extraction wrote, the attribute stands.

    Returns the model and the projections that were applied, so a caller can
    record what moved rather than diff two models to find out.

    **An applied projection whose rows are inferred writes an Assumption.** An
    :class:`~analysis_service.system_model.Assumption` is the record of a value
    this service inferred into a graph attribute, and a projection resting on
    an ``inferred`` row is exactly that. Without the entry the model would
    carry an inference with nothing naming it, which is the state the gate
    refuses for every other inferred attribute.

    Measured, over every archived assertion sweep: 210 settled projections
    already agreed with the blessed model, 7 disagreed, and every one of the 7
    was the catalog reading a stated absence where the graph read ``unknown``.
    That is the substitution this layer exists to remove, and it is the whole
    of what this function changes.

    ``hold_unchecked`` is ADR 0041's guard (:func:`_unchecked_over_lead`).
    Every job keeps it on. ``run.py guard-cost`` turns it off to measure what
    the guard holds back.
    """
    applied = tuple(
        projection
        for projection in project(catalog)
        if PROJECTION_EFFECT[projection.reason] != "leave"
    )
    if not applied:
        return model, ()
    updated = _projected_model(model, catalog, applied, hold_unchecked)
    # **Fail closed on the model, not on the rows.** A projected ``trust_zone``
    # is a reference, and a row naming a zone this model does not hold would
    # leave a dangling endpoint that ``boundary_crossings`` refuses — after
    # every consumer downstream has been handed the model. So the result is put
    # back through the shared gate, and a model the gate refuses is discarded
    # whole: the graph keeps what extraction wrote and the rows stay in the
    # catalog, which is the state this function exists to improve on rather
    # than a state it may leave worse.
    if validate(updated):
        return model, ()
    return updated, applied


def _projected_model(
    model: SystemModel,
    catalog: AssertionCatalog,
    applied: tuple[Projection, ...],
    hold_unchecked: bool,
) -> SystemModel:
    """``model`` with each applied projection written in, before the gate sees it."""
    updated = model.model_copy(deep=True)
    elements = {element.id: element for element in updated.elements()}
    rows = {assertion_id(entry): entry for entry in catalog.entries}
    subjects = {subject.id: subject for subject in catalog.subjects}
    for projection in applied:
        element = elements.get(projection.element_id)
        if element is None or not hasattr(element, projection.attribute):
            continue
        if PROJECTION_EFFECT[projection.reason] == "qualify":
            setattr(
                element,
                projection.attribute,
                qualified(projection, [rows[ref] for ref in projection.rows], subjects),
            )
            continue
        behind = [rows[ref] for ref in projection.rows if ref in rows]
        if hold_unchecked and _unchecked_over_lead(
            projection, getattr(element, projection.attribute), behind
        ):
            setattr(
                element,
                projection.attribute,
                qualified(projection, behind, subjects, because=UNCHECKED_BECAUSE),
            )
            continue
        setattr(element, projection.attribute, projection.value)
        bases = {entry.basis for entry in behind}
        if "inferred" in bases and not any(
            entry.element_id == projection.element_id
            and entry.attribute == projection.attribute
            for entry in updated.assumptions
        ):
            updated.assumptions.append(
                Assumption(
                    assumption=f"{projection.attribute} is {projection.value}",
                    element_id=projection.element_id,
                    attribute=projection.attribute,
                    basis="inferred by the assertion pass from the sources"
                    f" ({', '.join(sorted(projection.rows))})"[:1000],
                )
            )
    return updated


#: What a qualified ``unknown`` says when an unchecked row would have turned a
#: lead into a stated control. See :func:`_unchecked_over_lead`.
UNCHECKED_BECAUSE = "the sources state this and no reviewer has checked it"


def _unchecked_over_lead(
    projection: Projection, held: object, rows: Sequence[Assertion]
) -> bool:
    """Whether writing this projection would let an unchecked row close a lead.

    **The unreviewed half of #926's contract** (the owner's decision of
    2026-09-23, option (iii)): an unchecked assertion informs conditional
    analysis and never becomes a verified control. So where the attribute
    reads as a lead — never stated, or stated absent — and the projection
    would make it a stated control, it is written as a qualified ``unknown``
    naming the value instead. The lead stays in the Evidence Catalog, and the
    row, no longer carried by the attribute, is cited as itself with its
    review status in the gloss (:func:`offered`).

    A row a reviewer marked ``supported`` may close the lead; that is what a
    review is for. ``run.py guard-cost`` measures what it holds back, and ADR
    0041 records the reading: over the archived proposal/graph pairings a job
    could project onto, 44 values the blessed model agrees with reach the lane
    as cited unchecked rows instead, and 18 wrong ``none`` authentications
    become questions. A stated absence over an unknown still writes, because an
    absence is itself a lead. A stated control over a stated one closes
    nothing, whichever is right, so it is left to the projection.
    """
    if control_state(projection.value) != "stated":
        return False
    if control_state(str(held)) == "stated":
        return False
    return not any(entry.assessment == "supported" for entry in rows)


def qualified(
    projection: Projection,
    rows: Sequence[Assertion],
    subjects: Mapping[str, Subject],
    *,
    because: str = "",
) -> str:
    """The ``unknown`` a qualifying projection writes, with what the rows state.

    Leads with the sentinel, so :func:`~analysis_service.analysis.control_state`
    reads the attribute as unsettled and the **Evidence Catalog** offers it as
    a question. What follows is the loss the projection could not fit in one
    value — each row's value and scope — so a reader of the attribute alone
    still sees the facts behind it. Cut at the field's bound; the rows stay in
    the catalog whole.

    Only :func:`admissible` rows are named. The attribute reaches every lane
    through the rendered model, so a value a reviewer set aside, written here,
    would reach analysis after review (ADR 0041).
    """
    if QUALIFIED_SPELLING[projection.attribute] == "bare":
        return UNKNOWN
    stated: list[str] = []
    for entry in sorted(filter(admissible, rows), key=_merge_rank):
        if entry.value == UNKNOWN:
            if entry.reason == "hedged":
                stated.extend(f'"{span.quote}"' for span in entry.support)
            continue
        value = _written(entry.value, subjects)
        value = ABSENT_WORD if value == ABSENT else value
        scope = ", ".join(f"{q.kind} {q.value}" for q in entry.scope)
        stated.append(f"{value} ({scope})" if scope else value)
    said = "; ".join(dict.fromkeys(stated))
    text = f"{UNKNOWN}; {because or _QUALIFIED_BECAUSE[projection.reason]}" + (
        f": {said}" if said else ""
    )
    return text if len(text) <= MAX_VALUE_CHARS else text[: MAX_VALUE_CHARS - 1] + "…"


def projected_attribute(predicate: str, subject: str) -> str:
    """The graph attribute a row of ``predicate`` about ``subject`` reaches, or ``""``.

    **The one reader of "does this row reach a graph field".** A predicate
    names a field and a field belongs to an element type, so the answer is a
    property of the row, never of the predicate alone: ``authentication-mechanism``
    reaches ``authentication`` on a **Data Flow** and nothing on a **Process**,
    and a credential a principal presents reaches nothing at all. Routing by
    predicate once left a legal row on a Process with neither a projection nor
    an evidence entry (#926). :func:`project` and :func:`offered` both ask this.
    """
    attribute = projection_fields().get(predicate, "")
    return attribute if attribute and attribute in _attributes_of(subject) else ""


def offered(catalog: AssertionCatalog, model: SystemModel) -> tuple[Assertion, ...]:
    """The settled rows a lane cites as themselves, in catalog order.

    **The one reader of "which rows does the Evidence Catalog carry".** A
    settled row is cited as itself unless the graph already carries it: its
    row reaches an attribute (:func:`projected_attribute`), that attribute's
    projection writes the catalog's value (:data:`SETTLING_REASONS`), and the
    model in front of the reader holds that value. Everything else a settled
    row says would otherwise reach no reader — a fact about a principal, a
    mechanism on a Process, a scoped control, one of two compatible values, or
    a row whose projection :func:`apply_projection` discarded.

    Asked of the model rather than of whether a projection ran, so a report
    re-checked on load and the job that wrote it agree without either
    recording which projections were applied.
    """
    held = {element.id: element for element in model.elements()}
    carried = {
        identity
        for projection in project(catalog)
        if projection.reason in SETTLING_REASONS
        and (element := held.get(projection.element_id)) is not None
        and getattr(element, projection.attribute, None) == projection.value
        for identity in projection.rows
    }
    return tuple(
        entry for entry in settled(catalog) if assertion_id(entry) not in carried
    )


def _attributes_of(element_id: str) -> frozenset[str]:
    """Every attribute the element type behind ``element_id`` declares.

    **A predicate projects into a field, and a field belongs to one element
    type.** ``authentication-mechanism`` takes a ``component`` subject as well
    as an ``interaction`` one, and only a **Data Flow** carries
    ``authentication``; ``storage-encryption`` takes any component and only a
    **Data Store** carries ``encryption_at_rest``. Without this, a legal row
    projected a value onto an element with no such field, and the comparison
    beside it read the absent attribute as ``unknown``.

    Read off the element classes by ID prefix, so a field moved between types
    moves this with it.
    """
    element_type = _ELEMENT_TYPES.get(element_id.split(":", 1)[0])
    return frozenset(element_type.model_fields) if element_type else frozenset()


def _projected(
    element_id: str,
    attribute: str,
    rows: list[tuple[str, Assertion]],
    subjects: Mapping[str, Subject],
) -> Projection:
    """One attribute's projected value, and the reason it reads that way."""
    ids = tuple(sorted(identity for identity, _ in rows))
    valued = [entry for _, entry in rows if entry.value != UNKNOWN]
    stated = [entry for entry in valued if admissible(entry)]

    def projected(value: str, reason: ProjectionReason) -> Projection:
        return Projection(element_id, attribute, value, reason, ids)

    if not valued:
        # A speaker who voiced doubt is the sources answering "we do not
        # know"; a row the pass could not fill is the sources not answering.
        # Only the first outranks a definite value extraction wrote.
        if any(entry.reason == "hedged" for _, entry in rows):
            return projected(UNKNOWN, "hedged")
        return projected(UNKNOWN, "unknown")
    if not stated:
        # Rows somebody set aside are a question the catalog raised about this
        # attribute; rows imported from before this layer are the attribute
        # itself, spelled again, and say nothing new about it.
        if all(entry.basis == "legacy" for entry in valued):
            return projected(UNKNOWN, "legacy")
        return projected(UNKNOWN, "unsupported")
    if any(entry.scope for entry in stated):
        return projected(UNKNOWN, "scoped")
    if len({(entry.predicate, entry.value) for entry in stated}) > 1:
        if _compatible(stated):
            return projected(UNKNOWN, "compatible")
        if len({entry.predicate for entry in stated}) > 1:
            return projected(UNKNOWN, "several-predicates")
        return projected(UNKNOWN, "several-values")
    value = _written(stated[0].value, subjects)
    if value == ABSENT:
        return projected(ABSENT_WORD, "absent")
    return projected(value, "stated")


def _compatible(rows: Sequence[Assertion]) -> bool:
    """Whether several unscoped values under one attribute can all hold at once.

    They can when every predicate behind them admits several values and none of
    them is :data:`ABSENT`: a connection authenticated by an API token and
    presenting that token states one control twice, and mutual TLS beside a
    bearer token states two. A stated absence beside any positive value is a
    contradiction, and so is a second value of a predicate that holds one.
    """
    return all(
        REGISTRY[entry.predicate].multiplicity == "many" and entry.value != ABSENT
        for entry in rows
    )


def _written(value: str, subjects: Mapping[str, Subject]) -> str:
    """One value as the graph's own field spells it.

    **A reference is spelled twice, and which spelling belongs in a field is a
    property of the referent.** A zone reference names an element, and
    ``trust_zone`` holds an **Element ID** by the validity gate's own rule, so
    the ID is the value. A credential reference names a subject this layer
    invented, and ``authentication`` holds prose, so a live run projected
    ``credential:session-cookie`` into a field whose every other value is a
    sentence. The subject's label is what the catalog holds for that, so
    writing it invents nothing.
    """
    referent = subjects.get(value)
    if referent is None or referent.type in GRAPH_BOUND:
        return value
    return referent.label
