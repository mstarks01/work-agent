# Blessing a golden case

The one-time, offline workflow that turns a system description into a golden
case. Nothing here runs at analysis time and nothing here is interactive —
per-analysis human review is out of scope for this effort. Authored with the
phase-1 corpus (wayfinder ticket 022); the phase-2 expansion follows the same
steps.

The whole point of this document is **step 3**. Everything else is bookkeeping.

## What a case is

Two artifacts plus their input, per wayfinder ticket 009 decision 1:

```
evals/corpus/<NN>-<slug>/
  source.md       the submitted text — the exact input the service would receive
  model.json      the blessed SystemModel; must pass the shipped validator
  threats.json    the reference threat set, written against model.json's IDs
  corrections.md  the bootstrap → blessed diff, and what it says about `extract`
  case.json       metadata: domain, exemplar proximity, provenance, source_sha256
```

`evals/judge_calibration/` holds the hand-labelled judge fixtures:
`build_pairs.py` carries the labels, `pairs.json` is generated from it.

`python evals/verify_corpus.py` checks everything mechanical about the above and
must be green before a case merges. `--write-sha` stamps `source_sha256`.

## The workflow

### 1. Write the source text

The input is what a front-end user would actually submit: prose, bullets, a
rough dump. **Semi-structured and incomplete on purpose.** A description with no
gaps in it tests nothing — the service's controlling behaviour is that
unstated facts become `unknown`, and a case with no unknowns cannot exercise it.

Size the system so the finished model lands at **8–20 elements**. That band is
not a style preference: reference threat sets must be *exhaustively* enumerable
by a human, because every real threat the SME failed to write down scores as a
false positive against the tool (ticket 009 decision 5).

**Sanitization is mandatory and non-negotiable for internal cases.** No real
hostnames, IPs, bucket names, account identifiers, credentials, customer data,
or employee names. The phase-1 internal cases (01–04) are synthetic systems
written for this corpus rather than sanitized copies of real ones, which is the
safer default: there is nothing to leak by omission. If a future case is derived
from a real system, sanitize before the text is written down, not afterwards.

For cookbook conversions, record the source entry and its licence in
`case.json`'s `provenance`. The OWASP Threat Model Cookbook is CC-BY 4.0 for
textual and graphical representations, so attribution is required. If the source
diagram is larger than the 8–20 band, convert a **scoped subset** whose removal
does not change any remaining element's attributes, and say what you dropped.

### 2. Bootstrap the model

Run the extraction node over `source.md` and keep its output as the candidate
model. Hand-authoring a DFD per case is the expensive path for no gain the
correction pass does not also give (ticket 009 decision 2).

#### Bootstrapping without credentials

Phase 1 was authored in an environment with no Vertex access, so the bootstrap
was produced by an **agent stand-in** running the shipped `prompts/extract.md`
rather than by the pinned `extract` node. Every phase-1 case records
`"bootstrap": "agent-stand-in"` in `case.json` to mark this.

The consequence is specific and worth stating plainly: the bootstrap→blessed
diffs in `corrections.md` are signal about **the prompt**, not about the pinned
Flash model's own blind spots. They are still the right shape of evidence — and
the corpus is already usable — but the moment credentials exist, re-running
`extract` over each `source.md` and re-recording the diff is cheap and turns
that signal into the thing decision 2 actually asked for. Nothing else in the
case changes: the blessed model is blessed against the *source text*, not
against whatever produced the candidate.

### 3. Correct the model against the source text

**Work the checklist against `source.md`. Do not read the candidate model
looking for things that seem wrong.** This is the entire mitigation for the
anchoring risk decision 2 accepted: correcting a plausible artifact is
measurably less thorough than authoring from a blank page, so the reviewer's
attention has to be driven by the input, not by the candidate.

In practice: take the source text a sentence at a time and ask, of each
sentence, what the model must say — then go and check that it says it.

Checklist, per pass over the source text:

1. **Every noun that is a thing.** Does an element exist for it, of the right
   type? Watch for things mentioned outside the main narrative path — an actor
   introduced in a closing paragraph is the most commonly dropped element.
2. **Every verb that is an interaction.** Does a flow exist for it? Is its
   direction *who initiates*, not which way the data travels? Pull, poll, and
   consume interactions are routinely reversed.
3. **Every security-relevant attribute.** For `authentication`,
   `encryption_in_transit`, `encryption_at_rest`, `exposure`,
   `data_classification`: does the text state it? If not, is it `unknown`?
   A plausible value the text never gave is the most common and most damaging
   error, and an *invented absence* ("no encryption") is worse than an invented
   control, because analysts file confident findings on it.
4. **Every stated qualifier.** "Shared", "never rotated", "full read/write",
   "does not check" — is it in the attribute an analyst reads, or did it only
   survive in `source_excerpt`? A qualifier stranded in an excerpt is invisible
   downstream. This is the corpus's most repeated extraction failure.
5. **Every inference.** Does each non-`unknown` value the text did not state
   appear in `assumptions` with a basis? An inference in an attribute but not in
   `assumptions` is a bug.
6. **Zones and asset tags.** Does every entity, process and store sit in a
   boundary the text implies? Are asset tags driven by what the data *is*, not
   by what the element is *called*?

Record every correction in `corrections.md`: path, bootstrap value, blessed
value, and the source-text reason. Close with a short **Signal** section naming
the pattern — that accumulating record is what the extraction eval's error
weighting will eventually be derived from.

### 4. Write the reference threat set

Against the blessed model's element IDs, one entry per threat:
`category`, `affected_element_ids`, `claim`, `tier`, `severity`, `notes`.

- **`claim` is the matching target.** One sentence, attacker-action phrasing:
  *who does what to what*. Not a control recommendation, not a description of a
  weakness — the judge rules on whether two claims describe the same attacker
  action against the same target, and a claim phrased as a missing control gives
  it nothing to compare.
- **Enumerate exhaustively**, lane by lane. Every case carries at least one
  reference in all six lanes; `verify_corpus.py` enforces it.
- **Tier honestly.** `must-find` means *missing this means the tool does not
  work*. If everything is must-find the gate is unreachable; if nothing is, it
  is vacuous.
- **Severity is `likelihood` and `impact` only.** The band derives from the
  shipped matrix — never write one.
- **Keep same-element threats in different lanes distinct.** Reading a flow and
  modifying it are two claims, and the pair teaches the judge the distinction it
  most often gets wrong.
- `notes` is SME rationale. Never scored, always worth writing: it is what makes
  a later reviewer able to disagree with you specifically.

### 5. Label the judge-calibration pairs

In the same session, hand-label candidate pairs match/no-match in
`build_pairs.py` (ticket 009 decision 13). These are the ground truth for the
**≥90% judge–human agreement bar**, and they are what lets the scorer be
unit-tested with zero Vertex calls.

- Label **within lane only** — the mechanical prefilter means cross-lane pairs
  never reach the judge.
- Weight toward **hard negatives**: same element, same lane, *different attacker
  action*. Easy negatives measure nothing. A judge that says "match" too readily
  inflates recall silently, which is the expensive direction of error.
- Include pairs that differ only in **which element they cite**. Those are
  matches: matching is decided on the claim, and element agreement is scored as
  its own dimension (decision 8).
- Include candidates that assert facts the model does not support. Those are
  no-match — and downstream they are `ungrounded`, the one gating bucket.
- Keep the set from going lopsided; `verify_corpus.py` fails below 30% of either
  label.

### 6. Bless and merge

One reading session, one PR, one approval. The reviewer signs off on `source.md`,
`model.json`, `threats.json` and the labelled pairs **together** — they are one
artifact, and reviewing them separately loses the property that the threat set
is exhaustive *against that model*.

Merge checklist:

- [ ] `python evals/verify_corpus.py` is green
- [ ] the model was corrected against the source text, per step 3
- [ ] `corrections.md` records every correction and its signal
- [ ] sanitization confirmed; provenance and licence recorded in `case.json`
- [ ] `source_sha256` stamped (`--write-sha`)
- [ ] tier assignment reviewed: some must-find, not all

## Feeding the corpus from real runs

References are non-exhaustive by construction and converge from real output, not
from anyone trying to be exhaustive up front (ticket 009 decision 11). Each
scoring run surfaces `valid-unlisted` threats — grounded, plausible, simply not
in the reference set. Recurring ones are reviewed and promoted into the
reference set at the next blessing pass, which is a repeat of steps 4–6 for the
affected case only. Promoting a threat is a reviewable diff with a human
explaining why; it is never automatic.
