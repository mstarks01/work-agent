# Recording how an artifact was made

**A fact about how an artifact was produced belongs in a field the code reads,
never in a sentence in a guide.** A field with no value is countable; a sentence
is only as true as the last person who read it.

A required field, such as `bootstrap` on `CaseMetadata`, stays true because
nobody can add a case without answering it. A paragraph that describes what
*should* happen stays true only while somebody remembers it. A claim retracted
in one paragraph stays alive in every noun that assumes it, so only a grep for
the *vocabulary* finds those. [#206](https://github.com/mstarks01/work-agent/pull/206)
records the case behind this rule.

## The rule

**When a design names a role — reviewer, SME, operator, approver — ship the field
and the list of what nobody has done before the artifact.** Then "nobody has done this" is a value
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

A list of what nobody has done. Five lists use this pattern:

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
means *the omission is correct*, while `UNREVIEWED` means *nobody has read this
yet*. A list that blurred the two would excuse the gap it exists to count.

## There is no lint here, and there cannot be

Do not add one. A check for the closely related defect — a reference claim
asserting a fact its own model does not hold — **fires on 231 of 243 claims**,
because a claim is *supposed* to describe an attack in words the system
description never uses. Narrowing it to the asset vocabulary fails too.

"A document written in the past tense about a process nobody ran" is the same
class of prose analysis and will fail the same way. What is mechanically
checkable is the *field*: whether it is present, and whether the list naming
its absence is honest. That is the half to build.
