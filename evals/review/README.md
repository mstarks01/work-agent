# The review ledger

`votes/` is the append-only record of what a **person** decided about a
finding — one `<login>.jsonl` file per voter, named by the GitHub login of
the account that submits the votes. It is the only human judgement this
repository holds, and it is checked in on purpose: it is the evidence behind
every quality number the tool publishes, so it belongs in version control
where each change is a reviewed diff with an author and a date.

One JSON object per line. Never edit a line, and never delete one — a
reviewer who changes their mind appends a new event, and the latest event for
a `(fingerprint, voter)` pair is the live verdict. That is what makes any
past state of the ledger reconstructible, and it is what lets a number
computed last month be recomputed to the same digit today.

One voter's history lives in one file, and the loader refuses a row filed
under another person's name. Two voters' PRs therefore never conflict with
each other; two PRs from one voter still can, and that is correct — one
person sequences their own PRs.

A **Case Sitting** binds the same way and splits the name in two: the entry's
`submitted_by` is the account that carries it and needs the roster line, and
`submitted_for` records who read the case — a login, or `anonymous` for a
reader who takes part on no name of their own. Only the first grants anything.

`voters.toml` is the roster: every voter's line, holding their **standing**
(`maintainer` or `contributor`) and, after a rename, the old login in
`aliases`. Standing lives only here — never on a vote row — so a promotion is
one edit that re-classes a voter's whole history. `evals/harness/roster.py`
is the reader, and it fails closed on anything it does not recognise.

Write votes through `webapp/review.py`, which validates every field against a
closed set before the line is written. `evals/harness/ledger.py` is the
reader.

[`../VOTING.md`](../VOTING.md) is how a sitting fills these files: what each
answer moves, what the standings do to a number, and how `rekey` moves every
vote when the match rule changes.

The `votes/` directory does not exist until the first sitting. That is a
starting state and not a fault; the loader returns an empty ledger.
