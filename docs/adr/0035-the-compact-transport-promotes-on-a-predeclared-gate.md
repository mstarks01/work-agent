# 35. The compact transport promotes on a predeclared gate

- **Status**: accepted
- **Date**: 2026-09-15
- **Effort**: [#938 — stage compact extraction output behind a SystemModel adapter](https://github.com/mstarks01/work-agent/issues/938),
  stage 4
- **Builds on**: [ADR 0016](0016-token-caps-are-drift-alarms.md), which says a
  cap here alarms rather than rations; `evals/TUNING.md` steps 3 and 5, which
  say price a fix before spending and read a band off runs already paid for

## Context

The compact extraction transport ships behind `ANALYSIS_COMPACT_EXTRACTION`,
off. It replaces each element ID with a short response-local `ref` and expands
the result into the same **System Model** in code. What it is worth is a
measurement nobody has made.

Two measurements exist, and neither settles it.

**Offline.** `evals/bench/deterministic.py transport` puts the saving at 10.1%
of the emitted characters over the thirteen blessed corpus models. That is an
emission size, in characters, on hand-corrected models.

**The first live run**, on 2026-09-15: case `01-payments-checkout`, five
full-route extractions and two compact, `openrouter/openai/gpt-5.6-luna` pinned
to `openai/flex`, repo `971fc7b`, $0.0166.

| | full (n=5) | compact (n=2) |
| --- | --- | --- |
| emitted tokens, completion less reasoning | mean 1,969, sd 297 | 1,776 and 1,870 |
| `extract` wall clock | mean 136 s, range 101–160 s | 109 s and 85 s |
| prompt tokens | 6,098 | 6,116 |

The compact mean sits 0.49 sd below the full mean on emitted tokens. The
offline estimate predicted about 200 tokens saved and the full route's own
spread is ±300, so **the effect this run existed to measure is inside the
spread of the run**. The latency column is worse than unresolved: the tiers
file pins `openai/flex`, whose queueing put a 60-second range on a single node.

That run also found a defect and paid for itself doing it — a flat ref
namespace could not tell an external entity from its own trust zone, fixed by
scoping each reference to the field that reads it. Which is the point: this
record is about what the *next* run may conclude, not about whether running was
worth it.

**The hazard this record exists to remove.** A 10% effect measured against a
±15% spread will, over enough slices and enough figures, produce a favourable
number. Choosing the slice after seeing the numbers is how a null result
becomes a promotion. #938 asks for the margins, the repetitions and the
thresholds to be fixed before any comparison is examined, and an issue comment
is not where a decision rule stays fixed.

## Decision

**The compact transport promotes only on the gate below, and the gate is
written before the run that answers it.**

### The unit and the repetitions

Five sweeps per arm, each over the whole thirteen-case corpus, in
`--mode extraction`. Sixty-five extractions an arm. A 13-case extraction sweep
is about $0.04 and takes 6.7 minutes at `--cases-in-flight 13`, so the pair is
about **$0.33 and half an hour**.

One case cannot answer this. The first run's spread is the reason: a per-case
reading of a 10% effect needs an n the corpus gives for free.

**The baseline arm runs first**, from the same tree, on the same tiers file and
the same node map. Nothing but `ANALYSIS_COMPACT_EXTRACTION` differs between
the arms.

### What decides it

One **primary** figure, declared as one so that no second figure can be
promoted to it afterwards:

> **Emitted tokens per case**, taken as `completion_tokens - reasoning_tokens`
> from `node_usage.extract`, paired by case and summed across the corpus.

The compact arm passes when its five-sweep mean is **at least one standard
deviation of the full arm's five-sweep spread below that arm's mean**. Half a
standard deviation is the bar `evals/TUNING.md` uses to sink a prompt edit;
this is a cost claim rather than a quality one, and a cost claim that cannot
clear a whole standard deviation is not worth a second route in the tree.

### The quality margins, non-inferiority

The compact arm must not be worse than the full arm by more than the margin
below, on the corpus mean of each figure, over the same five sweeps:

| figure | margin |
| --- | --- |
| `recall` | 0.03 |
| `endpoint_recall` | 0.03 |
| `interaction_recall` | 0.03 |
| `precision` | 0.05 |
| `zone_partition_agreement` | 0.03 |
| scored field agreement | 0.05 |

Margins rather than equality, because both arms run a model and neither
reproduces itself exactly. They are absolute and one-sided: the compact arm
may be better by any amount and that buys it nothing, because a transport is
not supposed to improve extraction and a route that did would be changing
something this record does not cover.

**A per-case veto.** One case falling below its own baseline spread vetoes the
change, whatever the corpus mean does, on `evals/TUNING.md` step 5's rule.

### The failure rates

| rate | ceiling |
| --- | --- |
| conversion failure — the payload did not expand | **0** of 65 |
| `duplicate-ref` | **0** of 65 |
| first-pass validity, compact arm | within 1 sd of the full arm's |

The first two are zero because they are faults the full route cannot have.
A transport that costs a whole extraction now and then is paying for its
saving with the most expensive thing in the run, and the fix for one is code
rather than tolerance.

### Latency

**Latency is not in this gate.** The pinned upstream is `openai/flex`, whose
per-call spread is larger than the whole effect, and measuring a claim the
instrument cannot resolve is how a spread becomes a result. #938 asks for a
demonstrated latency improvement before promotion; this record narrows that to
the emitted-token claim and defers latency to its own run, off flex, which must
happen before any traffic moves.

So the flag stays off on this gate alone. **Passing it earns a measured
saving and an argument, not a rollout.**

### What a failure means

If the primary figure does not clear one standard deviation, the route does
not promote, the benchmark findings stay, and stage 3 — evidence pooling — is
the next thing measured rather than the next thing built. #938's own definition
of done says so: "if measurement shows no worthwhile benefit, retain the
benchmark findings and do not promote the route."

## Consequences

**The gate can be applied by someone who did not run the sweep.** Every figure
named here is one an artifact already carries: `node_usage.extract` for the
primary figure, `mode_output[]` for the quality figures,
`structural_failures` for the rates. `run.py stability` reads a band off a
repeat set and `run.py compare` reads a delta, so the arms are compared with
the instruments the repo already has rather than with a script written after
the numbers arrived.

**The thresholds are arguable and are not measured.** One standard deviation is
a convention, and the quality margins are set from what the first run's
overlap looked like rather than from a power calculation. That is stated rather
than hidden: a reader who wants different numbers should change them here,
before the run, and say why.

**This record does not cover stage 3.** Evidence pooling changes what the wire
form carries rather than how it is spelled, and #938 keeps it separable so its
benefit and its failure rate are measured apart from this. It needs its own
gate.

**The cost of predeclaring is one review cycle.** The alternative is cheaper
and is what this exists to prevent: reading six figures off two arms, finding
the one that favours the route, and calling it the result.

## Alternatives considered

**Leave the thresholds in the issue.** #938 permits it, and it is where the
first run's numbers went. An issue comment is not versioned with the tree, is
not reviewed, and nothing fails when a later comment contradicts an earlier
one. The decision that matters — what counts as success — belongs where a
reviewer sees it change.

**Make the gate a table the harness reads.** It is this repository's strongest
habit, and it is the right shape for a rule applied many times. This one is
applied once, to one route, and the machinery would outlive the question. The
figures are already in the artifact; a reader applies the gate from here.

**Gate on latency, as #938 asks.** The instrument cannot resolve it on the
pinned upstream, and running anyway would produce a number that looks like an
answer. Deferring it to its own run states the gap instead of hiding it, and it
stays a precondition of moving traffic.
