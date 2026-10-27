"""The instruction and the user turn one LLM node sends."""

from __future__ import annotations

import asyncio

from analysis_service.node_requests import node_instruction, node_request


def test_the_skill_text_comes_before_the_prompt_text():
    assert (
        node_instruction(" Know this. ", "\nDo this.\n") == "Know this.\n\nDo this.\n"
    )


def test_a_closing_is_the_last_part_of_the_user_turn():
    instruction, turn = asyncio.run(
        node_request("Read {key}.", {"key": "this"}, {"a": 1}, "Last words.")
    )

    assert instruction == "Read this."
    assert [part.text for part in turn.parts] == ['{"a": 1}', "Last words."]
