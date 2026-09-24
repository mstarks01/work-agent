# Attribution

## The phases

Six. Print them, with the graph nodes each one owns and the instruments that
read each:

```bash
uv run python -m evals.harness.run phases
```

To attribute one node read off an artifact, ask for it by the name the artifact
spells:

```bash
uv run python -m evals.harness.run phases --node analyze_stride_spoofing
```

The table lives in `evals/harness/audit.py`, and a test compares it against the
graph's own node names. Do not restate it in a report; print it.

## What an attribution claims

Two facts per failure, and they are different:

- **The earliest observed failure.** The first phase that did not carry the
  thing the report needed.
- **Every other contributing failure.** A repaired upstream phase often reveals
  an independent downstream one, and a report that names only the earliest
  hides that.

`unknown` is a legitimate attribution. Record it rather than guessing.

## What an attribution may not claim

- **A missing final output is not an extraction defect.** It is a missing final
  output. Anything more needs a reading of the stage artifacts.
- **A whole-stage oracle substitution locates a broad bottleneck. It does not
  prove a local mechanism.** `oracle` is exactly this: a perfect reading put
  through the deterministic path. What it loses, no route can keep; what it
  keeps says nothing about what a model would emit.
- **One stochastic successful replay is suggestive, not causal.** Where the
  run-to-run spread could change the decision, use paired comparisons and
  repeat.
- **A ceiling is not a bound.** `losses` charges each STRIDE miss to one cause
  and `attribution` charges each ASVS miss to one stage. Each row records one
  observation and hides no other cause. A gain outside the class is a signal to
  read, never noise.

## Controlled substitutions

Each one holds every other input fixed and changes exactly one thing. Label all
of them **diagnostic**: they read reference material, so their numbers are
never production performance and never go in a scored comparison.

| To test | Substitute | Then |
| --- | --- | --- |
| Extraction lost the fact | the signed reference fact, via the oracle bundle | replay the deterministic descendants |
| Preparation misrouted it | the omitted assessment unit into the lane's material | re-run that lane only |
| Analysis never wrote it | a reviewed grounded candidate, injected | see whether it survives to the report |
| Fan-in destroyed it | the candidate the merge dropped, restored | re-run the critic and the report |
| Criticism killed it | the evidence missing from the critic's input | re-run the critic on the saved candidates |
| Reporting dropped it | a correct adjudicated result | re-assemble the report |

`oracle` and `bottleneck` already implement the first of these. The others need
a capture or a replay seam that may not exist yet — when one does not, that is
an extension ticket, not a reason to guess.

To compare a whole route against a corrected input, rather than one stage,
use `references/three-conditions.md`. It holds the three conditions, the
leakage controls and the table that says what each result supports.

## Reading the loss blocks

A scored artifact carries both, and they are where a ceiling comes from.

**`losses`** is STRIDE's, and charges each missed reference to one of seven
causes, decided in this order: `verb` (a claim cites the same place with
another action), `merged`, `misfiled` (right finding, wrong lane), `critic` (a
draft cited it and was rejected), `place` (a rule led there, nothing was
drafted), `unled` (nothing sent the lane there). `fan-in` sits between
`critic` and `place`: the lane proposed the finding, and the fan-in removed it
or narrowed its elements off the place. See `evals/harness/losses.py`.

**`attribution`** is ASVS's, and charges each wrong requirement to a stage. See
`evals/harness/attribution.py`. Extraction is deliberately not a stage there: a
scored sweep runs in `analysis` mode over a blessed model, so no element can be
missing from the graph.

Both split a critic charge in two. A row carrying a non-empty `re_ask` was lost
by the bounded re-ask rather than by the pass that reasoned about it. Price
those against the re-ask.

## The fix classes each block prices

| Loss class | What a fix in its class is | Priced on |
| --- | --- | --- |
| `verb` | an exemplar or an equivalence edit | the `verb` rows in that lane |
| `unled` | a candidate rule | the `unled` rows |
| `fan-in` | a grounding change in the lane, or a change to the fan-in rule its `fan_in_reasons` name | the `fan-in` rows, split by `fan_in_effect` |
| `place` | a prompt or a rule that leads the lane further | the `place` rows |
| `critic` | a critic prompt or an evidence change | the `critic` rows, split by `re_ask` |
| `misfiled` | a routing fix | the `misfiled` rows |
| `merged` | a corpus ruling, not a lane fix | the corpus |

Read `evals/TUNING.md` step 3 before pricing anything. Do not price a targeted
fix against the corpus total: the total's band is about four must-finds of 129,
and no ceiling written so far clears it.
