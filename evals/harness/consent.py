"""The estimate gate: informed, affirmative consent before a sweep spends.

There is **no spend ceiling** (#334). A contributor may spend any amount; the
gate's job is to make sure they know what they are accepting, know how good
that number is, and accept it in a way a rote hand cannot.

**Every amount carries one of three labels, always.**

``recorded``
    A merged Baseline with this exact configuration recorded this actual. A
    real number, not a guess.
``estimated``
    Price-map arithmetic over another Baseline's token counts, repriced with
    this run's models. A best guess.
``unpriced``
    No number exists — because a tier's model is absent from the price map,
    or because no merged Baseline exists to calibrate from. Never a zero.

**Acceptance is typing the amount back.** An enter or a ``y`` never proceeds.
The mechanism is rote-proof because the number changes with the
configuration. A script states its own number with ``--accept-cost <usd>``,
and the run refuses when the estimate exceeds it; ``--accept-cost unknown``
is how a script accepts a cost nobody can state. The kinds must match: a
number does not answer an unpriced estimate, and ``unknown`` does not answer
a stated one.

**The run holds the contributor to what they accepted.** Between cases —
never inside one — :func:`hold` compares the spend so far to the accepted
amount. A terminal re-prompts with the new number and the sweep continues on
a fresh typed acceptance; under ``--accept-cost`` no hand is present, so the
run stops. This is not a ceiling by the back door: the number is the
contributor's own, and the stop only holds them to it.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from evals.harness.prices import price_calls, unit_prices
from stride_service.report import NodeRun, TokenUsage

Label = Literal["recorded", "estimated", "unpriced"]

#: The word that accepts a cost nobody can state, typed or passed.
UNKNOWN = "unknown"


class Refused(RuntimeError):
    """The gate did not get consent; nothing may be spent."""


@dataclass(frozen=True)
class Estimate:
    """What this sweep is expected to cost, and how good that number is."""

    label: Label
    amount_usd: float | None
    lines: tuple[str, ...]

    def __post_init__(self) -> None:
        # The invariant the whole gate rests on: an amount exists exactly when
        # one can be stated. A zero standing in for "no idea" is the one
        # outcome #334 forbids outright.
        if (self.amount_usd is None) != (self.label == "unpriced"):
            raise ValueError(
                f"label {self.label!r} and amount {self.amount_usd!r} disagree"
                " about whether a number exists"
            )

    @property
    def offer(self) -> str:
        """What the contributor must type back to accept."""
        return UNKNOWN if self.amount_usd is None else f"{self.amount_usd:.2f}"


def _calls_of(executions: Sequence[NodeRun]) -> list[tuple[str, str, TokenUsage]]:
    """The billable calls in a set of node runs, ready for pricing."""
    return [
        (run.model, run.requested_model or run.model, run.usage)
        for run in executions
        if run.usage is not None and run.model is not None
    ]


def spent(executions: Sequence[NodeRun]) -> float:
    """What the sweep has spent so far, priced against the live map."""
    return price_calls(_calls_of(executions)).total_usd


def _merged_baselines(root: Path) -> list[dict[str, Any]]:
    directory = root / "evals" / "baselines"
    manifests = []
    for manifest in sorted(directory.glob("*/baseline.json")):
        try:
            manifests.append(json.loads(manifest.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue  # verify() is what refuses a broken Baseline, not the gate
    return manifests


def _recorded_actual(manifest: dict[str, Any]) -> float | None:
    """A Baseline's mean recorded actual across its sweeps."""
    actuals = [
        float(sweep.get("cost", {}).get("actual_usd"))
        for sweep in manifest.get("sweeps", [])
        if sweep.get("cost", {}).get("actual_usd") is not None
    ]
    return sum(actuals) / len(actuals) if actuals else None


def estimate(
    identity: dict[str, Any],
    routes: dict[str, str],
    root: Path,
) -> Estimate:
    """Price this sweep before it runs, from the merged Baselines.

    ``identity`` is the five-part Baseline identity this sweep would carry,
    and ``routes`` is the requested route per tier. An exact identity match
    is a ``recorded`` number; any other Baseline lends its per-tier token
    counts, repriced with this run's models, for an ``estimated`` one.
    """
    manifests = _merged_baselines(root)
    lines: list[str] = []
    for manifest in manifests:
        if manifest.get("identity") == identity:
            actual = _recorded_actual(manifest)
            if actual is not None:
                lines.append(
                    f"calibrated from {manifest.get('name')}, which ran this"
                    " exact configuration"
                )
                return Estimate(label="recorded", amount_usd=actual, lines=tuple(lines))

    missing = sorted(
        tier for tier, route in routes.items() if unit_prices(route) is None
    )
    if missing:
        lines.append(
            "no price for " + ", ".join(f"{tier} ({routes[tier]})" for tier in missing)
        )
    for tier, route in sorted(routes.items()):
        prices = unit_prices(route)
        if prices is not None:
            lines.append(
                f"{tier} ({route}): ${prices.input_per_token * 1e6:.2f} in,"
                f" ${prices.output_per_token * 1e6:.2f} out, per million tokens"
            )
    if missing:
        return Estimate(label="unpriced", amount_usd=None, lines=tuple(lines))

    borrowed = _borrow(manifests, routes)
    if borrowed is None:
        lines.append(
            "no merged Baseline to calibrate token counts from, so no amount"
            " can be stated"
        )
        return Estimate(label="unpriced", amount_usd=None, lines=tuple(lines))
    amount, source = borrowed
    lines.append(f"token counts borrowed from {source}, repriced for this run")
    return Estimate(label="estimated", amount_usd=amount, lines=tuple(lines))


def _borrow(
    manifests: Sequence[dict[str, Any]], routes: dict[str, str]
) -> tuple[float, str] | None:
    """Another Baseline's token counts, repriced with this run's models.

    Scales that Baseline's recorded actual by the ratio of this run's unit
    prices to the ones it recorded — the arithmetic is crude on purpose,
    because the label already says the number is a guess and a more elaborate
    model would only make the guess look better than it is.

    **Both sides of the ratio count one rate per distinct model.** That is the
    invariant, and it is the whole of the correctness here: the lent actual is
    a mean over the Baseline's sweeps, so a divisor that grew with the sweep
    count divided a per-sweep number by a per-directory one. A ten-sweep
    Baseline then lent a tenth of its own cost, and the contributor consented
    to that. Deduplicating by model keeps the ratio a property of the two
    configurations rather than of how many times somebody ran one of them.
    """
    for manifest in manifests:
        actual = _recorded_actual(manifest)
        sweeps = manifest.get("sweeps", [])
        if actual is None or not sweeps:
            continue
        theirs = sum(_recorded_rates(sweeps).values())
        ours = sum(_route_rates(routes).values())
        if theirs <= 0 or ours <= 0:
            continue
        return actual * (ours / theirs), str(manifest.get("name"))
    return None


def _recorded_rates(sweeps: Sequence[dict[str, Any]]) -> dict[str, float]:
    """The lending Baseline's output rate per model, across all its sweeps.

    Every sweep of one Baseline priced the same models, so the entries repeat
    once per sweep. Keying by model is what collapses them back to the one
    rate each model actually has.
    """
    return {
        str(entry.get("model")): float(entry.get("output_per_token", 0.0))
        for sweep in sweeps
        for entry in sweep.get("cost", {}).get("unit_prices", [])
    }


def _route_rates(routes: dict[str, str]) -> dict[str, float]:
    """This run's output rate per model, keyed the same way as the lender's.

    Keyed by model rather than by tier, because two tiers may resolve to one
    model and the other side of the ratio counts that model once. Sorted so a
    consented amount is reproducible down to the floating-point addition.
    """
    return {
        route: prices.output_per_token
        for route in sorted(set(routes.values()))
        if (prices := unit_prices(route)) is not None
    }


def _render(estimate: Estimate) -> str:
    headline = (
        f"no amount can be stated [{estimate.label}]"
        if estimate.amount_usd is None
        else f"${estimate.amount_usd:.2f} [{estimate.label}]"
    )
    body = "\n".join(f"  {line}" for line in estimate.lines)
    return f"\nthis sweep is expected to cost {headline}\n{body}"


def gate(
    estimate: Estimate,
    accept_cost: str | None,
    ask: Callable[[str], str] | None = None,
) -> float | None:
    """Take consent, or raise :class:`Refused`. Returns the accepted amount.

    ``None`` comes back when the contributor accepted ``unknown`` — a real
    acceptance of a cost nobody can state, and the one case the mid-run hold
    cannot measure against.
    """
    print(_render(estimate))
    if accept_cost is not None:
        return _accept_from_flag(estimate, accept_cost)
    return _accept_by_typing(estimate, ask or (lambda prompt: input(prompt)))


def _accept_from_flag(estimate: Estimate, accept_cost: str) -> float | None:
    if accept_cost.strip().lower() == UNKNOWN:
        if estimate.amount_usd is not None:
            raise Refused(
                f"--accept-cost {UNKNOWN} does not answer a stated estimate of"
                f" ${estimate.amount_usd:.2f}; accept the number instead"
            )
        print(f"accepted {UNKNOWN}: no amount could be stated, and you said so")
        return None
    try:
        accepted = float(accept_cost)
    except ValueError as exc:
        raise Refused(
            f"--accept-cost {accept_cost!r} is neither a number of dollars nor"
            f" the word {UNKNOWN!r}"
        ) from exc
    if estimate.amount_usd is None:
        raise Refused(
            f"no amount can be stated, so ${accepted:.2f} answers nothing;"
            f" pass --accept-cost {UNKNOWN} to accept a cost nobody can state"
        )
    if estimate.amount_usd > accepted:
        raise Refused(
            f"the estimate is ${estimate.amount_usd:.2f} and you accepted"
            f" ${accepted:.2f}; raise --accept-cost to proceed"
        )
    print(f"accepted ${accepted:.2f}")
    return accepted


def _accept_by_typing(estimate: Estimate, ask: Callable[[str], str]) -> float | None:
    """Type the amount back. An enter or a ``y`` never proceeds."""
    prompt = (
        f"\ntype {estimate.offer!r} to accept, or anything else to stop: "
        if estimate.amount_usd is None
        else f"\ntype the amount ({estimate.offer}) to accept, or anything"
        " else to stop: "
    )
    typed = ask(prompt).strip()
    if typed.lstrip("$") != estimate.offer:
        raise Refused(
            f"{typed!r} is not {estimate.offer!r}; nothing was spent."
            " Accepting a cost means typing it, so a habit cannot do it for you"
        )
    return estimate.amount_usd


def hold(
    accepted: float | None,
    executions: Sequence[NodeRun],
    remaining: Sequence[str],
    ask: Callable[[str], str] | None,
    *,
    ran: int,
) -> float | None:
    """Between cases: is the run still inside what was accepted?

    Returns the amount now in force — the same one, or a freshly accepted
    larger one. Raises :class:`Refused` to stop the sweep, which the caller
    turns into a written artifact rather than a lost one.

    **The re-prompt offers the whole sweep, never the sunk spend.** Accepting
    what is already spent puts the run over its own figure again on the very
    next case, so a single overrun became a prompt at every remaining case
    boundary. What the contributor is asked for instead is the projection —
    the spend so far, plus the same per-case rate over the cases still to run
    — so one overrun costs one prompt and the next one means the rate moved
    again. ``ran`` is how many cases produced ``executions``, which is what
    turns a total into a rate.
    """
    if accepted is None:
        return None  # accepted ``unknown``: there is nothing to measure against
    so_far = spent(executions)
    if so_far <= accepted:
        return accepted
    print(
        f"\nspent ${so_far:.2f}, which passes the ${accepted:.2f} you accepted."
        f" {len(remaining)} case(s) have not run: {', '.join(remaining)}"
    )
    if ask is None:
        raise Refused(
            f"stopping: ${so_far:.2f} spent against ${accepted:.2f} accepted,"
            " and --accept-cost leaves no hand here to accept more"
        )
    projected = so_far + (so_far / ran) * len(remaining) if ran > 0 else so_far
    return _accept_by_typing(
        Estimate(
            label="estimated",
            amount_usd=projected,
            lines=(
                (
                    f"${so_far:.2f} spent over {ran} case(s), and"
                    f" {len(remaining)} to run at the same rate"
                ),
            ),
        ),
        ask,
    )
