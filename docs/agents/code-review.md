# Code review checkpoints

**A completed code review ends in an annotated tag named `reviewed/<date>`.**
That tag is the fixed point the next review starts from, so "review the code
since the last review" resolves to a command rather than to a question.

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
