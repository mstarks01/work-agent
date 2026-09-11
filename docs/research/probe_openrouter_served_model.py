"""Measure what an OpenRouter response names in ``model`` (#806).

`tests/test_identity.py` already drives the pinned translator and shows that
litellm's OpenRouter transformation fills ``model_response.model`` from the
response body's ``model``. That is the offline half, and it is settled. What
nobody has measured is what OpenRouter puts in that field: the upstream build
that answered, or the slug the request named. The answer decides whether the
``openrouter`` row keeps ``served_trust = "provider_reported"`` or moves to
``"requested_echo"``.

It is a claim about a third party, so only a live call settles it. Run this
from the repository root with a key in ``ANALYSIS_OPENROUTER_API_KEY``::

    uv run python docs/research/probe_openrouter_served_model.py

Each repetition is one billed completion of a single output token. The default
slugs cost well under a cent for the whole run; ``--slug`` takes any other.

Three legs, and each one answers something the others cannot:

1. **The body.** One POST per repetition, printing every top-level field the
   response carries except the message content — ``model`` beside the
   requested slug, and whatever else OpenRouter states about the route.
2. **The witness.** ``GET /generation`` on the returned id, which names the
   upstream provider that served. Without it a constant ``model`` proves
   nothing, because two calls may have reached the same provider anyway.
3. **The translator.** litellm's own OpenRouter transformation, run over the
   body leg 1 recorded. This costs no second call and shows what the served
   half of an **Execution Identity** would actually carry.

Leg 2 is why the second default slug is one OpenRouter fronts with more than
one provider. A ``model`` that stays constant while the witness names two
different providers is an echo, whatever else it looks like.

This is a probe, not a gate. It asserts nothing and fails no build.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any

import httpx

from analysis_service.vendors import CredentialMode, ProviderAuthError, vendor_for

#: The base litellm's pinned ``main.py`` falls back to for this provider. It is
#: a literal there rather than a constant, so it is repeated rather than read.
API_BASE = "https://openrouter.ai/api/v1"

#: One upstream provider, and a family whose canonical identifier carries a
#: dated build. If OpenRouter passed the upstream name through anywhere, it
#: would be visible here as a value the request never sent.
SINGLE_PROVIDER_SLUG = "anthropic/claude-opus-4.7"

#: Many upstream providers behind one slug. This is the leg that decides it.
MULTI_PROVIDER_SLUG = "meta-llama/llama-3.3-70b-instruct"

#: Fields printed from the completion body. Everything except the content.
BODY_FIELDS = ("model", "provider", "id", "object", "created")

TIMEOUT = httpx.Timeout(60.0)


def _api_key() -> str:
    """The key, read through the shipped registry rather than from os.environ here.

    One rule, one reader: which variable holds this vendor's key is the
    registry's answer, and a probe that spelled the name itself would be a
    second reader of it.
    """
    kwargs = vendor_for("openrouter").credential_kwargs(
        os.environ, CredentialMode.API_KEY
    )
    return kwargs["api_key"]


def _redacted(text: str, key: str) -> str:
    """Provider text with the key removed, for the one path that prints a body."""
    return text.replace(key, "<redacted>")


def _complete(client: httpx.Client, key: str, slug: str) -> dict[str, Any]:
    """One billed completion of a single token, returning the parsed body."""
    response = client.post(
        f"{API_BASE}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": slug,
            "messages": [{"role": "user", "content": "Say ok."}],
            "max_tokens": 1,
        },
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"{slug}: HTTP {response.status_code}:"
            f" {_redacted(response.text[:500], key)}"
        )
    return response.json()


def _witness(client: httpx.Client, key: str, generation_id: str) -> dict[str, Any]:
    """Which upstream provider served, from OpenRouter's own generation record.

    The record lands some seconds after the completion returns, so a miss is
    retried and then reported as absent rather than raised. An unavailable
    witness weakens leg 2 and invalidates nothing else. Ten tries at three
    seconds, because five at two seconds was measured too short.
    """
    for _ in range(10):
        response = client.get(
            f"{API_BASE}/generation",
            headers={"Authorization": f"Bearer {key}"},
            params={"id": generation_id},
        )
        if response.status_code == 200:
            return response.json().get("data", {})
        time.sleep(3)
    return {}


def _permaslug(witness: dict[str, Any]) -> str | None:
    """The pinned, dated identifier OpenRouter records for the build that served.

    It sits on the per-upstream entry rather than on the record's own ``model``,
    and it is the field that carries a build the request never named.
    """
    responses = witness.get("provider_responses") or [{}]
    return responses[0].get("model_permaslug")


def _translated(body: dict[str, Any], slug: str) -> str:
    """What the installed translator puts in ``model_response.model`` for this body.

    The same transformation ``tests/test_identity.py`` drives, over the body
    this run actually received rather than over a canned one.
    """
    from litellm.litellm_core_utils.litellm_logging import Logging
    from litellm.types.utils import LlmProviders, ModelResponse
    from litellm.utils import ProviderConfigManager

    vendor = vendor_for("openrouter")
    config = ProviderConfigManager.get_provider_chat_config(
        model=slug, provider=LlmProviders(vendor.litellm_provider)
    )
    assert config is not None
    logging_obj = Logging(
        model=slug,
        messages=[],
        stream=False,
        call_type="completion",
        start_time=0,
        litellm_call_id="probe",
        function_id="probe",
    )
    logging_obj.optional_params = {}
    translated = config.transform_response(
        model=slug,
        raw_response=httpx.Response(
            200, json=body, request=httpx.Request("POST", f"{API_BASE}/recorded")
        ),
        model_response=ModelResponse(),
        logging_obj=logging_obj,
        request_data={},
        messages=[{"role": "user", "content": "Say ok."}],
        optional_params={},
        litellm_params={},
        encoding=None,
    )
    return translated.model


def probe_slug(client: httpx.Client, key: str, slug: str, repeat: int) -> None:
    """Run every leg over one slug and print what each returned."""
    print(f"\n=== requested slug: {slug!r} ({repeat} call(s)) ===")
    served: list[str] = []
    providers: list[str] = []
    for attempt in range(1, repeat + 1):
        body = _complete(client, key, slug)
        witness = _witness(client, key, str(body.get("id", "")))
        served.append(str(body.get("model")))
        providers.append(str(witness.get("provider_name", "<no witness>")))
        print(f"\ncall {attempt}")
        for field in BODY_FIELDS:
            print(f"  body.{field:<8} = {body.get(field)!r}")
        extra = sorted(set(body) - set(BODY_FIELDS) - {"choices", "usage"})
        print(f"  body other keys = {extra}")
        print(f"  witness.provider_name  = {witness.get('provider_name')!r}")
        print(f"  witness.model          = {witness.get('model')!r}")
        print(f"  witness.model_permaslug = {_permaslug(witness)!r}")
        print(f"  translated model      = {_translated(body, slug)!r}")

    print(f"\nsummary for {slug!r}")
    print(f"  distinct served values : {sorted(set(served))}")
    print(f"  distinct providers     : {sorted(set(providers))}")
    print(f"  served equals requested: {set(served) == {slug}}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure what an OpenRouter response names in `model` (#806)."
    )
    parser.add_argument(
        "--slug",
        action="append",
        help="a slug to probe; repeatable, and replaces both defaults",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=3,
        help="billed completions per slug (default: 3)",
    )
    args = parser.parse_args()
    slugs = args.slug or [SINGLE_PROVIDER_SLUG, MULTI_PROVIDER_SLUG]

    try:
        key = _api_key()
    except ProviderAuthError as exc:
        print(exc, file=sys.stderr)
        return 1
    with httpx.Client(timeout=TIMEOUT) as client:
        for slug in slugs:
            probe_slug(client, key, slug, args.repeat)
    return 0


if __name__ == "__main__":
    sys.exit(main())
