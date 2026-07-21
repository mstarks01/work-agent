---
id: 037
title: "Derive element IDs in code instead of asking the model for two agreeing fields"
label: wayfinder:task
status: resolved
assignee: github@michaelstarks.com
blocked-by: []
---

## Question

Credential-free, and graduated from
[Expand the golden corpus to 12](029-expand-golden-corpus.md) on evidence rather
than on taste: **three bootstrap candidates out of three have failed the same
mechanical rule**, and the count is now large enough to act on.

- case 08: 2 `id-mismatch` errors, and the candidate was the corpus's first that
  the validator rejected outright
- case 09: 14
- case 10: 5

Twenty-one errors, every one of the same shape — the model emits a sensible ID
and a sensible `name`, and the two disagree, because `validation.py` requires
`id == make_element_id(prefix, name)` and, for flows,
`id == make_flow_id(source, destination, name)`. The reading of the source is
correct every time this fires. `prompts/extract.md` states the ID rule correctly
and the model still applies it to a shorter name than the one it goes on to
emit; the corpus's own blessing pass fixed all 21 by editing `name`, never by
editing the ID.

The question is where the invariant belongs. Two fields that must agree is a
constraint the model is being asked to satisfy by hand, and it costs the one
repair pass the design allows — spent on a defect with no bearing on extraction
quality, on a job that may then have no repair left for a real referential
error.

The obvious move is to **derive the ID in code from the emitted name** and stop
asking for it, which would have erased all 21 errors with no prompt change at
all. Before doing that, settle what it breaks:

1. **Do IDs stay stable?** They are already a pure function of the name, so
   deriving them changes nothing about their value — but the model would lose
   the ability to *choose* a shorter ID than its own name, which is what all 21
   candidates were reaching for. If short IDs are worth having, the fix is a
   short `name`, not a divergent ID.
2. **What emits the ID today?** Flow references (`source`, `destination`),
   `assumptions[].element_id`, and every reference threat in the corpus cite IDs.
   Deriving means the model must still cite IDs it has not been told — so
   derivation may have to run as a normalization pass that rewrites references
   too, not merely as a field-drop.
3. **Does the schema change, or only the pipeline?** `_Element.id` is a required
   field on the shipped Pydantic models, and `parse_and_validate` is used by
   `verify_corpus.py`, the corpus, and the graph. A schema change touches all
   three; a pre-validation normalization step touches one.
4. **Does `repair` still have a job?** If it does, keep the `id-mismatch` issue
   code for models that arrive from elsewhere; if the normalization runs first,
   the issue becomes unreachable from `extract` and should be documented as such
   rather than deleted.

Deterministic decisions belong in code and models are for judgement — which side
of that line an ID slug sits on is not in doubt.

Resolved when the decision is made and, if it is to derive, the change is shipped
with the offline suite green and `prompts/extract.md` reconciled to whatever the
model is still asked for.

## Resolution

**Derive.** Names are authoritative, IDs follow, and the model is no longer
asked to keep two fields in agreement. Shipped as
`stride_service.system_model.normalize_element_ids` — a pure function returning
a normalized copy — plus `derive_element_id`, which is now the single
definition of the invariant that `validation.validate` compares against
(`_expected_id` deleted). Suite 436 green (was 420), `verify_corpus.py` clean at
12 cases, 0 problems.

The four questions, settled:

**1. IDs stay stable, and the model loses only the abbreviation.** The value is
unchanged for every element whose ID already matched — `normalize_element_ids`
is the identity on a valid model, asserted as a test. What the model gives up
is the shorter-than-its-name slug all 21 errors were reaching for, and the
answer to wanting one is a shorter `name`, which is what the blessing pass
reached for too: it fixed all 21 by editing `name`, never the ID. Worth naming
the alternative that was rejected — **dropping the `id == f(name)` rule
entirely** and requiring only a unique typed slug would also have erased the 21
errors, and it is the wrong direction: it hands the model *more* freedom over a
mechanical field, and it un-guards ID legibility, which is what makes a threat
citing `process:auth-service` traceable at all. Deriving and de-constraining
both remove the error; only one of them removes the judgement.

**2. Derivation is a whole-model rewrite, not a field-drop.** This is the part
that would have broken had it shipped naively: flow `source`/`destination`,
`trust_zone`, and `assumptions[].element_id` all cite IDs, so overwriting IDs
alone converts 21 `id-mismatch` errors into a comparable pile of
`invalid-reference` ones — the same job failing at a different rule. The pass
therefore records old→new for every element it rewrites and repoints every
reference through it, in an order the dependency dictates: non-flow elements
first, then zones and endpoints, then flow IDs (built from the endpoints' *new*
IDs), then assumptions (which may cite a flow). A self-consistent model stays
self-consistent; a dangling reference is left exactly as emitted, for the gate
to report.

**3. The schema does not change — only the pipeline.** `_Element.id` stays
required, so the corpus, the reports, and every reference threat are untouched.
Normalization is opt-in via `parse_and_validate(..., normalize_ids=True)`, and
the choice of who passes it is the load-bearing half of this decision:

- `validate_extraction` (both validate nodes, so `repair`'s output is
  normalized too) — **on**.
- `run_extraction` in the eval harness — **on**, because `score_extraction`
  compares blessed and candidate element IDs by set membership, and blessed IDs
  are already derived. An abbreviated candidate slug scored as one missing
  element *and* one extra, on a reading of the source that was correct.
- `verify_corpus.py` and `reference.py` — **off**, and deliberately. In a
  hand-authored `model.json` two disagreeing fields are an authoring error a
  human should be shown, not a slug to canonicalize silently.

That split is also the answer to the "reports and denies, never silently
auto-repairs" rule: this is derivation, not repair. It reads no source text and
decides nothing the gate did not already know — the ID was always a pure
function of the name. Repair is for facts; this is for representation.

**4. `repair` keeps its job, and gets it back.** `invalid-reference`,
`duplicate-id`, `no-trust-zones`, `illegal-asset-tag` and `schema` are all
still reachable; what changes is that the one repair pass the design allows is
no longer spent on a defect with no bearing on extraction quality. The
`id-mismatch` code is **kept and now unreachable from `extract`** — it still
fires for models that arrive hand-authored, which is exactly the corpus path
above, and `validation.py`'s docstring records that reachability split so the
code is not later read as dead.

One consequence worth watching rather than fixing: **normalization can create a
`duplicate-id` that the emitted model did not have**, when two elements share a
name and were told apart only by hand-shortened IDs. That is a real defect
surfacing, not one introduced — two elements with one name is the class/instance
duplication cases 08 and 09 already record — and it now arrives as the gate
error it always was, with `repair` and the source text available to resolve it.
Tested.

`prompts/extract.md` reconciled at step 3: the model is still asked for IDs
(references need them and the schema requires them), but told they are
recomputed from the names it gives, that abbreviating one is pointless because
references follow automatically, and that two elements sharing a name share an
ID. No other prompt changed — `repair` never sees the issue code.

**Not verified against the real Flash node**, same constraint as 022/023/026/028:
the 21 errors came from agent stand-ins, so what is proven here is that the
failure shape cannot reach the gate, not that the pinned model produces it at
the same rate. [Re-bootstrap the phase-1 corpus](030-rebootstrap-corpus.md) is
where that number comes from — and it should now find `id-mismatch` at zero by
construction, which makes the rest of its taxonomy easier to read.
