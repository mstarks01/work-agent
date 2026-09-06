# Calibration-label review 02 — boundary census and random sample

**Date:** 2026-09-02  
**Tracking issue:** [#534](https://github.com/mstarks01/work-agent/issues/534)  
**Machine-readable record:** [`reviews/02.json`](reviews/02.json)

This review had two parts:

1. a census of 44 decision-boundary fixtures: every pre-review candidate false
   merge, false split, `unsupported` fixture, and scored fixture the shipped
   matcher refused; and
2. a simple random sample without replacement of 60 of the remaining 295
   fixtures.

For each pair, the assistant first assessed the reference and candidate claims
without seeing the stored label or note. The human reviewer then saw that
recommendation and made the final decision. This is a joint review, not two
independent ratings and not an inter-rater-reliability measurement.

## Random-sample result

The final joint disposition agreed with the original agent-authored primary
label on **58 of 60** sampled fixtures. Two `no-match` fixtures were changed to
`unsupported`, for an observed discrepancy rate of **3.3%**.

Under simple random sampling without replacement from this 295-fixture
non-boundary population, exact equal-tail inversion of the hypergeometric
distribution gives a **95% interval of 0.7%–10.5%** for the population label
discrepancy rate (2–31 fixtures of 295).

That interval measures sampling uncertainty around agreement with this joint
review standard. It does **not** measure matcher accuracy, corpus correctness,
threat quality, production-model quality, or independent reviewer agreement.
It also does not describe the deliberately excluded 44 boundary fixtures.

The seed, snapshot digest, ordered fixture manifests, changed fixture IDs, and
exact interval inputs are fields in `reviews/02.json`. Tests reproduce the
sample and interval so those provenance claims cannot drift away from the data.

## Applied vocabulary

The review retained `match`, `no-match`, `unclear`, and `unsupported`; added
`invalid-claim` for text that is not a STRIDE threat claim at all; and added two
diagnostic annotations that do not affect scoring:

- `mixed` — the candidate crosses a second decision axis in addition to its
  primary disposition;
- `misclassified-lane` — the candidate describes a valid threat in another
  STRIDE lane.

After applying both review parts, the 339 fixtures contain 200 `match`, 111
`no-match`, 25 `unsupported`, 2 `unclear`, and 1 `invalid-claim` dispositions.
The shipped matcher has 14 false splits, 3 candidate false merges, no refusals,
and 294/311 (94.5%) agreement with the scored dispositions.
