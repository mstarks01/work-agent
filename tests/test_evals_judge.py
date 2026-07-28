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
from stride_service.model_tiers import validate_model_string
from stride_service.resilience import load_resilience
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

    def __init__(self, text: str, model_version: str | None = None) -> None:
        self.models = self
        self.text = text
        self.model_version = model_version
        self.calls: list[dict] = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return type(
            "Response", (), {"text": self.text, "model_version": self.model_version}
        )()


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
    # The judge names a (vendor, model) pair like any tier does — it is no
    # longer Google-only, and the pinned-form rule is the vendor's.
    validate_model_string(config.model, config.vendor, source="judge.model")


def test_alias_judge_model_is_refused(tmp_path):
    path = tmp_path / "judge.toml"
    path.write_text(
        'version = 3\nvendor = "vertex"\n'
        'model = "gemini-2.5-pro-latest"\ntemperature = 0.0\n'
    )

    with pytest.raises(ValueError, match="latest"):
        load_judge_config(path)


def test_unknown_key_in_judge_config_fails_closed(tmp_path):
    path = tmp_path / "judge.toml"
    path.write_text(
        'version = 3\nvendor = "vertex"\nmodel = "gemini-2.5-pro"\n'
        'temperature = 0.0\ntier = "strong"\n'
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


def test_the_served_model_version_is_recorded():
    """Ticket 026: the configured string is a request, not an immutable build.

    Gemini 2.5+ stable identifiers resolve to whichever build is current, so
    what actually answered is the only thing that makes a run's numbers
    comparable to the next run's.
    """
    served = "gemini-2.5-pro-2026-05-01"
    client = FakeClient(json.dumps({"match": True, "rationale": "same"}), served)
    judge = VertexJudge(
        load_judge_config(), client=client, prompts=MarkdownLoader(JUDGE_PROMPTS)
    )

    assert judge.served_model_versions == ()
    judge.equivalent(PAIR)

    assert judge.served_model_versions == (served,)


def test_default_client_carries_the_resilience_config(monkeypatch):
    """Ticket 038: the live judge retries and times out like the graph.

    Without an injected client, the judge builds its own — and a sweep that
    inherits the SDK's never-retry, no-timeout defaults dies on one 429 after
    hours of work.
    """
    from google import genai

    captured: dict[str, object] = {}

    def fake_client(*, http_options=None):
        captured["http_options"] = http_options
        return FakeClient("{}")

    monkeypatch.setattr(genai, "Client", fake_client)
    resilience = load_resilience(
        Path(__file__).resolve().parents[1] / "config" / "resilience.toml", env={}
    )
    VertexJudge(
        load_judge_config(),
        prompts=MarkdownLoader(JUDGE_PROMPTS),
        resilience=resilience,
    )

    http_options = captured["http_options"]
    assert http_options.timeout == resilience.timeout_ms
    assert http_options.retry_options.attempts == resilience.attempts
