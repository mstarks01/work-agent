"""The archived-case critic replay rebuilds the request the critic made.

The claim worth testing is fidelity, as for the lane replay: a scripted sweep
records every request, and the critic request rebuilt from the archive is held
against the one the graph sent, instruction and user turn, byte for byte.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from analysis_service.assertions import AssertionCatalog, AssertionRecord
from analysis_service.graph import STATE_ASSERTION_CATALOG
from analysis_service.markdown_loader import MarkdownLoader
from evals.harness import critic_replay
from evals.harness.bundle import case_file, reports_dir, write_failures
from evals.harness.modes import EvalRunError
from evals.harness.provenance import REPO_ROOT
from tests import test_evals_run_grounds as grounds
from tests.eval_factories import write_emission_sweep
from tests.test_evals_lane_replay import (  # noqa: F401  (fixture)
    finished,
    record_requests,
    recorded,
)
from tests.test_evals_run_grounds import case  # noqa: F401  (fixture)

PACKAGE_LOADER = MarkdownLoader(REPO_ROOT / "frameworks" / "stride")
PROMPT_LOADER = MarkdownLoader(REPO_ROOT / "prompts")


def failed(out, case_id):
    """The artifact of a sweep whose one case failed."""
    return write_emission_sweep(out, "analysis", [case_id], failed=[case_id])


@pytest.mark.parametrize("kind", ["drafts", "lanes"])
def test_a_finished_case_with_a_lost_file_is_refused(recorded, case, kind):  # noqa: F811
    """The replay reads each file through finished_file, so none is optional."""
    out, _ = recorded
    case_file(out, case.id, kind).unlink()

    with pytest.raises(EvalRunError, match=f"holds no {kind} file"):
        critic_replay.load(finished(out, case.id), case.id, "stride")


def test_the_critic_request_is_rebuilt_byte_for_byte(recorded, case):  # noqa: F811
    out, seen = recorded
    archived = critic_replay.load(finished(out, case.id), case.id, "stride")

    instruction, turn = asyncio.run(
        critic_replay.compose(archived, PACKAGE_LOADER, PROMPT_LOADER)
    )

    sent = {text: contents for text, contents in seen}
    assert instruction in sent, "no node sent this critic instruction"
    assert sent[instruction] == [turn]


def test_changed_drafts_make_a_request_the_critic_never_saw(recorded, case):  # noqa: F811
    """The control for the byte-for-byte test."""
    out, seen = recorded
    archived = critic_replay.load(finished(out, case.id), case.id, "stride")
    archived.drafts.pop()

    instruction, _ = asyncio.run(
        critic_replay.compose(archived, PACKAGE_LOADER, PROMPT_LOADER)
    )

    assert instruction not in {text for text, _ in seen}


def test_a_replayed_answer_is_compared_draft_by_draft(recorded, case):  # noqa: F811
    out, _ = recorded
    archived = critic_replay.load(finished(out, case.id), case.id, "stride")
    block = archived.report.analyses[0]
    first = block.all_claims()[0]
    flipped = {
        "claims": [
            {
                "id": claim.id,
                "verdict": {
                    "reason": "replayed",
                    **(
                        {"rejected_because": "evidence"}
                        if claim.id == first.id
                        else claim.verdict.model_dump(
                            include={
                                "related_unknowns",
                                "immaterial_unknowns",
                                "rejected_because",
                            }
                        )
                    ),
                },
                "confidence": getattr(claim, "confidence", "medium"),
            }
            for claim in block.all_claims()
        ]
    }

    async def call(instruction, turn):
        return json.dumps(flipped)

    rulings = asyncio.run(
        critic_replay.replay(archived, call, PACKAGE_LOADER, PROMPT_LOADER)
    )
    rows = critic_replay.compare(archived, rulings)

    moved = [row for row in rows if row.archived != row.replayed]
    assert [row.draft_id for row in moved] == [first.id]
    assert moved[0].replayed == "rejected"


def test_an_answer_outside_the_contract_is_refused(recorded, case):  # noqa: F811
    out, _ = recorded
    archived = critic_replay.load(finished(out, case.id), case.id, "stride")

    async def call(instruction, turn):
        return json.dumps({"threats": []})

    with pytest.raises(ValueError, match="no 'claims'"):
        asyncio.run(critic_replay.replay(archived, call, PACKAGE_LOADER, PROMPT_LOADER))


def test_the_replayed_rulings_are_written_completed(recorded, case, tmp_path):  # noqa: F811
    """The file carries what a status cannot: the reason and the open facts.

    A ruling that names nothing on a draft citing an unknown ground is written
    with that ground in ``related_unknowns``, as the graph completes it.
    """
    out, _ = recorded
    archived = critic_replay.load(finished(out, case.id), case.id, "stride")
    bare = critic_replay.parse(
        json.dumps(
            {
                "claims": [
                    {
                        "id": claim.id,
                        "verdict": {},
                        "confidence": getattr(claim, "confidence", "medium"),
                    }
                    for claim in archived.drafts
                ]
            }
        ),
        "stride",
    )
    written = tmp_path / "rulings.json"

    critic_replay.write_rulings(written, archived, bare)

    claims = json.loads(written.read_text(encoding="utf-8"))["claims"]
    assert [claim["id"] for claim in claims] == [d.id for d in archived.drafts]
    conditional = [d.id for d in archived.drafts if d.unknown_grounds()]
    assert conditional, "the recorded case carries a draft citing an unknown"
    for claim in claims:
        assert bool(claim["verdict"]["related_unknowns"]) == (
            claim["id"] in conditional
        )


def test_a_failed_case_rebuilds_its_critic_request_from_the_dump(
    monkeypatch,
    case,  # noqa: F811
    tmp_path,
):
    """#1476: a case whose critic failed wrote no report, only its state dump,
    and the replay rebuilds the same request from that dump."""
    seen = record_requests(monkeypatch)
    silent_on_spoofing = json.dumps(
        {
            "claims": [
                grounds.scripted_ruling(case, category)
                for category in grounds.STRIDE_CATEGORIES
                if category != "spoofing"
            ]
        }
    )
    run = grounds.sweep(monkeypatch, case, None, critic_first=[silent_on_spoofing])
    out = tmp_path / "sweep.json"
    write_failures(str(out), run.failed_states)

    archived = critic_replay.load(failed(out, case.id), case.id, "stride")
    instruction, turn = asyncio.run(
        critic_replay.compose(archived, PACKAGE_LOADER, PROMPT_LOADER)
    )

    assert archived.report is None
    assert next(contents for text, contents in seen if text == instruction) == [turn]


def test_a_failure_dump_keeps_the_fact_catalog_the_drafts_cited(
    monkeypatch,
    case,  # noqa: F811
    tmp_path,
):
    """A dump from a run with the assertion pass holds its catalog, and the
    replay hands the critic view that catalog rather than none."""
    silent_on_spoofing = json.dumps(
        {
            "claims": [
                grounds.scripted_ruling(case, category)
                for category in grounds.STRIDE_CATEGORIES
                if category != "spoofing"
            ]
        }
    )
    run = grounds.sweep(monkeypatch, case, None, critic_first=[silent_on_spoofing])
    out = tmp_path / "sweep.json"
    write_failures(str(out), run.failed_states)
    dump = reports_dir(out) / f"{case.id}.failure.json"
    record = AssertionRecord(proposed=0, catalog=AssertionCatalog())
    written = json.loads(dump.read_text(encoding="utf-8"))
    written["state"][STATE_ASSERTION_CATALOG] = record.model_dump(mode="json")
    dump.write_text(json.dumps(written), encoding="utf-8")

    archived = critic_replay.load(failed(out, case.id), case.id, "stride")

    assert archived.assertions == record
