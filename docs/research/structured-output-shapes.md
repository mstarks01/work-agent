# Structured-output schema shapes one route refuses

**Ticket:** [#942](https://github.com/mstarks01/work-agent/issues/942).
**Date:** 2026-09-14. **Probed against:** repo `5d2cf5a`, `litellm==1.97.0`,
`openrouter/google/gemini-3.5-flash-lite` served by Google AI Studio.
**Probe:** `probe_structured_output_shapes.py`.

Evidence, not current behaviour. The shapes this service sends today are in
`tests/test_vendor_neutrality.py`.

## The question

While benchmarking `google/gemini-3.5-flash-lite` for #926, three assertion runs
on that route returned `INVALID_ARGUMENT` from Google AI Studio. An extraction
run on the same route, in the same session, succeeded — 10,602 tokens, 8.6 s. So
the route was reachable and the credential worked. Something about one schema
decided it.

`litellm` reports the refusal as an OpenRouter error carrying the upstream body:

```
litellm.BadRequestError: OpenrouterException - {"error":{"message":"Provider
returned error","code":400,"metadata":{"raw":"{\n  \"error\": {\n    \"code\":
400,\n    \"message\": \"Request contains an invalid argument.\",\n
\"status\": \"INVALID_ARGUMENT\"\n  }\n}\n","provider_name":"Google AI
Studio","is_byok":false,...
```

The message names no field, so the cause has to be bisected.

## What the route accepted, by schema

| schema | root capped object arrays | route |
|---|---|---|
| `SystemModel` (extract, repair) | none | accepted |
| `CatalogProposal` (assert) | `assertions(maxItems=500)` | `INVALID_ARGUMENT` |
| `stride.proposals` (lane agents) | `claims(maxItems=400)` | `INVALID_ARGUMENT` |
| `stride.rulings` (critic, recritic) | none | accepted |
| `asvs.proposals` (lane agents) | `claims(maxItems=400)` | `INVALID_ARGUMENT` |
| `asvs.rulings` (critic, recritic) | none | accepted |

The column in the middle predicts the column on the right exactly.

## Where the cap has to sit

| shape | route |
|---|---|
| root array of objects, capped; nested arrays capped | `INVALID_ARGUMENT` |
| root array of objects, capped; nested arrays uncapped | `INVALID_ARGUMENT` |
| root array of objects, uncapped; nested arrays capped | accepted |
| root array of objects, uncapped; nested arrays uncapped | accepted |
| root array of **strings**, capped | accepted |

**A cap on a root-level array of objects is the shape refused.** A cap on a
nested array of objects is accepted, and so is a cap on a root array of strings —
which is why `SystemModel` runs on this route with five capped `assets` lists.

## At which values

`maxItems` of 8, 100, 256, 400 and 500 were each refused. The same schema with
no `maxItems` was accepted. So it is the keyword's presence, not a value the
route considers too large.

## `anyOf` is not the cause

It was the first suspect, and the first version of #942 named it. These were all
accepted on the same route in the same session:

- a nullable enum (`Literal[...] | None`, which pydantic emits as `anyOf`);
- a nullable nested object;
- `stride.rulings` and `asvs.rulings` entire, both of which carry several.

So the nullable judgement fields a critic record needs cost nothing here.

## What this does not say

- **One route, one model.** The refusal is Google AI Studio's, reached through
  OpenRouter. Whether Vertex AI refuses the same shape is unprobed, and the two
  are separate vendor rows in this service for exactly this kind of reason.
- **Nothing about a JSON Schema draft.** `maxItems` is valid JSON Schema. This
  records what one provider's structured-output surface accepts, which is a
  narrower vocabulary than the schema language.
- **Nothing about the other capped arrays this service sends.** Every nested cap
  was accepted here; a route that refused those too would need its own probe.
