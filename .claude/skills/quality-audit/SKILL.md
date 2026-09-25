---
name: quality-audit
description: Audit what is hurting this service's report quality, attribute each loss to a phase, price a fix before paying for one, and record the result so the next audit reads it. Use when asked to run a quality audit, diagnose report quality, find why recall is low, or improve the analysis.
---

# Quality audit

## What this does

You find the things that are hurting report quality, say which phase caused
each one, propose a fix, and validate the fix at the lowest cost that can
settle the question. Then you write the result down so the next audit does not
repeat it.

The default audit is **offline and free**. Every instrument named here reads an
archive this repository already paid for. A paid run needs the user's explicit
permission, every time, and the ceiling of the fix has to clear the run-to-run
band before you ask.

## What this is not

It is not a new evaluation framework. The harness under `evals/` already
measures this service, and `evals/TUNING.md` is already the procedure for
changing a lever and measuring what it did. Your job is to diagnose, to choose
the narrowest instrument, and to keep a memory. Where an instrument is missing,
you write an extension ticket — you do not build a second scorer.

It is not authority to spend. See "Money" below.

## Invocation

`Run a quality audit` is the whole required invocation. Everything else has a
default, and you print the effective settings before you start.

| Parameter | Default | Where it comes from |
| --- | --- | --- |
| Repository and revision | this checkout, `HEAD` | `git rev-parse HEAD` |
| Scope | every phase | the user may name extraction, preparation, analysis, fan-in, criticism or reporting |
| Paid-inference budget | **$0** | only an explicit amount in this conversation raises it |
| Mode | audit and recommend | implement and validate only when the user says so |
| Capability extension | propose only | implement only when the user says so |
| Attempts | 3 improvement attempts | the user may set another number |
| Resume | none | the user names a prior audit ID |

Print these back as a short block before step 1, so a wrong default is caught
before it costs anything.

## The loop

Work the steps in order. A step that cannot run is a finding, not a reason to
skip ahead.

**1. Fix the coordinates.** Record the commit, whether the tree is clean, which
frameworks are in `PACKAGES`, and what the user authorised. Give the audit an
ID of the form `QA-<UTC date>-<two digits>`.

**2. Read the history first.** Before you propose anything:

```bash
uv run python -m evals.harness.run experiments --signature "<the failure you are about to propose a fix for>"
```

Every match comes back with its outcome and whether the tree has moved under it
(`current`, `stale`, `unanchored`, `undecidable`). A `refuted` match that reads
`current` means the proposal is already answered. Say so and move on. A
`refuted` match that reads `stale` may be worth repeating, and you say which
file changed and why that matters.

**3. Establish the metric before you use a number.** A figure somebody reported
is motivation, not a baseline. Say which metric, which run, which denominator,
which scorer version and which reference set produced it. `evals/baselines/README.md`
holds every merged Baseline with its commit and corpus digest; numbers compare
only inside one of its groups. `evals/README.md` says what each figure does
*not* mean, and you read it before quoting one.

**4. Run the free checks.** See `references/instruments.md` for the full
inventory. The usual first pass:

```bash
uv run python -m evals.harness.run score <artifact> --out /tmp/<name>-rescored.json
uv run python -m evals.harness.run replay evals/emissions/*.json --out /tmp/replay.json
uv run python -m evals.harness.run oracle
uv run python -m evals.harness.run bottleneck
uv run python evals/verify_corpus.py
uv run pytest -q
```

**Read each instrument's whole output.** Filtering a command for the lines you
expected is how this audit's first pass missed `run.py score` saying
`NOT COMPARABLE TO THIS ARTIFACT'S OWN FIGURES` and built a finding on a number
the tool had already disowned. Save the output, then read it.

**5. Rank the failures.** One row per failure, ordered by how many important
outcomes it can recover. Use the output contract in
`templates/audit-report.md`.

**6. Attribute each one.** `references/attribution.md` holds the rules. Print
the phase table with `uv run python -m evals.harness.run phases` rather than
restating it. Record the **earliest** observed failure and every other
contributing one. `unknown` is a legitimate attribution.

**7. Price each fix before you propose a run.** `evals/TUNING.md` step 3 is the
procedure, and it is not optional. State the ceiling in must-finds, read off
the archived misses in the fix's own class. A ceiling inside the run-to-run
band gets no run; batch it.

**8. Execute the lowest-cost discriminating test** the authorisation allows.
`references/experiment-protocol.md` holds the ladder. Offline first, always.

When important misses stay unexplained after step 4, and the evidence cannot
tell a lost fact from a lost argument or a dropped finding, read
`references/three-conditions.md`. Its entry test decides whether this audit
takes that branch.

**9. Record the result.** Every experiment gets a row, whatever it concluded.
`refuted` and `null` are outcomes, not failures to hide. Write the row with
`templates/experiment-record.md` and append it:

```bash
uv run python -m evals.harness.run experiments --record /tmp/<experiment-id>.json
```

**10. Report.** Use `templates/audit-report.md`. Lead with the ranked table and
a one-screen summary.

## Stopping rules

Stop and report when any of these is true:

- the budget or the attempt limit is reached;
- no proposal has a ceiling that clears the band;
- the next question needs a capability that does not exist — write the
  extension ticket from `templates/extension-ticket.md` and stop there;
- the evidence does not settle the question and no cheaper test would.

An audit that stops with a good extension ticket is a successful audit.

## Money

**Never call a paid model without the user's explicit permission in this
conversation.** The default budget is zero. "One run" means one case, not a
sweep; a corpus sweep is a separate ask with its own number.

The spend control that matters is in code, not here: `run` states its estimate
and holds the operator to what they typed (`evals/harness/consent.py`). This
file cannot enforce a budget and does not claim to. What you do is ask, name
the amount, and stop when the answer is no.

Before any paid sweep, run one case first and read its provenance.

## Hard rules

- Never loosen an acceptance criterion, edit a reference answer, or change a
  human ruling to make an output pass.
- Never sign anything on the user's behalf. A signature is a human judgement.
- Never expose a held-out answer to generation. Oracle-assisted runs are
  diagnostics and are reported apart from any score.
- Never diagnose a **Holdout Case** (`holdout: true` in `case.json`). The
  diagnosis commands filter it out and the replays refuse it. Read only its
  figure in the holdout split, and only to confirm a fix measured elsewhere.
- Never claim a report improved from an intermediate metric. The ladder in
  `references/experiment-protocol.md` says what each rung licenses.
- A structural identity match is not proof that a finding is semantically
  right, and an unlisted output is not automatically wrong.
- A change to one framework package owes an explicit answer for every other
  package in `PACKAGES`. See `docs/agents/framework-parity.md`.

## References

- `references/quality-contract.md` — the nine axes, and which instrument reads each.
- `references/instruments.md` — every command and archive, and what it answers.
- `references/attribution.md` — phases, substitutions, and what attribution may not claim.
- `references/experiment-protocol.md` — the cost ladder, execution identity, budgets.
- `references/three-conditions.md` — where a finding is lost: the shipped route, a corrected extraction, and signed facts given directly to analysis.
- `references/memory-and-extension.md` — the ledger, diagnostic rules, extension tickets.
