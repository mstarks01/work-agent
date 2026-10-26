# 102. A report's blocks follow the order of the frameworks file

- **Status**: accepted
- **Date**: 2026-10-26
- **Ruled by**: the maintainer on 2026-10-26
- **Relates to**: [ADR 0077](0077-the-first-run-app-holds-job-records-in-the-job-store.md),
  whose one module now also submits a new job for both surfaces

## Context

A **Job** names the **Framework**s it wants, and its **Report** holds one
**Framework Analysis** block per name, in the order of `job.frameworks`. The two
HTTP surfaces disagreed about that order. The first-run app sorted the
selection into the order of `config/frameworks.toml`. The `/v1` route kept the
order of the request. Each surface stated its own order in a docstring, and
each had a test that agreed with it.

The two routes also repeated the seven steps of a submission: the selection,
the sources with the link answers composed in, the source limits, the owner,
the carried answers, the record with its reservation, and the admission. The
module of ADR 0077 held the steps of an answer and an amendment, and not the
steps of a submission.

## Decision

**The blocks of every Report follow the order of `config/frameworks.toml`,
whatever order the submission listed.** The maintainer ruled this on
2026-10-26.

- `resolve_selection` is the one reader of the rule. It returns the selection
  in the carried order, for every entry point. No entry point sorts what it
  returns.
- `JobAnswers.submit` is the one submission of a new **Job**, for both HTTP
  surfaces. It reads a **Submission**: it checks the selection, composes the
  sources, holds them to the bounds, names the **Owner**, carries the earlier
  job's answers, creates the record with its reservation and admits it. Each
  route keeps only its HTTP mapping: a selection refusal is a `422` on `/v1`
  and a `400` on the first-run app, and a handler answers each other refusal.
- `JobAnswers` holds the carried frameworks, in config order, beside the
  bounds of its **Deployment**.
- A lint in `tests/test_one_reader_lints.py` refuses a second site that
  creates a job record or admits one with a `start`.

## Considered options

- **The order of the request.** Rejected by the ruling. Two submissions of the
  same frameworks would give two reports with different block orders.
- **Order the selection in each route.** Rejected: a second reader of one
  rule, which is how the two surfaces came to disagree.

## Consequences

- One rule, one reader, one test file for the order and for the steps of a
  submission. The route tests hold only the status each refusal takes.
- The in-process engine holds no Deployment, so it holds a selection to the
  registry of packages this build carries, and the same reader orders it by
  that registry.
- The construction of a `JobAnswers` from a Deployment stays in two places:
  `create_app` of the `/v1` service, which takes each bound as an injectable
  setting, and `Startup.current` of the first-run app, which attaches the
  running store. Each now also passes the carried frameworks.

## Evidence

`TestSubmitting` in `tests/test_job_answers.py` (both orders in, config order
out; the refusals; the carried answers; the reservation),
`test_the_selection_comes_back_in_the_carried_order` in
`tests/test_selection.py`, and
`test_a_new_job_is_created_and_admitted_in_job_answers` in
`tests/test_one_reader_lints.py`.
