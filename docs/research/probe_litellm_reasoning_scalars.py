"""Probe: where do the repo's ``thinking`` scalars land on the enum surface?

Follow-up to ``probe_reasoning_surface.py`` for wayfinder ticket #15. The first
pass established that ``reasoning_effort`` is accepted by all three vendors.
This one asks what happens to the two non-integer scalars ``sampling.toml``
already offers — ``"auto"`` (gemini dynamic allocation, ``thinking_budget=-1``)
and ``"off"`` — plus whether the raw dict form crosses vendors.
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
GEMINI_PRO = "gemini-2.5-pro"
GEMINI_FLASH = "gemini-2.5-flash"

CASES: tuple[Case, ...] = (
    # "auto" = gemini dynamic allocation. Is there an enum spelling for it?
    Case("gemini-pro / effort auto", GEMINI_PRO, "vertex_ai", {"reasoning_effort": "auto"}),
    Case("anthropic / effort auto", ANTHROPIC, "anthropic", {"reasoning_effort": "auto"}),
    Case("openai o3 / effort auto", "o3", "openai", {"reasoning_effort": "auto"}),
    Case("gemini-pro / effort minimal", GEMINI_PRO, "vertex_ai", {"reasoning_effort": "minimal"}),
    Case("openai o3 / effort minimal", "o3", "openai", {"reasoning_effort": "minimal"}),
    Case("openai o3 / effort disable", "o3", "openai", {"reasoning_effort": "disable"}),
    # The floor that THINKING_RANGE encoded: gemini-2.5-pro cannot disable
    # thinking (0 is a 400). Does the gate know, on either tier's model?
    Case("gemini-flash / effort none", GEMINI_FLASH, "vertex_ai", {"reasoning_effort": "none"}),
    Case("gemini-pro / thinking dict", GEMINI_PRO, "vertex_ai", {"thinking": {"type": "enabled", "budget_tokens": 2048}}),
    Case("gemini-pro / thinking budget 0", GEMINI_PRO, "vertex_ai", {"thinking": {"type": "enabled", "budget_tokens": 0}}),
    Case("gemini-pro / thinking budget -1", GEMINI_PRO, "vertex_ai", {"thinking": {"type": "enabled", "budget_tokens": -1}}),
    Case("openai o3 / thinking dict", "o3", "openai", {"thinking": {"type": "enabled", "budget_tokens": 2048}}),
    # Garbage: does the gate validate the enum's *values*, or wave them through?
    Case("gemini-pro / effort nonsense", GEMINI_PRO, "vertex_ai", {"reasoning_effort": "banana"}),
    Case("anthropic / effort nonsense", ANTHROPIC, "anthropic", {"reasoning_effort": "banana"}),
    Case("openai o3 / effort nonsense", "o3", "openai", {"reasoning_effort": "banana"}),
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
