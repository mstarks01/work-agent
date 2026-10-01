# Experiment protocol

## State the claim first

Each claim is one of four kinds, and the kind decides the evidence that can
finish it:

| Claim | Evidence that can finish it | Needs fresh paid output? |
| --- | --- | --- |
| implementation correctness | a reproduction, a regression test through the production function, and the affected deterministic consumers | no |
| behaviour on archived outputs | `score`, `replay`, `assembly`, `descendants` and the other free instruments over the archive | no |
| fresh model behaviour | new calls on the deployment model | yes |
| end-to-end quality or generalisation | a designed run on the shipped route, and the holdout split for generalisation | yes |

A scripted, recorded or replayed response is never new evidence about a model.
It proves what the code does with that response.

## Before an experiment exists

State all seven, in the audit report, before running anything:

1. the observed failure, and the artifact a reader can open to see it;
2. the suspected mechanism, and the competing explanations;
3. the likely fix, and which phases it touches;
4. which outcome the hypothesis is about, and what the fix can recover on it.
   For a must-find recovery hypothesis that is the **ceiling**, read off the
   archived misses in its class. Applicability, false certainty, question
   effort and provider compatibility each have their own outcome;
5. what would disprove the hypothesis;
6. the smallest experiment that discriminates between the explanations;
7. the expected cost and the acceptance criterion.

A hypothesis with no falsifier is not an experiment. The ledger refuses a row
without one.

## The ladder

**Stop at the rung the claim needs.** The ladder is not a path to its top. A
correctness claim is finished at rung 3; a claim about live report quality
starts to need rung 4. Each rung licenses a narrower claim than the next, and
a report that states a higher claim than its rung has reached is wrong.

| Rung | What it is | What it licenses you to say | Paid? |
| --- | --- | --- | --- |
| 1 | offline analysis and deterministic replay of the unfixed code | the defect is reproduced | no |
| 2 | the same input through the fixed production function | the local fix is verified | no |
| 3 | the changed stage plus its affected deterministic consumers, on scripted or archived input | the affected consumers are verified | no |
| 3a | the free instruments over archived outputs under the fixed code | the behaviour on archived inputs is measured | no |
| 4 | a designed run on the shipped route | a downstream gain was measured | yes |
| 5 | held-out confirmation | the gain holds on material the fix never saw | yes |

A reproduction is not a verification. Rung 1 shows that the defect exists;
rung 2 shows that the fix removes it. Report both, and never collapse "fix
verified" into "defect reproduced".

A condition that feeds signed material to generation (`corrected-extraction`,
`direct-facts` in `references/three-conditions.md`) never reaches rung 4 or 5.
Its figures locate a loss, and only the shipped route can measure a gain.

Report each recommendation's state with these words, one per claim:

- defect reproduced;
- local fix verified;
- affected deterministic consumers verified;
- behaviour on archived inputs measured;
- live downstream gain unmeasured, or measured;
- held-out gain unmeasured, or confirmed.

A fix can be complete at "affected deterministic consumers verified" while
its live gain stays unmeasured. Say both. Where an acceptance criterion needs
the live gain, that criterion stays unmet, and you split it onto its own
issue rather than close the whole issue.

## The evidence table

Every audit and every finished fix reports this table, one row per claim or
decision:

| Claim / decision | Evidence and provenance | What passed or failed | What remains unknown | Fresh paid inference necessary? |
|---|---|---|---|---|

The last column is `no`, or `yes` with the reason that no free check can
answer the question.

## Before you propose paid work

Write all eight down. A proposal that leaves one out is not ready:

1. the exact open question, and the decision each possible result changes;
2. the offline checks you completed, and their results;
3. the ledger and archive search, and whether a compatible result exists.
   Name the dependency that changed before you call a result stale; a
   different `HEAD` alone does not;
4. why no free check can answer the question;
5. the smallest affected stage, and the case set with its reason;
6. the metric, the denominator, the baseline, the decision thresholds, and
   the sampling and stopping plan;
7. the maximum spend, and what a retry costs;
8. what you do if the result is null, adverse or inconclusive.

"More confidence" and "confirmation" are not questions. A deferred experiment
is not proposed again unless the decision, the evidence or the budget changed;
name the change.

## Money

The default budget is zero. Raise it only on an explicit amount the user states
in this conversation.

- **Ask every time.** A permission for one run is not a permission for the
  next.
- **"One run" means one case.** A corpus sweep is a separate ask with its own
  number.
- **Price before you ask.** A must-find fix whose ceiling sits inside the
  run-to-run band gets no run. Batch it with the next fixes until the batch
  clears the band. A batch amortises the cost of a confirmation, but it cannot
  say which change caused what.
- **Choose the repeats from the design.** There is no fixed count. Five runs
  of one case measure that case's spread; a correctness claim needs none.
- **Pre-flight.** One case first; read its provenance before the sweep.
- **This file enforces nothing.** The estimate gate and the spend hold are in
  `evals/harness/consent.py`, and they are the only spend enforcement that
  exists. `--accept-cost unknown` removes the hold entirely — never pass it to
  raise a budget.
- **Set `ANALYSIS_OFFLINE` for offline work.** It refuses a live provider call
  before it leaves the process, on every node, every retry and every
  single-node replay. It guards against an accident and is not consent.
- **Classify a command by what it does.** `score` and `replay` re-read an
  archive. `lane-replay` and `critic-replay` send a request again and cost a
  call, whatever their names say.
- **A cache hit is not free.** Meter paid requests.
- **Do not substitute a cheaper model in a confirmation run.** That is a
  changed condition, and the comparison stops being a comparison. A cheaper
  model cannot stand in for the deployment model in a claim of equal quality.

There is no dollar ceiling in the code, by decision: a contributor may spend
any amount. The control is informed consent, so your job is to state the
labelled amount — `recorded`, `estimated` or `unpriced` — and stop when the
answer is no.

## Execution identity

Reuse an earlier result only under a complete identity: the same inputs,
prompts, schemas, relevant code and dependencies, model and settings, and the
same reachable provider identity. Where the exact provider revision cannot be
read, record the ambiguity rather than assuming it away.

- A changed upstream artifact invalidates its descendants. Unrelated branches
  stay reusable.
- **A replay is not a repeat.** Re-scoring an archive under new code measures
  the code. Only a new run measures the run-to-run spread.
- An incomplete experiment that stopped on a budget is incomplete, not passing.

## Isolation

When implementation is authorised:

- one candidate change per branch, and one lever per change;
- run the regressions the change can reach, and say which;
- an unsuccessful change is reverted and its record kept;
- the skill's own files are candidate changes too, with reviewable diffs.

## Before changing any identity rule

Replay the archive first:

```bash
uv run python -m evals.harness.run replay evals/emissions/*.json --out /tmp/replay.json
```

A slug, identity or digest rule change moves figures silently, and the diff
against the archive is the only thing that says whether it did. This costs
nothing.
