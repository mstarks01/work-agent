# An OpenRouter response repeats the slug it was asked for (#806)

Probed 2026-09-11 against `litellm==1.97.0`, repo `d16a6c4`, and OpenRouter's
live API. Produced by `probe_openrouter_served_model.py` in this directory,
plus two unbilled `GET`s recorded below. Four billed completions of one output
token each, $0.000105 total.

## The question

`VENDORS["openrouter"].served_trust` read `provider_reported`. That was right
about the translator: litellm's OpenRouter config inherits the OpenAI
transformation and fills `model_response.model` from the response body's
`model`. Nobody had measured what OpenRouter puts in that field.

## The answer

**It repeats the slug the request named.** Four calls, two slugs, no
divergence.

| requested slug | body `model` | equal |
| --- | --- | --- |
| `anthropic/claude-opus-4.7` | `anthropic/claude-opus-4.7` | yes |
| `meta-llama/llama-3.3-70b-instruct` (×3) | `meta-llama/llama-3.3-70b-instruct` | yes |

The Claude call is the one that decides it, because Anthropic's own API answers
with a dated build. That build existed and OpenRouter did not pass it through:
its generation record names `anthropic/claude-4.7-opus-20260416` in
`provider_responses[0].model_permaslug` while the completion body said
`anthropic/claude-opus-4.7`. The two fields disagree in the same response, so
the body's `model` is an echo rather than a build that happened to match.

## The upstream is named, in fields nothing here reads

OpenRouter states which upstream served. It just does not state it where
litellm looks:

| field | where | value on the Claude call |
| --- | --- | --- |
| `model` | completion body | `anthropic/claude-opus-4.7` — the request |
| `provider` | completion body | `Claude Platform on AWS` |
| `provider_name` | `GET /generation` | `Claude Platform on AWS` |
| `model_permaslug` | `GET /generation` | `anthropic/claude-4.7-opus-20260416` |

litellm's OpenAI-shaped transformation reads `model` and ignores `provider`, so
the served half of an **Execution Identity** carries the echo and discards the
evidence sitting beside it. The generation record needs a second call, arrives a
few seconds later, and is not billed.

That a Claude slug was served by **AWS** rather than by Anthropic is the point
in one line. A direct `anthropic` route could not have produced that response,
and the served identifier does not say so.

## What this repository does with it, as of 2026-09-11

`provider` is recorded per node execution as `served_upstream` and a sweep's
tier summary reports every upstream that answered
(`TierIdentity.served_upstreams`). It is evidence, never identity: it enters no
fingerprint, and a direct route records none rather than its own vendor.

`ServedTrust` stays two-valued. The generation record stays here, in this probe,
rather than on the job path. The reasoning is on
[#815](https://github.com/mstarks01/work-agent/issues/815).

## One slug spans many upstreams

`GET /models/<slug>/endpoints`, unbilled, on the same date:

- `anthropic/claude-opus-4.7` — **8 endpoints, 5 providers**: Claude Platform on
  AWS, Azure, Google Vertex (global, us, europe), Amazon Bedrock (global,
  eu-west-1), Anthropic.
- `meta-llama/llama-3.3-70b-instruct` — **12 endpoints, 11 providers**, and the
  tags carry a numeric format: `deepinfra/turbo`, `novita/bf16`,
  `akashml/fp8`, `crusoe/bf16`, `coreweave/fp16`, and more.

So one slug reaches builds that are not the same numerics, and the served
identifier is byte-identical across all of them. This is the measured backing
for `routes_to_one_provider=False`, which `evals/harness/baseline.py` already
reads to refuse naming a **Baseline** after an `openrouter` route.

Three calls on the Llama slug all landed on DeepInfra, so **this probe did not
observe one slug reaching two providers in consecutive calls.** The endpoint
listing is the evidence for the span, not the routing observed here.

## What this ruled

`served_trust` moved to `requested_echo`. The field's own docstring already
settled which way to read it: the reader asks whether the served build adds
evidence the requested build did not, and "an echo answers no whatever the
reason for it." The reason is new — for `vertex` and `bedrock` the translator
is why, and here the provider is why — and the worth is the same.

The `openrouter` entry in `tests/test_identity.py` is a claim about the
**value**, so its canned body now repeats the request, as Bedrock's names no
model at all. One limit follows and is accepted: that test would keep passing if
OpenRouter started naming the build, because it asserts an echo against an
echoing body. This file is the dated record that the body is a measurement
rather than a convenience.
