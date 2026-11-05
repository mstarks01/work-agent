# QA-2026-11-05-02 design: does the re-ask repair a Control row fault?

Fixed 2026-11-05, before the first call of E1. The maintainer granted $2.00
on 2026-11-05 for QA-2026-11-05-01 (B2) and this run. B2 spent $0.7606, so at
most $1.23 remains. Stage 1 takes at most $0.84 of it.

## The question

Review finding Spec 3. `src/analysis_service/critic.py` sends a re-ask of
kind `control-row` in three cases: a ruling leaves a shown Control row in
neither `control_row` nor `refused_controls`; a ruling names a row the draft
was not shown; or a `control_row` sits on a ruling not rejected for
`control`. The procedure of `prompts/recritic.md` lists each fault the re-ask
repairs, with a step for each, and has no step for these three. A re-ask that
does not reconcile fails the job, and the job writes no report. The
maintainer ruled: add a repair step, and measure it.

Hypothesis for the later treatment: a repair step for the Control row fault
repairs more faults than today's `prompts/recritic.md` (the control).

## The instrument

`run.py recritic-replay` (#2188). No archived run holds a Control, so the
instrument writes one:

- **The first pass.** Case `05-cookbook-queue-webapp`, from
  `evals/runs/20261024T-sonnet-medium-sweep/05-cookbook-queue-webapp.json`
  (material commit `5d75b23f`). The case is not a holdout. Its STRIDE block
  records no unreconciled ruling, so the report's rulings are the critic's
  first pass. `assembly.rulings_from_report` rebuilds the 22 rulings. The
  first pass came from the strong tier of that sweep, not from
  `openai/gpt-5.6-terra`. The re-ask runs on `openai/gpt-5.6-terra`. This is
  a mixed provenance, and the record states it.
- **The Control.** `faults.json` holds one Control, "Worker config file
  permissions", scoped by name to `store:worker-config`. It has an
  `information-disclosure` row and a `repudiation` row, and one `config`
  evidence file, `worker.env`. The file states the file mode and the owner of
  the worker's configuration, and the two connection URLs. It shows nothing
  that either draft calls missing.
- **The answered first pass.** The Control shows I-02 the
  `information-disclosure` row and R-03 the `repudiation` row. No other draft
  is shown a row. `faults.json` refuses each shown row with a reason. With
  these refusals the first pass reconciles. Both drafts are `needs-info`.
- **The six fault views.** One fault of one shape on one draft, on I-02 and
  on R-03:
  - `row-unanswered`: the refusal is removed;
  - `row-not-shown`: `refused_controls` also names the other row of the
    Control, with the same reason;
  - `row-on-other-step`: the refusal moves to `control_row`, and
    `rejected_because` stays empty.

  Offline, the round's check names exactly one `control-row` problem on the
  faulted draft for each view, and the command refuses a view otherwise.

## Stage 1: the control arm

`run_control.sh`: today's `prompts/recritic.md` unchanged, 6 views x 2
repeats, 12 re-ask calls. The order runs all six views once, then all six
again. Route: OpenRouter, `openai/flex` upstream, strong tier
`openai/gpt-5.6-terra`, through `~/.config/analysis-service/model_tiers.toml`,
as QA-2026-11-05-01 used. The runner stops before a call that could take the
total over $0.84 (the total plus $0.10). It stops on a non-zero exit, a
missing charge, or a credit error. On a credit error the run stops and does
not switch route or model. Expected cost: about $0.05 a call, $0.60 in all.

## The counting rule

`count.sh` reads each run file. For each call:

- **repaired**: the answer was read (`schema_error` is empty), the merged set has
  no problem (`remaining` is empty), and the re-ask changed no ruling that no
  problem named (`unbriefed` is empty);
- **kept fault**: a remaining problem with the message that was sent;
- **new problem**: a remaining problem with a message that was not sent;
- **unbriefed change**: an entry in `unbriefed`;
- **status change**: the draft's verdict after the repair is not the verdict
  with every row answered (for example, a kept draft rejected for `control`).
  This is read and reported. It does not enter the repaired count, because
  the mechanical check accepts it.

The rule is validated on the control's output before any number of stage 2
is fixed: each file is read by hand against its `count.sh` line.

## The stop rule

If the control repairs 12 of 12 with no new problem and no unbriefed change,
the ceiling is zero. Record that and stop: the treatment cannot repair more.

Otherwise, the ceiling is 12 minus the control's repaired count, and stage 2
runs the treatment (`treatment.patch`) on the same 12 calls. Stage 2 needs a
new grant from the maintainer. It is not run under this grant.

## The treatment (drafted, not run)

`treatment.patch` changes `prompts/recritic.md` in two places. Step 1 lists
the Control row fault. A new step 8 answers the draft's `controls` rows as
step 3 of the first pass does. The old step 8 becomes step 9. The held phrase
"a `rejected` ruling owes its reader" (`.github/scripts/held_wording.toml`)
is unchanged. The patch is not committed to `prompts/`.

## The falsifier (for stage 2)

"A repair step for the Control row fault repairs more faults than the
control" is refuted if the treatment's repaired count is not greater than the
control's on the same 12 calls, or if the treatment has more new problems or
more unbriefed changes than the control.

## Stage 1 result, added after the run

The run read `6201cfa4` (#2188), clean, and spent $0.1634 for 12 calls. The
second six calls cost about $0.004 each, against about $0.024 for the first
six. This fits a cached prompt prefix. Each answer is a separate call.

**The counting rule, validated by hand.** Each of the 12 files was read
against its `count.sh` line. One fault in the rule was found and fixed before
any number was fixed: a view that was not repaired has no status after the
repair, and the rule counted it as a status change. The status change now
counts only repaired views. The repaired, kept-fault, new-problem and
unbriefed counts needed no change.

**The control:** 8 of 12 repaired, 4 new problems, 0 unbriefed changes, 0
status changes, 0 unread answers. `row-unanswered` and `row-not-shown` are
repaired 8 of 8. `row-on-other-step` is repaired 0 of 4: on each call the
re-ask removes the `control_row` and does not put the row in
`refused_controls`, so the row is left unanswered (a new `control-row`
problem). Each of those four jobs would fail with no report.

**The stop rule** does not hold, so the ceiling is 4 of 12, all on
`row-on-other-step`.

**The stage 2 numbers, fixed now.** The treatment runs the same 12 calls with
`treatment.patch` applied. The hypothesis is refuted if the treatment
repairs 8 or fewer, or has more than 4 new problems, or has 1 or more
unbriefed change. Expected cost: about $0.17. The runner cap for stage 2:
$0.30. Stage 2 needs a new grant from the maintainer.

## Stage 2 result, added after the run

The maintainer's grant of 2026-11-05 covers stage 2, because stage 1 left a
fault open (the grant row of 22:08Z). `run_treatment.sh` is `run_control.sh`
with `treatment.patch` applied in the run worktree and a cap of $0.30. The run
read `9468e79d` with the patch (`clean: false`; the `prompts/recritic.md`
input digest `fab74487` is the patched file). It spent $0.1666 for 12 calls.

Each of the 12 files was read by hand before the totals. Each answer returns
only the faulted draft, with `control_row` empty and the shown row in
`refused_controls` with a reason. On 4 calls the re-ask also adds a sentence
on the Control to the draft's reason. The check accepts this, and the
counting rule does not count it.

**The treatment:** 12 of 12 repaired, 0 new problems, 0 unbriefed changes,
0 status changes, 0 unread answers. `row-on-other-step` is repaired 4 of 4.
The falsifier does not hold, so the hypothesis is supported (E2).
