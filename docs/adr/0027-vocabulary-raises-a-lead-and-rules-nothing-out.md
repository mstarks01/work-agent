# 27. Vocabulary raises a lead and rules nothing out

- **Status**: accepted
- **Date**: 2026-09-07
- **Effort**: [#659 — the ASVS audit](https://github.com/mstarks01/work-agent/issues/659),
  finding F04
- **Amends**: [#443](https://github.com/mstarks01/work-agent/issues/443), which
  added the chapter-wide exclusion, and
  [#455](https://github.com/mstarks01/work-agent/issues/455), which added the
  per-requirement one. Both are reverted by this.

## Context

ASVS carried two tables of words. A **Presence Test** that fired nowhere ruled
out every requirement of the sections it decided; a requirement in
`REQUIREMENT_TESTS` whose own words appeared nowhere ruled itself out. Both
read the ten free-text attributes of the **Valid System Model**, matched at the
start of a word.

Together they removed **357 of 1,502 requirement rulings across the 11 ASVS
corpus cases, 24%**: 210 from the section tests and 147 from the per-requirement
table. At level 2 that is roughly 77 of 253 requirements a job never looks at.

The audit found the mechanism wrong rather than mistuned. `names_term` already
said so in its own docstring: answering `False` proves absence from the
description, not from the system. Two months of edits are the evidence. A model
saying "creates downloadable PDFs from user-supplied filenames" lost nine
file-handling requirements; "receives documents from customers" lost the same
nine. Widening the vocabulary to fix that made `document` match "documented"
and `import` match "important", so the predicate became nearly always true and
tested nothing. Each fix was found by somebody noticing one miss. The misses
nobody noticed stayed.

## Decision

**A vocabulary table may raise a candidate. It may not rule anything out.**

The tables remain as candidate rules, which is their honest job: a term that
fires hands the lane agent a lead, and a word nobody listed costs one lead. The
agent still reads the whole model and can find the thing without the word.

`REQUIREMENT_TESTS`, `FILE_TERMS`, `PresenceTest.decides` and
`ruled_out_requirements` are deleted. ASVS no longer overrides the neutral
`ruled_out` hook, and no shipped package does.

**A unit still leaves the analysis three ways, and each reads a stated fact.**
The **Precondition** refuses the framework on what the processes say they
present. The lane agent rules a requirement inapplicable and grounds it in
`absent_elements`, which the shipped exemplars demonstrate. The **Critic**
rejects a draft with `rejected_because` of `evidence`. All three are judgements
about what the model says, not inferences from what it does not.

## Consequences

**Every in-level requirement reaches a lane.** Roughly a quarter more
requirements per job, and about a third more at level 2. That is the price, it
is paid in tokens, and it buys back a quarter of the standard that was being
dropped without a reader ever seeing why.

**The deterministic exclusion is not replaced by a cheaper one.** The next
candidate is a typed feature fact: the extraction step answering "does this
system accept files from users" in three states, and the rule reading a field
instead of guessing from words. That preserves the unknown-versus-absent
distinction the audit demanded, and it is judgement done by the model rather
than by a word list in code. It needs a **System Model** schema change and a
live sweep to validate, so it is not made here.

**The hook stays.** `Claim.ruled_out` remains part of the framework contract for
a package that can refute a unit from a stated fact. Nothing overrides it today,
and `tests/test_asvs.py` asserts that ASVS does not.
