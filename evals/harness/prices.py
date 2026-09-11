"""Unit prices for a model, from litellm's offline map, and the one cost rule.

The map ships inside the litellm package (#324), so reading it costs no network
call and pins to the installed version. It serves the estimate side only. A
merged Baseline's manifest records the unit prices it was priced with, and CI's
check is pure arithmetic over those recorded numbers, because an honest artifact
must not start failing when the package updates its prices (#323).

There is never a silent zero. A model the map does not carry prices at ``None``,
and every caller states that: the manifest lists it under ``unpriced``, and the
estimate gate refuses without an explicit acceptance (#334). A suffixed served
build, such as ``gpt-5.6-luna``, is the expected miss. The stated fallback is
its requested route, and the manifest records that substitution under
``fallbacks``.

**There is never a silent price off another vendor either, and that is newer.**
A lookup may fall back to a spelling of the same model; it may not fall back to
a different route. :func:`_bare_name` is where the line sits, and an
aggregator's two-segment identifier is what drew it.

That rule reaches the cache-read rate too, which is absent from most of the map.
It is carried as ``None`` rather than zero, and :attr:`UnitPrices.cached_rate`
bills those tokens at the full input rate. An unknown discount priced as no
discount over-states rather than under-states.

The cost rule is spelled once, so submit and CI compute the same number:
uncached prompt tokens at the input rate, cached prompt tokens at the cache-read
rate, and completion tokens at the output rate. Reasoning tokens are not added
on top. The vendor that bills them separately reports them inside
``completion_tokens``, and adding the separate field as well would double-count
exactly there. Where a vendor reports them only outside, the recorded actual is
a floor, and this module says so rather than pretending otherwise.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from analysis_service.report import TokenUsage


@dataclass(frozen=True)
class UnitPrices:
    """One model's per-token USD rates, as the map states them."""

    model: str
    input_per_token: float
    output_per_token: float
    #: ``None`` where the map states no cache-read rate, which is most of it:
    #: 1820 of litellm's 3212 entries price input and output and say nothing
    #: about cached reads. A zero there would be a silent zero — the one thing
    #: this module says it never writes — and it bills a 90%-cached call at a
    #: seventh of a plausible cost.
    cache_read_per_token: float | None

    @property
    def cached_rate(self) -> float:
        """What a cached prompt token costs: the stated rate, or the full one.

        **An unknown discount is priced as no discount.** A provider that
        publishes no cache-read rate is one this repository cannot show a
        saving for, so the cached tokens bill at the input rate. That
        over-states rather than under-states, which is the only safe
        direction for a number somebody consents to spend against — and it is
        the honest reading, because a provider with no published cache price
        most likely applies no discount.
        """
        if self.cache_read_per_token is None:
            return self.input_per_token
        return self.cache_read_per_token

    def cost(self, usage: TokenUsage) -> float:
        """The one cost rule; see the module docstring for what it excludes."""
        uncached = max(usage.prompt_tokens - usage.cached_prompt_tokens, 0)
        return (
            uncached * self.input_per_token
            + usage.cached_prompt_tokens * self.cached_rate
            + usage.completion_tokens * self.output_per_token
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "input_per_token": self.input_per_token,
            "output_per_token": self.output_per_token,
            "cache_read_per_token": self.cache_read_per_token,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> UnitPrices:
        return cls(
            model=str(raw["model"]),
            input_per_token=float(raw["input_per_token"]),
            output_per_token=float(raw["output_per_token"]),
            cache_read_per_token=(
                None
                if raw.get("cache_read_per_token") is None
                else float(raw["cache_read_per_token"])
            ),
        )


@dataclass(frozen=True)
class Priced:
    """What a set of calls cost, with every hole in the pricing named."""

    unit_prices: tuple[UnitPrices, ...]
    unpriced: tuple[str, ...]
    fallbacks: tuple[tuple[str, str], ...]
    total_usd: float


def price_calls(
    calls: Iterable[tuple[str, str, TokenUsage]],
    rates: dict[str, UnitPrices] | None = None,
) -> Priced:
    """Price ``(served, requested, usage)`` triples — the one costing path.

    The Baseline's recorded actual, CI's re-check and the pre-run estimate all
    come through here, so a disagreement between them is impossible by
    construction rather than by three matching edits.

    Priced by the served build; where the map misses it — a suffixed build
    like ``gpt-5.6-luna`` is the expected miss (#324) — the requested route is
    the stated fallback, and a model neither answers for lands in
    ``unpriced``. Pass ``rates`` to price against recorded unit prices instead
    of the live map, which is what keeps CI's arithmetic from rotting when the
    package updates its prices (#323).
    """
    lookup = (lambda model: rates.get(model)) if rates is not None else unit_prices
    priced: dict[str, UnitPrices] = {}
    unpriced: set[str] = set()
    fallbacks: dict[str, str] = {}
    total = 0.0
    for served, requested, usage in calls:
        prices = lookup(served)
        if prices is None:
            prices = lookup(requested)
            if prices is not None:
                fallbacks[served] = requested
        if prices is None:
            unpriced.add(served)
            continue
        priced[prices.model] = prices
        total += prices.cost(usage)
    return Priced(
        unit_prices=tuple(priced[model] for model in sorted(priced)),
        unpriced=tuple(sorted(unpriced)),
        fallbacks=tuple(sorted(fallbacks.items())),
        total_usd=total,
    )


def _bare_name(model: str) -> str | None:
    """``model`` without its provider prefix, or ``None`` if that is not what remains.

    **One leading segment, and only when nothing but a model name is left.**
    The fallback exists because the map keys some models both ways —
    ``vertex_ai/gemini-2.5-pro`` is absent and ``gemini-2.5-pro`` is there — and
    that reasoning holds exactly as far as one prefix.

    An aggregator's identifier carries a vendor of its own, so a route like
    ``openrouter/deepseek/deepseek-v4-pro`` has two segments in front of the
    name. Taking the text after the *last* slash stripped both and priced the
    route off a third party's entry. Measured against OpenRouter's live
    catalogue on 2026-09-11: of 444 slugs, 82 had an ``openrouter/`` key, 39
    were priced correctly by coincidence and **6 were priced wrongly**, the
    worst under-stating by 2.2x — ``deepseek/deepseek-v4-pro`` billed at
    4.35e-07 against the 9.48e-07 OpenRouter charges. An under-stated figure is
    the one direction :class:`UnitPrices` says it never goes, and it reached the
    consent screen with the route's own name printed beside it.

    So a remainder that still carries a slash is not this route's model name,
    and there is no price here. The caller reports it as ``unpriced``, which is
    this module's answer for a question it cannot answer — and the 39 that were
    right were right by coincidence rather than by construction.
    """
    _, separator, rest = model.partition("/")
    if not separator or "/" in rest:
        return None
    return rest


def unit_prices(model: str) -> UnitPrices | None:
    """The map's rates for ``model``, or None where the map has no entry.

    Tried as given and then bare of its provider prefix, because the map keys
    some models both ways and a tier string may carry either. A hit without
    an input or output rate counts as a miss: half a price is not a price.

    :func:`_bare_name` decides what "bare of its provider prefix" means, and it
    refuses to answer for an aggregator's two-segment identifier rather than
    reaching another vendor's entry.
    """
    from litellm import model_cost  # deferred: importing litellm is slow

    bare = _bare_name(model)
    entry = model_cost.get(model) or (None if bare is None else model_cost.get(bare))
    if not isinstance(entry, dict):
        return None
    input_rate = entry.get("input_cost_per_token")
    output_rate = entry.get("output_cost_per_token")
    if input_rate is None or output_rate is None:
        return None
    return UnitPrices(
        model=model,
        input_per_token=float(input_rate),
        output_per_token=float(output_rate),
        cache_read_per_token=(
            None
            if (cached := entry.get("cache_read_input_token_cost")) is None
            else float(cached)
        ),
    )
