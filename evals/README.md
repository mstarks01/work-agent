# Evals

How we measure the analysis quality: a corpus of "golden" cases, a scorer that
compares the service's output against them, a deterministic identity rule that
decides when two threats describe the same thing, and a vote ledger that holds
what a person decided about each finding.

> For **anyone reading or quoting these numbers**. It explains what the harness
> measures and what each number does not mean; it is not a procedure. To
> contribute one, start at [`../CONTRIBUTING.md`](../CONTRIBUTING.md).

## Read this before you quote a number

**An agent wrote all of it, and a person has read 30 of the 339 match labels.**
Every golden case, every reference claim and every calibration label was written
by an agent. One review sitting has happened:
[`calibration_labels/REVIEW-01.md`](calibration_labels/REVIEW-01.md), on
2026-08-18, over the 30 hardest pairs. It answered 25 `same`, 1 `different` and 4
`unclear`, and it changed two things — a wrong label, and a reference claim in
case 04 that asserted a fact its own model does not hold.

**One case has been through `BLESSING.md` step 6, and twelve have not.** That is
the reading session over a case's source, model and reference sets together, and
it is what would catch the case-04 defect anywhere else. That act is a **Case
Sitting**, recorded as an entry in the `reviews` list in the case's
`case.json` — who read it, the digest of each file they read, and the filled
document as evidence. Case `01-payments-checkout`, the control, carries one
dated 2026-08-23: the reader agreed with all 21 STRIDE claims and all 17 ASVS
records, and changed none of them
([`REVIEW-02.md`](corpus/01-payments-checkout/REVIEW-02.md)).
`tests/test_case_review.py` names the other twelve as unread, fails a new case
that arrives without a sitting, and fails a read file that changes under its
recorded digest.

So every agreement figure the suite produces is **self-consistency, not
accuracy**: it measures how closely a rule reproduces what an earlier agent
wrote down. That includes the 90% bar. A rule at 92.5% agrees with an agent's
opinions 92.5% of the time, and sitting 01 is the only evidence anywhere that
any of those opinions are right.

**Two live sweeps exist, and neither is a quality standard.** The service has
been swept live twice: `claude-opus-4-6` on 2026-08-14 (12 cases) and
`gpt-5.6-luna` on 2026-08-23 (13 cases), both STRIDE only and both
`analysis` mode. The second used the cheapest model its vendor sells,
deliberately — it was measuring whether the *machinery* holds, not how well the
service analyses. Its recall and groundedness figures describe luna and nothing
else. What the pair does establish is that the mechanical gates held on both:
zero mis-shaped grounds, zero structural failures, and an unverified-quote rate
of 2.0% and 3.4%. [`TUNING.md`](TUNING.md#choosing-a-model-to-sweep-with) says
which questions a cheap model may answer and which it may not.

**There is no model judge.** Claim matching is `SubsetVerbIdentity`, a rule in
`harness/identity.py`; it scores 185/200 against the recorded labels
(`python -m evals.harness.run calibrate`), over the 90% bar, with no provider
call. Whether an unmatched finding is real is a question about prose, and a
person answers it: each unmatched finding is keyed by its fingerprint and
looked up in the vote ledger (`harness/ledger.py`). A finding nobody voted on
is `unvoted` — visible, non-gating, and served by the review queue. The
retired LLM judge and the vendor question it carried are recorded in
[ADR 0003](../docs/adr/0003-no-privileged-vendor.md).

Why it drifted, and the rule taken from it, are in
[docs/agents/provenance.md](../docs/agents/provenance.md): `bootstrap` on
`case.json` recorded the agent stand-in honestly for a year because it was a
required field, while the reviewer claim beside it was prose and did not.

The numbers are still worth having. They track movement, they compare
configurations, and they fail loudly when a change breaks something. They are
not evidence that this tool finds real threats, and they must never be quoted
against another tool's published figures. Where this file and the ones beside it
say "the labels" or "the reference set", read *what an agent recorded*.

Nothing in this directory ships in the production image — the corpus, the
ledger, and the scorer are test-side only, and the package build takes just
`src/analysis_service`. Three companion guides:

- **[BLESSING.md](BLESSING.md)** — how to author a new golden case, including a
  reference set per framework it declares.
- **[VOTING.md](VOTING.md)** — how to hold a review sitting: what each answer
  moves, what the four standings do to a number, and how to re-key the ledger
  when the match rule changes.
- **[TUNING.md](TUNING.md)** — how to use these evals to improve the models. Read
  it per instrument: every score is computed offline, and only producing
  a fresh sweep's reports needs credentials.

## Layout

```
evals/
  README.md                     this file
  BLESSING.md                   how to author a golden case
  TUNING.md                     how to tune the models against the corpus
  verify_corpus.py              mechanical checks over everything below
  corpus/<NN>-<slug>/
    source.md                   the submitted text
    model.json                  the blessed System Model (passes the shipped validator)
    claims/<framework>.json     that framework's reference set, keyed to model.json's IDs
    corrections.md              notes on how the model was corrected, and why
    case.json                   metadata, provenance, and the declared sources
  calibration_labels/
    build_pairs.py              the match fixtures and their labels (edit this)
    pairs.json                  generated from build_pairs.py (never hand-edit)
  harness/                      the scorer and the eval runner
  review/voters.toml            the roster: voter → standing, the only place standing lives
  review/votes/                 the vote ledger, one file per voter — the only human record here
  baselines/README.md           the published comparison over every merged Baseline (generated)
  baselines/<derived-name>/     merged Baselines: up to ten sweeps with their reports, per configuration
  runs/                         local sweeps (gitignored) — the private scratch area
```

## The harness

| Module | What it owns |
|---|---|
| `harness/reference.py` | The `ReferenceThreat` type and the fail-closed corpus loader. |
| `harness/structural.py` | The structural gates — the only checks that fail a run. |
| `harness/scorer.py` | The scoring pipeline: prefilter → rule → match → standing → severity. |
| `harness/pairing.py` | The reading view behind one applicability disagreement: every requirement the run applied that the case did not expect, and every one the case expected that the run did not deliver, each with the standard's text and the argument made for it. Scores nothing and rules on nothing. |
| `harness/applicability.py` | ASVS's scorer: a confusion matrix over a finite catalog, matched by requirement ID. A closed claim set needs no composed identity, so no rule runs here. Carries the `disposition` instrument beside it, which reads what the run concluded rather than whether the requirement applies. |
| `harness/critic_yield.py` | What the critic added and removed, scored on both sides. |
| `harness/grounds.py` | What the category agents did with `grounds` — the branch mix, the padding number and the unverified-quote rate — plus the two failures the grounding path kills a case with. |
| `harness/coverage.py` | What each category agent was offered and how much of it its drafts cite, pooled over the sweep. |
| `harness/filler.py` | Whether a package's required justifications say anything: questions pointed at an attribute the evidence catalog would refuse, and how concentrated its grounds are. |
| `harness/stability.py` | Run-to-run stability: which references two or more finished sweeps agree on. Reads artifacts rather than re-running. |
| `harness/triggers.py` | Candidate-trigger recall: whether `analysis_service.candidates` fired a rule in a reference claim's own lane, on an element that claim names. Costs no provider call. |
| `harness/calibration.py` | Rule-vs-label agreement over the labelled fixtures — the scoreboard any rule change must clear. |
| `harness/verbs.py` | The closed vocabulary of attacker actions, and what counts as one action. |
| `harness/identity.py` | Claim identity from the fields a claim carries. `SubsetVerbIdentity` scores 185/200 against the recorded labels, over the 90% bar, with no model call. |
| `harness/fingerprint.py` | A **Claim**'s identity as a versioned value code computes. No model call. |
| `harness/ledger.py` | The append-only record of what a **person** decided about a finding. One file per voter, named by the GitHub login. |
| `harness/roster.py` | Voter → standing, read from `review/voters.toml` — the only place a standing lives. Refuses an unrostered voter rather than defaulting one. |
| `harness/baseline.py` | A **Baseline**: one directory under `baselines/`, one configuration, up to ten sweeps. Computes the five-part identity and the derived name, assembles the directory, and verifies that everything recomputes — never that a model ran. |
| `harness/prices.py` | Unit prices from litellm's offline map, and the one cost rule submit and CI both run. A model the map misses prices at `None`, never at zero. |
| `harness/sitting.py` | Holding a **Case Sitting**: which files it must read, the digest of each, what a reader may say about one recorded finding and the key that mark files under, the append-only entry that records it, the line it clears in the unreviewed list, and the rail of every case with the status the clearing rule gives it. `webapp/sitting.py` is one surface over it; the shell path writes the same files. |
| `harness/comparison.py` | The published comparison over the merged Baselines, generated into `baselines/README.md`. Walks the instruments table for its columns, so it names no framework and no column. A test fails a stale copy. |
| `harness/standings.py` | What a **Standing** does to a number: the series table, the ledger narrowing each series reads, and the pairwise agreement report a maintainer reads before a promotion. It promotes nobody. |
| `harness/consent.py` | The estimate gate: what a sweep is expected to cost, labelled `recorded` / `estimated` / `unpriced`, and the affirmative acceptance that lets it spend. Holds the run to the accepted amount between cases. |
| `harness/queue.py` | Which findings a reviewer is asked about, and in what order. Blind to the configuration. Several sweeps of one configuration are merged here, which is where a finding's run count comes from. |
| `harness/writing.py` | What reviewers said about how a finding reads, per case and framework. The one number a style down-vote moves, and it moves no other. |
| `harness/instruction.py` | How much instruction each node was given, per framework, per sweep. The drift alarms of ADR 0016, read as a measurement. |
| `harness/instruction_delta.py` | What one prompt edit did: which node's instruction moved, and what moved with it. |
| `harness/provenance.py` | What each node execution actually ran on — tier, requested route, served build, fingerprint — written into the artifact and read back by a promotion. |
| `harness/certify.py` | Promoting a winning configuration: rewrites `config/sampling.toml` and records its fingerprints as blessed. The certification check itself lives in the service (`analysis_service.certification`), which this imports. |
| `harness/modes.py` | The three run modes over the shipped graph, and the extraction score: element agreement, the derived crossings, and the attributes a Candidate rule reads. |
| `harness/instruments.py` | Every measurement a sweep reports, as one table keyed by instrument — the per-case row, the fold, the rendering, and the artifact keys each one owns. |
| `harness/artifact.py` | The sweep artifact: one declared shape, written once by `build` and read back through `load_artifact`, which refuses a file missing any declared key. |
| `harness/run.py` | The command-line entry point. `score` re-reads the ledger over a finished sweep's saved reports, so a vote reaches the numbers without a second sweep. |
| `harness/submit.py` | `submit <kind>`: runs a contribution's CI checks locally as a checklist, then packages the kind's allowlist on a fresh branch and opens the PR through `gh`. Kinds live in a table: `vote`, `sitting`, `baseline`. `verify-contribution --author` re-runs those checks in CI against the login GitHub says opened the PR. |

Sampling parameters are **not** here: the harness reads `config/sampling.toml`
at the repo root, the exact same file production reads. Grading a configuration
you don't ship is how a test suite goes green while production quietly drifts.
The matching rule is versioned in the fingerprints it produces
(`harness/fingerprint.py`), so a rule change is a visible re-keying event
rather than a silent re-score: `rekey` recomputes every vote's key from its
stored components, offline and with no re-vote.

Every run also records which model builds the provider actually served, not just
which ones it asked for. Stable model identifiers name the current build rather
than a frozen one, so two runs that report different served versions have run on
different models — that's a model change, not a regression, and nothing else in
the output would reveal it.

Those served builds land in the artifact's `provenance` block, one entry per node
execution, which is what makes promotion a command rather than an archaeology
exercise: a fingerprint is `sha256(served build, tier sampling)`, and neither half
is recoverable from the configured tier strings. The block carries an
`artifact_version` beside it at the artifact's root; a promotion refuses any
version it does not know rather than interpreting it best-effort.

**And which repository state produced it.** `provenance` says which models
answered. `repo_commit` and `corpus_digest` say what they were asked — the
prompts, the reference sets and the tiers a sweep ran against. Without the
pair, two sweeps can differ by vendor, prompt edits and corpus size at once
and the artifact cannot tell you which, so a comparison between them is not a
model comparison and nothing in the file says so.

Both are resolved before the sweep starts. A repository that cannot name what
it is about to run stops in the first second rather than 90 minutes later,
holding an artifact that cannot name its own prompts.

`repo_commit` carries `clean` beside the commit, because running a sweep over
uncommitted prompt edits is the ordinary tuning loop rather than a mistake —
[TUNING.md](TUNING.md) step 3 asks for exactly that. The artifact records that
the tree did not match the commit instead of pretending it did.

`corpus_digest` covers every corpus file a number is computed from, by
subtraction: a file added to a case counts until somebody rules it out. Only
`corrections.md` and the reading documents are outside it, since no number
reads them.

An artifact taken before this was recorded carries `unrecorded` in both, which
is a required field honestly stating a deficient provenance — the design
`bootstrap` uses on `case.json`. A commit inferred from a run's date would be a
guess, and indistinguishable from an observation once written down.

## Running

Offline — no credentials, safe on every change:

```sh
python evals/verify_corpus.py                 # check every case and the match fixtures
python evals/verify_corpus.py --write-sha     # restamp source digests after editing a source
python evals/calibration_labels/build_pairs.py # regenerate pairs.json from the labels
pytest tests/test_evals_*.py tests/test_corpus_lints.py
```

Live — needs credentials for whichever vendor the tiers are configured to use
(see [Configuration](../docs/Configuration.md#provider-environment)):

```sh
python -m evals.harness.run run --mode analysis --out artifact.json
python -m evals.harness.run run --mode extraction --case 01-payments-checkout
```

Scoring itself is offline: matching is the identity rule, and the standing of
each unmatched finding comes from the vote ledger. So a finished sweep can be
scored again, against the ledger as it stands now, with no provider call:

```sh
python -m evals.harness.run score artifact.json
```

`calibrate` is offline too — it prices the rule against the recorded labels and
gates a rule change on the 90% bar:

```sh
python -m evals.harness.run calibrate --out agreement.json
```

A `run --out artifact.json` also writes `artifact.reports/<case>.report.json`
and `artifact.reports/<case>.drafts.json` — one whole report for each case that
finished, and the drafts its critic was handed. The artifact holds the
aggregates this harness computes; the pair holds what the agents said, so a
question the metric set did not anticipate is answered offline instead of
costing a second sweep, and `score` can recompute every scored reading from
them. `extraction` mode stops at the validity gate, produces no report, and
says so. Expect roughly 30–80 KB per report. These files are publishable: they
carry corpus source text, which is in this repository.

An `extraction` run prints element recall and precision per case, then the
attribute agreement split by attribute. Read the split first when the element
numbers look clean: an extraction that names every element and types none of
them correctly scores 1.00 on both, and a `kind` or an `exposure` the live
pipeline stopped producing appears only in that column. Every number in it is
an instrument. It carries no threshold and fails no run — a low agreement is a
question to take back to the source text.

Offline again, once a sweep has been reviewed — `promote` needs no credentials,
because everything it certifies was observed during the run and written into the
artifact:

```sh
python -m evals.harness.run promote artifact.json          # preview, writes nothing
python -m evals.harness.run promote artifact.json --yes    # re-pin and bless
```

See [TUNING.md](TUNING.md#step-5--promote-the-winner).

Also credential-free, over two or more finished sweeps of the same corpus —
what one sweep cannot tell you is how much of its own number is sampling noise:

```sh
python -m evals.harness.run stability run-a.json run-b.json --out stability.json
```

A `run` fails (exits non-zero) **only** on a structural problem — a report that
doesn't parse, references that don't resolve, a severity that contradicts the
matrix, or a summary that disagrees with its own contents. Every quality metric
below is computed, printed, and written to the artifact, but does **not** fail
the run: a gate that fires before anyone knows the normal range just trains
people to bypass it. `calibrate` is the exception — it fails below the 90%
rule–label agreement bar, because a rule that disagrees with the recorded
labels makes every other number meaningless. Read that bar as the top of this
file describes it: the labels are agent-authored and unreviewed, so the bar
measures agreement with them and not correctness.

## What the metrics mean

Every number here is measured **against the rule and the ledger** — use
them to compare configurations and track movement, never as absolute scores or
against another tool's published figures. The reference sets they rest on are
agent-authored and unreviewed.

**ASVS's differ only in mechanism.** The `applicability` block is a confusion
matrix over a finite catalog, matched by requirement ID: its claims carry
their identity, so no rule composes one. It is reported separately because a
catalog match and a composed match are not one kind of number.

**ASVS measures applicability and evidentiary reach, never pass/fail.** A job
here carries prose, and a prose-only service cannot verify that a control is
correctly implemented — so no ASVS number is a verification score, and the
corpus has no `pass` disposition to expect. The two blocks answer two different
questions, and neither substitutes for the other:

- `applicability` — **is this requirement in play for this system?** A
  `needs-other-evidence` scope entry counts as *applied* here: the lane raised
  the requirement, and the service withheld the claim for want of evidence the
  job does not carry. Withholding is not missing.
- `disposition` — **given that it is in play, did the run reach the right next
  action?** A run can score full applicability recall and still send the
  submitter to re-describe a property only the source code settles.

The disposition metrics read two things a report actually carries — a claim's
verdict, and a scope entry's state and `needs` field. They never read
`needs_evidence`, which the fan-in strips before the payload. Which kinds of
evidence become a scope entry is a policy `CARRIED_EVIDENCE_KINDS` sets, and
the scorer reads it rather than assuming that code, config and people are
always deferred.

- **disposition accuracy** — of the requirements a case judged and the run
  answered, how many got the right answer. Requirements the run said nothing
  about are reported as **unreached** and excluded, because applicability recall
  already charges them and one miss should not move two metrics.
- **false-prose-request rate** — the run asked for more description where no
  description could ever answer. The failure the block exists for: it reads to a
  submitter as *send more of what you already sent*. Denominated in the
  requirements that need evidence this job cannot carry, which are the only ones
  where it is reachable.
- **false-confirmed rate** — the run ruled a deficiency from prose that only
  code, config or a person could establish. Same denominator.
- **evidence-kind accuracy** — of those same requirements, how many the run
  routed to the right kind of evidence.
- **false-not-applicable rate** — the run ruled out a requirement the case says
  applies. Denominated in the judged requirements that are not expected to be
  `not-applicable`.
- **unjudged** — reference records carrying no expected disposition. Read it
  beside the accuracy figure: a small denominator and a good score are different
  facts.

An `applicable` scope entry — *considered, and nothing raised* — reads as
silence rather than as a seventh disposition, so a case cannot expect one. For a
requirement the case listed, that is the miss recall already counts.

**The complement is only a negative on an exhaustive set.** Each framework
declares `reference_set` in `case.json`, and it defaults to `sampled`, which is
what is true of a set nobody has read. On a sampled set a requirement the run
applied that the case did not list is a candidate for promotion, not a false
positive — the same rule `harness/scorer.py` applies to STRIDE's unmatched
threats, and for the same reason: scoring it as an error punishes finding real
things and pushes every tuning cycle toward under-reporting.

So **precision is reported only where the set says `exhaustive`**. Every case is
`sampled` today, so the `prec` column reads `n/a` and the pooled line names how
many of the cases it could read. The `over` column and
`over_applied_for_promotion` are unchanged either way: the list is a reading for
the next sitting, and only the rate over it is a score.

- **must-find recall** — did the tool find the threats a case marks as
  essential? Reported **per case**, never averaged: an average hides one case
  failing completely, which is the failure that matters most.
- **lane accuracy** — was each threat filed under the right STRIDE category? A
  misfiled threat counts as a category error, not a near-miss.
- **element accuracy** — did the threat cite the right element? Scored, but never
  used to reject a match — citing the process where the reference cited the flow
  at its endpoint still counts as the same threat.
- **rejected rate** — of the threats the tool produced that aren't in the
  reference set, how many did a person vote down for substance? The one "extra
  threat" standing that counts against the tool. It reads 0.0 over a cold
  ledger, beside an **unvoted** count that says how much of the answer still
  waits on a review sitting — a plausible, grounded extra never gates.
- **critic yield** — always read as a **pair**: how many junk threats the critic
  removed (good) *against* how many real threats it removed (bad). A kill count
  on its own tells you nothing about which of those two is happening.
- **near/far exemplar delta** — see below.

The **grounds** measurements each watch a prompt rule that nothing enforces
mechanically:

- **grounds per threat** — `analyze.md` asks for one ground per load-bearing
  fact with no padding, so this should stay low. Rising means the agents are
  filling the field rather than citing what triggered them.
- **quoteless rate** — the share of findings carrying no quote. A finding whose
  trigger was an unknown attribute or a boundary crossing is *correctly*
  quoteless, so read this **low with suspicion, not high**: a rate near zero is
  evidence the agents are manufacturing quotes to fill a required field.
- **unverified rate** — of the quotes the agents wrote, the share the shipped
  ladder (`analysis_service.grounding`) could not find in the source they name.
  Denominated in quotes, never in grounds.
- **dropped rate** — of every claim the lanes drafted, the share the service
  dropped for a fault in one entry: a proposal that failed its schema, every
  quote absent from its source, every reference outside the catalog, every
  element absent from the model, or an ID repeated. Read off the report's
  `dropped_claims` marks, so a dropped claim is counted, never a dead case.
- **failed cases** — the ways the fan-in still kills a case, counted rather than
  allowed to abort the sweep: a dangling element reference, a duplicate ID, an
  unresolvable source label. These remain structural failures, so a run that
  hits one still exits non-zero.

**Coverage** is a rate over the whole sweep rather than a
per-case number: it counts what deterministic code offered each lane —
candidates, elements, boundary crossings, unknown controls — against how much of
it the lane's drafts cite. Read it as *cited*, never as *considered*: an agent
that examined a lead and correctly rejected it cites nothing. The `rules fired`
column counts firings over evaluations — one rule against one case — and the
unambiguous reading of it is zero: a lane whose rules fire nowhere in the corpus
is reading a shape the corpus does not contain, or nothing at all.

**Token usage and latency** are folded per node across the whole sweep and
printed as two tables. They answer different questions about the same
executions: the dearest node is not the slowest one, and the deterministic
derivations cost no tokens while still costing the job its seconds.

**Filler** asks whether a package's required justifications say anything. A
mechanical check can be satisfiable by construction for one package's claim
shape and not for another's, and a model required to satisfy it finds the value
that always passes — so every check goes green and the output tells a reader
nothing. Two readings, both over the shared claim shape:

- **Ineligible pointers** counts `needs-info` entries naming an attribute the
  evidence catalog would refuse as a ground. The catalog admits only
  type-specific attributes, on the stated grounds that a note "is a sentence,
  not an unstated control", so a question pointed at `notes` names a field this
  repo has already ruled says nothing. **Read a non-zero here as a finding**:
  across fifteen archived STRIDE sweeps it is 0 of 378.
- **Ground concentration** is the share of claims whose grounds use the single
  most common combination of kinds. It is a number to judge rather than a
  threshold. STRIDE sits near 23%; a package justifying most claims one way is
  either reading a very uniform system or reaching for one filler.

It reads finished reports rather than a scored pass, because the defect it looks
for passes every offline check by construction — the suite scripts the agents,
so pointers always resolve and quotes always verify.

**Stability** needs two sweeps, so it is its own command rather than a metric of
one run. It compares which *reference indices* each sweep matched — a corpus
coordinate that means the same thing in every run, where a produced threat's ID
and wording do not — and splits every reference into matched-in-every-run,
matched-in-some, and matched-in-none. The middle bucket is the band a one-sweep
recall number can move within while nothing has actually changed, which is what
any comparison of two other numbers has to clear before it means anything.

## Reviewing findings

Everything above measures the tool against records an agent wrote. This is the
one loop that measures it against what a **person** says, and it needs no
credentials at all — it reads a finished sweep and writes one line per click.

```sh
python -m evals.harness.run run --mode analysis --out artifact.json   # needs credentials
python -m evals.harness.run review artifact.json --voter <your-name>  # what is waiting
uv run python webapp/review.py --voter <your-name> --artifact artifact.json
```

`review` is credential-free and read-only, like `promote` and `stability`. The
web app is where an answer is recorded, because a vote wants the source text
beside the finding.

When the identity rule changes, the ledger moves with it and costs no re-vote:

```sh
python -m evals.harness.run rekey --to-version 2        # preview, writes nothing
python -m evals.harness.run rekey --to-version 2 --yes  # rewrite
```

A vote stores the fields its fingerprint was computed from, so re-keying is
arithmetic over one file — no provider, no credentials, and every verdict, voter
and date survives. That property is what made the vote affordable: a
model-scored history re-scores whole every time its scorer changes, with no
way to recompute the old numbers.

The reviewer answers one question per finding — **could this attack happen in
this system?** — as up, down, unsure, or needs-more-evidence. A down-vote picks
one reason from a closed set, and the reason decides which number moves:

| Reason kind | Counts against the analysis? | Stays in the reference pool? |
|---|---|---|
| substance (`not-a-threat`, `unsupported-by-the-model`, …) | yes | no |
| style (`too-vague`, `poorly-written`, …) | **no** | **yes** |

That split is the control for personal preference, and it is mechanical. A
reviewer who dislikes a finding's wording cannot move recall with that opinion,
because the reason they picked routes it to the writing score instead.

Three properties make the record worth keeping:

- **A vote is spent once.** It hangs on a **fingerprint** — framework, lane and
  endpoint-resolved elements — not on a run, so it stays valid when the wording
  moves, when the model changes, and when a prompt is rewritten. The second
  sitting over one configuration shows nothing; a sitting after a change shows
  only what changed.
- **Nothing is ever overwritten.** A correction is a new event, so any past
  state of the ledger is reconstructible, and the numbers computed from it can
  be recomputed to the same digit.
- **The reviewer is blind to the configuration.** `QueueItem` has no field for a
  model name, and the vote is stamped with it afterwards.

`docs/agents/claim-identity.md` holds the design and the measurements behind it.

## The corpus

Thirteen cases, each sized so a person can enumerate its threats exhaustively
(roughly 8–20 elements).

The shipped prompts teach by example, and every lane's `exemplars.md` works its
threats against one of **two** systems: a payments system, and an event-driven
sensor fleet the exemplars call system B. A tool shown an architecture may find
that architecture's threats more easily. You cannot read that bias off an
absolute score — only off the **gap** between recall on the near cases and
recall on the far ones.

So the corpus carries one control per exemplar system: `01` for payments and
`02` for the fleet. Neither is optional. Drop either and the near side loses a
system to compare against. `exemplar_proximity` sits on the (case, framework)
pair, because each framework writes its own exemplars — a case near STRIDE's
payments system is near nothing of ASVS's unless ASVS demonstrates that shape
too. The gap is tracked and watched, but never fails a build.

| Case | Domain | Proximity | Source |
|---|---|---|---|
| `01-payments-checkout` | payments | **near** | synthetic |
| `02-iot-fleet-telemetry` | IoT fleet | **near** | synthetic |
| `03-batch-data-pipeline` | batch data | far | synthetic |
| `04-ml-inference-service` | ML serving | far | synthetic |
| `05-cookbook-queue-webapp` | web app + queue | far | OWASP Threat Model Cookbook |
| `06-cookbook-online-game` | online game | far | OWASP Threat Model Cookbook |
| `07-cicd-store-deploy` | CI/CD release | far | synthetic |
| `08-sso-identity-broker` | identity & access | far | synthetic |
| `09-cookbook-sokify-retail` | online retail | far | OWASP Threat Model Cookbook |
| `10-cookbook-generic-cms` | content management | far | OWASP Threat Model Cookbook |
| `11-sparse-shift-scheduling` | workforce scheduling | far | synthetic (sparse input) |
| `12-overclaiming-supplier-portal` | supplier management | far | synthetic (over-claiming input) |
| `13-dispatch-control-plane` | field service dispatch | far | synthetic |

The Cookbook cases come from the [OWASP Threat Model
Cookbook](https://github.com/OWASP/threat-model-cookbook) (CC-BY 4.0), converted
to the kind of prose a user would submit; each case records its exact source and
licence in `case.json`. Cases `11` and `12` are deliberately adversarial — one
with input too sparse to model confidently (which should yield `needs-info`
threats, not invented ones), one that over-claims security controls the text
doesn't justify.

The corpus checks in `verify_corpus.py` are deterministic and need no
credentials, so they run on every PR (via `tests/test_corpus_lints.py`) and stay
runnable by hand.

## Things to keep in mind

- **The metrics are rule-and-ledger-relative.** Recall and precision move
  *with* the identity rule's version and with what the ledger holds. They're
  valid for tracking change and comparing configurations, not as absolutes and
  not comparable to other tools' figures.
- **Reference sets grow from real output.** They aren't meant to be exhaustive up
  front. Each run surfaces grounded, plausible threats it produced that aren't in
  the reference set; recurring ones get promoted into the reference set on the
  next blessing pass (see [BLESSING.md](BLESSING.md)). Promotion is always a
  reviewed change with a human explaining why.
