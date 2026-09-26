"""The lane replay rebuilds the request a lane made, and nothing else.

The one claim worth testing is fidelity: the instruction and the user turn the
replay composes from ``<case>.lanes.json`` are the ones the lane agent sent in
a real graph run. So a scripted sweep records every request the lanes make,
and the replay is held against those, byte for byte. Everything else here is a
refusal that keeps a replay from sending a request the lane never made.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from analysis_service.frameworks import LANE_CLOSING_DOC, PACKAGES
from analysis_service.graph import FrameworkNodes
from analysis_service.markdown_loader import MarkdownLoader
from analysis_service.prompts import lane_closing
from evals.harness import lane_replay
from evals.harness.bundle import reports_dir, write_reports
from evals.harness.modes import EvalRunError
from evals.harness.provenance import REPO_ROOT
from tests import test_evals_run_grounds as grounds
from tests.test_evals_run_grounds import case  # noqa: F401  (fixture)

PACKAGE_LOADER = MarkdownLoader(REPO_ROOT / "frameworks" / "stride")
PROMPT_LOADER = MarkdownLoader(REPO_ROOT / "prompts")


@pytest.fixture
def recorded(monkeypatch, case, tmp_path):  # noqa: F811
    """A scripted sweep's reports, and every request its model was sent."""
    seen: list[tuple[str, list]] = []
    real = grounds.QueuedLlm.generate_content_async

    async def recording(self, llm_request, stream=False):
        seen.append(
            (llm_request.config.system_instruction or "", list(llm_request.contents))
        )
        async for response in real(self, llm_request, stream):
            yield response

    monkeypatch.setattr(grounds.QueuedLlm, "generate_content_async", recording)
    run = grounds.sweep(monkeypatch, case, None)
    out = tmp_path / "artifact.json"
    write_reports(str(out), "analysis", run.runs)
    return out, seen


def test_every_lane_request_is_rebuilt_byte_for_byte(recorded, case):  # noqa: F811
    out, seen = recorded
    material = lane_replay.load_material(out, case.id)
    sent = {instruction: contents for instruction, contents in seen}

    for lane in FrameworkNodes("stride").lanes:
        instruction, turn = asyncio.run(
            lane_replay.compose(
                material, "stride", lane.lane, PACKAGE_LOADER, PROMPT_LOADER
            )
        )
        assert instruction in sent, f"{lane.lane}: no lane sent this instruction"
        assert sent[instruction] == [turn], f"{lane.lane}: another user turn"


def test_a_replay_parses_the_answer_under_the_package_schema(recorded, case):  # noqa: F811
    out, _ = recorded
    material = lane_replay.load_material(out, case.id)
    asked: list[str] = []

    async def call(instruction, turn):
        asked.append(instruction)
        return json.dumps({"claims": []})

    batch = asyncio.run(
        lane_replay.replay(
            material, "stride", "spoofing", call, PACKAGE_LOADER, PROMPT_LOADER
        )
    )

    assert batch.model_dump()["claims"] == []
    assert len(asked) == 1, "one lane, one call"


def test_an_appended_part_follows_the_captured_turn(recorded, case):  # noqa: F811
    out, _ = recorded
    material = lane_replay.load_material(out, case.id)
    _, captured = asyncio.run(
        lane_replay.compose(
            material, "stride", "spoofing", PACKAGE_LOADER, PROMPT_LOADER
        )
    )
    sent: list = []

    async def call(instruction, turn):
        sent.append(turn)
        return json.dumps({"claims": []})

    asyncio.run(
        lane_replay.replay(
            material,
            "stride",
            "spoofing",
            call,
            PACKAGE_LOADER,
            PROMPT_LOADER,
            "Address every lead.",
        )
    )

    assert sent[0].parts[:-1] == captured.parts
    assert sent[0].parts[-1].text == "Address every lead."


def test_an_answer_outside_the_schema_is_refused(recorded, case):  # noqa: F811
    out, _ = recorded
    material = lane_replay.load_material(out, case.id)

    async def call(instruction, turn):
        return json.dumps({"threats": []})

    with pytest.raises(ValueError):
        asyncio.run(
            lane_replay.replay(
                material, "stride", "spoofing", call, PACKAGE_LOADER, PROMPT_LOADER
            )
        )


def test_a_sweep_without_the_capture_is_refused_by_name(tmp_path):
    out = tmp_path / "artifact.json"
    reports_dir(out).mkdir()

    with pytest.raises(EvalRunError, match="predates the lane capture"):
        lane_replay.load_material(out, "01-payments-checkout")


def test_material_without_the_user_turn_is_refused(tmp_path):
    out = tmp_path / "artifact.json"
    reports_dir(out).mkdir()
    (reports_dir(out) / "c.lanes.json").write_text(
        json.dumps({"prepared": None, "shared": {}, "lanes": {}})
    )

    with pytest.raises(EvalRunError, match="no user turn"):
        lane_replay.load_material(out, "c")


def test_a_lane_the_package_does_not_have_is_refused_with_the_real_ones():
    with pytest.raises(EvalRunError, match="spoofing"):
        lane_replay.lane_of("stride", "phishing")


def test_the_fidelity_check_fails_on_material_the_lane_never_had(recorded, case):  # noqa: F811
    """The control for the byte-for-byte test: a changed lead is a new request."""
    out, seen = recorded
    material = lane_replay.load_material(out, case.id)
    material["lanes"]["stride"]["spoofing"]["candidates"] = "a lead nobody wrote"

    instruction, _ = asyncio.run(
        lane_replay.compose(
            material, "stride", "spoofing", PACKAGE_LOADER, PROMPT_LOADER
        )
    )

    assert instruction not in {sent for sent, _ in seen}


def test_a_thought_part_is_not_part_of_the_answer():
    """ADK leaves thoughts out of a node's output, so a replay must too."""
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    from evals.harness.node_call import answer_text

    response = LlmResponse(
        content=types.Content(
            role="model",
            parts=[
                types.Part(text="**Assessing security properties**", thought=True),
                types.Part(text='{"claims": []}'),
            ],
        )
    )

    assert answer_text(response) == '{"claims": []}'


def test_a_stride_lane_reads_its_closing_last(recorded):
    """ADR 0030's instruction is the last part of every STRIDE lane's user turn."""
    _, seen = recorded
    closing = lane_closing(PACKAGE_LOADER, "stride")
    lane_turns = [contents[-1] for _, contents in seen]
    stride_lanes = [turn for turn in lane_turns if turn.parts[-1].text == closing]

    # Two cases, six lanes each; the critic's request carries no closing.
    assert closing
    assert len(stride_lanes) == 2 * len(FrameworkNodes("stride").lanes)
    assert len(stride_lanes) < len(seen)


def test_the_closing_table_answers_for_every_package():
    assert set(LANE_CLOSING_DOC) == set(PACKAGES)


def test_fresh_leads_on_todays_run_are_the_leads_it_captured(recorded, case):  # noqa: F811
    """One reader: rebuilt with today's code, every lane's inputs are the captured ones."""
    out, _ = recorded
    material = lane_replay.load_material(out, case.id)

    for lane in FrameworkNodes("stride").lanes:
        rebuilt = lane_replay.fresh_leads(
            material, case, "stride", lane.lane, PACKAGE_LOADER
        )
        assert rebuilt["lanes"] == material["lanes"], lane.lane
