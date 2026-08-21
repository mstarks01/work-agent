"""Probe: can ``litellm.utils.get_optional_params`` serve as a build-time gate?

Throwaway evidence for wayfinder ticket #13. Answers three things the ticket
could not settle from source alone, because ``litellm`` was not installed:

1. Is ``get_optional_params`` callable standalone — offline, no credentials?
2. Does it catch **name** violations (``seed`` on Anthropic)?
3. Does it catch **value** violations (o-series ``temperature != 1``), which a
   ``frozenset[str]`` membership check provably cannot express?

Run with ``LITELLM_LOCAL_MODEL_COST_MAP=True`` to confirm the hermetic path;
run without it to observe the import-time fetch of the remote model-cost map.
"""

from __future__ import annotations

import os
import socket
from typing import Any, NamedTuple


class _NoNetworkSocket(socket.socket):
    """A socket that refuses to connect.

    The claim under test is that the gate is a *pure* build-time call, so any
    outbound connection is a failed probe, not a slow one.
    """

    def connect(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("network access attempted")

    def connect_ex(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("network access attempted")


def _isolate() -> None:
    """Block the network and strip every credential before importing litellm."""
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


CASES: tuple[Case, ...] = (
    # Name constraints: the same param disagreeing across vendors.
    Case("anthropic / seed", "claude-sonnet-4-5", "anthropic", {"seed": 42}),
    Case("anthropic / temperature", "claude-sonnet-4-5", "anthropic", {"temperature": 0.0}),
    Case("anthropic / presence_penalty", "claude-sonnet-4-5", "anthropic", {"presence_penalty": 0.5}),
    # The #12 split: one `vertex_ai` provider, two answers, by model prefix.
    Case("vertex claude / seed", "claude-sonnet-4-5@20250929", "vertex_ai", {"seed": 42}),
    Case("vertex gemini / seed", "gemini-2.5-pro", "vertex_ai", {"seed": 42}),
    Case("openai gpt-4o / seed", "gpt-4o", "openai", {"seed": 42}),
    # Value constraint: in the supported list, but only at exactly 1.
    Case("openai o-series / temperature 0", "o3", "openai", {"temperature": 0.0}),
    Case("openai o-series / temperature 1", "o3", "openai", {"temperature": 1.0}),
    Case("openai o-series / top_p", "o3", "openai", {"top_p": 0.9}),
    # Unknown models: does the gate become a de-facto existence check?
    Case("unknown anthropic model / seed", "claude-nonexistent-9", "anthropic", {"seed": 42}),
    Case("unknown vertex model / temperature", "some-future-model", "vertex_ai", {"temperature": 0.0}),
    Case("unknown openai model / top_p", "gpt-9-turbo", "openai", {"top_p": 0.9}),
    # Reasoning surfaces, which differ in *shape*, not just availability.
    Case("anthropic / thinking", "claude-sonnet-4-5", "anthropic", {"thinking": {"type": "enabled", "budget_tokens": 2048}}),
    Case("gemini / reasoning_effort", "gemini-2.5-pro", "vertex_ai", {"reasoning_effort": "low"}),
    Case("openai o-series / reasoning_effort", "o3", "openai", {"reasoning_effort": "high"}),
)


def main() -> None:
    _isolate()

    import litellm
    from litellm.utils import get_optional_params

    print(f"litellm.drop_params = {litellm.drop_params!r}  (must be falsy: fail-closed)")
    print(f"{'case':38} {'outcome':8} detail")
    print("-" * 100)

    for case in CASES:
        try:
            resolved = get_optional_params(
                model=case.model,
                custom_llm_provider=case.provider,
                **case.params,
            )
        except Exception as exc:
            detail = f"{type(exc).__name__}: {str(exc)[:70]}"
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
