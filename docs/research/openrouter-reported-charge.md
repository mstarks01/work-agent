# OpenRouter states what it charged, and the record now keeps it (#822)

Measured 2026-09-11 against `litellm==1.97.0`, ADK 2.5.0 and OpenRouter's live
API, by `probe_openrouter_reported_charge.py` in this directory. Six billed
completions on `meta-llama/llama-3.3-70b-instruct`, about $0.000015 in total,
plus unbilled `GET /generation` reads.

## The question

`evals/harness/prices.py` refuses to state a unit price for an `openrouter/`
route, because one slug reaches many endpoints at many rates
(`openrouter-pricing.md`). That settles the estimate and leaves the actual: the
arithmetic that priced the run has no rate to multiply by either.

OpenRouter reports what it charged. Two claims stood between that figure and
this repository's record, and neither could be settled offline: whether the
figure arrives at all, and whether litellm files it where
`analysis_service.charges` looks.

## The answer: it arrives, and the record keeps it

One call through the service's own adapter, built by `build_tier_adapters` with
`[charges] openrouter = "direct"`:

| where | field | value |
| --- | --- | --- |
| response body | `usage.cost` | `2.54e-06` |
| `_hidden_params` | `additional_headers["llm_provider-x-litellm-response-cost"]` | `2.54e-06` |
| `_hidden_params` | `response_cost` | `2.54e-06` |
| ADK response | `custom_metadata["reported_charge_usd"]` | `2.54e-06` |

The last row is what `NodeRun.reported_charge_usd` is read from, so the figure
crossed every seam: OpenRouter to litellm, litellm to the capturing client, the
client to the adapter, and the adapter to the executor.

The bound tier's client was `analysis_service.charges.ChargeCapturingClient`,
which is the wiring decision made from the registry and the declared
arrangement rather than from a vendor's name.

## The reader sees the arrangement the provider states

`stated_arrangement_of` over the same live response:

```
stated_arrangement_of(...) -> 'direct'
```

The deployment declared `direct`, the provider said `is_byok: false`, the two
agree and the run proceeded. That is the whole of what a live call can show
here: the disagreeing case needs a second account with an upstream provider key,
and the refusal is exercised offline against a response carrying the other flag.

## The body names the upstream, and it agrees with the generation record

litellm keeps `provider` as a pydantic extra on the response, so the evidence
costs no second call:

```
served_upstream_of(...) -> 'DeepInfra'
custom_metadata['served_upstream'] -> 'DeepInfra'
```

The unbilled generation record for the same call says `provider_name:
DeepInfra`. The two fields agree, which is what makes the free one usable: the
body's `provider` names the same organisation the generation record does, so a
reader that takes the cheap one loses nothing but the dated build — and the
dated build is not something this repository binds. See
[#815](https://github.com/mstarks01/work-agent/issues/815).

## The figure is the whole charge, under this arrangement

`GET /generation` on the same call, unbilled:

| field | value |
| --- | --- |
| `is_byok` | `false` |
| `total_cost` | `2.54e-06` |
| `usage` | `2.54e-06` |
| `upstream_inference_cost` | `0` |
| `provider_name` | `DeepInfra` |

`total_cost` equals the reported `cost` equals the recorded figure, and no
separate upstream charge exists. That is what `ChargeMode.DIRECT` means, and it
is now measured rather than assumed.

The bring-your-own-key half is **not** measured here, because measuring it needs
a second account with an upstream provider key. It rests on OpenRouter's
published terms, recorded in `openrouter-pricing.md`.

## The response does say which arrangement ran, and litellm drops it

`is_byok` appears twice in one response — in the completion body's `usage`
block, and again in the generation record, including per upstream response. So
the arrangement is not only a deployment fact; the provider states it.

litellm's OpenAI-shaped transformation reads `cost` out of that same `usage`
block and ignores `is_byok`, so the statement never reaches this process.

This does not move the design. The declaration stays the mechanism, on the rule
the credential modes already follow: **the mechanism is declared, and the
material is discovered.** A figure that is worth recording only if a provider
also says so is a figure that trusts the provider to describe its own charge.

What it does offer is a **cross-check**, and that is what the field is used
for: a deployment declaring `direct` whose responses carry `is_byok: true` has a
declaration that does not match what ran, so the run stops at the node that
found out. The flag contradicts a declaration and never supplies one.

It is checked in both directions. A `direct` declaration answered as BYOK would
record a routing fee as a whole cost. A `own_upstream_key` declaration answered
as direct moves no number wrongly and is still wrong: the deployment discards a
whole reported charge on every call and reports a cost of nothing.

## One trap, for whoever runs the probe next

The service refuses a completion that stopped at `max_output_tokens`
(`analysis_service.retry`), so a one-token call raises `TruncatedCompletionError`
after the provider has already charged for it. The smallest call this service
will *accept* is one the model finishes on its own. The probe asks for a
one-word reply under a cap of 16.
