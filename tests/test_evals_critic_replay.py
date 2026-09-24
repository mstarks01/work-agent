"""The archived-case critic replay rebuilds the request the critic made.

The claim worth testing is fidelity, as for the lane replay: a scripted sweep
records every request, and the critic request rebuilt from the archive is held
against the one the graph sent, instruction and user turn, byte for byte.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from analysis_service.markdown_loader import MarkdownLoader
from evals.harness import critic_replay
from evals.harness.provenance import REPO_ROOT
from tests.test_evals_lane_replay import recorded  # noqa: F401  (fixture)
from tests.test_evals_run_grounds import case  # noqa: F401  (fixture)

PACKAGE_LOADER = MarkdownLoader(REPO_ROOT / "frameworks" / "stride")
PROMPT_LOADER = MarkdownLoader(REPO_ROOT / "prompts")


def test_the_critic_request_is_rebuilt_byte_for_byte(recorded, case):  # noqa: F811
    out, seen = recorded
    archived = critic_replay.load(out, case.id, "stride")

    instruction, turn = asyncio.run(
        critic_replay.compose(archived, PACKAGE_LOADER, PROMPT_LOADER)
    )

    sent = {text: contents for text, contents in seen}
    assert instruction in sent, "no node sent this critic instruction"
    assert sent[instruction] == [turn]


def test_changed_drafts_make_a_request_the_critic_never_saw(recorded, case):  # noqa: F811
    """The control for the byte-for-byte test."""
    out, seen = recorded
    archived = critic_replay.load(out, case.id, "stride")
    archived.drafts.pop()

    instruction, _ = asyncio.run(
        critic_replay.compose(archived, PACKAGE_LOADER, PROMPT_LOADER)
    )

    assert instruction not in {text for text, _ in seen}


def test_a_replayed_answer_is_compared_draft_by_draft(recorded, case):  # noqa: F811
    out, _ = recorded
    archived = critic_replay.load(out, case.id, "stride")
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
    archived = critic_replay.load(out, case.id, "stride")

    async def call(instruction, turn):
        return json.dumps({"threats": []})

    with pytest.raises(ValueError, match="no 'claims'"):
        asyncio.run(critic_replay.replay(archived, call, PACKAGE_LOADER, PROMPT_LOADER))


def test_the_job_wide_keys_render_from_the_report_as_prepare_captured(
    recorded,  # noqa: F811
    case,  # noqa: F811
):
    """A sweep with no lane capture gets the keys a capture holds."""
    out, _ = recorded
    captured = critic_replay.load(out, case.id, "stride")

    assert critic_replay.shared_keys(captured.report) == {
        name: captured.shared[name]
        for name in critic_replay.shared_keys(captured.report)
    }


def test_a_sweep_with_no_lane_capture_rebuilds_the_same_request(recorded, case):  # noqa: F811
    out, seen = recorded
    (out.parent / f"{out.stem}.reports" / f"{case.id}.lanes.json").unlink()
    archived = critic_replay.load(out, case.id, "stride")

    instruction, turn = asyncio.run(
        critic_replay.compose(archived, PACKAGE_LOADER, PROMPT_LOADER)
    )

    assert dict(seen)[instruction] == [turn]


def test_the_replayed_rulings_are_written_completed(recorded, case, tmp_path):  # noqa: F811
    """The file carries what a status cannot: the reason and the open facts.

    A ruling that names nothing on a draft citing an unknown ground is written
    with that ground in ``related_unknowns``, as the graph completes it.
    """
    out, _ = recorded
    archived = critic_replay.load(out, case.id, "stride")
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
