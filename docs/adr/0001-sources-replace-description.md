# 1. A job carries sources, not a description

- **Status**: accepted
- **Date**: 2026-08-01
- **Effort**: [#49 — accept call transcripts as job input](https://github.com/mstarks01/work-agent/issues/49)
  (ten resolution comments), implemented per
  [#61](https://github.com/mstarks01/work-agent/issues/61)

## Context

A threat model is built from a conversation, but a job could only carry one
`description`: a single blob someone wrote up *after* the call. That hand-off is
where the evidence died — a hedge became a fact, an admitted gap became silence,
and a `source_excerpt` quoted the write-up rather than anything a person said.

A job now takes an ordered, non-empty list of **Sources**, each `{kind, label,
text}`. Most of what that entailed is recorded where it belongs: the vocabulary
in `CONTEXT.md`, the reasoning in the ten tickets, the behaviour in code and
tests. Three decisions are here because they have consequences inside this repo
that neither a glossary nor a docstring can carry, and because someone will
otherwise reverse them for good-looking reasons.

## Decisions

### The budget is in bytes, not tokens

A job is bounded by a total UTF-8 byte count and a source count, both in
`config/resilience.toml`. Tokens are the obvious unit and are wrong here: each
**Model Tier** selects its vendor independently, so a token budget would make
the *public contract* depend on which vendor a deployment happens to run. The
same submission would be accepted by one install and refused by its neighbour,
and a caller could not tell in advance which. Bytes are something the caller can
measure themselves.

There is no per-source cap, only a total. A per-source cap forbids only shapes
the total already permits, and it invites the service to blame one source for a
budget the whole submission overspent — which is why the over-budget error names
no culprit and carries a per-label breakdown instead.

### Sources carry equal weight

Nothing confers authority: not list order, not `kind`, not the caller. Where two
sources disagree, extraction **records** the disagreement rather than settling
it — `unknown` with both claims quoted in `notes` where the schema allows it, a
legal value plus an `assumptions` entry where it does not.

The temptation is a precedence rule, and every available one is unsound. Order
was defined as presentation-only, so it carries no claim. Nothing in a job
carries a **date**, so `kind` cannot proxy for recency. And choosing between two
positive claims is *analysis*, which extraction is forbidden to do and which
would be invisible downstream: an analyst cannot tell an adjudicated value from
a stated one.

The rule needs its guards to be safe. Silence is not a claim — one source
stating a value while another is simply quiet is not a disagreement — and a more
specific claim refines a compatible one. Without those, "no precedence" reads as
"anything not confirmed by every source is disputed" and flattens every
multi-source model to `unknown`.

### One fence per source, sized to its content

Every caller byte reaches a model inside a fenced block whose fence is one
backtick longer than the longest backtick run in that block (CommonMark's own
rule), so no submitted text can close the block it sits in. The `label` rides
*inside* the fence: it is caller-controlled, so it can never sit on a marker
line. What is left outside is the index, the count, and a fixed register phrase.

A `<source-NONCE label="...">` envelope was tried first and is unbuildable
against this contract: `"` and `>` are legal label bytes, and rescuing the
attribute needs either escaping — which mutates a citation key — or a charset
restriction on labels that was refused.

The rendering is a pure function called from `GraphExecutor.run`, the one seam
both the service and the eval harness cross. Seeding raw input text is refused
outright, which is what makes it impossible to show the same job to a model two
different ways.

The same rule applies one node downstream: `json.dumps` escapes quotes and
newlines but **not** backticks, and the System Model carries caller words by
rule, so the fences around `{system_model}` and `{previous_model}` are sized to
their content too.

## Consequences

- **Breaking, with no shim.** `description` is gone from the wire, from
  `StrideEngine.analyze`, and from the package's public exports. An old-shaped
  submission is refused rather than adapted.
- `config/resilience.toml` is **v3**; a v2 file fails to load, so every
  deployment edits its file rather than inheriting bounds nobody chose.
- Adding a `kind` means editing the enum and `prompts/extract.md` together —
  `kind` selects the register rendered around the text.
- A `source_excerpt` must cite a label its job carried, enforced by the validity
  gate. This is the only gate rule that reads data from outside the model.
- `notes` and `source_excerpt` ship raw caller words in the report JSON. No
  viewer renders them today; whether that residue is the integrator's problem or
  the service's is
  [left open](https://github.com/mstarks01/work-agent/issues/49) rather than
  answered here.
