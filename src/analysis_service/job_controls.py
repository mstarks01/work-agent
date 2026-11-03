"""A job's copy of the Controls it applies, and the match of each scope to
the job's model (ADRs 0124 and 0127).

A submission names Controls by ID, and the job copies each one into the
object ``<owner-id>/<job-id>/controls.json``: the document, the text of
each evidence file and the version check of each row. The store writes the copy once, so a later
edit or delete of a Control changes no job.

The model exists only after the extraction. So the pause offers each copied
Control with its :class:`ScopeMatch`, and a pause save states one
:data:`ControlResolution` for each copy, which the job row keeps in
``control_scopes``. A job with no stated resolution takes the default that
the match gives. :func:`resolutions` is the one reader of both.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field

from analysis_service.controls import (
    CONTROL_ID,
    EVIDENCE_ID,
    Control,
    ControlDocument,
    ControlRow,
    ControlScope,
)
from analysis_service.frameworks import PACKAGES, FrameworkName
from analysis_service.parsing import Sha256Hex
from analysis_service.sources import EvidenceClass
from analysis_service.system_model import (
    ELEMENT_ID,
    ELEMENT_ID_MAX_CHARS,
    UNKNOWN,
    SystemModel,
    element_prefix,
    is_zone,
)

#: How a scope that applies reached its element. ``application`` needs no
#: element; ``owner-mapped`` is an element the owner chose at the pause.
MatchOutcome = Literal[
    "application", "matched-by-name", "matched-by-alias", "owner-mapped"
]

#: Why a scope found no element: no element of its type carries the name or
#: an alias, or its zone matched and no element holds the zone.
NoMatch = Literal["missing-element", "empty-zone"]

#: Why a copied Control does not apply to the job. ``unticked`` is a scope
#: that matched with no element ticked: the owner left it out, or a zone
#: holds only candidates that the default does not tick.
LeftOutReason = Literal["missing-element", "empty-zone", "unticked"]

#: Why an element is a candidate of a zone scope: its zone is stated, its
#: zone is an inference, or no source placed it.
CoverReason = Literal["stated-zone", "zone-inferred", "no-zone-stated"]

#: The refusal of a Control that the owner or the job does not hold. One
#: sentence for a deleted Control and for another owner's ID, which names no ID.
CONTROL_GONE = (
    "A Control that this analysis names is no longer in Controls. Remove it"
    " from the analysis to continue."
)

#: The refusal of a pause save that names a Control the job did not copy. It
#: names no ID either.
NOT_COPIED = "The save names a Control that this analysis did not copy."

_ElementId = Annotated[str, Field(max_length=ELEMENT_ID_MAX_CHARS, pattern=ELEMENT_ID)]


class CopiedEvidence(BaseModel):
    """One evidence file as the job copied it, with its text."""

    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)

    id: str = Field(pattern=EVIDENCE_ID)
    name: str
    evidence_class: EvidenceClass = Field(alias="class")
    sha256: Sha256Hex
    text: str


class CopiedRow(BaseModel):
    """One row of a copied Control, with whether its package version is the
    version this build carries. A row of another version is offered
    unticked, so a renumbered unit never passes with a new meaning."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    framework: str
    unit: str
    version: str
    ticked: bool


def _current(row: ControlRow) -> bool:
    if row.framework not in PACKAGES:
        return False
    return PACKAGES[cast(FrameworkName, row.framework)].version == row.version


class CopiedControl(BaseModel):
    """One Control as the job copied it at the submission."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document: ControlDocument
    evidence: list[CopiedEvidence]
    rows: list[CopiedRow]

    @property
    def id(self) -> str:
        return self.document.id

    @classmethod
    def of(cls, control: Control, texts: Mapping[str, str]) -> Self:
        """The copy of ``control``, whose evidence text ``texts`` holds by ID."""
        document = control.document
        return cls(
            document=document,
            evidence=[
                CopiedEvidence.model_validate(
                    file.model_dump(exclude={"size_bytes"}) | {"text": texts[file.id]}
                )
                for file in document.evidence
            ],
            rows=[
                CopiedRow(
                    framework=row.framework,
                    unit=row.unit,
                    version=row.version,
                    ticked=_current(row),
                )
                for row in document.rows
            ],
        )


class CoveredElement(BaseModel):
    """One candidate of a zone scope: the element, why it is a candidate,
    and whether the Control covers it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    element: _ElementId
    reason: CoverReason
    ticked: bool


class AppliedScope(BaseModel):
    """A copied Control that applies, and what it resolved to. ``element`` is
    ``""`` for the whole application, and ``covers`` lists a zone's
    candidates."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal["applied"] = "applied"
    control: str = Field(pattern=CONTROL_ID)
    outcome: MatchOutcome
    element: Annotated[str, Field(max_length=ELEMENT_ID_MAX_CHARS)] = ""
    covers: list[CoveredElement] = Field(default_factory=list)

    def element_ids(self) -> tuple[str, ...]:
        """The elements the Control covers: the element of an element scope,
        each ticked element of a zone, and none for the whole application."""
        if is_zone(self.element):
            return tuple(cover.element for cover in self.covers if cover.ticked)
        return (self.element,) if self.element else ()


class LeftOutScope(BaseModel):
    """A copied Control that does not apply, and why."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal["left-out"] = "left-out"
    control: str = Field(pattern=CONTROL_ID)
    reason: LeftOutReason


ControlResolution = Annotated[AppliedScope | LeftOutScope, Field(discriminator="state")]


class ControlScopeEntry(BaseModel):
    """One applied Control in a pause save's ``control_scopes``: the element
    it resolves to, ``""`` for the whole application, and for a zone the
    elements the owner ticked."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    control: str = Field(pattern=CONTROL_ID)
    element: Annotated[str, Field(max_length=ELEMENT_ID_MAX_CHARS)] = ""
    ticked: list[_ElementId] = Field(default_factory=list)


@dataclass(frozen=True)
class ScopeMatch:
    """What a scope matches in a model. ``element`` is ``""`` where nothing
    matched or the scope is the whole application. ``covers`` lists a zone's
    candidates with the default ticks, and ``choices`` the elements of the
    scope's type that the owner can map a ``missing-element`` scope to."""

    outcome: MatchOutcome | NoMatch
    element: str = ""
    covers: tuple[CoveredElement, ...] = ()
    choices: tuple[str, ...] = ()


def zone_covers(model: SystemModel, boundary_id: str) -> tuple[CoveredElement, ...]:
    """The candidates of a zone scope matched to ``boundary_id``: each
    element whose stated zone it is, ticked, each element whose inferred zone
    it is, and each element no source placed, unticked."""
    inferred = model.assumed_zone_elements()
    covers = []
    for element in model.zoned_elements():
        reason: CoverReason
        if element.trust_zone == boundary_id:
            reason = "zone-inferred" if element.id in inferred else "stated-zone"
        elif element.trust_zone == UNKNOWN:
            reason = "no-zone-stated"
        else:
            continue
        covers.append(
            CoveredElement(
                element=element.id, reason=reason, ticked=reason == "stated-zone"
            )
        )
    return tuple(covers)


def _holds_nothing(covers: Sequence[CoveredElement]) -> bool:
    """Whether a zone with the candidates ``covers`` is an empty zone: no
    element has the zone, stated or inferred."""
    return all(cover.reason == "no-zone-stated" for cover in covers)


def _reached_by(scope: ControlScope, element: str) -> MatchOutcome:
    """How ``scope`` reached ``element``: its name, an alias, or the owner's
    choice."""
    if element == scope.name_id():
        return "matched-by-name"
    if element in scope.alias_ids():
        return "matched-by-alias"
    return "owner-mapped"


def match_scope(scope: ControlScope, model: SystemModel) -> ScopeMatch:
    """Match ``scope`` to ``model`` by element ID: the name, then each alias.

    **The one rule of a scope match** (ADR 0124). An ID is equal or it is
    not, so a similar name proposes nothing.
    """
    if scope.type == "application":
        return ScopeMatch("application")
    held = [element.id for element in model.elements()]
    element = next(
        (id_ for id_ in (scope.name_id(), *scope.alias_ids()) if id_ in held), ""
    )
    if not element:
        choices = tuple(id_ for id_ in held if element_prefix(id_) == scope.type)
        return ScopeMatch("missing-element", choices=choices)
    outcome = _reached_by(scope, element)
    if scope.type != "boundary":
        return ScopeMatch(outcome, element)
    covers = zone_covers(model, element)
    if _holds_nothing(covers):
        return ScopeMatch("empty-zone", element, covers)
    return ScopeMatch(outcome, element, covers)


def _applied(
    control: str, outcome: MatchOutcome, element: str, covers: Sequence[CoveredElement]
) -> ControlResolution:
    """The resolution of a scope that reached ``element``. A zone applies
    only with a ticked element: an empty zone, or a zone with no tick, is
    left out."""
    if is_zone(element) and not any(cover.ticked for cover in covers):
        return LeftOutScope(
            control=control,
            reason="empty-zone" if _holds_nothing(covers) else "unticked",
        )
    return AppliedScope(
        control=control, outcome=outcome, element=element, covers=list(covers)
    )


def default_resolution(copy: CopiedControl, model: SystemModel) -> ControlResolution:
    """What ``copy`` resolves to where no save stated it: the match, with
    its default ticks, and a scope with no match left out with its reason."""
    found = match_scope(copy.document.scope, model)
    match found.outcome:
        case "missing-element" | "empty-zone" as reason:
            return LeftOutScope(control=copy.id, reason=reason)
        case outcome:
            return _applied(copy.id, outcome, found.element, found.covers)


def _stated(
    copy: CopiedControl, entry: ControlScopeEntry, model: SystemModel
) -> ControlResolution:
    """The resolution an entry of a save states for ``copy``, or a
    :class:`ValueError` that names the fault."""
    name = copy.document.name
    scope = copy.document.scope
    if scope.type == "application":
        if entry.element or entry.ticked:
            raise ValueError(
                f"“{name}” protects the whole application, so it names no element."
            )
        return AppliedScope(control=copy.id, outcome="application")
    if element_prefix(entry.element) != scope.type:
        raise ValueError(
            f"“{name}” protects a {scope.type}, and {entry.element} is not one."
        )
    if model.get(entry.element) is None:
        raise ValueError(f"{entry.element} is not an element of this model.")
    outcome = _reached_by(scope, entry.element)
    if scope.type != "boundary":
        if entry.ticked:
            raise ValueError(f"“{name}” protects one element, so it ticks none.")
        return AppliedScope(control=copy.id, outcome=outcome, element=entry.element)
    candidates = zone_covers(model, entry.element)
    outside = set(entry.ticked) - {cover.element for cover in candidates}
    if outside:
        raise ValueError(
            f"“{name}” ticks an element outside the list of its zone: {min(outside)}."
        )
    ticked = set(entry.ticked)
    covers = [
        cover.model_copy(update={"ticked": cover.element in ticked})
        for cover in candidates
    ]
    return _applied(copy.id, outcome, entry.element, covers)


def resolve_scopes(
    entries: Sequence[ControlScopeEntry],
    copies: Sequence[CopiedControl],
    model: SystemModel,
) -> list[ControlResolution]:
    """The resolution of each copy that a pause save's ``entries`` state, in
    the copies' order, or a :class:`ValueError` that names the first fault.

    An entry names a Control the job holds, once. A copy with no entry is
    left out: with the reason its default gives where the default leaves it
    out, else ``unticked``. Many Controls may resolve to one element.
    """
    held = {copy.id for copy in copies}
    named: dict[str, ControlScopeEntry] = {}
    for entry in entries:
        if entry.control not in held:
            raise ValueError(NOT_COPIED)
        if entry.control in named:
            raise ValueError("The save names one Control twice.")
        named[entry.control] = entry
    stated: list[ControlResolution] = []
    for copy in copies:
        if copy.id in named:
            stated.append(_stated(copy, named[copy.id], model))
            continue
        default = default_resolution(copy, model)
        if isinstance(default, LeftOutScope):
            stated.append(default)
        else:
            stated.append(LeftOutScope(control=copy.id, reason="unticked"))
    return stated


def resolutions(
    copies: Sequence[CopiedControl],
    scopes: Sequence[ControlResolution] | None,
    model: SystemModel,
) -> list[ControlResolution]:
    """The resolution of each copy, in the copies' order.

    **The one reader of what a job's Controls resolve to.** ``scopes`` is
    what the job row holds: the resolution a pause save stated for each copy,
    or ``None`` where no save stated one, and each copy takes its default.
    """
    if scopes is None:
        return [default_resolution(copy, model) for copy in copies]
    stated = {scope.control: scope for scope in scopes}
    return [stated[copy.id] for copy in copies]


def control_offer(
    copies: Sequence[CopiedControl],
    scopes: Sequence[ControlResolution] | None,
    model: SystemModel,
) -> list[dict[str, object]]:
    """What the pause shows of each copied Control: its scope as authored,
    its match against ``model`` with the choices and the candidates, its
    resolution, and its rows with their version check."""
    names = {element.id: element.name for element in model.elements()}
    offer: list[dict[str, object]] = []
    for copy, resolution in zip(
        copies, resolutions(copies, scopes, model), strict=True
    ):
        found = match_scope(copy.document.scope, model)
        offer.append(
            {
                "id": copy.id,
                "name": copy.document.name,
                "scope": copy.document.scope.model_dump(mode="json"),
                "match": found.outcome,
                "element": (
                    {"id": found.element, "name": names[found.element]}
                    if found.element
                    else None
                ),
                "choices": [
                    {"id": choice, "name": names[choice]} for choice in found.choices
                ],
                "covers": [
                    cover.model_dump(mode="json") | {"name": names[cover.element]}
                    for cover in found.covers
                ],
                "resolution": resolution.model_dump(mode="json"),
                "rows": [row.model_dump(mode="json") for row in copy.rows],
            }
        )
    return offer


#: What the reopen payload says of a copied Control: its resolution, or
#: ``pending`` where the job has no model yet to resolve it against.
UsedState = Literal["applied", "left-out", "pending"]


def controls_used(
    copies: Sequence[CopiedControl],
    scopes: Sequence[ControlResolution] | None,
    model: SystemModel | None,
) -> list[dict[str, str]]:
    """Each copied Control of a job, read-only, with its :data:`UsedState`.
    ``model`` is the job's model, or ``None`` where the job has none yet."""
    states: list[UsedState] = (
        ["pending"] * len(copies)
        if model is None
        else [resolution.state for resolution in resolutions(copies, scopes, model)]
    )
    return [
        {"id": copy.id, "name": copy.document.name, "state": state}
        for copy, state in zip(copies, states, strict=True)
    ]
