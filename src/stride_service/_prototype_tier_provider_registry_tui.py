"""PROTOTYPE — throwaway TUI for wayfinder ticket #6. See the logic module
(``_prototype_tier_provider_registry.py``) for the question being answered.

Run it:

    .venv/bin/python src/stride_service/_prototype_tier_provider_registry_tui.py

Drive it by hand. Point each tier at a different vendor and watch: how many
adapter instances exist, which credential mode each tier lands in, what a
missing API key does, which sampling params never reach the wire, and how many
tries a node actually gets. The interesting moments are the ones where the
panel says something you did not expect.
"""

from __future__ import annotations

import sys
import termios
import tty
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stride_service._prototype_tier_provider_registry import (
    NODE_TIERS,
    TIER_NAMES,
    LiteLlmPlan,
    ProviderAuthError,
    ProviderConfigError,
    Resilience,
    TierProvider,
    TierSampling,
    build_tier_bindings,
    fingerprint,
    known_vendors,
    resolve_model,
    wire_sampling,
)

BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
RESET = "\x1b[0m"

# Where the tiers start: today's shipped config, renamed base/strong.
STATE = {
    "providers": {
        "base": TierProvider("vertex", "gemini-2.5-flash"),
        "strong": TierProvider("vertex", "gemini-2.5-pro"),
    },
    "sampling": {
        "base": TierSampling(temperature=0.0),
        "strong": TierSampling(temperature=0.0),
    },
    "env": {},
    # Defaults are the decisions taken so far: sampling params ADK does not
    # forward go on the constructor, a param the vendor cannot take refuses to
    # build, and retry does its own arithmetic. [x]/[v]/[r] show the rejected
    # alternatives.
    "retry_position": "constructor",
    "rescue_unforwarded": True,
    "check_vendor_support": True,
}
RETRY_POSITIONS = ("none", "http_options", "constructor")
RESILIENCE = Resilience(attempts=3, timeout_ms=300_000)


def _cycle_vendor(tier: str) -> None:
    vendors = known_vendors()
    current = STATE["providers"][tier].vendor
    nxt = vendors[(vendors.index(current) + 1) % len(vendors)]
    STATE["providers"][tier] = TierProvider(nxt, _default_model(nxt, tier))


def _default_model(vendor: str, tier: str) -> str:
    """A plausible pinned string per vendor, so the prefix effect is visible."""
    models = {
        "vertex": {"base": "gemini-2.5-flash", "strong": "gemini-2.5-pro"},
        "anthropic": {
            "base": "claude-haiku-4-5-20251001",
            "strong": "claude-sonnet-4-5-20250929",
        },
        "openai": {"base": "gpt-4o-mini-2024-07-18", "strong": "gpt-4o-2024-11-20"},
    }
    return models[vendor][tier]


def _cycle_retry() -> None:
    current = RETRY_POSITIONS.index(STATE["retry_position"])
    STATE["retry_position"] = RETRY_POSITIONS[(current + 1) % len(RETRY_POSITIONS)]


def _toggle_key(vendor_env: str) -> None:
    env = STATE["env"]
    if vendor_env in env:
        del env[vendor_env]
    else:
        env[vendor_env] = "sk-not-a-real-key"


def _toggle_seed() -> None:
    for tier in TIER_NAMES:
        s = STATE["sampling"][tier]
        STATE["sampling"][tier] = TierSampling(
            temperature=s.temperature,
            seed=None if s.seed is not None else 42,
            thinking_budget=None if s.thinking_budget is not None else 4096,
        )


def _render_tier(tier: str, plan: LiteLlmPlan) -> list[str]:
    sampling = STATE["sampling"][tier]
    sent, lost = wire_sampling(sampling, plan)
    rescued = "".join(
        f", {kwarg}={value}" for kwarg, value in sorted(plan.rescued.values())
    )
    kwargs = (
        f"                  timeout={plan.timeout_ms}, num_retries={plan.num_retries}"
        + rescued
        + (f", api_key=<{plan.api_key_var}>" if plan.api_key_var else "")
        + ")"
    )
    lines = [
        (
            f"  {BOLD}{tier:<7}{RESET} {plan.vendor:<10}"
            f" {DIM}pinned{RESET} {plan.pinned_model}"
        ),
        f"          {BOLD}LiteLlm(model={plan.model!r}{RESET}",
        kwargs,
        (
            f"          {DIM}credential{RESET} {plan.credential}"
            f"   {DIM}fingerprint{RESET} {fingerprint(plan.model, sampling)[:12]}…"
        ),
    ]
    tries = plan.effective_attempts
    verdict = GREEN if tries == RESILIENCE.attempts else RED
    word = "try" if tries == 1 else "tries"
    lines.append(
        f"          {DIM}config attempts={RESILIENCE.attempts}{RESET} ->"
        f" {verdict}{tries} actual {word}{RESET}"
    )
    if plan.rescued:
        moves = ", ".join(
            f"{name} -> {kwarg}=" for name, (kwarg, _) in sorted(plan.rescued.items())
        )
        lines.append(
            f"          {YELLOW}rescued by the binding:{RESET} {moves}"
            f" {DIM}(constructor kwarg){RESET}"
        )
    if lost:
        lines.append(
            f"          {RED}LOST before LiteLLM:{RESET} {', '.join(lost)}"
            f" {DIM}(in the fingerprint, never on the wire){RESET}"
        )
    lines.append(f"          {DIM}on the wire:{RESET} {sent}")
    return lines


def render() -> None:
    print("\033[2J\033[H", end="")
    print(f"{BOLD}wayfinder #6 — per-tier provider binding seam{RESET}")
    subtitle = "registry -> one LiteLlm per tier; config names (vendor, model) only"
    print(f"{DIM}{subtitle}{RESET}\n")

    try:
        bindings = build_tier_bindings(
            STATE["providers"],
            STATE["sampling"],
            RESILIENCE,
            STATE["env"],
            retry_position=STATE["retry_position"],
            rescue_unforwarded=STATE["rescue_unforwarded"],
            check_vendor_support=STATE["check_vendor_support"],
        )
    except (ProviderConfigError, ProviderAuthError) as exc:
        print(f"{BOLD}BINDINGS{RESET}\n")
        print(f"  {RED}FAIL CLOSED{RESET}  {type(exc).__name__}: {exc}\n")
        print(f"  {DIM}no adapter built; the service does not start{RESET}\n")
        _render_controls()
        return

    print(f"{BOLD}BINDINGS{RESET}\n")
    for tier in TIER_NAMES:
        for line in _render_tier(tier, bindings[tier]):
            print(line)
        print()

    instances = {id(resolve_model(bindings, node)) for node in NODE_TIERS}
    print(
        f"{BOLD}NODES{RESET}  {len(NODE_TIERS)} LLM nodes ->"
        f" {GREEN}{len(instances)} adapter instances{RESET}"
        f" {DIM}(one per tier, shared by identity){RESET}"
    )
    mixed = len({p.vendor for p in bindings.values()}) > 1
    if mixed:
        print(f"       {YELLOW}two vendors live at once{RESET}")
    print()
    _render_controls()


def _render_controls() -> None:
    labels = {
        "none": "nowhere (today)",
        "http_options": "http_options (verbatim)",
        "constructor": "constructor (attempts-1)",
    }
    retry = labels[STATE["retry_position"]]
    keys = ", ".join(sorted(STATE["env"])) or "none set"
    on = f"{GREEN}on{RESET}"
    off = f"{DIM}off{RESET}"
    print(
        f"{BOLD}TOGGLES{RESET}  {DIM}retry lives:{RESET} {retry}"
        f"   {DIM}rescue:{RESET} {on if STATE['rescue_unforwarded'] else off}"
        f"   {DIM}vendor check:{RESET} {on if STATE['check_vendor_support'] else off}"
        f"   {DIM}keys:{RESET} {keys}"
    )
    print(
        f"{BOLD}[b]{RESET}{DIM} base vendor{RESET}  "
        f"{BOLD}[s]{RESET}{DIM} strong vendor{RESET}  "
        f"{BOLD}[r]{RESET}{DIM} retry position{RESET}  "
        f"{BOLD}[t]{RESET}{DIM} seed+thinking{RESET}"
    )
    print(
        f"{BOLD}[a]{RESET}{DIM} anthropic key{RESET}  "
        f"{BOLD}[o]{RESET}{DIM} openai key{RESET}  "
        f"{BOLD}[x]{RESET}{DIM} rescue unforwarded{RESET}  "
        f"{BOLD}[v]{RESET}{DIM} vendor support check{RESET}  "
        f"{BOLD}[q]{RESET}{DIM} quit{RESET}"
    )


def _read_key() -> str:
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def main() -> None:
    actions = {
        "b": lambda: _cycle_vendor("base"),
        "s": lambda: _cycle_vendor("strong"),
        "r": _cycle_retry,
        "t": _toggle_seed,
        "a": lambda: _toggle_key("STRIDE_ANTHROPIC_API_KEY"),
        "o": lambda: _toggle_key("STRIDE_OPENAI_API_KEY"),
        "x": lambda: STATE.__setitem__(
            "rescue_unforwarded", not STATE["rescue_unforwarded"]
        ),
        "v": lambda: STATE.__setitem__(
            "check_vendor_support", not STATE["check_vendor_support"]
        ),
    }
    while True:
        render()
        key = _read_key().lower()
        if key in ("q", "\x03"):
            print()
            return
        action = actions.get(key)
        if action is not None:
            action()


if __name__ == "__main__":
    main()
