# 67. A paused job asks what decides a framework first

- **Status**: accepted
- **Date**: 2026-10-07
- **Effort**: [#1542](https://github.com/mstarks01/work-agent/issues/1542) (F1)
- **Relates to**: [ADR 0048](0048-a-paused-job-ranks-its-open-facts-before-any-finding.md),
  whose ranking this keeps for a framework that will run, and
  [ADR 0053](0053-a-paused-job-asks-in-bounded-rounds.md), whose limits do
  not count the questions this adds

## Context

The early list ranked questions for every selected framework. It did not read
the framework's precondition, which `prepare` runs after the pause. So a pause
asked questions for a framework that would not run.

The audit of 7 October 2026 changed every process in the shared test model to
`interface_kind="non-web"`. The ASVS precondition then returns `refuted`, but a
level 2 pause still asked 15 questions and estimated 31 more. With every
interface and protocol unknown, the precondition returns `undecidable`. The
pause again asked 15 questions, and none of them asked a process's
`interface_kind`, which is the fact that decides the gate. The prior does not
name that field, so the list could not ask it.

## Decision

**The early list reads each selected framework's precondition first.** It
calls `run_precondition` through `early_questions.framework_gates`, over the
model that a resumed job's `prepare` reads: the checkpoint model with the
saved answers written in and the catalog applied.

- A `satisfied` framework ranks its questions as before.
- A `refuted` framework adds no question and no score. A question that another
  framework also asks stays, for that framework only.
- An `undecidable` framework asks only the facts that its package says decide
  it. Its own capability and field questions wait until an answer satisfies
  the gate.

**A package declares the facts that decide its precondition.** The new
`precondition_facts` member of `FrameworkPackage` returns them for a model
where the precondition is undecidable. Each fact is a field that the
precondition reads, so the answer that the resumed job writes onto the model
is the value that the gate reads. ASVS returns the `interface_kind` of each
process that states none. For a model with no process, it returns the
`protocol` of each flow that states none. STRIDE's precondition is total, so
it returns nothing. The member is required, so a later package must say how
an owner decides its precondition, or say that nothing can be undecidable.

**A gate question comes first in every round, outside the limits.** No prior
and no floor decide it, because the framework runs only after an answer. A
round shows every gate question that is still open, before the other
questions. Its answer counts toward no kind's limit. The remaining counts
carry a `gate` entry.

**The question set says what each gate reads.** `QuestionSet.gates` maps each
selected framework to its precondition result, and the `/v1` questions route
carries it as `framework_gates`. The first-run page states why a refuted
framework will not run, and what an undecidable framework waits on. A pause
with a framework that will not run does not start the analysis unseen, even
where it asks nothing.

**An "I don't know" answer to a gate question leaves the framework
undecidable.** The question is not asked again, and the framework asks no
other question. The analysis can still start, and the framework then reports
its refusal as before.

## Consequences

On the 15 corpus models, ASVS is refuted on cases 02 and 03, so an ASVS pause
asks them nothing. Case 07 is undecidable, so its first round asks the
`interface_kind` of three processes. The other 12 cases are satisfied and ask
as before. A STRIDE pause does not change.

A gate question asks per process. A model with many processes that state no
interface asks each one, although one "web" answer satisfies ASVS. The later
questions then disappear, because the gate is satisfied.

The follow-up after a report does not ask gate questions. A framework that did
not run has no conditional finding to rank.

## Evidence

The reproduction in #1542, run at `e2ef1b0d` before the change and after it.
The regression tests are `TestTheFrameworkGates` in
`tests/test_early_questions.py` and the contract test
`test_every_undecidable_precondition_offers_an_answer_that_decides_it` in
`tests/test_frameworks.py`.
