"""The re-ask replay injects one fault and rebuilds the re-ask.

Two claims are tested offline. The round's check names exactly the injected
fault, the problems ``EXPECTED`` gives the shape on the faulted ID, for each
shape. And the re-ask request rebuilt from an archive equals the one a scripted
graph run sent, instruction and user turn, byte for byte.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from types import SimpleNamespace
from typing import cast, get_args

import pytest

from analysis_service import graph
from analysis_service.claims import ProposedVerdict, RefusedControl, Ruling, UnknownRef
from analysis_service.critic import review_issues
from analysis_service.frameworks import PACKAGES
from analysis_service.markdown_loader import MarkdownLoader
from analysis_service.system_model import attribute_names
from evals.harness import critic_replay, modes, recritic_replay
from evals.harness.bundle import write_failures, write_reports
from evals.harness.modes import EvalRunError
from evals.harness.provenance import REPO_ROOT
from evals.harness.run_record import loaded
from tests import test_evals_run_grounds as grounds
from tests.factories import asvs_draft, valid_model
from tests.test_critic_review_replay import EVERY_LANE
from tests.test_evals_critic_replay import failed
from tests.test_evals_lane_replay import (  # noqa: F401  (fixtures)
    finished,
    record_requests,
    recorded,
)
from tests.test_evals_run_grounds import case  # noqa: F401  (fixture)

PACKAGE = PACKAGES["stride"]
#: The pipeline builder as the module defines it, before a sweep replaces it.
BUILD_EVAL_PIPELINE = modes.build_eval_pipeline
PACKAGE_LOADER = MarkdownLoader(REPO_ROOT / "frameworks" / "stride")
PROMPT_LOADER = MarkdownLoader(REPO_ROOT / "prompts")
SHAPES = get_args(recritic_replay.FaultShape)
#: The shapes a STRIDE first pass can carry: no STRIDE draft rules its unit out.
STRIDE_SHAPES = [shape for shape in SHAPES if shape != "confirmed-ruled-out"]

#: The sentence of each shape's first problem, as the round's check words it.
WORDS = {
    "row-unanswered": "unanswered: answer it in control_row or in refused_controls",
    "row-not-shown": "which the draft was not shown",
    "row-on-other-step": "names a control_row on a ruling not rejected for control",
    "control-row-missing": "is rejected for control and names no control_row",
    "control-row-not-shown": "which the draft was not shown",
    "dropped": "critic dropped draft",
    "invented": "which no lane agent drafted",
    "duplicate-id": "is used by 2 drafts",
    "verdict-shape": "is rejected for reasoning but also names open facts",
    "confirmed-ruled-out": "excludes its requirement and is ruled confirmed",
    "unresolved-unknown": "which element",
}


def faults_for(archived, refused_by=None) -> recritic_replay.FaultSet:
    """A fault set that refuses the lane row the Control shows each draft."""
    return recritic_replay.FaultSet(
        control=EVERY_LANE,
        refused={
            draft.id: [
                RefusedControl(
                    control=EVERY_LANE.id,
                    unit=PACKAGE.control_unit_of(draft),
                    reason="the audit log names no write to this element",
                )
            ]
            for draft in archived.drafts
            if refused_by is None or draft.id in refused_by
        },
        control_reasons={
            draft.id: "the audit log names every write to this element"
            for draft in archived.drafts
        },
    )


@pytest.fixture
def archived(recorded, case):  # noqa: F811
    out, _ = recorded
    return critic_replay.load(finished(out, case.id), case.id, "stride")


@pytest.fixture
def open_facts(monkeypatch, archived):
    """Give every archived ruling one open fact and a reason.

    The scripted sweep confirms each draft with no reason, and a
    ``verdict-shape`` or an ``unresolved-unknown`` fault needs a ruling that
    names an open fact and gives a reason.
    """
    element = next(iter(archived.model.elements()))
    fact = UnknownRef(element_id=element.id, attribute=attribute_names(element)[0])
    real = recritic_replay.rulings_from_report

    def with_a_fact(block, drafts):
        return [
            ruling.model_copy(
                update={
                    "verdict": ruling.verdict.model_copy(
                        update={"related_unknowns": [fact], "reason": "open"}
                    )
                }
            )
            for ruling in real(block, drafts)
        ]

    monkeypatch.setattr(recritic_replay, "rulings_from_report", with_a_fact)


def test_each_shape_states_its_problems():
    assert set(recritic_replay.EXPECTED) == set(SHAPES) == set(WORDS)


@pytest.mark.parametrize("shape", STRIDE_SHAPES)
@pytest.mark.usefixtures("open_facts")
def test_each_fault_gives_its_problems_on_its_draft(archived, shape):
    faults = faults_for(archived)

    for draft in archived.drafts[:2]:
        view = recritic_replay.fault_view(archived, faults, draft.id, shape)

        assert [(each.kind, each.claim_id) for each in view.reask.problems] == [
            (kind, view.faulted) for kind in recritic_replay.EXPECTED[shape]
        ]
        assert (view.faulted == draft.id) is (shape != "invented")
        assert WORDS[shape] in view.reask.problems[0].message
        implicated = [] if shape in {"invented", "duplicate-id"} else [draft.id]
        assert [item["id"] for item in view.reask.unreconciled] == implicated


def test_a_draft_that_keeps_its_unit_cannot_be_confirmed_ruled_out(archived):
    draft = archived.drafts[0].id

    with pytest.raises(EvalRunError, match="does not rule its unit out"):
        recritic_replay.fault_view(
            archived, faults_for(archived), draft, "confirmed-ruled-out"
        )


def test_a_confirmed_exclusion_is_the_one_fault_on_an_asvs_draft():
    """No case archive holds an ASVS first pass, so the draft is the check's own."""
    draft = asvs_draft(direction="excluded")
    ruled = Ruling(
        id=draft.id,
        verdict=ProposedVerdict(rejected_because="evidence", reason="not applicable"),
    )
    archived = cast(
        critic_replay.Archived, SimpleNamespace(drafts=[draft], model=valid_model())
    )
    faults = recritic_replay.FaultSet(
        control=EVERY_LANE, refused={}, control_reasons={}
    )

    faulty, faulted = recritic_replay.inject(
        archived, [ruled], faults, frozenset(), draft.id, "confirmed-ruled-out"
    )

    problems = review_issues([draft], faulty, valid_model(), {})
    assert [(each.kind, each.claim_id) for each in problems.problems] == [
        ("verdict-shape", faulted)
    ]
    assert WORDS["confirmed-ruled-out"] in problems.messages[0]


@pytest.mark.parametrize(
    "path",
    sorted((REPO_ROOT / "evals" / "experiments").glob("*/faults*.json")),
    ids=lambda path: f"{path.parent.name}/{path.name}",
)
def test_each_tracked_fault_set_loads(path):
    """A run script in the ledger passes its fault set to the command."""
    recritic_replay.read_faults(path)


def test_a_control_rejection_needs_the_reason_it_is_covered(archived):
    draft = archived.drafts[0].id
    faults = faults_for(archived).model_copy(update={"control_reasons": {}})

    with pytest.raises(EvalRunError, match="no reason"):
        recritic_replay.fault_view(archived, faults, draft, "control-row-missing")


@pytest.mark.parametrize("shape", STRIDE_SHAPES)
@pytest.mark.usefixtures("open_facts")
def test_each_fault_view_sends_the_graphs_re_ask(
    monkeypatch,
    archived,
    case,  # noqa: F811
    shape,
):
    """A graph run whose critic emits the faulted first pass sends this re-ask.

    The archive's own sweep put its pipeline in place of the builder, so the
    builder is restored before a second sweep builds its own.
    """
    draft = archived.drafts[0].id
    view = recritic_replay.fault_view(archived, faults_for(archived), draft, shape)
    monkeypatch.setattr(modes, "build_eval_pipeline", BUILD_EVAL_PIPELINE)
    monkeypatch.setattr(graph, "held_controls", lambda state: [EVERY_LANE])
    seen = record_requests(monkeypatch)

    grounds.sweep(
        monkeypatch,
        case,
        None,
        critic_first=[json.dumps({"claims": view.reask.emitted})],
    )

    instruction, turn = asyncio.run(
        recritic_replay.compose(archived, view.reask, PACKAGE_LOADER, PROMPT_LOADER)
    )
    sent = {text: contents for text, contents in seen}
    assert instruction in sent, "no node sent this re-ask instruction"
    assert sent[instruction] == [turn]


def test_a_first_pass_that_leaves_a_shown_row_unanswered_is_refused(archived):
    """The answered first pass is the clean side, so it must reconcile."""
    first = archived.drafts[0].id
    faults = faults_for(archived, refused_by={first})

    with pytest.raises(EvalRunError, match="does not reconcile"):
        recritic_replay.fault_view(archived, faults, first, "row-unanswered")


def answer(view, draft_id, **verdict) -> str:
    """The re-ask's answer: the faulted draft's clean ruling, with changes."""
    (ruling,) = (
        ruling
        for ruling in view.critic_round.canonical(view.reask.emitted)
        if ruling["id"] == draft_id
    )
    clean = view.clean
    refused = next(
        claim.verdict.refused_controls
        for claim in (*clean.claims, *clean.rejected)
        if claim.id == draft_id
    )
    ruling["verdict"] |= {
        "control_row": None,
        "refused_controls": [ref.model_dump(mode="json") for ref in refused],
        **verdict,
    }
    return json.dumps({"claims": [ruling]})


def test_a_repair_that_answers_the_row_leaves_no_problem(archived):
    draft = archived.drafts[0].id
    view = recritic_replay.fault_view(
        archived, faults_for(archived), draft, "row-not-shown"
    )

    result = recritic_replay.repair(view, answer(view, draft))

    assert (result["schema_error"], result["remaining"], result["unbriefed"]) == (
        None,
        [],
        [],
    )
    assert result["status"]["repaired"] == result["status"]["answered"]


def test_a_repair_that_leaves_the_row_unanswered_still_fails(archived):
    draft = archived.drafts[0].id
    view = recritic_replay.fault_view(
        archived, faults_for(archived), draft, "row-unanswered"
    )

    result = recritic_replay.repair(view, answer(view, draft, refused_controls=[]))

    assert [problem["kind"] for problem in result["remaining"]] == ["control-row"]
    assert result["status"]["repaired"] is None


def test_a_change_to_a_ruling_no_problem_named_is_unbriefed(archived):
    draft, other = (each.id for each in archived.drafts[:2])
    view = recritic_replay.fault_view(
        archived, faults_for(archived), draft, "row-on-other-step"
    )
    repaired = json.loads(answer(view, draft))["claims"]
    changed = json.loads(answer(view, other, reason="changed"))["claims"]

    result = recritic_replay.repair(view, json.dumps({"claims": repaired + changed}))

    assert [problem["kind"] for problem in result["unbriefed"]] == ["unbriefed-change"]
    assert result["remaining"] == []


def test_an_answer_outside_the_contract_is_kept_and_refused(archived):
    draft = archived.drafts[0].id
    view = recritic_replay.fault_view(
        archived, faults_for(archived), draft, "row-not-shown"
    )

    result = recritic_replay.repair(view, json.dumps({"threats": []}))

    assert "Extra inputs are not permitted" in result["schema_error"]
    assert result["answer"] == json.dumps({"threats": []})


@pytest.mark.parametrize("as_stored", [True, False])
def test_the_re_ask_request_is_rebuilt_byte_for_byte(
    monkeypatch,
    case,  # noqa: F811
    tmp_path,
    as_stored,
):
    """The critic drops the spoofing draft, so the graph sends the re-ask.

    The control reads the answer as sent rather than as ADK stores it, and
    the graph never sent that request.
    """
    dropped = json.dumps(
        {
            "claims": [
                grounds.scripted_ruling(case, category)
                for category in grounds.STRIDE_CATEGORIES
                if category != "spoofing"
            ]
        }
    )
    seen = record_requests(monkeypatch)
    run = grounds.sweep(monkeypatch, case, None, critic_first=[dropped])
    out = tmp_path / "artifact.json"
    write_reports(str(out), "analysis", run.runs)
    write_failures(str(out), run.failed_states)
    archived = critic_replay.load(failed(out, case.id), case.id, "stride")
    emitted = (
        recritic_replay.stored(dropped, "stride")
        if as_stored
        else json.loads(dropped)["claims"]
    )
    reask = critic_replay.round_of(archived, ()).first(emitted)

    instruction, turn = asyncio.run(
        recritic_replay.compose(archived, reask, PACKAGE_LOADER, PROMPT_LOADER)
    )

    sent = {text: contents for text, contents in seen}
    if not as_stored:
        assert instruction not in sent
        return
    assert instruction in sent, "no node sent this re-ask instruction"
    assert sent[instruction] == [turn]


def test_a_fault_set_that_names_no_draft_is_refused(archived):
    faults = faults_for(archived)
    misspelt = faults.model_copy(
        update={
            "refused": {**faults.refused, "X-99": faults.refused[archived.drafts[0].id]}
        }
    )

    with pytest.raises(EvalRunError, match="names no draft"):
        recritic_replay.fault_view(
            archived, misspelt, archived.drafts[0].id, "row-unanswered"
        )


def test_the_command_writes_the_repair_after_its_record(
    recorded,  # noqa: F811
    case,  # noqa: F811
    tmp_path,
    monkeypatch,
):
    """One scripted re-ask that repairs the fault, through the command."""
    out, _ = recorded
    artifact = finished(out, case.id)
    archived = critic_replay.load(artifact, case.id, "stride")
    draft = archived.drafts[0].id
    faults = tmp_path / "faults.json"
    faults.write_text(faults_for(archived).model_dump_json(), encoding="utf-8")
    view = recritic_replay.fault_view(
        archived, recritic_replay.read_faults(faults), draft, "row-not-shown"
    )

    def scripted(deployment, graph_node, schema, spent):
        async def call(instruction, turn):
            spent.append(0.01)
            return answer(view, draft)

        return call

    monkeypatch.setattr(recritic_replay, "node_call", scripted)
    monkeypatch.setattr(recritic_replay.Deployment, "from_env", lambda: None)
    written = tmp_path / "repair.json"
    args = argparse.Namespace(
        artifact=artifact.path,
        case=case.id,
        framework="stride",
        faults=faults,
        draft=draft,
        fault="row-not-shown",
        accept_cost="unknown",
        out=written,
    )

    assert recritic_replay.command_recritic_replay(args) == 0

    held = json.loads(written.read_text(encoding="utf-8"))
    assert held["record"]["command"] == "recritic-replay"
    assert [one["path"] for one in held["record"]["inputs"]] == sorted(
        str(path)
        for path in (
            artifact.path,
            faults,
            *archived.files,
            *loaded((PACKAGE_LOADER, PROMPT_LOADER)),
        )
    )
    assert (held["draft"], held["fault"]) == (draft, "row-not-shown")
    assert (held["remaining"], held["unbriefed"]) == ([], [])


def test_a_draft_id_that_names_no_draft_is_refused(archived):
    with pytest.raises(EvalRunError, match="no draft X-99"):
        recritic_replay.fault_view(
            archived, faults_for(archived), "X-99", "row-unanswered"
        )
