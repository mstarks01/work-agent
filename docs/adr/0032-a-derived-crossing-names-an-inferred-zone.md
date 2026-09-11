# 32. A derived crossing names the endpoints whose zone was inferred

- **Status**: accepted
- **Date**: 2026-09-11
- **Closes**:
  [issue #468](https://github.com/mstarks01/work-agent/issues/468), split out of
  [#465](https://github.com/mstarks01/work-agent/issues/465), which settled every
  other decision it held.
- **Relates to**:
  [ADR 0010](0010-package-cannot-extend-the-evidence-catalog.md), whose three
  tests any widening of what an agent may read has to pass, and
  [ADR 0012](0012-the-catalog-carries-a-stated-absence.md), the first widening
  argued against them.

## Context

`CONTEXT.md`'s **Assumption** entry gives extraction a tie-break. Where two
**Sources** make positive conflicting claims about an attribute that cannot hold
`unknown`, extraction emits a legal value anyway and records an **Assumption**;
for `trust_zone` it picks the reading that puts the two ends of a flow in
different zones. `prompts/extract.md` rule 6 reproduces it.

#468 asked whether extraction should preserve the uncertainty instead. The
argument against the tie-break was never that the pick is wrong. It was that
**the Assumption does not reach the place the inference lands**:
`boundary_crossings()` derives a crossing from zones alone, `evidence.py`
publishes `crossing:<flow-id>` as a `derived-fact` **Ground**, and 11 of
STRIDE's 12 candidate rules key on the crossing set. Nothing on that path could
see that one endpoint's zone was contested, so a **Lane Agent** cited a
mechanically derived fact that was not entirely one.

**The measurement widened the question.** Across the 13 corpus models, the
disagreement tie-break has fired **zero** times. A `trust_zone` Assumption from
some *other* basis has fired nine times, and those nine sit under **9 of the 43
crossings** the corpus derives, in 5 of the 13 cases. Every one of them infers a
zone from silence rather than from a conflict — "the source names no other
placement for them", "it does not say where the provider runs". A rule written
for the tie-break alone would have covered none of the corpus.

## Decision

**Extraction keeps picking the reading that yields a crossing, and the crossing
names the endpoints whose zone was inferred.** `BoundaryCrossing` carries
`assumed_endpoints`: the endpoint element IDs whose `trust_zone` the model
records an Assumption for, in source-then-destination order, empty when the
input stated both.

**The mark is derived, never declared.** `SystemModel.assumed_zone_elements()`
is the one reader of the question, and `boundary_crossings()` is its only
caller, so the mark recomputes from the same model as the crossing and the two
cannot disagree. The `Report` envelope already checks its embedded crossings
against the ones it derives, which is what makes this field checkable rather
than asserted — and is why 24 archived reports had to be caught up before it
could land.

**Every basis, not only a disagreement.** A zone inferred from silence is as
unstated as a zone inferred from a conflict, and the rule is a property of the
Assumption entry rather than of the prose behind it, so nothing parses a basis
to decide. That is also the rule the corpus supports: the narrow reading covers
nothing that exists today.

**What reads the mark: the lane agent, the critic and the report. Not the
Ground, and not the candidate rules.** A `derived-fact` Ground carries a flow ID
and no zones, because the crossing recomputes from the model the report already
embeds; the mark travels the same way for the same reason, and copying it into
the Ground would be a second copy to disagree with the first. `prompts/analyze.md`
and `prompts/critic.md` both say what the field means, because both already
render the crossings block.

**A marked crossing fires every rule it fired before.** No candidate rule reads
the field, so recall and precision cannot move, and this decision owes no
measurement. #468's third question — whether a marked crossing should still fire
a candidate — is answered *yes, unchanged*, and deliberately: the alternative
moves two numbers the corpus measures, which makes it a separate decision with
its own paid evidence. This ADR does not authorise it.

**Against ADR 0010's three tests**, which the mark has to pass because it
reaches an agent: it is a **pure function of the Valid System Model** (the
`assumptions` list and the endpoints' zones); it is **framework-neutral** —
every lane of every framework reads the same crossings block, and nothing in the
field names a method; and it introduces **no new ID** at all, because the
evidence catalog is untouched. `crossing:<flow-id>` still resolves to the same
entry with the same gloss. The catalog gains no row, no kind and no derivation,
so this is narrower than ADR 0012's widening rather than another one.

## Consequences

**A crossing that rests on an inference now says so where it is read.** On the
corpus that is 9 crossings of 43. The lane agent reads it in the crossings
block, the critic reads it when it rules on whether a ground supports a claim,
and `webapp/static/report_view.js` prints it beside the crossing rather than
leaving a reader to join two lists.

**The mark reaches a crossing that exists, and never the absence of one.** A
zone inferred *equal* to its neighbour's produces no crossing, so there is no
record to carry a mark, and the missing threat stays invisible. That is the
honest limit, and it is the argument for keeping the tie-break rather than
against it: **the direction extraction picks is the one a mark can reach.** The
rule's own trade-off — "a component modelled in error is visible, one omitted is
an invisible missing threat" — now holds for zones as well as for components.
`tests/test_system_model.py` records the limit as a test rather than as prose.

**The archive needed a migration, and a merged Baseline's digests needed a
refresh.** 24 of the 78 archived reports embed a model with a `trust_zone`
assumption, and their stored crossings no longer matched the derived ones, so
`run.py score` was dead on them. `evals/migrations/2026-09-11-crossing-assumed-endpoints.py`
recomputes `boundary_crossings` from the model in the same file — nothing is
inferred, because the value is derived — and refreshes the file digests in the
one merged Baseline's manifest through the same reader `verify` uses. The
migration rewrites all 78 so the archive holds one shape.

**A merged Baseline outlives the PR that laid it down, and the contribution
gate now says so.** `submit.detect_kind` read a touch of `evals/baselines/` as a
baseline submission, so this PR's migration failed "nothing outside this kind's
allowlist changed" for every code file beside it, with no diff that could pass.
That is the shape the kind's `derived` entry already records for the comparison
table. `Kind` gains a `selects` member with no default, and the baseline kind
answers it with "this diff adds a sweep", read by the one reader
`added_sweeps`, which the author-stamp check now calls too. A PR that adds a
sweep still selects the kind, so #320's binding, #323's digests and the
one-directory rule run on every contribution; a maintenance diff loses the
checklist and no check that was protecting anything.

**`prompts/critic` costs 16 more static tokens and its cap moves 2100 → 2400.**
What the sentence buys is the critic's ability to weigh a ground it is already
being shown: without it, `assumed_endpoints` arrives in a fenced JSON block as a
key nothing explains.

**What was considered and rejected: preserving the uncertainty instead of
picking a side.** It is what #468 asked about, and it loses the crossing. A flow
whose endpoints land in one zone derives nothing, so an agent is not told less
about the crossing — it is told nothing at all, and no mark can reach a record
that does not exist. Marking the pick keeps both halves: the crossing is
derived, and what it rests on is on the record beside it.

**What was considered and rejected: a fifth `Ground` kind for a contested
crossing.** ADR 0012 took a fourth kind because an unknown and a stated absence
carry identical fields and different *facts*, and a consumer folding them would
report one as the other. This is not that shape. A crossing is one fact whether
or not a zone was inferred, the claim resting on it is not conditional in a new
way, and every one of STRIDE's crossing rules would have to answer for a kind
that means the same thing. A field on the record the kind already points at is
where the difference belongs.
