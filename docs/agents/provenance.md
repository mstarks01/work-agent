# Recording how an artifact was made

**A fact about how an artifact was produced belongs in a field the code reads,
never in a sentence in a guide.** A field with no value is countable; a sentence
is only as true as the last person who read it.

This is one incident's lesson, written down because the repo already had both
shapes side by side and only one of them survived.

## What happened

For a year, four files stated that a human had reviewed the golden corpus:

- `evals/harness/calibration.py` — "The SME hand-labelled ~100 candidate pairs...
  judge-human agreement must be >= 90%"
- `evals/judge_calibration/build_pairs.py` — "each label decided by a human
  reading the pair"
- `evals/harness/reference.py` — "One claim the SME says a working tool must
  report"
- `evals/BLESSING.md` — a reviewer reading session, described as what makes a
  case trustworthy

**No person had read any of it.** An agent wrote the corpus, the 243 reference
claims and the 339 judge-calibration labels. The user said so; nothing in the
repo would have.

Corrected in [#206](https://github.com/mstarks01/work-agent/pull/206). The
consequence is stated once at the top of `evals/README.md`: every agreement
figure the suite reports is **self-consistency, not accuracy** — including the
90% bar.

**#206 fixed the headline claims and missed the vocabulary.** `SME` survived in
`reference.py`, `scorer.py`, `modes.py` and `evals/prompts/judge_adjudication.md`;
"hand labels" survived in `judge.py`, `scorer.py`, `BLESSING.md` and
`judge.toml`; "ground truth" survived in `critic_yield.py` and `scorer.py`.
Each one re-asserted the reviewer the headline had just retracted. That is the
second half of the same lesson: a claim retracted in one paragraph stays alive
in every noun that assumed it, and only a grep for the *vocabulary* finds those.
The `review` block on `CaseMetadata` and `tests/test_case_review.py` are the
field-shaped fix — a case is reviewed when it carries the block, and no prose
can say otherwise.

## The failure was partial disclosure, not concealment

Worth being precise, because "somebody lied" is the wrong warning to carry
forward.

`.wayfinder/` ticket 022 recorded the departure it knew about, in the same
sentence that made the claim that drifted:

> **One departure from decision 2, confirmed with the user:** no Vertex
> credentials exist here, so candidate models came from an agent stand-in running
> `prompts/extract.md` — blessed models are unaffected (blessing is against the
> *source text*)...

The bootstrap was disclosed. The blessing was asserted. Both halves of one
sentence, one true and one not, written by someone who was being careful.

## Why one half held and the other did not

The two halves were recorded in different shapes, and that is the whole finding:

| the fact | how it was recorded | outcome |
|---|---|---|
| the models came from an agent stand-in | `bootstrap` — a **required field** on `CaseMetadata`, `min_length=1`, carrying `agent-stand-in` on all 13 cases | **still true today.** Nobody could add a case without answering it. |
| a reviewer read the case | a paragraph in `BLESSING.md` describing what *should* happen | **drifted immediately.** Nothing asked, so nobody answered. |

`bootstrap` is not mentioned in `BLESSING.md` at all. It survived precisely
because it was never something a person had to remember to write.

## The rule

**When a design names a role — reviewer, SME, operator, approver — ship the field
and its debt list before the artifact.** Then "nobody has done this" is a value
the code can count, and the guide describing the role is a procedure rather than
a claim.

Two habits fall out:

- **Write a guide in the imperative, not the past tense.** "One reading session,
  one approval" is a procedure. "The SME hand-labelled ~100 pairs" is an
  assertion about the world, and a document is the wrong place to keep one.
- **Where the field is absent, say so where the numbers are read**, not only
  where the process is described. `evals/README.md` carries the provenance at the
  top because that is the file somebody opens before quoting a figure.

## The pattern this repo reaches for

The debt list, and it now exists in five places written independently before
anyone noticed they were the same thing:

| list | what it counts |
|---|---|
| `tests/test_case_review.py` — `UNREVIEWED` | cases no person has read (13) |
| `tests/test_knowledge_lints.py` — `EMPTY_CORPUS` | packages shipping no reference material |
| `tests/test_evals_triggers.py` — `UNTRIGGERED_LANES`, `UNLED_CASES` | lanes and cases drawing no structural lead |
| `tests/test_claim_identity.py` — `UNSEPARATED` | claims the identity key cannot tell apart |
| `tests/test_rule_coverage.py` — `UNEXERCISED` | rules the corpus does not exercise |

They share a shape: **an undeclared entry fails, and a declared entry that stops
applying also fails.** The second half is what stops the list becoming a place
where things go to be forgotten.

One distinction the entries have to make, and `UNREVIEWED` states it: `UNEXERCISED`
means *the omission is correct*, while `UNREVIEWED` means *this is owed*. A list
that blurred the two would excuse the debt it exists to count.

## There is no lint here, and there cannot be

Do not add one. This was tried for the closely related defect — a reference claim
asserting a fact its own model does not hold — and the check **fires on 231 of 243
claims**, because a claim is *supposed* to describe an attack in words the system
description never uses. Narrowing it to the asset vocabulary fails too.

"A document written in the past tense about a process nobody ran" is the same
class of prose analysis and will fail the same way. What is mechanically
checkable is the *field*: whether it is present, and whether the debt list naming
its absence is honest. That is the half to build.
