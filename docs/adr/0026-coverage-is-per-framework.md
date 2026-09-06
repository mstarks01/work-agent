# 26. A sitting covers the frameworks it read, not the case

- **Status**: accepted
- **Date**: 2026-09-06
- **Sharpens**: [ADR 24](0024-a-case-sitting-merges-as-one-file.md), which
  settled what a **Case Sitting** merges as. This settles what one covers.
- **Relates to**: [ADR 25](0025-unsure-is-a-mark.md), the other half of making a
  bar answerable, and
  [issue #226](https://github.com/mstarks01/work-agent/issues/226), the thirteen
  unread cases this protects the work on.

## Context

A **Framework Package** can arrive on a case somebody already read. The case
declares it, a reference set appears beside the others, and the sitting that
merged before it existed carries no digest for it.

`required_files` was all-or-nothing, so that sitting stopped clearing the case
and the case reverted whole. Measured, a reader was told four things and only
one of them was intended:

* `claims/<new>.json changed since the reviewer opened the case` — **false**.
  The file is new. `moved` was passed a missing digest as an empty string, so an
  absent digest read as a drifted one.
* `8 of 26 recorded findings carry no mark` — misleading. The reader marked
  every finding of the set they read. The count spanned both frameworks and
  named neither.
* the rail flipped to `to do` — too coarse.
* the case opened with an empty own list — **harmful**.

The last one is not a cost. The method's one rule is that the reader writes
their own list before the recorded sets open. A reader coming back to a case has
read those sets, so any list they write now is evidence of an order that did not
happen. Reverting the case did not just ask for the hour again; it destroyed the
instrument for the second sitting.

`tests/test_case_review.py` already stated the intended rule — "a case reviewed
for one framework stays unread for the other". The implementation was coarser
than the sentence.

## Decision

**A sitting covers the frameworks whose reference sets it read.** A case reads
as read when every framework it declares is covered, by one sitting or by
several.

1. **Coverage is derived from the digests.**
   :func:`~evals.harness.sitting.covered_frameworks` answers it: a framework is
   covered when the sitting carries a digest for that framework's set and the
   digest still matches. A framework the case gained later is not covered, and
   that is not a fault.

2. **`current_reviews` is keyed `case -> framework -> review`.** Fail-closed
   still: an edit to a read file drops exactly the frameworks whose evidence
   moved, and leaves the rest standing.

3. **Every finding of a covered framework carries a mark**, and the count is
   per framework. A set that arrived after a reader finished is not their
   unfinished work.

4. **A mark for a set the sitting did not read is refused**, named by
   framework. A reader may not judge what they do not say they opened.

5. **The rail names what waits.** A partly covered case presses like an unread
   one, and its status reads `<framework> waiting; <framework> read` rather
   than `to do`.

6. **The blind own list rides forward, locked**, with the marks already made.
   A returning reader answers the new set and nothing else.

7. **One reader, three callers.**
   :func:`~evals.harness.sitting.sitting_problems` holds the whole rule, and the
   press, the CI check and the offline import all ask it. The import used to
   spell part of it a second time.

## Consequences

**The "together" property narrows, and only where it must.** Step 6 asks a
reader to judge a case's sets together, and a fresh case still gets that: the
first sitting covers every framework the case declares. What is given up is the
claim that a set added later was judged in the same session as the sets before
it. Nobody could have done that, so nothing real is lost.

**A case can be covered by two readers.** The read-only view shows the newest
covering sitting's document, and `partial_signatures` says who read what. That
is a gain rather than a cost: a framework's set gets the reader who knows it.

**A submission that opens no reference set is refused.** Digests alone say
nothing about any **Claim**, so a sitting has to read at least one set.
