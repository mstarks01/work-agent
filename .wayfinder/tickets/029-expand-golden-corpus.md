---
id: 029
title: "Expand the golden corpus to 12 and run the promotion pass"
label: wayfinder:task
status: resolved
assignee: github@michaelstarks.com
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

## Progress

### 2026-07-21 — the validity-gate case is dropped, and what replaced it

The second adversarial case as specified above — one that should **fail the
validity gate** and exercise `repair` → `reject` — **cannot be built, and should
not be**. Confirmed with the user on 2026-07-21 and replaced rather than
deferred.

Three reasons, in increasing order of how much they matter:

1. **The corpus schema cannot express it.** Every case must carry a `model.json`
   that *passes* `parse_and_validate` and a `threats.json` with a reference in
   all six lanes (`evals/verify_corpus.py`). A rejected job produces no threats,
   so there is nothing to score against, and `CASE_FIELDS` is closed — there is
   no expected-outcome field to branch on.
2. **`repair` → `reject` is not reachable from source text.** The validity gate
   checks well-formedness — unique typed IDs, referential integrity, zone
   membership, legal enums. Those are properties of *what the extractor emits*,
   never of what the user wrote. No prose obliges a correct extractor to emit a
   dangling reference, so a well-behaved `extract` would make the case "fail",
   which scores the tool down for working. Input containing no system at all
   does not help either: that yields an empty-but-well-formed model, which the
   gate passes.
3. **It is already covered, offline, for free.** `tests/test_pipeline.py:201`
   feeds the graph a model with a dangling `destination` twice and asserts
   `PipelineRejected` carrying the issues, `prepare` never reached, no analyst
   run; routing is pinned at `tests/test_graph.py:127`, issue parking at `:228`,
   and surfacing through the job and API layers at `tests/test_jobs.py:176` and
   `tests/test_api.py:172`. Scripted stand-ins, no credentials, no SME time.

**Replaced by an over-claiming source** — `12-overclaiming-supplier-portal`.
Where the sparse case grades silence, this one grades noise dressed as fact: a
vendor-hosted SaaS described almost entirely in security marketing language
(secure by design, enterprise-grade encryption throughout, fully authenticated
and audited, fully compliant), none of which may set an attribute, plus a
straight self-contradiction — the nightly extract is claimed encrypted end to
end where the runbook says it arrives as a plain CSV — which must resolve to
`unknown` rather than to either side. One genuine control is stated among the
noise so the case cannot be passed by discarding everything, and one genuine
absence is stated so grounded findings remain available.

This keeps decision 6's composition at 12 cases with 2 adversarial, and it
grades the failure mode `BLESSING.md` step 3 names as "the most common and most
damaging error" together with its inverse, invented absence.

### 2026-07-21 — cases 11 and 12 blessed

Both adversarial cases are authored, blessed and merged:
`evals/corpus/11-sparse-shift-scheduling` and
`evals/corpus/12-overclaiming-supplier-portal`. `verify_corpus.py` is green at 8
cases, the offline suite is 416 passed / 1 skipped, and 47 hand-labelled
judge-calibration pairs were added for the two cases (176 total, 106 match / 70
no-match), weighted toward hard negatives and ungrounded candidates as step 5
requires.

The two are a deliberate pair pulling on opposite sides of one mechanism: on a
sparse input the candidate invents values to fill gaps, and on an assurance-dense
one it adopts values the text supplies but never establishes. Both produce a
confident non-`unknown` attribute the source does not support, so both are graded
by the same `unknown`/assumption distinction.

`tests/test_evals_reference.py::test_loads_every_shipped_case` carried the corpus
size as a literal and was updated 6 → 8; it will need updating again as the
remaining cases land.

**Remaining on this ticket:** cases 07 and 08 (internal systems — the user chose
a **CI/CD build-and-deploy pipeline** and an **SSO / identity broker**, for a
large retail estate; trust flowing upward through a build, and claims-based
rather than network-based trust, neither of which any current case has), cases
09 and 10 (two OWASP cookbook conversions, entries to be picked and reviewed at
blessing), and the promotion pass, which stays deferred until run artifacts
exist.

### 2026-07-21 — cases 07 and 08 blessed

Both internal-systems cases are authored, blessed and merged:
`evals/corpus/07-cicd-store-deploy` and `evals/corpus/08-sso-identity-broker`.
`verify_corpus.py` is green at 10 cases, the offline suite is 418 passed / 1
skipped, and 64 more hand-labelled pairs landed (240 total, 142 match / 98
no-match), each case's set weighted toward hard negatives.

The pair is not two systems so much as two attacks on the same instrument.
**07** is the only case where authority flows *upward through a build* — a
developer's input becomes the artifact running in 1,200 stores — and where both
direction traps are pull-shaped, so every flow runs against the intuition that
data flow and initiation agree. **08** is the case where **the boundary
crossings under-describe the risk**: its two highest-severity references cross
nothing at all, so an analyst that follows only the crossings scores well
everywhere else in the corpus and badly here. That is a deliberate counterweight
to the fact that crossings are the highest-signal STRIDE input the design leans
on — until now nothing in the corpus could tell a model that leans on them *too*
hard from one that reads the system.

Three findings from the bootstraps, which are signal about `prompts/extract.md`
and not about the pinned Flash node (same credential constraint; re-bootstrap is
[ticket 030](030-rebootstrap-corpus.md)):

- **The stranded qualifier appeared in a new and worse form, then failed to
  appear at all.** In 07 a *stated absence* — "the runner does not verify
  signatures" — reached `description` and `data_description` but left
  `authentication` at `unknown`, which does not lose the fact so much as
  **downgrade the verdict**, grounded to needs-info, invisibly. In 08 the
  equivalent qualifier landed in the attribute intact. The difference worth
  testing is that 08's qualifier sits in the same sentence as the behaviour it
  qualifies.
- **Asset tags come from what an element is called, not from what the source
  says is in it** — both cases, four instances, in both directions: flows
  carrying the system's crown jewels tagged empty, a flow the source calls the
  *public* half tagged `secrets`, a directory tagged `credentials` because
  directories usually hold them.
- **08's candidate is the corpus's first that the validator rejects** (two
  `id-mismatch` errors), and it came back at 23 elements, outside the band. The
  over-production was one reflex from three angles: a response modelled as its
  own flow, a generalized class modelled beside its one named instance, and an
  out-of-scope actor invented to hang an assumption on. Neither defect is a
  recall failure, and both have mechanical fixes worth weighing against prompt
  changes — deriving the ID in code from the emitted name being the obvious one.

`tests/test_evals_reference.py::test_loads_every_shipped_case` went 8 → 10 and
will need updating again as 09 and 10 land.

**Remaining after this session:** cases 09 and 10 (the two OWASP cookbook
conversions — entries still to be picked, and to be reviewed with the case at
blessing), and the promotion pass, still deferred until run artifacts exist.

## Resolution

**Twelve cases, blessed and merged.** The two OWASP cookbook conversions landed
this session as `evals/corpus/09-cookbook-sokify-retail` (Threat Dragon
`alt1-sokify.json`) and `evals/corpus/10-cookbook-generic-cms` (pytm
`generic-cms.py`), both CC-BY 4.0 and attributed in `case.json`. `verify_corpus.py`
is green at 12 cases, the offline suite is 420 passed / 1 skipped, and the
calibration set stands at 313 hand-labelled pairs (185 match / 128 no-match), 73
of them added here and weighted toward hard negatives. `test_loads_every_shipped_case` went 10 → 12.

Decision 6's composition is now complete: 12 cases, 1 near control, 4 cookbook
conversions, 2 adversarial. 09 is scoped (the Instagram leg dropped, the source's
store-to-store address copy re-modelled as SIMS performing the export, since a
data store does not initiate an interaction); 10 is the only conversion taken
whole, the source diagram landing inside the band unscoped.

**What the two cases buy that the corpus did not have.** 09 carries a standing
write path from an unmanaged endpoint in another zone — marketing's spreadsheet
macros speak SQL straight into the web API, so catalogue authority and database
authority are one authority — and the fax leg, where a control is not
*unverified* but *unavailable*. That distinction is new: every other unknown in
the corpus is a fact nobody wrote down, and this one is a control the channel
cannot have. 10 carries mixed transport (page over HTTPS, executable furniture
over HTTP) and a path that bypasses the application entirely, which is where its
repudiation lane comes from.

**The extraction findings, which are the part worth carrying forward.**

- **The `unknown` rule is asymmetric in practice.** Every stranded-qualifier
  correction the corpus had recorded until now involved a stated *absence*. Case
  10 is the first with a stated *presence* — "some of them signed in" — and the
  candidate routed it to `data_description` while leaving `authentication` at
  `unknown`, which tells an analyst the opposite of what the source says. This
  **disconfirms the in-sentence hypothesis** case 08 raised: that qualifier sits
  in the same sentence as the behaviour it qualifies and still did not land. The
  reading that fits all four observations is whether the qualifier is phrased
  about the **link** ("not HTTPS", "no TLS on it" — both landed) or about the
  **actor** ("some of them signed in", "the runner does not verify signatures" —
  both missed). `prompts/extract.md` is written entirely around the danger of
  inventing a control and says nothing about the text saying something vague.
  Left as fog on the map: it is a prompt experiment, and it wants the instrument.
- **The invented fact arrived through `data_description`, not through a governed
  attribute.** On case 09 the candidate handled all five enumerated attributes
  correctly and then put payment card details on the customer path, which the
  source never states — it says only that the *database* holds the card they paid
  with — and propagated it into a `financial` asset tag. The prompt's whole
  defence is built around the five attributes; `data_description` and `assets`
  are downstream of the same failure with no equivalent guard.
- **Asset tags follow what the element is called: fourth and fifth instances.**
  A customer tagged `pii`/`financial` because they are a customer, and a database
  tagged `credentials` because it holds "accounts".
- **`id-mismatch` is now 3 candidates out of 3** — 2 errors on case 08, 14 on 09,
  5 on 10, 21 in total, every one of them a name and an ID that disagree while
  the reading of the source is correct. This is no longer a per-case observation,
  and it graduates as
  [Derive element IDs in code instead of asking the model for two agreeing fields](037-derive-element-ids-in-code.md).

**The promotion pass is not done, and does not belong here.** Decision 11's
feedback pass needs `valid-unlisted` threats from a real run artifact, and no
run has happened — the ordering wrinkle this ticket anticipated. Rather than
hold the expansion open behind
[Provision Vertex access](027-provision-live-infrastructure.md), which is parked
by the user's call, it graduates as
[Run the corpus promotion pass](036-corpus-promotion-pass.md), blocked by
[Establish baselines and promote the gates](032-establish-baselines.md).

Same credential constraint as every corpus ticket: candidates came from an agent
stand-in running `prompts/extract.md`, so the diffs above are signal about the
prompt and not about the pinned Flash node. Re-bootstrapping stays with
[ticket 030](030-rebootstrap-corpus.md).
