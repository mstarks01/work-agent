# Blessing a golden case

How to turn one or more submitted sources into a golden case the evals can
score against.
This is a one-time, offline authoring task — nothing here runs during a live
analysis. The whole point of the document is **step 3**; everything else is
bookkeeping around it.

> **Nobody has ever run this document.** All 13 cases in `evals/corpus/` were
> written by an agent, and no person has read a `model.json`, a reference set or
> a labelled pair. The `corrections.md` files record what an agent changed
> against the source text, not what a reviewer caught. Steps 3 and 6 below
> describe what *should* happen and what has not happened yet, so treat this as
> a procedure waiting for its first run rather than a record of one. Every
> number the suite reports carries that provenance — see the top of
> [README.md](README.md).

## What a case is

Two artifacts plus the input they're built from:

```
evals/corpus/<NN>-<slug>/
  source.md       the submitted text — exactly what the service would receive
  model.json      the blessed System Model; must pass the shipped validator
  claims/<framework>.json
                  that framework's reference set, written against model.json's IDs
  corrections.md  how the model was corrected against the source, and what that says
  case.json       metadata, plus the sources array declaring the case's input
```

A case may declare **more than one source**, because a job may submit more than
one. `case.json` names them the way a caller does:

```json
"sources": [
  {"kind": "description", "label": "System description",
   "file": "source.md", "sha256": "<digest of that file>"},
  {"kind": "transcript", "label": "Kickoff call",
   "file": "call.md", "sha256": "<digest of that file>"}
]
```

Each entry's `file` is relative to the case directory, so a second source is a
second file beside `source.md` — name it for what it is. `label` must be unique
within the case, and it is the label every `source_label` in `model.json` has to
cite: the service rejects a model citing a label its job never carried, so a
corpus that broke that rule would grade a shape production refuses.
`source_sha256` at the top level is the **aggregate over those refs**, computed
exactly as a report's `InputRef` computes it — not a digest of the text.

The judge fixtures live alongside, in `evals/judge_calibration/`:
`build_pairs.py` holds the hand labels, and `pairs.json` is generated from it.

Run `python evals/verify_corpus.py` to check everything mechanical about the
above; it must be green before a case merges. It checks that every declared file
exists and digests as claimed, that labels are unique, that every
`source_label` in `model.json` names a source the case declares, and that
every `source_excerpt` is really found in the source it cites — the same
ladder the service runs, so a case cannot be blessed in a shape the gate
would reject. `--write-sha`
restamps each source's digest and the aggregate over them.

## The workflow

### 1. Write the source text

The input is what a real user would submit: prose, bullets, a rough dump, or a
transcribed call — **semi-structured and incomplete on purpose**. A source with
no gaps tests nothing, because the behaviour that matters most is how the
service handles what the text *doesn't* say (unstated facts become `unknown`).
A case with no unknowns can't exercise that.

**Writing a transcript source.** Match the form real exports have, which was
measured rather than guessed ([#51](https://github.com/mstarks01/work-agent/issues/51)):
attribution names the *participant* and never their role, there are **no**
uncertainty markers like `[inaudible]` (the real failure mode is fluent
fabrication, not visible garbling), and merged turns run around 220 characters.
Write it **cleaned, not raw** — the byte budget forces cleaning, and cue timings
are stripped by it.

**Grading a conversational rule needs care.** `score_extraction` reads element
IDs, boundary crossings, and the attributes in `_SCORED_ATTRIBUTES` — the closed
vocabularies exactly, and each free-text control through `control_state`. So "a
hedge became `unknown`" *is* measured, on any element both models carry. What is
still invisible is the wording: `technology`, `protocol` and `data_description`
are compared by nothing, because two correct readings of one sentence word them
differently. An assertion about those reaches a number only through the
reference threat set in end-to-end mode — a needs-info must-find threat that a
wrongly-confident attribute would suppress, dropping recall. Write each
assertion so it fires through that or through `ExtractionScore`, or it will not
be measured at all.

Size the system so the finished model lands at **8–20 elements**. That's not a
style preference: the reference threat set has to be exhaustively enumerable by a
human, because any real threat the author forgets to write down will score
against the tool as a false positive.

**Sanitization is mandatory for anything based on a real system.** No real
hostnames, IPs, bucket names, account IDs, credentials, customer data, or
employee names. The safest default — and what the synthetic cases do — is to
invent a plausible system rather than sanitize a real one: there's nothing to
leak by omission. If a case *is* derived from a real system, sanitize before you
write the text down, not after.

For a case converted from the OWASP Threat Model Cookbook (CC-BY 4.0), record the
source entry and its licence in `case.json`. If the source diagram is larger than
the 8–20 band, convert a **scoped subset** whose removal doesn't change any
remaining element's attributes, and note what you dropped.

### 2. Draft the model

Run the extraction step over `source.md` and keep its output as the starting
draft of the model:

```sh
python -m evals.harness.run run --mode extraction --case <NN>-<slug>
```

Hand-authoring a diagram from scratch is the expensive path and buys nothing the
correction pass in step 3 doesn't already give.

### 3. Correct the model against the source text

This is the real work. **Read the source text and check the model against it —
do not read the draft model looking for things that seem wrong.** Correcting a
plausible-looking artifact is measurably less thorough than checking it against
the source, so let the *input* drive your attention, not the draft.

In practice: take the source one sentence at a time and ask what the model must
say about that sentence — then go confirm it says it. Run this checklist on each
pass:

1. **Every noun that's a thing** — is there an element for it, of the right type?
   Watch for things mentioned in passing; an actor introduced in a closing
   sentence is the most commonly dropped element.
2. **Every verb that's an interaction** — is there a flow for it? Is its
   direction *who initiates*, not which way the data travels? Pull, poll, and
   consume interactions are routinely reversed.
3. **Every security-relevant attribute** — for authentication, encryption in
   transit, encryption at rest, exposure, and data classification: does the text
   state it? If not, is it `unknown`? A plausible value the text never gave is
   the most common and most damaging error — and inventing an *absence* ("no
   encryption") is worse than inventing a control, because category agents file
   confident findings on it.
4. **Every stated qualifier** — "shared", "never rotated", "full read/write",
   "does not check": is it in an attribute a category agent will read, or did it only
   survive as a quoted excerpt? A qualifier stranded in a quote is invisible
   downstream — literally so: `source_excerpt` is stripped from the model the
   category agents read. This is the single most repeated extraction failure.
5. **Every inference** — does each non-`unknown` value the text *didn't* state
   appear in the model's `assumptions` list, with a basis? An inferred value with
   no matching assumption is a bug.
6. **Zones and asset tags** — does every entity, process, and store sit in a
   trust zone the text implies? Are asset tags driven by what the data *is*, not
   by what the element is *called*?

Record every correction in `corrections.md`: the path, the draft value, the
corrected value, and the reason from the source text. Close with a short summary
of the pattern you saw — that record is what later informs how extraction errors
are weighted.

### 4. Write the reference threat set

Write one entry per threat, against the corrected model's element IDs:
`category`, `affected_element_ids`, `claim`, `tier`, `severity`, `notes`.

- **`claim` is what the judge matches on.** One sentence, phrased as an attacker
  action — *who does what to what*. Not a control recommendation, not a
  description of a weakness. A claim written as a missing control gives the judge
  nothing to compare.
- **Enumerate exhaustively, lane by lane.** Every case must carry at least one
  reference threat in each of the six STRIDE categories; `verify_corpus.py`
  enforces it.
- **Tier honestly.** `must-find` means *if the tool misses this, it doesn't
  work*. If everything is must-find the bar is unreachable; if nothing is, it's
  meaningless.
- **Severity is `likelihood` and `impact` only** — the band is derived from the
  shipped matrix, so never write one.
- **Keep same-element threats in different lanes distinct.** Reading a flow and
  modifying it are two different claims; the pair teaches the judge the
  distinction it most often gets wrong.
- **`notes` is your rationale.** Never scored, always worth writing — it's what
  lets a later reviewer disagree with you specifically.

### 5. Label the judge-calibration pairs

In the same sitting, label candidate threat pairs as match / no-match in
`build_pairs.py`. These are what the **≥90% judge–label agreement bar** scores
against, and they're what lets the scorer be tested with no live calls at all.
They are not ground truth: nobody has reviewed them, so the bar says the judge
reproduces them and says nothing about whether they are right.

- **Label within a category only** — the prefilter means cross-category pairs
  never reach the judge.
- **Weight toward hard negatives:** same element, same category, *different
  attacker action*. Easy negatives measure nothing, and a judge that says "match"
  too readily inflates recall silently — the expensive direction to be wrong.
- **Include pairs that differ only in which element they cite.** Those are
  matches: matching is decided on the claim, and element agreement is scored
  separately. This rule is why element agreement alone cannot decide claim
  identity — it labels the two apart on purpose — and
  `tests/test_evals_identity.py` measures the size of the gap.
- **A better-explained write-up of one attack is still a match.** One side
  naming the credential, the cause, a figure, or the control the other leaves
  implicit does not make a second claim. **The test: does the extra text change
  what the attacker does, or only how well it is explained?** Review sitting 01
  showed this is the rule a reader cannot apply from the bullet above it — every
  `unclear` answer in that sitting was a pair where one side carried specificity
  the other lacked.
- **But a different route is a different claim.** Same fabricated data landing in
  the same store is *not* one finding when one write-up comes through a server's
  write path and the other through what a client reports: the remedies differ,
  and the route is the finding. Review sitting 01 relabelled a pair for exactly
  this, so the two bullets above have a floor under them.
- **Include candidates that assert facts the model doesn't support.** Those are
  no-match — and downstream they're the "unsupported" bucket that counts against
  the tool.
- **Keep the set balanced;** `verify_corpus.py` fails if either label drops below
  30%.
- **Assign the candidate's affected element IDs on every `match` pair.** The
  sixth field of the tuple, and `verify_corpus.py` fails on a `match` pair
  without one. Answer it from the candidate sentence's own words against
  `model.json`, **before** reading the reference's element list: copying that
  list makes every pair agree by construction and the measurement in
  `tests/test_evals_identity.py` worthless. Follow the reference sets' own
  conventions — a flow, process, store or entity, one or two of them, never a
  boundary. `no-match` candidates carry `None`; nobody has assigned them yet.

### 6. Bless and merge

One reading session, one pull request, one approval. The reviewer signs off on
`source.md`, `model.json`, the `claims/` reference sets, and the labelled pairs
**together** — they're one artifact, and reviewing them separately loses the
property that the threat set is exhaustive *against that model*.

**This step is now enforced.** Record the session in `case.json`:

```json
  "review": {
    "reviewer": "<name or handle>",
    "date": "<YYYY-MM-DD>",
    "read": ["source.md", "model.json", "claims/stride.json"],
    "notes": "<counts, and anything you changed>"
  },
```

`tests/test_case_review.py` fails on a new case that arrives without that block,
and names every case still waiting in its `UNREVIEWED` list. The 13 cases that
shipped before this was enforced are on that list; it is debt, not an exemption,
and it is meant to shrink to nothing.

**Why it cannot be replaced by a lint.** Review sitting 01 found a reference claim
asserting the model emits training data in a case with no training pipeline. A
mechanical version of that check — flag a claim using a word absent from the case's
source and model — fires on 231 of 243 claims, because a claim is *supposed* to
describe an attack in words the system description never uses. Narrowing to the
asset vocabulary fails too. This step is the only instrument for that class of
defect.

`REVIEW-02.md` in case 01 is the first run, and the template for the rest: read the
source and write your own threat list *before* opening the recorded one, or the
sitting measures nothing.

Merge checklist:

- [ ] `python evals/verify_corpus.py` is green
- [ ] the model was corrected against the source text, per step 3
- [ ] `corrections.md` records every correction and the pattern behind it
- [ ] sanitization confirmed; provenance and licence recorded in `case.json`
- [ ] `sources` declared in `case.json`, one entry per input file
- [ ] every `source_label` in `model.json` names one of those labels
- [ ] every `source_excerpt` is a verbatim span of the source it cites, with `…` marking any cut
- [ ] digests and the aggregate stamped (`--write-sha`)
- [ ] tier assignment reviewed: some must-find, not all
- [ ] every `match` pair's candidate element IDs read against the candidate's own words

## Growing a case from real runs

Reference sets aren't meant to be exhaustive up front — they converge from real
output. Each scoring run surfaces grounded, plausible threats the tool produced
that simply aren't in the reference set (`unlisted_for_promotion` in the
artifact). Review the recurring ones and promote them into the reference set at
the next blessing pass — which is just steps 4–6 again, for that one case.
Promoting a threat is always a reviewed change with a human explaining why; it's
never automatic.

**A promoted threat arrives carrying `grounds`, and loses them.** A reference
threat keeps its six fields, so write step 4's entry as you would any other and
drop the grounds on the way in. That is deliberate, not an oversight: a
hand-authored ground would be graded by nothing. Grounds are produced by a
category agent and checked against the case's real `source.md` at merge time, so
adding them here would mean extending `ReferenceThreat` *and* writing a scorer
to measure agreement between a human's choice of evidence and an agent's — which
is not a property this corpus exists to measure, and would put every reference
threat on the maintenance path for it. Do not carry what you do not grade.

The grounds are still worth *reading* before you promote. A threat whose only
ground is an `unknown-attribute` is telling you the case leaves that attribute
unstated, which is a fact about your `model.json`; an `absent-attribute` says
the case states the control is missing, which is a different fact about the
same file; and a `quote` ground points at the sentence in `source.md` that a
real reference entry should have been written from.
