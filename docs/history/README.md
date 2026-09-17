# History

One record, for one rewrite. A file here answers a question a stale commit ID
raises, and nothing here is a changelog.

## The attribution strip of 17 September 2026

A rewrite removed every AI attribution line from the commit messages on `main`,
and rewrote the 36 commits that named an agent as their author. The commits now
name the human author alone, which is what `CLAUDE.md` asks for.

The rewrite changed the ID of 979 commits. Content did not change: the tree at
the tip is the same tree, byte for byte, and no file moved.

### What to do with an old commit ID

`attribution-strip-2026-09-17.tsv` maps each old ID to the one it became. The
first column is the ID before the rewrite and the second is the ID now. A commit
whose ID did not change is absent.

```bash
grep ^<old-id> docs/history/attribution-strip-2026-09-17.tsv
```

### Where an old ID still appears

Three kinds of file name a commit the rewrite renamed.

A **Baseline** records `repo_commit`, and its directory name derives from the
same ID. `tests/test_evals_baseline.py` refuses a merged Baseline whose commit
is not an ancestor of `origin/main`, because the identity names a commit a
reader opens (#323). The three merged Baselines therefore carry the ID as it
stands now: each one re-keyed by recomputation, which renamed its directory and
its sweep file. No score, report or cost changed, and the tree the sweep ran
against is the same tree.

An archived sweep under `evals/emissions/` records the commit it ran at. The
artifact is evidence of a run somebody paid for, so it stays as it was written.

Every citation in `docs/` names the ID as it stands now, because the tags and
branches those citations point at moved with the rewrite.

### What the rewrite did not reach

GitHub keeps a copy of every merged pull request's commits under `refs/pull/`,
and nobody can delete those. The branches of merged pull requests also survive.
Both still carry the old IDs and the attribution lines.
