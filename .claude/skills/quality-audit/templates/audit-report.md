# Audit report template

Fill this. Keep the summary to one screen and put the full detail in the
durable record.

---

# Quality audit `<QA-YYYY-MM-DD-NN>`

**Coordinates.** Commit `<sha>`, tree `<clean|dirty>`, frameworks `<names>`,
scope `<phases>`, mode `<audit|implement>`, budget `<$0 or the stated amount>`,
attempts `<n>`. Resumed from `<audit id or none>`.

**Status.** One of:

- **Complete** — the conclusions are supported within the scope stated. Not a
  claim that nothing else is wrong.
- **Inconclusive** — the evidence does not settle it.
- **Blocked, extension required** — with the ticket attached.
- **Extended and resumed** — the capability change, and the answer to the
  original question.

## Ranked

| Priority | Likely issue / phase | Evidence and confidence | Proposed fix | Expected recoverable outcomes | Validation result | Cost / next action |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | | | | | | |

Label every evidence cell with its kind: **historical**, **issue-reported**,
**directly reproduced**, or **newly measured**. They are not interchangeable.

Give every row its rung: suspected → reproduced → local fix verified →
downstream gain measured → held-out gain confirmed.

## Answers

**What is hurting report quality?**

**Which phase is responsible, how confidently, and what alternatives remain?**

**What is the proposed fix?**

**What was changed, and what was measured?**

**What did not work?** — list the refuted and null experiments by ID. They are
part of the result.

**What remains untested or blocked?**

**What was spent, and what remains?**

**What should the next audit do first?**

## Baseline comparability

State these before quoting any figure:

- metric and denominator;
- the run or Baseline it came from, with its commit and corpus digest;
- scorer and reference-set version;
- whether the comparison sits inside one `evals/baselines/README.md` group.

## Prior experiments consulted

| Experiment | Outcome | State | What it says about this proposal |
| --- | --- | --- | --- |

## Reproduction

Exact commands, in order, with the artifact paths a reader can open.

## Conditions compared

Only for an audit that took `references/three-conditions.md`.

| Condition | Input | Adapters | Unrepresentable facts | Builder's exposure | Stage read | Result |
| --- | --- | --- | --- | --- | --- | --- |

## Experiments recorded

| ID | Hypothesis | Outcome | Cost | Row |
| --- | --- | --- | --- | --- |

## Extension tickets raised

| Blocked question | Where the change belongs | Issue |
| --- | --- | --- |
