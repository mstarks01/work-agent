# 18. A refused quote is replaced by the source's own nearest span

- **Status**: accepted
- **Date**: 2026-08-27
- **Amends**: [ADR 0002](0002-finding-level-attribution.md), whose ladder is
  unchanged and now has a rung after it.
- **Relates to**: [ADR 0017](0017-a-groundless-claim-costs-its-entry.md).

## Context

The ladder in `analysis_service.grounding` decides whether a quote is present in
the source it names. It is pinned, and its module docstring refuses a
similarity threshold twice: a threshold accepts the one measured fabrication, a
stitched sentence, and a threshold cannot be explained to a submitter.

A stronger model tidies a span more often than a weaker one: it changes a
preposition, drops an article, fixes a plural. The ladder refuses each of those,
correctly, because the words are not in the document. After ADR 0017 that
refusal costs a claim its place when the quote is its only ground. The question
is whether code can put the real words back.

## Decision

**A refused quote is offered to a repair rung, which replaces it.** The rung
finds the window of the source nearest to the quote and hands that window
back. The ground then carries the submitter's words. The agent's words go into a
`RepairedQuote` mark with the similarity that licensed the replacement.

**The rung accepts nothing.** This is the difference from the threshold the
ladder refuses. A threshold would let the model's words stand as a citation. The
rung discards the model's words and cites the document. What the report shows
as a quote is always text the ladder verifies.

**Candidates are windows of the quote's word count, up to two more.** Never
fewer: a tidy drops or swaps words, so the span it came from is as long or
longer, and a shorter window that wins on ratio has cut a word the quote
carried. The stitched sentence is seven words cut from a twenty-word span, so
no candidate comes near it, and the tests pin that case. A quote marking a cut
with `…` is never repaired: each fragment is a span of its own.

**The threshold is 0.9 and not yet measured.** At the corpus median of 80
characters it allows at most eight characters of difference. The eval sweep now
reports a `repaired_rate` per framework, and that number is what moves it.

## Consequences

**The viewer shows the replacement beside the ground**, with the agent's words,
so a reader can see what changed.

**A replacement can say something different.** A long quote that drops a
"not" is within the threshold, and the rung will put the "not" back. The claim
then rests on a span that contradicts it. That is the correct outcome for the
report — the submitter's words are shown — and the critic reads the replaced
span, so the claim is its to reject. The mark makes the difference visible.

**Framework parity.** The rung runs in the shared fan-in on every quote ground
of every package. A framework whose claims often carry one quote and nothing
else gains the most, because a repaired quote is a claim not dropped. A
framework whose claims usually carry a catalog ground beside the quote gains a
better citation and nothing else.
