"""What a missed must-find's lane was given, read from the archive (#2271, #2314).

:mod:`evals.harness.losses` charges each miss to a cause. This module traces
every charged miss through its lane, whatever the cause, and states two things
apart: whether the lane was sent the leads at the reference's place, and
whether it was sent the signed facts at that place. A draft at the place shows
neither. A lane can write about the right element without the principal, the
authority or the control value the finding turns on.

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
reading is ``present``, ``absent`` or ``unavailable`` with its reason. A row
with no place fact reads ``required facts: undefined``.

**The matched control.** Each row carries a must-find of the same case and
lane that the run answered with the right action at the right place: a
``matched`` row first, an ``unstated`` row second. The control is traced the
same way as the miss. A row with no such must-find reads ``no control``, and
its loss stays unresolved. A control never comes from another case, because
each case's lanes are given other material.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic import ValidationError

from analysis_service.analysis import control_state
from analysis_service.assertions import (
    Assertion,
    AssertionCatalog,
    assertion_id,
    project,
    projected_attribute,
    subjects_of,
)
from analysis_service.candidates import CandidateSet, leads
from analysis_service.graph import (
    STATE_EVIDENCE_CATALOG,
    STATE_SYSTEM_MODEL,
    LaneArtifact,
    unfence,
)
from analysis_service.report import Report
from evals.harness.artifact import EvalArtifact
from evals.harness.bundle import finished_file, read_report, stride_block
from evals.harness.identity import CaseFlows
from evals.harness.lane_replay import captured_lane, lanes_file, material_at
from evals.harness.losses import RELATIONS, Relation, relation
from evals.harness.modes import AssertionResult, EvalRunError
from evals.harness.reference import GoldenCase, flows_by_case
from evals.harness.replay import (
    AnsweredFate,
    AssertionReplay,
    SignedReference,
    compares_exactly,
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
#: extraction is the model's own reading of the sources. The catalog is the
#: report's assertion catalog, joined to the signed row through
#: :func:`~evals.harness.replay.replay_assertions`. The projected model is the
#: report's ``system_model``, read at the field
#: :func:`~analysis_service.assertions.projected_attribute` names. The lane
#: system model and the lane evidence catalog are the shared keys the sweep
#: captured, and the candidate facts are the captured lane's candidates.
PlaceStage = Literal[
    "raw extraction",
    "catalog",
    "projected model",
    "lane system model",
    "lane evidence catalog",
    "candidate facts",
]
PLACE_STAGES: tuple[PlaceStage, ...] = get_args(PlaceStage)

#: The stages of the lane input: a place fact reached the lane where one of
#: them holds it. The lane system model counts only for a row that reaches a
#: graph field.
LANE_INPUT: tuple[PlaceStage, ...] = ("lane system model", "lane evidence catalog")

#: The stages that read a graph field, which a row that reaches none skips.
GRAPH_STAGES: tuple[PlaceStage, ...] = (
    "projected model",
    "lane system model",
    "candidate facts",
)

StageState = Literal["present", "absent", "unavailable"]

#: The replay fates that answer a signed row with its value. ``worded`` and
#: ``wrong_certainty`` keep the value, and differ in its words or its force.
ANSWERING_FATES: frozenset[AnsweredFate] = frozenset(
    {"found", "worded", "wrong_certainty"}
)

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
    fact: str
    predicate: str
    subject: str
    #: The graph field the row reaches, or ``""`` where it reaches none.
    attribute: str
    stages: Mapping[PlaceStage, StageReading]

    @property
    def delivered(self) -> bool | None:
        """Whether the lane input held the fact, or ``None`` where no reading says."""
        states = {
            self.stages[stage].state
            for stage in LANE_INPUT
            if self.attribute or stage not in GRAPH_STAGES
        }
        if "present" in states:
            return True
        return None if "unavailable" in states else False

    @property
    def lost_at(self) -> PlaceStage | None:
        """Where a fact the lane was not sent was lost.

        The first stage that reads ``absent`` after the last stage, up to the
        lane input, that reads ``present``. A fact the blessed model carries
        reaches the lane with no catalog row, so an absent catalog before a
        present model is not the loss.
        """
        if self.delivered is not False:
            return None
        path = PLACE_STAGES[: PLACE_STAGES.index(LANE_INPUT[-1]) + 1]
        held = [
            index
            for index, stage in enumerate(path)
            if self.stages[stage].state == "present"
        ]
        start = held[-1] + 1 if held else 0
        return next(
            stage for stage in path[start:] if self.stages[stage].state == "absent"
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "fact": self.fact,
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

    facts: tuple[PlaceFactTrace, ...]
    #: Why the case's facts are not read, or ``""`` where they are.
    unread: str

    def describe(self) -> str:
        if self.unread:
            return f"place facts unavailable: {self.unread}"
        if not self.facts:
            return "required facts: undefined"
        delivered = [fact.delivered for fact in self.facts]
        shown = f"place facts delivered {delivered.count(True)} of {len(self.facts)}"
        if None in delivered:
            shown += f", {delivered.count(None)} unavailable"
        lost = [fact.lost_at for fact in self.facts if fact.lost_at is not None]
        if lost:
            shown += f" (lost at {', '.join(lost)})"
        return shown

    def to_json(self) -> dict[str, Any]:
        return {
            "unread": self.unread,
            "facts": [fact.to_json() for fact in self.facts],
        }


@dataclass(frozen=True)
class SharedInput:
    """The shared keys a case's lanes were sent, as the sweep captured them."""

    #: Each element of the captured ``system_model``, by its ID.
    elements: Mapping[str, Mapping[str, Any]]
    evidence_catalog: str


def sent_shared(material: Mapping[str, Any]) -> SharedInput:
    """The captured ``system_model`` and ``evidence_catalog`` of a case, or a refusal."""
    shared = material.get("shared")
    held = shared if isinstance(shared, Mapping) else {}
    texts = {
        key: text
        for key in (STATE_SYSTEM_MODEL, STATE_EVIDENCE_CATALOG)
        if isinstance(text := held.get(key), str)
    }
    missing = sorted({STATE_SYSTEM_MODEL, STATE_EVIDENCE_CATALOG} - set(texts))
    if missing:
        raise EvalRunError(f"the capture holds no shared {', '.join(missing)} text")
    try:
        model = json.loads(unfence(texts[STATE_SYSTEM_MODEL]))
    except json.JSONDecodeError as error:
        raise EvalRunError(
            f"the captured system model is not JSON: {error.msg}"
        ) from error
    if not isinstance(model, dict):
        raise EvalRunError("the captured system model holds no JSON object")
    elements = {
        element["id"]: element
        for collection in model.values()
        if isinstance(collection, list)
        for element in collection
        if isinstance(element, dict) and isinstance(element.get("id"), str)
    }
    return SharedInput(elements, texts[STATE_EVIDENCE_CATALOG])


@dataclass(frozen=True)
class CaseFacts:
    """What one case holds for its place-fact traces."""

    reference: SignedReference | None
    #: Why ``reference`` is ``None``, or ``""``.
    unread: str
    report: Report
    #: The report's catalog against the signed reference, or ``None`` where the
    #: report holds no assertion record.
    replay: AssertionReplay | None
    #: ``None`` where the case's lane material is untraced.
    shared: SharedInput | None
    #: Why ``shared`` is ``None``, or ``""``.
    untraced: str


def case_facts(
    case: GoldenCase,
    corpus: Path,
    report: Report,
    shared: SharedInput | None,
    untraced: str,
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
        report=report,
        replay=replayed,
        shared=shared,
        untraced=untraced,
    )


def holds(held: object, value: str, predicate: str) -> StageReading:
    """Whether a field that holds ``held`` states the projected ``value``.

    A term and a graph-bound reference compare exactly
    (:func:`~evals.harness.replay.compares_exactly`). Free text compares by
    :func:`~analysis_service.analysis.control_state` first: two states that
    differ are two facts. Two stated free-text values are a reader's question.
    """
    if not isinstance(held, str):
        return StageReading("absent", "the field is not there")
    if held == value:
        return StageReading("present", f"holds {held!r}")
    if compares_exactly(predicate) or control_state(held) != control_state(value):
        return StageReading("absent", f"holds {held!r}")
    if control_state(held) != "stated":
        return StageReading("present", f"holds {held!r}")
    return StageReading(
        "unavailable",
        f"holds free text {held!r}; whether it states {value!r} is a reader's question",
    )


def _catalog(fact: str, facts: CaseFacts) -> tuple[StageReading, str]:
    """The catalog stage, and the produced row's ID where one answers the fact."""
    if facts.replay is None:
        # A job that ran no assertion pass kept no catalog to carry the fact.
        return StageReading("absent", "the report holds no assertion record"), ""
    row = next(row for row in facts.replay.rows if row.reference == fact)
    if row.fate in ANSWERING_FATES:
        return StageReading("present", f"{row.fate}: {row.produced}"), row.produced
    if fact in facts.replay.aligned:
        return StageReading("present", "aligned under another flow label"), ""
    return StageReading("absent", row.fate), ""


def _evidence(
    catalog: StageReading, produced: str, shared: SharedInput | None, untraced: str
) -> StageReading:
    if shared is None:
        return StageReading("unavailable", untraced)
    if catalog.state == "unavailable":
        return catalog
    if catalog.state == "absent":
        return StageReading("absent", "no catalog row answers the fact")
    if not produced:
        return StageReading("unavailable", "the alignment names no produced row")
    if f"`{produced}`" in shared.evidence_catalog:
        return StageReading("present", f"keys {produced}")
    return StageReading("absent", f"no key {produced}")


def _candidate_facts(
    subject: str, attribute: str, value: str, predicate: str, sent: SentLane | None
) -> StageReading:
    if sent is None:
        return StageReading("unavailable", "the lane is untraced")
    readings = [
        holds(candidate.facts.get(attribute), value, predicate)
        for candidate in sent.candidates.candidates
        if subject in candidate.element_ids and attribute in candidate.facts
    ]
    if not readings:
        return StageReading(
            "absent", f"no candidate on the subject carries {attribute}"
        )
    states = {reading.state for reading in readings}
    state: StageState = next(
        each for each in ("present", "unavailable", "absent") if each in states
    )
    return next(reading for reading in readings if reading.state == state)


def trace_fact(
    entry: Assertion, facts: CaseFacts, sent: SentLane | None
) -> PlaceFactTrace:
    """One signed place fact, read at each stage the archive holds."""
    assert facts.reference is not None
    fact = assertion_id(entry)
    catalog, produced = _catalog(fact, facts)
    stages: dict[PlaceStage, StageReading] = {
        "raw extraction": StageReading("unavailable", NO_EXTRACTION),
        "catalog": catalog,
        "lane evidence catalog": _evidence(
            catalog, produced, facts.shared, facts.untraced
        ),
    }
    attribute = projected_attribute(entry.predicate, entry.subject)
    if not attribute:
        stages |= dict.fromkeys(
            GRAPH_STAGES, StageReading("unavailable", "the row reaches no graph field")
        )
    else:
        labels = {subject.id: subject for subject in facts.reference.subjects}
        alone = AssertionCatalog(entries=[entry], subjects=subjects_of([entry], labels))
        (projection,) = project(alone)
        model = {
            element.id: element for element in facts.report.system_model.elements()
        }
        element = model.get(entry.subject)
        stages["projected model"] = holds(
            getattr(element, attribute, None), projection.value, entry.predicate
        )
        stages["lane system model"] = (
            StageReading("unavailable", facts.untraced)
            if facts.shared is None
            else holds(
                facts.shared.elements.get(entry.subject, {}).get(attribute),
                projection.value,
                entry.predicate,
            )
        )
        stages["candidate facts"] = _candidate_facts(
            entry.subject, attribute, projection.value, entry.predicate, sent
        )
    return PlaceFactTrace(
        fact=fact,
        predicate=entry.predicate,
        subject=entry.subject,
        attribute=attribute,
        stages={stage: stages[stage] for stage in PLACE_STAGES},
    )


def place_facts(
    reference_ids: Sequence[str], facts: CaseFacts, sent: SentLane | None
) -> PlaceFacts:
    """The signed facts at the reference's place, each traced to the lane."""
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
    case and lane hold neither.
    """
    same = [row for row in rows if (row.case, row.lane) == (miss.case, miss.lane)]
    return next((row for row in same if row.status == "matched"), None) or next(
        (row for row in same if row.cause == "unstated"), None
    )


@dataclass(frozen=True)
class TracedReference:
    """One must-find, traced through its lane: its leads and its place facts."""

    reference: ReferenceRow
    lane: LaneTrace
    facts: PlaceFacts

    def to_json(self) -> dict[str, Any]:
        return {
            "reference": self.reference.to_json(),
            "lane": self.lane.to_json(),
            "place_facts": self.facts.to_json(),
        }

    def describe(self) -> str:
        reference, lane = self.reference, self.lane
        compared = "untraced" if lane.comparison is None else lane.comparison.describe()
        return (
            f"{reference.case} #{reference.reference_index} {reference.lane}"
            f" {reference.cause or reference.status} [{reference.compatibility}]:"
            f" {compared}, rebuilt {lane.rebuilt}; lead rules today"
            f" {', '.join(reference.lead_rules) or '-'}, archived"
            f" {', '.join(lane.archived_lead_rules) or '-'}; {self.facts.describe()}"
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
    sent: dict[str, dict[str, SentLane]] = {}
    shared: dict[str, SharedInput] = {}
    untraced: dict[str, str] = {}
    for case in traced_cases:
        try:
            material = material_at(lanes_file(artifact, case))
            shared[case] = sent_shared(material)
            sent[case] = {
                row.lane: sent_lane(material, row.lane)
                for row in traced
                if row.case == case
            }
        except EvalRunError as error:
            shared.pop(case, None)
            untraced[case] = str(error)
    held = {
        case: case_facts(
            by_id[case],
            corpus,
            reports[case],
            shared.get(case),
            untraced.get(case, ""),
        )
        for case in traced_cases
    }

    def traced_reference(row: ReferenceRow) -> TracedReference:
        lane = sent.get(row.case, {}).get(row.lane)
        return TracedReference(
            reference=row,
            lane=trace_lane(
                row.elements,
                row.lane,
                reports[row.case],
                lane,
                flows[row.case],
                by_id[row.case].declaration("stride").options,
            ),
            facts=place_facts(row.elements, held[row.case], lane),
        )

    rows = []
    for row in misses:
        miss = traced_reference(row)
        control = controls[row.case, row.reference_index]
        rows.append(
            DeliveryRow(
                reference=miss.reference,
                lane=miss.lane,
                facts=miss.facts,
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
        f"{len(result.rows)} charged miss(es), each traced;"
        f" {result.re_keyed} re-keyed must-find(s) outside the population"
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
