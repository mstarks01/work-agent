# Instruments

Every reading available to an audit, what it answers, and what it costs. The
commands are the real ones; `tests/test_doc_command_lints.py` checks each line
here against the parser that would run it, so a command that moves fails this
file rather than failing you at the terminal.

Run everything from the repository root, under `uv run`.

## Free, and where an audit starts

| Command | Answers |
| --- | --- |
| `python -m evals.harness.run score <artifact>` | re-scores a finished sweep against the corpus and the vote ledger, and writes the loss-attribution blocks |
| `python -m evals.harness.run replay <artifacts>` | re-scores archived extraction and assertion emissions under today's code, giving every reference element one fate |
| `python -m evals.harness.run bind --graphs <artifacts> <proposals>` | binds archived assertion proposals to archived graphs and names every binding the graphs refuse |
| `python -m evals.harness.run assembly <artifact>` | assembles every archived block again from its drafts and the rulings its report carries, and names any ruled draft the report omits |
| `python -m evals.harness.run descendants <artifact>` | runs the fan-in again over a sweep's archived proposals, with `--inject` proposals added, and bounds what the critic would keep |
| `python -m evals.harness.run oracle` | puts a perfect reading of every signed case through the deterministic path and charges each loss to a stage |
| `python -m evals.harness.run bottleneck` | puts ten hand-authored shapes through the **System Model** and charges every archived miss to a stage |
| `python -m evals.harness.run stability <artifacts>` | the run-to-run band, from sweeps already paid for |
| `python -m evals.harness.run compare <before> <after>` | what a prompt edit did: the instruction delta beside the score delta |
| `python -m evals.harness.run price-verbs` | prices a verb equivalence on the frontier, with no run at all |
| `python -m evals.harness.run extraction-losses <artifacts>` | what an end-to-end run lost before its lanes ran |
| `python -m evals.harness.run pairing` | the two sides of one case's applicability disagreement |
| `python -m evals.harness.run calibrate` | how far the identity rule agrees with the calibration labels |
| `python -m evals.harness.run review --voter <login> <artifacts>` | what a reviewer has waiting over a finished sweep |
| `python -m evals.harness.run experiments` | what a prior audit already tested, and whether the tree moved under it |
| `python -m evals.harness.run phases` | the six phases and the graph nodes each owns; `--node` answers for one node |
| `python evals/verify_corpus.py` | the corpus lint: what each case holds, how much of it is unsigned, and whether a verified input carries a reference answer |
| `pytest -q` | every offline gate, including the neutrality, licence and prose lints |

## The archives these read

| Directory | What it holds |
| --- | --- |
| `evals/runs/` | every sweep artifact, each with its `.reports/` directory beside it |
| `evals/emissions/` | what `extract` emitted and what `assert` proposed, per paid sweep |
| `evals/baselines/` | merged Baselines, digest-sealed; score one to a copy, never in place |
| `evals/corpus/` | the golden cases, their reference sets and their signed facts |
| `evals/review/votes/` | the vote ledger: the only human judgements in the tree |
| `evals/experiments/` | the experiment ledger this skill writes |

`evals/baselines/README.md` is generated. Rows compare only inside one commit
and corpus-digest group, and `evals/README.md` says what every figure does not
mean.

## Paid, and only on explicit permission

`python -m evals.harness.run run --mode <mode>` is the sweep command, and it
needs provider credentials. It states its own cost estimate and holds the
operator to what they accept; see `evals/harness/consent.py` and
`evals/TUNING.md`.

`python -m evals.harness.run lane-replay <artifact> --case <case> --framework <package> --lane <lane> --accept-cost unknown`
costs one lane call. `python -m evals.harness.run critic-replay <artifact> --case <case> --framework <package> --accept-cost unknown`
costs one critic call and compares every draft's verdict with the archived one. It rebuilds the request one lane made from the sweep's
`<case>.lanes.json` and sends it again, so a `place` loss can be read against
what the lane was shown. The material is the run's and the prompt files are
this checkout's, and the command prints both commits.

Narrow it as far as the question allows: `--case` for one case, `--framework`
to narrow to one package. One case first, then five runs of that case, then the
corpus — and only for a batch of fixes whose combined ceiling clears the band.

## What has no instrument today

Name these as blockers when an audit needs one, rather than approximating them:

- **Report prose.** The claim scorer reads structured claims. A report whose
  prose contradicts its own claims scores the same.
- **Entailment.** Evidence validation proves shape and quotation, not that the
  quote supports the claim.
- **Blind comparison against a human analyst.** No holdout of that kind exists.
  Corpus recall cannot stand in for it.
- **Reviewed precision.** The rejected rate and the writing objections both
  need votes, and most Baselines have none.

Each of these is a real gap with an open issue behind it. Check the tracker
before writing a new extension ticket for one.
