---
id: 029
title: "Expand the golden corpus to 12 and run the promotion pass"
label: wayfinder:task
status: open
assignee:
blocked-by: []
---

## Question

HITL — the binding cost is serialized SME time, which is why decision 19 cut
phase 1 to six cases in the first place. Follow the blessing workflow shipped in
`evals/BLESSING.md` by
[Author the phase-1 golden corpus](022-author-golden-corpus.md).

Credential-free by construction: blessing is against the **source text**, not
against a model, which is precisely how ticket 022 produced six blessed cases
with no Vertex access. Candidate-model provenance is a separate concern owned by
[Re-bootstrap the phase-1 corpus](030-rebootstrap-corpus.md).

**Six more cases to twelve** (decision 6): the remaining internal systems, two
more OWASP cookbook conversions (CC-BY 4.0, attributed, subset-scoped if the
source diagram exceeds the 8-20 element band), and the two adversarial /
degenerate cases:

- a **sparse input** that should yield heavy `unknown` attributes and
  `needs-info` verdicts — grading the behavior ticket 003 built the `unknown`
  /assumptions distinction for;
- one that should **fail the validity gate** and exercise `repair` → `reject`.

Those two grade behaviors nothing else in the corpus reaches. Keep every case
inside 8-20 elements: non-exhaustive reference sets silently corrupt precision,
and the dangling-reference guard at load time exists because a dropped reference
lowers the recall denominator and improves a metric nobody then questions.

Preserve the near/far instrument — the corpus measures exemplar-domain bias as a
**delta**, so new far cases should keep carrying trust shapes the payments
exemplar has no instance of, and `01-payments-checkout` remains the sole control.

**Also run the corpus feedback pass** (decision 11): the artifact side shipped as
`unlisted_for_promotion` in
[Implement the eval harness, ReferenceThreat, and scorer](023-implement-eval-harness.md),
so what remains is the human pass — review recurring `valid-unlisted` threats and
promote the real ones into the reference sets. Ground truth converges from real
output, never from someone trying to be exhaustive up front. Note the ordering
wrinkle: there is no run artifact to review until sweeps happen, so if this
ticket is worked before any live run, do the six new cases now and leave the
promotion pass for the next blessing round.

`verify_corpus.py` must stay green throughout.
