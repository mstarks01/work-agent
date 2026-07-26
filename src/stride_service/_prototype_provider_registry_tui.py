"""PROTOTYPE — throwaway TUI for wayfinder ticket #6. See the logic module
(``_prototype_provider_registry.py``) for the question being answered.

Run it:

    python src/stride_service/_prototype_provider_registry_tui.py

Drive it by hand: switch the provider and watch how each one binds a node's
model and where it folds resilience (retry vs timeout). The point is to feel
whether the ``ProviderBinding(resolve_model, http_options)`` seam actually
absorbs the Gemini-vs-Claude difference cleanly, and that an unknown provider
fails closed.
"""

from __future__ import annotations

import sys

from stride_service._prototype_provider_registry import (
    DEFAULT_PROVIDER,
    ModelConfigError,
    Resilience,
    Tiers,
    build_model_resolver,
    known_providers,
)

BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
RESET = "\x1b[0m"

# A realistic-ish slice of the repo's node->model map (flash/pro tiers).
TIERS = Tiers(
    by_node={
        "extract": "gemini-2.5-flash",
        "repair": "gemini-2.5-flash",
        "analyst/spoofing": "gemini-2.5-pro",
        "critic": "gemini-2.5-pro",
        "recritic": "gemini-2.5-pro",
    }
)
RESILIENCE = Resilience(attempts=3, timeout_ms=60_000)
NODES = list(TIERS.by_node)

# Providers to cycle through, plus a deliberately-bogus one to prove fail-closed.
CYCLE = [None, *known_providers(), "openai-direct"]


def clear() -> None:
    print("\x1b[2J\x1b[H", end="")


def render(provider_idx: int, node_idx: int) -> None:
    clear()
    provider = CYCLE[provider_idx]
    node = NODES[node_idx]

    print(f"{BOLD}Ticket #6 — build_model_resolver registry seam{RESET}")
    print(f"{DIM}resilience: attempts=3 (total), timeout_ms=60000{RESET}\n")

    shown = provider if provider is not None else f"(unset -> {DEFAULT_PROVIDER})"
    print(f"{BOLD}STRIDE_MODEL_PROVIDER{RESET} = {GREEN}{shown}{RESET}")
    print(f"{BOLD}node{RESET}                 = {node}\n")

    try:
        binding = build_model_resolver(provider, TIERS, RESILIENCE)
    except ModelConfigError as exc:
        print(f"{RED}{BOLD}FAIL CLOSED{RESET} {RED}{exc}{RESET}")
        _footer()
        return

    plan = binding.resolve_model(node)
    http = binding.http_options

    print(f"{BOLD}resolve_model(node) ->{RESET}")
    print(f"    {plan.wrapper}(model={plan.model!r}")
    for key, value in plan.kwargs.items():
        print(f"           {key}={value}")
    print("    )")
    print(f"    {DIM}report records model = {plan.model!r}{RESET}\n")

    retry_here = http.num_retries is not None
    print(f"{BOLD}every node's http_options ->{RESET}")
    print(f"    timeout_ms = {http.timeout_ms}")
    if retry_here:
        print(f"    num_retries = {http.num_retries}   {DIM}(attempts-1){RESET}")
    else:
        print(f"    num_retries = {DIM}(not here){RESET}")
    print()

    where_retry = "http_options" if retry_here else f"{plan.wrapper} constructor"
    print(f"{BOLD}resilience expressed:{RESET}")
    print(f"    retry   -> {GREEN}{where_retry}{RESET}")
    print(f"    timeout -> {GREEN}http_options{RESET}")

    _footer()


def _footer() -> None:
    print(
        f"\n{DIM}[p] next provider   [n] next node   [q] quit{RESET}"
    )


def _read_key() -> str:
    """One keystroke, no Enter, raw terminal."""
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def main() -> None:
    provider_idx = 0
    node_idx = 0
    while True:
        render(provider_idx, node_idx)
        key = _read_key().lower()
        if key == "q":
            clear()
            return
        if key == "p":
            provider_idx = (provider_idx + 1) % len(CYCLE)
        elif key == "n":
            node_idx = (node_idx + 1) % len(NODES)


if __name__ == "__main__":
    main()
