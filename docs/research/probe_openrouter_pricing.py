"""Measure whether an OpenRouter route has a unit price (#822).

`evals/harness/prices.py` reads one input rate and one output rate per model
out of litellm's offline map, and the estimate gate spends a contributor's
consent against the product. That works for a vendor that serves a model
itself. An aggregator fronts many upstream endpoints behind one slug, so the
question is whether any single pair of rates can describe such a route at all.

Three legs, each answering something the others cannot:

1. **Coverage.** How many of OpenRouter's listed slugs the pinned map carries
   under an ``openrouter/`` key. An absent key is the honest miss the harness
   already reports as ``unpriced``.
2. **Agreement.** For the slugs it does carry, whether the map's rates equal
   the rates OpenRouter lists today. This is a claim about a third party's
   catalogue, so only the live listing settles it.
3. **Spread.** For a sample of slugs, the listed rate against the rates of the
   individual endpoints the slug reaches. This is the leg that decides the
   question: where the endpoints disagree, no one rate is the route's price,
   and the listed one is the cheapest of them.

Leg 3 outranks legs 1 and 2. A perfectly synchronised map still states a
number that under-states whenever a dearer endpoint serves, and which endpoint
serves is not knowable before the call.

**The map this reads is the pinned one.** litellm fetches its cost map from
``BerriAI/litellm@main`` at import unless ``LITELLM_LOCAL_MODEL_COST_MAP`` is
set first, and :mod:`analysis_service.model_gate` sets it. So the module-level
import of the harness above is what makes leg 2 a statement about the map the
estimate path actually reads. A probe that imported ``litellm`` on its own
would measure the network's map instead, and the two differ: the fetched map
carries 244 of these slugs where the pinned one carries 82.

Every request here is an unbilled ``GET`` against the public catalogue, so the
probe needs no credential and costs nothing. Run it from the repository root::

    uv run python docs/research/probe_openrouter_pricing.py

This is a probe, not a gate. It asserts nothing and fails no build.
"""

from __future__ import annotations

import argparse
from typing import Any

import httpx

from analysis_service.vendors import vendor_for
from evals.harness.prices import unit_prices

#: The base litellm's pinned ``main.py`` falls back to for this provider. It is
#: a literal there rather than a constant, so it is repeated rather than read.
API_BASE = "https://openrouter.ai/api/v1"

#: Slugs whose endpoint listing leg 3 reads. Chosen for range rather than for
#: popularity: a family served by one upstream, a family served by eleven, and
#: three in between. The second is the one that answers the question.
SAMPLE_SLUGS = (
    "anthropic/claude-opus-4.7",
    "meta-llama/llama-3.3-70b-instruct",
    "deepseek/deepseek-v4-pro",
    "qwen/qwen3-235b-a22b-2507",
    "z-ai/glm-4.6",
)

#: The catalogue's price keys beside the map's, in the order a reader compares
#: them. The map spells them per token and so does the catalogue.
RATE_FIELDS = (
    ("prompt", "input_cost_per_token"),
    ("completion", "output_cost_per_token"),
)

#: Below this the two numbers are the same number rounded differently.
TOLERANCE = 0.005

TIMEOUT = httpx.Timeout(60.0)


def _get(client: httpx.Client, path: str) -> Any:
    response = client.get(f"{API_BASE}{path}")
    if response.status_code != 200:
        raise RuntimeError(
            f"GET {path}: HTTP {response.status_code}: {response.text[:300]}"
        )
    return response.json()["data"]


def _rate(pricing: dict[str, Any], field: str) -> float | None:
    """One rate from a catalogue entry, or None where it states no number.

    OpenRouter writes ``-1`` where a price varies by route — the ``auto`` slugs
    do — and that is not a rate. It is reported as absent rather than compared,
    because a negative price would pass a ratio check and mean nothing.
    """
    try:
        value = float(pricing.get(field))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if value < 0 else value


def coverage(catalogue: dict[str, Any], mapped: dict[str, Any]) -> list[str]:
    """Leg 1: which listed slugs the pinned map carries, and which it does not."""
    carried = sorted(slug for slug in catalogue if f"openrouter/{slug}" in mapped)
    print("\n=== leg 1: coverage ===")
    print(f"  listed slugs                : {len(catalogue)}")
    print(f"  carried under openrouter/   : {len(carried)}")
    print(f"  no entry in the pinned map  : {len(catalogue) - len(carried)}")
    stale = [
        key
        for key in mapped
        if key.startswith("openrouter/") and key[len("openrouter/") :] not in catalogue
    ]
    print(f"  keys for slugs no longer listed: {len(stale)}")
    return carried


def agreement(
    carried: list[str], catalogue: dict[str, Any], mapped: dict[str, Any]
) -> None:
    """Leg 2: where the map's rate differs from the rate OpenRouter lists."""
    print("\n=== leg 2: agreement with the live listing ===")
    rows = []
    for slug in carried:
        entry = mapped[f"openrouter/{slug}"]
        for field, key in RATE_FIELDS:
            live = _rate(catalogue[slug]["pricing"], field)
            listed = entry.get(key)
            if live is None or listed is None:
                rows.append((slug, field, live, listed, "no number on one side"))
                continue
            listed = float(listed)
            if live == listed == 0:
                continue
            if listed == 0 or abs(live - listed) / max(live, listed) > TOLERANCE:
                ratio = "undefined" if not listed else f"{live / listed:.2f}x"
                rows.append((slug, field, live, listed, ratio))
    print(f"  slug/rate pairs compared    : {len(carried) * len(RATE_FIELDS)}")
    print(f"  pairs that disagree         : {len(rows)}")
    print(f"  distinct slugs affected     : {len({row[0] for row in rows})}")
    for slug, field, live, listed, note in rows:
        live_text = "none" if live is None else f"{live:.6g}"
        listed_text = "none" if listed is None else f"{listed:.6g}"
        print(f"    {slug} {field}: listed {live_text}, map {listed_text} ({note})")


def spread(client: httpx.Client, catalogue: dict[str, Any]) -> None:
    """Leg 3: the listed rate against the endpoints the slug actually reaches."""
    print("\n=== leg 3: endpoint spread behind one slug ===")
    for slug in SAMPLE_SLUGS:
        if slug not in catalogue:
            print(f"  {slug}: no longer listed")
            continue
        endpoints = _get(client, f"/models/{slug}/endpoints")["endpoints"]
        rates = sorted(
            rate
            for endpoint in endpoints
            if (rate := _rate(endpoint["pricing"], "prompt")) is not None
        )
        listed = _rate(catalogue[slug]["pricing"], "prompt")
        providers = {endpoint.get("provider_name") for endpoint in endpoints}
        print(f"\n  {slug}")
        print(f"    endpoints / providers : {len(endpoints)} / {len(providers)}")
        print(f"    listed input rate     : {listed}")
        print(f"    endpoint input rates  : {rates[0]:.6g} .. {rates[-1]:.6g}")
        if rates[0]:
            print(f"    dearest over cheapest : {rates[-1] / rates[0]:.2f}x")
        if listed:
            print(f"    dearest over listed   : {rates[-1] / listed:.2f}x")


def harness_answer() -> None:
    """What the shipped estimate path now returns for these routes."""
    print("\n=== what the harness answers ===")
    vendor = vendor_for("openrouter")
    print(f"  routes_to_one_provider = {vendor.routes_to_one_provider}")
    for slug in SAMPLE_SLUGS:
        route = vendor.route(slug)
        print(f"  unit_prices({route!r}) = {unit_prices(route)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-spread",
        action="store_true",
        help="run legs 1 and 2 only, one request in total",
    )
    args = parser.parse_args()

    with httpx.Client(timeout=TIMEOUT) as client:
        from litellm import model_cost

        catalogue = {entry["id"]: entry for entry in _get(client, "/models")}
        carried = coverage(catalogue, model_cost)
        agreement(carried, catalogue, model_cost)
        if not args.skip_spread:
            spread(client, catalogue)
    harness_answer()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
