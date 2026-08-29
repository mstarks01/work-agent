# 20. A sitting pull request carries N cases

- **Status**: accepted
- **Date**: 2026-08-28
- **Amends**: [issue #327](https://github.com/mstarks01/work-agent/issues/327), which put
  one **Case Sitting** and the claim edits it justifies in one pull request. That rule
  stands. This record removes the count that the check read into it.
- **Relates to**: [issue #369](https://github.com/mstarks01/work-agent/issues/369), the map
  that settled the spec for a walk across many cases in one session, and
  [issue #371](https://github.com/mstarks01/work-agent/issues/371), which holds the detail
  of the checks.

## Context

`evals/harness/submit.py` refuses a sitting pull request that touches more than one case
directory. `_check_one_directory` reads `_one_subdir`, which returns `None` both for no
directory and for more than one, so either miss gets the same refusal.

Nothing records why the count is one. Issue #327 decided that a sitting record and its claim
edits travel together in one pull request. That sentence binds a record to its own edits. It
does not say how many cases one pull request carries. The check chose one, and the choice
never reached a decision record.

Two facts now push against that count. The sitting app walks many cases in one browser
session: the reader picks cases in a page, walks them with next and previous, and finishes
some of them. And eleven of the thirteen corpus cases wait for a sitting. Under a count of
one, a reader who sits eight cases must open eight pull requests.

## Decision

**A sitting pull request carries N case directories.** The unit is the reader's session.

The vote kind is the precedent. A vote pull request already carries N judgements over N
cases, and it titles itself `Vote: <author>, <n> votes over <n> cases`. A **Case Sitting**
is the same act at a coarser grain: one person reads, decides, and submits once. A count of
one made the sitting kind the odd row in the table.

The count itself becomes a field. `Kind` gains `subjects`, and the sitting row sets it to
`"many"`. `_check_one_directory` becomes `_check_subject_count` and reads that field. No
check branches on a kind name, so a fourth kind still arrives as a table row.

**One bad case refuses the whole submission**, and the failure names the case. Every problem
string starts with the case id, and the checklist prints every case that fails, in one
pass. A reader who walks eight cases repairs them in one round. A stop at the first bad
case would make the sitting kind the odd row again, because the other kinds report every
problem they find.

### The rejected alternative: one case per pull request

The app opens several pull requests, one per finished case, and the check keeps its count.

This fails on the outcome. The app must drive `gh` N times, and a failure part way through
leaves some cases submitted and the rest not. The reader then holds a half-submitted session
and no single thing to retry. One pull request has one outcome.

It also moves a cost, and it removes none. A maintainer reads N pull requests for one
person's session, and each one repeats the same roster line and the same unreviewed-list
edit. The reader pays for a rule that no record ever justified.

## Consequences

**A part-finished sitting lives outside the repository.** A session over many cases holds
unfinished work, and the working tree is the wrong place for it: an own list in the tree is
a file the reader can commit, and an own list that reaches a pull request before the reader
signs it destroys the evidence the method rests on. So a **Draft Sitting** lives at
`~/.local/state/work-agent/sittings/<login>/<case-id>.json`, it is never committed, and it
cannot reach a pull request.
[Issue #370](https://github.com/mstarks01/work-agent/issues/370) holds its shape.

**A new guard replaces the cardinality gate.** The allowlist builds itself from the changed
paths under every touched case prefix, so `_check_scope` no longer refuses a second case's
files. `_new_sittings` carries the replacement, because it already returns the problem
"appends no sitting entry": a case directory in the diff with no new `reviews` entry that
names the author refuses the submission. A reader cannot carry a case they did not sit.

> **Amended by [#388](https://github.com/mstarks01/work-agent/issues/388).** The allowlist
> no longer builds itself from the changed paths. Under each touched case it names the case
> metadata, the reader's own `REVIEW-<login>.md`, and one claim file per framework the case
> declares. A sitting may change an answer, never the question it answers, so an edit to the
> blessed **System Model** or to a declared source now fails scope. The guard this paragraph
> describes is unchanged, and #327's rule that a claim edit travels with its record still
> holds — a reference set is an answer.

**The checklist length does not move with N.** Each per-case check loops and collects the
problems of every case into one line, so the sitting checklist is the same length whatever
N is. The check names are the contract the reader reads, and 104 lines at thirteen cases
would bury them. It is nine lines today; a new rule adds a line, and a new case never does.

**The pull request says how many.** `_sitting_title` returns `Sitting: <author>, <n> cases`,
and the closing text lists the case ids.

**CI needs no edit.** `command_verify` calls the same preflight functions as the CLI, so one
implementation of the rules serves both surfaces.

## Framework parity

No package in `PACKAGES` needs an entry, and no check here keys on a framework.

The property that makes this true: **a submission check that derives its requirement from
the artifact under review needs no per-framework entry.** `_check_sitting_covers` reads the
case's own `frameworks` list and builds the required file set from it. So a framework with
an open claim set, a framework whose claims carry a catalog identifier, and a package that
nobody writes yet are all covered the moment a case declares the framework. The checks that
loop inherit that, because the loop is over cases and never over frameworks.
