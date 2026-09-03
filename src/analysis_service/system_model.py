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
from collections.abc import Collection
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from analysis_service.references import canonical

UNKNOWN = "unknown"

# Controlled asset vocabulary; config-extendable via the validator's
# extra_asset_tags parameter (see analysis_service.validation).
CORE_ASSET_TAGS = frozenset(
    {
        "credentials",
        "pii",
        "financial",
        "health",
        "secrets",
        "business-critical-data",
        "availability-critical",
        "reputation",
    }
)

_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")


#: The shape every element ID this service builds already has:
#: ``<prefix>:<slug>``, or a flow's ``flow:<slug>-to-<slug>:<slug>``, where a
#: slug is what :func:`normalize_name` produces.
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
ELEMENT_ID = r"^[a-z_]+:[a-z0-9]+(?:-[a-z0-9]+)*(?::[a-z0-9]+(?:-[a-z0-9]+)*)?$"


def normalize_name(name: str) -> str:
    """Normalize a human-readable name into the slug used inside element IDs."""
    slug = _NON_SLUG_CHARS_RE.sub("-", name.lower()).strip("-")
    if not slug:
        raise ValueError(f"name {name!r} normalizes to an empty slug")
    return slug


def make_element_id(prefix: str, name: str) -> str:
    """Build the deterministic typed-slug ID for a non-flow element."""
    return f"{prefix}:{normalize_name(name)}"


def make_flow_id(source_id: str, destination_id: str, label: str) -> str:
    """Build the deterministic ID for a Data Flow: flow:<source>-to-<dest>:<label>.

    ``source_id`` and ``destination_id`` are the endpoints' element IDs; their
    type prefixes are stripped so only the name slugs appear in the flow ID.
    """
    source_slug = source_id.split(":", 1)[-1]
    destination_slug = destination_id.split(":", 1)[-1]
    return f"flow:{source_slug}-to-{destination_slug}:{normalize_name(label)}"


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
    attribute: str = Field(min_length=1, max_length=100)
    basis: str = Field(min_length=1, max_length=1000)


class BoundaryCrossing(BaseModel):
    """Derived fact: a Data Flow whose endpoints sit in different trust zones."""

    model_config = ConfigDict(extra="forbid")

    flow_id: str
    source_zone: str
    destination_zone: str


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
        """All elements in a stable order."""
        return [
            *self.external_entities,
            *self.processes,
            *self.data_stores,
            *self.data_flows,
            *self.trust_boundaries,
        ]

    def zoned_elements(self) -> list[ZonedElement]:
        """The elements that carry a ``trust_zone``, in a stable order."""
        return [*self.external_entities, *self.processes, *self.data_stores]

    def get(self, element_id: str) -> Element | None:
        """Look up an element by ID, or None if absent."""
        return {element.id: element for element in self.elements()}.get(element_id)

    def boundary_crossings(self) -> list[BoundaryCrossing]:
        """Derive boundary crossings mechanically. Requires a valid model.

        Raises ValueError on a dangling flow endpoint or an endpoint without a
        trust zone — derivation on an invalid model would produce misleading
        STRIDE input, so it fails closed instead of skipping.
        """
        zone_by_id = {
            element.id: element.trust_zone for element in self.zoned_elements()
        }
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
            if source_zone != destination_zone:
                crossings.append(
                    BoundaryCrossing(
                        flow_id=flow.id,
                        source_zone=source_zone,
                        destination_zone=destination_zone,
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
        :class:`~analysis_service.report.SharedElementName` for where it lands
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


def _rewrite_id(element: Element, rewrites: dict[str, str]) -> None:
    """Overwrite one element's ID with its derived form, recording the change.

    A name that normalizes to an empty slug has no derived form; the emitted ID
    is left alone so the validity gate reports it rather than this pass
    guessing at it.
    """
    try:
        derived = derive_element_id(element)
    except ValueError:
        return
    if derived != element.id:
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

    non_flows: list[Element] = [*zoned, *normalized.trust_boundaries]
    for element in non_flows:
        _rewrite_id(element, rewrites)

    for element in zoned:
        element.trust_zone = rewrites.get(element.trust_zone, element.trust_zone)

    # Flows last: a flow's derived ID is built from its endpoints' IDs, so the
    # endpoints have to carry their derived values before it is computed.
    for flow in normalized.data_flows:
        flow.source = rewrites.get(flow.source, flow.source)
        flow.destination = rewrites.get(flow.destination, flow.destination)
        _rewrite_id(flow, rewrites)

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
