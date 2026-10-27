"""The near-miss ballot: which rows it asks, and how a returned answer is recorded."""

from __future__ import annotations

import csv
import json
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from analysis_service.system_model import SystemModel, normalize_element_ids
from evals.harness import near_misses
from evals.harness.losses import CaseLosses, Loss
from evals.harness.reference import (
    RULINGS_FILE,
    CorpusError,
    Reading,
    flows_by_case,
    load_case,
)
from tests.eval_factories import draft_threat

CORPUS_DIR = Path(__file__).resolve().parents[1] / "evals" / "corpus"
CASE = "02-iot-fleet-telemetry"


@pytest.fixture
def corpus(tmp_path):
    shutil.copytree(CORPUS_DIR / CASE, tmp_path / CASE)
    return tmp_path


def _ballot(case, loss, draft, matched=(), analysed=None):
    score = SimpleNamespace(
        case_id=case.id,
        matched=[SimpleNamespace(threat_id=threat_id) for threat_id in matched],
    )
    return near_misses.rows(
        [case],
        [score],
        [CaseLosses(case=case.id, losses=(loss,))],
        {case.id: [draft]},
        flows_by_case([case], {case.id: analysed} if analysed else {}),
        source="sweep",
    )


def _verb_loss(index, draft_id="T-01"):
    return Loss(
        reference_index=index,
        lane="tampering",
        must_find=True,
        cause="verb",
        reference_verb="plant",
        draft_id=draft_id,
        draft_verb="alter",
    )


def _tampering_draft(case, index):
    reference = case.stride_claims()[index]
    return draft_threat(
        1,
        "tampering",
        "An image in the bucket is replaced",
        element_ids=reference.affected_element_ids,
        verb="alter",
    )


def test_a_free_same_lane_draft_beside_a_miss_is_asked():
    case = load_case(CORPUS_DIR / CASE)
    ballot = _ballot(case, _verb_loss(3), _tampering_draft(case, 3))
    assert [(row["ref"], row["draft_id"], row["differs_by"]) for row in ballot] == [
        ("3", "T-01", "verb: alter vs plant")
    ]


def test_a_draft_that_matched_its_own_reference_is_not_asked():
    case = load_case(CORPUS_DIR / CASE)
    ballot = _ballot(case, _verb_loss(3), _tampering_draft(case, 3), matched=("T-01",))
    assert ballot == []


def test_a_draft_a_person_voted_down_is_not_asked():
    case = load_case(CORPUS_DIR / CASE)
    loss = Loss(
        reference_index=3,
        lane="tampering",
        must_find=True,
        cause="place",
        reference_verb="plant",
        displaced_draft_id="T-01",
        displaced_status="rejected",
    )
    assert _ballot(case, loss, _tampering_draft(case, 3)) == []


def test_a_reading_already_ruled_is_not_asked_again():
    case = load_case(CORPUS_DIR / CASE)
    ruled = case.ruled_readings[5][0]
    draft = draft_threat(
        1,
        "repudiation",
        "Readings cannot be attributed",
        element_ids=ruled.affected_element_ids,
        verb=ruled.verb,
    )
    loss = Loss(
        reference_index=5,
        lane="repudiation",
        must_find=True,
        cause="place",
        reference_verb="unattributable",
        displaced_draft_id=draft.id,
        displaced_status="pooled",
    )
    assert _ballot(case, loss, draft) == []


def test_a_ruled_reading_of_one_action_is_not_asked_again():
    """``forge`` and ``inject`` are one action to the matcher and the vote key
    (:data:`~evals.harness.verbs.EQUIVALENT`), so a ruling on one is a ruling
    on the other, and a place reads in its endpoint form."""
    case = load_case(CORPUS_DIR / CASE)
    place = case.stride_claims()[3].affected_element_ids
    ruled = replace(
        case,
        ruled_readings={3: (Reading(verb="forge", affected_element_ids=place),)},
    )
    draft = draft_threat(
        1,
        "tampering",
        "A forged image is planted",
        element_ids=tuple(reversed(place)),
        verb="inject",
    )
    loss = Loss(
        reference_index=3,
        lane="tampering",
        must_find=True,
        cause="place",
        reference_verb="plant",
        displaced_draft_id=draft.id,
        displaced_status="pooled",
    )

    assert _ballot(case, loss, draft)
    assert _ballot(ruled, loss, draft) == []


def _place_loss(case, index, draft):
    return Loss(
        reference_index=index,
        lane=case.stride_claims()[index].lane,
        must_find=True,
        cause="place",
        reference_verb=case.stride_claims()[index].verb,
        displaced_draft_id=draft.id,
        displaced_status="pooled",
    )


def test_a_reading_ruled_on_a_flow_the_extraction_renamed_is_not_asked_again():
    """The draft cites the extraction's name for the flow the ruling cites, and
    the ballot resolves it through the scorer's map (``flows_by_case``)."""
    case = load_case(CORPUS_DIR / CASE)
    flow = case.model.data_flows[0]
    raw = case.model.model_dump(mode="json")
    raw["data_flows"][0]["name"] = f"{raw['data_flows'][0]['name']} differently put"
    analysed = normalize_element_ids(SystemModel.model_validate(raw))
    renamed = next(
        other.id
        for other in analysed.data_flows
        if (other.source, other.destination) == (flow.source, flow.destination)
    )
    assert renamed != flow.id
    ruled = replace(
        case,
        ruled_readings={3: (Reading(verb="alter", affected_element_ids=(flow.id,)),)},
    )
    draft = draft_threat(
        1, "tampering", "A record is changed", element_ids=(renamed,), verb="alter"
    )
    loss = _place_loss(ruled, 3, draft)

    assert _ballot(ruled, loss, draft)
    assert _ballot(ruled, loss, draft, analysed=analysed) == []


def test_a_reading_ruled_on_one_sibling_flow_does_not_answer_the_other():
    """ADR 0061: two flows between one pair of elements are two places, so a
    ruling on the REST requests does not answer a draft on the live status."""
    case = load_case(CORPUS_DIR / "13-dispatch-control-plane")
    requests = "flow:process:dispatch-console>process:dispatch-api>dispatch-requests"
    status = "flow:process:dispatch-console>process:dispatch-api>live-job-status"
    lane = case.stride_claims()[0].lane
    verb = case.stride_claims()[0].verb
    draft = draft_threat(
        1, lane, "The status feed is read", element_ids=(status,), verb=verb
    )
    loss = _place_loss(case, 0, draft)

    on_requests = replace(
        case,
        refused_readings={0: (Reading(verb=verb, affected_element_ids=(requests,)),)},
    )
    on_status = replace(
        case,
        refused_readings={0: (Reading(verb=verb, affected_element_ids=(status,)),)},
    )

    assert _ballot(on_requests, loss, draft)
    assert _ballot(on_status, loss, draft) == []


def _write(path, rows, columns):
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _issue(tmp_path, case, draft):
    ballot = _ballot(case, _verb_loss(3), draft)
    issued = tmp_path / "ballot.csv"
    near_misses.write(ballot, issued)
    return issued


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        ({"n": "1", "verdict": "maybe", "note": "x"}, "is not one of"),
        ({"n": "1", "verdict": "same", "note": " "}, "add a note"),
        ({"n": "1", "verdict": "different", "note": ""}, "add a note"),
        ({"n": "2", "verdict": "same", "note": "x"}, "do not match"),
        (
            {"n": "1", "verdict": "same", "note": "x", "draft_verb": "plant"},
            "draft_verb changed",
        ),
    ],
    ids=["verdict", "same-note", "different-note", "rows", "carried"],
)
def test_a_returned_ballot_that_does_not_fit_is_refused(tmp_path, answer, message):
    case = load_case(CORPUS_DIR / CASE)
    issued = _issue(tmp_path, case, _tampering_draft(case, 3))
    returned = tmp_path / "returned.csv"
    _write(returned, [answer], list(answer))
    with pytest.raises(ValueError, match=message):
        near_misses.returned_rows(issued, returned)


def test_a_returned_row_short_of_a_cell_is_refused(tmp_path):
    """``csv`` fills a missing cell with ``None``, which no answer can be."""
    case = load_case(CORPUS_DIR / CASE)
    issued = _issue(tmp_path, case, _tampering_draft(case, 3))
    returned = tmp_path / "returned.csv"
    returned.write_text("n,verdict,note\n1,same\n", encoding="utf-8")
    with pytest.raises(ValueError, match="one cell per column"):
        near_misses.returned_rows(issued, returned)


@pytest.mark.parametrize("verdict", ["same", "different"])
def test_a_ruled_row_is_written_once_and_loads(tmp_path, corpus, verdict):
    case = load_case(corpus / CASE)
    issued = _issue(tmp_path, case, _tampering_draft(case, 3))
    returned = tmp_path / "returned.csv"
    _write(
        returned,
        [{"n": "1", "verdict": verdict, "note": "why"}],
        ["n", "verdict", "note"],
    )
    answered = near_misses.returned_rows(issued, returned)

    assert near_misses.record(answered, corpus, "reader", "ballot") == 1
    assert near_misses.record(answered, corpus, "reader", "ballot") == 0

    reloaded = load_case(corpus / CASE)
    field = "ruled_readings" if verdict == "same" else "refused_readings"
    assert "alter" in [r.verb for r in getattr(reloaded, field)[3]]


def test_an_unsure_row_records_nothing(tmp_path, corpus):
    case = load_case(corpus / CASE)
    before = (corpus / CASE / RULINGS_FILE).read_bytes()
    issued = _issue(tmp_path, case, _tampering_draft(case, 3))
    returned = tmp_path / "returned.csv"
    _write(
        returned,
        [{"n": "1", "verdict": "unsure", "note": ""}],
        ["n", "verdict", "note"],
    )

    near_misses.record(near_misses.returned_rows(issued, returned), corpus, "r", "b")

    assert (corpus / CASE / RULINGS_FILE).read_bytes() == before


def test_a_ruling_that_does_not_load_leaves_the_file_as_it_was(tmp_path, corpus):
    """An accepted reading must use a verb its lane admits; the write is undone."""
    case = load_case(corpus / CASE)
    draft = draft_threat(
        1,
        "tampering",
        "Filed with a verb tampering does not take",
        element_ids=case.stride_claims()[3].affected_element_ids,
        verb="flood",
    )
    issued = _issue(tmp_path, case, draft)
    returned = tmp_path / "returned.csv"
    _write(
        returned,
        [{"n": "1", "verdict": "same", "note": "why"}],
        ["n", "verdict", "note"],
    )
    before = (corpus / CASE / RULINGS_FILE).read_bytes()

    with pytest.raises(CorpusError, match="is not a tampering verb"):
        near_misses.record(
            near_misses.returned_rows(issued, returned), corpus, "r", "b"
        )

    assert (corpus / CASE / RULINGS_FILE).read_bytes() == before


def test_a_refused_reading_may_carry_a_verb_its_lane_does_not_take(corpus):
    path = corpus / CASE / RULINGS_FILE
    data = json.loads(path.read_text())
    entry = dict(data["stride"][0])
    entry["reading"] = {**entry["reading"], "verb": "flood"}
    entry["verdict"] = "different"
    data["stride"].append(entry)
    path.write_text(json.dumps(data))

    case = load_case(corpus / CASE)

    assert [r.verb for r in case.refused_readings[5]] == ["flood"]


def _edit_claims(corpus, edit):
    path = corpus / CASE / "claims" / "stride.json"
    claims = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(edit(claims), indent=2), encoding="utf-8")


def _moved(claims):
    """Claim 2 removed, so index 3 now names the claim after it."""
    return claims[:2] + claims[3:]


def _reworded(claims):
    claims[3]["claim"] += " Reworded."
    return claims


def _removed(claims):
    """Index 3 names no claim; the claims the case's rulings name stay."""
    return [claims[3], claims[5]]


@pytest.mark.parametrize("edit", [_moved, _reworded, _removed])
def test_a_ruling_on_a_claim_that_changed_since_the_ballot_is_refused(
    tmp_path, corpus, edit
):
    """The reader ruled on the claim the ballot showed, not the one at its index now."""
    case = load_case(corpus / CASE)
    issued = _issue(tmp_path, case, _tampering_draft(case, 3))
    returned = tmp_path / "returned.csv"
    _write(
        returned,
        [{"n": "1", "verdict": "same", "note": "why"}],
        ["n", "verdict", "note"],
    )
    answered = near_misses.returned_rows(issued, returned)
    _edit_claims(corpus, edit)
    before = (corpus / CASE / RULINGS_FILE).read_bytes()

    with pytest.raises(ValueError, match="row 1: reference claim 3"):
        near_misses.record(answered, corpus, "reader", "ballot")

    assert (corpus / CASE / RULINGS_FILE).read_bytes() == before


@pytest.mark.parametrize("reviewed_by", [" ", "\t"])
def test_a_ruling_signed_by_nobody_is_refused(tmp_path, corpus, reviewed_by):
    case = load_case(corpus / CASE)
    issued = _issue(tmp_path, case, _tampering_draft(case, 3))
    returned = tmp_path / "returned.csv"
    _write(
        returned,
        [{"n": "1", "verdict": "same", "note": "why"}],
        ["n", "verdict", "note"],
    )
    before = (corpus / CASE / RULINGS_FILE).read_bytes()

    with pytest.raises(CorpusError, match="names nobody"):
        near_misses.record(
            near_misses.returned_rows(issued, returned), corpus, reviewed_by, "b"
        )

    assert (corpus / CASE / RULINGS_FILE).read_bytes() == before


def test_any_fault_after_a_write_leaves_the_file_as_it_was(
    tmp_path, corpus, monkeypatch
):
    """The restore runs for every exception, not only for a refused ruling."""
    case = load_case(corpus / CASE)
    issued = _issue(tmp_path, case, _tampering_draft(case, 3))
    returned = tmp_path / "returned.csv"
    _write(
        returned,
        [{"n": "1", "verdict": "same", "note": "why"}],
        ["n", "verdict", "note"],
    )
    answered = near_misses.returned_rows(issued, returned)
    before = (corpus / CASE / RULINGS_FILE).read_bytes()

    calls = []

    def fault_on_the_reload(case_dir):
        calls.append(case_dir)
        if len(calls) > 1:
            raise OSError("the disk failed")
        return load_case(case_dir)

    monkeypatch.setattr(near_misses, "load_case", fault_on_the_reload)

    with pytest.raises(OSError, match="the disk failed"):
        near_misses.record(answered, corpus, "reader", "ballot")

    assert (corpus / CASE / RULINGS_FILE).read_bytes() == before
