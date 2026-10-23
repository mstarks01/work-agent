# 100. The extraction gate repairs an excerpt before it refuses it

- **Status**: accepted
- **Date**: 2026-10-23
- **Extends**: [ADR 0018](0018-the-repair-rung.md),
  whose rung now runs at the extraction gate too.

## Context

The extraction gate refuses an element whose `source_excerpt` the ladder does
not find in the source it cites (`unverifiable-excerpt`). The refusal sends the
model to the one `repair` pass, a paid model call. Of the 165 extraction-gate
issues archived under `evals/runs/` and `evals/emissions/`, 121 are this code,
which makes it the main reason the repair pass runs.

Most of these excerpts are tidied, not invented: the model changes the last
punctuation mark or drops a word. ADR 0018 already answers this case for a
lane's quote ground. The rung puts the source's own nearest span in place of
the quote and records what the agent wrote. It ran only in the fan-in.

The 121 issues hold 39 distinct excerpts. Given to the shipped
`repair_prepared` against today's corpus sources, 21 are repaired, and
`meaning_moved` names a change in none of them. Of the 18 that are not
repaired, 10 mark a cut with `...` or `…`, which the rung refuses by design.

## Decision

**The gate gives a refused excerpt to the rung before it refuses it.**
`repair_excerpts` runs on a model that arrived from a model, after
`normalize_element_ids` has snapped each label to the job's spelling. Where the
rung finds a span and `meaning_moved` names nothing, the element carries the
span and an `ExcerptRepair` records what the model wrote, the similarity and
whether the scan was complete. Every other excerpt is refused as before, so the
repair pass still sees it.

**A repair that changes a negation or a number is refused here**, where ADR
0018 keeps it in the fan-in. In the fan-in, the critic reads the replaced span
beside the agent's words and can reject the claim. Nothing reads an element
excerpt that way after the gate, so the repair pass is the reader that can
settle it.

**An authored model is never rewritten.** Only the normalizing path runs the
rung, the same path that snaps a label.

**The record is the model's own field.** `SystemModel.excerpt_repairs` sits
beside `capabilities`, outside `EmittedSystemModel`, so neither extraction node
can write it. The lane and repair views leave it out: an agent reads the
model's facts, not what an extraction wrote before the gate. The repair
pass's overlay keeps the record of each element it restores, and the gate
refuses a record that names no element.

## Consequences

**Fewer repair calls.** About half of the archived `unverifiable-excerpt`
excerpts no longer need one. How many repair calls this saves live is
unmeasured.

**The report shows the submitter's words.** A consumer that reads an element
excerpt as "what the extraction wrote" must read `excerpt_repairs` too, as it
reads `repaired_quotes` for a ground.

**The 0.9 threshold is still not measured** as a boundary between a tidy and
an invention. The evidence here is that no archived repair at the gate moved a
negation or a number.

**Framework parity.** Extraction and its gate run once for every framework, so
every package reads the repaired model.
