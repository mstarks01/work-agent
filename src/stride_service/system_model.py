"""Canonical System Model: the extraction agent's output, every downstream agent's input.

Terminology and the decisions behind this schema live in CONTEXT.md and
wayfinder ticket 003 (domain model: canonical system representation).

The five element types are the classic DFD-based STRIDE-per-element taxonomy.
Free-form security-relevant attributes accept the sentinel value ``"unknown"``,
meaning the input neither stated nor allowed inference of the fact; analysts
treat an unknown control as unverified, never as present or absent. Inferred
values are recorded in the attribute *and* in the top-level ``assumptions``
list — never as silent guesses.

Boundary crossings are derived, never extracted: a Data Flow crosses a trust
boundary iff its endpoints' zones differ.
"""

from __future__ import annotations

import re
from typing import ClassVar, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

UNKNOWN = "unknown"

# Controlled asset vocabulary; config-extendable via the validator's
# extra_asset_tags parameter (see stride_service.validation).
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

_SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")


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

    id: str = Field(max_length=300)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    assets: list[str] = Field(default_factory=list, max_length=len(CORE_ASSET_TAGS) * 4)
    source_excerpt: str = Field(default="", max_length=1000)
    notes: str = Field(default="", max_length=2000)


class ExternalEntity(_Element):
    """An actor outside the system's control: users, third-party systems."""

    id_prefix: ClassVar[str] = "entity"

    kind: Literal["human", "external-system"]
    trust_zone: str


class Process(_Element):
    """Running code or a component that transforms data."""

    id_prefix: ClassVar[str] = "process"

    technology: str = Field(max_length=200)
    trust_zone: str
    exposure: Literal["internet-facing", "internal", "unknown"]


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
    """A named flat trust zone: network zones, auth boundaries, privilege levels."""

    id_prefix: ClassVar[str] = "boundary"

    kind: Literal["network", "privilege", "tenant", "other"]


Element = Union[ExternalEntity, Process, DataStore, DataFlow, TrustBoundary]

# Elements that belong to a trust zone (everything except flows and boundaries).
ZonedElement = Union[ExternalEntity, Process, DataStore]


class Assumption(BaseModel):
    """A value extraction inferred on the record, with a stated basis."""

    model_config = ConfigDict(extra="forbid")

    assumption: str = Field(min_length=1, max_length=1000)
    element_id: str = Field(max_length=300)
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
            element.id: element.trust_zone
            for element in (*self.external_entities, *self.processes, *self.data_stores)
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


def is_slug(value: str) -> bool:
    """True if value is a legal normalized slug."""
    return _SLUG_RE.fullmatch(value) is not None
