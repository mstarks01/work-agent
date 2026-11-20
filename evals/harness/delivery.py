"""What a missed must-find's lane was given, read from the archive (#2271, #2314).

:mod:`evals.harness.losses` charges each miss to a cause. This module traces
every charged miss through its lane, whatever the cause, and states two things
apart: whether the lane was sent the leads at the reference's place, and
whether it was sent the signed facts at that place. A draft at the place shows
neither. A lane can write about the right element without the principal, the
authority or the protection the finding turns on.

**Lead identities delivered.** For each row, :class:`LaneTrace` gives these
readings:

* ``delivered``: the strongest :data:`~evals.harness.losses.Relation` between
  the reference's place and a candidate in the lane's captured
  ``<case>.lanes.json``, or ``absent``.
* ``same_leads``: whether the captured and the rebuilt candidate sets hold the
  same :func:`~analysis_service.candidates.leads`. A lead is a lane, a rule
  and elements only, so a candidate's facts and a rule's question text are not
  part of this reading.
* ``same_lane_artifacts``: whether the lane's captured artifacts (each
  :data:`~analysis_service.graph.LaneArtifact`) are, byte for byte, the ones
  that :func:`~evals.harness.triggers.rebuilt_lane_inputs` writes today. A
  changed fact or question text reads ``differs`` here. The lane's other
  inputs have no rebuild from the archive, and
  :func:`~evals.harness.panels.preparation_panel` names each of them.

These three are ``comparison``, which is ``None`` where the sweep captured no
lane material, or where the capture is refused: a case's reason is in
``untraced``. The other two readings stand for every traced row:

* ``rebuilt``: the strongest relation over the candidates that today's rules
  write for the archived model and the archived catalog, with the case's
  options from today's corpus. The archive records no options, so a captured
  reading's ``differs`` can come from an options edit as well as from a rule
  edit.
* ``archived_lead_rules``: the rules that
  :func:`~evals.harness.triggers.archived_lead_rules` reads as leading to the
  reference. A rule leads when one of its candidates names an element ID the
  reference names. The relation resolves a cited flow to its two endpoints
  first, so a row can read ``overlap`` with no lead rule: a candidate on a
  flow's endpoint shares a place with a reference that cites the flow, and
  names none of its element IDs.

The relation reads the candidate as the second side of
:func:`~evals.harness.losses.relation`, so ``draft-contains`` is a candidate
whose place contains the reference's, at a coarser grain. A candidate that
shares an element is a structural overlap only. It does not show that the
rule's question is the mechanism the reference describes.

**Place facts delivered.** A place fact is a row of the case's signed
``facts.json`` that :func:`~evals.harness.replay.required_rows` counts and
whose subject is one of the reference's elements. No reference names the facts
its mechanism needs, so a place fact is a fact at the place, not a required
fact. :class:`PlaceFacts` reads each one at each :data:`PlaceStage`, and each
reading is ``present``, ``absent`` or ``unavailable`` with its reason. A fact
is delivered where a stage of the lane input holds it. The graph stages count
only where the signed rows settle the fact's field in one value; elsewhere they
read ``unavailable``, and the evidence catalog alone decides. A row with no
place fact reads ``required facts: undefined``.

**The matched control.** Each row carries a must-find of the same case and
lane that the run answered with the right action at the right place: a
``matched`` row first, an ``unstated`` row second. The control is traced the
same way as the miss. A row with no such must-find reads ``no control``, and
its loss stays unresolved. A control never comes from another case, because
each case's lanes are given other material.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic import ValidationError

from analysis_service.analysis import control_state
from analysis_service.assertions import (
    SETTLING_REASONS,
    Assertion,
    ProjectedField,
    Projection,
    assertion_id,
    project,
    projected_attribute,
)
from analysis_service.candidates import CandidateSet, leads
from analysis_service.evidence import catalog_refs
from analysis_service.graph import (
    STATE_EVIDENCE_CATALOG,
    STATE_SYSTEM_MODEL,
    LaneArtifact,
    unfence,
)
from analysis_service.report import Report
from analysis_service.system_model import Element
from evals.harness.artifact import EvalArtifact
from evals.harness.bundle import finished_file, read_report, stride_block
from evals.harness.identity import CaseFlows
from evals.harness.lane_replay import (
    SharedInput,
    captured_lane,
    captured_shared,
    lanes_file,
    material_at,
)
from evals.harness.losses import RELATIONS, UNREAD_CAUSE, Relation, relation
from evals.harness.modes import PROJECTION_COMPARED, AssertionResult, EvalRunError
from evals.harness.reference import GoldenCase, flows_by_case
from evals.harness.replay import (
    AnsweredFate,
    AssertionReplay,
    SignedReference,
    replay_assertions,
    required_rows,
    signed_reference,
    unsigned_reason,
)
from evals.harness.scorecard import (
    ReferenceRow,
    RunIdentity,
    Scorecard,
    charged_misses,
)
from evals.harness.triggers import archived_lead_rules, rebuilt_lane_inputs

#: How the closest candidate stands to the reference's place, or ``absent``
#: where no candidate in the lane shares an element with it.
PlaceRelation = Relation | Literal["absent"]

#: Whether a reading of a lane's capture is the same reading of its rebuild.
Agreement = Literal["agrees", "differs"]


@dataclass(frozen=True)
class LaneComparison:
    """The readings of a captured lane: what it was sent, against the rebuild."""

    delivered: PlaceRelation
    same_leads: Agreement
    same_lane_artifacts: Agreement

    def to_json(self) -> dict[str, Any]:
        return {
            "delivered": self.delivered,
            "same_leads": self.same_leads,
            "same_lane_artifacts": self.same_lane_artifacts,
        }

    def describe(self) -> str:
        return (
            f"lead identities delivered {self.delivered}, same leads"
            f" {self.same_leads}, same lane artifacts {self.same_lane_artifacts}"
        )


@dataclass(frozen=True)
class LaneTrace:
    """One reference's lane, as the archive holds it and as today's rules rebuild it."""

    #: ``None`` where the sweep captured no lane material for the case, or
    #: where the trace refused the capture; ``Delivery.untraced`` names why.
    comparison: LaneComparison | None
    rebuilt: PlaceRelation
    archived_lead_rules: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "comparison": (
                None if self.comparison is None else self.comparison.to_json()
            ),
            "rebuilt": self.rebuilt,
            "archived_lead_rules": list(self.archived_lead_rules),
        }


def closest_relation(
    reference_ids: Sequence[str], candidates: CandidateSet, flows: CaseFlows
) -> PlaceRelation:
    """The strongest relation of one of ``candidates`` to the reference's place."""
    found = {
        relation(reference_ids, candidate.element_ids, flows)
        for candidate in candidates.candidates
    }
    return next((strength for strength in RELATIONS if strength in found), "absent")


@dataclass(frozen=True)
class SentLane:
    """One lane's captured artifacts, and the candidates they hold."""

    artifacts: Mapping[LaneArtifact, str]
    candidates: CandidateSet


def sent_lane(material: Mapping[str, Any], lane: str) -> SentLane:
    """The STRIDE ``lane`` as ``material`` captured it, or a refusal."""
    artifacts = captured_lane(material, "stride", lane)
    try:
        candidates = CandidateSet.model_validate_json(unfence(artifacts["candidates"]))
    except ValidationError as error:
        raise EvalRunError(
            f"the {lane} lane's captured candidates are not a candidate set:"
            f" {error.error_count()} error(s)"
        ) from error
    return SentLane(artifacts, candidates)


def _agreement(sent: object, rebuilt: object) -> Agreement:
    return "agrees" if sent == rebuilt else "differs"


def trace_lane(
    reference_ids: Sequence[str],
    lane: str,
    report: Report,
    sent: SentLane | None,
    flows: CaseFlows,
    options: Mapping[str, object],
) -> LaneTrace:
    """Trace one reference through its lane: ``sent`` is ``None`` where untraced.

    The rebuild is
    :func:`~evals.harness.triggers.rebuilt_lane_inputs` over the archived model
    and catalog with the case's ``options``.
    """
    inputs = rebuilt_lane_inputs("stride", report, options)
    rebuilt = inputs.candidates[lane]
    comparison = None
    if sent is not None:
        comparison = LaneComparison(
            delivered=closest_relation(reference_ids, sent.candidates, flows),
            same_leads=_agreement(
                leads({lane: sent.candidates}), leads({lane: rebuilt})
            ),
            same_lane_artifacts=_agreement(
                dict(sent.artifacts), dict(inputs.artifacts[lane])
            ),
        )
    return LaneTrace(
        comparison=comparison,
        rebuilt=closest_relation(reference_ids, rebuilt, flows),
        archived_lead_rules=archived_lead_rules(
            rebuilt, reference_ids, stride_block(report).fired_rules
        ),
    )


#: Where a place fact is read, in the order a fact reaches a lane. The raw
#: extraction is the model's own reading of the sources. The assertion catalog
#: is the report's catalog, joined to the signed row through
#: :func:`~evals.harness.replay.replay_assertions`. The projected model is the
#: report's ``system_model``, read at the field
#: :func:`~analysis_service.assertions.projected_attribute` names. The lane
#: system model and the lane evidence catalog are the shared keys the sweep
#: captured, and the candidate facts are the captured lane's candidates.
PlaceStage = Literal[
    "raw extraction",
    "assertion catalog",
    "projected model",
    "lane system model",
    "lane evidence catalog",
    "candidate facts",
]
PLACE_STAGES: tuple[PlaceStage, ...] = get_args(PlaceStage)

#: The stages of the lane input: a place fact reached the lane where one of
#: them holds it. A stage of :data:`GRAPH_STAGES` counts only for a row that
#: reaches a graph field.
LANE_INPUT: tuple[PlaceStage, ...] = (
    "lane system model",
    "lane evidence catalog",
    "candidate facts",
)

#: The stages that read a graph field, which a row that reaches none skips.
GRAPH_STAGES: tuple[PlaceStage, ...] = (
    "projected model",
    "lane system model",
    "candidate facts",
)

#: How a stage stands to a place fact, in the order a reading wins where
#: several candidates read one stage: one that holds the fact, then one no
#: reading decides, then one that lacks it.
StageState = Literal["present", "unavailable", "absent"]
STATE_PREFERENCE: tuple[StageState, ...] = get_args(StageState)

#: Whether the lane input held a place fact.
FactDelivery = Literal["delivered", "not delivered", "unavailable"]
FACT_DELIVERIES: tuple[FactDelivery, ...] = get_args(FactDelivery)

#: The replay fates that answer a signed row with its value. ``worded`` and
#: ``wrong_certainty`` keep the value, and differ in its words or its force.
VALUE_KEEPING_FATES: frozenset[AnsweredFate] = frozenset(
    {"found", "worded", "wrong_certainty"}
)

#: How a reference's place facts stand: the case's facts are not read, the
#: place holds no signed fact, or each fact is traced.
PlaceFactSet = Literal["unread", "empty", "traced"]
PLACE_FACT_SETS: tuple[PlaceFactSet, ...] = get_args(PlaceFactSet)

#: Why an analysis sweep has no raw extraction to read.
NO_EXTRACTION = "an analysis sweep seeds the blessed model, so no extraction ran"


@dataclass(frozen=True)
class StageReading:
    """One place fact at one stage: its state, and what the stage holds or why."""

    state: StageState
    reason: str

    def to_json(self) -> dict[str, str]:
        return {"state": self.state, "reason": self.reason}


@dataclass(frozen=True)
class PlaceFactTrace:
    """One signed place fact, read at each :data:`PlaceStage`."""

    #: The signed row's assertion ID.
    fact_id: str
    predicate: str
    subject: str
    #: The graph field the row reaches, or ``""`` where it reaches none.
    attribute: ProjectedField | Literal[""]
    #: Whether a graph field can carry the fact: the row reaches one, and the
    #: signed rows settle it in one value. Where not, the graph stages read
    #: ``unavailable`` and do not count toward delivery.
    reads_graph: bool
    stages: Mapping[PlaceStage, StageReading]

    @property
    def delivered(self) -> FactDelivery:
        """Whether a stage of the lane input held the fact, or no reading says."""
        states = {
            self.stages[stage].state
            for stage in LANE_INPUT
            if self.reads_graph or stage not in GRAPH_STAGES
        }
        if "present" in states:
            return "delivered"
        return "unavailable" if "unavailable" in states else "not delivered"

    @property
    def lost_at(self) -> PlaceStage | None:
        """Where a fact the lane was not sent was lost.

        The first stage that reads ``absent`` after the last stage that reads
        ``present``. A fact the blessed model carries reaches the lane with no
        catalog row, so an absent catalog before a present model is not the
        loss.
        """
        if self.delivered != "not delivered":
            return None
        present_at = [
            index
            for index, stage in enumerate(PLACE_STAGES)
            if self.stages[stage].state == "present"
        ]
        start = present_at[-1] + 1 if present_at else 0
        lost = [
            stage
            for stage in PLACE_STAGES[start:]
            if self.stages[stage].state == "absent"
        ]
        # "not delivered" means a lane stage reads absent after any present one.
        assert lost, "a fact not delivered has an absent stage after its last present"
        return lost[0]

    def to_json(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "predicate": self.predicate,
            "subject": self.subject,
            "attribute": self.attribute,
            "stages": {stage: one.to_json() for stage, one in self.stages.items()},
            "delivered": self.delivered,
            "lost_at": self.lost_at,
        }


@dataclass(frozen=True)
class PlaceFacts:
    """The signed facts at one reference's place, each traced to the lane."""

    traces: tuple[PlaceFactTrace, ...]
    #: Why the case's facts are not read, or ``""`` where they are.
    unread: str

    @property
    def kind(self) -> PlaceFactSet:
        if self.unread:
            return "unread"
        return "traced" if self.traces else "empty"

    def describe(self) -> str:
        if self.kind == "unread":
            return f"place facts unavailable: {self.unread}"
        if self.kind == "empty":
            return "required facts: undefined"
        delivered = Counter(fact.delivered for fact in self.traces)
        shown = f"place facts delivered {delivered['delivered']} of {len(self.traces)}"
        if delivered["unavailable"]:
            shown += f", {delivered['unavailable']} unavailable"
        lost = Counter(fact.lost_at for fact in self.traces)
        named = [f"{lost[stage]} at {stage}" for stage in PLACE_STAGES if lost[stage]]
        if named:
            shown += f" (lost: {', '.join(named)})"
        return shown

    def to_json(self) -> dict[str, Any]:
        return {
            "unread": self.unread,
            "facts": [fact.to_json() for fact in self.traces],
        }


@dataclass(frozen=True)
class CaseFacts:
    """What one case holds for its place-fact traces."""

    reference: SignedReference | None
    #: Why ``reference`` is ``None``, or ``""``.
    unread: str
    #: The report's catalog against the signed reference, or ``None`` where the
    #: report holds no assertion record.
    replay: AssertionReplay | None
    #: Each element of the report's model, by its ID.
    elements: Mapping[str, Element]
    #: What each graph attribute the signed rows reach would hold, by element
    #: and attribute.
    projections: Mapping[tuple[str, str], Projection]
    #: ``None`` where the case's lanes file is refused.
    shared: SharedInput | None
    #: Why ``shared`` is ``None``, or ``""``.
    unshared: str


def case_facts(
    case: GoldenCase,
    corpus: Path,
    report: Report,
    shared: SharedInput | None,
    unshared: str,
) -> CaseFacts:
    """The signed reference and the report's catalog replayed against it."""
    reference = signed_reference(corpus, case)
    replayed = None
    if reference is not None and report.assertions is not None:
        # The report keeps no proposal, and the replay reads none.
        result = AssertionResult(case.id, proposal={}, record=report.assertions)
        replayed = replay_assertions(case, reference, result)
    return CaseFacts(
        reference=reference,
        unread="" if reference is not None else unsigned_reason(corpus, case),
        replay=replayed,
        elements={element.id: element for element in report.system_model.elements()},
        projections=(
            {}
            if reference is None
            else {
                (one.element_id, one.attribute): one
                for one in project(reference.catalog)
            }
        ),
        shared=shared,
        unshared=unshared,
    )


def field_reading(held: object, value: str, attribute: ProjectedField) -> StageReading:
    """Whether a field that holds ``held`` states the projected ``value``.

    Text that :data:`~evals.harness.modes.PROJECTION_COMPARED` reads as
    another value is ``absent``: a closed term by its value, a mechanism by its
    state, a zone by identity. Other text that it reads as the same value is
    ``present``, except two stated mechanisms in other words: those can name
    two mechanisms, so they are ``unavailable``.
    """
    if held is None:
        return StageReading("absent", "the field is missing or null")
    if not isinstance(held, str):
        return StageReading("absent", f"holds {held!r}, not text")
    if held == value:
        return StageReading("present", f"holds {held!r}")
    compared = PROJECTION_COMPARED[attribute]
    if compared(held) != compared(value):
        return StageReading("absent", f"holds {held!r}")
    if control_state(held) != "stated":
        return StageReading("present", f"holds {held!r}")
    return StageReading(
        "unavailable",
        f"holds {held!r}, in the same state as {value!r}; whether it names the"
        " same mechanism is a reader's question",
    )


def catalog_reading(fact: str, facts: CaseFacts) -> tuple[StageReading, str]:
    """The catalog stage, and the ID the run's catalog keys its answering row by."""
    if facts.replay is None:
        # A job that ran no assertion pass kept no catalog to carry the fact.
        return StageReading("absent", "the report holds no assertion record"), ""
    row = next(row for row in facts.replay.rows if row.reference == fact)
    if row.fate in VALUE_KEEPING_FATES:
        return StageReading("present", f"{row.fate}: {row.produced}"), row.produced_own
    if fact in facts.replay.aligned:
        return StageReading("present", "aligned under another flow label"), ""
    return StageReading("absent", row.fate), ""


def shared_refusal(facts: CaseFacts, key: str) -> StageReading | None:
    """The ``unavailable`` reading of a shared ``key`` the trace did not read, or ``None``."""
    if facts.shared is None:
        return StageReading("unavailable", facts.unshared)
    if key in facts.shared.refused:
        return StageReading("unavailable", facts.shared.refused[key])
    return None


def lane_evidence_reading(
    catalog: StageReading, produced_own: str, facts: CaseFacts
) -> StageReading:
    """The lane evidence catalog stage: the key the run's own row is filed under.

    ``produced_own`` is that row's ID before the signed aliases, as
    :attr:`~evals.harness.replay.ReferenceRowFate.produced_own` holds it.
    """
    refusal = shared_refusal(facts, STATE_EVIDENCE_CATALOG)
    if refusal is not None:
        return refusal
    assert facts.shared is not None and facts.shared.evidence_catalog is not None
    if catalog.state == "absent":
        return StageReading("absent", "no catalog row answers the fact")
    if not produced_own:
        return StageReading("unavailable", "the alignment names no produced row")
    if produced_own in catalog_refs(facts.shared.evidence_catalog):
        return StageReading("present", f"keys {produced_own}")
    return StageReading("absent", f"no key {produced_own}")


def lane_model_reading(
    subject: str, attribute: ProjectedField, value: str, facts: CaseFacts
) -> StageReading:
    """The lane system model stage: the subject's field in the captured model."""
    refusal = shared_refusal(facts, STATE_SYSTEM_MODEL)
    if refusal is not None:
        return refusal
    assert facts.shared is not None and facts.shared.elements is not None
    held = facts.shared.elements.get(subject, {}).get(attribute)
    return field_reading(held, value, attribute)


def candidate_reading(
    subject: str, attribute: ProjectedField, value: str, sent: SentLane | None
) -> StageReading:
    """The candidate facts stage: a candidate on the subject that carries the field.

    A candidate's facts are keyed by attribute, not by element, so an
    attribute fact of a candidate that names the subject is read as the
    subject's own.
    """
    if sent is None:
        return StageReading("unavailable", "the lane is untraced")
    readings = [
        field_reading(candidate.facts.get(attribute), value, attribute)
        for candidate in sent.candidates.candidates
        if subject in candidate.element_ids and attribute in candidate.facts
    ]
    if not readings:
        return StageReading(
            "absent", f"no candidate on the subject carries {attribute}"
        )
    states = {reading.state for reading in readings}
    state = next(each for each in STATE_PREFERENCE if each in states)
    return next(reading for reading in readings if reading.state == state)


def trace_fact(
    entry: Assertion, facts: CaseFacts, sent: SentLane | None
) -> PlaceFactTrace:
    """One signed place fact, read at each stage the archive holds."""
    assert facts.reference is not None
    fact_id = assertion_id(entry)
    catalog, produced_own = catalog_reading(fact_id, facts)
    stages: dict[PlaceStage, StageReading] = {
        "raw extraction": StageReading("unavailable", NO_EXTRACTION),
        "assertion catalog": catalog,
        "lane evidence catalog": lane_evidence_reading(catalog, produced_own, facts),
    }
    attribute = projected_attribute(entry.predicate, entry.subject)
    reads_graph = False
    if not attribute:
        stages |= dict.fromkeys(
            GRAPH_STAGES, StageReading("unavailable", "the row reaches no graph field")
        )
    elif (
        projection := facts.projections[entry.subject, attribute]
    ).reason not in SETTLING_REASONS:
        stages |= dict.fromkeys(
            GRAPH_STAGES,
            StageReading(
                "unavailable",
                f"the signed rows leave {attribute} unsettled: {projection.reason}",
            ),
        )
    else:
        reads_graph = True
        element = facts.elements.get(entry.subject)
        stages["projected model"] = field_reading(
            getattr(element, attribute, None), projection.value, attribute
        )
        stages["lane system model"] = lane_model_reading(
            entry.subject, attribute, projection.value, facts
        )
        stages["candidate facts"] = candidate_reading(
            entry.subject, attribute, projection.value, sent
        )
    return PlaceFactTrace(
        fact_id=fact_id,
        predicate=entry.predicate,
        subject=entry.subject,
        attribute=attribute,
        reads_graph=reads_graph,
        stages={stage: stages[stage] for stage in PLACE_STAGES},
    )


def place_facts(
    reference_ids: Sequence[str], facts: CaseFacts, sent: SentLane | None
) -> PlaceFacts:
    """The signed facts at the reference's place, each traced to the lane.

    A place fact's subject is one of the elements the reference names. A cited
    flow is not resolved to its endpoints, as the lead relation resolves it: a
    fact about an endpoint is a fact about another element.
    """
    if facts.reference is None:
        return PlaceFacts((), facts.unread)
    required = set(required_rows(facts.reference))
    return PlaceFacts(
        tuple(
            trace_fact(entry, facts, sent)
            for entry in facts.reference.entries
            if entry.subject in reference_ids and assertion_id(entry) in required
        ),
        "",
    )


def matched_control(
    miss: ReferenceRow, rows: Sequence[ReferenceRow]
) -> ReferenceRow | None:
    """A must-find of the miss's case and lane the run answered at its place.

    A ``matched`` row first, an ``unstated`` row second, and ``None`` where the
    case and lane hold neither. A ``re-keyed`` row is never one: its reading
    rests on an element the run's model did not hold.
    """
    same = [
        row
        for row in rows
        if (row.case, row.lane) == (miss.case, miss.lane)
        and row.compatibility != "re-keyed"
    ]
    return next((row for row in same if row.status == "matched"), None) or next(
        (row for row in same if row.cause == UNREAD_CAUSE), None
    )


@dataclass(frozen=True)
class TracedReference:
    """One must-find, traced through its lane: its leads and its place facts."""

    reference: ReferenceRow
    lane: LaneTrace
    place_facts: PlaceFacts

    def to_json(self) -> dict[str, Any]:
        return {
            "reference": self.reference.to_json(),
            "lane": self.lane.to_json(),
            "place_facts": self.place_facts.to_json(),
        }

    def describe(self) -> str:
        reference, lane = self.reference, self.lane
        compared = "untraced" if lane.comparison is None else lane.comparison.describe()
        return (
            f"{reference.case} #{reference.reference_index} {reference.lane}"
            f" {reference.cause or reference.status} [{reference.compatibility}]:"
            f" {compared}, rebuilt {lane.rebuilt}; lead rules today"
            f" {', '.join(reference.lead_rules) or '-'}, archived"
            f" {', '.join(lane.archived_lead_rules) or '-'}; {self.place_facts.describe()}"
        )


@dataclass(frozen=True)
class DeliveryRow(TracedReference):
    """One charged miss, traced, beside its matched control."""

    #: ``None`` where the case and lane hold no matched or unstated must-find.
    control: TracedReference | None

    def to_json(self) -> dict[str, Any]:
        return {
            **super().to_json(),
            "control": None if self.control is None else self.control.to_json(),
        }


@dataclass(frozen=True)
class Delivery:
    """One run's charged misses, each traced through its lane."""

    run: RunIdentity
    rows: tuple[DeliveryRow, ...]
    #: The must-finds the scorecard reads as ``re-keyed``. They are outside
    #: :func:`~evals.harness.scorecard.charged_misses`, and are counted here so
    #: the traced population is read beside them.
    re_keyed: int
    #: Each case whose lane material the trace does not read, and why: the
    #: sweep captured none, or the trace refused the capture.
    untraced: Mapping[str, str]

    def to_json(self) -> dict[str, Any]:
        return {
            "run": self.run.to_json(),
            "re_keyed": self.re_keyed,
            "untraced": dict(self.untraced),
            "rows": [row.to_json() for row in self.rows],
        }


def trace(
    card: Scorecard,
    artifact: EvalArtifact,
    cases: Sequence[GoldenCase],
    corpus: Path,
) -> Delivery:
    """Trace each charged miss of ``card`` and its control through the lane material."""
    by_id = {case.id: case for case in cases}
    misses = charged_misses(card.rows)
    # A row holds a dict, so its case and reference index key it.
    controls = {
        (row.case, row.reference_index): matched_control(row, card.rows)
        for row in misses
    }
    traced = [*misses, *(row for row in controls.values() if row is not None)]
    traced_cases = sorted({row.case for row in traced})
    reports = {
        case: read_report(_required(finished_file(artifact, case, "report"), case))
        for case in traced_cases
    }
    flows = flows_by_case(
        [by_id[case] for case in traced_cases],
        {case: report.system_model for case, report in reports.items()},
    )

    # A case's lanes are one file, so a lane the trace refuses refuses its case.
    # The shared keys are read apart from the lanes and from each other.
    sent: dict[str, dict[str, SentLane]] = {}
    shared: dict[str, SharedInput] = {}
    untraced: dict[str, str] = {}
    unshared: dict[str, str] = {}
    for case in traced_cases:
        try:
            material = material_at(lanes_file(artifact, case))
        except EvalRunError as error:
            untraced[case] = unshared[case] = str(error)
            continue
        try:
            sent[case] = {
                row.lane: sent_lane(material, row.lane)
                for row in traced
                if row.case == case
            }
        except EvalRunError as error:
            untraced[case] = str(error)
        shared[case] = captured_shared(material)
    facts_by_case = {
        case: case_facts(
            by_id[case],
            corpus,
            reports[case],
            shared.get(case),
            unshared.get(case, ""),
        )
        for case in traced_cases
    }

    def traced_reference(row: ReferenceRow) -> TracedReference:
        captured = sent.get(row.case, {}).get(row.lane)
        return TracedReference(
            reference=row,
            lane=trace_lane(
                row.elements,
                row.lane,
                reports[row.case],
                captured,
                flows[row.case],
                by_id[row.case].declaration("stride").options,
            ),
            place_facts=place_facts(row.elements, facts_by_case[row.case], captured),
        )

    rows = []
    for row in misses:
        miss = traced_reference(row)
        control = controls[row.case, row.reference_index]
        rows.append(
            DeliveryRow(
                reference=miss.reference,
                lane=miss.lane,
                place_facts=miss.place_facts,
                control=None if control is None else traced_reference(control),
            )
        )
    return Delivery(
        run=card.run,
        rows=tuple(rows),
        re_keyed=sum(row.compatibility == "re-keyed" for row in card.rows),
        untraced=untraced,
    )


def _required(path: Path | None, case: str) -> Path:
    """``path``, which a charged miss's case always has: a failed case has no row."""
    if path is None:
        raise EvalRunError(f"{case}: the sweep records the case as failed")
    return path


def render(result: Delivery) -> None:
    """Print each charged miss, its lane's trace, its control, and the populations."""
    print(
        f"{len(result.rows)} charged miss(es), {len(result.untraced)} case(s) with"
        f" lane material untraced; {result.re_keyed} re-keyed must-find(s) outside"
        " the population"
    )
    for case, reason in sorted(result.untraced.items()):
        print(f"  lane material untraced: {case}: {reason}")
    for row in result.rows:
        print(f"  {row.describe()}")
        print(
            "    no control"
            if row.control is None
            else f"    control {row.control.describe()}"
        )
