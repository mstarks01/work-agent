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
                    "status": "rejected"
                    if claim.id == first.id
                    else claim.verdict.status,
                    "reason": "replayed",
                    **(
                        {"rejected_because": "evidence"} if claim.id == first.id else {}
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
