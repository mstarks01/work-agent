# One rule, one reader

The first defect class in `docs/agents/code-review.md`, with its corollaries.

When two pieces of code answer the same question, they will eventually answer it
differently, and the disagreement is invisible because each one's test agrees
with it. **Give a rule one reader and let every other site call it.**

Where a second reader is unavoidable — an app and an offline gate, a harness
check and a corpus lint — test the two **against each other**, never each
against its own expectation.

Examples of one question with two readers, each tested separately: what an
UNREVIEWED key is (substring vs `ast`); whether a finding is answered
(`queue.build` vs `Session.remaining`); which UNREVIEWED table is the table
(first assignment vs last); which version keys a ledger row (`__post_init__` vs
`rekey` vs `VERSION_FOR`); what a filled reading document is (two copies of one
line); when an element ID is checked (the rule and the deriver, on the
empty-slug case).

Five corollaries:

- **A self-sized fence is safe only while its neighbours are fenced too.** Ask
  what sits beside the value, not only what wraps it.
- **A bound that predicts a cost from its inputs is wrong whenever the cost
  turns on which inputs survive a filter.** Spend a budget where the work
  happens.
- **A rule that re-derives a value must compare it against the material it
  derives from, never against a value something else derived earlier.** The two
  are readers of one rule separated by *time* rather than by place, so they
  agree until the rule moves and there is no site to read side by side.
  Examples from the alignment and the archive (#1041): an element alias
  re-slugged today against an **Element ID** slugged when the run wrote it; a
  flow alias against a label baked into a flow ID; a signed reference's subject
  ID against an archived catalog's. A mismatch breaks a ruling that is still
  right about the words.

  The repair is one of three, in this order. Derive both sides from the
  authoritative material now — a **name** is authoritative and an ID follows,
  which is what `normalize_element_ids` already states. Where one side is
  frozen, store the components beside the derived key, as a `Vote` stores
  `components` beside `fingerprint`, so a rule change re-keys by recomputation.
  Where neither is possible, version the rule and keep every version's decoder,
  as `FLOW_ID_RULES` does.

  **Before changing any slug, identity or digest rule, replay the archive
  first.** `run.py replay` over `evals/emissions/` costs nothing, and the diff
  is the only thing that says whether a rule change moved a figure. A fixture
  that sets an ID without its name hides this whole class.
- **A check before admission runs the writer it guards, and discards the
  result.** `check_answers` writes the link answers with `apply_answers`, and
  the attribute check builds the answered model and asks the validity gate. A
  check that restates the writer's rules admits what the writer then refuses
  (#1289, Q5).
- **Two writers of one fact need a stated precedence and a test that asks
  every reader.** An answer and the catalog's projection both write an
  attribute (#1289, Q1).
  `tests/test_answer_invariants.py` drives the resume graph once for each
  `PROJECTION_EFFECT` reason and asks the model, the catalog and a later
  reader's view for the same fact.
