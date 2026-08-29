# 2. A finding cites the words that justify it

- **Status**: accepted
- **Date**: 2026-08-04
- **Effort**: [#76 — tie every finding back to the input text that justifies it](https://github.com/mstarks01/work-agent/issues/76)
  (nine resolution comments), cutover planned in
  [#85](https://github.com/mstarks01/work-agent/issues/85)

## Context

A threat named the elements it affected and nothing else. Whether it rested on
something a submitter actually said, on an attribute nobody ever stated, or on
nothing at all was unanswerable from the report — and a reviewer's first
question about any finding is *why do you think so*.

Every threat now carries **Grounds**: one or more records, each a `quote`, an
`unknown-attribute` or a `derived-fact`. Most of what that entailed is recorded
where it belongs — the vocabulary in `CONTEXT.md`, the payload in
[`docs/Report-Schema.md`](../Report-Schema.md), the rules in docstrings, the
reasoning in the nine resolution comments. Four decisions are here because each
lost to a *measurement*, which means each looks reversible to someone reading
only the code, and each would be reversed for a good-looking reason.

## Decisions

### `Ground` is one flat model, not a discriminated union

A tagged union is the better shape and the more Pythonic one: it forbids a
nonsense combination — a quote carrying an `element_id` — in the schema itself,
so a constrained model cannot generate one. It lost to the fact that **provider
schema compilers are the unpredictable part of this system**.

`config/sampling.toml` already records a vendor refusing `SystemModel`'s
compiled grammar as *too large*, and `constrain_output = false` measured as no
fallback at all: unconstrained, the model fences its JSON and omits required
fields. So the cost of a grammar a vendor chokes on is a **dead run, not a
degraded one** — and this schema rides in `DraftThreats` on the `strong` tier
for six category agents whose vendor is selected independently. `oneOf` is the
construct with the thinnest and least uniform support across those vendors.

The portable object wins, and a validator buys the guarantee back on arrival:
each `kind` declares its required fields, and every field belonging to another
kind is forbidden rather than ignored, because the two readings of such a record
differ and nothing downstream could choose between them. `Verdict` is already
exactly this pattern, so the repo has one answer to "tagged variant in a
provider-facing schema" rather than two.

What is given up, plainly: the schema no longer prevents a mis-shaped `Ground`
at generation time. It is caught at `join_drafts`, and there is no re-ask path
for a category agent's drafts — so a mis-shape fails the job.

### An unverified quote is marked, not fatal

Quote grounds are checked mechanically against the submitted bytes. The
good-looking reversal is *we should not ship unverified evidence*; the
arithmetic is what refuses it.

Across the 12 corpus cases' 206 element excerpts — the closest available proxy,
since they come from the same "quote verbatim" instruction over the same kind of
input — the pinned ladder rejects exactly one, and that one is a genuine
fabrication. **Zero false rejections in 206 is not evidence of zero**: the Rule
of Three bound is 1.46% per quote, and at 18.7 threats per job that is a **24%
chance of killing a job** over a cosmetic mismatch. Dropping the entry instead
is not available either — `grounds` is `min_length=1`, so dropping the last one
produces an invalid draft or deletes the finding, and silently removing a threat
is the worst outcome a security tool can have.

So an unverifiable quote renders, marked. A threat where *nothing* verifies
still fails the job: that is a finding with no machine-checkable justification
at all, which is a different thing from one citation of several not matching.

> **Amended by [ADR 0017](0017-a-groundless-claim-costs-its-entry.md).** A
> claim where nothing verifies is now dropped and marked as a groundless claim.
> The per-entry mark above is unchanged.

The marks live on `StrideReport` as references, deliberately outside `Ground`:
a verification field on an agent-owned model is a field the agent could set
about its own honesty.

The ladder itself is pinned, and stops one rung short of punctuation-blindness.
Whitespace collapse carries the entire result (78.2% false rejections to 1.0%);
punctuation-blindness recovers nothing the markdown rung does not and spends
real precision. It is not a similarity threshold: best-window similarity puts
the lowest accepted quote at 0.986 and the highest rejected one at 0.963 —
separable by 2.3 points, fitted to a single negative example — and every
threshold a human would pick by intuition (0.90, 0.95) accepts that negative: a
quote that excised a span and stitched a subject onto a predicate, unmarked,
producing a sentence the source never contains. A deterministic ladder is also
explainable to a submitter ("this word is not in your document"); a threshold is
not.

### Neither the critic nor the eval judge receives submitter text

*The critic should check the quotes* is the obvious-looking addition. Three
things are against it.

The critic's call is already the graph's longest, and a job may carry 100 KiB of
sources. The check is mechanical and already runs for free one node earlier.
And putting the source text into the critic gives the node that holds **verdict
power** a second, competing evidence base — while widening the graph's largest
prompt-injection surface (OWASP LLM01) to the node whose output decides what
ships. `critic.md`'s own rule against re-spending judgement on a check code has
run applies here to the letter.

The same argument holds one node over for the eval judge: its `unsupported`
bucket is defined **model-relative** — a threat asserting a fact the System
Model does not support — while grounds are **submitter-text-relative**. Judging
them properly means putting the case's source text into the judge call, which
redefines the metric mid-flight and resets every recorded baseline. The
adjudication payload is unchanged, and grounds do not leak into it.

Consequently the *only* thing that reads a quote against its source is
`analysis_service.grounding`, at `join_drafts`.

### `source_excerpt` survives beside `grounds`

It looks redundant — two verbatim quotes from the same submission, on records
that reference each other — and it is not. An excerpt answers **why this element
is in the model**; a ground answers **why this threat was raised**. Removing
either leaves a question the report can no longer answer, and deriving one from
the other produces a citation that is always *a* quote and frequently the wrong
one.

The corollary is what makes it safe: the category agents and the critic read a
model with `source_excerpt`, `source_label` and `source_speaker` **stripped**.
Wording an instruction against reusing an excerpt would not have worked — an
agent cannot align its quote to an excerpt it can no longer see. `notes` stays,
because the prompt binds it and because a quote lifted out of a note is
something the critic can spot, and the ladder cannot: it *is* verbatim submitter
text.

## Consequences

- **Breaking, with no shim.** `grounds` is required under `extra="forbid"`, so
  the schema and the 18 prompt exemplars could not land separately;
  `schema_version` is **2.0**, earned by `nodes[].node` moving from
  `analyst_<category>` to `analyze_<category>`.
- The report can now fail a job for a reason that is not a model error: a threat
  whose grounds none verify, or a `Ground` whose shape is wrong.
- Reference threats in `evals/` **do not** gain grounds. They would be graded by
  nothing, and `reference.py`'s own rule is that the corpus does not carry what
  it does not grade — so a threat promoted from a real run into the corpus
  arrives with grounds and loses them. See `evals/BLESSING.md`.
- The eval-side `ungrounded_rate` is renamed `unsupported_rate`. It meant
  *hallucinated*, which is a genuinely different thing from *carrying no
  grounds*, and after this cutover the old spelling reads as the wrong one.
- `analyze.md` now templates the job's source text, so a category agent's prompt
  carries untrusted input under the same fenced-block rule the extractor uses
  ([ADR 0001](0001-sources-replace-description.md)).
- Quote grounds put more submitted prose, and more speaker names, onto a screen.
  Whether that residue needs a redaction policy is
  [out of scope](https://github.com/mstarks01/work-agent/issues/76) here rather
  than answered.
