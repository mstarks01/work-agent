"""The shared schema re-ask (ADR 0058, #1410).

A node's answer that fails its output schema goes back to the same model once,
with the validation errors. These tests drive :class:`ExecutedLlm`, the one
path every node's model call takes, and one real graph run through it.
"""

from __future__ import annotations

import asyncio

from google.adk.agents import LlmAgent
from google.adk.models.llm_request import LlmRequest
from google.genai import types
from pydantic import BaseModel, Field

from analysis_service import graph
from analysis_service.budgets import retried_prompt_tokens
from analysis_service.provider import (
    REASK_INSTRUCTION,
    REASKS_METADATA_KEY,
    ExecutedLlm,
    GenerationRequest,
    GenerationResult,
)
from analysis_service.report import NodeRun, TokenUsage
from analysis_service.retry import ATTEMPTS_METADATA_KEY, RetryBudget, RetryPolicy
from tests.factories import (
    STRONG_MODEL,
    scripted_pipeline,
    scripted_usage,
)
from tests.test_execution import by_node, drive, happy_replies


class _Answer(BaseModel):
    title: str = Field(max_length=5)


VALID = '{"title": "ok"}'
TOO_LONG = '{"title": "far too long"}'


class _Replies:
    """An executor that answers each call with the next scripted text."""

    def __init__(self, *texts: str, charge: float | None = None) -> None:
        self.texts = list(texts)
        self.charge = charge
        self.seen: list[GenerationRequest] = []

    async def generate(self, request: GenerationRequest) -> list[GenerationResult]:
        self.seen.append(request)
        text = self.texts[len(self.seen) - 1]
        return [
            GenerationResult(
                content=types.Content(role="model", parts=[types.Part(text=text)]),
                served_model="served",
                usage=scripted_usage(),
                finish_reason="STOP",
                reported_charge_usd=self.charge,
                served_upstream=None,
                cache_write_tokens=None,
            )
        ]


def _policy() -> RetryPolicy:
    return RetryPolicy(attempts=3, budget=RetryBudget(capacity=10, ratio=0.1))


def _ask(executor: _Replies, schema: type[BaseModel] | None = _Answer):
    adapter = ExecutedLlm(model="x/y", executor=executor, retry_policy=_policy())
    request = LlmRequest(
        model="x/y",
        contents=[types.Content(role="user", parts=[types.Part(text="go")])],
        config=types.GenerateContentConfig(response_schema=schema),
    )

    return asyncio.run(_collected(adapter.generate_content_async(request, False)))


async def _collected(responses):
    return [response async for response in responses]


def test_a_valid_answer_is_not_reasked():
    executor = _Replies(VALID)

    (response,) = _ask(executor)

    assert len(executor.seen) == 1
    assert response.custom_metadata[REASKS_METADATA_KEY] == 0


def test_an_answer_that_breaks_the_schema_is_asked_again_once():
    executor = _Replies(TOO_LONG, VALID)

    (response,) = _ask(executor)

    assert len(executor.seen) == 2
    assert response.content.parts[0].text == VALID
    assert response.custom_metadata[REASKS_METADATA_KEY] == 1
    assert response.custom_metadata[ATTEMPTS_METADATA_KEY] == 2


def test_the_reask_carries_the_answer_and_the_errors():
    executor = _Replies(TOO_LONG, VALID)

    _ask(executor)

    _, answer, follow_up = executor.seen[1].contents
    assert answer.parts[0].text == TOO_LONG
    assert REASK_INSTRUCTION in follow_up.parts[0].text
    assert "- title:" in follow_up.parts[0].text
    # The input value is not echoed back.
    assert "far too long" not in follow_up.parts[0].text


def test_invalid_json_is_reasked_too():
    executor = _Replies("not json", VALID)

    (response,) = _ask(executor)

    assert response.custom_metadata[REASKS_METADATA_KEY] == 1


def test_a_second_failure_is_returned_for_adk_to_refuse():
    """Today's behaviour after one re-ask: ADK's validation ends the job."""
    executor = _Replies(TOO_LONG, TOO_LONG)

    (response,) = _ask(executor)

    assert len(executor.seen) == 2
    assert response.content.parts[0].text == TOO_LONG


def test_a_node_with_no_schema_is_not_checked():
    executor = _Replies("anything")

    _ask(executor, schema=None)

    assert len(executor.seen) == 1


def test_both_answering_calls_are_metered():
    one = scripted_usage()
    executor = _Replies(TOO_LONG, VALID, charge=0.25)

    (response,) = _ask(executor)

    usage = response.usage_metadata
    assert usage.prompt_token_count == 2 * one.prompt_token_count
    assert usage.candidates_token_count == 2 * one.candidates_token_count
    assert usage.total_token_count == 2 * one.total_token_count
    assert response.custom_metadata["reported_charge_usd"] == 0.5


def test_a_metered_reask_is_not_charged_again_as_a_retry():
    usage = TokenUsage(
        prompt_tokens=100,
        completion_tokens=10,
        total_tokens=110,
        cached_prompt_tokens=0,
        reasoning_tokens=0,
    )
    reasked = NodeRun(node="n", duration_ms=0, usage=usage, attempts=2, reasks=1)
    retried = NodeRun(node="n", duration_ms=0, usage=usage, attempts=2, reasks=0)

    assert retried_prompt_tokens(reasked) == 0
    assert retried_prompt_tokens(retried) == 100


def test_a_graph_run_survives_one_bad_lane_answer():
    """One STRIDE lane answers outside its schema, then inside it."""
    lane = graph.analyze_node_name("stride", "spoofing")
    replies = happy_replies()
    pipeline, _ = scripted_pipeline(replies)
    agents = {
        node.name: node
        for node in pipeline.workflow.graph.nodes
        if isinstance(node, LlmAgent)
    }
    executor = _Replies('{"claims": "not a list"}', replies[lane])
    agents[lane].model = ExecutedLlm(
        model=STRONG_MODEL, executor=executor, retry_policy=_policy()
    )

    run = drive(pipeline)

    assert graph.STATE_ANALYSIS in run.final_state
    assert by_node(run)[lane].reasks == 1
    assert by_node(run)[lane].attempts == 2
    assert len(executor.seen) == 2
