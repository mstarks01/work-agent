# Quality contract

Seven axes. Report them apart, because a change that moves one usually moves
another the other way, and one combined number hides that.

For each axis: what it means, which instrument reads it today, and what the
number does not say.

## 1. Recall of important, correct findings

**Reads it:** `score` writes must-find coverage and reference coverage per
framework. `evals/baselines/README.md` carries the merged figures.

**Does not say:** that a matched claim is semantically right. The match is a
deterministic identity rule over an action verb and a resolved element set. Two
claims can match on identity and mean different things.

**Never improve this by flooding.** More findings raises coverage and lowers
precision, and the negative control below is what catches it.

## 2. Reviewed precision, and unsupported conclusions

**Reads it:** the rejected rate and the writing objections, both from the vote
ledger. `review --voter <login>` says what is waiting.

**Does not say:** anything at all where nobody has voted. Most Baselines read
`no votes yet`. Report the denominator every time, and never quote a precision
figure without it.

**Unlisted is not wrong.** The reference sets are incomplete, so an output the
corpus does not list may be a real finding. That is what the `valid-unlisted`
queue is for.

## 3. Disposition

**Reads it:** ASVS scores a verdict and a scope entry per requirement.
`attribution` in a scored artifact charges each wrong disposition to a stage.

**Does not say:** whether a `needs_evidence` was the right call. Read the
verdict and the scope entry, not the flag.

## 4. Evidence, scope, provenance and uncertainty

**Reads it:** the structural gates — grounds shape, quote verification,
reference resolution — run on every sweep and block a run that fails them.

**Does not say:** that the quote supports the claim. There is no entailment
check. A verified quote is a quote that exists in the source and nothing more.

## 5. Remediation and questions

**Reads it:** nothing mechanical. This axis is read by a person, through the
Case Sitting and the vote ledger.

**Say so.** An audit that reports this axis as measured is wrong.

## 6. Repeatability

**Reads it:** `stability` over two or more sweeps of one configuration. Use
`--calibrate` with a repeat set, or the band is reported as a floor.

**Does not say:** that a difference inside the band is nothing. It says you
cannot tell from these runs.

**Measure the band before comparing two runs.** An unchanged configuration has
given 2, 2, 5, 5, 10 on one reading.

## 7. Cost and latency

**Reads it:** the sweep envelope — token usage, charges per node, latency per
node — recorded in every artifact.

**Does not say:** what a cache hit cost. Meter paid requests, not calls.

## Negative controls

Two, and an audit that claims a recall gain runs both:

1. **Precision beside recall.** A gain in coverage with a rise in rejected or
   unmatched output is a flood, not an improvement.
2. **Coverage beside the flag count.** An empty flag list is three different
   facts — nothing found, nothing measured, or nothing reported. Report the
   coverage denominator beside the findings, or an unmeasured model reads as
   clean.

## What the corpus cannot establish

Reports better than a competent human analyst is the long-term ambition, and
corpus recall cannot measure it. That needs blind, independent comparison on
unseen systems. Say that plainly whenever an audit's conclusion is about
quality rather than about a mechanism, and do not let the missing study block
the audit.
