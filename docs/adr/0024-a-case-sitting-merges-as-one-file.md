# 24. A Case Sitting merges as one file, and the corpus records nothing

- **Status**: accepted
- **Date**: 2026-09-06
- **Effort**: [#634 — the canonical review format and the CI gate disagree about
  whether a case is read](https://github.com/mstarks01/work-agent/issues/634)
- **Amends**: [ADR 20](0020-a-sitting-pull-request-carries-n-cases.md) by
  replacement of its subject. ADR 20 settled how many cases one sitting pull
  request may carry, over a record that lived under each case directory. There
  is no such record now, and a submission carries every case a reader read in
  one file, so the count ADR 20 removed cannot come back.
- **Relates to**: [issue #327](https://github.com/mstarks01/work-agent/issues/327),
  which established the act and is unchanged, and
  [issue #226](https://github.com/mstarks01/work-agent/issues/226), the thirteen
  unread cases this decides the shape of the answer for.

## Context

A **Case Sitting** was recorded two ways.

`webapp/sitting.py` wrote one JSON submission under `evals/review/submissions/`.
`evals/BLESSING.md` and the `sitting` kind of the submit spine wrote a `reviews`
entry into the case's own `case.json`, beside a filled `REVIEW-<login>.md`, and
deleted the case's line from the `UNREVIEWED` table.

The two disagreed about whether a case was read. `tests/test_case_review.py` —
the gate that decides it — built its verdict from `case.json` alone and never
opened `evals/review/submissions/`. A reader who sat a case through the
documented app therefore saw the rail grey the row and the case leave `--list`,
while the gate learned nothing: the case kept its `UNREVIEWED` line, and no
check fired to say so.

The app made the disagreement plain. `/api/finish` wrote all three case-local
artifacts, `/api/contribute` opened a pull request carrying only the JSON, and
`_clean_local_record` then undid the three. The case-local format was written to
be deleted.

This is the failure `CLAUDE.md` names under **One rule, one reader**: two readers
of one rule, each with its own test agreeing with it, on the act that produces
the corpus's only human evidence.

## Decision

**A Case Sitting is one JSON file under `evals/review/submissions/`, and nothing
else.** The corpus records no sittings.

1. **One reader decides whether a case is read.**
   `evals.review_submission.current_reviews` answers it from the merged
   submissions. The rail, the CI gate and the printed count all call it. A
   submission stops clearing its case the moment any file it read changes, so a
   corpus edit puts the case back fail-closed.

2. **The unread list is derived, never maintained.**
   `evals.review_submission.unreviewed_cases` is the corpus minus the current
   submissions. `UNREVIEWED` in `tests/test_case_review.py` stays as the record
   of what each unread case leaves unchecked, and a new case that arrives
   without a submission and without an entry still fails. It is not the count,
   so it cannot rot into a wrong one.

3. **A review pull request carries one file.** `verify_pull_request` already
   refused every other path, and that refusal is now the whole contract:
   nothing about a sitting asks a reader to edit a case, a list, a test or the
   roster.

4. **Recording writes nothing into the working tree.** `finish` marks the
   reader's draft finished and `withdraw` puts it back to open. A reader who
   stops has nothing to clean up.

5. **`sitting-import` writes the same one file.** An offline reader's envelope
   and a reader at a keyboard produce the same bytes, checked the same way.

6. **The cutover is hard.** `CaseMetadata` has no `reviews` field, so a
   `case.json` still carrying one refuses to load and names the case and the
   field. There is no compatibility path and no data to migrate: no case
   carried an entry.

## Consequences

**A sitting no longer needs a roster line.** The old clearing rule required
`submitted_by` to be rostered. A submission's `submitted_by` is bound by
contribution CI to the account that opened the pull request, which is a stronger
claim than a line in a file that pull request would otherwise be editing — and
under a one-file rule the roster line cannot ride along, so requiring one would
refuse every first-timer. **Standing** is unchanged and still governs a vote,
which is a different act under `evals/review/votes/`.

**The filled reading document is a rendering, not a file.** The submission
carries the answers; `sitting.document` renders the document from them, which is
what the read-only view serves. Nothing is committed beside a case, so nothing
can drift from the answers it claims to show.
`evals/corpus/01-payments-checkout/REVIEW-02.md` stays where it is: it is a
reader's own words and the template for the method, rather than a record this
rule reads.

**The submit spine has no `sitting` kind.** Its allowlist, its five preflight
checks and the `/api/submit` endpoint that ran them are gone, along with
`can_submit`. The spine keeps the two kinds that still package a working tree,
`vote` and `baseline`.

**What is given up.** A sitting can no longer carry a correction to the
reference set it justifies. ADR 20's premise — that a record and its edits
travel together — does not survive a one-file rule. A reader who would change a
set writes it in their notes, and a maintainer makes the change in a pull
request of its own. That trade buys a contribution that needs no clone, no
allowlist and no write access to the corpus, which is what #599 set out to make
possible and what the thirteen unread cases need.
