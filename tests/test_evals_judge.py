"""The judge seam: its pinned config, its prompts, and its output handling.

No Vertex call happens here — :class:`VertexJudge` takes its client by
injection, so the request it builds and the way it treats the response are both
testable offline.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest
from pydantic import BaseModel

from evals.harness.judge import (
    ADJUDICATION_PROMPT_NAME,
    CLAIM_PROMPT_NAME,
    ClaimPair,
    ClaimRuling,
    JudgeConfigError,
    JudgeError,
    UnmatchedThreat,
    VertexJudge,
    adjudication_payload,
    claim_payload,
    load_judge_config,
)
from stride_service.markdown_loader import MarkdownLoader
from tests.factories import valid_model

EVALS_ROOT = Path(__file__).resolve().parents[1] / "evals"
JUDGE_PROMPTS = EVALS_ROOT / "prompts"

PAIR = ClaimPair(
    case="01-payments-checkout",
    category="spoofing",
    reference_claim="An attacker replays a stolen session cookie.",
    candidate_claim="An attacker reuses a stolen cookie to order as the shopper.",
)


class FakeClient:
    """Records the request and replays a canned response."""

    def __init__(self, text: str) -> None:
        self.models = self
        self.text = text
        self.calls: list[dict] = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return type("Response", (), {"text": self.text})()


def judge(text: str) -> VertexJudge:
    return VertexJudge(
        load_judge_config(),
        client=FakeClient(text),
        prompts=MarkdownLoader(JUDGE_PROMPTS),
    )


def test_shipped_judge_config_is_pinned_and_versioned():
    config = load_judge_config()

    assert config.version >= 1
    assert config.temperature == 0.0
    # Ticket 007's pinned-suffix rule, reused: an alias is not reproducible,
    # and a judge that moves silently re-scores history.
    assert config.model.endswith("-002")


def test_alias_judge_model_is_refused(tmp_path):
    path = tmp_path / "judge.toml"
    path.write_text('version = 1\nmodel = "gemini-2.5-pro-latest"\ntemperature = 0.0\n')

    with pytest.raises(ValueError, match="latest"):
        load_judge_config(path)


def test_unknown_key_in_judge_config_fails_closed(tmp_path):
    path = tmp_path / "judge.toml"
    path.write_text(
        'version = 1\nmodel = "gemini-2.5-pro-002"\ntemperature = 0.0\ntier = "pro"\n'
    )

    with pytest.raises(JudgeConfigError):
        load_judge_config(path)


def test_missing_judge_config_fails_closed(tmp_path):
    with pytest.raises(JudgeConfigError, match="cannot be read"):
        load_judge_config(tmp_path / "absent.toml")


def test_both_judge_prompts_load():
    loader = MarkdownLoader(JUDGE_PROMPTS)

    for name in (CLAIM_PROMPT_NAME, ADJUDICATION_PROMPT_NAME):
        assert loader.load(name).strip()


def test_judge_prompts_stay_out_of_the_shipped_prompt_tree():
    # Ticket 009 decision 14: the judge prompt satisfies none of ticket 020's
    # lints and has no business in the production image.
    shipped = MarkdownLoader(Path(__file__).resolve().parents[1] / "prompts").names()

    assert not any(name.startswith("judge") for name in shipped)


def test_claim_payload_randomizes_presentation_order():
    seen = {
        tuple(claim_payload(PAIR, rng=random.Random(seed)).values())
        for seed in range(20)
    }

    assert len(seen) == 2  # both orderings occur; position carries no meaning
    for entry in seen:
        assert set(entry[1:]) == {PAIR.reference_claim, PAIR.candidate_claim}


def test_claim_payload_carries_the_lane():
    payload = claim_payload(PAIR, rng=random.Random(0))

    assert payload["stride_category"] == "spoofing"


def test_adjudication_payload_carries_the_grounding_facts():
    model = valid_model()
    threat = UnmatchedThreat(
        threat_id="S-01",
        category="spoofing",
        claim="An attacker does a thing.",
        description="Longer prose.",
        affected_element_ids=("process:web-app",),
    )

    payload = adjudication_payload(threat, model, ("another claim",))

    assert payload["system_model"]["processes"][0]["id"] == "process:web-app"
    assert payload["boundary_crossings"], "crossings ground the judge's ruling"
    assert payload["other_reported_claims"] == ["another claim"]


def test_ruling_is_validated_before_it_reaches_a_metric():
    ruling = judge(json.dumps({"match": True, "rationale": "same action"})).equivalent(
        PAIR
    )

    assert isinstance(ruling, ClaimRuling)
    assert ruling.match is True


def test_unusable_judge_output_raises_rather_than_scoring():
    # Model output is untrusted input (OWASP LLM05): a malformed ruling fails
    # the run instead of silently counting as a non-match.
    with pytest.raises(JudgeError):
        judge('{"match": "yes"}').equivalent(PAIR)


def test_request_pins_model_temperature_and_schema():
    vertex = judge(json.dumps({"match": False, "rationale": "different action"}))
    vertex.equivalent(PAIR)
    call = vertex._client.calls[0]

    assert call["model"] == load_judge_config().model
    assert call["config"].temperature == 0.0
    assert issubclass(call["config"].response_schema, BaseModel)
    # Claims ride as JSON data, never concatenated into the instruction
    # (OWASP LLM01).
    assert PAIR.reference_claim in json.loads(call["contents"]).values()
    assert PAIR.reference_claim not in call["config"].system_instruction
