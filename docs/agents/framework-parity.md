# Framework parity

**Every change for one framework needs an explicit answer for every other
framework this repo carries.** A fix, an enhancement, an eval or a test written
for one **Framework Package** is not finished until the PR body says what it
means for each of the others.

The answer may be "nothing". That is a legitimate outcome and often the right
one. What is not legitimate is silence.

The set is `PACKAGES` in `src/stride_service/frameworks/__init__.py`, whatever is
in it at the time you read this — two today, and
[#170](https://github.com/mstarks01/work-agent/issues/170) files LINDDUN as a
candidate. **Read this document as N frameworks, never as a pair.** Every rule
below is written that way on purpose: a note that said "the other framework"
would be wrong the day a third one lands, and would have to be rewritten by
whoever was least likely to notice.

## Why this rule exists

The repo name, the service name, the package root and most of the history all
say STRIDE, so STRIDE is the default the eye falls on. Any package that lands
later inherits none of that gravity — ASVS did not
([#176](https://github.com/mstarks01/work-agent/issues/176), 2026-08-14), and a
third will not either.

Three defects in the tree came from exactly that, and none of them was a
decision:

- [#199](https://github.com/mstarks01/work-agent/issues/199) — the concurrency
  ceiling comment sized the barrier fan-out against STRIDE's 6 lanes. ASVS
  declares 17, so a two-framework job is 23 concurrent `strong` requests and the
  documented arithmetic was wrong by a factor of four. **A third package moves
  that number again.**
- [#200](https://github.com/mstarks01/work-agent/issues/200) — the eval sweep
  grades the STRIDE block and raises if it is absent. The corpus's 63 ASVS
  records are shape-checked by `verify_corpus.py` and then read by nothing.
- PR #209 — a step 6 review check asked only whether a sign-off block existed,
  so a `read` list naming one package's reference set would have cleared a case
  holding another's unread. Fixed in #210 by deriving the requirement from the
  case's own declaration.

Notice the shape all three share: **a number, a code path or a check that was
correct when one package existed, and silently wrong afterwards.**

## The answers, and how to write each

Give one per other package, not one for the change.

**"It applies to all of them."** Do them in the same change, or say why the rest
is a follow-up and file it. A test, a lint or a doc correction is almost always
this: shared machinery serves every package, so a check that reads one package's
output is usually a check that should read every package's output.

**"It applies to some, by design."** State the design reason and cite where it
was settled. Real example: claim-identity deduplication
([#201](https://github.com/mstarks01/work-agent/issues/201)) is STRIDE's alone,
because ASVS matches by requirement ID with no judge — one requirement sits in
one chapter, which is one **Lane**, which is one **Lane Agent**, so two ASVS
claims about one requirement cannot arise.
[#167](https://github.com/mstarks01/work-agent/issues/167) settled that.

Write the reason as a **property of the framework**, never as its name. "ASVS is
exempt" tells the next reader nothing about LINDDUN. "A framework whose claims
carry a catalog identifier needs no equivalence judgement" answers for every
framework that ever ships, including ones nobody has thought of. #201's exemption
holds for the second form and not the first.

**"It applies to all of them and I am doing one."** Name every missing half and
file them. #200 is this answer, written down after the fact.

## Where the asymmetry bites hardest

A less-exercised package makes a gap quieter, not smaller:

- One `strong` fingerprint covers every framework in a job, so a package nobody
  graded still **certifies**. Certification is a claim about a deployment's
  blessed list and never a claim about quality, and this is the seam where the
  two read alike.
- A STRIDE reference claim gets exercised by a sweep eventually. An ASVS
  reference record is read by nothing until #200 lands, so a defect in one
  survives until somebody reads it — and becomes a wrong number the day the
  applicability matrix ships. **Any package added before #200 lands inherits
  that same silence.**

## When a new package lands, the question runs backwards

Registering a package is a table edit and an import, so it is small to do and
wide in what it invalidates. Every framework-shaped decision already in the tree
needs re-asking against the newcomer, not only the change in front of you.

Start where the three defects above were found, because each is a place a
one-package assumption survived:

- **Fan-out arithmetic.** `config/resilience.toml`'s ceiling and every count in
  `docs/Configuration.md` are stated as one `strong` request per lane of every
  framework a job runs. A new lane count changes the product.
- **The eval sweep.** Does anything grade the new package's block, or does
  `run.py` still pick one framework's block off the report?
- **The corpus.** Does every case that satisfies the new package's
  **Precondition** carry a reference set for it? The merge bar checks that every
  lane of every carried package has a `must-find` record somewhere, so a package
  with no records anywhere fails it — that one is mechanical.
- **Review coverage.** Already generalised: `required_reading` in
  `tests/test_case_review.py` derives from `case.meta.frameworks`, so a case that
  gains a reference set re-opens its own review with no edit here.
- **Certification.** A new package's lanes ride an existing tier, so they
  certify against fingerprints blessed before the package existed.

## It runs every way, not just outward from STRIDE

An ASVS-first change owes STRIDE and every other package the same question, and
a LINDDUN-first change will owe both of them. ASVS's closed catalog and its 17
chapters make some work natural there and awkward for an open claim set;
LINDDUN's unit of analysis is the interaction rather than the element (#170), so
work that assumes per-element lanes owes it an answer too. "The others get this
for free" is a claim to check, never one to assume.

## Where this is enforced

`tests/test_case_review.py` is the one mechanical instance, and it is written
N-ary: a step 6 sign-off is checked against every framework the case declares, so
a review that read one package's reference set leaves the case in debt. The merge
bar in `verify_corpus.py` is the other, for a narrower question — every lane of
every carried package has a `must-find` record somewhere.

Everything else in this document is a rule for the PR body, because the general
form is not mechanically checkable.
