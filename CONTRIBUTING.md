# Contributing

Anyone may open a pull request here. A maintainer reads every line before it
merges, and that review — plus a set of mechanical checks — is the whole
defence; there is no account to apply for and nothing to be granted. What you
contribute feeds the tuning loop and the quality numbers this project
publishes. It never gates a merge, so a submission can be wrong without
breaking anything.

## What you can contribute, by what you have

**If you have time.** You can read what the tool produced and say whether it
is any good. Two kinds:

- A **vote** — you answer one finding at a time: is this a real threat to this
  system, or noise? [`evals/VOTING.md`](evals/VOTING.md) is the procedure.
- A **Case Sitting** — you read one golden case whole (its sources, its model,
  every framework's reference set) and say whether the recorded set describes
  what could actually go wrong. [`evals/BLESSING.md`](evals/BLESSING.md) step 6
  is the procedure, and 12 of the 13 cases are still waiting for one.

Both are **free and offline**, because everything they read is already in this
repository: a merged **Baseline** keeps the reports its findings came from, and
a golden case carries its own sources, model and reference sets. Neither needs
a provider key, and neither spends anything.

**If you have time and a provider key.** You can also run the corpus through
live models and contribute the result as a **Baseline**, so the published
numbers cover a configuration nobody here has measured — a different vendor, a
different model, a different sampling.
[`evals/BASELINES.md`](evals/BASELINES.md) is the procedure. It costs your own
money, and the tool tells you what it expects to cost before it spends
anything.

## The rules, all of them

- **One kind per pull request.** A vote, a sitting and a baseline each travel
  separately.
- **`submit` opens the PR for you.** `python -m evals.harness.run submit
  <vote|sitting|baseline>` runs the same checks CI runs, prints them as a
  checklist, and opens the pull request through `gh`. Add `--dry-run` to stop
  after the checklist.
- **You are registered automatically.** On your first submission, `submit`
  adds your line to `evals/review/voters.toml` with standing `contributor`,
  and it travels in the same pull request. Your GitHub login is your name in
  every record here, and your **Standing** is what published numbers state
  alongside the numbers themselves — see the glossary entry in
  [`CONTEXT.md`](CONTEXT.md).

## Contributing code

Code contributions are welcome and follow the ordinary path: open an issue,
work on a branch, keep the tests green. Nothing above applies to them.
