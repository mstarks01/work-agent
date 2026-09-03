"""The estimate gate: informed, affirmative consent before a sweep spends.

There is no spend ceiling (#334). A contributor may spend any amount. The gate's
job is to make sure they know what they are accepting, know how good that number
is, and accept it in a way a rote hand cannot.

Every amount carries one of three labels, always.

``recorded``
    A merged Baseline with this exact configuration recorded this actual. It is
    a real number rather than a guess.
``estimated``
    Price-map arithmetic over another Baseline's token counts, repriced with
    this run's models. It is a best guess.
``unpriced``
    No number exists, either because a tier's model is absent from the price
    map, or because no merged Baseline exists to calibrate from. It is never a
    zero.

The estimate says which Baseline it read, and what it does not know. A borrowed
number rests on somebody else's token counts, so the lender is chosen for what
it ran rather than for where it sorts; see :data:`COMPARABLE_ON`. Whatever the
lender still does not share with this run is printed beside the figure. Where
the price map now disagrees with what that Baseline recorded, one line names the
model and both prices. That is #331's whole alarm, and the only one, because the
repository pins litellm exactly and a CI check would fail honest history the day
after somebody bumps the pin.

Acceptance is typing the amount back. An enter or a ``y`` never proceeds. The
mechanism is rote-proof because the number changes with the configuration. A
script states its own number with ``--accept-cost <usd>``, and the run refuses
when the estimate exceeds it. ``--accept-cost unknown`` is how a script accepts
a cost nobody can state. The kinds must match: a number does not answer an
unpriced estimate, and ``unknown`` does not answer a stated one.

The run holds the contributor to what they accepted. Between cases, and never
inside one, :func:`hold` compares the spend so far to the accepted amount. A
terminal re-prompts with the new number, and the sweep continues on a fresh
typed acceptance. Under ``--accept-cost`` no hand is present, so the run stops.
This is not a ceiling by the back door: the number is the contributor's own, and
the stop only holds them to it.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from analysis_service.budgets import retried_prompt_tokens
from analysis_service.markdown_loader import RESOLVE_ERRORS
from analysis_service.report import NodeRun, TokenUsage
from evals.harness import baseline
from evals.harness.artifact import load_artifact
from evals.harness.prices import UnitPrices, price_calls, unit_prices

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
    """The billable calls in a set of node runs, ready for pricing.

    **A retried node bills more than its usage says**, and this asks
    :func:`~analysis_service.budgets.retried_prompt_tokens` rather than
    answering for itself. It used to answer for itself by not asking at all:
    the failed attempts were priced at nothing while `measured_tokens` charged
    them, so a sweep offered a contributor $9.75 against a $25.35 bill at the
    shipped `attempts = 3`. The number a person types back to accept has to be
    the number they pay.

    The retried prompt is a second call at the same model's prompt rate, with no
    completion, which is what a failed attempt actually was.
    """
    calls = []
    for run in executions:
        if run.usage is None or run.model is None:
            continue
        requested = run.requested_model or run.model
        calls.append((run.model, requested, run.usage))
        retried = retried_prompt_tokens(run)
        if retried:
            calls.append(
                (
                    run.model,
                    requested,
                    TokenUsage(prompt_tokens=retried, total_tokens=retried),
                )
            )
    return calls


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


#: What a Baseline must share with this run for its token counts to mean
#: anything here, in the order a reader should care about.
#:
#: A table rather than a chain of ``if``s, so a sixth identity component is a
#: row. Each entry is a component of the Baseline identity and the sentence
#: that says what differing about it does to the counts.
COMPARABLE_ON: tuple[tuple[str, str], ...] = (
    (
        "frameworks",
        (
            "ran a different framework selection, and a selection decides how"
            " many lane agents each case fires"
        ),
    ),
    (
        "corpus_digest",
        (
            "ran a different corpus, and the corpus decides how many cases run"
            " and how long each one is"
        ),
    ),
    (
        "repo_commit",
        "ran different prompt text, which moves every node's input size",
    ),
)


def _differences(identity: dict[str, Any], lender: dict[str, Any]) -> tuple[str, ...]:
    """Which of :data:`COMPARABLE_ON` this lender does not share with the run."""
    return tuple(
        reason
        for (field, reason), differs in zip(
            COMPARABLE_ON, _mismatches(identity, lender), strict=True
        )
        if differs
    )


def _mismatches(identity: dict[str, Any], lender: dict[str, Any]) -> tuple[bool, ...]:
    """One flag per :data:`COMPARABLE_ON` row, in the table's own order.

    Sorted on directly, so the **order of the table is the precedence**: a
    lender that shares the framework selection beats one that does not,
    whatever else it differs on. Counting the flags instead would make a
    mismatched selection trade against a mismatched commit, and those are not
    worth the same — the selection decides how many agents run, the commit
    only how long their instructions are.
    """
    return tuple(
        _normalised(lender.get(field)) != _normalised(identity.get(field))
        for field, _ in COMPARABLE_ON
    )


def _normalised(value: object) -> object:
    """A list compares as a set: block order is not a difference in workload."""
    return sorted(str(item) for item in value) if isinstance(value, list) else value


@dataclass(frozen=True)
class Borrowed:
    """One lender's repriced counts, and what it did not share with this run."""

    amount_usd: float
    source: str
    differs: tuple[str, ...] = ()


def _recorded_prices(manifest: dict[str, Any]) -> dict[str, UnitPrices]:
    """The unit prices the calibrating Baseline recorded, one entry per model.

    Every sweep of one Baseline priced the same models, so the rows repeat
    once per sweep and keying by model collapses them back.
    """
    return {
        str(entry["model"]): UnitPrices.from_json(entry)
        for sweep in manifest.get("sweeps", [])
        for entry in sweep.get("cost", {}).get("unit_prices", [])
        if entry.get("model")
    }


def _price_drift(manifest: dict[str, Any]) -> tuple[str, ...]:
    """Where the price map now disagrees with what this Baseline recorded.

    #331's whole alarm, and deliberately the only one: the repo pins litellm
    exactly, so the map moves when somebody bumps the pin and then every
    older Baseline drifts at once. A CI check would fail honest history the
    day after a bump, so the drift is disclosed at the one moment it matters
    — to the person about to accept a number calibrated from that Baseline.

    The repricing already keeps drift out of the arithmetic. What the line
    answers is why the figure differs from the recorded cost sitting in the
    Baseline the contributor can go and read.
    """
    lines = []
    for model, recorded in sorted(_recorded_prices(manifest).items()):
        current = unit_prices(model)
        if current is None or _same_rates(recorded, current):
            continue
        lines.append(
            f"{model}: this Baseline recorded {_rates(recorded)}; the price map"
            f" now says {_rates(current)}, per million tokens"
        )
    return tuple(lines)


def _rates(prices: UnitPrices) -> str:
    """Every rate :func:`_same_rates` compares, so a line never shows two equal pairs."""
    cached = (
        "no cache rate"
        if prices.cache_read_per_token is None
        else f"${prices.cache_read_per_token * 1e6:.2f} cached"
    )
    return (
        f"${prices.input_per_token * 1e6:.2f} in /"
        f" ${prices.output_per_token * 1e6:.2f} out / {cached}"
    )


def _same_rates(one: UnitPrices, other: UnitPrices) -> bool:
    """Whether two price sets agree on every rate, cache-read included."""
    return (
        one.input_per_token == other.input_per_token
        and one.output_per_token == other.output_per_token
        and one.cache_read_per_token == other.cache_read_per_token
    )


def _recorded_actual(manifest: dict[str, Any]) -> float | None:
    """A Baseline's mean recorded actual across its sweeps."""
    # A committed manifest is a contributor's file, so its number gets the same
    # question the flag gets: a non-finite or negative `actual_usd` renders as
    # an acceptable offer and prices the whole estimate off it.
    actuals = [
        value
        for sweep in manifest.get("sweeps", [])
        if (value := baseline.recorded_usd(sweep.get("cost", {}))) is not None
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
                lines += _price_drift(manifest)
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

    borrowed = _borrow(baselines, routes, identity)
    if borrowed is None:
        lines.append(
            "no merged Baseline to calibrate token counts from, so no amount"
            " can be stated"
        )
        return Estimate(label="unpriced", amount_usd=None, lines=tuple(lines))
    lines.append(
        f"token counts borrowed from {borrowed.source}, each node repriced at"
        " this run's route for its tier"
    )
    lines += [
        f"the closest Baseline is not this configuration: {borrowed.source} {reason}"
        for reason in borrowed.differs
    ]
    lines += _price_drift(_manifest_named(baselines, borrowed.source))
    return Estimate(
        label="estimated", amount_usd=borrowed.amount_usd, lines=tuple(lines)
    )


def _manifest_named(
    baselines: Sequence[tuple[Path, dict[str, Any]]], name: str
) -> dict[str, Any]:
    """The manifest the borrowing chose, for the lines that describe it."""
    return next(
        (manifest for _, manifest in baselines if manifest.get("name") == name), {}
    )


def _borrow(
    baselines: Sequence[tuple[Path, dict[str, Any]]],
    routes: dict[str, str],
    identity: dict[str, Any],
) -> Borrowed | None:
    """The most comparable Baseline's recorded token counts, at this run's prices.

    **The counts are borrowed, not the total.** Each of the lender's nodes
    ran on some tier; this run has its own route for that tier; so the node's
    recorded usage is priced at that route's rates and the sweep's cost falls
    out of :func:`~evals.harness.prices.price_calls` — the one costing path
    the recorded actual and CI's re-check already go through.

    **Which Baseline lends is a decision, not the directory order.** Token
    counts are counts of work, so a lender that ran a different framework
    selection or a different corpus did a different amount of it: a selection
    naming fewer packages fires a lane agent for fewer lanes on every case,
    and lending its counts understates by about that ratio. So the candidates
    are ranked by :data:`COMPARABLE_ON` and the best one lends — and whatever
    it still does not share is returned, because a number the contributor
    cannot see the caveat on is a number they cannot weigh.

    Ties break on the Baseline name, so one tree gives one answer.

    The mean across the chosen lender's sweeps, because one sweep is one
    sample and ``TUNING.md`` records what single-run numbers cost this
    project before.
    """
    ranked = sorted(
        baselines,
        key=lambda pair: (
            _mismatches(identity, pair[1].get("identity", {})),
            str(pair[1].get("name")),
        ),
    )
    for directory, manifest in ranked:
        priced = []
        for entry in manifest.get("sweeps", []):
            amount = _reprice(directory, str(entry.get("artifact", "")), routes)
            if amount is not None:
                priced.append(amount)
        if priced:
            return Borrowed(
                amount_usd=sum(priced) / len(priced),
                source=str(manifest.get("name")),
                differs=_differences(identity, manifest.get("identity", {})),
            )
    return None


def _reprice(
    directory: Path, artifact_name: str, routes: dict[str, str]
) -> float | None:
    """One recorded sweep's usage, as if this run's models had served it.

    ``None`` rather than a number whenever the answer would rest on a guess:
    an artifact that will not load, a node provenance never recorded, a tier
    this run does not route, or a route the price map misses. The caller
    tries the next Baseline, and states ``unpriced`` when none answers —
    never a zero, which is the one outcome #334 forbids outright.

    The name is joined here rather than by the caller, so the join and the
    check that it stayed inside the directory sit together. ``artifact`` comes
    off a committed manifest, and an absolute value replaces the left side of a
    join rather than extending it — the same shape found in two other places
    this audit round.
    """
    path = directory / artifact_name
    try:
        resolved = path.resolve()
        if not resolved.is_relative_to(directory.resolve()):
            return None
        artifact = load_artifact(resolved)
    except RESOLVE_ERRORS:
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
    # `float()` takes "inf" and "nan", and neither is a number of dollars.
    # Both satisfy any stated estimate, because `x > inf` and `x > nan` are
    # False -- so either one turns this gate off and, passed to `hold`, turns
    # it off for every case boundary the sweep has left. A negative one accepts
    # a spend by claiming to accept less than nothing.
    if not math.isfinite(accepted) or accepted < 0:
        raise Refused(
            f"--accept-cost {accept_cost!r} is not an amount of money;"
            " state the dollars you accept"
        )
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
