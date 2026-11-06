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

## E3 design, fixed before the first call

The treatment of E2 has two blockers in #2192. First, it takes the body of
`prompts/recritic.md` to 1511 tokens, and the cap is 1400. Second, its step 8
moves a `control_row` to `refused_controls` on every ruling. For a ruling
rejected for `evidence`, `reasoning` or `lane`, the draft did not reach the
control step, so the row must be dropped.

The maintainer ruled on 2026-11-05. Code drops a `control_row` and the
`refused_controls` of a ruling rejected before the control step. The prompt
step covers only a ruling with no `rejected_because`, and the whole body fits
under the cap.

`treatment-e3.patch` changes `prompts/recritic.md` in two places:

- Step 1 keeps its first two sentences and drops the list of faults. Each
  step that follows names its fault in bold, so the list repeated them. The
  list also had no entry for a Control row fault, and step 8 covers only one
  case of it.
- A new step 8: "For a **Control row** problem on a ruling with no
  `rejected_because`, answer each row the draft was shown once, in
  `refused_controls` with a reason, and name no other row. Move a
  `control_row` to `refused_controls`." The old step 8 becomes step 9.

The held phrase "a `rejected` ruling owes its reader" is unchanged. The body
is 1367 tokens by `estimate_tokens`, against 1396 on `main` and a cap of 1400.

The run: `run_e3.sh` is `run_treatment.sh` with the run worktree at `main`
plus `treatment-e3.patch`, the `e3-` prefix and the same cap of $0.30. The
same 12 calls, the same `faults.json`, the same route (OpenRouter,
`openai/flex`, `openai/gpt-5.6-terra`). Expected cost: about $0.17. The
code change does not run in E3. Each faulted draft in the 12 views has no
`rejected_because`, and the code change acts only on a rejected ruling.

The counting rule is `count.sh` of E1, unchanged. Each of the 12 files is read
by hand against its `count.sh` line before the totals.

**The falsifier.** "The shorter step repairs the Control row faults as the E2
step does" is refuted if E3 repairs 11 or fewer of the 12 calls, or has 1 or
more new problem, or has 1 or more unbriefed change.

What E3 does not measure: the drop of the fault list in step 1 on the other
fault kinds (dropped, invented, duplicate, unresolved unknown, verdict shape,
confirmed exclusion), and a Control row fault on a ruling rejected for
`control`.

## E3 result, added after the run

The run read `e8cad1a2` with `treatment-e3.patch` applied (`clean: false`;
the `prompts/recritic.md` input digest `b20ec3cb` is the patched file). It
spent $0.1658 for 12 calls.

Each of the 12 files was read by hand against its `count.sh` line before the
totals. Each answer returns only the faulted draft, with `rejected_because`
empty, `control_row` empty and the shown row alone in `refused_controls` with
a reason. The related unknowns are the first pass's (3 on I-02, 2 on R-03). On
1 call (`e3-R-03-row-unanswered-1`) the re-ask also adds a sentence on the
Control to the draft's reason. The check accepts this, and the counting rule
does not count it.

**E3:** 12 of 12 repaired, 0 new problems, 0 unbriefed changes, 0 status
changes, 0 unread answers. `row-on-other-step` is repaired 4 of 4. The
falsifier does not hold, so the hypothesis is supported.

## E4 design, fixed before the first call

PR #2192 (`b8319a8f`) cut the list of faults from step 1 of
`prompts/recritic.md` to fit the token cap. Step 1 now reads "Each names a
draft ID and its fault." No run measured that cut on the fault kinds other
than the Control row. A standards read also found that step 8 covers a
Control row problem only "on a ruling with no `rejected_because`". So a ruling
rejected for `control` with a row unanswered, or with a row it was not shown,
has no step. The maintainer ruled on 2026-11-05: measure it. The maintainer's
$2.00 grant of 2026-11-05 covers E4: spent $1.2564; E4 cap $0.50.

**The question.** Does the cut of the step 1 list make the re-ask repair fewer
faults of the other kinds, or add problems or unbriefed changes?

**The instrument.** `run.py recritic-replay` with the eleven fault shapes of
#2196. The first pass is case 05's, as in E1 to E3, from the same archive and
with the same Control. `faults-e4.json` is `faults.json` with `control_reasons`: the
reason a ruling of I-02 rejected for `control` gives.

**The seven views**, one fault each:

- On I-01 (a `needs-info` draft that is shown no Control row): `dropped`,
  `invented`, `duplicate-id`, `verdict-shape` and `unresolved-unknown`.
- On I-02, rejected for `control` on its `information-disclosure` row:
  `control-row-missing` (no `control_row`, so the row is unanswered) and
  `control-row-not-shown` (`refused_controls` also names the `repudiation`
  row, which I-02 was not shown).

Offline, the round's check names exactly the expected problems for each view
(`EXPECTED` in `evals/harness/recritic_replay.py`), and the command refuses a
view otherwise.

**The sixth kind is not run.** A `confirmed` ruling on a draft that rules its
unit out needs a draft that excludes its unit. No STRIDE draft does that
(`Claim.excludes` is the neutral answer), and no archive holds an ASVS first
pass of case 05. So `confirmed-ruled-out` is refused on case 05, and E4
measures five of the six other kinds. That view needs an ASVS archive.

**The two arms.** Each arm runs the same seven views:

- control: `prompts/recritic.md` with `control-e4.patch`. The patch puts back
  the step 1 list of `b8319a8f^`. Step 8 stays as on `main`.
- treatment: `prompts/recritic.md` as on `main`.

The route is the route of E1 to E3: OpenRouter, `openai/flex` upstream,
strong tier `openai/gpt-5.6-terra`. `run_e4.sh` runs repeat 1: each view once
in each arm, the control first. It runs repeat 2 only if repeat 1 spent $0.20
or less. It stops before a call that could take the total over $0.50 (the
total plus $0.10). It stops on a non-zero exit, a missing charge or a credit
error, and it does not switch route or model. Expected cost: about $0.02 a
call, $0.30 for 14 calls.

**The counting rule** is `count.sh` of E1, unchanged, read per arm and per
fault kind. Each file is read by hand against its `count.sh` line before any
total.

**The falsifier.** The cut does harm if the treatment repairs fewer of the
five other kinds' views than the control, or if the treatment has a new
problem or an unbriefed change that the control does not have.

**The two control-rejection views** are reported apart and do not enter the
falsifier. Neither arm has a step for a Control row on a ruling rejected for
`control`. A view the treatment does not repair says that step 8 needs to
cover a `control` rejection.

**The commit read.** Both arms read `523c6650` (#2196 merged). The control
arm adds `control-e4.patch`, so its record reads `clean: false`. Offline, all
seven views build in both run worktrees, and `confirmed-ruled-out` is refused
on I-01 ("does not rule its unit out").
