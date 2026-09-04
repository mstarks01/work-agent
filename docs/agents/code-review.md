# Code review checkpoints

**A completed checkpoint round ends in an annotated tag named `reviewed/<date>`.**
That tag is the fixed point the next round starts from, so "review the code
since the last review" resolves to a command rather than to a question.

## Two instruments, two questions

Review runs twice, at two scales, and each scale sees what the other cannot.

- **A pre-merge review** reads one pull request's diff before the merge. It
  asks whether this change is correct.
- **A checkpoint round** reads a range of merged commits. It asks what the tree
  now holds that no single diff showed.

Run both. Nine audit rounds produced the evidence, and it points both ways at
once.

| Round | Findings | From the previous round's own fixes |
|---|---|---|
| 5 | 6 | 4 |
| 6 | 6 | 3 |
| 7 | 11 | 9 shared one rule that a fix gave a second reader |
| 8 | 3 | 3 |
| 9 | 2 | 1 |

A defect in a recent fix is the dominant class, and it sits inside one diff, so
a reader of that diff before the merge is the instrument that fits it. Run-9's
HIGH is the plainest case: the bound sat on a scan where the caller runs a body,
and `CLAUDE.md` named that mistake two days before the fix shipped.

The rest of the table is the argument for the round. Run-9's LOW was a
`RuntimeError` that `Path.resolve` raises on a symlink loop. It was legal in
every diff that touched it, and it became reachable only when a later pull
request made the gate resolve a path. No diff carries that fact; the tree does.

### What a pre-merge review reads

The diff, against the three named defect causes in `CLAUDE.md` — a rule with two
readers, a bound that predicts a cost from its inputs, and a value shape the
author never listed — and against the fix habits at the end of this guide.

Put the result in the pull request, so the record sits with the change. A
pre-merge review cuts no tag.

### What the round reads after that

The same range as before. Its question narrows, because each pull request in the
range already had a reader: the round hunts across pull requests, over the whole
tree, and on surfaces no diff touched. The tag message names which pull requests
in the range got a pre-merge review, so the next round knows which half of the
range is new ground.

## Find the last checkpoint

```
git tag -l 'reviewed/*' --sort=-creatordate | head -1
```

Read what it covered, and what it deliberately left:

```
git show reviewed/2026-08-21
```

Then diff against it:

```
git diff reviewed/2026-08-21...HEAD
git log reviewed/2026-08-21..HEAD --oneline
```

Three dots, so the comparison runs from the merge-base.

## Record a new one

Tag the **head of the range the review covered**, never a later one. The PR
that fixes the findings lands after the tag, so the next review reads it as the
first commits of its own range. A tag on the fix PR puts that PR outside every
review: the review before it ended at the range head, and the review after it
starts at the tag.

```
git tag -a reviewed/<date> <sha> -F -
git push origin reviewed/<date>
```

The message carries what a later reader cannot recover from the diff:

- the range reviewed, as `<base>...<head>`, with the size at the time
- which pull requests in the range had a pre-merge review, so the next round
  knows which half of its own range nobody read before the merge
- which axes ran, and what each read as its source of truth
- how many findings each axis produced, and where each was fixed
- **what was left open by decision**, with the reason — this is the part that
  stops the next review re-reporting a settled question as a new finding

## Why a tag

The checkpoint is a fact about a commit, so it belongs on the commit. A date
in a guide drifts the moment somebody rewrites the guide, and a note in a
tracker needs a network call and a lookup to answer "since when". A tag is in
every clone, needs no service, and is what `git diff` already takes.

`reviewed/*` sits beside the repository's other tag namespaces, `archive/*`
and `backup/*`, and reads the same way: the branch is gone, the tag is the
record.

## Habits that fixes need

All five come from the audit runs that found defects in the previous run's
fixes. Run 4 found 4 of its 6 findings in run 3's fixes, run 5 found 4 of 6 in
run 4's, and all 3 of run 8's came from two fix pull requests. Every one of
those defects passed the tests that shipped with it, so these are rules about
the fix itself, not about testing harder.

A fix is the riskiest code in the tree. It is new, it lands fast, and the
attention that found the defect is spent by the time the repair is written.

### Review your own fix the way you reviewed the defect

Read the fix diff against the three named causes in `CLAUDE.md`, the same way a
pre-merge review reads anybody else's diff. This includes the fix that closes a
finding you reported an hour ago. Run 9 shipped a bound with no per-body limit,
and `CLAUDE.md` carried that corollary two days before the fix merged.

Let a fix to a hot path sit long enough to read it once more. Minutes between
the last keystroke and the merge is how the previous rounds shipped their
defects.

### Prefer deleting a reader over adding a guard

When the defect is a rule with two readers, make one reader call the other. A
guard copied into the second reader contains this defect and leaves the class
open; one shared helper removes the class.

Run 10 fixed a corpus loader that followed a symlink out of a case directory. It
inlined the resolve-and-bound rule a third time rather than exporting the one
`sitting.moved` already held. The two readers are tested against each other, so
they cannot drift in silence — but that is the fallback, not the fix to reach
for first.

### Make the harness that proved the bug the regression test

The input that reproduced the defect is the honest one. A simpler test written
after the repair tends to check the repair rather than the defect, and it passes
for a reason the author chose.

### Ship no bound without two measurements

A constant that bounds work needs a measured case it **must admit** and a
measured case it **must refuse**, and the commit message carries both numbers.

The repair rung took three attempts because the first two bounds computed a
cost from the input sizes. Measurement showed the metric ordered two real cases
backwards: English prose at 288M ran 0.39 s, and a repetitive source at 112M
ran 5.95 s. No reading of the code produces that; only running it does.

If you cannot produce both numbers, the bound is a guess. Say so, or measure.

Then name what sits beside the new bound. A ceiling makes the next unbounded
value the weak one, so the pull request body lists the neighbours and says which
of them are bounded. Run 9's finding was a bound on one scan beside an unbounded
count of scans.

### When a fix breaks an existing test, suspect the fix

The default assumption is that the test is right and the fix is wrong. Read the
test and find out what it protects before you touch its premise.

`test_rekey_refuses_a_move_the_components_cannot_satisfy` caught a fix that
would have made `rekey` impossible to run: it asked a ledger row to prove itself
against the *current* rule, and a ledger written before a rule change is exactly
what `rekey` reads. The test failed for that reason and no other.

A test that fails on a correct change is a real thing, and pinned lists move
that way. It is the second explanation to reach for, not the first.
