# 40. An asset tag names what an element holds

- **Status**: accepted. The vocabulary drops `availability-critical` and
  `reputation`, and every consumer moves with it
- **Date**: 2026-09-19
- **Effort**: [#877](https://github.com/mstarks01/work-agent/issues/877), whose
  three steps this completes. Step 1 shipped in #878 and step 2 in #1077
- **Relates to**:
  [ADR 0027](0027-vocabulary-raises-a-lead-and-rules-nothing-out.md), whose rule
  — a vocabulary raises a lead and rules nothing out — is why a tag nobody can
  state is worse than no tag
- **Evidence**: ten extraction runs of 2026-09-12, recorded on #877, and the
  convergence counts in "What replaces it" below

## Context

An **Asset** is what an attacker acts on. `CORE_ASSET_TAGS` mixed two kinds
under that one word: six **data classes** that say what an element holds, and
two **consequences** that say what a failure would cost.

The two consequences cannot be extracted, and the measurement says so.

| tag | dropped per 5 runs | added per 5 runs |
| --- | ---: | ---: |
| `reputation` | 24 | 1 |
| `availability-critical` | 22 | 41 |

**No corpus source contains the word "reputation"** — none of the thirteen. Nor
is there a rule a model could learn instead: 11 corpus processes are
`exposure: internet-facing` and 5 carry the tag, so the corpus applies no rule
either. An asset edit defining the ambiguous tags measured +0.004, 0.2 sd over
ten runs, and was reverted (#295).

## Decision

**Every shipped asset tag names something an element holds, and nothing names
what a failure would cost.** The vocabulary is `credentials`, `pii`,
`financial`, `health`, `secrets` and `business-critical-data`.

A hard cutover, per the repository's rule: a model carrying a retired tag is
refused with `illegal-asset-tag` rather than silently accepted. A deployment
that wants to model one of them adds it through `extra_asset_tags`, which is
where the judgement belongs — whether an outage matters, or whether a public
failure is embarrassing, is a property of a business rather than of a system.

## What replaces it

`availability-critical` had one real consumer that `reputation` did not: the
denial-of-service lane and the severity rubric read it for an outage's stakes.
Convergence is the same idea computed rather than guessed, and over the
thirteen blessed models the two are not the same signal:

| | |
| --- | ---: |
| elements the shared-dependency rule fires on | 20 |
| elements carrying a consequence tag | 12 |
| elements in both | **3** |

So the rubric rates an outage on the dependent set, and the lane reads the
`distinct_callers` a candidate carries. That is #877's step 2, shipped in
#1077, which is why it had to come first.

## What it costs

- **The corpus.** Eight `model.json` files drop a tag, and three reference
  notes drop a clause naming one. Each of those three already stated a
  structural reason beside the tag — the single ingest point, the one datastore
  dependency, the chokepoint every session passes through — so no reference
  claim's severity or wording changes.
- **The sittings.** None. `model.json` and `claims/*.json` are two of the four
  files whose digests un-read a case, and all thirteen cases were already
  unread (#226), so this edit un-reads nothing that was read.
- **The archive.** Nothing is refused: `run.py replay` over the archived
  emissions reports 0 refused by today's gate, because the replay path scores
  what was emitted rather than re-gating it. 12 of 231 emitted elements in one
  archived sweep carry a retired tag, and those now count as a disagreement
  where #878 had excluded them from both sides. The exclusion has no live
  effect any more, because the gate refuses the tag before a score is reached.
- **A pre-cutover corpus.** Refused, by design. `replay --corpus` against the
  corpus as it stood before this change stops with `illegal-asset-tag`, which
  is the cutover failing closed on the old vocabulary rather than reading it.

## What would falsify this

- **A source that states one.** If submitted text routinely said which
  components must not go down, the tag would be extractable and the measurement
  above would not hold. Nothing in thirteen corpus sources does.
- **An outage rating that needs more than the dependent set.** The rubric now
  rates an outage on blast radius, reversibility and the convergence count. If
  a reader finds those insufficient where the tag was sufficient, the clause
  that replaced it is wrong rather than the removal.

## Consequences

- The public schema's asset enum shrinks, and a stored model carrying a retired
  tag fails validation. That is the intended hard cutover.
- `SENSITIVE_ASSET_TAGS` now equals `CORE_ASSET_TAGS` and stays a separate
  name, because a deployment's configured tag is not one this service may rule
  is disclosed.
- Every **Framework Package** sees the shorter vocabulary at once, because the
  tags are the System Model's and the System Model is the service's. STRIDE's
  rubric and its denial-of-service lane are the consumers that change. ASVS
  reads assets through `AUDITED_ASSET_TAGS`, a subset of the data classes, so
  it holds no retired tag and its behaviour is unchanged.
