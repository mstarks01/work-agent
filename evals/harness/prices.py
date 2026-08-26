"""Unit prices for a model, from litellm's offline map, and the one cost rule.

The map ships inside the litellm package (#324), so reading it costs no
network call and pins to the installed version. It serves the **estimate**
side only: a merged Baseline's manifest records the unit prices it was priced
with, and CI's check is pure arithmetic over those recorded numbers — an
honest artifact must not start failing because the package updated its
prices (#323).

**Never a silent zero.** A model the map does not carry prices at ``None``,
and every caller states that: the manifest lists it under ``unpriced``, and
the estimate gate refuses without an explicit acceptance (#334). A suffixed
served build (``gpt-5.6-luna``) is the expected miss; the stated fallback is
its requested route, and the manifest shows which model each price came from.

The cost rule, spelled once so submit and CI compute the same number:
uncached prompt tokens at the input rate, cached prompt tokens at the
cache-read rate, completion tokens at the output rate. Reasoning tokens are
not added on top — the vendor that bills them separately reports them inside
``completion_tokens``, and adding the separate field too would double-count
exactly there. Where a vendor reports them only outside, the recorded actual
is a floor, and it says so here rather than pretending otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from stride_service.report import TokenUsage


@dataclass(frozen=True)
class UnitPrices:
    """One model's per-token USD rates, as the map states them."""

    model: str
    input_per_token: float
    output_per_token: float
    cache_read_per_token: float

    def cost(self, usage: TokenUsage) -> float:
        """The one cost rule; see the module docstring for what it excludes."""
        uncached = max(usage.prompt_tokens - usage.cached_prompt_tokens, 0)
        return (
            uncached * self.input_per_token
            + usage.cached_prompt_tokens * self.cache_read_per_token
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
            cache_read_per_token=float(raw["cache_read_per_token"]),
        )


def unit_prices(model: str) -> UnitPrices | None:
    """The map's rates for ``model``, or None where the map has no entry.

    Tried as given and then bare of its provider prefix, because the map keys
    some models both ways and a tier string may carry either. A hit without
    an input or output rate counts as a miss: half a price is not a price.
    """
    from litellm import model_cost  # deferred: importing litellm is slow

    entry = model_cost.get(model) or model_cost.get(model.rsplit("/", 1)[-1])
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
        cache_read_per_token=float(entry.get("cache_read_input_token_cost") or 0.0),
    )
