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

**Native structured output is preferred where it works, and each tier falls
back to a more traditional format where it does not.** The schema always
reaches the model. Only the mechanism changes. Every vendor gets the same
ladder, best first:

1. `native`: the provider's own structured-output field;
2. `forced_tool`: the schema as a tool's parameters, with the call forced;
3. `offered_tool`: the same tool, offered, so the model chooses to call it;
4. `prompt`: the schema stated in the request text, and the answer read as
   plain JSON. One fence around the whole answer is removed, because a model
   without a constraint often writes one.

At build time the probe removes each rung the pair does not support.
`forced_tool` is also removed where the tier sets `thinking`, because
Anthropic refuses a forced tool together with extended thinking. `prompt`
takes every model, so under `auto` no pair is refused for how it carries a
schema.

**Each tier selects which rungs it allows.** `structured_output` in
`config/sampling.toml` takes `auto`, `native` or `tool`, and `auto` is the
default. `auto` allows every rung. `native` allows `native` alone and keeps the
refusal for a deployment that wants the guarantee. `tool` allows the two tool
rungs, for a model that the price map marks native and the provider refuses.
An explicit `native` or `tool` is part of the **Execution Identity**. `auto` is
not, because under it the ladder follows from the vendor, the model and the
installed library, which the identity already hashes.

**A provider refusal moves the tier down one rung, at run time.** A 400 error,
or OpenRouter's 404 when no upstream serves the request, that matches a rule in
`retry.SCHEMA_REFUSALS` sends the same request again on the next rung. The
rules cover a refused schema field on every vendor's spelling, a grammar too
large, a refused forced tool, and a model that takes no tools. The refusal is a
property of the `(vendor, model)` pair, so the tier stays on the lower rung for
the life of the process. Any other error stays a failure, and so does a refusal
on the last rung.

**Three safeguards keep the tool path equal to native:**

1. **A shared schema re-ask.** A response that fails its schema goes back to
   the same model once, with the validation errors. It serves every node that
   binds a schema, on every rung.
2. **One schema text on every rung.** A table keyed by vendor and model
   family names where the schema keywords a model refuses are rewritten,
   because a gateway such as OpenRouter serves Claude beside models that
   take the keywords. One function moves them into the field description
   before litellm sees the schema. The pydantic validators still
   apply every bound on arrival.
3. **The path is a recorded fact.** Each node execution records the path it
   used, any switch, and its re-ask count. `run.py schema-paths` reads the
   three fields off archived reports.

## Consequences

**Quality equality is a claim, not a result.** The safeguards are built and
verified offline. A paired run on one model, native against tool on the same
cases, decides whether `auto` stays the default. Until it runs, every statement
about the tool path says that its quality effect is unmeasured.

**The runtime matcher rests on documentation.** Bedrock does not document the
error for an unsupported model, and no rule was confirmed by a live call. A
refusal that no rule matches fails the call as it did before, and is handled as
a bug when it occurs.

**The `prompt` rung is the weakest format.** It is the format that
`constrain_output = false` measured as a dead run: the model fenced its JSON and
left out required fields. The rung adds what that run lacked: the schema stated
in the request, the fence removed, and the schema re-ask. Whether that is
enough is not measured. The rung is used only where every better rung is
refused.

**A fingerprint names an explicit path.** Setting a tier to `native` or `tool`
re-baselines its **Blessed** identities, because the path decides what the node
could answer. `auto` leaves them where they are.

**The re-ask adds cost where it fires.** It is charged to the node, as
`repair` is today.
