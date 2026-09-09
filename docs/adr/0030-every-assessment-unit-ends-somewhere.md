# 30. Every assessment unit ends somewhere

- **Status**: proposed, priced on the first Baseline below
- **Date**: 2026-09-09
- **Effort**: [#739 — a bounded coverage pass that recovers what a lane omitted](https://github.com/mstarks01/work-agent/issues/739),
  finding 4 of the audit on [#732](https://github.com/mstarks01/work-agent/issues/732)
- **Builds on**: [ADR 0026](0026-coverage-is-per-framework.md), which counts
  what a lane cited; [ADR 0028](0028-a-draft-states-its-direction.md), which
  gives an ASVS draft a direction

## Context

The graph runs each lane once, merges, and reviews. The **Critic** may not add a
claim the lanes missed, move a misfiled one, or improve one; the re-ask repairs
malformed rulings only. So one enumeration by one lane agent is the only
discovery opportunity a job has, and a valid but short answer ends the search.

The first STRIDE Baseline charges 55 of its 131 misses to `place`, a candidate
rule led the lane to the elements and nothing was drafted, and 28 to `unled`,
33 of the 83 must-finds. The coverage instrument counts citations against what
the lane was offered, and its own docstring says a lane that cleared a flow and
one that skipped it look alike. Nothing reads that count back into the run.

## Decision

**A lane answers for every assessment unit it was offered, and a unit with no
answer is asked about once more, bounded.**

**An assessment unit is the package's own.** The neutral `units_for` hook
already names them for a package whose claims rule on a catalog: the selected
requirements. For a package whose claims compose an identity from an action
and a place, a unit is one **candidate** or one **boundary crossing** in one
lane, never every element: the first Baseline offered each STRIDE lane 223
elements and the lanes cited about half, so a unit per element makes the
second call a second pass. Candidates and crossings are what the lane's rules
led it to, and both come out of what the graph already computes for the
coverage instrument, so no table is added.

**A unit ends one of three ways.** A **Claim** cites it; a **Cleared** mark
names it with one sentence of why nothing follows, which the lane writes in
a list beside its drafts; or a **Prerequisite** names it with the unknown that
blocks a ruling, which for an ASVS lane is a `question` and for a STRIDE lane a
conditional draft. A unit that ends none of these ways is **unanswered**, and
the fan-in computes that set in code from the lane's own output.

**The unanswered units get one bounded second call per lane.** The call
carries the model, the lane's first drafts as context it must not repeat, and
the unanswered units only. It may add drafts and marks and may change nothing
it was given. Its size is bounded by the unanswered count, and a lane whose
first pass answered everything makes no second call. The critic reads the
union, as it does today.

**A cleared unit is recorded, never scored as a hit.** The coverage block
gains a `cleared` count beside `cited`. A cleared mark proves attention, not
correctness, and the recall instruments read claims alone.

## Consequences

**Recall recovery has a mechanism.** The `place` and `unled` rows are the
units this pass asks about a second time. The ceiling is those rows' must-find
count, 33 on the first Baseline, and it is priced before any run as
`CLAUDE.md` requires.

**Cost rises by the unanswered count.** On a lane that answers most of what it
is offered the second call is small; on a sparse case it can approach a
second pass. The bound is measured, not chosen: the first paid run records
the unanswered count per lane, and the cap is set from it.

**The lane prompt grows one list.** `cleared` beside `claims`, one sentence per
unit, and the field-count lint holds the contract to the schema.

**A model can clear a unit it never read.** A cleared mark is a sentence a
model writes, and this record trusts it exactly as far as the coverage
docstring trusts a citation: as attention, not as an assessment. The reader
sees the sentence.

## Alternatives considered

**Repeat the whole lane prompt at the same cost.** The control this record
must beat: two full passes merged. It is the audit's own condition, and the
first measurement runs both.

**Let the critic add findings.** Rejected: the critic prompt's first rule is
that it rules and does not write, and a critic that writes is a third lane
with no candidates and no exemplars.

**A structural lint on coverage.** Rejected: it is what the coverage
instrument already is, and it recovers nothing.

## Priced on the first Baseline

Offline, 2026-09-09, over `352b72d-gpt-5.6-terra-2f7e336d`. Units unanswered
today, across 13 cases: 121 of 323 candidates and 85 of 258 crossings were
cited by no draft, about 16 units per case across six lanes. Elements: 555
of 1,338, which is why an element is not a unit. What the pass can recover,
by lane, from the `place` and `unled` rows:

| lane | place | unled | must-find |
|---|---:|---:|---:|
| elevation-of-privilege | 6 | 13 | 13 |
| tampering | 11 | 3 | 6 |
| denial-of-service | 16 | 4 | 6 |
| repudiation | 8 | 4 | 5 |
| spoofing | 6 | 3 | 2 |
| information-disclosure | 8 | 1 | 1 |

Ceiling 33 must-finds of 129, and the elevation lane holds 13 of them. The
ceiling is a price and not a bound, as `evals/TUNING.md` step 3 says.

## Measurement before acceptance

Five runs of case 01 each way against the control, reading matched and
must-find counts, the unanswered count per lane, the cleared count, the
unlisted count and the cost. The runs wait on spend approval, and the code
waits on the runs: a second call on every lane is a cost every job pays, and
it ships measured or not at all.
