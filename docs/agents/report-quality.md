# Report quality: state, mechanisms and evidence

This page is the entry point for anyone who reviews or continues the work on
the quality of STRIDE findings. It states what is measured, what the current
figures are, which mechanisms in the pipeline exist for quality, what is still
open, and where each piece of evidence is. The figures are true at commit
`d44bbee` (2026-09-27). The experiment ledger holds each measurement in full.

## How quality is measured

**Structural coverage.** The scorer matches a finding to a reference claim by
lane, action verb and endpoint-resolved place (`evals/harness/scorer.py`). It
reads no prose. `must_find_coverage` is the share of must-find reference
claims that some finding matches.

**Coverage by meaning.** A person reads a matched pair side by side and rules
`same`, `different` or `unsure`. This is the only reader of whether a finding
states the reference's threat. On Baseline `6bff717`, 12 of the 61 matches the
scorer makes through a claim's own reading state a different threat
(`QA-2026-09-26-01-E1`). So quote a structural figure as structural, and read
the rows that change by meaning before any decision.

**Holdout cases.** Cases 14 and 15 carry `holdout: true`. Every sweep scores
them and reports them apart (`holdout_split`), and no diagnosis command reads
them. A gain that the tuned cases show and the holdout cases do not was shaped
by the cases it was read on. See **Holdout Case** in `CONTEXT.md`.

**Instruments for one lane or one critic call.** `run.py lane-replay` sends one
captured lane request again, and `--append` adds a user part after the captured
input. `run.py critic-replay` does the same for a critic call. The pattern A
branch (see open work) adds `--fresh-leads`, which rebuilds a lane's leads with
today's rules through the function the prepare node calls. A replay costs one
model call, about $0.03 to $0.06 on the strong tier.

**The ledger.** `evals/experiments/` holds one JSONL file per audit. Read it
before a new experiment: `python -m evals.harness.run experiments --signature
"<the failure>"`. A refuted row is an answer that saves a paid run.

## Current figures

| Measurement | Value | Source |
|---|---|---|
| Baseline `6bff717`, must-finds by structure | 71 of 103 | ledger `QA-2026-09-26-01-E1` |
| Baseline `6bff717`, must-finds by meaning | 59 of 103 | ledger `QA-2026-09-26-01-E1` |
| Sweep B (commit `7ec9f22`), tuned cases by structure | 72 of 103 | `evals/experiments/QA-2026-09-26-01.md`, E2 |
| Sweep B, holdout cases by structure | 9 of 14 (case 14: 3 of 7, case 15: 6 of 7) | `evals/experiments/QA-2026-09-26-01.md`, E2 |
| Completeness instruction, missed must-finds recovered by meaning (30 lanes, two passes) | 8 and 8, against 3 and 4 for a plain repeat | ledger `QA-2026-09-26-01-E2` |
| Completeness instruction end to end (cases 02, 05, 10) | must-finds 13 to 15; lane proposals 44 to 84 | ledger `QA-2026-09-26-01-E5` |
| Sweep B, tuned cases, findings that are conditional (needs-info) | 175 of 200, on 129 open facts | ledger `QA-2026-09-26-02-E1` |
| Lane closing on cases 02, 05, 10: needs-info, confirmed, open facts | 38 to 78, 6 to 6, 28 to 44 | ledger `QA-2026-09-26-02-E1` |
| Share of needs-info findings six ranked questions settle (cases 02, 05, 10) | 76% without the closing, 63% with it; ten settle 82% | ledger `QA-2026-09-26-02-E2` |

The holdout figure measures agreement with references that nobody has read yet
(see open work below). The corpus spread is about 3.4 must-finds per sweep, so
one sweep cannot separate a change smaller than that from sampling.

## Mechanisms in the pipeline

**Lane closing (ADR 0030).** Each STRIDE lane reads one instruction last, as
the final part of its user turn: address every lead and every boundary
crossing, and file a finding that rests on an open fact as a conditional draft.
The text is `frameworks/stride/lane_closing.md`, the table that selects it is
`LANE_CLOSING_DOC` in `analysis_service.frameworks`, and a callback on the lane
agents adds it. The position matters: the same words inside `output.md` raised
lane output 18%, where the final user part raised it about 75%
(`QA-2026-09-26-01-E4`, `-E5`). ASVS closes nothing, because its scope table
gives every selected requirement an entry. Cost: reports about twice as long,
mostly conditional findings, and about 20% more per lane call.

**Grouped conditional findings.** `analysis_service.open_facts` groups a
report's needs-info findings under the open fact that would settle them, and
the first-run report page shows each conditional finding once, in a
collapsible group per fact. It reads only the report, so it cannot disagree
with the verdicts. This is the partner to the lane closing.

**Holdout filter.** `reference.diagnosable` is the one reader of the holdout
rule. Diagnosis commands filter through `tuning_cases`, and
`tests/test_holdout_cases.py` resolves every `load_corpus` call under `evals/`
so a new caller must filter or state its reason.

## Known losses

**Pattern A, access path against storage copy (4 must-finds).** Four
references (02/6, 05/6, 05/7, 12/8) name a read grant or a compromised reader.
Every run, with or without the completeness instruction, writes a
storage-copy threat there instead (`QA-2026-09-26-01-E3`). The cause is the
only store lead, whose question asks who reaches the storage layer. A fix is
built and not merged; see open work.

**Prose-only mismatches (8 rows).** A different principal impersonated, a
different resource flooded, or a defining part missing
(`QA-2026-09-26-01-E1`). They share place and verb with the reference, so no
rule can see them. A person is the only reader.

**Verb filing.** A lane sometimes files the right finding under the wrong verb,
for example `impersonate` where a held credential makes it `use-credential`
(case 02 ref 0 in `QA-2026-09-26-01-E5`). `run.py near-misses` issues a ballot
of such rows, and a signed ruling in `rulings.json` records the answer.

**Earlier classes.** `QA-2026-09-25-01-E5` classes the 36 misses of Baseline
`6bff717`: absent (9), a different attack (9), another verb or place (8),
joined facts (4), critic kills (3, now recovered by merged code) and one
fan-in loss.

## Open work

| Item | State | Where |
|---|---|---|
| Pattern A lead | Built on branch `pattern-a-store-readers` (commit `897fdc3`); 12 replays done ($0.54); waits on a blind 47-row ballot | #1239 item 1 |
| Case Sitting on cases 14 and 15 | Round 1 prepared; waits on the maintainer | #1239 item 2 |
| Vote on the findings the lane closing added | Not built; the cost side of ADR 0030 | #1239 item 3 |
| Full 15-case sweep with the lane closing | About $5.50; gives the first holdout reading with the closing | after OpenRouter credits allow |
| Intake questions | Ranking design measured offline; not built | #1225 |
| Readable report remainder | Plain summaries and progressive disclosure | #561 |
| Assertion flag and reference edits | Parked by decision | #1231 |

## Evidence index

**Ledger rows:** `evals/experiments/QA-2026-09-26-01.jsonl` (E1 to E5) and its
report `evals/experiments/QA-2026-09-26-01.md`; `evals/experiments/QA-2026-09-26-02.jsonl` (E1, E2,
the report side of the lane closing) and its report; the earlier audit
`evals/experiments/QA-2026-09-25-01.jsonl`.

**Human rulings, committed:**
`evals/experiments/QA-2026-09-26-01/E1-must-find-matches-by-meaning.csv` (61
pairs, verdict and note per pair) and
`evals/experiments/QA-2026-09-26-01/E2-sentence-against-control-by-meaning.csv`
(40 pairs; the `arm` column is S for the instruction and C for the control,
added after the blind ruling).

**Pull requests:** #1230 (`lane-replay --append`), #1232 (holdout mechanism),
#1233 (holdout cases 14 and 15), #1234, #1235 and #1236 (ledger rows), #1237
(lane closing, ADR 0030 accepted), #1238 (grouped conditional findings).

**Issue comments with measurements:** #890 (match-by-meaning ballot), #1225
(question ranking and its stability across two critic samples), #744 (holdout
phase 1), #201 (identity frontier after cases 14 and 15).

**Run artifacts, local only.** The sweeps and replays are not tracked: sweep B
is in the local directory evals/runs/20260926T-sweep-b, the end-to-end runs in
evals/runs/20260926T-adr0030-confirm and evals/runs/20260926T-adr0030-closing,
and the pattern A replays with their pending ballot and arm key in
evals/runs/20260927T-pattern-a. A reviewer on another machine reads the ledger
rows and the committed rulings instead.

## How to review

1. Read `QA-2026-09-26-01.md` and check each row's `outcome` against its
   `falsifier`.
2. Check the rulings: the verdict counts in the two committed CSV files must
   give the figures above (E1: 49 `same`, 12 `different`; E2: sentence arm 8
   and 8, control 3 and 4, by distinct missed must-find).
3. Read ADR 0030 and the lane-closing code path: the table, the callback in
   `analysis_service.graph`, and the test in `tests/test_evals_lane_replay.py`
   that a STRIDE lane's request ends with the closing and the critic's does
   not.
4. Run `python -m evals.harness.run experiments --signature "a lane passes by
   a lead it was offered"` and confirm each row reads `current` or explains
   why it is `stale`.
