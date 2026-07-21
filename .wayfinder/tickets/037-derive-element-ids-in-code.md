---
id: 037
title: "Derive element IDs in code instead of asking the model for two agreeing fields"
label: wayfinder:task
status: open
assignee:
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
