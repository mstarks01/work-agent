"""Probe: is ``reasoning_effort`` a uniform reasoning surface across vendors?

Evidence for wayfinder ticket #15, question 3. Ticket #13's probe confirmed
``reasoning_effort`` on ``vertex_ai`` gemini and on openai o-series, and the
``thinking={...}`` dict form on anthropic — but never asked whether anthropic
*also* accepts the enum. If it does, the config surface can carry one spelling
for every vendor; if it does not, ``thinking`` becomes vendor-shaped.

Also probes the ``max_tokens`` collision: anthropic's reasoning form injects a
``max_tokens`` this repo sets independently via ``max_output_tokens``.

Isolation follows ``docs/research/probe_litellm_buildtime_gate.py``: the network
is blocked and every credential stripped before ``litellm`` is imported, so a
pass here is a pure build-time call, not a slow one.
"""

from __future__ import annotations

import os
import socket
from importlib import metadata
from typing import Any, NamedTuple


class _NoNetworkSocket(socket.socket):
    """A socket that refuses to connect."""

    def connect(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("network access attempted")

    def connect_ex(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("network access attempted")


def _isolate() -> None:
    """Block the network and strip every credential before importing litellm."""
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    socket.socket = _NoNetworkSocket  # type: ignore[misc]
    leaked = [
        name
        for name in os.environ
        if any(marker in name for marker in ("API_KEY", "GOOGLE", "AWS", "AZURE"))
    ]
    for name in leaked:
        del os.environ[name]


class Case(NamedTuple):
    """One ``(vendor, model, params)`` probe and the label to report it under."""

    label: str
    model: str
    provider: str
    params: dict[str, Any]


ANTHROPIC = "claude-sonnet-4-5"
VERTEX_CLAUDE = "claude-sonnet-4-5@20250929"
GEMINI = "gemini-2.5-pro"

CASES: tuple[Case, ...] = (
    # The open question: does the enum reach anthropic at all, direct and
    # via Vertex (a distinct config class per #12)?
    Case("anthropic / effort low", ANTHROPIC, "anthropic", {"reasoning_effort": "low"}),
    Case("anthropic / effort medium", ANTHROPIC, "anthropic", {"reasoning_effort": "medium"}),
    Case("anthropic / effort high", ANTHROPIC, "anthropic", {"reasoning_effort": "high"}),
    Case("vertex claude / effort low", VERTEX_CLAUDE, "vertex_ai", {"reasoning_effort": "low"}),
    # Baselines #13 already established, re-run under this pin for comparison.
    Case("anthropic / thinking dict", ANTHROPIC, "anthropic", {"thinking": {"type": "enabled", "budget_tokens": 2048}}),
    Case("gemini / effort low", GEMINI, "vertex_ai", {"reasoning_effort": "low"}),
    Case("gemini / effort high", GEMINI, "vertex_ai", {"reasoning_effort": "high"}),
    Case("openai o3 / effort high", "o3", "openai", {"reasoning_effort": "high"}),
    # Is there a uniform "off"? Gemini can disable thinking; the repo's
    # `thinking = "off"` scalar needs somewhere to land on every vendor.
    Case("anthropic / effort none", ANTHROPIC, "anthropic", {"reasoning_effort": "none"}),
    Case("gemini / effort none", GEMINI, "vertex_ai", {"reasoning_effort": "none"}),
    Case("openai o3 / effort none", "o3", "openai", {"reasoning_effort": "none"}),
    Case("openai gpt-4o / effort low", "gpt-4o", "openai", {"reasoning_effort": "low"}),
    # The max_tokens collision: reasoning alongside an explicit cap.
    Case("anthropic / effort + max_tokens", ANTHROPIC, "anthropic", {"reasoning_effort": "low", "max_tokens": 8192}),
    Case("anthropic / thinking + max_tokens", ANTHROPIC, "anthropic", {"thinking": {"type": "enabled", "budget_tokens": 2048}, "max_tokens": 8192}),
    Case("anthropic / max_tokens alone", ANTHROPIC, "anthropic", {"max_tokens": 8192}),
    Case("gemini / effort + max_tokens", GEMINI, "vertex_ai", {"reasoning_effort": "low", "max_tokens": 8192}),
)


def main() -> None:
    _isolate()

    import litellm
    from litellm.utils import get_optional_params

    installed = metadata.version("litellm")
    print(f"litellm {installed}, drop_params={litellm.drop_params!r}")
    print(f"{'case':38} {'outcome':8} detail")
    print("-" * 110)

    for case in CASES:
        try:
            resolved = get_optional_params(
                model=case.model,
                custom_llm_provider=case.provider,
                **case.params,
            )
        except Exception as exc:
            detail = f"{type(exc).__name__}: {str(exc)[:64]}"
            print(f"{case.label:38} {'RAISE':8} {detail}")
        else:
            interesting = {
                key: value
                for key, value in resolved.items()
                if key not in ("stream", "extra_body")
            }
            print(f"{case.label:38} {'PASS':8} -> {interesting}")


if __name__ == "__main__":
    main()
