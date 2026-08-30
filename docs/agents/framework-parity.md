# Framework parity

**Every change for one framework needs an explicit answer for every other
framework this repo carries.** A fix, an enhancement, an eval or a test written
for one **Framework Package** is not finished until the PR body says what it
means for each of the others.

The answer may be "nothing". That is a legitimate outcome and often the right
one. What is not legitimate is silence.

The set is `PACKAGES` in `src/analysis_service/frameworks/__init__.py`, whatever is
in it at the time you read this — two today, and
[#170](https://github.com/mstarks01/work-agent/issues/170) files LINDDUN as a
candidate. **Read this document as N frameworks, never as a pair.** Every rule
below is written that way on purpose: a note that said "the other framework"
would be wrong the day a third one lands, and would have to be rewritten by
whoever was least likely to notice.

## How the asymmetry happened, once, in detail

Worth reading before the rules, because the rules are derived from it rather than
from principle.

**The cutover stopped at the service boundary.**
[#172](https://github.com/mstarks01/work-agent/issues/172) made the service
framework-neutral: one extraction, one **Valid System Model**, N packages. It
worked — `src/` is clean today, and every `stride` in it is a module path.
Everything that *grades* the service was outside that scope and kept its
one-package shape. ASVS landed four days later
([#176](https://github.com/mstarks01/work-agent/issues/176)) and **nothing
failed**.

**A one-package assumption is vacuously correct when written and silently wrong
afterwards.** It does not raise; it reports a smaller, plausible number.
`EVAL_FRAMEWORKS = ("stride",)` was right when there was one package.
`aggregate_coverage` keyed by lane name was right while no two packages shared a
slug. `summarize()` pooling every framework was right with one — and once ASVS
records reached it, it returned 70%, which cleared STRIDE's floor while hiding
that ASVS sat at 32%. None of these broke on the day the second package arrived.
They kept answering about half the system, which is why an audit found them and a
test suite did not.

**Eight PRs to find them one at a time:**
[#214](https://github.com/mstarks01/work-agent/pull/214) (nothing scored 63 ASVS
records), [#215](https://github.com/mstarks01/work-agent/pull/215) (coverage,
grounds), [#221](https://github.com/mstarks01/work-agent/pull/221) (the knowledge
lint read one directory), [#222](https://github.com/mstarks01/work-agent/pull/222)
(stability, exemplar delta, promotion feed),
[#223](https://github.com/mstarks01/work-agent/pull/223) (trigger recall),
[#224](https://github.com/mstarks01/work-agent/pull/224) (the tuning guide),
[#225](https://github.com/mstarks01/work-agent/pull/225) (critic yield), and
[#210](https://github.com/mstarks01/work-agent/pull/210), which fixed a review
check *I had shipped an hour earlier* that let a sign-off naming one package's
reference set clear a case holding another's unread. The rule does not exempt
whoever is applying it.

### The one usable rule this gives

Sorting those fixes by what they touched separates two shapes cleanly:

| shape | what happened when ASVS landed |
|---|---|
| **a table keyed by framework** — `PACKAGES`, `SCHEMAS`, `REFERENCE_TYPES`, the five maps in `verify_corpus.py` | **already correct, no change needed.** A missing key raises `KeyError` at the first call, so the edit is forced. |
| **a constant or a branch naming one framework** — the eval framework list, `stride_block` call sites, the grounds fold, stability, the exemplar delta, trigger recall, the knowledge lint | **every one of them was a gap.** |

**So: prefer a table keyed by framework over a constant or a branch.** The table
is self-completing; the branch needs somebody to remember, and this document
exists because somebody did not.

`tests/test_framework_neutrality.py` enforces the decidable half: every framework
literal outside a package root is declared with the reason it is allowed, and a
new one fails until it is. Its `DECLARED` map is also the checklist to re-read
when a package lands — every entry reading *"this code is that framework's"* is a
dispatch a third package may need adding to.

**A literal is not the only way to name a framework, and two of the other ways
shipped.** A scan that reads `.py` files under `src/` and `evals/` and matches
the string `"stride"` is blind to both of these:

- **A framework's name inside a word.** A class, a function or a default value
  that serves every framework can still carry one framework's name, and none of
  those is a string literal. Public surface is where this shape survives
  longest, because renaming it is a larger change than the pull request that
  finds it.
- **Text a person reads.** `webapp/` went unsearched, so an app served a heading
  naming one framework over a form that offers every framework the install
  carries. A job naming ASVS alone got its answer under another framework's
  name.

The scan now covers `webapp/` too, and two further checks close those gaps. A
framework-named class, function or value outside a package must be in a
`DECLARED` file or in `OPEN_BY_DECISION`, which records a name somebody chose to
keep and why. A page may not name a framework at all: the name reaches a person
through the report, or through a table keyed by framework where the wording
genuinely differs. `webapp/review.py`'s `QUESTIONS` is that table — STRIDE rules
on whether an attack is credible and ASVS on whether a requirement applies, so
one heading could not ask both.

### The rule has a second axis

The shape above is about **which framework** a piece of code reads. The same
shape turned up on a second axis, and it is worth naming because the axis is not
obvious until somebody trips over it: **which measurement the eval sweep
reports.**

An **instrument** is one reading over a finished sweep — a per-case row, a fold
over those rows, a rendering, and the artifact keys it owns. Seven exist. Each
one already had all four parts, and nothing named the shape, so each was wired by
hand into four places in `evals/harness/run.py`. Adding ASVS's two instruments
cost six artifact keys and two renderers, written one at a time. Nothing raised;
a sweep missing an instrument simply printed one measurement fewer.

`evals/harness/instruments.py` holds that table now, and it carries the framework
axis inside it: every entry declares the packages whose record it reads, so a
sweep of one package skips another package's scorer rather than failing in it.
That declaration closed a one-package assumption the harness still carried —
the STRIDE scorer asked **every** case in a sweep for a STRIDE block, so a sweep
of a package producing no such block died inside a scorer that had nothing to say
about it. No corpus case declared one framework without STRIDE, so nothing caught
it: correct when written, silent afterwards, exactly the shape above.

**So the question to ask a new axis is the same one.** When a piece of machinery
grows an entry per framework, per measurement, per mode or per anything else,
prefer a table keyed by that thing. Then ask what forces the table to stay
complete, because a table nobody checks against the registry has the same silent
failure as the branch it replaced — an entry missing from
`evals.harness.instruments.INSTRUMENTS` would be a package measured by nothing at
all.

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
because ASVS matches by requirement ID with no composed identity — one requirement sits in
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
- A reference record nothing grades is a defect that survives until somebody
  reads it, and becomes a wrong number the day a matrix over it ships. The
  corpus's 63 ASVS records sat in exactly that state until
  [#214](https://github.com/mstarks01/work-agent/pull/214) scored them. **A
  package registered without an instrument that reads its record inherits the
  same silence**, which is why `test_every_package_has_an_instrument` exists.

## When a new package lands, the question runs backwards

Registering a package is a table edit and an import, so it is small to do and
wide in what it invalidates. Every framework-shaped decision already in the tree
needs re-asking against the newcomer, not only the change in front of you.

Start where the three defects above were found, because each is a place a
one-package assumption survived:

- **Fan-out arithmetic.** `config/resilience.toml`'s ceiling and every count in
  `docs/Configuration.md` are stated as one `strong` request per lane of every
  framework a job runs. A new lane count changes the product.
- **The eval sweep.** Mechanical now: `test_every_package_has_an_instrument`
  and `test_every_package_declares_a_scorer` both fail until the new package is
  named. What they cannot decide is whether the instrument it was attached to
  *fits* — an instrument that reads a category and two rated severity axes says
  nothing about a package whose claims carry a catalog identifier, so a package
  whose record no existing instrument can read needs its own.
- **The corpus.** Does every case that satisfies the new package's
  **Precondition** carry a reference set for it? The merge bar checks that every
  lane of every carried package has a `must-find` record somewhere, so a package
  with no records anywhere fails it — that one is mechanical.
- **Review coverage.** Already generalised: `required_files` in
  `evals/harness/sitting.py` derives from the case's own `frameworks`
  declaration, so a case that gains a reference set re-opens its own review with
  no edit here.
- **Certification.** A new package's lanes ride an existing tier, so they
  certify against fingerprints blessed before the package existed.
- **The justification vocabulary.** Can this package's claims always name
  something in the **System Model**? Every ground kind and every element-spelled
  `related_unknowns` entry points at a thing that exists — an element, one of its
  attributes, a flow, a quotable span. A package ruling on documents, coding
  practices or absent components has claims that are about none of those. Two
  defects came from exactly this, one at each surface: #410 and #412.
- **Every required field on a claim, one at a time.** For each, ask what this
  package will put there **when it has nothing true to say**. A field it can
  always satisfy honestly is fine. A field whose legal values all name something
  this package's claims are not about will be filled with whatever passes —
  `notes` on every question, an arbitrary verified quote on every ruling — and
  every check goes green while the output says nothing.

- **Which class the service asks for.** A package overriding a neutral hook has
  to put it on the class the *caller* reaches, not merely on one of its own.
  ASVS put `partition_proposals` on its analysis block while the fan-in asks its
  record; Python resolved the neutral default and two live runs deferred nothing
  while reading as a model that would not answer. This one *is* mechanical —
  `test_no_package_override_is_orphaned` — and the tests that missed it did so
  by calling the override on the same wrong class. **A test that names a class
  the caller never reaches proves the method works, not that it runs.**

The second is the general form of the first, and neither is decidable in
advance. `evals/harness/filler.py` measures the symptom *after* a run, which is
the best that can be done: the defect passes every offline check by
construction, because the suite scripts the agents. So these two are questions
to ask a new package, not tests that will ask them for you.

## It runs every way, not just outward from STRIDE

An ASVS-first change owes STRIDE and every other package the same question, and
a LINDDUN-first change will owe both of them. ASVS's closed catalog and its 17
chapters make some work natural there and awkward for an open claim set;
LINDDUN's unit of analysis is the interaction rather than the element (#170), so
work that assumes per-element lanes owes it an answer too. "The others get this
for free" is a claim to check, never one to assume.

## Where this is enforced

Seven mechanical instances, each for a narrow question:

- **`tests/test_framework_neutrality.py`** — every framework literal outside a
  package root is declared with a reason, so a new one fails until somebody says
  why it is not a table. This is the check derived from the root cause above.
- **The identifier and page checks in the same module** — a framework's name
  inside a word is declared or recorded as open, and no text an app puts in
  front of a person names a framework. These cover the two ways of naming one
  that carry no literal.
- **`test_no_package_override_is_orphaned`** — a package that overrides a
  neutral hook is reached through the class the *caller* asks for. The hooks
  reached through `package.record` and the two reached through the analysis
  block are listed in `NEUTRAL_HOOKS`; an
  override on any other class of the package resolves to the neutral default,
  silently, and the package's intent is dropped with no error.
- **`evals/harness/filler.py`** — whether a package's required justifications
  say anything, over a finished sweep. It reports rather than gates, because the
  honest threshold is unknown for every reading but one: a question pointed at
  an attribute the evidence catalog refuses should be zero, and is 0 of 378
  across the archived STRIDE sweeps.
- **The registry checks in the same module** — every carried package is named by
  an instrument and declares a per-case scorer, and neither table names a package
  this build does not carry. These close the second axis: a table stays complete
  only while something compares it to `PACKAGES`.
- **`tests/test_case_review.py`** — a step 6 sign-off is checked against every
  framework the case declares, so a review that read one package's reference set
  leaves the case on the unreviewed list.
- **The merge bar in `verify_corpus.py`** — every lane of every carried package
  has a `must-find` record somewhere in the corpus.

Everything else in this document is a rule for the PR body, because the general
form is not mechanically checkable: no test can tell that a *number* was computed
over one framework when the code reading it was already neutral.
