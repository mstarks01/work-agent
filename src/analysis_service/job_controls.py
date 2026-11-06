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

Each applied Control then writes its facts onto the elements it resolves to,
where the fact is open (ADR 0125). :func:`control_facts` is the one reader of
what each Control writes, and the job row keeps the result with each fact's
state in ``control_facts``.

The run reads each applied Control as an :class:`AppliedControl`. The
fan-in keeps each unit a row covers, and the critic judges each row it is
shown on a draft (ADR 0126). :func:`shown_controls` is the one reader of
what a draft is shown, and :func:`row_outcome` the one reader of what
became of a row, from the rulings the report's verdicts hold.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Annotated, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field

from analysis_service.answer_sets import AnswerSet
from analysis_service.assertions import AssertionCatalog
from analysis_service.claims import (
    Claim,
    ControlRowRef,
    FrameworkAnalysis,
    ReturnedClaim,
    RuledClaim,
)
from analysis_service.controls import (
    CONTROL_ID,
    EVIDENCE_ID,
    SCOPE_WORDS,
    Control,
    ControlDocument,
    ControlRow,
    ControlScope,
    scope_phrase,
)
from analysis_service.fact_answers import (
    FactAnswer,
    fact_label,
    key_ref,
    merged_facts,
    with_element,
)
from analysis_service.fact_writes import check_fact_answers
from analysis_service.frameworks import PACKAGES, FrameworkName, FrameworkPackage
from analysis_service.open_facts import open_attribute, prepared_model
from analysis_service.owner_files import text_lines
from analysis_service.parsing import Sha256Hex
from analysis_service.sources import EvidenceClass
from analysis_service.system_model import (
    ELEMENT_ID,
    ELEMENT_ID_MAX_CHARS,
    UNKNOWN,
    SystemModel,
    attribute_names,
    element_prefix,
    is_zone,
)
from analysis_service.validation import MAX_ELEMENTS

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

#: How a person reads each match outcome, on the pause and in the report.
MATCH_MEANINGS: Mapping[MatchOutcome, str] = MappingProxyType(
    {
        "application": "it applies with no match",
        "matched-by-name": "a part of this description has its name",
        "matched-by-alias": "a part of this description has one of its aliases",
        "owner-mapped": "you chose the part at the pause",
    }
)

#: How a person reads each reason a Control is left out, on the pause and in
#: the report.
LEFT_OUT_MEANINGS: Mapping[LeftOutReason, str] = MappingProxyType(
    {
        "missing-element": "no part of this description has the name or an alias",
        "empty-zone": "the zone holds no part of this description",
        "unticked": "it is not ticked",
    }
)

#: Why an element is a candidate of a zone scope: its zone is stated, its
#: zone is an inference, or no source placed it.
CoverReason = Literal["stated-zone", "zone-inferred", "no-zone-stated"]

#: The refusal of a submission that names a Control the owner does not
#: hold. One sentence for a deleted Control and for another owner's ID,
#: which names no ID.
CONTROL_GONE = (
    "A Control that this analysis names is no longer in Controls. Remove it"
    " from the analysis to continue."
)

#: The refusal of a pause save that names a Control the job did not copy. It
#: names no ID either.
NOT_COPIED = "The save names a Control that this analysis did not copy."

_ElementId = Annotated[str, Field(max_length=ELEMENT_ID_MAX_CHARS, pattern=ELEMENT_ID)]


def changes_nothing(copy: CopiedControl, frameworks: Iterable[str]) -> bool:
    """Whether ``copy`` would change nothing in an analysis of ``frameworks``:
    it states no fact, and no ticked row of it names one of them. A row of
    another package version is not ticked. The form page states the same
    rule in ``form_controls.js`` over ``current_rows``, and
    ``test_the_form_and_the_service_agree_on_a_control_that_changes_nothing``
    holds the two against each other."""
    names = set(frameworks)
    return not copy.document.facts and not any(
        row.ticked and row.framework in names for row in copy.rows
    )


def not_for_this_analysis(name: str) -> str:
    """The refusal of a submission that names a Control that changes nothing."""
    return (
        f"The Control “{name}” states no fact and covers no framework of this"
        " analysis at the version this service carries, so it changes nothing"
        " here. Untick it, choose a framework it covers, or edit its rows in"
        " Controls."
    )


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


def current_row(row: ControlRow) -> bool:
    """Whether ``row`` names the package version this build carries. A copy
    ticks a row by it, and the form page reads it through ``control_view``."""
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
                    ticked=current_row(row),
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


#: Whether a copied Control applies to the job, or the job leaves it out.
ResolutionState = Literal["applied", "left-out"]


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

    @property
    def meaning(self) -> str:
        """How a person reads the outcome."""
        return MATCH_MEANINGS[self.outcome]


class LeftOutScope(BaseModel):
    """A copied Control that does not apply, and why."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal["left-out"] = "left-out"
    control: str = Field(pattern=CONTROL_ID)
    reason: LeftOutReason

    @property
    def meaning(self) -> str:
        """How a person reads the reason."""
        return LEFT_OUT_MEANINGS[self.reason]


ControlResolution = Annotated[AppliedScope | LeftOutScope, Field(discriminator="state")]


class ControlScopeEntry(BaseModel):
    """One applied Control in a pause save's ``control_scopes``: the element
    it resolves to, ``""`` for the whole application, and for a zone the
    elements the owner ticked."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    control: str = Field(pattern=CONTROL_ID)
    element: Annotated[str, Field(max_length=ELEMENT_ID_MAX_CHARS)] = ""
    ticked: list[_ElementId] = Field(default_factory=list, max_length=MAX_ELEMENTS)


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
    if not is_zone(element):
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
                f"“{name}” protects {scope_phrase(scope)}, so it names no element."
            )
        return AppliedScope(control=copy.id, outcome="application")
    if element_prefix(entry.element) != scope.type:
        words = SCOPE_WORDS[scope.type]
        raise ValueError(
            f"“{name}” protects {words.article.lower()} {words.noun}, and"
            f" {entry.element} is not one."
        )
    if model.get(entry.element) is None:
        raise ValueError(f"{entry.element} is not an element of this model.")
    outcome = _reached_by(scope, entry.element)
    if not is_zone(entry.element):
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
    for copy, default in zip(copies, resolutions(copies, None, model), strict=True):
        if copy.id in named:
            stated.append(_stated(copy, named[copy.id], model))
        elif isinstance(default, LeftOutScope):
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
    resolution, and its rows with their version check. A choice of a zone
    lists the candidates of that zone, so the owner can tick them."""
    names = {element.id: element.name for element in model.elements()}

    def covers(found: Sequence[CoveredElement]) -> list[dict[str, object]]:
        return [
            cover.model_dump(mode="json") | {"name": names[cover.element]}
            for cover in found
        ]

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
                "scope_phrase": scope_phrase(copy.document.scope),
                "match": found.outcome,
                "element": (
                    {"id": found.element, "name": names[found.element]}
                    if found.element
                    else None
                ),
                "choices": [
                    {
                        "id": choice,
                        "name": names[choice],
                        "covers": (
                            covers(zone_covers(model, choice))
                            if is_zone(choice)
                            else []
                        ),
                    }
                    for choice in found.choices
                ],
                "covers": covers(found.covers),
                "resolution": resolution.model_dump(mode="json"),
                "meaning": resolution.meaning,
                "rows": [row.model_dump(mode="json") for row in copy.rows],
            }
        )
    return offer


class AppliedControl(BaseModel):
    """One applied Control as the run reads it: its resolved scope, each
    ticked row and the text of each evidence file (ADR 0126)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    scope: AppliedScope
    rows: list[CopiedRow]
    evidence: list[CopiedEvidence]

    @property
    def id(self) -> str:
        return self.scope.control

    def reaches(self, elements: Sequence[str]) -> bool:
        """Whether the scope is the application or holds one of ``elements``."""
        return self.scope.outcome == "application" or bool(
            set(elements) & set(self.scope.element_ids())
        )


def applied_controls(
    copies: Sequence[CopiedControl],
    scopes: Sequence[ControlResolution] | None,
    model: SystemModel,
) -> list[AppliedControl]:
    """Each copy that applies to ``model``, with its ticked rows, in the
    copies' order."""
    return [
        AppliedControl(
            name=copy.document.name,
            scope=resolution,
            rows=[row for row in copy.rows if row.ticked],
            evidence=copy.evidence,
        )
        for copy, resolution in zip(
            copies, resolutions(copies, scopes, model), strict=True
        )
        if isinstance(resolution, AppliedScope)
    ]


def shown_controls(
    draft: Claim, package: FrameworkPackage, controls: Sequence[AppliedControl]
) -> list[AppliedControl]:
    """The Controls the critic is shown on ``draft``, each with only the rows
    on the draft's unit or on a unit that holds it.

    **The one reader of what a draft is shown** (ADR 0126): the critic view
    renders it, and the review seam checks the rulings against it.
    """
    shown = []
    for control in controls:
        rows = _rows_on(draft, package, control)
        if rows and control.reaches(draft.affected_element_ids):
            shown.append(control.model_copy(update={"rows": rows}))
    return shown


def _rows_on(
    draft: Claim, package: FrameworkPackage, control: AppliedControl
) -> list[CopiedRow]:
    """Each row of ``control`` on the draft's unit or on a unit that holds it."""
    holding = package.units_holding(package.control_unit_of(draft))
    return [
        row
        for row in control.rows
        if row.framework == package.name and row.unit in holding
    ]


def unreached_rows(
    claims: Iterable[Claim], package: FrameworkPackage, control: AppliedControl
) -> frozenset[str]:
    """The unit of each row of ``control`` that a claim sits on, where the
    scope does not reach the claim's elements, so the critic did not see the
    row on it."""
    return frozenset(
        row.unit
        for claim in claims
        if not shown_controls(claim, package, [control])
        for row in _rows_on(claim, package, control)
    )


def covered_units(
    package: FrameworkPackage, controls: Sequence[AppliedControl]
) -> frozenset[str]:
    """Each unit of ``package`` that a row of ``controls`` covers: the row's
    unit and each unit inside it."""
    rows = {
        row.unit
        for control in controls
        for row in control.rows
        if row.framework == package.name
    }
    return frozenset(
        unit.id for unit in package.units if package.units_holding(unit.id) & rows
    )


#: What the critic said of one row on one draft: the row covers the draft, or
#: it does not.
RowVerdict = Literal["applied", "refused"]

#: What became of one row over a job: it covered a draft, the critic refused
#: it on every draft it was shown, a finding on its unit lay outside the
#: scope, or no draft was shown it.
RowOutcome = Literal["applied", "refused", "not-reached", "no-finding"]


class RowRuling(BaseModel):
    """One ruling on one row: the claim, the critic's verdict and its reason."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim: str
    ruling: RowVerdict
    reason: str


def row_rulings(
    claims: Iterable[RuledClaim], returned: Iterable[ReturnedClaim]
) -> dict[ControlRowRef, list[RowRuling]]:
    """Each row a verdict of ``claims`` or an entry of ``returned`` names,
    against its rulings in that order. The report holds each ruling once, so
    the job keeps no second record of it."""
    rulings: dict[ControlRowRef, list[RowRuling]] = {}
    for claim in claims:
        verdict = claim.verdict
        if verdict.control_row is not None:
            rulings.setdefault(verdict.control_row, []).append(
                RowRuling(claim=claim.id, ruling="applied", reason=verdict.reason)
            )
        for refused in verdict.refused_controls:
            row = ControlRowRef(control=refused.control, unit=refused.unit)
            rulings.setdefault(row, []).append(
                RowRuling(claim=claim.id, ruling="refused", reason=refused.reason)
            )
    for entry in returned:
        for refused in entry.refused_controls:
            row = ControlRowRef(control=refused.control, unit=refused.unit)
            rulings.setdefault(row, []).append(
                RowRuling(claim=entry.claim_id, ruling="refused", reason=refused.reason)
            )
    return rulings


def row_outcome(rulings: Sequence[RowRuling], *, unreached: bool) -> RowOutcome:
    """**The one reader of a row's outcome** (ADR 0126): ``applied`` where a
    ruling applied it, ``refused`` where every ruling refused it. A row with
    no ruling is ``not-reached`` where ``unreached`` says a finding on its
    unit lay outside the scope, and ``no-finding`` where none did."""
    if not rulings:
        return "not-reached" if unreached else "no-finding"
    if any(ruling.ruling == "applied" for ruling in rulings):
        return "applied"
    return "refused"


#: What the reopen payload says of a copied Control: its resolution, or
#: ``pending`` where the job serves no model to resolve it against: the job
#: is not complete yet, it ended with no report, or the certification gate
#: withholds its report.
UsedState = Literal[ResolutionState, "pending"]


def controls_used(
    copies: Sequence[CopiedControl],
    scopes: Sequence[ControlResolution] | None,
    model: SystemModel | None,
) -> list[dict[str, str]]:
    """Each copied Control of a job, read-only, with its :data:`UsedState`.
    ``model`` is the job's model, or ``None`` where the read gets none."""
    states: list[UsedState] = (
        ["pending"] * len(copies)
        if model is None
        else [resolution.state for resolution in resolutions(copies, scopes, model)]
    )
    return [
        {"id": copy.id, "name": copy.document.name, "state": state}
        for copy, state in zip(copies, states, strict=True)
    ]


#: What became of one fact a Control writes: ``written`` onto an open fact,
#: ``changed`` where an own answer lies over it, or ``stated`` where the
#: model states the fact and the Control writes nothing.
FactState = Literal["written", "changed", "stated"]

#: How a person reads each fact state, on the report page and in the
#: Markdown export.
FACT_MEANINGS: Mapping[FactState, str] = MappingProxyType(
    {
        "written": "the Control wrote it onto an open fact",
        "changed": "your own answer lies over it",
        "stated": "the description states the fact, so the Control wrote nothing",
    }
)


class ControlFact(BaseModel):
    """One fact a Control writes, with its element, and its state.
    ``stated`` is the value the model states, for a ``stated`` fact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    answer: FactAnswer
    state: FactState
    stated: str = ""


class ControlFacts(BaseModel):
    """The facts one applied Control writes after its scope resolution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    control: str = Field(pattern=CONTROL_ID)
    name: str
    facts: list[ControlFact]

    def writes(self) -> list[FactAnswer]:
        """Each fact this Control writes: every fact but a ``stated`` one."""
        return [fact.answer for fact in self.facts if fact.state != "stated"]


def _placed(facts: Sequence[FactAnswer], model: SystemModel, elements: Sequence[str]):
    """Each fact of a Control on each element its scope covers. A fact takes
    the element; an attribute the element does not have is no fact of it."""
    for fact in facts:
        for element_id in elements:
            element = model.get(element_id)
            attribute = key_ref(fact.key).attribute
            if element is None or (
                fact.kind == "attribute" and attribute not in attribute_names(element)
            ):
                continue
            yield fact.model_copy(update={"key": with_element(fact.key, element_id)})


def _stated_value(model: SystemModel, fact: FactAnswer) -> str | None:
    """The value ``model`` states for the fact, or ``None`` where it is open."""
    ref = key_ref(fact.key)
    if fact.kind == "attribute":
        if open_attribute(model, ref.element_id, ref.attribute):
            return None
        return str(getattr(model.get(ref.element_id), ref.attribute))
    return None


def control_facts(
    copies: Sequence[CopiedControl],
    scopes: Sequence[ControlResolution] | None,
    model: SystemModel,
    catalog: AssertionCatalog | None,
) -> list[ControlFacts]:
    """The facts each applied copy writes onto ``model``, in the copies'
    order (ADR 0125).

    **The one reader of what a job's Controls write.** A Control fills only
    a fact that the model the questions read leaves open. A fact the model
    states is ``stated``, with the stated value, and blocks nothing.
    """
    prepared = prepared_model(model, catalog)
    written = []
    for copy, resolution in zip(
        copies, resolutions(copies, scopes, model), strict=True
    ):
        if not isinstance(resolution, AppliedScope):
            continue
        facts = []
        for fact in _placed(copy.document.facts, model, resolution.element_ids()):
            stated = _stated_value(prepared, fact)
            facts.append(
                ControlFact(answer=fact, state="written")
                if stated is None
                else ControlFact(answer=fact, state="stated", stated=stated)
            )
        written.append(
            ControlFacts(control=copy.id, name=copy.document.name, facts=facts)
        )
    return written


def with_own_answers(
    entries: Sequence[ControlFacts], own: Sequence[FactAnswer]
) -> list[ControlFacts]:
    """``entries`` with each fact an own answer of ``own`` lies over marked
    ``changed``, and each other fact it writes marked ``written``. ``own``
    holds no fact as a Control wrote it
    (:meth:`~analysis_service.answer_state.AnswerState.own`)."""
    said = {answer.key for answer in own}

    def state(fact: ControlFact) -> ControlFact:
        if fact.state == "stated":
            return fact
        changed = fact.answer.key in said
        return fact.model_copy(update={"state": "changed" if changed else "written"})

    return [
        entry.model_copy(update={"facts": [state(fact) for fact in entry.facts]})
        for entry in entries
    ]


def control_answers(entries: Sequence[ControlFacts]) -> AnswerSet:
    """Every fact the Controls write, merged as :func:`merged_facts` merges
    a facet answer. :func:`refuse_disagreement` refuses two values first."""
    facts: list[FactAnswer] = []
    for entry in entries:
        facts = merged_facts(facts, entry.writes())
    return AnswerSet(facts=tuple(facts))


def control_lines(entries: Sequence[ControlFacts]) -> list[tuple[str, FactAnswer]]:
    """Each fact a Control writes with the Control's name: one line of the
    answers Source each (:func:`~analysis_service.fact_answers.control_line`)."""
    return [(entry.name, fact) for entry in entries for fact in entry.writes()]


def _disagree(one: FactAnswer, other: FactAnswer) -> bool:
    """Whether two answers to one fact hold two values. Facets disagree
    only where one facet holds two answers."""
    if one.facets is not None and other.facets is not None:
        return any(
            other.facets.get(facet, answer) != answer
            for facet, answer in one.facets.items()
        )
    return one.value != other.value


def refuse_disagreement(
    entries: Sequence[ControlFacts],
    model: SystemModel,
    catalog: AssertionCatalog | None,
) -> None:
    """Raise a :class:`ValueError` where a Control writes a value its fact
    cannot hold, or two Controls write one fact with two values. Two Controls
    with one value both stay writers.

    Each Control's written facts take the answer check of
    :func:`~analysis_service.fact_writes.check_fact_answers` (ADR 0125), and
    the refusal names the Control.
    """
    for entry in entries:
        written = [fact.answer for fact in entry.facts if fact.state == "written"]
        try:
            check_fact_answers(written, model, catalog, reopen=True)
        except ValueError as exc:
            raise ValueError(f"The Control “{entry.name}” cannot apply: {exc}") from exc
    first: dict[tuple[str, ...], tuple[str, FactAnswer]] = {}
    for entry in entries:
        for fact in entry.writes():
            control, earlier = first.setdefault(fact.key, (entry.control, fact))
            if control == entry.control or not _disagree(earlier, fact):
                continue
            ref = key_ref(fact.key)
            element = model.get(ref.element_id)
            place = ref.element_id if element is None else element.name
            subject = ref.attribute or ref.question
            raise ValueError(
                f"Two Controls write “{subject}” on {place} with two values:"
                f" “{earlier.value}” and “{fact.value}”. Untick one, or edit one"
                " Control so that they agree."
            )


#: What each outcome of a row means, as the report's Controls section says it.
ROW_MEANINGS: Mapping[RowOutcome, str] = MappingProxyType(
    {
        "applied": (
            "Applied: the critic ruled out each finding listed here, because you"
            " state that this Control covers the unit. The service verified"
            " nothing."
        ),
        "refused": (
            "Refused: the critic judged that this Control does not show what each"
            " finding on the unit calls missing, so the findings stand."
        ),
        "not-reached": (
            "Not reached: each finding on this unit names no part that this"
            " Control protects, so the critic did not judge this row and the"
            " findings stand."
        ),
        "no-finding": (
            "No finding: no draft on this unit reached the critic with this row,"
            " so nothing was ruled."
        ),
    }
)


class EvidenceLine(BaseModel):
    """One line of an evidence file that a ruling's reason quotes, with the
    file, its class and the line's number."""

    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)

    file: str
    evidence_class: EvidenceClass = Field(alias="class")
    line: int
    text: str


class SectionRow(BaseModel):
    """One row of a Control in the report: its unit, its outcome over the
    job, each ruling and each evidence line a ruling quotes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    framework: str
    unit: str
    label: str
    version: str
    ticked: bool
    outcome: RowOutcome
    meaning: str
    rulings: list[RowRuling]
    quoted: list[EvidenceLine]


class SectionFact(BaseModel):
    """One fact a Control wrote, as the job row keeps it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str
    value: str
    state: FactState
    meaning: str
    stated: str


class SectionElement(BaseModel):
    """One element a Control in the report's Controls section reached."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str


class SectionEntry(BaseModel):
    """One copied Control in the report's Controls section."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str
    scope: ControlScope
    #: The words that name the scope in a sentence (:func:`scope_phrase`).
    scope_phrase: str
    state: ResolutionState
    outcome: MatchOutcome | None
    reason: LeftOutReason | None
    meaning: str
    elements: list[SectionElement]
    facts: list[SectionFact]
    rows: list[SectionRow]


#: A quotation in a ruling's reason: the text inside a pair of double quotes,
#: curly single quotes or backticks. A straight single quote is left out,
#: because an apostrophe is one. A curly closing quote that a word character
#: follows is an apostrophe, as in “owner’s”, and closes nothing.
_QUOTATION = re.compile(r"“([^”]+)”|\"([^\"]+)\"|‘(.+?)’(?!\w)|`([^`]+)`")


#: The widest straight-quoted span on one line of a reason, so a quotation of
#: a line that itself holds a straight double quote is read whole.
_WIDEST_STRAIGHT_QUOTATION = re.compile(r'"(.+)"')


def _quotes(line: str, quotation: str) -> bool:
    """Whether ``quotation`` quotes ``line``: the shorter of the two stands in
    the longer, and is at least three quarters as long, where each run of
    spaces counts as one space."""
    short, long = sorted((" ".join(line.split()), " ".join(quotation.split())), key=len)
    return short in long and 4 * len(short) >= 3 * len(long)


def _quotations(reason: str) -> list[str]:
    """Each quotation of ``reason``, stripped, with the empty ones left out.
    A straight-quoted span also counts at its widest on a line."""
    found = [text for groups in _QUOTATION.findall(reason) for text in groups]
    found += _WIDEST_STRAIGHT_QUOTATION.findall(reason)
    return [text.strip() for text in found if text.strip()]


def quotes_control(reason: str, control: AppliedControl) -> bool:
    """Whether ``reason`` quotes the Control's name or a line of its evidence,
    which is what a ``control`` rejection rests on (ADR 0126). The review seam
    sends back a ``control`` rejection that quotes neither."""
    quotations = _quotations(reason)
    return any(_quotes(control.name, quotation) for quotation in quotations) or bool(
        _quoted(reason, control.evidence)
    )


def _quoted(reason: str, evidence: Sequence[CopiedEvidence]) -> list[EvidenceLine]:
    """Each line of ``evidence`` that a quotation of ``reason`` quotes, once,
    by file and line. A line counts where its text, stripped, holds a letter
    or a digit. A reason that quotes nothing quotes no line."""
    quotations = _quotations(reason)
    lines = []
    for file in evidence:
        for number, line in enumerate(text_lines(file.text), start=1):
            text = line.strip()
            if any(char.isalnum() for char in text) and any(
                _quotes(text, quotation) for quotation in quotations
            ):
                lines.append(
                    EvidenceLine.model_validate(
                        {"file": file.name, "class": file.evidence_class}
                        | {"line": number, "text": text}
                    )
                )
    return lines


def controls_section(
    copies: Sequence[CopiedControl],
    scopes: Sequence[ControlResolution] | None,
    facts: Sequence[ControlFacts] | None,
    model: SystemModel,
    blocks: Sequence[FrameworkAnalysis],
) -> list[SectionEntry]:
    """The report's Controls section: each copy of the job, what it resolved
    to, each fact it wrote with the state the job row keeps, and each row
    with its outcome from the rulings ``blocks`` hold (ADRs 0125 and 0126).
    Nothing here recomputes a fact's state from the model."""
    rulings = row_rulings(
        [claim for block in blocks for claim in block.all_claims()],
        [entry for block in blocks for entry in block.returned_claims],
    )
    written = {entry.control: entry for entry in facts or ()}
    packages_run: dict[str, FrameworkPackage] = {
        block.framework: PACKAGES[block.framework] for block in blocks
    }
    unreached = {
        (control.id, unit)
        for control in applied_controls(copies, scopes, model)
        for block in blocks
        for unit in unreached_rows(block.claims, packages_run[block.framework], control)
    }
    entries = []
    for copy, resolution in zip(
        copies, resolutions(copies, scopes, model), strict=True
    ):
        if isinstance(resolution, AppliedScope):
            reached, outcome, reason = (
                resolution.element_ids(),
                resolution.outcome,
                None,
            )
        else:
            reached, outcome, reason = (), None, resolution.reason
        elements = [
            SectionElement(id=element.id, name=element.name)
            for element_id in reached
            if (element := model.get(element_id)) is not None
        ]
        rows = []
        # A row of a framework the analysis did not run has no outcome here.
        for row in (row for row in copy.rows if row.framework in packages_run):
            ruled = rulings.get(ControlRowRef(control=copy.id, unit=row.unit), [])
            row_result = row_outcome(ruled, unreached=(copy.id, row.unit) in unreached)
            rows.append(
                SectionRow(
                    framework=row.framework,
                    unit=row.unit,
                    label=packages_run[row.framework].record.text_of_unit(row.unit),
                    version=row.version,
                    ticked=row.ticked,
                    outcome=row_result,
                    meaning=ROW_MEANINGS[row_result],
                    rulings=ruled,
                    quoted=list(
                        dict.fromkeys(
                            line
                            for ruling in ruled
                            if ruling.ruling == "applied"
                            for line in _quoted(ruling.reason, copy.evidence)
                        )
                    ),
                )
            )
        entry = written.get(copy.id)
        entries.append(
            SectionEntry(
                id=copy.id,
                name=copy.document.name,
                scope=copy.document.scope,
                scope_phrase=scope_phrase(copy.document.scope),
                state=resolution.state,
                outcome=outcome,
                reason=reason,
                meaning=resolution.meaning,
                elements=elements,
                facts=[
                    SectionFact(
                        label=fact_label(fact.answer.key, model),
                        value=fact.answer.value,
                        state=fact.state,
                        meaning=FACT_MEANINGS[fact.state],
                        stated=fact.stated,
                    )
                    for fact in (entry.facts if entry else ())
                ],
                rows=rows,
            )
        )
    return entries
