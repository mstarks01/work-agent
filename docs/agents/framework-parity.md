# Framework parity

**Every change for one framework needs an explicit answer for the others.** This
repo carries two **Framework Package**s, STRIDE and ASVS. A fix, an enhancement,
an eval or a test written for one is not finished until you have said, in the PR
body, what it means for the other.

The answer may be "nothing". That is a legitimate outcome and often the right
one. What is not legitimate is silence.

## Why this rule exists

The name of the repo, the service, the package root and most of the history all
say STRIDE, so STRIDE is the default the eye falls on. ASVS landed later
([#176](https://github.com/mstarks01/work-agent/issues/176), 2026-08-14) and
inherits none of that gravity. Two consequences already exist in the tree:

- [#199](https://github.com/mstarks01/work-agent/issues/199) — the concurrency
  ceiling comment sized the barrier fan-out against STRIDE's 6 lanes. ASVS
  declares 17, so a two-framework job is 23 concurrent `strong` requests and the
  documented arithmetic was wrong by a factor of four.
- [#200](https://github.com/mstarks01/work-agent/issues/200) — the eval sweep
  grades the STRIDE block and raises if it is absent. The corpus's 63 ASVS
  records are shape-checked by `verify_corpus.py` and then read by nothing.

Neither was a decision. Both were STRIDE-shaped work that nobody asked the
second question about.

## The three answers, and how to write each

**"It applies to both."** Do both in the same change, or say why the second is
a follow-up and file it. A test, a lint or a doc correction is almost always
this: shared machinery serves every package, so a check that reads one
package's output is usually a check that should read every package's output.

**"It applies to one, by design."** State the design reason and cite where it
was settled. Real example: claim-identity deduplication
([#201](https://github.com/mstarks01/work-agent/issues/201)) is STRIDE's alone,
because ASVS matches by requirement ID with no judge — one requirement sits in
one chapter, which is one **Lane**, which is one **Lane Agent**, so two ASVS
claims about one requirement cannot arise.
[#167](https://github.com/mstarks01/work-agent/issues/167) settled that. A
reader must be able to tell this apart from an oversight without re-deriving it.

**"It applies to both and I am doing one."** Say which half is missing and file
the other. #200 is this answer written down after the fact.

## Where the asymmetry bites hardest

ASVS is the *less* exercised package, so a gap there is quieter, not smaller:

- One `strong` fingerprint covers both frameworks, so an ASVS run **certifies**
  today on a tier nobody graded. Certification is a claim about a deployment's
  blessed list and never a claim about quality, and this is the seam where the
  two read alike.
- A STRIDE reference claim gets exercised by a sweep eventually. An ASVS
  reference record is read by nothing until #200 lands, so a defect in one
  survives until somebody reads it — and becomes a wrong number the day the
  applicability matrix ships.

## It runs both ways

An ASVS-first change owes STRIDE the same question. ASVS's closed catalog and
its 17 chapters make some work natural there and awkward for an open claim set,
and "STRIDE gets this for free" is an answer that has to be checked rather than
assumed.

## Where this is enforced

`tests/test_case_review.py` is the one mechanical instance: a step 6 sign-off is
checked against every framework the case declares, so a review that read one
package's reference set leaves the case in debt. Everything else in this document
is a rule for the PR body, because the general form is not mechanically
checkable.
