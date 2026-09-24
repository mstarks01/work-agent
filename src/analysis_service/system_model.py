"""Canonical System Model: the extraction agent's output, every downstream agent's input.

Terminology for this schema lives in CONTEXT.md.

The five element types are the classic DFD-based STRIDE-per-element taxonomy.
Free-form security-relevant attributes accept the sentinel value ``"unknown"``,
meaning the input neither stated nor allowed inference of the fact; category agents
treat an unknown control as unverified, never as present or absent. Inferred
values are recorded in the attribute *and* in the top-level ``assumptions``
list — never as silent guesses.

Boundary crossings are derived, never extracted: a Data Flow crosses a trust
boundary iff its endpoints' zones differ.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import ClassVar, Literal, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field

from analysis_service.references import canonical

UNKNOWN = "unknown"

#: The attribute a :class:`ZonedElement` carries its zone in, and the one an
#: :class:`Assumption` names when extraction placed that element itself. Named
#: rather than spelled at each site because two readers of one attribute name
#: is two chances to spell it differently.
ZONE_ATTRIBUTE = "trust_zone"

# Controlled asset vocabulary; config-extendable via the validator's
# extra_asset_tags parameter (see analysis_service.validation).
#
# **Every tag names something an element holds.** An Asset is what an attacker
# acts on, and each of these is a class of data a source can state. What a
# failure would *cost* is not one: reputation loss is what the business suffers
# because an attacker acted, and whether a component being down matters is a
# judgement about the business rather than a fact about the system. Those
# belong to the person reading the report, and a deployment that wants to model
# one adds it through ``extra_asset_tags`` (#877).
CORE_ASSET_TAGS = frozenset(
    {
        "credentials",
        "pii",
        "financial",
        "health",
        "secrets",
        "business-critical-data",
    }
)

_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")

#: The apostrophes a writer types, elided rather than separated on.
#:
#: **An apostrophe sits inside a word, so it is not a word boundary.** Every
#: other non-slug character runs words together — a slash, a comma, a space —
#: and becomes one separator. An apostrophe does the opposite: treating it as a
#: separator cuts one word in two and leaves a segment that is not a word, so
#: "the calling team's users" slugged to ``calling-team-s-users`` with a stray
#: ``s``. Live extractions wrote that shape, and one signed reference subject
#: carried it.
#:
#: Both spellings, because a person types either and a model emits either: the
#: typewriter apostrophe and the typographic one. Nothing else is elided — a
#: backtick and a prime are not apostrophes in a name, and reading them as one
#: would run two words together instead.
_APOSTROPHES_RE = re.compile("['\u2019]")


#: An element ID's two halves, named so the ID pattern, the flow encoding and
#: the flow decoder read one spelling of each. A prefix is an element class's
#: ``id_prefix``; a slug is what :func:`normalize_name` produces.
_ID_PREFIX = r"[a-z_]+"
_ID_SLUG = r"[a-z0-9]+(?:-[a-z0-9]+)*"

#: A non-flow element ID: ``<prefix>:<slug>``, and exactly one colon.
_PLAIN_ID = rf"{_ID_PREFIX}:{_ID_SLUG}"

#: The character that separates a **Data Flow** ID's three parts.
#:
#: Picked against the ID alphabet rather than for how it reads. A part is a
#: prefix drawn from ``[a-z_]`` and a slug drawn from ``[a-z0-9-]``, so a colon
#: and a hyphen both occur *inside* a part and neither can delimit one — which
#: is what made version 1's ``-to-`` undecodable. ``>`` occurs in no part, so
#: splitting on it yields exactly three fields, and :func:`parse_flow_id` is
#: the decoder that holds the round trip.
#:
#: It is also inert where a flow ID is read. Every ID this service renders into
#: a prompt goes inside a backtick span or inside a JSON fence
#: (:func:`~analysis_service.graph.render_model`), and ``>`` closes neither.
FLOW_DELIMITER = ">"

#: Version 2's shape: ``flow:<source id><d><destination id><d><label slug>``,
#: with ``<d>`` the delimiter above.
_FLOW_ID_V2 = rf"flow:{_PLAIN_ID}{FLOW_DELIMITER}{_PLAIN_ID}{FLOW_DELIMITER}{_ID_SLUG}"

#: Version 1's shape: ``flow:<slug>-to-<slug>:<slug>``, the endpoints' type
#: prefixes dropped.
#:
#: Still part of the schema's bound because archived emissions and archived
#: reports hold it and are read back — ``evals.harness.stability`` and
#: ``evals.harness.extraction_losses`` both parse an archived system model
#: through this schema. **The pattern is a bound, not the rule.** Which shape a
#: live extraction may carry is decided by the gate's ``id-mismatch`` rule
#: against :func:`derive_element_id`, which writes :data:`FLOW_ID_VERSION` and
#: nothing else.
_FLOW_ID_V1 = rf"flow:{_ID_SLUG}:{_ID_SLUG}"


def normalize_name(name: str) -> str:
    """Normalize a human-readable name into the slug used inside element IDs.

    Two passes, and the order is the rule: an apostrophe is **elided** and
    every other non-slug character **separates**. So "the team's account"
    becomes ``the-teams-account`` rather than ``the-team-s-account``, and a
    slug carries no segment that is not a word.
    """
    lowered = _APOSTROPHES_RE.sub("", name.lower())
    slug = _NON_SLUG_CHARS_RE.sub("-", lowered).strip("-")
    if not slug:
        raise ValueError(f"name {name!r} normalizes to an empty slug")
    return slug


def make_element_id(prefix: str, name: str) -> str:
    """Build the deterministic typed-slug ID for a non-flow element."""
    return f"{prefix}:{normalize_name(name)}"


class FlowIdError(ValueError):
    """A flow ID cannot be decoded under the version it was asked for."""


@dataclass(frozen=True)
class FlowParts:
    """The three parts a flow ID is built from, as a decoder recovers them.

    What ``source`` and ``destination`` hold is the version's own business:
    version 2 recovers full **Element ID**s, version 1 recovers bare name slugs
    because its derivation dropped the type prefix. That is why the migration
    reads the original graph rather than the recorded ID (ADR 0037 rule 5), and
    why :func:`flow_label` is the only part a version-blind caller asks for.
    """

    source: str
    destination: str
    label: str


@dataclass(frozen=True)
class FlowIdRule:
    """One version of the flow identity: its shape, its builder and its decoder.

    All three in one entry, because a version recorded without its decoder is
    not compatibility (ADR 0037 rule 4). Every version stays computable, and
    :data:`FLOW_ID_RULES` is the table a reader of a versioned artifact looks
    its rule up in — and the table :data:`ELEMENT_ID` and
    :func:`flow_id_version` are both built from, so a version added here is
    admitted by the schema and recognised by the shape reader on the same line.
    """

    version: int
    #: The unanchored regular expression one ID of this version matches.
    pattern: str
    build: Callable[[str, str, str], str]
    parse: Callable[[str], FlowParts]


def _build_v1(source_id: str, destination_id: str, label: str) -> str:
    """Version 1: ``flow:<source slug>-to-<destination slug>:<label slug>``."""
    source_slug = source_id.split(":", 1)[-1]
    destination_slug = destination_id.split(":", 1)[-1]
    return f"flow:{source_slug}-to-{destination_slug}:{normalize_name(label)}"


def _parse_v1(flow_id: str) -> FlowParts:
    """Version 1's decoder, which recovers slugs and often cannot decide at all.

    ``-to-`` is drawn from the slug alphabet, so it occurs inside an endpoint's
    own slug as readily as between two of them: ``flow:a-to-b-to-c:read`` is
    three legal splits and this raises on it. That is the defect ADR 0037 fixes,
    stated as a property of the rule rather than as a comment — and it is why
    rule 5 builds the migration mapping from the graph.
    """
    body, _, label = _flow_body(flow_id, 1).rpartition(":")
    if not body or not label:
        raise FlowIdError(f"{flow_id!r} carries no version 1 label")
    halves = body.split("-to-")
    if len(halves) != 2:
        raise FlowIdError(
            f"{flow_id!r} splits {len(halves)} ways on version 1's '-to-';"
            " a version 1 ID whose endpoint slugs carry the separator names no"
            " pair of endpoints, which is why a migration reads the graph"
        )
    return FlowParts(source=halves[0], destination=halves[1], label=label)


def _build_v2(source_id: str, destination_id: str, label: str) -> str:
    """Version 2: the typed endpoint IDs and the label under :data:`FLOW_DELIMITER`."""
    return FLOW_DELIMITER.join(
        (f"flow:{source_id}", destination_id, normalize_name(label))
    )


def _parse_v2(flow_id: str) -> FlowParts:
    """Version 2's decoder. Three fields, because the delimiter is in no part."""
    fields = _flow_body(flow_id, 2).split(FLOW_DELIMITER)
    if len(fields) != 3:
        raise FlowIdError(
            f"{flow_id!r} splits into {len(fields)} fields on"
            f" {FLOW_DELIMITER!r}; a version 2 flow ID carries exactly three"
        )
    source, destination, label = fields
    return FlowParts(source=source, destination=destination, label=label)


def _flow_body(flow_id: str, version: int) -> str:
    """Everything after the ``flow:`` prefix, or raise naming the version."""
    if not flow_id.startswith("flow:"):
        raise FlowIdError(
            f"{flow_id!r} is not a flow ID; version {version}'s decoder reads"
            " an ID carrying the 'flow:' prefix"
        )
    return flow_id[len("flow:") :]


#: Every flow identity version this service can write or read, keyed by version.
#:
#: **Keyed, never branched.** A reader of a versioned artifact looks its rule up
#: here and gets both halves, so a version cannot be recorded without the
#: decoder that reads it. A version missing from this table raises rather than
#: falling back on the current rule, which would decode an old ID under a rule
#: it was never written by and return parts nobody wrote.
FLOW_ID_RULES: Mapping[int, FlowIdRule] = MappingProxyType(
    {
        1: FlowIdRule(1, _FLOW_ID_V1, _build_v1, _parse_v1),
        2: FlowIdRule(2, _FLOW_ID_V2, _build_v2, _parse_v2),
    }
)

#: Each version's shape, anchored: what :func:`parse_flow_id` holds an ID to
#: before it decodes, and what :func:`flow_id_version` asks to find out which
#: rule wrote one.
_FLOW_ID_SHAPES = MappingProxyType(
    {
        version: re.compile(rf"^{rule.pattern}$")
        for version, rule in FLOW_ID_RULES.items()
    }
)

#: The version this service writes. Version 1 dropped the endpoints' type
#: prefixes, so an entity and a process sharing one name derived one flow ID
#: (#989); version 2 encodes both endpoints' full IDs. Bumping it is a schema
#: change with a migration — see ``evals/harness/flow_ids.py``.
FLOW_ID_VERSION = 2


def flow_id_rule(version: int) -> FlowIdRule:
    """The rule that version writes and reads, or raise naming the table."""
    try:
        return FLOW_ID_RULES[version]
    except KeyError:
        raise FlowIdError(
            f"no flow identity rule is declared for version {version!r};"
            " add it to FLOW_ID_RULES with both its builder and its decoder"
        ) from None


def make_flow_id(
    source_id: str, destination_id: str, label: str, version: int = FLOW_ID_VERSION
) -> str:
    """Build the deterministic ID for a Data Flow under one identity version.

    ``source_id`` and ``destination_id`` are the endpoints' element IDs. Under
    the current version both are carried whole, prefix included, so two legal
    elements of different types sharing one name derive two flows.
    """
    return flow_id_rule(version).build(source_id, destination_id, label)


def parse_flow_id(flow_id: str, version: int = FLOW_ID_VERSION) -> FlowParts:
    """Decode a flow ID back into the three parts it was built from.

    The round trip against :func:`make_flow_id` is what makes the encoding
    unambiguous rather than merely readable, and ``tests/test_system_model.py``
    holds it over every version in :data:`FLOW_ID_RULES`.

    The ID is held to the version's shape before it is split, so a string that
    is not an ID of that version raises rather than decoding into three fields
    nobody wrote. Splitting alone would accept ``flow:a>b>c``, whose halves are
    not element IDs, and hand a caller parts that name nothing.
    """
    rule = flow_id_rule(version)
    if not _FLOW_ID_SHAPES[version].match(flow_id):
        raise FlowIdError(
            f"{flow_id!r} is not a version {version} flow ID; that version"
            f" writes {rule.pattern}"
        )
    return rule.parse(flow_id)


def flow_id_version(flow_id: str) -> int:
    """Which version's rule wrote one flow ID, decided by its shape.

    Sound because the shapes are disjoint **by construction** rather than by
    inspection: :data:`FLOW_DELIMITER` is outside the alphabet every part is
    drawn from, so a version 2 ID carries a character no version 1 ID can and
    no string is legal under both. Two matches or none raises, which is what
    keeps that property a check rather than an assumption.

    This is how a *single* ID is read. It is not how a *tree* is read: the
    corpus migration takes its source version as an argument, because a corpus
    holding both shapes must fail as a whole rather than have its unmigrated
    half quietly fixed.
    """
    matched = [
        version for version, shape in _FLOW_ID_SHAPES.items() if shape.match(flow_id)
    ]
    if len(matched) != 1:
        raise FlowIdError(
            f"{flow_id!r} matches {len(matched)} flow identity shapes"
            f" {matched or ''}; one ID is written by exactly one rule"
        )
    return matched[0]


def flow_label(flow_id: str) -> str:
    """The describing half of one flow ID, under whichever rule wrote it.

    **The one reader of "what does a flow call itself".** Splitting the ID on
    its last colon answered this while a flow ID ended in ``:<label>``, and
    two instruments did exactly that; under version 2 the same split returns
    the destination's slug glued to the label, and every alignment that turns on
    a label would have silently stopped matching.
    """
    return parse_flow_id(flow_id, flow_id_version(flow_id)).label


#: The shape every element ID this service builds already has: a plain
#: ``<prefix>:<slug>``, or any version's flow shape, taken from
#: :data:`FLOW_ID_RULES` so the schema admits every version whose decoder ships.
#:
#: Stated on the field because :func:`derive_element_id` cannot always be asked.
#: It raises when an element's *name* slugs to empty -- a name of ``"!!!"`` --
#: and the ``id-mismatch`` rule that would otherwise pin the ID to the derived
#: one is skipped exactly then, so the emitted ID survived verbatim with only a
#: length bound. ``references.py`` records that hole as a reference-resolution
#: one; it is also a fencing one, because an element ID is rendered into a lane
#: agent's prompt in a table that carries no fence of its own, and a value with
#: a newline and a backtick run there opens a block that swallows every fenced
#: block after it. A self-sized fence is only safe while its neighbours are
#: fenced too.
#:
#: The 300-character bound on the field is the other half of that fence, and
#: version 2 spends 9 to 13 characters of it: the two endpoint prefixes, their
#: colons and the third delimiter, less the four ``-to-`` costs. Measured over
#: the corpus the longest derived flow ID is 83 characters, so the headroom is
#: unchanged in practice — but a flow between two long names that fitted under
#: version 1 can now fail the gate as ``schema``, which is a refusal rather
#: than a silent truncation.
ELEMENT_ID = "^(?:{})$".format(
    "|".join([_PLAIN_ID, *(rule.pattern for _, rule in sorted(FLOW_ID_RULES.items()))])
)


class _Element(BaseModel):
    """Attributes common to every element type."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(max_length=300, pattern=ELEMENT_ID)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    assets: list[str] = Field(default_factory=list, max_length=len(CORE_ASSET_TAGS) * 4)
    source_excerpt: str = Field(default="", max_length=1000)
    # Which Source the *excerpt* came from — the quote, not the element. A
    # verbatim quote has exactly one origin by construction, so this field can
    # never be half-true, where a list of labels beside one quote would leave
    # the pairing unstated. An element drawing on several sources records that
    # in ``notes``. Gate-enforced against the job's labels: see
    # :func:`~analysis_service.validation.validate`.
    source_label: str = Field(default="", max_length=200)
    # Who spoke the quote, where the text attributes it. Provenance on a quote,
    # never a role and never a claim a category agent weighs. It exists to be
    # *strippable*: a participant name inside a verbatim excerpt is unreachable,
    # while one in its own field is a single delete. Never gated — a wrong or
    # missing speaker must not fail a job.
    source_speaker: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=2000)


class ExternalEntity(_Element):
    """An actor outside the system's control: users, third-party systems."""

    id_prefix: ClassVar[str] = "entity"

    kind: Literal["human", "external-system"]
    trust_zone: str


class Process(_Element):
    """Running code or a component that transforms data.

    ``interface_kind`` says what kind of interface this process presents, which
    is a different fact from what any connection to it carries. A process can
    present a web interface over a transport nobody stated, and a flow can state
    HTTPS to something that is not an application at all. Keeping the two apart
    is what lets a framework scope itself to web applications without a model
    having to guess at transport — see
    :func:`~analysis_service.frameworks.asvs.rules.asvs_precondition`.

    ``web`` covers the HTTP family as an application presents it: a browser UI, a
    REST, GraphQL or SOAP API, a websocket endpoint. ``non-web`` is anything
    else — a batch job, a broker, a daemon reading a queue. ``unknown`` is the
    default the extraction starts from and the honest answer whenever the input
    does not say, exactly as it is for every other attribute here.
    """

    id_prefix: ClassVar[str] = "process"

    technology: str = Field(max_length=200)
    trust_zone: str
    exposure: Literal["internet-facing", "internal", "unknown"]
    interface_kind: Literal["web", "non-web", "unknown"]


class DataStore(_Element):
    """Where data rests: databases, buckets, queues-at-rest."""

    id_prefix: ClassVar[str] = "store"

    technology: str = Field(max_length=200)
    trust_zone: str
    data_classification: str = Field(max_length=200)
    encryption_at_rest: str = Field(max_length=200)


class DataFlow(_Element):
    """A directed connection carrying data. Direction = who initiates."""

    id_prefix: ClassVar[str] = "flow"

    source: str
    destination: str
    protocol: str = Field(max_length=200)
    authentication: str = Field(max_length=200)
    data_description: str = Field(max_length=1000)
    encryption_in_transit: str = Field(max_length=200)
    #: What the initiator does at the destination — the fact ``direction`` does
    #: **not** carry. A service reading a database and a service writing one are
    #: the same shape without this, so a rule about writes fired on both and told
    #: an agent a read-only path could be tampered with.
    #:
    #: A closed vocabulary because it is a question with a finite answer, unlike
    #: a control whose value is the mechanism the submitter described. ``unknown``
    #: is the extraction sentinel and the honest default: a description that says
    #: a service "talks to" a store settles nothing, and the evidence catalog
    #: turns that into a fact an agent can ground a question on, because
    #: :func:`~analysis_service.analysis.control_state` reads the word.
    #:
    #: It is not a **Control**: no control is missing when a flow only reads, so
    #: it stays out of :data:`~analysis_service.analysis.CONTROL_ATTRIBUTES` and
    #: an ``unknown`` here is never counted as an unstated control.
    #:
    #: **The default is the one place a default is honest.** Every other
    #: attribute is required, so an extraction that omits one fails rather than
    #: reading as silence. This one carries ``unknown`` because a report written
    #: before the field existed has to keep loading — every artifact under
    #: ``evals/runs/`` predates it — and ``unknown`` is exactly what those
    #: models say about operations: nobody stated them. A live extraction is
    #: still asked for the value by ``prompts/extract.md`` and graded on it by
    #: the extraction scorer, so omitting it costs an attribute against the
    #: blessed model rather than passing unnoticed.
    operations: Literal["read", "write", "read-write", "unknown"] = "unknown"


class TrustBoundary(_Element):
    """A named flat trust zone: network zones, auth boundaries, privilege levels.

    ``kind`` says *what separates* this zone from the rest, and the four values
    are ordered by how much a reader learns from them. Two questions decide it,
    in this order: **who controls the zone**, then **what authority the zone
    holds**.

    ``tenant``
        A different party controls it — another company, a franchise, a vendor's
        hosting, a customer's own hardware, another team's environment. The
        boundary is *whose* rather than *where*.
    ``privilege``
        The same party controls both sides, and this side holds authority the
        other does not: an administrative zone, a control plane, a production
        estate reached from a corporate one.
    ``network``
        The same party, the same authority, a different network location. A
        DMZ, an internal tier, a segment.
    ``other``
        None of the three fit. This is the last resort, not the default: a
        zone typed ``other`` tells a downstream rule nothing at all.
    """

    id_prefix: ClassVar[str] = "boundary"

    kind: Literal["network", "privilege", "tenant", "other"]


Element = ExternalEntity | Process | DataStore | DataFlow | TrustBoundary


def attribute_names(element: Element) -> tuple[str, ...]:
    """The security-relevant attributes of one element, in declaration order.

    An element's type-specific fields and nothing else: the five subclasses
    declare exactly the facts STRIDE reasons from, while ``_Element`` carries
    identity and provenance — ``id``, ``name``, ``description``, ``assets``,
    the three source fields and ``notes`` — which say what an element *is*
    rather than what is true of it. Derived from the classes themselves, so a
    field added to either side moves the line without anything here changing.

    Declaration order rather than a set, because
    :func:`~analysis_service.evidence.evidence_catalog` walks this to build
    stable evidence IDs and a set's iteration order is not the model's.
    """
    return tuple(
        name for name in type(element).model_fields if name not in _Element.model_fields
    )


def all_attribute_names() -> tuple[str, ...]:
    """Every security-relevant attribute any element type declares, sorted.

    The union of :func:`attribute_names` over the five element classes, read
    off the classes so it moves with them. It is what a provider-facing schema
    may list where a field names an attribute: a critic constrained to this
    list cannot spell ``description`` or ``notes``, which are identity fields
    and never a place a fact lives, and the per-type check at the review seam
    still decides whether the named attribute belongs to the named element.
    """
    return tuple(
        sorted(
            {
                name
                for element_type in get_args(Element)
                for name in element_type.model_fields
                if name not in _Element.model_fields
            }
        )
    )


def assumable_attributes(element: Element) -> tuple[str, ...]:
    """Every field an **Assumption** may name, derived from the schema.

    :func:`attribute_names` plus ``assets``. The type-specific fields are the
    facts an element's *type* makes true of it; ``assets`` is the one fact
    every type can hold, which is why it sits on ``_Element`` beside identity
    rather than beside them. It is still a fact about the element and still
    one a rule reads, so an inference can land there and has to be recordable:
    the corpus infers ``pii`` on a store whose ``data_classification`` nobody
    stated, and an assumption with nowhere to point would have to be dropped.

    One name added to a derived tuple, never a list of its own. A field added
    to any element type is covered the day it lands, and
    ``tests/test_validation.py`` compares this against the schema so the one
    exception stays the only one.
    """
    return (*attribute_names(element), "assets")


def assumable_attribute_names() -> tuple[str, ...]:
    """Every name an **Assumption** may carry, over all element types.

    The union counterpart of :func:`assumable_attributes`, which answers for
    one element: this answers "could any element carry this name at all", which
    is the question a provider-facing schema can put and the per-element check
    cannot. Both read the same classes, and ``tests/test_validation.py`` holds
    this to the union of that one over every element type, so a field added to
    a type reaches both the day it lands.

    **Spelled once because four fields name an attribute** -- the two ``Ground``
    branches, the :class:`Assumption` and its compact form. The enum sat on one
    of them, so a model was told the closed set in one place and asked for free
    text in three, and one wrote a whole sentence into
    ``Assumption.attribute``: legal against ``max_length=100``, and refused by
    the gate one layer later.

    ``assets`` rides along here and not in :func:`all_attribute_names`, for the
    reason :func:`assumable_attributes` gives: it is the one fact every type
    can hold, so an inference can land on it.
    """
    return (*all_attribute_names(), "assets")


def derive_element_id(element: Element) -> str:
    """The deterministic ID an element's type, name, and endpoints imply.

    The single definition of the ID invariant: the validity gate compares an
    emitted ID against this, and :func:`normalize_element_ids` overwrites with
    it. Raises ValueError when a name normalizes to an empty slug.
    """
    if isinstance(element, DataFlow):
        return make_flow_id(element.source, element.destination, element.name)
    return make_element_id(element.id_prefix, element.name)


# Elements that belong to a trust zone (everything except flows and boundaries).
ZonedElement = ExternalEntity | Process | DataStore


class Assumption(BaseModel):
    """A value extraction inferred on the record, with a stated basis.

    ``element_id`` and ``attribute`` together name *what* was inferred, which
    is the whole reason the pair exists rather than the element alone. An
    element carries several security-relevant attributes and often more than
    one stated value; an assumption naming only the element leaves a reader
    unable to tell which of them the basis covers. The gate resolves the pair
    (:func:`~analysis_service.validation.validate`), so the link is checked
    rather than asserted.

    The value is deliberately *not* carried. It is ``getattr(element,
    attribute)``, and a copy beside it would be two fields kept in agreement
    by hand — the mechanical constraint :func:`normalize_element_ids` exists
    to remove from element IDs.

    An assumption is an inference, so the attribute it names holds one: the
    gate refuses an assumption on an attribute that is still unverified. A
    hedge somebody voiced is not an inference this service made, and it
    belongs in ``notes``.
    """

    model_config = ConfigDict(extra="forbid")

    assumption: str = Field(min_length=1, max_length=1000)
    element_id: str = Field(max_length=300)
    # The provider-facing schema lists the legal names, on the rule
    # :class:`~analysis_service.claims.Ground` already follows: a model asked
    # for a bounded string writes prose into it, and one wrote a 100-character
    # sentence here. The enum is a schema fact and not a validator, so a model
    # written before it still loads and a value outside the set stays an
    # ``invalid-reference`` the repair pass can answer narrowly, rather than a
    # schema fault that condemns the whole object. Whether the named attribute
    # belongs to the *named element* stays with the gate.
    attribute: str = Field(
        min_length=1,
        max_length=100,
        json_schema_extra={"enum": list(assumable_attribute_names())},
    )
    basis: str = Field(min_length=1, max_length=1000)


class BoundaryCrossing(BaseModel):
    """Derived fact: a Data Flow whose endpoints the model cannot place together.

    ``decided`` says which of the two readings this is. A decided crossing has
    two zones the model holds and they differ. An **undecidable** crossing has
    at least one endpoint the sources never placed, so ``source_zone`` or
    ``destination_zone`` is :data:`UNKNOWN` and nothing here says a boundary was
    crossed — only that no comparison could rule it out
    ([ADR 0039](../../docs/adr/0039-a-crossing-a-model-cannot-decide-is-still-a-lead.md)).
    Two equal known zones are not a crossing at all and appear here in neither
    form.

    **An undecidable crossing establishes nothing.** It confers eligibility for
    analysis and no more: not that a crossing occurred, not that a control is
    missing, not that an attack succeeds. A rule whose premise is the crossing
    itself fires on one and says so; a rule whose premise is what the zones
    *are* cannot, and skips it.

    ``assumed_endpoints`` names the endpoints whose zone the service *inferred*
    rather than read, in source-then-destination order, and is empty for a
    crossing both of whose zones the input stated. It is derived from the same
    model as the crossing — an :class:`Assumption` on ``trust_zone`` — so the
    two cannot disagree. It keeps its meaning for the placements a model still
    infers: an inferred zone is a value the model chose, and an unknown one is a
    value it declined to choose.

    **The field is here because this is where the inference lands.** An
    Assumption sits on the model's top-level list, and a lane agent reading a
    crossing has to join the two to find out that a zone was inferred. The
    crossing is the strongest input that agent reads, so a crossing that rests
    on an inference has to say so at the place it is read.

    It carries element IDs rather than zone names: the zones are already in
    ``source_zone`` and ``destination_zone``, and what a reader needs is which
    *end* of this flow was placed by the service.
    """

    model_config = ConfigDict(extra="forbid")

    flow_id: str
    source_zone: str
    destination_zone: str
    #: False when either zone is :data:`UNKNOWN`. Read it rather than comparing
    #: the zones again, so the three outcomes have one reader.
    decided: bool = True
    assumed_endpoints: list[str] = Field(default_factory=list)


class SystemModel(BaseModel):
    """The canonical structured representation of the system under analysis."""

    model_config = ConfigDict(extra="forbid")

    external_entities: list[ExternalEntity] = Field(default_factory=list)
    processes: list[Process] = Field(default_factory=list)
    data_stores: list[DataStore] = Field(default_factory=list)
    data_flows: list[DataFlow] = Field(default_factory=list)
    trust_boundaries: list[TrustBoundary] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)

    def elements(self) -> list[Element]:
        """All elements in a stable order: :data:`ELEMENT_GROUPS`, each in list order."""
        return [element for group in ELEMENT_GROUPS for element in getattr(self, group)]

    def zoned_elements(self) -> list[ZonedElement]:
        """The elements that carry a ``trust_zone``, in a stable order."""
        return [*self.external_entities, *self.processes, *self.data_stores]

    def get(self, element_id: str) -> Element | None:
        """Look up an element by ID, or None if absent.

        The convenience form, for a caller holding a model and one ID. It
        builds a whole :class:`ModelIndex` to answer, exactly as
        :func:`~analysis_service.grounding.verify_quote` folds a whole source
        to answer one quote, so that how an element is found by ID is written
        down once. A caller asking more than once — the critic asks per claim,
        and per grounds entry within a claim — builds the index itself.

        What the convenience costs is the index's fixed part: 2.4 us on a
        five-element model, against 0.6 us for the element walk alone. The walk
        is what grows with the model, so the share falls as the model gets
        bigger, and no production path takes this form.
        """
        return ModelIndex.of(self).get(element_id)

    def assumed_zone_elements(self) -> frozenset[str]:
        """Every element whose ``trust_zone`` the service inferred rather than read.

        The one reader of that question. A zone is inferred when the model
        records an :class:`Assumption` naming the element and
        :data:`ZONE_ATTRIBUTE`; the gate resolves both, so an entry here names
        an element this model holds and an attribute that holds a value.

        **Every basis, not only a disagreement.** Extraction infers a zone from
        silence far more often than from two sources that conflict, and both
        readings put a zone on the record that the input never stated. The rule
        is a property of the entry rather than of the prose behind it, so it
        needs no basis parsed to decide.
        """
        return frozenset(
            assumption.element_id
            for assumption in self.assumptions
            if assumption.attribute == ZONE_ATTRIBUTE
        )

    def boundary_crossings(self) -> list[BoundaryCrossing]:
        """Derive boundary crossings mechanically. Requires a valid model.

        Raises ValueError on a dangling flow endpoint — derivation on an
        invalid model would produce misleading framework input, so it fails
        closed instead of skipping.

        **Three outcomes, not two.** Both zones known and different is a
        crossing; both known and equal is not; either unknown is an
        *undecidable* crossing, which carries ``decided=False`` and the unknown
        zone as it stands. An endpoint the sources never placed is a value here
        rather than a refusal, so an unplaced component still reaches a reader.

        Each crossing also names the endpoints whose zone was inferred, read
        from :meth:`assumed_zone_elements`. A crossing that both zones state
        outright names none, which is the ordinary case.
        """
        zone_by_id = {
            element.id: element.trust_zone for element in self.zoned_elements()
        }
        assumed = self.assumed_zone_elements()
        crossings = []
        for flow in self.data_flows:
            for endpoint in (flow.source, flow.destination):
                if endpoint not in zone_by_id:
                    raise ValueError(
                        f"cannot derive crossings: flow {flow.id!r} endpoint "
                        f"{endpoint!r} is not a zoned element in this model"
                    )
            source_zone = zone_by_id[flow.source]
            destination_zone = zone_by_id[flow.destination]
            decided = UNKNOWN not in (source_zone, destination_zone)
            if decided and source_zone == destination_zone:
                continue
            crossings.append(
                BoundaryCrossing(
                    flow_id=flow.id,
                    source_zone=source_zone,
                    destination_zone=destination_zone,
                    decided=decided,
                    assumed_endpoints=[
                        endpoint
                        for endpoint in (flow.source, flow.destination)
                        if endpoint in assumed
                    ],
                )
            )
        return crossings

    def shared_names(self) -> dict[str, list[str]]:
        """Name slugs more than one zoned element claims, mapped to their IDs.

        An element ID is ``type:name-slug``, so two elements of *different*
        types can share a name and still hold distinct IDs. The gate's
        ``duplicate-id`` rule compares whole IDs, so it passes them cleanly.
        That pair is what ``extract.md``'s "nothing gets two types" warns
        about: one real thing transcribed twice, once as a process and once as
        a store.

        **A suspicion, never a verdict.** It is not always wrong — a system
        really can run a process and keep a store that share a name — so this
        returns what it found and rules on nothing. Nothing routes on it; see
        :class:`~analysis_service.claims.SharedElementName` for where it lands
        and why it is marked rather than failed.

        Reads IDs rather than names because the gate has already pinned every
        ID to :func:`derive_element_id`, which is the name slug by
        construction. Same-type collisions never appear here: two elements of
        one type sharing a name hold the *same* ID, which is ``duplicate-id``'s
        to report.

        Zoned elements only. A trust boundary is a zone rather than a thing in
        the system, so a boundary and a process sharing a name is ordinary
        naming rather than a doubled transcription. Flows are excluded by that
        logic and a stronger one: a flow ID is built from its endpoints, so it
        is never a bare type-and-name pair.
        """
        by_slug: dict[str, set[str]] = {}
        for element in self.zoned_elements():
            by_slug.setdefault(element.id.split(":", 1)[-1], set()).add(element.id)
        return {
            slug: sorted(ids) for slug, ids in sorted(by_slug.items()) if len(ids) > 1
        }


@dataclass(frozen=True)
class ModelIndex:
    """The lookups a validated model answers repeatedly, computed once.

    Three questions the critic asks per claim, and each one walks the whole
    model to answer: which element carries this ID, which two elements does
    this flow run between, and which flows touch this element. A model of 200
    processes and 400 flows answered them 2,000 times in 0.104 s of a node
    body's own CPU; over this index the same run costs 0.003 s.
    ``evals/bench/deterministic.py index`` re-derives both.

    **Built by an explicit call rather than cached on the model.**
    :func:`normalize_element_ids` rewrites element IDs in place, so an index a
    model kept would answer for the IDs the model held before the rewrite, and
    no call site would say so. A caller builds one from the model it is about to
    read many times, which makes it a snapshot of that model by construction:
    the model it indexes has already been through the validity gate, and no
    later pass mutates it.
    """

    #: Every element of the model, by ID.
    elements: Mapping[str, Element]

    #: Each flow's ID against the two elements it runs between.
    flow_endpoints: Mapping[str, tuple[str, str]]

    #: Each element's ID against every element and flow one hop from it. A
    #: flow's own ID is absent here: :attr:`flow_endpoints` answers for a flow,
    #: and one hop from a flow is exactly its two endpoints.
    adjacent: Mapping[str, frozenset[str]]

    @classmethod
    def of(cls, model: SystemModel) -> ModelIndex:
        """Index one validated model. The model is read, never held."""
        endpoints = {
            flow.id: (flow.source, flow.destination) for flow in model.data_flows
        }
        adjacent: dict[str, set[str]] = {}
        for flow_id, (source, destination) in endpoints.items():
            for endpoint in (source, destination):
                adjacent.setdefault(endpoint, set()).update(
                    (flow_id, source, destination)
                )
        return cls(
            elements=MappingProxyType(
                {element.id: element for element in model.elements()}
            ),
            flow_endpoints=MappingProxyType(endpoints),
            adjacent=MappingProxyType(
                {place: frozenset(reach) for place, reach in adjacent.items()}
            ),
        )

    def get(self, element_id: str) -> Element | None:
        """Look up an element by ID, or ``None`` if absent."""
        return self.elements.get(element_id)

    def reach(self, places: Collection[str]) -> frozenset[str]:
        """``places`` plus every element one hop away in the graph.

        A flow reaches its two endpoints. An element reaches the flows that
        touch it and the elements at their far ends, and nothing further.
        :func:`~analysis_service.fan_in.within_bound` applies it
        :data:`~analysis_service.fan_in.BOUND_HOPS` times for the bound on a
        claim's elements. A place the model does not contain reaches only itself.
        """
        reach = set(places)
        for place in places:
            endpoints = self.flow_endpoints.get(place)
            if endpoints is None:
                reach.update(self.adjacent.get(place, ()))
            else:
                reach.update(endpoints)
        return frozenset(reach)


# An element ID as it appears inside prose. Flows carry a second segment and
# nothing else does, so the two shapes are spelled separately rather than as one
# optional group that would read ``store:orders-db:anything`` as a store.
#
# Built from the element classes' own ``id_prefix``, so a sixth element type
# joins this pattern by existing rather than by someone remembering. Matched
# case-insensitively and snapped afterwards by every reader, for the reason
# every other reference is: the spelling is not the claim. It lives here, with
# the ID grammar, because its two readers -- the fan-in and the coverage
# account -- import each other's neighbours and neither may import the other.
_FLOW_PREFIX = DataFlow.id_prefix
_OTHER_PREFIXES = sorted(
    element.id_prefix for element in get_args(Element) if element is not DataFlow
)
_SLUG = r"[a-z0-9-]+"
# Its own slug, looser than the grammar's on purpose — see the docstring — but
# the flow **delimiter** comes from :data:`FLOW_DELIMITER` rather than being
# spelled again. That is the fact two readers would disagree about: a prose
# reader still splitting a flow ID on a colon would find its first endpoint and
# report the rest as a second citation.
_MENTION_PLAIN = rf"(?:{'|'.join(_OTHER_PREFIXES)}):{_SLUG}"
_MENTION_FLOW = (
    rf"{_FLOW_PREFIX}:{_MENTION_PLAIN}{FLOW_DELIMITER}"
    rf"{_MENTION_PLAIN}{FLOW_DELIMITER}{_SLUG}"
)
_MENTION_RE = re.compile(
    rf"\b(?:{_MENTION_FLOW}|{_MENTION_PLAIN})",
    re.IGNORECASE,
)


def mentioned_ids(description: str) -> list[str]:
    """Every element ID a description names in prose, in the order written.

    Deliberately narrow: a token has to open with one of the five real type
    prefixes and a colon, which is a shape ordinary English does not produce —
    ``"Process: the web app"`` has a space and does not match. The cost of that
    narrowness is a miss rather than a false alarm, which is the right way
    round for a check whose output annotates a finding a human will read.

    Measured over the 18 hand-authored descriptions in STRIDE's own lane
    exemplars, the closest thing the repo holds to real agent prose: **24 distinct IDs
    extracted, 0 of them spurious** — every token found is one of the two
    exemplar systems' 24 real element IDs, and all 24 are found. Small, and the
    only corpus of threat descriptions that exists; enough to say the pattern
    reads prose without inventing citations in it.

    Trailing hyphens are trimmed because prose runs an ID into an em-dash
    substitute more often than a real slug ends in one; ``normalize_name``
    strips them, so no legal ID ends in a hyphen anyway.
    """
    return [match.group().rstrip("-") for match in _MENTION_RE.finditer(description)]


#: The five fields of a :class:`SystemModel` that hold elements, in the order
#: :meth:`SystemModel.elements` walks them. Read off the model's own fields —
#: every list-typed field whose items are an element — so a sixth group joins
#: here, the walk and the repair overlay together, or not at all.
ELEMENT_GROUPS: tuple[str, ...] = tuple(
    name
    for name, info in SystemModel.model_fields.items()
    if get_origin(info.annotation) is list
    and isinstance(get_args(info.annotation)[0], type)
    and issubclass(get_args(info.annotation)[0], _Element)
)


def duplicate_ids(elements: Iterable[Element]) -> dict[str, int]:
    """Each element ID more than one element carries, with how many carry it.

    The one reader of "is this ID unique": the validity gate reports each
    entry as ``duplicate-id``, and :func:`normalize_element_ids` refuses to
    rewrite a reference through one, because an ID two elements carry names
    neither of them.
    """
    counts = Counter(element.id for element in elements)
    return {
        element_id: count for element_id, count in sorted(counts.items()) if count > 1
    }


def _rewrite_id(
    element: Element, rewrites: dict[str, str], ambiguous: Collection[str]
) -> None:
    """Overwrite one element's ID with its derived form, recording the change.

    A name that normalizes to an empty slug has no derived form; the emitted ID
    is left alone so the validity gate reports it rather than this pass
    guessing at it.

    An emitted ID in ``ambiguous`` — one more than one element arrived with —
    still gives way to the derived ID, because the name is authoritative, but
    the change is **not recorded**: a rewrite table entry for it would bind
    every reference to whichever element was rewritten last, and a reference
    to an ID two elements carried is a reference the source left ambiguous.
    """
    try:
        derived = derive_element_id(element)
    except ValueError:
        return
    if derived != element.id:
        if element.id not in ambiguous:
            rewrites[element.id] = derived
        element.id = derived


def normalize_element_ids(
    model: SystemModel, source_labels: Collection[str] = ()
) -> SystemModel:
    """Return a copy whose IDs are derived from names, references rewritten.

    An element ID is a pure function of type and name, so a model that emits
    both is being asked to keep two fields in agreement by hand — a mechanical
    constraint that belongs in code. This is derivation, not repair: it decides
    nothing the emitting model knew and the gate does not know, and it reads no
    source text.

    Names are authoritative and IDs follow. An emitted ID is therefore only a
    *link*: every reference to it — ``trust_zone``, flow endpoints, and
    ``assumptions[].element_id`` — is rewritten to the derived ID, so a
    self-consistent model stays self-consistent. Dangling references are left
    untouched for the gate to report.

    Normalization can make two elements collide on one derived ID. That is a
    real defect surfacing, not one introduced: two elements sharing a name are
    the class/instance duplication the gate's ``duplicate-id`` rule exists to
    catch.

    The reverse collision — two elements that *arrived* with one ID and
    different names — is never resolved here. Each element takes its own
    derived ID, and every reference to the shared emitted ID is left as it
    was, so the gate reports it dangling beside the ``duplicate-id`` that
    :func:`~analysis_service.validation.parse_and_validate` raises for the
    emitted ID. Rewriting through that ID would bind ``A calls B`` to
    ``B calls B`` and report nothing, which is the silent wrong binding this
    pass has to be incapable of (#961).

    ``source_labels`` extends the same idea to the one reference an element
    carries that points *outside* the model: a ``source_label`` naming one of
    the job's sources. It is snapped to the job's own spelling
    (:func:`~analysis_service.references.canonical`) for the reason IDs are
    derived — which spelling of a name arrived is mechanical — and by the same
    rule, a label naming no source is left untouched for the gate to report.
    The caller's label is never rewritten; the *model's echo of it* is, so the
    report cites the bytes the caller actually submitted.
    """
    normalized = model.model_copy(deep=True)
    zoned = normalized.zoned_elements()
    rewrites: dict[str, str] = {}
    # Read before any ID moves, over every element: a flow can be referenced
    # too, by an assumption, so its emitted ID is held to the same rule.
    ambiguous = duplicate_ids(normalized.elements())

    non_flows: list[Element] = [*zoned, *normalized.trust_boundaries]
    for element in non_flows:
        _rewrite_id(element, rewrites, ambiguous)

    for element in zoned:
        element.trust_zone = rewrites.get(element.trust_zone, element.trust_zone)

    # Flows last: a flow's derived ID is built from its endpoints' IDs, so the
    # endpoints have to carry their derived values before it is computed.
    for flow in normalized.data_flows:
        flow.source = rewrites.get(flow.source, flow.source)
        flow.destination = rewrites.get(flow.destination, flow.destination)
        _rewrite_id(flow, rewrites, ambiguous)

    for assumption in normalized.assumptions:
        assumption.element_id = rewrites.get(
            assumption.element_id, assumption.element_id
        )

    if source_labels:
        for element in normalized.elements():
            element.source_label = (
                canonical(element.source_label, source_labels) or element.source_label
            )

    return normalized
