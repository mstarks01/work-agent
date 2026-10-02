# 58. Native structured output is preferred, not required

- **Status**: accepted
- **Date**: 2026-10-02
- **Effort**: [#1408](https://github.com/mstarks01/work-agent/issues/1408)
- **Supersedes**: the build-time refusal of the forced tool call in
  `binding._check_native_structured_output`, which no earlier ADR recorded.
- **Relates to**: [ADR 0002](0002-finding-level-attribution.md), which records
  that a run with no schema is a dead run;
  [ADR 0003](0003-no-privileged-vendor.md), which records the refusal as a
  conformance finding.

## Context

Every LLM node binds an output schema. litellm can carry that schema to a
provider in two ways:

- **Native structured output.** The schema goes in the provider's own field:
  `outputConfig.textFormat` on Bedrock, `output_format` on the Anthropic API,
  `response_format` on OpenAI. The provider constrains decoding, so the answer
  matches the schema.
- **A forced tool call.** litellm defines one tool whose input schema is the
  node schema, and forces the model to call it. The model usually follows the
  schema. Nothing makes it.

The build-time gate refuses the forced tool call. Its stated reason was that
the tool path sends `$defs` unresolved. PR #1407 measured that reason under the
pinned litellm and corrected it: the Bedrock native path also sends `$defs`,
and Bedrock documents internal references as supported. The real difference is
the guarantee. No recorded run measured how often the tool path breaks a
schema.

The refusal now blocks current models. The AWS model cards (read 2026-10-02)
list structured outputs as unsupported on `bedrock-runtime` for Claude Opus
4.7, Opus 5, Sonnet 5 and Opus 5.5. On Bedrock those models can carry a schema
only through a forced tool call, so the newest Opus the Bedrock row can name
is Opus 4.6. A provider can also refuse a native schema on a model that
supports it: `config/sampling.toml` records Anthropic refusing `SystemModel`
as "the compiled grammar is too large".

The goal is that a deployment can always select the latest hosted model on
every vendor, with no loss of report quality.

## Decision

**Native structured output is preferred where it works, and the forced tool
call is an accepted fallback.** The schema always reaches the model. Only the
mechanism changes.

**Each tier selects its path.** `structured_output` in `config/sampling.toml`
takes `auto`, `native` or `tool`, and `auto` is the default. `auto` uses native
output where the pinned library sends it, and the forced tool call elsewhere.
`native` keeps today's refusal for a deployment that wants the guarantee.
`tool` covers a model that the price map marks native and the provider
refuses. A model that refuses the schema parameter outright stays refused on
every setting. An explicit `native` or `tool` is part of the **Execution
Identity**. `auto` is not, because under it the path follows from the vendor,
the model and the installed library, which the identity already hashes.

**A provider refusal moves the tier, at run time.** Under `auto`, a 400 error
that refuses the native schema field or the compiled schema sends the same
request again on the tool path, and the tier stays there for the rest of the
job. Any other 400 error stays a failure.

**Three safeguards keep the tool path equal to native:**

1. **A shared schema re-ask.** A response that fails its schema goes back to
   the same model once, with the validation errors. It serves every node that
   binds a schema, on both paths.
2. **One schema text on both paths.** A vendor-keyed table names the schema
   keywords that each provider refuses. One function moves them into the field
   description before litellm sees the schema. The pydantic validators still
   apply every bound on arrival.
3. **The path is a recorded fact.** Each node execution records the path it
   used, any switch, and its re-ask count, in fields that the eval harness
   reads.

## Consequences

**Quality equality is a claim, not a result.** The safeguards are built and
verified offline. A paired run on one model, native against tool on the same
cases, decides whether `auto` stays the default. Until it runs, every statement
about the tool path says that its quality effect is unmeasured.

**The runtime matcher rests on documentation.** Bedrock does not document the
error for an unsupported model. The first live call on each vendor confirms
the shape, and the confirmed message is recorded beside the matcher.

**A fingerprint names an explicit path.** Setting a tier to `native` or `tool`
re-baselines its **Blessed** identities, because the path decides what the node
could answer. `auto` leaves them where they are.

**The re-ask adds cost where it fires.** It is charged to the node, as
`repair` is today.
