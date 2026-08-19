# Claim identity, and the vote that hangs on it

A **Claim**'s identity is a value code computes from the fields the claim
carries. Nothing here reads prose, and nothing here calls a model.

This is the design [#201](https://github.com/mstarks01/work-agent/issues/201)
asks for, plus the thing it makes affordable: a human vote that stays valid
across runs, models and prompt edits.

## The rule

One question decides everything else: **what does the attacker do, and to
what?**

| Part | Where it comes from | Read by |
|---|---|---|
| framework | the block | v1, v2 |
| lane | the block | v1, v2 |
| targets | `affected_element_ids`, endpoint-resolved | v1, v2 |
| action verb | `evals/harness/verbs.py`, a closed set | v2 only |

`evals/harness/fingerprint.py` hashes those into `v<version>:<16 hex>`.

**Targets are endpoint-resolved before they are hashed.** A cited **Data Flow**
becomes the two **Element**s it runs between, and a **Trust Boundary** is
dropped. The corpus carries one finding cited as a flow by one writer and as the
process at the end of that flow by another; without this they fingerprint
differently and one finding becomes two.

## Why the verb, and what it is worth

Elements alone cannot separate a read from a write against one store. The
frontier in `tests/test_evals_identity.py` prices every element-only rule on
both errors at once:

| Rule | False splits (of 200) | False merges (of 287) |
|---|---|---|
| equality | 89 | 1 |
| endpoint subset | 14 | 23 |
| overlap | 4 | 34 |

No row is usable. `endpoint subset` clears the 90% bar on splits and pays 23
merges for it — and the verb is what buys those back. Over those 23 pairs the
vocabulary separates **20**. `verbs.UNSEPARATED` names the three it does not,
each with the reason, and none of the three is a gap in the vocabulary: one is
arguably a correct merge, one is a lane where no attacker acts, and one is a
corpus wording gap.

**That measurement is agent-authored and unreviewed**, like everything else
under `evals/`. The 23 assignments it rests on are 23 printed rows; a person
checks them in ten minutes, and `verbs.UNSEPARATED` is where the result goes.

It is also now measured through the shipped loader rather than by analysis.
Case 01 carries a verb on all 21 of its claims, and `VERB_MEASURED` in
`tests/test_evals_identity.py` pins what that buys: over its 27 within-lane
reference pairs, `endpoint subset` merges 4 and the verb cuts that to **1**. The
one survivor is the elevation pair `UNSEPARATED` already names. That test grows
as the debt shrinks, and fails when the numbers move.

## The version is in the value

A better recogniser changes every key. That would be fatal if a vote stored only
its hash — so **a vote stores its components**, and `ledger.rekey` recomputes
the whole file under a new version with no re-vote, no provider and no
credentials.

This is the property the judge design could not offer. `evals/config/judge.toml`
records the problem in its own header: a judge upgrade silently re-scores every
historical number. Here the re-score is explicit, total, offline and free.

`DEFAULT_VERSION` is 1 because twelve of thirteen cases carry no verb yet.
`tests/test_verb_coverage.py` counts that debt per case and fails when it moves
in either direction. When every case reaches zero, the default becomes 2.

## The vote

`evals/harness/ledger.py` is the only place in this repository where a **human**
judgement is the datum. Append-only JSONL; a correction is a new event, never an
edit.

**The reason code is the control for personal preference.** A reviewer who
dislikes a finding's writing and a reviewer who says it is not a threat report
two different facts, and averaging them lets taste move a recall number. So:

- `SUBSTANCE_REASONS` — the finding is wrong. Counts against the configuration,
  and the finding leaves the reference pool.
- `STYLE_REASONS` — the finding is real and badly written. Moves the writing
  score, and the finding **stays** in the pool.

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
| `evals/harness/identity.py` | `endpoint_subset`, and `SubsetVerbIdentity` as a `Judge` so one scoreboard scores both. |
| `evals/harness/ledger.py` | The append-only vote record, the reason split, the pool, the re-key. |
| `evals/harness/queue.py` | Which findings a reviewer is asked, in what order, blind to the configuration. |
| `webapp/review.py` | The reviewer's interface. Loopback, no credentials, no engine. |
