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
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

from analysis_service.grounding import (
    index_source,
    locate_quote,
    normalize,
    verify_quote,
)
from analysis_service.sources import text_digest
from analysis_service.system_model import (
    ELEMENT_ID,
    UNKNOWN,
    DataFlow,
    Element,
    SystemModel,
    TrustBoundary,
    ZonedElement,
    normalize_name,
)

__all__ = [
    "ABSENT",
    "MAX_ASSERTIONS",
    "MAX_PREMISES",
    "MAX_QUALIFIERS",
    "MAX_SPANS",
    "MAX_SUBJECTS",
    "REGISTRY",
    "REGISTRY_VERSION",
    "UNIVERSAL_TERMS",
    "Assertion",
    "AssertionCatalog",
    "Basis",
    "CatalogIssue",
    "CatalogIssueCode",
    "Conflict",
    "Predicate",
    "Qualifier",
    "QualifierKind",
    "Subject",
    "SubjectType",
    "SupportSpan",
    "UnknownReason",
    "assertion_id",
    "catalog_issues",
    "conflicts",
    "projection_fields",
    "subject_id",
    "support_span",
]

#: The registry's version. It keys every assertion identity, so a bump re-keys
#: the whole catalog by recomputation rather than by a migration. Bumped
#: whenever a predicate is added, removed, re-spelled, or has its value
#: vocabulary, scope requirement or multiplicity changed — each of those
#: changes what a row means, and a reader comparing two runs has to know.
REGISTRY_VERSION = 1

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

#: Every prefix the element schema uses, read off the five element classes.
_ELEMENT_PREFIXES: frozenset[str] = frozenset(
    element_type.id_prefix for element_type in get_args(Element)
)

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
    is authoritative for, or ``""`` where the graph has no field — which is
    nine of the fourteen.
    """

    meaning: str
    subjects: frozenset[str]
    value: ValueKind
    terms: frozenset[str] = frozenset()
    refers_to: frozenset[str] = frozenset()
    requires: tuple[str, ...] = ()
    multiplicity: Multiplicity = "one"
    projects_into: str = ""

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
        "credential-expiry": Predicate(
            meaning="whether this credential stops working on its own",
            subjects=frozenset({"credential"}),
            value="term",
            terms=frozenset({"expires", "does-not-expire"}),
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
        "signature-verification": Predicate(
            meaning="whether the receiver checks who signed the artifact it took",
            subjects=frozenset({"artifact", "interaction"}),
            value="term",
            terms=frozenset({"verified"}),
        ),
        "destination-verification": Predicate(
            meaning="whether the sender checks it is sending to the right place",
            subjects=frozenset({"interaction"}),
            value="term",
            terms=frozenset({"verified"}),
        ),
        "network-membership": Predicate(
            meaning="which zone this component sits in",
            subjects=frozenset({"component"}),
            value="reference",
            refers_to=frozenset({"zone"}),
            projects_into="trust_zone",
        ),
        "administrative-authority": Predicate(
            meaning="which component this principal administers",
            subjects=frozenset({"principal"}),
            value="reference",
            refers_to=frozenset({"component", "zone"}),
            multiplicity="many",
        ),
        "tenant-ownership": Predicate(
            meaning="which party controls this component",
            subjects=frozenset({"component", "zone"}),
            value="reference",
            refers_to=frozenset({"principal"}),
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
    changed source is visible rather than silently re-read.

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

    A graph-bound subject's ``id`` is the **Element ID** itself, so the binding
    cannot drift from the thing it binds.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(max_length=300, pattern=ELEMENT_ID)
    type: SubjectType
    label: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=8)


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
    support: list[SupportSpan] = Field(default_factory=list, max_length=MAX_SPANS)
    #: The assertion IDs an ``inferred`` value rests on.
    premises: list[str] = Field(default_factory=list, max_length=MAX_PREMISES)
    #: What an ``inferred`` value was inferred from, or what a ``derived`` rule
    #: is. One field, because both answer "why is this value here".
    explanation: str = Field(default="", max_length=1000)
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
    "legacy-with-support",
    "missing-premise",
    "dangling-premise",
    "circular-support",
    "dangling-source",
    "stale-digest",
    "unverifiable-span",
    "unassessed-assessor",
]


class CatalogIssue(BaseModel):
    """One structured catalog failure, addressed to the pass that can fix it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: CatalogIssueCode
    message: str
    subject: str | None = None
    assertion: str | None = None


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
    """
    if subject_type in GRAPH_BOUND:
        raise ValueError(
            f"{subject_type!r} is graph-bound; its subject ID is the element ID"
        )
    prefixes = SUBJECT_PREFIXES.get(subject_type)
    if prefixes is None:
        raise ValueError(f"unknown subject type {subject_type!r}")
    return f"{next(iter(prefixes))}:{normalize_name(name)}"


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
    predicate = REGISTRY[assertion.predicate]
    return (
        f"assertion:{assertion.predicate}"
        f"~{assertion.subject}"
        f"~{_scope_key(assertion.scope)}"
        f"~{_value_key(predicate, assertion.value)}"
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


def support_span(quote: str, source_label: str, source_text: str) -> SupportSpan | None:
    """The span ``quote`` occupies in ``source_text``, or ``None`` if it is absent.

    **The only way to build a span.** The offsets come from
    :func:`~analysis_service.grounding.locate_quote`, which reads the same
    matcher :func:`~analysis_service.grounding.verify_quote` reads, so a span
    and a **Ground** can never disagree about one quote.

    A quote marking a cut with ``…`` covers several spans of the source. This
    answers the first, and a caller that needs each fragment separately calls
    :func:`spans_for` instead — the two are the same lookup, and this is the
    common case named once.
    """
    spans = spans_for(quote, source_label, source_text)
    return spans[0] if spans else None


def spans_for(
    quote: str, source_label: str, source_text: str
) -> tuple[SupportSpan, ...]:
    """Every span ``quote`` occupies, one per fragment, or empty when absent."""
    indexed = index_source(source_text)
    if indexed is None:
        return ()
    located = locate_quote(quote, indexed)
    if located is None:
        return ()
    digest = text_digest(source_text)
    return tuple(
        SupportSpan(
            source_label=source_label,
            digest=digest,
            start=span.start,
            end=span.end,
            quote=quote[:MAX_QUOTE_CHARS],
        )
        for span in located
    )


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

    Sorted by subject, then predicate, then scope, so two runs over one catalog
    report the same order.
    """
    groups: dict[tuple[str, str, str], dict[str, None]] = {}
    for entry in catalog.entries:
        if entry.predicate not in REGISTRY or entry.value == UNKNOWN:
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
        issues.extend(_entry_issues(entry, identity, by_id, sources))
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
    sources: Mapping[str, str],
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
    if entry.basis == "stated" and entry.value != UNKNOWN and not entry.support:
        refuse("unsupported-assertion", "a stated value cites the words that state it")
    if entry.basis == "legacy" and entry.support:
        refuse("legacy-with-support", "a legacy value is never support-backed")
    if entry.basis == "inferred" and not entry.premises:
        refuse("missing-premise", "an inferred value names what it rests on")
    if entry.basis == "derived" and not entry.explanation:
        refuse("missing-premise", "a derived value names the rule that derived it")
    if entry.assessment != "unchecked" and not entry.assessor:
        refuse("unassessed-assessor", "an assessment names who made it")

    issues.extend(_span_issues(entry, identity, sources))
    return issues


def _kinds(entry: Assertion) -> frozenset[str]:
    """The qualifier kinds this assertion's scope carries."""
    return frozenset(qualifier.kind for qualifier in entry.scope)


def _span_issues(
    entry: Assertion, identity: str, sources: Mapping[str, str]
) -> list[CatalogIssue]:
    """Whether each span still names the words it was taken from.

    Skipped where the caller supplied no sources, which is the one case a
    reader has to know about: without the text there is nothing to check a
    quote against, and inventing a pass would be the failure this module exists
    to prevent.
    """
    if not sources:
        return []
    issues = []
    for span in entry.support:
        text = sources.get(span.source_label)
        if text is None:
            issues.append(
                CatalogIssue(
                    code="dangling-source",
                    message=f"no source is labelled {span.source_label!r}",
                    assertion=identity,
                )
            )
            continue
        if span.digest != text_digest(text):
            issues.append(
                CatalogIssue(
                    code="stale-digest",
                    message=f"source {span.source_label!r} changed since this span"
                    " was taken",
                    assertion=identity,
                )
            )
            continue
        window = text[span.start : span.end]
        if span.end > len(text) or not verify_quote(span.quote, window):
            issues.append(
                CatalogIssue(
                    code="unverifiable-span",
                    message=f"offsets {span.start}-{span.end} of"
                    f" {span.source_label!r} do not hold this quote",
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
