"""The **Source Fact Bundle**: what the sources state, before any graph ID exists.

The resolver half of #1003's arm B. The one-step extraction reads the sources
and emits a **System Model**, so every fact it keeps has to fit a node the same
pass invented, and the assertion pass then reads that graph and cannot introduce
a component the graph left out. This module is the other order: a stage names
what the sources say in **local handles**, and code resolves those handles into
canonical IDs afterwards.

``build_pipeline`` routes here only where a deployment asks for the facts-first
head, which no production install sets. ``prompts/extract-facts.md`` and the
two prompts of the split route write what this reads. The names are
experimental interfaces, and :data:`BUNDLE_VERSION` says which spelling an
artifact was written under.

**The resolver constructs, and every input row gets a
:class:`DispositionRow`.** That is the contract the experiment measures
against: a fact that disappears between the bundle, the graph and the catalog
is a loss nobody can attribute, so a row reaching no output says which of the
five dispositions it took and why. :attr:`Resolution.gaps` is the sidecar that
carries them, outside the graph, because the System Model has no field for a
fact it could not hold.

Three rules decide the hard cases, and each one refuses rather than guesses.

**A mention with more than one role stays unresolved.** Competing referents are
preserved, never settled by array order: the bundle stage may decide
coreference, and where it declined, code does not decide for it.

**A component with no stated placement is placed only where the bundle names
exactly one zone**, and that placement is recorded as an
:class:`~analysis_service.system_model.Assumption` on ``trust_zone`` — the
convention ``prompts/extract.md`` rule 6 already uses for a required field the
sources do not state, and the one
:class:`~analysis_service.system_model.BoundaryCrossing` reads to mark an
inferred endpoint. Every other unplaced component is ``unsupported``.

**The resolver invents no Trust Boundary.** Where the bundle names no zone at
all, the model holds none and
:func:`~analysis_service.validation.validate` refuses it with
``no-trust-zones`` — visibly, through the gate every arm shares. Naming the one
zone that covers the system as described is the extraction stage's job, because
only that stage holds a source span to cite for it.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field

from analysis_service.assertions import (
    ABSENT,
    GRAPH_BOUND,
    MAX_ASSERTIONS,
    MAX_QUALIFIERS,
    MAX_SPANS,
    MAX_VALUE_CHARS,
    REGISTRY,
    UNPROJECTED,
    AssertionCatalog,
    AssertionProposal,
    AssertionRecord,
    Basis,
    CatalogIssueCode,
    CatalogProposal,
    Qualifier,
    QuoteProposal,
    SpanSource,
    SubjectType,
    UnknownReason,
    ambiguous_quote,
    span_source,
    spans_for,
)
from analysis_service.system_model import (
    UNKNOWN,
    Assumption,
    DataFlow,
    DataStore,
    Element,
    ExternalEntity,
    Process,
    SystemModel,
    TrustBoundary,
    ZonedElement,
    make_element_id,
    make_flow_id,
)
from analysis_service.validation import MAX_ELEMENTS

#: The bundle schema's own version, recorded beside any artifact written from
#: it. Not the **Claim** identity version and not
#: :data:`~analysis_service.system_model.FLOW_ID_VERSION`: those rule what an
#: ID means, and this rules what a bundle row may carry.
BUNDLE_VERSION = 2

#: The structural vocabulary's version, apart from :data:`BUNDLE_VERSION`
#: because a role added to :data:`ROLES` changes what a bundle can say without
#: changing the shape it says it in.
ROLE_VERSION = 1

#: What a local handle may be spelled as. Short, because it is a reference
#: inside one bundle and is never persisted: the canonical ID a handle resolves
#: to is what an artifact carries.
HANDLE_PATTERN = r"^[a-z][a-z0-9_]{0,31}$"

#: How long a handle may run, matching :data:`HANDLE_PATTERN`.
MAX_HANDLE_CHARS = 32

#: How long a reference may run. A reference field is **either** a handle of
#: this bundle or an **Element ID** a base model already holds, which is what
#: lets one resolver serve an empty graph and a patch over an existing one.
#: No pattern, because the two shapes are disjoint and :func:`_bound` is the
#: reader that decides which arrived — a pattern here would be a second one.
MAX_REFERENCE_CHARS = 300


@dataclass(frozen=True)
class RoleRule:
    """Which element class one structural role names, and what it settles.

    ``fixed`` holds the fields the role itself decides. Two element classes
    declare a required ``kind`` whose vocabulary has no ``unknown`` member — an
    **External Entity** is a human or an external system, and a **Trust
    Boundary** separates by network, privilege or tenancy — so a role that left
    them open would make the resolver choose a value no source stated. The
    vocabulary carries the distinction instead, which is why there are eight
    roles and not four.
    """

    #: A component or a zone, never a **Data Flow**: an interaction names a
    #: flow, and a mention names one of its endpoints. The annotation is what
    #: says so, so a role that tried would fail the checker rather than a test.
    element: type[TrustBoundary | ZonedElement]
    fixed: Mapping[str, str]


#: What a mention can be: one role per element class, with the two External
#: Entity kinds and the four Trust Boundary kinds spelled out.
Role = Literal[
    "person",
    "external-system",
    "process",
    "store",
    "network-zone",
    "privilege-zone",
    "tenant-zone",
    "other-zone",
]

#: Which element each role builds. A table rather than a branch: a role added
#: here projects the day it lands, and ``tests/test_factbundle.py`` holds the
#: table against :data:`Role` so neither side can gain an entry alone.
ROLES: Mapping[str, RoleRule] = MappingProxyType(
    {
        "person": RoleRule(ExternalEntity, MappingProxyType({"kind": "human"})),
        "external-system": RoleRule(
            ExternalEntity, MappingProxyType({"kind": "external-system"})
        ),
        "process": RoleRule(Process, MappingProxyType({})),
        "store": RoleRule(DataStore, MappingProxyType({})),
        "network-zone": RoleRule(TrustBoundary, MappingProxyType({"kind": "network"})),
        "privilege-zone": RoleRule(
            TrustBoundary, MappingProxyType({"kind": "privilege"})
        ),
        "tenant-zone": RoleRule(TrustBoundary, MappingProxyType({"kind": "tenant"})),
        "other-zone": RoleRule(TrustBoundary, MappingProxyType({"kind": "other"})),
    }
)

#: The roles that name a zone, read off the table so a zone role added tomorrow
#: is one of these without being listed again.
ZONE_ROLES: frozenset[str] = frozenset(
    role for role, rule in ROLES.items() if rule.element is TrustBoundary
)

#: The predicates whose value names a **graph** subject, so a bundle spells it
#: as a handle and the resolver rewrites it to the ID that handle became. Read
#: off the registry: a reference predicate pointing at a component, an
#: interaction or a zone is one of these, and one pointing at a principal, a
#: credential or an artifact is not, because those subjects are named rather
#: than built.
GRAPH_REFERENTS: frozenset[str] = frozenset(
    name
    for name, predicate in REGISTRY.items()
    if predicate.refers_to and predicate.refers_to <= GRAPH_BOUND
)

#: The predicates that say where a component sits. **The one reader of
#: placement**: a mention carries no zone field, so a component's
#: ``trust_zone`` and the catalog row behind it come from one fact. Read off
#: the graph field rather than by name, so a second placement predicate is
#: covered the day it is registered.
PLACEMENT_PREDICATES: frozenset[str] = frozenset(
    name
    for name, predicate in REGISTRY.items()
    if predicate.projects_into == "trust_zone"
)

#: The element fields code fills rather than the role: identity and a flow's
#: endpoints. Every other field a class requires takes
#: :data:`~analysis_service.system_model.UNKNOWN`, ``trust_zone`` included —
#: it starts there and :func:`_place` is the only thing that writes it, so an
#: element whose placement no fact states never leaves the resolver.
_SET_BY_CODE: frozenset[str] = frozenset({"id", "name", "source", "destination"})

#: What became of one bundle row. ``consumed`` reached the graph, ``preserved``
#: reached the catalog beside it, ``unresolved`` kept a question the bundle
#: raised, ``rejected`` failed a check code runs, and ``unsupported`` named a
#: fact neither target schema can express.
DispositionCode = Literal[
    "consumed",
    "preserved",
    "unresolved",
    "rejected",
    "unsupported",
]

#: The dispositions that reached an output. Derived nowhere else, because
#: :attr:`Resolution.gaps` is its complement.
LANDED: frozenset[str] = frozenset({"consumed", "preserved"})

#: Which table a disposition row is about. ``bundle`` is the whole submission,
#: for a bound no single row broke.
RowKind = Literal["bundle", "mention", "interaction", "fact", "unresolved"]

#: Which disposition each catalog refusal takes. ``unknown-predicate`` is the
#: one refusal that is a property of the *target schema* rather than of the
#: row: the registry holds no question for that fact, so it is unrepresented
#: rather than wrong. Keyed by code and held against
#: :data:`~analysis_service.assertions.CatalogIssueCode`, so a code added to
#: that registry is classified here before anything reads it.
REFUSAL_DISPOSITIONS: Mapping[str, DispositionCode] = MappingProxyType(
    {
        code: ("unsupported" if code == "unknown-predicate" else "rejected")
        for code in get_args(CatalogIssueCode)
    }
)

#: The most mentions and the most interactions one bundle may carry, each at
#: the element cap, because each one can become an element. The real bound is
#: :func:`~analysis_service.validation.validate`'s; this stops the resolver
#: walking a model that gate would refuse anyway.
MAX_MENTIONS = MAX_ELEMENTS
MAX_INTERACTIONS = MAX_ELEMENTS

#: The most facts one bundle may carry: the catalog's own cap, imported rather
#: than respelled, since every fact is a proposed assertion.
MAX_FACTS = MAX_ASSERTIONS

#: The most open questions one bundle may carry.
MAX_UNRESOLVED = 100


# --- What the extraction stage emits ----------------------------------------
#
# The **Proposal** shape, for the reason ``assertions.CatalogProposal`` gives:
# a model names its evidence in its own spelling and code decides where those
# words sit, so a span cannot claim a position the source does not hold. No
# offsets anywhere here, and no graph IDs anywhere here.


class MentionProposal(BaseModel):
    """One thing a source names, with the role or roles it could play.

    ``handle`` is local to this bundle. ``roles`` carries more than one entry
    where the stage read the mention two ways and declined to settle it, and
    the resolver then keeps the ambiguity rather than taking the first.

    **There is no zone field.** Where a component sits is a fact the predicate
    registry already registers, and a second field for it here would be a
    second reader of one question: a fact row states the placement, the
    resolver reads it to build ``trust_zone``, and the same row reaches the
    catalog carrying its own span.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    handle: str = Field(pattern=HANDLE_PATTERN)
    text: str = Field(min_length=1, max_length=200)
    roles: list[Role] = Field(default_factory=list, max_length=len(get_args(Role)))
    quotes: list[QuoteProposal] = Field(default_factory=list, max_length=MAX_SPANS)


class InteractionProposal(BaseModel):
    """One interaction between two mentions, in the direction it is initiated.

    ``action`` is what the initiator does at the receiver, in the source's own
    verb, and becomes the flow's label — so two interactions between one pair
    of endpoints stay separately addressable under
    :func:`~analysis_service.system_model.make_flow_id`.

    ``operations`` is what the initiator does to the receiver's data, in the
    closed vocabulary :class:`~analysis_service.system_model.DataFlow` holds.
    **A field, because a rule reads it**: STRIDE's store-tampering rule skips a
    read-only path, and a route that could not say which paths are read-only
    would take that rule's answer away from every job it ran. It is the verb's
    effect rather than the verb, which is why ``action`` cannot stand for it.

    Each endpoint is a mention handle of this bundle or an **Element ID** the
    base model holds, so an interaction can be added between two components
    that are already there.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    handle: str = Field(pattern=HANDLE_PATTERN)
    initiator: str = Field(min_length=1, max_length=MAX_REFERENCE_CHARS)
    receiver: str = Field(min_length=1, max_length=MAX_REFERENCE_CHARS)
    action: str = Field(min_length=1, max_length=200)
    protocol: str = Field(default=UNKNOWN, max_length=200)
    operations: Literal["read", "write", "read-write", "unknown"] = "unknown"
    quotes: list[QuoteProposal] = Field(default_factory=list, max_length=MAX_SPANS)


class FactProposal(BaseModel):
    """One fact a source states about one subject.

    ``subject_kind`` says which table ``subject`` reads from, so the handle
    namespace never has to carry the type: ``mention`` and ``interaction`` name
    a row of this bundle, and the other three name the assertion layer's own
    subjects by the words the source used.

    ``value`` reads by the predicate, as it does for an
    :class:`~analysis_service.assertions.AssertionProposal` — with one
    difference this stage needs: where the predicate refers to a **graph**
    subject, the value is a *handle* of this bundle rather than a written name,
    because no graph ID exists yet for it to name. The resolver rewrites it to
    the ID the handle became. :data:`GRAPH_REFERENTS` is the set, read off the
    registry.

    Everything else is
    :class:`~analysis_service.assertions.AssertionProposal`, field for field.
    The predicate registry is the service's and this stage may not extend it: a
    fact outside it stays source material rather than being forced into the
    nearest wrong predicate.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    handle: str = Field(pattern=HANDLE_PATTERN)
    subject_kind: Literal[
        "mention", "interaction", "principal", "credential", "artifact"
    ]
    subject: str = Field(min_length=1, max_length=300)
    predicate: str = Field(min_length=1, max_length=60)
    value: str = Field(min_length=1, max_length=MAX_VALUE_CHARS)
    reason: UnknownReason | None = None
    scope: list[Qualifier] = Field(default_factory=list, max_length=MAX_QUALIFIERS)
    basis: Basis
    quotes: list[QuoteProposal] = Field(default_factory=list, max_length=MAX_SPANS)
    explanation: str = Field(default="", max_length=1000)
    exclusive: bool = False


class UnresolvedProposal(BaseModel):
    """One question the extraction stage raised and did not answer.

    A missing referent, two readings it would not choose between, a
    contradiction across sources, or a request the vocabulary cannot carry. It
    is an output in its own right: the row a graph-first extraction has nowhere
    to put, and #1003's measurement of hedge and conflict preservation reads it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    handle: str = Field(pattern=HANDLE_PATTERN)
    question: str = Field(min_length=1, max_length=1000)
    quotes: list[QuoteProposal] = Field(default_factory=list, max_length=MAX_SPANS)


class SourceFactBundle(BaseModel):
    """What the sources state, in local handles, with no graph in sight.

    The stage that writes it is shown the labelled sources and this vocabulary,
    and nothing else: no reference graph, no expected facts, no aliases, no
    case identifier.
    """

    model_config = ConfigDict(extra="forbid")

    bundle_version: int = Field(default=BUNDLE_VERSION, ge=1)
    role_version: int = Field(default=ROLE_VERSION, ge=1)
    mentions: list[MentionProposal] = Field(default_factory=list)
    interactions: list[InteractionProposal] = Field(default_factory=list)
    facts: list[FactProposal] = Field(default_factory=list)
    unresolved: list[UnresolvedProposal] = Field(default_factory=list)


# --- What the resolver answers ----------------------------------------------


class DispositionRow(BaseModel):
    """What became of one bundle row, and why.

    One row per input row, always. ``target`` names what it became — an
    **Element ID**, a flow ID, or the subject ID a fact bound to — and is empty
    for every disposition but ``consumed`` and ``preserved``. ``code`` and
    ``message`` name the check that stopped it, and are empty for the two that
    did not stop.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    handle: str = Field(default="", max_length=MAX_HANDLE_CHARS)
    kind: RowKind
    disposition: DispositionCode
    target: str = Field(default="", max_length=300)
    code: str = Field(default="", max_length=60)
    message: str = Field(default="", max_length=1000)


@dataclass(frozen=True)
class Resolution:
    """One bundle, resolved: a graph, a catalog, and what every row came to.

    ``model`` is a candidate and not a **Valid System Model**:
    :func:`~analysis_service.validation.validate` is still the gate, and every
    arm of #1003 runs the same one. A bundle that named no zone yields a model
    with no Trust Boundary, which that gate refuses.

    ``proposal`` is the rows whose handles resolved, in the shape the ``assert``
    node emits — so a pipeline hands it to ``prepare`` at the key that node
    writes, and ``prepare`` resolves it through the seam it always used. There
    is no second injection point for an already-resolved catalog.

    ``record`` is what those rows came to **here**, over the model this bundle
    built. It is the reader of the dispositions and of an offline comparison; a
    pipeline that repairs the model resolves the proposal again over the model
    the gate passed, which is the model the rows should bind to.
    """

    model: SystemModel
    proposal: CatalogProposal
    record: AssertionRecord
    dispositions: tuple[DispositionRow, ...]

    @property
    def gaps(self) -> tuple[DispositionRow, ...]:
        """Every row that reached neither the graph nor the catalog.

        The sidecar #1003 asks for, derived from the disposition rows rather
        than collected beside them, so nothing can report a gap the
        dispositions do not hold or hide one they do.
        """
        return tuple(row for row in self.dispositions if row.disposition not in LANDED)


def unstated_fields(
    element_type: type[Element], fixed: Collection[str]
) -> dict[str, str]:
    """Every field ``element_type`` requires that neither the role nor code fills.

    Each one takes :data:`~analysis_service.system_model.UNKNOWN`, which is the
    value the extraction contract already asks for where a source is silent.

    **It raises on a required field whose vocabulary has no such member.** A
    closed field with no ``unknown`` is a fact a role has to settle, and a new
    one added to the schema must be classified in :data:`ROLES` rather than
    take whichever literal happens to come first. Read off the classes, so a
    field added to any of the five is covered the day it lands.
    """
    unstated = {}
    for name, field in element_type.model_fields.items():
        if not field.is_required() or name in fixed or name in _SET_BY_CODE:
            continue
        annotation = field.annotation
        if get_origin(annotation) is Literal and UNKNOWN not in get_args(annotation):
            raise ValueError(
                f"{element_type.__name__}.{name} is required and admits no"
                f" {UNKNOWN!r}; a role in ROLES has to settle it"
            )
        unstated[name] = UNKNOWN
    return unstated


#: Which half of a bundle each call of a split reading owns. A call that filled
#: the other half is not read for it: two producers of one list would let a
#: later call quietly restate what an earlier one settled, and #1003's split
#: exists to give each call **one** job. Keyed by the field, so a fifth list
#: added to the schema has to be assigned before a split route can carry it.
OWNED_BY: Mapping[str, str] = MappingProxyType(
    {
        "mentions": "inventory",
        "interactions": "inventory",
        "facts": "rows",
        "unresolved": "both",
    }
)


def joined(
    inventory: SourceFactBundle, rows: SourceFactBundle
) -> tuple[SourceFactBundle, tuple[DispositionRow, ...]]:
    """One bundle from a split reading's two emissions, and what each ignored.

    **Each call is read only for the half it owns.** A reading split in two is
    the answer to a question #1003 cannot ask of its own arms: whether the gap
    between graph-first and facts-first is the reading order or the number of
    calls the work is spread over. It is only an answer if each call does one
    job, so a mention proposed by the facts call and a fact proposed by the
    inventory call are dropped here — and reported, because a call answering
    the wrong half is a fact about the prompt rather than noise.

    ``unresolved`` is the one list both may write. A question is an output in
    its own right and either call can raise one; nothing downstream reads them
    as a set, so two sources cannot disagree about it.
    """
    ignored = []
    for field, owner in OWNED_BY.items():
        for call, held in (("inventory", inventory), ("rows", rows)):
            if owner in (call, "both"):
                continue
            for row in getattr(held, field):
                ignored.append(
                    DispositionRow(
                        handle=row.handle,
                        kind="bundle",
                        disposition="rejected",
                        code="wrong-call",
                        message=(
                            f"the {call} call proposed a {field} row, which the"
                            f" {owner} call owns"
                        ),
                    )
                )
    return (
        SourceFactBundle(
            mentions=list(inventory.mentions),
            interactions=list(inventory.interactions),
            facts=list(rows.facts),
            unresolved=[*inventory.unresolved, *rows.unresolved],
        ),
        tuple(ignored),
    )


def resolve_bundle(
    bundle: SourceFactBundle,
    sources: Mapping[str, str],
    base: SystemModel | None = None,
) -> Resolution:
    """Build the graph and the catalog one bundle describes, and account for it.

    Four passes, in the order their dependencies run: handles are checked once
    across every table, mentions become elements, interactions become flows
    between elements that were placed, and facts become assertions about
    whatever the first two produced. A row whose dependency failed is
    ``rejected`` rather than silently absent, which is what makes the
    disposition count equal the input count.

    ``base`` is a graph the bundle adds to rather than replaces. Arm B leaves
    it out and builds from nothing; the patch route
    (:mod:`analysis_service.patch`) passes the model an arm already produced,
    so a source-backed component omitted from that model is added **through
    this same code**. Every element of ``base`` is already taken, already a
    candidate zone, and already resolvable by its **Element ID** wherever a
    bundle row names a reference — which is how a new interaction reaches two
    components that are there and a new fact reaches one.

    The catalog half is the existing reader, called rather than copied:
    :meth:`~analysis_service.assertions.AssertionRecord.of` resolves the
    proposal and runs the gate over what it built, so a fact this route keeps
    is one the production route would keep too, and #1003's comparison measures
    the extraction order rather than two spellings of one gate.
    """
    refused = _version_issue(bundle) or _cap_issue(bundle)
    if refused is not None:
        return Resolution(SystemModel(), CatalogProposal(), _empty_record(), (refused,))

    held = base if base is not None else SystemModel()
    prepared = _prepared(sources)
    repeated = _repeated_handles(bundle)
    rows: list[DispositionRow] = []

    stated, competing, bases = _stated_placements(bundle, prepared, repeated)
    zones, zoned, assumptions, unplaced = _mentions(
        bundle, prepared, repeated, stated, competing, bases, held, rows
    )
    flows = _interactions(bundle, prepared, repeated, zoned, held, rows)
    model = SystemModel(
        external_entities=[
            element for element in zoned.values() if isinstance(element, ExternalEntity)
        ],
        processes=[
            element for element in zoned.values() if isinstance(element, Process)
        ],
        data_stores=[
            element for element in zoned.values() if isinstance(element, DataStore)
        ],
        data_flows=list(flows.values()),
        trust_boundaries=list(zones.values()),
        assumptions=[*held.assumptions, *assumptions],
    )
    proposal, record = _facts(
        bundle, sources, model, zones, zoned, flows, repeated, unplaced, rows
    )
    rows.extend(
        DispositionRow(
            handle=row.handle,
            kind="unresolved",
            disposition="unresolved",
            code="open-question",
            message=row.question,
        )
        for row in bundle.unresolved
        if row.handle not in repeated
    )
    rows.extend(
        DispositionRow(
            handle=handle,
            kind=kind,
            disposition="rejected",
            code="duplicate-handle",
            message=f"handle {handle!r} is claimed by more than one row",
        )
        for handle, kind in _repeated_rows(bundle, repeated)
    )
    return Resolution(model, proposal, record, tuple(rows))


def _empty_record() -> AssertionRecord:
    """The record a bundle nothing was read from produced."""
    return AssertionRecord(proposed=0, catalog=AssertionCatalog())


def _version_issue(bundle: SourceFactBundle) -> DispositionRow | None:
    """A bundle written under another spelling of the schema, refused whole.

    **Fail closed, and read no older version.** The shape and the role
    vocabulary each say what a row means, so resolving a bundle under the wrong
    one would bind facts by a rule its writer did not use. There is no
    migration: a bundle is model output, and re-running the stage costs less
    than a reader that guesses which spelling arrived.
    """
    for field, held, current in (
        ("bundle_version", bundle.bundle_version, BUNDLE_VERSION),
        ("role_version", bundle.role_version, ROLE_VERSION),
    ):
        if held != current:
            return DispositionRow(
                kind="bundle",
                disposition="rejected",
                code="wrong-version",
                message=(
                    f"{field} {held} is not {current}; this resolver reads one"
                    " spelling of the schema"
                ),
            )
    return None


def _cap_issue(bundle: SourceFactBundle) -> DispositionRow | None:
    """The one bound a bundle broke, or ``None``.

    Checked first and returned alone, as the validity gate's element cap is: a
    bundle too large to resolve is not made acceptable by naming which of its
    four hundred rows also failed a check.
    """
    for kind, count, cap in (
        ("mention", len(bundle.mentions), MAX_MENTIONS),
        ("interaction", len(bundle.interactions), MAX_INTERACTIONS),
        ("fact", len(bundle.facts), MAX_FACTS),
        ("unresolved", len(bundle.unresolved), MAX_UNRESOLVED),
    ):
        if count > cap:
            return DispositionRow(
                kind="bundle",
                disposition="rejected",
                code="too-many-rows",
                message=f"{count} {kind} rows proposed; the cap is {cap}",
            )
    return None


def _handles(bundle: SourceFactBundle) -> tuple[tuple[str, RowKind], ...]:
    """Every row's handle with the table it came from, in table order.

    **The one reader of "which rows does this bundle hold".** Both the repeated-
    handle rules read it, so a fifth table added to the schema reaches them
    together rather than one at a time.
    """
    rows: list[tuple[str, RowKind]] = []
    rows.extend((row.handle, "mention") for row in bundle.mentions)
    rows.extend((row.handle, "interaction") for row in bundle.interactions)
    rows.extend((row.handle, "fact") for row in bundle.facts)
    rows.extend((row.handle, "unresolved") for row in bundle.unresolved)
    return tuple(rows)


def _repeated_handles(bundle: SourceFactBundle) -> frozenset[str]:
    """Every handle more than one row of the bundle claims.

    One namespace across all four tables, so a fact naming ``m1`` can never
    reach a mention and an interaction both. A repeated handle rejects **every**
    row carrying it rather than letting the first win: which row the writer
    meant is not knowable here.
    """
    seen: set[str] = set()
    repeated: set[str] = set()
    for handle, _ in _handles(bundle):
        if handle in seen:
            repeated.add(handle)
        seen.add(handle)
    return frozenset(repeated)


def _repeated_rows(
    bundle: SourceFactBundle, repeated: Collection[str]
) -> tuple[tuple[str, RowKind], ...]:
    """Each row carrying a repeated handle, with the table it came from."""
    return tuple(row for row in _handles(bundle) if row[0] in repeated)


def _prepared(sources: Mapping[str, str]) -> Mapping[str, SpanSource]:
    """Every source folded once, keyed by label, for every quote taken from it.

    A source whose two folds disagree is absent here rather than wrong, exactly
    as it is for the assertion resolver: its quotes then find no span, which is
    the same outcome as a quote that is not in it.
    """
    built = {}
    for label, text in sources.items():
        folded = span_source(label, text)
        if folded is not None:
            built[label] = folded
    return built


@dataclass(frozen=True)
class _Cited:
    """One row's first usable quote: the words, and the source that holds them."""

    quote: str
    label: str


def _cite(
    quotes: Sequence[QuoteProposal], prepared: Mapping[str, SpanSource]
) -> tuple[_Cited | None, str, str]:
    """The first quote that locates in the source it names, or why none did.

    Four refusals, each a shape a quote can take rather than a judgement about
    it: the row proposes none, the label names no source this job carried, the
    words are not in that source, or the source holds them in more than one
    place. The last is
    :func:`~analysis_service.assertions.ambiguous_quote`, the one reader of that
    rule, so an element's citation and an assertion's span refuse the same
    repeated line.
    """
    if not quotes:
        return (
            None,
            "uncited",
            "the row proposes no quote, so nothing ties it to a source",
        )
    code = ""
    message = ""
    for proposed in quotes:
        folded = prepared.get(proposed.source_label)
        if folded is None:
            code = "dangling-source"
            message = f"no source is labelled {proposed.source_label!r}"
            continue
        if not spans_for(proposed.quote, folded):
            code = "unlocatable-quote"
            message = f"source {folded.label!r} does not hold {proposed.quote!r}"
            continue
        if ambiguous_quote(proposed.quote, folded.indexed.haystack):
            code = "ambiguous-quote"
            message = (
                f"source {folded.label!r} holds {proposed.quote!r} in more than"
                " one place, so it names no one of them"
            )
            continue
        return _Cited(proposed.quote, folded.label), "", ""
    return None, code, message


def _places(fact: FactProposal, prepared: Mapping[str, SpanSource]) -> bool:
    """Whether one placement row may decide where the graph puts its subject.

    **A row the catalog will refuse must not shape the graph first.** The
    placement a component takes and the assertion recording it come from one
    row, so the two answers have to rest on the same reading of it.

    Three refusals, in what each one says about the row. A value of
    :data:`~analysis_service.system_model.UNKNOWN` or
    :data:`~analysis_service.assertions.ABSENT` is an **epistemic value** and
    never a zone handle: the row says the sources leave the placement open, and
    reading it as the name of a zone would look for a zone called "unknown". A
    row carrying a scope places its subject under a condition, and the graph's
    ``trust_zone`` holds one zone under none. A ``stated`` row whose quotes
    locate in no source states nothing, by :func:`_cite` — the same reader the
    element citations and the assertion spans use.
    """
    if fact.value in (UNKNOWN, ABSENT) or fact.scope:
        return False
    return fact.basis != "stated" or _cite(fact.quotes, prepared)[0] is not None


def _stated_placements(
    bundle: SourceFactBundle,
    prepared: Mapping[str, SpanSource],
    repeated: Collection[str],
) -> tuple[Mapping[str, str], frozenset[str], Mapping[str, str]]:
    """Which zone handle each mention's facts place it in, and which disagree.

    Read from the fact rows, because :data:`PLACEMENT_PREDICATES` is the one
    reader of placement. Two rows placing one component in two zones settle
    nothing — the predicate's multiplicity is ``one`` and the catalog would
    call them a conflict — so the component is returned as competing rather
    than placed by whichever row came first.

    :func:`_places` decides which rows are read at all. The third return is the
    basis each placement rests on, so :func:`_place` can mark an inferred one:
    a zone nobody stated is an assumption whether code chose it or a reader did.
    """
    stated: dict[str, str] = {}
    bases: dict[str, str] = {}
    competing: set[str] = set()
    for fact in bundle.facts:
        if fact.handle in repeated or fact.predicate not in PLACEMENT_PREDICATES:
            continue
        if fact.subject_kind != "mention" or not _places(fact, prepared):
            continue
        held = stated.get(fact.subject)
        if held is not None and held != fact.value:
            competing.add(fact.subject)
        stated[fact.subject] = fact.value
        bases[fact.subject] = fact.basis
    return stated, frozenset(competing), bases


def _mentions(
    bundle: SourceFactBundle,
    prepared: Mapping[str, SpanSource],
    repeated: Collection[str],
    stated: Mapping[str, str],
    competing: Collection[str],
    bases: Mapping[str, str],
    held: SystemModel,
    rows: list[DispositionRow],
) -> tuple[
    dict[str, TrustBoundary],
    dict[str, ZonedElement],
    list[Assumption],
    frozenset[str],
]:
    """Turn every mention into a zone or a placed element, or say why not.

    Two passes, because placement reads the zones: the first builds every
    element the roles decide, and the second places the zoned ones. A component
    no fact places takes the sole zone where there is exactly one, with an
    :class:`~analysis_service.system_model.Assumption` naming why; with none or
    several it is ``unsupported``, because choosing between zones to obtain a
    binding is the failure #1003 names.
    """
    # Keyed by handle for what this bundle builds and by **Element ID** for
    # what ``base`` already held. The two shapes are disjoint —
    # :data:`HANDLE_PATTERN` admits no colon — so one table serves both, and
    # :func:`_bound` needs no second lookup to know which arrived.
    zones: dict[str, TrustBoundary] = {
        boundary.id: boundary for boundary in held.trust_boundaries
    }
    unplaced: dict[str, tuple[MentionProposal, ZonedElement]] = {}
    taken: set[str] = {element.id for element in held.elements()}

    for mention in bundle.mentions:
        if mention.handle in repeated:
            continue
        built = _build_mention(mention, prepared, taken, rows)
        if built is None:
            continue
        if isinstance(built, TrustBoundary):
            zones[mention.handle] = built
            rows.append(
                DispositionRow(
                    handle=mention.handle,
                    kind="mention",
                    disposition="consumed",
                    target=built.id,
                )
            )
        else:
            unplaced[mention.handle] = (mention, built)

    zoned: dict[str, ZonedElement] = {
        element.id: element for element in held.zoned_elements()
    }
    assumptions: list[Assumption] = []
    unplaced_handles: set[str] = set()
    sole = next(iter(zones.values())).id if len(zones) == 1 else ""
    for handle, (mention, element) in unplaced.items():
        placed, assumption, code, message = _place(
            mention,
            element,
            zones,
            stated.get(handle, ""),
            bases.get(handle, ""),
            handle in competing,
            sole,
        )
        if placed is None:
            unplaced_handles.add(handle)
            rows.append(
                DispositionRow(
                    handle=handle,
                    kind="mention",
                    disposition="unsupported",
                    code=code,
                    message=message,
                )
            )
            continue
        zoned[handle] = placed
        if assumption is not None:
            assumptions.append(assumption)
        rows.append(
            DispositionRow(
                handle=handle,
                kind="mention",
                disposition="consumed",
                target=placed.id,
            )
        )
    return zones, zoned, assumptions, frozenset(unplaced_handles)


def _build_mention(
    mention: MentionProposal,
    prepared: Mapping[str, SpanSource],
    taken: set[str],
    rows: list[DispositionRow],
) -> TrustBoundary | ZonedElement | None:
    """One mention's element, or a disposition row saying what stopped it.

    A mention with no role names something the vocabulary cannot type; one with
    several names something the stage read two ways. Both stay ``unresolved``,
    because each is a question rather than a defect, and #1003 measures how
    many of them a route leaves open.
    """

    def refuse(disposition: DispositionCode, code: str, message: str) -> None:
        rows.append(
            DispositionRow(
                handle=mention.handle,
                kind="mention",
                disposition=disposition,
                code=code,
                message=message,
            )
        )

    if len(mention.roles) != 1:
        named = ", ".join(mention.roles) or "nothing"
        refuse(
            "unresolved",
            "no-role" if not mention.roles else "competing-roles",
            f"mention {mention.text!r} reads as {named}; the bundle settles"
            " which, and code does not settle it by position",
        )
        return None

    cited, code, message = _cite(mention.quotes, prepared)
    if cited is None:
        refuse("rejected", code, message)
        return None

    rule = ROLES[mention.roles[0]]
    try:
        element_id = make_element_id(rule.element.id_prefix, mention.text)
    except ValueError:
        refuse(
            "rejected",
            "unnameable",
            f"mention {mention.text!r} normalizes to an empty slug, so it can"
            " carry no element ID",
        )
        return None
    if element_id in taken:
        refuse(
            "rejected",
            "duplicate-element",
            f"element ID {element_id!r} is already taken by another mention;"
            " two things named the same are one element or two names",
        )
        return None
    taken.add(element_id)
    # ``model_validate`` rather than the constructor: ``rule.element`` is any
    # of the five classes, and the fields a role settles differ per class, so
    # the keyword form would have to be written out once per class — which is
    # the branch :data:`ROLES` exists to replace.
    return rule.element.model_validate(
        {
            "id": element_id,
            "name": mention.text,
            "source_excerpt": cited.quote,
            "source_label": cited.label,
            **rule.fixed,
            **unstated_fields(rule.element, rule.fixed),
        }
    )


def _place(
    mention: MentionProposal,
    element: ZonedElement,
    zones: Mapping[str, TrustBoundary],
    placement: str,
    basis: str,
    competing: bool,
    sole: str,
) -> tuple[ZonedElement | None, Assumption | None, str, str]:
    """Put one component in its zone, or say why the graph cannot hold it.

    **A zone no source states is an assumption, whoever chose it.** A reader
    who inferred the placement and code taking the one zone a bundle names are
    the same fact about the graph: the sources did not put the component there.
    Both leave an :class:`~analysis_service.system_model.Assumption` on
    ``trust_zone``, which is what a reader of the model has to see.
    """
    if competing:
        return (
            None,
            None,
            "competing-placement",
            (
                f"more than one fact places {mention.text!r}, in different"
                " zones; the graph holds one trust_zone and the bundle settles"
                " which, not code"
            ),
        )
    if placement:
        boundary = zones.get(placement)
        if boundary is None:
            return (
                None,
                None,
                "dangling-zone",
                f"zone handle {placement!r} names no zone mention of this bundle",
            )
        assumed = (
            None
            if basis == "stated"
            else Assumption(
                assumption=f"{mention.text} sits in {boundary.name}",
                element_id=element.id,
                attribute="trust_zone",
                basis=f"no source places this component; the placement is {basis}",
            )
        )
        return element.model_copy(update={"trust_zone": boundary.id}), assumed, "", ""
    if not sole:
        return (
            None,
            None,
            "unplaced",
            (
                f"no source places {mention.text!r}, and the bundle names"
                f" {len(zones)} zones, so the graph's required trust_zone would"
                " be a choice nobody stated"
            ),
        )
    assumption = Assumption(
        assumption=f"{mention.text} sits in the one zone the sources describe",
        element_id=element.id,
        attribute="trust_zone",
        basis="no source places this component, and the bundle names one zone",
    )
    return element.model_copy(update={"trust_zone": sole}), assumption, "", ""


def _interactions(
    bundle: SourceFactBundle,
    prepared: Mapping[str, SpanSource],
    repeated: Collection[str],
    zoned: Mapping[str, ZonedElement],
    held: SystemModel,
    rows: list[DispositionRow],
) -> dict[str, DataFlow]:
    """Turn every interaction into a Data Flow between two placed elements."""
    flows: dict[str, DataFlow] = {flow.id: flow for flow in held.data_flows}
    taken: set[str] = set(flows)
    for interaction in bundle.interactions:
        if interaction.handle in repeated:
            continue
        built, code, message = _build_interaction(interaction, prepared, zoned, taken)
        if built is None:
            rows.append(
                DispositionRow(
                    handle=interaction.handle,
                    kind="interaction",
                    disposition="rejected",
                    code=code,
                    message=message,
                )
            )
            continue
        taken.add(built.id)
        flows[interaction.handle] = built
        rows.append(
            DispositionRow(
                handle=interaction.handle,
                kind="interaction",
                disposition="consumed",
                target=built.id,
            )
        )
    return flows


def _build_interaction(
    interaction: InteractionProposal,
    prepared: Mapping[str, SpanSource],
    zoned: Mapping[str, ZonedElement],
    taken: Collection[str],
) -> tuple[DataFlow | None, str, str]:
    """One interaction's flow, or the code and message that stopped it.

    Both endpoints must be components this resolver placed. A zone handle is
    refused here rather than coerced: a flow to a **Trust Boundary** is not a
    flow, and the graph says so by giving ``source`` and ``destination`` the
    zoned element types alone.
    """
    for field, handle in (
        ("initiator", interaction.initiator),
        ("receiver", interaction.receiver),
    ):
        if handle not in zoned:
            return (
                None,
                "dangling-endpoint",
                f"{field} {handle!r} names no placed component of this bundle",
            )
    cited, code, message = _cite(interaction.quotes, prepared)
    if cited is None:
        return None, code, message
    source = zoned[interaction.initiator]
    destination = zoned[interaction.receiver]
    try:
        flow_id = make_flow_id(source.id, destination.id, interaction.action)
    except ValueError:
        return (
            None,
            "unnameable",
            (
                f"action {interaction.action!r} normalizes to an empty slug, so"
                " the flow can carry no ID"
            ),
        )
    if flow_id in taken:
        return (
            None,
            "duplicate-flow",
            (
                f"flow ID {flow_id!r} is already taken; two interactions between"
                " one pair of endpoints need the verbs the sources used"
            ),
        )
    return (
        DataFlow.model_validate(
            {
                "id": flow_id,
                "name": interaction.action,
                "source": source.id,
                "destination": destination.id,
                "protocol": interaction.protocol,
                "operations": interaction.operations,
                "source_excerpt": cited.quote,
                "source_label": cited.label,
                **unstated_fields(DataFlow, ("protocol", "operations")),
            }
        ),
        "",
        "",
    )


#: Which subject type each bundle subject kind resolves to. The three graph
#: kinds are decided by what the handle turned into, and the other three are
#: the assertion layer's own, named by the words the source used.
_SUBJECT_TYPES: Mapping[str, SubjectType] = MappingProxyType(
    {
        "principal": "principal",
        "credential": "credential",
        "artifact": "artifact",
    }
)


def _bound(
    handle: str,
    zones: Mapping[str, TrustBoundary],
    zoned: Mapping[str, ZonedElement],
    flows: Mapping[str, DataFlow],
) -> tuple[str, SubjectType] | None:
    """What one handle became, with the subject type that ID carries.

    **The one reader of "did this handle reach the graph".** A subject and a
    reference value ask the same question of the same three tables, and asking
    it twice is how the two would come to disagree about an ID that resolves.
    """
    boundary = zones.get(handle)
    if boundary is not None:
        return boundary.id, "zone"
    element = zoned.get(handle)
    if element is not None:
        return element.id, "component"
    flow = flows.get(handle)
    if flow is not None:
        return flow.id, "interaction"
    return None


def _subject(
    fact: FactProposal,
    zones: Mapping[str, TrustBoundary],
    zoned: Mapping[str, ZonedElement],
    flows: Mapping[str, DataFlow],
) -> tuple[str | None, SubjectType, str, str]:
    """What one fact is about, resolved from its handle or read as a name."""
    if fact.subject_kind not in ("mention", "interaction"):
        return fact.subject, _SUBJECT_TYPES[fact.subject_kind], "", ""
    found = _bound(fact.subject, zones, zoned, flows)
    wanted = "interaction" if fact.subject_kind == "interaction" else "mention"
    if found is None:
        return (
            None,
            "component",
            "dangling-subject",
            f"subject {fact.subject!r} names no {wanted} this bundle built",
        )
    identity, subject_type = found
    if (subject_type == "interaction") != (fact.subject_kind == "interaction"):
        return (
            None,
            subject_type,
            "wrong-handle-kind",
            (
                f"subject {fact.subject!r} is a {subject_type}, and the row"
                f" calls it a {fact.subject_kind}"
            ),
        )
    return identity, subject_type, "", ""


def _referent(
    fact: FactProposal,
    zones: Mapping[str, TrustBoundary],
    zoned: Mapping[str, ZonedElement],
    flows: Mapping[str, DataFlow],
) -> tuple[str | None, str, str]:
    """One fact's value, with a graph handle rewritten to the ID it became.

    A value naming a principal, a credential or an artifact is left as written:
    those subjects are named rather than built, and the catalog slugs the name.
    Everything else passes through, including the value of a predicate no
    registry entry holds — that row is refused by
    :func:`~analysis_service.assertions.resolve_catalog`, which is the reader
    of what a predicate admits.
    """
    if fact.predicate not in GRAPH_REFERENTS:
        return fact.value, "", ""
    wanted = next(iter(REGISTRY[fact.predicate].refers_to))
    found = _bound(fact.value, zones, zoned, flows)
    if found is None:
        return (
            None,
            "dangling-value",
            (
                f"{fact.predicate} names {fact.value!r}, which is no handle"
                " this bundle built"
            ),
        )
    identity, subject_type = found
    if subject_type != wanted:
        return (
            None,
            "wrong-referent-type",
            (
                f"{fact.predicate} refers to a {wanted}, and {fact.value!r} is"
                f" a {subject_type}"
            ),
        )
    return identity, "", ""


def _facts(
    bundle: SourceFactBundle,
    sources: Mapping[str, str],
    model: SystemModel,
    zones: Mapping[str, TrustBoundary],
    zoned: Mapping[str, ZonedElement],
    flows: Mapping[str, DataFlow],
    repeated: Collection[str],
    unplaced: Collection[str],
    rows: list[DispositionRow],
) -> tuple[CatalogProposal, AssertionRecord]:
    """Turn every fact into an assertion, through the existing resolver.

    A fact whose subject or reference handle reached no output is ``rejected``
    here, before the catalog sees it: what it is about does not exist, so the
    row has nothing to say.

    **A component the bundle named and the graph could not place is a question,
    not a missing handle.** The sources say the thing exists and say this about
    it; what failed is the graph's required ``trust_zone``. Those rows are
    ``unresolved`` under ``unplaced-subject``, so a reader counting what a route
    lost can tell "nobody read this" from "the model cannot hold it", and the
    sidecar carries the fact rather than dropping it. Everything else goes to
    :meth:`~analysis_service.assertions.AssertionRecord.of`, the one reader of
    what a proposal came to — the production ``prepare`` node, the assertion
    eval mode and the offline replay all ask it, so a resolver change moves
    this route with them. What it refuses comes back by row index:
    ``unsupported`` where the registry holds no such predicate, ``rejected``
    for every other reason.

    A row the catalog kept is ``consumed`` where its predicate projects into a
    graph field and ``preserved`` where it does not, which is the split
    :data:`~analysis_service.assertions.UNPROJECTED` already rules.
    """
    proposals: list[AssertionProposal] = []
    handles: list[str] = []
    for fact in bundle.facts:
        if fact.handle in repeated:
            continue
        subject, subject_type, code, message = _subject(fact, zones, zoned, flows)
        value, value_code, value_message = _referent(fact, zones, zoned, flows)
        if subject is None or value is None:
            open_question = fact.subject in unplaced and fact.subject_kind == "mention"
            rows.append(
                DispositionRow(
                    handle=fact.handle,
                    kind="fact",
                    disposition="unresolved" if open_question else "rejected",
                    code="unplaced-subject" if open_question else (code or value_code),
                    message=(
                        f"the sources state this about {fact.subject!r}, and the"
                        " graph holds no zone to place that component in"
                        if open_question
                        else message or value_message
                    ),
                )
            )
            continue
        proposals.append(
            AssertionProposal(
                subject_type=subject_type,
                subject=subject,
                predicate=fact.predicate,
                value=value,
                reason=fact.reason,
                scope=fact.scope,
                basis=fact.basis,
                quotes=fact.quotes,
                explanation=fact.explanation,
                exclusive=fact.exclusive,
            )
        )
        handles.append(fact.handle)

    proposal = CatalogProposal(assertions=proposals)
    record = AssertionRecord.of(proposal, model, sources)
    refused: dict[int, tuple[DispositionCode, str, str]] = {}
    for issue in record.issues:
        if issue.row is None or issue.row in refused:
            continue
        refused[issue.row] = (
            REFUSAL_DISPOSITIONS[issue.code],
            issue.code,
            issue.message,
        )
    for index, (handle, row) in enumerate(zip(handles, proposals, strict=True)):
        stopped = refused.get(index)
        if stopped is not None:
            disposition, code, message = stopped
            rows.append(
                DispositionRow(
                    handle=handle,
                    kind="fact",
                    disposition=disposition,
                    code=code,
                    message=message,
                )
            )
            continue
        rows.append(
            DispositionRow(
                handle=handle,
                kind="fact",
                disposition=(
                    "preserved" if row.predicate in UNPROJECTED else "consumed"
                ),
                target=row.subject,
            )
        )
    return proposal, record
