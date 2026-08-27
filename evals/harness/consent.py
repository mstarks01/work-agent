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

from evals.harness import baseline
from evals.harness.artifact import load_artifact
from evals.harness.prices import UnitPrices, price_calls, unit_prices
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


def _merged_baselines(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    """Each merged Baseline as ``(directory, manifest)``.

    The directory travels with the manifest because borrowing reads the
    sweeps' own artifacts, which sit beside it — the manifest records what a
    sweep cost, never how many tokens bought it.
    """
    directory = root / "evals" / "baselines"
    baselines = []
    for path in sorted(directory.glob("*/baseline.json")):
        try:
            baselines.append(
                (path.parent, json.loads(path.read_text(encoding="utf-8")))
            )
        except (OSError, json.JSONDecodeError):
            continue  # verify() is what refuses a broken Baseline, not the gate
    return baselines


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
    baselines = _merged_baselines(root)
    lines: list[str] = []
    for _, manifest in baselines:
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

    borrowed = _borrow(baselines, routes)
    if borrowed is None:
        lines.append(
            "no merged Baseline to calibrate token counts from, so no amount"
            " can be stated"
        )
        return Estimate(label="unpriced", amount_usd=None, lines=tuple(lines))
    amount, source = borrowed
    lines.append(
        f"token counts borrowed from {source}, each node repriced at this"
        " run's route for its tier"
    )
    return Estimate(label="estimated", amount_usd=amount, lines=tuple(lines))


def _borrow(
    baselines: Sequence[tuple[Path, dict[str, Any]]], routes: dict[str, str]
) -> tuple[float, str] | None:
    """Another Baseline's recorded token counts, at this run's prices.

    **The counts are borrowed, not the total.** Each of the lender's nodes
    ran on some tier; this run has its own route for that tier; so the node's
    recorded usage is priced at that route's rates and the sweep's cost falls
    out of :func:`~evals.harness.prices.price_calls` — the one costing path
    the recorded actual and CI's re-check already go through.

    What this replaces was a ratio: the lender's dollar total scaled by the
    summed unit prices of this run's models over the lender's. That ignored
    the token mix, so swapping a base-tier model moved the guess as much as
    swapping the strong-tier one, though the strong tier carries most of the
    spend. It also had to be told not to divide by the sweep count. Pricing
    the counts directly needs neither correction and is less arithmetic, not
    more — which is why the docstring's old case for crudeness does not argue
    against it. A model of the cost would be elaborate; this is the
    subtraction and multiplication the recorded number already used.

    The mean across the lender's sweeps, because one sweep is one sample and
    ``TUNING.md`` records what single-run numbers cost this project before.
    """
    for directory, manifest in baselines:
        priced = [
            amount
            for entry in manifest.get("sweeps", [])
            if (amount := _reprice(directory / str(entry.get("artifact", "")), routes))
            is not None
        ]
        if priced:
            return sum(priced) / len(priced), str(manifest.get("name"))
    return None


def _reprice(path: Path, routes: dict[str, str]) -> float | None:
    """One recorded sweep's usage, as if this run's models had served it.

    ``None`` rather than a number whenever the answer would rest on a guess:
    an artifact that will not load, a node provenance never recorded, a tier
    this run does not route, or a route the price map misses. The caller
    tries the next Baseline, and states ``unpriced`` when none answers —
    never a zero, which is the one outcome #334 forbids outright.
    """
    try:
        artifact = load_artifact(path)
    except (OSError, ValueError):
        return None

    calls: list[tuple[str, str, TokenUsage]] = []
    rates: dict[str, UnitPrices] = {}
    for node, usage in sorted(baseline.usage_of(artifact).items()):
        executions = artifact.provenance.node_runs.get(node)
        if not executions:
            return None
        route = routes.get(executions[-1].tier)
        if route is None:
            return None
        prices = unit_prices(route)
        if prices is None:
            return None
        rates[route] = prices
        # The route stands where the served model would: in the hypothetical
        # this prices, the route is what serves the call.
        calls.append((route, route, usage))
    return price_calls(calls, rates=rates).total_usd if calls else None


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
