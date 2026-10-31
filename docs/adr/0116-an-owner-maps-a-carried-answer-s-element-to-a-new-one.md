# 116. An owner maps a carried answer's element to a new one

- **Status**: accepted
- **Date**: 2026-10-31
- **Effort**: [#2029](https://github.com/mstarks01/work-agent/issues/2029) (package 7, first step)
- **Amends**: [ADR 0079](0079-a-new-analysis-can-carry-an-earlier-job-s-answers.md),
  whose carried answer carried only where the new description gave its
  element the same name

## Context

A new job carries the answers of one earlier job of its owner (ADR 0079). An
answer names its element by an ID, and an ID follows the element's name, so
an answer about an element that the new extraction names differently is
dropped and listed in `carried_dropped`.

Over the archived extractions of 21 cases under `evals/`, 81.6% of the
element IDs of one extraction recur in another extraction of the same
unchanged description (686 ordered pairs). Inside one Baseline or
run, with the same code, 98.2% recur. So an owner who analyses the same system
again after the service has changed can lose the answers of about one element
in five.

## Decision

**The owner states a rename, and the service never guesses one.** A save to a
waiting job may carry `renames`: every **Element Rename** the job holds after
the save, each an element that a carried answer names and the model does not
have, and the element of the model it is. The service refuses a rename that
crosses element types, that names an element a carried answer already names,
or that maps an element the carried answers do not name, and two renames of
one element. A similar name proposes nothing.

**A rename reaches the flows of its element.** A flow's ID holds its two
endpoints' IDs, so each carried flow ID that names a renamed endpoint is built
again with the new endpoint. A flow whose own label changed takes its own
rename.

**The job keeps both the answers and the mapping.** `JobRecord.carried` holds
the answers as carried, and `JobRecord.carried_renames` holds the renames.
`carried_renames.renamed` is the one reader of a rename: the answer state and
`JobRecord.held_answers` both apply it when they read the carried answers. A
renamed answer passes the new job's answer check as any carried answer does,
and a renamed answer never lies over another carried answer to the same fact.

**Each dropped answer says why.** `missing-element` names an element the
model does not have, and `not-taken` an answer the job's questions refuse.
`rename_choices` lists each missing element with the elements of its type
that no carried answer names.

## Consequences

The jobs table gains the `carried_renames` column, written in the create
migration. A job that a later submission carries from hands on its carried
answers under its renames.

A save that sends only renames keeps the job's own answers. Only a save to a
waiting job sends renames.

The questions route lists the kept answers in `carried_kept` and
`carried_kept_links`. The first-run page marks each one under "Your answers"
and counts the answers kept, not kept and waiting for a part, as one change
review. A carried answer is not taken back when the facts it depends on
change; that is the next step of package 7.

## Evidence

`tests/test_carried_renames.py`.
