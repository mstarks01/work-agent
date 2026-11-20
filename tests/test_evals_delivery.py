"""The delivery trace of a missed must-find's lane (#2271)."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from analysis_service.assertions import (
    AssertionCatalog,
    AssertionRecord,
    Projection,
    assertion_id,
    offered,
    project,
    projected_attribute,
)
from analysis_service.candidates import CandidateSet, generate_candidates
from analysis_service.evidence import evidence_catalog, prepared_view, render_catalog
from analysis_service.frameworks import package_for
from analysis_service.graph import render_model, unfence
from analysis_service.sources import render_fenced
from evals.harness import delivery, losses, panels, run
from evals.harness.artifact import load_artifact
from evals.harness.bundle import read_report, stride_block
from evals.harness.lane_replay import SharedInput, captured_lane
from evals.harness.ledger import Ledger
from evals.harness.modes import PROJECTION_COMPARED, AssertionResult, EvalRunError
from evals.harness.reference import development_cases, flows_by_case, load_corpus
from evals.harness.replay import replay_assertions, signed_reference
from evals.harness.scorecard import charged_misses
from evals.harness.triggers import rebuilt_lane_inputs

REPO_ROOT = Path(__file__).resolve().parent.parent
CASE = "05-cookbook-queue-webapp"
REPORT = (
    REPO_ROOT
    / "evals/baselines/d4edd64-gpt-5.6-terra-fd1c49af/mstarks01-ab5d72fb.reports"
    / f"{CASE}.report.json"
)


@pytest.fixture(scope="module")
def report():
    return read_report(REPORT)


@pytest.fixture(scope="module")
def case():
    (held,) = [
        case for case in load_corpus(REPO_ROOT / "evals/corpus") if case.id == CASE
    ]
    return held


@pytest.fixture(scope="module")
def flows(report, case):
    return flows_by_case([case], {CASE: report.system_model})[CASE]


@pytest.fixture(scope="module")
def options(case):
    return case.declaration("stride").options


@pytest.fixture(scope="module")
def tampering(report):
    package = package_for("stride")
    catalog = report.assertions.catalog if report.assertions else None
    return generate_candidates(
        report.system_model, package.lanes, package.rules, catalog
    )["tampering"]


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        (("process:background-worker", "store:database"), "equal"),
        (("store:message-queue",), "draft-contains"),
        (("process:web-application", "store:database"), "overlap"),
        (("entity:nobody",), "absent"),
    ],
)
def test_the_lead_is_the_strongest_relation_of_a_candidate(
    tampering, flows, reference, expected
):
    assert delivery.closest_relation(reference, tampering, flows) == expected


@pytest.fixture(scope="module")
def sent(report, options):
    """The tampering lane's artifacts, as a capture of today's rebuild."""
    return dict(rebuilt_lane_inputs("stride", report, options).artifacts["tampering"])


def _rendered(candidates: CandidateSet) -> str:
    return render_fenced(candidates.model_dump(mode="json"))


def _candidates(sent) -> CandidateSet:
    return CandidateSet.model_validate_json(unfence(sent["candidates"]))


def _sent(artifacts) -> delivery.SentLane:
    """The tampering lane of a capture that holds ``artifacts``."""
    return delivery.sent_lane(
        {"lanes": {"stride": {"tampering": dict(artifacts)}}}, "tampering"
    )


def _comparison(traced: delivery.LaneTrace) -> delivery.LaneComparison:
    assert traced.comparison is not None
    return traced.comparison


def test_the_captured_candidates_read_back_as_the_lane_was_sent(sent, tampering):
    material = {"lanes": {"stride": {"tampering": sent}}}

    held = captured_lane(material, "stride", "tampering")

    assert held == sent
    assert _candidates(held) == tampering


def _without(sent, name):
    return {key: text for key, text in sent.items() if key != name}


@pytest.mark.parametrize(
    ("lane", "change"),
    [
        ("tampering", lambda sent: _without(sent, "candidates")),
        ("tampering", lambda sent: _without(sent, "prior_cases")),
        ("tampering", lambda sent: {**sent, "drafts": "a stray key"}),
        ("tampering", lambda sent: {**sent, "candidates": {"lane": "tampering"}}),
        ("tampering", lambda sent: ["not", "an", "object"]),
        ("spoofing", lambda sent: sent),
    ],
)
def test_a_capture_that_prepare_cannot_write_is_refused(sent, lane, change):
    """A missing or stray artifact, a value that is not text, or no such lane."""
    material = {"lanes": {"stride": {"tampering": change(sent)}}}

    with pytest.raises(EvalRunError, match="capture"):
        captured_lane(material, "stride", lane)


def test_a_capture_the_rebuild_reproduces_agrees(report, sent, flows, options):
    traced = delivery.trace_lane(
        ("store:message-queue",), "tampering", report, _sent(sent), flows, options
    )

    assert _comparison(traced).same_leads == "agrees"
    assert _comparison(traced).same_lane_artifacts == "agrees"
    assert _comparison(traced).delivered == traced.rebuilt == "draft-contains"


def test_a_capture_without_a_rebuilt_candidate_differs(
    report, tampering, sent, flows, options
):
    trimmed = CandidateSet(lane="tampering", candidates=tampering.candidates[:1])

    traced = delivery.trace_lane(
        ("process:background-worker", "store:database"),
        "tampering",
        report,
        _sent({**sent, "candidates": _rendered(trimmed)}),
        flows,
        options,
    )

    assert _comparison(traced).same_leads == "differs"
    assert _comparison(traced).same_lane_artifacts == "differs"
    assert _comparison(traced).delivered == "absent"
    assert traced.rebuilt == "equal"


def _no_facts(candidates: CandidateSet) -> CandidateSet:
    return candidates.model_copy(
        update={
            "candidates": tuple(
                candidate.model_copy(update={"facts": {}})
                for candidate in candidates.candidates
            )
        }
    )


def _inverted_questions(candidates: CandidateSet) -> CandidateSet:
    return candidates.model_copy(
        update={"questions": dict.fromkeys(candidates.questions, "INVERTED QUESTION")}
    )


@pytest.mark.parametrize("change", [_no_facts, _inverted_questions])
def test_a_changed_candidate_payload_with_the_same_leads_is_not_equivalent(
    report, sent, flows, options, change
):
    """The #2314 reproduction: the same IDs, with other facts or questions."""
    changed = change(_candidates(sent))
    assert changed != _candidates(sent)

    traced = delivery.trace_lane(
        ("store:message-queue",),
        "tampering",
        report,
        _sent({**sent, "candidates": _rendered(changed)}),
        flows,
        options,
    )
    path = panels.preparation_panel(_one_row(traced)).path

    assert _comparison(traced).same_leads == "agrees"
    assert _comparison(traced).same_lane_artifacts == "differs"
    assert path is not None and path.fidelity == "known-different"


@pytest.mark.parametrize("artifact", ["scope", "reference_notes", "prior_cases"])
def test_a_changed_lane_artifact_differs(report, sent, flows, options, artifact):
    traced = delivery.trace_lane(
        ("store:message-queue",),
        "tampering",
        report,
        _sent({**sent, artifact: sent[artifact] + "\nchanged"}),
        flows,
        options,
    )

    assert _comparison(traced).same_leads == "agrees"
    assert _comparison(traced).same_lane_artifacts == "differs"


def test_an_agreeing_lane_leaves_the_evidence_payload_unverified(
    report, sent, flows, options
):
    """The evidence catalog is a shared key, which no rebuild reproduces."""
    traced = delivery.trace_lane(
        ("store:message-queue",), "tampering", report, _sent(sent), flows, options
    )
    path = panels.preparation_panel(_one_row(traced)).path

    assert path is not None and path.fidelity == "unverified"
    assert "not compared: the shared key evidence_catalog" in path.evidence
    assert "not compared: the prepared user turn" in path.evidence


def _one_row(traced: delivery.LaneTrace) -> delivery.Delivery:
    card = run.scorecard_of(BASELINE, REPO_ROOT / "evals/corpus", Ledger())
    row = delivery.DeliveryRow(
        reference=card.rows[0],
        lane=traced,
        place_facts=delivery.PlaceFacts((), ""),
        control=None,
    )
    return delivery.Delivery(run=card.run, rows=(row,), re_keyed=0, untraced={})


def test_an_uncaptured_lane_reads_only_the_rebuild(report, flows, options):
    traced = delivery.trace_lane(
        ("store:message-queue",), "tampering", report, None, flows, options
    )

    assert traced.comparison is None
    assert traced.rebuilt == "draft-contains"


def test_an_archived_lead_rule_is_one_the_archived_block_fired(report, flows, options):
    references = ("store:message-queue",)
    block = stride_block(report)
    kept = [
        rule
        for rule in block.fired_rules
        if rule != "tampering-unprotected-transit-crossing"
    ]
    trimmed = report.model_copy(
        update={
            "analyses": [
                block.model_copy(update={"fired_rules": kept})
                if each is block
                else each
                for each in report.analyses
            ]
        }
    )

    before = delivery.trace_lane(references, "tampering", report, None, flows, options)
    after = delivery.trace_lane(references, "tampering", trimmed, None, flows, options)

    assert before.archived_lead_rules == (
        "tampering-unprotected-transit-crossing",
        "tampering-unverified-write-to-store",
    )
    assert after.archived_lead_rules == ("tampering-unverified-write-to-store",)


BASELINE = (
    REPO_ROOT / "evals/baselines/6bff717-gpt-5.6-terra-24dda4db/mstarks01-e32ad9c5.json"
)


def _trace(artifact: Path) -> delivery.Delivery:
    corpus = REPO_ROOT / "evals/corpus"
    return delivery.trace(
        run.scorecard_of(artifact, corpus, Ledger()),
        load_artifact(artifact),
        development_cases(load_corpus(corpus)),
        corpus,
    )


def test_each_charged_miss_is_a_row_and_a_verb_row_is_traced_too():
    """A draft at the place does not show that the lane was sent its facts."""
    card = run.scorecard_of(BASELINE, REPO_ROOT / "evals/corpus", Ledger())

    traced = _trace(BASELINE)

    assert [row.reference for row in traced.rows] == list(charged_misses(card.rows))
    causes = {row.reference.cause for row in traced.rows}
    assert "verb" in causes and causes - losses.SILENT
    assert all(
        row.place_facts.traces
        or row.place_facts.describe() == "required facts: undefined"
        for row in traced.rows
    )


def test_a_sweep_with_no_lane_capture_reads_only_the_rebuild():
    traced = _trace(BASELINE)

    assert traced.rows
    assert {row.lane.comparison for row in traced.rows} == {None}
    assert set(traced.untraced) == {row.reference.case for row in traced.rows}
    facts = [fact for row in traced.rows for fact in row.place_facts.traces]
    assert facts
    assert {fact.stages["lane system model"].state for fact in facts} <= {"unavailable"}
    assert {fact.stages["catalog"].reason for fact in facts} == {
        "the report holds no assertion record"
    }


CAPTURED_CASE = "02-iot-fleet-telemetry"


def _captured_sweep(
    tmp_path: Path,
    extra: dict[str, str],
    case: str = CAPTURED_CASE,
    change: Callable[[dict[str, Any]], None] = lambda model: None,
    signed: bool = False,
) -> Path:
    """The Baseline with a lane capture of today's rebuild for one case.

    ``extra`` is added to each lane's artifacts, and ``change`` edits the
    captured system model. ``signed`` gives the case's report the signed
    reference as its catalog, projected as ``prepare`` projects one.
    """
    shutil.copy(BASELINE, tmp_path / BASELINE.name)
    reports = BASELINE.with_suffix(".reports")
    shutil.copytree(reports, tmp_path / reports.name)
    path = tmp_path / reports.name / f"{case}.report.json"
    report = read_report(path)
    (declared,) = [
        each for each in load_corpus(REPO_ROOT / "evals/corpus") if each.id == case
    ]
    held = None
    model = report.system_model
    catalog = evidence_catalog(model)
    if signed:
        reference = signed_reference(REPO_ROOT / "evals/corpus", declared)
        assert reference is not None
        held = reference.catalog
        model, _, catalog = prepared_view(model, held)
        report = report.model_copy(
            update={
                "system_model": model,
                "assertions": AssertionRecord(proposed=len(held.entries), catalog=held),
            }
        )
        path.write_text(report.model_dump_json())
    sent = rebuilt_lane_inputs(
        "stride", report, declared.declaration("stride").options
    ).artifacts
    dumped = model.model_dump(mode="json")
    change(dumped)
    material = {
        "prepared": {"user": "the input prepare handed the lanes"},
        "shared": {
            "system_model": render_model(dumped),
            "evidence_catalog": render_catalog(catalog, held),
        },
        "lanes": {"stride": {lane: {**given, **extra} for lane, given in sent.items()}},
    }
    lanes = tmp_path / reports.name / f"{case}.lanes.json"
    lanes.write_text(json.dumps(material))
    return tmp_path / BASELINE.name


def _captured_case_lane(traced: delivery.Delivery) -> delivery.LaneTrace:
    """The one traced lane of :data:`CAPTURED_CASE`."""
    (lane,) = [row.lane for row in traced.rows if row.reference.case == CAPTURED_CASE]
    return lane


def test_a_captured_lane_is_read_as_the_sweep_sent_it(tmp_path):
    traced = _trace(_captured_sweep(tmp_path, {}))

    lane = _captured_case_lane(traced)
    captured = _comparison(lane)
    assert captured.same_leads == captured.same_lane_artifacts == "agrees"
    assert captured.delivered == lane.rebuilt == "equal"
    assert CAPTURED_CASE not in traced.untraced


def test_a_captured_lane_prints_each_reading_once(tmp_path, capsys):
    delivery.render(_trace(_captured_sweep(tmp_path, {})))

    assert (
        ": lead identities delivered equal, same leads agrees, same lane"
        " artifacts agrees, rebuilt equal;" in capsys.readouterr().out
    )


@pytest.mark.parametrize(
    ("extra", "reason"),
    [({"drafts": "a stray key"}, "drafts"), ({"candidates": "not json"}, "candidate")],
)
def test_a_refused_lane_capture_reads_as_untraced_with_its_reason(
    tmp_path, extra, reason
):
    traced = _trace(_captured_sweep(tmp_path, extra))

    assert _captured_case_lane(traced).comparison is None
    assert reason in traced.untraced[CAPTURED_CASE]


@pytest.mark.parametrize(
    ("text", "reason"), [("not json", "not JSON"), ("[]", "object")]
)
def test_a_lanes_file_that_is_not_a_json_object_is_untraced(tmp_path, text, reason):
    artifact = _captured_sweep(tmp_path, {})
    (artifact.with_suffix(".reports") / f"{CAPTURED_CASE}.lanes.json").write_text(text)

    traced = _trace(artifact)

    assert _captured_case_lane(traced).comparison is None
    assert reason in traced.untraced[CAPTURED_CASE]


def test_the_command_writes_the_trace_with_its_record(tmp_path):
    out = tmp_path / "delivery.json"

    assert run.main(["delivery", str(BASELINE), "--out", str(out)]) == 0

    written = json.loads(out.read_text())
    assert written["record"]["command"] == "delivery"
    assert len(written["rows"]) == len(_trace(BASELINE).rows)


def test_a_shared_endpoint_is_a_relation_and_no_lead_rule(report, flows, options):
    """The worker is an endpoint of a candidate's flow, and no candidate names it."""
    traced = delivery.trace_lane(
        ("process:background-worker",), "tampering", report, None, flows, options
    )

    assert traced.rebuilt == "draft-contains"
    assert traced.archived_lead_rules == ()


def test_the_re_keyed_must_finds_are_counted_beside_the_rows():
    """The 2198870 Baseline cites 12 must-finds by element IDs the corpus re-keyed."""
    artifact = (
        REPO_ROOT
        / "evals/baselines/2198870-gpt-5.6-terra-6cd39a95/mstarks01-e5d71171.json"
    )

    assert _trace(artifact).re_keyed == 12


#: A must-find of the Baseline that a draft names at its place: the lane wrote
#: the target under another action, so the cause is ``verb``.
VERB_CASE = "12-overclaiming-supplier-portal"
VERB_INDEX = 17


def _verb_row(traced: delivery.Delivery) -> delivery.DeliveryRow:
    (row,) = [
        row
        for row in traced.rows
        if (row.reference.case, row.reference.reference_index)
        == (VERB_CASE, VERB_INDEX)
    ]
    assert row.reference.cause == "verb"
    return row


def _drop_zone(subject: str) -> Callable[[dict[str, Any]], None]:
    """Take ``subject``'s ``trust_zone`` out of a dumped model."""

    def change(model: dict[str, Any]) -> None:
        for collection in model.values():
            for element in collection if isinstance(collection, list) else ():
                if isinstance(element, dict) and element.get("id") == subject:
                    del element["trust_zone"]

    return change


def test_a_verb_row_with_its_place_facts_sent_reads_n_of_n(tmp_path):
    """The control of the negative fixture below: the same capture, unchanged."""
    row = _verb_row(_trace(_captured_sweep(tmp_path, {}, VERB_CASE)))

    assert row.place_facts.traces
    assert {fact.delivered for fact in row.place_facts.traces} == {"delivered"}
    count = len(row.place_facts.traces)
    assert row.place_facts.describe() == f"place facts delivered {count} of {count}"


def test_a_place_fact_taken_out_of_the_lane_input_is_lost_at_that_stage(tmp_path):
    """A draft names the target, and the lane was still not sent a fact there."""
    (tmp_path / "sent").mkdir()
    (tmp_path / "lost").mkdir()
    sent = _verb_row(_trace(_captured_sweep(tmp_path / "sent", {}, VERB_CASE)))
    (zone,) = [
        f for f in sent.place_facts.traces if f.predicate == "network-membership"
    ]

    lost = _verb_row(
        _trace(
            _captured_sweep(
                tmp_path / "lost", {}, VERB_CASE, change=_drop_zone(zone.subject)
            )
        )
    )

    (fact,) = [f for f in lost.place_facts.traces if f.fact_id == zone.fact_id]
    assert fact.delivered == "not delivered"
    assert fact.lost_at == "lane system model"
    assert fact.stages["projected model"].state == "present"
    others = [f for f in lost.place_facts.traces if f.fact_id != zone.fact_id]
    assert {f.delivered for f in others} <= {"delivered"}
    count = len(lost.place_facts.traces)
    assert lost.place_facts.describe() == (
        f"place facts delivered {count - 1} of {count} (lost: 1 at lane system model)"
    )


def test_the_catalog_stage_reads_the_replay_of_the_signed_reference(tmp_path):
    row = _verb_row(_trace(_captured_sweep(tmp_path, {}, VERB_CASE, signed=True)))

    assert row.place_facts.traces
    for fact in row.place_facts.traces:
        assert fact.stages["catalog"] == delivery.StageReading(
            "present", f"found: {fact.fact_id}"
        )
        assert fact.stages["raw extraction"].state == "unavailable"
        assert fact.delivered == "delivered"


@pytest.mark.parametrize(
    ("held", "value", "attribute"),
    [
        ("boundary:a", "boundary:a", "trust_zone"),
        ("boundary:b", "boundary:a", "trust_zone"),
        ("internal", "internet-facing", "exposure"),
        ("unknown", "TLS", "encryption_in_transit"),
        ("none; plain HTTP", "none", "encryption_in_transit"),
        ("HTTP/S", "encrypted", "encryption_in_transit"),
    ],
)
def test_a_field_reads_a_value_as_projection_compared_reads_it(held, value, attribute):
    """One rule: the scorer's comparison of a projected value with a field."""
    compared = PROJECTION_COMPARED[attribute]
    expected = "present" if compared(held) == compared(value) else "absent"

    assert delivery.field_reading(held, value, attribute).state == expected


@pytest.mark.parametrize("held", [None, 3])
def test_a_missing_or_non_text_field_is_absent(held):
    assert delivery.field_reading(held, "boundary:a", "trust_zone").state == "absent"


def _row(card, **changes):
    return replace(card.rows[0], **changes)


def test_the_control_is_a_matched_row_first_then_an_unstated_row():
    card = run.scorecard_of(BASELINE, REPO_ROOT / "evals/corpus", Ledger())
    miss = _row(card, reference_index=90, cause="verb")
    unstated = _row(
        card, reference_index=91, cause="unstated", compatibility="position-unstated"
    )
    re_keyed = _row(
        card, reference_index=93, cause="unstated", compatibility="re-keyed"
    )
    stages = {stage: True for stage in card.rows[0].stages}
    matched = _row(
        card, reference_index=92, cause=None, compatibility="compatible", stages=stages
    )
    elsewhere = replace(matched, case="another-case")

    assert delivery.matched_control(miss, [miss, unstated, matched]) == matched
    assert delivery.matched_control(miss, [miss, unstated]) == unstated
    assert delivery.matched_control(miss, [miss, elsewhere]) is None
    assert delivery.matched_control(miss, [miss, re_keyed]) is None


def test_a_row_with_no_control_and_no_place_fact_says_so(capsys):
    traced = _trace(BASELINE)
    delivery.render(traced)
    out = capsys.readouterr().out

    assert any(row.control is None for row in traced.rows)
    assert "    no control\n" in out
    assert "required facts: undefined" in out


def test_a_capture_with_no_shared_model_leaves_the_leads_traced(tmp_path):
    artifact = _captured_sweep(tmp_path, {})
    lanes = artifact.with_suffix(".reports") / f"{CAPTURED_CASE}.lanes.json"
    material = json.loads(lanes.read_text())
    del material["shared"]["system_model"]
    lanes.write_text(json.dumps(material))

    traced = _trace(artifact)

    (row,) = [row for row in traced.rows if row.reference.case == CAPTURED_CASE]
    assert CAPTURED_CASE not in traced.untraced
    assert row.lane.comparison is not None
    assert row.place_facts.traces
    for fact in row.place_facts.traces:
        model = fact.stages["lane system model"]
        assert model.state == "unavailable"
        assert "no shared system_model" in model.reason
        assert "system_model" not in fact.stages["lane evidence catalog"].reason


def _case_facts(report, shared, projections=None):
    return delivery.CaseFacts(
        reference=None,
        unread="",
        replay=None,
        elements={element.id: element for element in report.system_model.elements()},
        projections=projections or {},
        shared=shared,
        unshared="",
    )


def _table(*rows: tuple[str, str]) -> str:
    cells = "\n".join(f"| `{ref}` | {gloss} |" for ref, gloss in rows)
    return f"{len(rows)} facts.\n\n| cite this exactly | what it says |\n| --- | --- |\n{cells}\n"


@pytest.mark.parametrize(
    ("table", "state"),
    [
        (_table(("assertion:own", "a row")), "present"),
        (_table(("assertion:other", "a gloss that quotes `assertion:own`")), "absent"),
        (_table(("assertion:read", "the ID after the aliases")), "absent"),
    ],
)
def test_the_evidence_stage_reads_the_key_the_catalog_filed_the_row_under(
    report, table, state
):
    """The table keys the run's own row ID, and a gloss that quotes it is no key."""
    shared = SharedInput(elements={}, evidence_catalog=table, refused={})
    facts = _case_facts(report, shared)
    catalog = delivery.StageReading("present", "found: assertion:read")

    assert (
        delivery.lane_evidence_reading(catalog, "assertion:own", facts).state == state
    )


def test_the_replay_names_the_run_s_own_id_of_a_row_read_under_an_alias():
    """The evidence table keys the run's ID, never the ID after the aliases."""
    corpus = REPO_ROOT / "evals/corpus"
    (golden,) = [
        each for each in load_corpus(corpus) if each.id == "04-ml-inference-service"
    ]
    reference = signed_reference(corpus, golden)
    assert reference is not None
    alias, target = next(iter(reference.subject_aliases.items()))
    entry = next(row for row in reference.entries if row.subject == target.to)
    produced = entry.model_copy(update={"subject": alias})
    record = AssertionRecord(proposed=1, catalog=AssertionCatalog(entries=[produced]))

    replayed = replay_assertions(
        golden, reference, AssertionResult(golden.id, proposal={}, record=record)
    )

    (row,) = [row for row in replayed.rows if row.reference == assertion_id(entry)]
    assert row.produced == assertion_id(entry)
    assert row.produced_own == assertion_id(produced) != row.produced


def test_a_projection_the_signed_rows_leave_unsettled_is_unavailable(report, case):
    """Two values under one attribute project to unknown, so no field states one."""
    reference = signed_reference(REPO_ROOT / "evals/corpus", case)
    assert reference is not None
    entry = next(
        entry
        for entry in reference.entries
        if projected_attribute(entry.predicate, entry.subject)
    )
    attribute = projected_attribute(entry.predicate, entry.subject)
    unsettled = Projection(
        element_id=entry.subject,
        attribute=attribute,
        value="unknown",
        reason="several-values",
        rows=(),
    )
    facts = replace(
        _case_facts(report, None, {(entry.subject, attribute): unsettled}),
        reference=reference,
    )

    traced = delivery.trace_fact(entry, facts, None)

    for stage in delivery.GRAPH_STAGES:
        assert traced.stages[stage].state == "unavailable"
        assert "several-values" in traced.stages[stage].reason


def test_a_field_offered_reads_as_carrying_a_row_reads_present(report, case):
    """``assertions.offered`` leaves out a row the graph carries: that field holds it."""
    reference = signed_reference(REPO_ROOT / "evals/corpus", case)
    assert reference is not None
    model, _, _ = prepared_view(report.system_model, reference.catalog)
    carried = {assertion_id(row) for row in reference.entries} - {
        assertion_id(row) for row in offered(reference.catalog, model)
    }
    held = {element.id: element for element in model.elements()}
    checked = 0
    for projection in project(reference.catalog):
        element = held.get(projection.element_id)
        value = getattr(element, projection.attribute, None)
        if value != projection.value or not set(projection.rows) & carried:
            continue
        reading = delivery.field_reading(value, projection.value, projection.attribute)
        assert reading.state == "present"
        checked += 1
    assert checked
