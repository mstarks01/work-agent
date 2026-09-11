# An OpenRouter route has no unit price, and an exact reported one (#822)

Probed 2026-09-11 against `litellm==1.97.0`, repo `242ceb0`, and OpenRouter's
live catalogue. Produced by `probe_openrouter_pricing.py` in this directory.
Every call is an unbilled `GET`, so the whole measurement cost nothing.

## The question

`evals/harness/prices.py` reads one input rate and one output rate per model,
and the estimate gate spends a contributor's typed consent against the product.
`_bare_name` was narrowed so an aggregator route could not reach a third
party's entry. It left the gateway's *own* entry pricing the route. Nobody had
asked whether one pair of rates can describe an aggregator route at all.

## The answer

**It cannot, and the error points down.** One slug reaches many endpoints at
many rates, and the catalogue lists the cheapest of them.

| slug | endpoints / providers | listed input rate | endpoint range | dearest over listed |
| --- | --- | --- | --- | --- |
| `meta-llama/llama-3.3-70b-instruct` | 12 / 11 | 1.00e-07 | 1.00e-07 – 1.04e-06 | **10.40x** |
| `deepseek/deepseek-v4-pro` | 16 / 15 | 9.48e-07 | 8.69e-07 – 2.00e-06 | 2.11x |
| `z-ai/glm-4.6` | 5 / 5 | 4.30e-07 | 4.30e-07 – 6.00e-07 | 1.40x |
| `qwen/qwen3-235b-a22b-2507` | 11 / 10 | 2.20e-07 | 8.75e-08 – 2.50e-07 | 1.14x |
| `anthropic/claude-opus-4.7` | 8 / 5 | 5.00e-06 | 5.00e-06 – 5.50e-06 | 1.10x |

Which endpoint serves decides the charge, and the caller does not know that
before the call. So the listed rate is a floor rather than a price, and
`UnitPrices` says it never states a floor as a price.

## The pinned map drifts on top of that

The pinned map carries 82 of the 443 listed slugs under an `openrouter/` key.
The other 361 have no entry, which the harness already reported as `unpriced`.

Of the 82 it carries, **39 disagree with the rate OpenRouter lists today** — 73
of the 164 slug-and-rate pairs, 31 of them under-stating. The worst is
`mistralai/mixtral-8x22b-instruct` output at 6.5e-07 against the 6.0e-06
listed, an under-statement of 9.23x. Thirteen further keys name slugs the
catalogue no longer lists.

Two catalogue entries price at `-1`, meaning the rate varies by route:
`openrouter/auto` and `openrouter/bodybuilder`. The map prices both at `0.0`.
No route reaches that zero today, because `Vendor.route` doubles the prefix and
the doubled string matches no key, but a silent zero sitting in the map is what
the module's first rule is about.

## What this ruled

`unit_prices` refuses a route whose vendor sets
`routes_to_one_provider=False`, before it reads the map. The caller reports it
as `unpriced`, and `--accept-cost unknown` is how a script accepts that. The
refusal is keyed off the registry property rather than off a vendor's name, so
the next aggregator is refused the day its row lands.

This costs the `openrouter` row nothing it had. A **Baseline** already refuses
to be named after such a route, for the same reason measured here: the spread
that makes the price unknowable is the spread that makes the sweeps
incomparable.

## An exact number exists, after the call

OpenRouter reports what it charged, and the pinned translator already carries
the value.

| where | field | what it holds |
| --- | --- | --- |
| request body | `usage.include` | litellm sets it to `True` on every OpenRouter request |
| response body | `usage.cost` | what OpenRouter charged the account, in credits |
| response body | `usage.cost_details.upstream_inference_cost` | what the upstream provider charged, on BYOK requests only |
| `_hidden_params` | `additional_headers["llm_provider-x-litellm-response-cost"]` | where litellm puts `usage.cost` |

`litellm/llms/openrouter/chat/transformation.py` lines 163 and 207 are the two
halves. Nothing in this repository reads `_hidden_params`, so the exact figure
arrives in the process and is discarded in favour of arithmetic that cannot
reach it.

## A custom provider key splits the charge in two

Under bring-your-own-key, OpenRouter charges 5% of what the same call would
cost on its own credits, and the upstream provider bills the contributor's own
account for the tokens. So `usage.cost` is the fee alone, and the rest sits in
`cost_details.upstream_inference_cost` — the field litellm does not read. The
fee is also waived below a monthly list-price threshold, $25,000 on
pay-as-you-go, so it is not a fixed fraction of the tokens either.

Read from OpenRouter's own documentation rather than measured: this repository
holds no BYOK deployment, and nothing in it declares one. Recording an actual
for such a deployment needs both fields and a declaration of which mode ran.
That is #822, and it is open.
