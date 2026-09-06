# Claim identity, and the vote that hangs on it

A **Claim**'s identity is a value code computes from the fields the claim
carries. Nothing here reads prose, and nothing here calls a model.

This is the design [#201](https://github.com/mstarks01/work-agent/issues/201)
asks for, plus the thing it makes affordable: a human vote that stays valid
across runs, models and prompt edits.

The procedure that runs on top of it — how to hold a review sitting, and how to
re-key the ledger after a rule change — is
[`evals/VOTING.md`](../../evals/VOTING.md).

## The rule

One question decides everything else: **what does the attacker do, and to
what?**

| Part | Where it comes from | Read by |
|---|---|---|
| framework | the block | v1, v2, v3 |
| lane | the block, from the field its package declares (`LANE_FIELD`) | v1, v2, v3 |
| targets | `affected_element_ids`, endpoint-resolved | v1, v2, v3 |
| action verb | `analysis_service.actions`, a closed set of 20 | v2 only |
| catalog identifier | the claim ID, read by the package that owns the catalog (`IDENTIFIER_OF`) | v3 only |

`Claim` carries `verb` and `DraftThreat` requires it, so a finding out of a live
run keys the same way a reference claim does. The vocabulary is **service-side**
rather than eval-side for that reason: a vocabulary that validates a shipped
model has to ship with it. `evals/harness/verbs.py` adds only what a measurement
needs — which verbs count as one action, and what the rule cannot separate.

`evals/harness/fingerprint.py` hashes those into `v<version>:<16 hex>`.

**Targets are endpoint-resolved before they are hashed.** A cited **Data Flow**
becomes the two **Element**s it runs between, and a **Trust Boundary** is
dropped. The corpus carries one finding cited as a flow by one writer and as the
process at the end of that flow by another; without this they fingerprint
differently and one finding becomes two.

## Why the verb, and what it is worth

Elements alone cannot separate a read from a write against one store. The
frontier in `tests/test_evals_identity.py` prices every rule on all three ways
of being wrong — false splits over the 200 labelled match pairs, false merges
over the 111 scored candidate negatives, and 3 false merges over the 287
within-lane reference pairs:

| Rule | False splits (of 200) | Candidate merges (of 111) | Reference merges (of 287) |
|---|---|---|---|
| equality | 88 | 22 | 1 |
| endpoint subset | 14 | 81 | 23 |
| **endpoint subset + verb** | **14** | **3** | **3** |
| overlap | 4 | 83 | 34 |
| endpoint overlap | 1 | 99 | 126 |

No element-only row is usable: the tightest loses 89 paraphrases and the loosest
destroys 126 findings. **The verb row is the first one that is.**

**Read the candidate column, not the reference one.** On reference pairs alone
`endpoint subset` merges 23 of 287 and looks survivable. On the candidate
paraphrases a live run actually emits it merges **81 of 111** — it is barely a
rule. The verb takes that to 3 without adding a split. That column did not exist
until [#511](https://github.com/mstarks01/work-agent/issues/511) assigned the
negative half its elements and verbs; before it, every candidate merge count
was structurally zero and the argument for the verb rested on the weaker
number.

### What each column is measured over, and what none of them is

The columns come from **three different populations**, so none is a rate over
what a live run emits and they do not combine into one figure.

- **False splits** are candidate-vs-reference: 200 paraphrase pairs an agent
  labelled equivalent.
- **Candidate merges** are candidate-vs-reference too: 111 hard negatives,
  weighted toward the same element and lane with a *different attacker action*.
  This is the population a live run resembles most closely.
- **Reference merges** are reference-vs-reference: every within-lane pair of
  distinct claims the corpus already records as two findings. It needs no label
  to interpret, because every merge is an error by construction.

**Twenty-eight fixtures are not on this axis at all.** Twenty-five assert facts
the **System Model** does not hold, two cannot be decided cleanly from the claim
pair alone, and one is not a valid STRIDE threat claim. They carry
`unsupported`, `unclear`, and `invalid-claim` dispositions respectively, sit
outside the identity score, and are counted beside it. Nothing here measures
the groundedness or claim-validity axes they belong to.

### The agreement ratio, and the one job it has

Scored on the shared scoreboard, through `measure_agreement`:

| Rule | Agreement with the recorded labels |
|---|---|
| `MechanicalIdentity` (element equality) | 201/311 = 64.6% |
| `SubsetVerbIdentity` | **294/311 = 94.5%** |

**This is an admission gate, not a quality statement.** It clears the 90% bar,
and the bar exists to price a *candidate* rule — one nobody has measured, which
therefore has no pinned counts to regress against. Once a rule ships, the split
and merge counts above are the regression signal: `tests/test_evals_identity.py`
asserts them exactly rather than as a floor, so they bind harder than the ratio
and a rule cannot rot downwards inside the bar's slack.

Read the ratio the way `evals/README.md` reads every agreement figure here: the
labels are agent-authored, so it measures reproduction and not correctness. It
is the number the judge's retirement rested on — #201's third bullet asked that
the rule not be obviously worse than the judge before the judge could go, and
this is the measurement that answered it. It is not a claim about accuracy, and
nothing downstream should quote it as one.

**The three merges the verb does not break** are in `verbs.UNSEPARATED`, each
with the reason. None is a gap in the vocabulary: one is arguably a correct
merge the corpus itself calls adjacent, one is a repudiation lane where no
attacker acts so no verb applies, and one is a corpus wording gap. Review 02
moved the disputed case-13 "writes"/"alters" pair to `unclear` rather than
forcing it to move either the rule or the score.

### A label may decline, and the reviewed set stays small

A pair carries `match`, `no-match`, `unclear`, `unsupported` or
`invalid-claim`. The last three leave the denominator and are counted beside
the refusals, each under its own key, because a rule cannot disagree with an
answer nobody gave it.

- **`unclear`** — a reader could not decide the pair from the two sentences
  alone. Two fixtures use it.
- **`unsupported`** — the candidate asserts a fact the model does not hold,
  whether or not the pair also differs on identity. Twenty-five fixtures.
- **`invalid-claim`** — the candidate is not a valid STRIDE threat claim at all.
  One fixture.

Two diagnostic annotations preserve secondary observations without changing
scoring: `mixed` records a second decision axis, and `misclassified-lane`
records a valid threat placed in another STRIDE lane.

[Review 02](../../evals/calibration_labels/REVIEW-02.md) read all 44 pre-review
decision-boundary fixtures and a blind random sample of 60 of the other 295.
The random sample agreed with the original primary label on 58 of 60; its exact
finite-population 95% interval is 0.7%–10.5%. That is assurance about label
self-consistency outside the boundary set, not corpus correctness or matcher
accuracy. Human reading effort still belongs primarily on Case Sittings, which
say whether the corpus itself is right.

The element and verb assignments remain agent-authored. The candidate side was
assigned from each candidate's own wording rather than by copying the
reference's, for the reason `build_pairs.py` gives about element IDs: reading
the answer first makes every pair agree by construction and the measurement
worthless.

## The version is in the value

A better recogniser changes every key. That would be fatal if a vote stored only
its hash — so **a vote stores its components**, and `ledger.rekey` recomputes
the whole file under a new version with no re-vote, no provider and no
credentials.

This is the property the retired judge design could not offer — a judge
upgrade silently re-scored every historical number, with no way to recompute
the old ones. Here the re-score is explicit, total, offline and free.

**The version is not one global default.** It is `VERSION_FOR`, a table keyed by
framework, checked against `PACKAGES` and declared in
`tests/test_framework_neutrality.py`:

| Package | Version | Why |
|---|---|---|
| `stride` | 2 | an open claim set, so the action is half of what makes two claims one finding |
| `asvs` | 3 | its claims name a requirement in a catalog, so the identifier and the place it was ruled in are the key |

Version 1 — place alone — keys nothing today. ASVS sat there until the collapse
it caused was named: two requirements ruled on one element in one chapter shared
a fingerprint, so one vote answered for both.

Those entries follow from what a package's claims *are*, not from preference —
a rule that reads an action is wrong for a claim that names a requirement, and
the reverse. A package added to `PACKAGES` and missing from the table raises at
its first finding, which is the question its author has to answer.

## The vote

`evals/harness/ledger.py` is the only place in this repository where a **human**
judgement is the datum. Append-only JSONL; a correction is a new event, never an
edit.

**The reason code is the control for personal preference.** A reviewer who
dislikes a finding's writing and a reviewer who says it is not a threat report
two different facts, and averaging them lets taste move a recall number. So:

- `SUBSTANCE_REASONS` — the finding is wrong. Counts against the configuration,
  and the finding leaves the reference pool.
- `STYLE_REASONS` — the finding is real and badly written. Counted by
  `evals/harness/writing.py`, per case and framework, and the finding **stays**
  in the pool.

That split is mechanical, not a request in a guide. `Vote.counts_against_analysis`
and `Vote.joins_the_pool` are the two booleans that carry it.

`needs-evidence` is not a verdict about the finding at all. It says the reviewer
cannot answer from what they were shown, and it routes to a re-ask rather than
to a score — an ungrounded finding demands more evidence, not a vote.

## What this buys, in reviewer minutes

A vote is spent once and kept forever, because it hangs on a fingerprint rather
than on a run. So `evals/harness/queue.py` shows only what nobody has answered:

- first sitting over a corpus — everything, a few hundred findings
- later sitting, same configuration — nothing
- after a configuration change — the findings that changed, and no others

Without the fingerprint every experiment costs a full re-review, and nobody runs
the loop. **The economics of the human loop are what force the determinism**;
removing the judge from the metric path is a consequence, not the goal.

## Framework parity

The rule is one rule, instantiated per framework and keyed, never branched.

A package whose claims name a catalog requirement is keyed by that requirement
and the place it was ruled in, and the verb never composes. A package with an
open claim set composes a key from lane, verb and targets. Both feed one ledger,
one pool and one queue, because a vote records a fact about a system and not
about a framework.

`PACKAGE_SCORERS` in `evals/harness/instruments.py` is the table this follows,
and it is checked against `PACKAGES`. A fingerprint rule keyed by framework must
be checked the same way: a table nobody compares to its registry fails as
quietly as the `if` it replaced.

**Identity validation is keyed too**, by `IDENTITY_VALIDATION` in
`evals/harness/calibration.py`, checked against `PACKAGES` in
`tests/test_framework_neutrality.py`. Which evidence a package needs follows
from its claim type:

| Claim type | Needs candidate pairs? | How a collision is decided |
|---|---|---|
| an open claim set in prose | yes — only a labelled pair says whether two spellings name one action | one lane, endpoint subset, one action |
| a claim naming a catalog requirement | no — the identifier decides equivalence | one lane, one requirement, one place |

The first column is settled design: `evals/BLESSING.md` step 5 records from #167
that a package matching by requirement identifier reaches no claim-equivalence
question and contributes no pair.

**The second column does not follow from the first.** Every claim type can
destroy a finding by keying two distinct claims alike, and the cost is the same
whatever the identity is composed from — the second finding stops existing and
no reviewer sees it go. So every package carries a collision rule, and
`measure_merges` raises for a package with no entry rather than answering zero:
"nothing was asked" must never read as "no collisions".

| Package | Comparable reference pairs | Collisions |
|---|---|---|
| `stride` | 287 | 3 |
| `asvs` | 20 | 0 |

ASVS's denominator is small because the chapter separates almost everything
first: 448 within-case pairs, of which 20 share a chapter, of which none shares
a requirement identifier. A rise in the second column would mean two rulings on
one requirement in one place, and one vote answering for both.

## Where each piece lives

| Module | What it owns |
|---|---|
| `evals/harness/verbs.py` | The closed vocabulary, the equivalence table, the three it cannot separate. |
| `evals/harness/fingerprint.py` | `Components`, the versioned hash, and the version read back off the value. |
| `evals/harness/identity.py` | `endpoint_subset`, `SubsetVerbIdentity`, and the `Matcher` protocol one scoreboard scores every rule through. |
| `evals/harness/ledger.py` | The append-only vote record, the reason split, the pool, the re-key. |
| `evals/harness/queue.py` | Which findings a reviewer is asked, in what order, blind to the configuration. |
| `webapp/review.py` | The reviewer's interface. Loopback, no credentials, no engine. |

## Where the rule is used

The rule is the only matcher a scored sweep has. A `match` is a recall hit; a
`no-match` leaves the finding unmatched, and its fingerprint is looked up in
the vote ledger — `rejected`, `pooled`, `open` or `unvoted`. Nothing asks a
model. The rule's known error costs are the record above: 14 of 200 labelled
matches split, 3 of 111 candidate negatives merged, 3 of 287 reference pairs
merged. A split surfaces as an unvoted finding in the queue rather than
vanishing. A merge does not surface at all, which is why the candidate merge
column is the one to watch.

The gain is determinism before it is cost. A match the rule settles cannot move
between two runs of one configuration — which is exactly the band
`evals/harness/stability.py` measures and every comparison has to clear.
