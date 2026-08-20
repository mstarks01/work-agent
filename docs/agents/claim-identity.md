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
| framework | the block | v1, v2 |
| lane | the block | v1, v2 |
| targets | `affected_element_ids`, endpoint-resolved | v1, v2 |
| action verb | `stride_service.actions`, a closed set of 20 | v2 only |

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
frontier in `tests/test_evals_identity.py` prices every rule on both errors at
once — false splits over the 200 labelled match pairs, false merges over the 287
within-lane reference pairs:

| Rule | False splits (of 200) | False merges (of 287) |
|---|---|---|
| equality | 89 | 1 |
| endpoint subset | 14 | 23 |
| **endpoint subset + verb** | **15** | **3** |
| overlap | 4 | 34 |
| endpoint overlap | 1 | 126 |

No element-only row is usable: the tightest loses 89 paraphrases and the loosest
destroys 126 findings. **The verb row is the first one that is.** One more split
buys twenty fewer merges.

Scored on the shared scoreboard, through `measure_agreement`:

| Rule | Agreement with the recorded labels |
|---|---|
| `MechanicalIdentity` (element equality) | 111/200 = 55.5% |
| `SubsetVerbIdentity` | **185/200 = 92.5%** |

That clears the 90% bar every matcher is held to. Read it the way
`evals/README.md` reads every agreement figure here: the labels are
agent-authored, so this measures reproduction and not correctness. It is the
number the judge's retirement rested on — #201's third bullet asked that the
rule not be obviously worse than the judge before the judge could go, and this
is the measurement that answered it.

**The three merges the verb does not break** are in `verbs.UNSEPARATED`, each
with the reason. None is a gap in the vocabulary: one is arguably a correct
merge the corpus itself calls adjacent, one is a repudiation lane where no
attacker acts so no verb applies, and one is a corpus wording gap. The single
extra split is also known — case 13 labels "writes job orders straight into the
database" and "alters job orders in the database" one threat, and the vocabulary
calls those `forge` and `alter`. That is a label worth a reading session rather
than a rule to loosen.

**All of it is agent-authored and unreviewed**, like everything under `evals/`.
243 reference verbs and 200 candidate verbs, assigned by an agent. The candidate
side was assigned from each candidate's own wording rather than by copying the
reference's, for the reason `build_pairs.py` gives about element IDs: reading the
answer first makes every pair agree by construction and the measurement
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
| `asvs` | 1 | its claims carry a catalog requirement identifier and already have an identity |

Those entries follow from what a package's claims *are*, not from preference —
so version 1 is not a lesser rule for ASVS, it is the whole of the right one. A
package added to `PACKAGES` and missing from the table raises at its first
finding, which is the question its author has to answer.

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

A package whose claims carry a catalog identifier already **has** an identity —
the identifier is the fingerprint, and the verb never composes. A package with
an open claim set composes one from lane, verb and targets. Both feed one
ledger, one pool and one queue, because a vote records a fact about a system and
not about a framework.

`PACKAGE_SCORERS` in `evals/harness/instruments.py` is the table this follows,
and it is checked against `PACKAGES`. A fingerprint rule keyed by framework must
be checked the same way: a table nobody compares to its registry fails as
quietly as the `if` it replaced.

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
model. The rule's known error costs are the record above: 3 of 287 reference
pairs merged, 15 of 200 labelled matches split, and a split surfaces as an
unvoted finding in the queue rather than vanishing.

The gain is determinism before it is cost. A match the rule settles cannot move
between two runs of one configuration — which is exactly the band
`evals/harness/stability.py` measures and every comparison has to clear.
