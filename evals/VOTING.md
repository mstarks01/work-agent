# The vote: how a person scores a finding

> For **anyone scoring findings**, maintainer or outside contributor. It is
> free and offline: a merged **Baseline** keeps its reports in this repository,
> so voting needs no provider key and spends nothing. What there is to score is
> what [`baselines/README.md`](baselines/README.md) lists, which is generated
> from the merged Baselines. New here? Start at
> [`../CONTRIBUTING.md`](../CONTRIBUTING.md).

A sweep produces findings. The identity rule
(`evals/harness/identity.py`) matches a finding against the case's reference
set, and a match is a recall hit. The rule stops there. It cannot say whether a
finding that matches nothing is a real threat or noise. That question is about
the system, and the rule only compares two claims.

**A person answers it, one finding at a time.** The answer goes into the vote
ledger — `evals/review/votes/`, one file per voter. Nothing else in this
repository holds a human judgement, and no model has a vote.

This guide is the procedure: how to hold a review sitting, what each answer
moves, and what to do when the rule itself changes. For the design behind it,
read [docs/agents/claim-identity.md](../docs/agents/claim-identity.md). For the
tuning loop that consumes these numbers, read [TUNING.md](TUNING.md).

## What you need

- **A finished sweep**, written with `run --out artifact.json`, together with
  the `artifact.json.reports/` directory beside it. The queue reads the reports,
  because the reports hold the claims and the artifact holds only the totals.
- **Every sweep of that configuration, if you ran more than one.** Both
  commands below take one artifact per sweep. The counts that follow — which
  findings every run produced, and which only some did — exist only when you
  name them all.
- **A name to vote under.** The ledger is never anonymous.
- **`uv sync`.** The review app needs FastAPI and uvicorn.

Only the sweep needs credentials. Every step below is offline, and none of them
calls a model.

## Step 1 — See what waits for you

```sh
python -m evals.harness.run review baseline-1.json baseline-2.json --voter ada
```

It prints how many findings wait for you, broken out per case, and what the
ledger already holds:

```text
412 findings waiting for ada, over 5 sweep(s)
  37 found in some runs and not others
    01-payments-checkout               38
    02-iot-fleet-telemetry             41
    ...

ledger: 0 votes by nobody; 0 findings in the pool; 0 answered twice
```

Read the per-case counts first. A sitting is usually one case, so the useful
question is which case you can finish in the time you have.

The second line is the one worth the most. A finding some runs produced and
others did not splits a recall number two ways, and your answer settles it.
Over a single artifact that line reads `0`, which is the honest reading of one
run.

The command answers for **you**. A finding another person voted on still waits
for you, which is how a second opinion happens.

## Step 2 — Hold the sitting

```sh
uv run python webapp/review.py --voter ada \
  --artifact baseline-1.json --artifact baseline-2.json
```

The app binds to `127.0.0.1:8010` and nothing else. Open
`http://127.0.0.1:8010/`, then start.

Each finding fills one page. On the left you get the case's source text, the
same words the agents read. On the right you get the finding: its title, its
description, the quotes it cites as grounds, and the elements it names. Above
both, one line tells you why this finding reached you.

**The page never tells you which configuration produced the finding.** The queue
item has no field for it, and the app stamps the configuration onto your vote
after you answer. A reviewer who sees the model can vote about the model instead
of about the finding.

**The question is the framework's own**, because the two do not rule on the
same thing. A STRIDE finding asks *could this attack happen in this system?* An
ASVS record asks *does this requirement apply to this system?* The page reads
the question off the finding, so you answer each one in its own terms.

You have four answers either way, and each records the same verdict.

| Answer | What it means |
| --- | --- |
| **Yes** — *this is real* (STRIDE), *it applies* (ASVS) | The claim holds for this system. |
| **No, or not as written** | You reject it. The app then asks for a reason. |
| **Unsure** | You read it and cannot decide. This is a real answer, and it is recorded. |
| **Needs more evidence** | The page did not show you enough to answer. It is not a verdict about the finding. |

Answer `Unsure` when you mean it. The first review sitting answered `unclear` on
4 of 30 pairs, and those four are what fixed the specificity rule in
[BLESSING.md](BLESSING.md). A two-button page throws that away.

### The reason decides what your rejection moves

A rejection needs a reason, from a closed list. The reason is the whole control
for personal taste, so pick the closest one honestly.

**"It is wrong"** — these count against the analysis, and the finding leaves the
reference pool:

| Reason | What it says |
| --- | --- |
| `not-a-threat` | this is not a threat to this system |
| `unsupported-by-the-model` | it asserts something the description does not say |
| `duplicate` | another finding in this report already says it |
| `wrong-lane` | real, but filed under the wrong category |
| `out-of-scope` | real, but outside what this system is responsible for |

**"It is badly written"** — these move no analysis number, and the finding
**stays** in the pool:

| Reason | What it says |
| --- | --- |
| `too-vague` | real, but too unspecific to act on |
| `poorly-written` | real, but the wording is wrong or confusing |
| `wrong-severity` | real, but rated too high or too low |
| `unhelpful-mitigation` | real, but the suggested fix does not help |

You cannot depress a recall number by disliking a sentence, and you cannot
inflate one by liking a sentence. That split is code
(`Vote.counts_against_analysis` and `Vote.joins_the_pool`), not a request in a
guide.

A style rejection lands in the **writing** block of the next sweep's artifact,
per case and per framework, with the reasons counted. It moves nothing else, and
that is the point of the split.

## Step 3 — Read what the votes did

Re-score the sweep you just voted over. It reads the ledger as it stands now,
costs no provider call, and rewrites the artifact in place:

```sh
python -m evals.harness.run score baseline-1.json
```

It recomputes only the readings that read the ledger — the standings, the
critic's yield and the writing numbers. Grounds, coverage and provenance are
facts about the run that no later vote changes, so they stay as the sweep wrote
them. Pass `--out` to write a second file instead of rewriting the first.

Every finding that matches no reference claim gets one of four standings, and
the artifact counts them per case:

| Standing | What it means | What it does |
| --- | --- | --- |
| `rejected` | somebody rejected it for a substance reason | The only standing that counts against the tool. It raises `rejected_rate`. |
| `pooled` | somebody says it is real | It joins the reference pool and feeds `unlisted_for_promotion`. |
| `open` | somebody answered `unsure` or `needs-evidence` | Nothing. It waits for a better sitting. |
| `unvoted` | nobody answered it | Nothing. It is visible in the counts and never counts against the tool. |

Both answers behind `open` move no number, and they part company in your queue.
`unsure` is a spent answer: you judged the finding and it stays judged. Needing
more evidence is not a judgement, so it holds for that sitting only, and a later
sitting asks again over whatever evidence exists by then. Pressing it is how you
say *not yet*, and it used to be the one answer that removed a finding from your
queue for good.

Two reviewers can disagree, and then `rejected` wins over `pooled`. A tool must
not score itself on the answer that flatters it most.

Your style rejections land beside the standings, under `writing`:

```sh
jq '.writing_aggregate' baseline-1.json
```

`objection_rate` is the share of the findings **somebody answered** that drew a
style reason. The denominator is answers rather than findings, so a sweep that
writes more findings cannot read as better written. `by_reason` says which
objection people made most.

Over a cold ledger `rejected_rate` reads `0.0`, beside an `unvoted` count that
says how much of the answer still waits on a person. Read the pair. A `0.0`
with 412 unvoted findings means nobody has looked yet, not that the tool
produced nothing wrong.

## Step 4 — Promote what a reviewer confirmed

The reference sets are not exhaustive, and they grow from real output that a
person confirmed:

```sh
jq '.unlisted_for_promotion' baseline-1.json
```

Every entry is a finding somebody voted into the pool: real, and absent from the
reference set. Add the recurring ones to the case's reference set, the way
[BLESSING.md](BLESSING.md) describes. Promotion consumes a judgement a person
already made, so this costs no new sitting.

Changing a reference set shifts every baseline. Re-run the baseline after it,
as [TUNING.md](TUNING.md) step 2 asks.

## What the queue asks first, and why

`evals/harness/queue.py` sorts by how much one click settles. The first reason
that applies wins, and the reasons never add up.

| Reason | Weight | Why it is worth your click |
| --- | --- | --- |
| `volatile` | 30 | The sweep found it in some runs and not others, so your answer settles which way a recall number should have gone. |
| `new` | 10 | Nobody has answered it, so it counts in no number. |

There was a third reason, `unmatched`, weighing whether the reference pool
already carried the finding. It is gone. The pool is built from votes and the
queue skips what is already answered, so the row either fired on everything or
told you how somebody else had voted — and the second opinion below is the one
pass that must not be told.

`volatile` needs more than one sweep to exist, which is why both commands take
an artifact per sweep. A finding every run produced is settled, and a finding
two runs of five produced is the question one click settles. Over a single
artifact every count is 1 of 1, nothing is volatile, and every finding sorts
under `new`.

The queue skips what **you** answered, never what anybody answered. Point a
second reviewer at the same artifact under their own name to get a second
opinion, and `run review` reports how many findings two people answered.

## The ledger

The ledger is a directory: `evals/review/votes/`, one `<login>.jsonl` file per
voter, one JSON object per line. The filename is the voter's GitHub login, and
the loader refuses a row filed under another person's name. It is checked in
on purpose: it is the evidence behind every quality number, so each change
should be a reviewed diff with an author and a date. The split means two
voters' PRs never conflict with each other.

**Never edit a line, and never delete one.** A reviewer who changes their mind
appends a new vote, and the latest vote for a `(fingerprint, voter)` pair is the
live one. That is what lets a number computed last month be recomputed to the
same digit today, by ignoring the events after it.

Every voter also has a line in `evals/review/voters.toml`, the roster. The
roster holds each voter's **standing** (`maintainer` or `contributor`), and it
is the only place a standing lives — a promotion is one roster edit, and it
re-classes the voter's whole history at once.

Write through `webapp/review.py`, which validates every field against a closed
set. `evals/harness/ledger.py` reads. The directory does not exist until the
first sitting, and the loader returns an empty ledger until then.

## What your standing does to a number

**It selects which series reads your vote. It never weighs it.** Inside any
series a substance rejection still wins over a pooled finding, whoever cast
it.

Every `score` computes two series in one pass, and no flag selects between
them:

| Series | Reads | Where it lands |
| --- | --- | --- |
| `maintainer` | maintainer votes only | The artifact's scored blocks — the published numbers. |
| `all` | maintainer and contributor votes | `series.blocks.all` in the artifact. |

So a contributor's vote is visible immediately and published only after a
promotion — and a promotion re-classes their whole history at once, with no
re-vote and no re-run. A voter with **no** roster line is read by no series at
all, and `score` names them rather than dropping them quietly.

### Deciding a promotion

```bash
python -m evals.harness.run agreement
```

For each pair of voters, this prints the findings both answered, how many were
excluded because one of them left the answer open, and how far the two agreed
on the rest. A pair marked `*` crosses the standing line, which is the
comparison a promotion rests on.

**It promotes nobody, and there is no threshold in the code.** An agreement
figure over a thin overlap is noise, and a number that promotes on noise is
worse than no number. About 30 compared answers — the size of the first
sitting — is where the figure starts being worth reading, and the report
prints the sample size beside every rate so you can weigh it yourself. The
promotion itself is a line in `evals/review/voters.toml` flipped to
`maintainer`, in a pull request a maintainer merges. A demotion is the same
edit reversed.

## When the match rule changes

A vote hangs on a **fingerprint**, and a better rule changes every fingerprint.
That would expire the ledger, so a vote stores the fields its key was computed
from. A rule change is then arithmetic over the ledger's files.

1. **Measure the new rule on both error directions.**

   ```sh
   python -m evals.harness.run calibrate --out agreement.json
   ```

   The command prints false splits, then false merges on both populations,
   then a collision line for every other package, then the 90% bar. Read them
   in that order: the bar is the weakest signal on the page.

   - **No merge count may rise, on any population.** A rule that matches too
     readily raises recall silently, which is the expensive direction to be
     wrong: a merged finding leaves no unmatched claim for anybody to review.
     Watch the candidate column first — it is the population a live run
     resembles, and an element-only rule that merges 23 of 287 reference pairs
     merges 81 of 111 candidates. A new collision needs a reason in that
     package's `recorded_collisions`, or the rule is wrong.
   - **The split count must not rise without an argument.** A split costs a
     reviewer one unmatched finding, which is visible and recoverable, so a rule
     may trade splits for merges. The current `SubsetVerbIdentity` adds no
     candidate split and removes 78 candidate merges. State any future trade.
   - **The bar must hold at 90%.** It admits a candidate rule that has no pinned
     counts of its own yet. Below it, fix the rule or the verb vocabulary
     (`evals/harness/verbs.py`). Never lower the bar.

   `tests/test_evals_identity.py` asserts the shipped counts exactly rather than
   as a floor, so a rule change fails there before it reaches the bar. Update
   those constants in the same commit that changes the rule, and re-quote them
   in `docs/agents/claim-identity.md` — the figure lint checks that file.

2. **Bump the framework's version** in `VERSION_FOR`
   (`evals/harness/fingerprint.py`). It is a table keyed by framework, checked
   against `PACKAGES`, so each package moves on its own.

3. **Move the ledger.** It reads the table you just edited, so there is no
   version to pass. Preview first:

   ```sh
   python -m evals.harness.run rekey
   python -m evals.harness.run rekey --yes
   ```

   It reports how many votes move, how many stay, and how many findings sit in
   the pool, which a re-key never changes. `--yes` rewrites the file through an
   atomic rename, so an interrupted re-key leaves the old file whole.

4. **Re-score the sweeps you still compare against.** Their standings were
   computed under the old keys:

   ```sh
   python -m evals.harness.run score baseline-1.json
   ```

5. **Commit the ledger with the rule.** Nobody votes again, and no run calls a
   provider.

## What a sitting does not tell you

- **The reference sets are agent-authored.** A vote says whether one produced
  finding is real. It does not say the reference set is right. That question
  belongs to the sitting [BLESSING.md](BLESSING.md) step 6 describes, which
  reads a case's sources and asks what the sets miss.
- **A vote is one person's judgement**, recorded with their name. Two reviewers
  who disagree are a result to read, not an error to resolve.
- **The numbers are relative.** Use them to compare configurations and to track
  movement. Never quote them as absolute scores, and never against another
  tool's published figures.

## Framework parity

One ledger, one pool and one queue serve every **Framework Package**, because a
vote records a fact about a system rather than about a framework.

What differs is only the key. A package with an open claim set composes an
identity from its lane, its action verb and the elements it names — version 2,
which is STRIDE's. A package whose claims name a requirement in a catalog is
keyed by that requirement and the place it was ruled in — version 3, which is
ASVS's. A package added to `PACKAGES` and missing from `VERSION_FOR` raises at
its first finding, so its author answers the question rather than inheriting a
default.
