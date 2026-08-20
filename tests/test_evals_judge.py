"""The judge seam: its pinned config, its prompts, and its output handling.

No provider call happens here — :class:`PinnedJudge` takes its request function
by injection, so the request it builds and the way it treats the response are
both testable offline.
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
    RATIONALE_LIMIT,
    ClaimPair,
    ClaimRuling,
    JudgeConfigError,
    JudgeError,
    PinnedJudge,
    UnmatchedThreat,
    adjudication_payload,
    claim_payload,
    load_judge_config,
)
from stride_service.markdown_loader import MarkdownLoader
from stride_service.model_tiers import validate_model_string
from stride_service.resilience import load_resilience
from stride_service.vendors import vendor_for
from tests.factories import valid_model

EVALS_ROOT = Path(__file__).resolve().parents[1] / "evals"
JUDGE_PROMPTS = EVALS_ROOT / "prompts"

PAIR = ClaimPair(
    case="01-payments-checkout",
    category="spoofing",
    reference_claim="An attacker replays a stolen session cookie.",
    candidate_claim="An attacker reuses a stolen cookie to order as the shopper.",
    reference_element_ids=("flow:shopper-to-storefront-api:place-order",),
    candidate_element_ids=("flow:shopper-to-storefront-api:place-order",),
)


class FakeCompletion:
    """Records the request and replays a canned completion response.

    Shaped like litellm's ``ModelResponse``: a ``model`` naming the served build
    and a ``choices[0].message.content`` carrying the text.
    """

    def __init__(self, text: str, served: str | None = None) -> None:
        self.text = text
        self.served = served
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        message = type("Message", (), {"content": self.text})()
        choice = type("Choice", (), {"message": message})()
        return type("Response", (), {"model": self.served, "choices": [choice]})()


ENV = {
    "STRIDE_VERTEX_PROJECT": "p",
    "STRIDE_VERTEX_LOCATION": "us-central1",
    "GOOGLE_APPLICATION_CREDENTIALS": "/adc.json",
}


def judge(text: str, served: str | None = None) -> PinnedJudge:
    return PinnedJudge(
        load_judge_config(),
        request=FakeCompletion(text, served),
        prompts=MarkdownLoader(JUDGE_PROMPTS),
        env=ENV,
    )


def test_shipped_judge_config_is_pinned_and_versioned():
    config = load_judge_config()

    assert config.version >= 1
    assert config.temperature == 0.0
    # The judge names a (vendor, model) pair like any tier does — it is no
    # longer Google-only, and the pinned-form rule is the vendor's.
    validate_model_string(config.model, config.vendor, source="judge.model")


def test_a_candidate_judge_loads_on_every_supported_vendor(tmp_path):
    """The judge is not vendor-locked, and this is what says so.

    The shipped *value* is Vertex/Gemini and the *mechanism* has been
    vendor-neutral since the v3 cutover — but nothing asserted the second, so
    the file read as a coupling. Selecting a judge on measured agreement
    ([#116](https://github.com/mstarks01/work-agent/issues/116)) requires
    loading candidates from other families, so a regression here would block
    the calibration rather than show up as a bad number.

    Each pair also clears the load-time gates every tier clears: the pinned-form
    rule, greedy decoding under the supported-param check and the model-keyed
    temperature rules, and native structured output.

    OpenAI's candidate is its **base** reference, because greedy decoding and
    its reasoning family are mutually exclusive — see the test below. That is a
    capability difference the comparison has to state, not a gap in this test.
    """
    candidates = {
        "anthropic": "claude-sonnet-4-6",
        "openai": "gpt-4o",
        "vertex": "gemini-2.5-pro",
    }
    for vendor, model in candidates.items():
        path = tmp_path / f"judge-{vendor}.toml"
        path.write_text(
            f'version = 3\nvendor = "{vendor}"\nmodel = "{model}"\n'
            "temperature = 0.0\norder_seed = 20260721\n"
        )

        config = load_judge_config(path)

        assert (config.vendor, config.model) == (vendor, model)


def test_a_judge_requiring_greedy_decoding_refuses_an_o_series_model(tmp_path):
    """Not vendor bias — a capability difference, caught at load rather than mid-sweep.

    Greedy decoding is required of the judge so a re-run cannot flip a verdict,
    and o-series models constrain ``temperature`` to exactly ``1``. The pair is
    therefore unrepresentable, and it fails where a candidate is chosen instead
    of hours into a paid sweep.
    """
    path = tmp_path / "judge.toml"
    path.write_text(
        'version = 3\nvendor = "openai"\nmodel = "o3"\n'
        "temperature = 0.0\norder_seed = 20260721\n"
    )

    with pytest.raises(ValueError):
        load_judge_config(path)


def test_a_judge_on_a_model_newer_than_the_cost_map_is_refused(tmp_path):
    """The gap the first calibration sweep walked into, pinned so it stays shut.

    ``check_supported`` answers from LiteLLM's pinned cost map, so a model
    released after that copy falls through to the provider's base config and
    passes. ``gpt-5.6`` is that model today: it loaded as a candidate judge and
    the OpenAI API rejected pair one with "does not support 0.0 with this
    model", which is the mid-sweep surprise the load-time gate exists to
    prevent.

    A tier never had this gap — :func:`stride_service.binding.check_temperature`
    covered it — and the judge ran a strictly weaker gate than every tier until
    it called the same function.
    """
    path = tmp_path / "judge.toml"
    path.write_text(
        'version = 3\nvendor = "openai"\nmodel = "gpt-5.6"\n'
        "temperature = 0.0\norder_seed = 20260721\n"
    )

    with pytest.raises(ValueError, match="only at its default"):
        load_judge_config(path)


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
    # The judge prompt satisfies none of the shipped prompt lints and has no
    # business in the production image.
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


def test_an_over_long_rationale_is_clipped_rather_than_losing_the_sweep():
    """The pair that killed the first calibration run, and what it cost.

    The prompt asks for one sentence and a judge wrote two, so a 400-character
    cap refused the ruling and the sweep died on pair one of 339 — every paid
    call before it lost. A rationale is printed in a disagreement report and no
    metric reads it, so the bound is enforced by clipping.
    """
    long_rationale = "s" * (RATIONALE_LIMIT + 200)

    ruling = judge(json.dumps({"match": True, "rationale": long_rationale})).equivalent(
        PAIR
    )

    assert len(ruling.rationale) == RATIONALE_LIMIT


def test_an_empty_rationale_is_still_refused():
    """Clipping bounds a rationale; it does not accept the absence of one."""
    with pytest.raises(JudgeError):
        judge(json.dumps({"match": True, "rationale": ""})).equivalent(PAIR)


def test_unusable_judge_output_raises_rather_than_scoring():
    # Model output is untrusted input (OWASP LLM05): a malformed ruling fails
    # the run instead of silently counting as a non-match.
    with pytest.raises(JudgeError):
        judge('{"match": "yes"}').equivalent(PAIR)


def test_request_pins_the_route_temperature_and_schema():
    pinned = judge(json.dumps({"match": False, "rationale": "different action"}))
    pinned.equivalent(PAIR)
    call = pinned._request.calls[0]

    config = load_judge_config()
    # The route carries the vendor prefix, like every other call in the repo.
    assert call["model"] == vendor_for(config.vendor).route(config.model)
    assert call["temperature"] == 0.0
    assert issubclass(call["response_format"], BaseModel)

    # Claims ride as a user message, never concatenated into the system
    # instruction (OWASP LLM01).
    system, user = call["messages"]
    assert system["role"] == "system"
    assert user["role"] == "user"
    assert PAIR.reference_claim in json.loads(user["content"]).values()
    assert PAIR.reference_claim not in system["content"]


def test_credentials_come_from_the_vendor_registry_not_ambient_pickup():
    pinned = judge(json.dumps({"match": True, "rationale": "same"}))
    pinned.equivalent(PAIR)
    call = pinned._request.calls[0]

    # Vertex implies ADC; an API-key vendor would read its own scoped var.
    assert call["vertex_project"] == "p"
    assert "api_key" not in call


def test_a_response_with_no_choices_fails_as_an_unusable_ruling():
    # A refusal or filtered completion must name the judge, not surface as an
    # IndexError from inside the harness.
    empty = PinnedJudge(
        load_judge_config(),
        request=lambda **_: type("R", (), {"model": None, "choices": []})(),
        prompts=MarkdownLoader(JUDGE_PROMPTS),
        env=ENV,
    )
    with pytest.raises(JudgeError):
        empty.equivalent(PAIR)


def test_the_served_model_version_is_recorded_vendor_prefixed():
    """The configured string is a request, not an immutable build.

    Stable identifiers resolve to whichever build is current, so what actually
    answered is the only thing making one run's numbers comparable to the next.
    The prefix rides along for the same reason it does on a fingerprint: a bare
    served id does not say which vendor produced it.
    """
    served = "gemini-2.5-pro-2026-05-01"
    pinned = judge(json.dumps({"match": True, "rationale": "same"}), served)

    assert pinned.served_model_versions == ()
    pinned.equivalent(PAIR)

    assert pinned.served_model_versions == (f"vertex_ai/{served}",)


def test_the_call_carries_the_resilience_config():
    """The live judge retries and times out like the graph.

    A sweep that inherits never-retry, no-timeout defaults dies on one 429 after
    hours of work. ``attempts`` is a total, so the provider's retry count is one
    less — the same arithmetic the graph's binding uses.
    """
    resilience = load_resilience(
        Path(__file__).resolve().parents[1] / "config" / "resilience.toml", env={}
    )
    pinned = PinnedJudge(
        load_judge_config(),
        request=FakeCompletion(json.dumps({"match": True, "rationale": "same"})),
        prompts=MarkdownLoader(JUDGE_PROMPTS),
        resilience=resilience,
        env=ENV,
    )
    pinned.equivalent(PAIR)
    call = pinned._request.calls[0]

    assert call["num_retries"] == resilience.attempts - 1
    assert call["timeout"] == resilience.timeout_ms / 1000
