# CLAUDE.md

Project instructions for the security-analysis service.

## Agent skills

### Issue tracker

Issues live as GitHub issues on `mstarks01/work-agent`, driven through the `gh` CLI;
wayfinder maps use native sub-issues and issue dependencies. Completed local-markdown
maps under `.wayfinder/` are archived history, not live. See `docs/agents/issue-tracker.md`.

### Code review checkpoints

Review runs at two scales. A **pre-merge review** reads one pull request's diff
and asks whether the change is correct; a **checkpoint round** reads a range of
merged commits and asks what the tree holds that no single diff showed. Run both:
a defect in a recent fix is the dominant class and sits inside one diff, and the
rest needs the whole tree.

**A fix is the riskiest code in the tree.** Across the audit rounds, most findings in
a round came from the previous round's fixes, and every one of those passed the tests
that shipped with it. So read your own fix diff against the three causes above, prefer
one shared reader over a guard copied into a second, and make the harness that proved
the defect the regression test.

A finished checkpoint round ends in an annotated `reviewed/<date>` tag on the commit
it covered. Start the next one from `git tag -l 'reviewed/*' --sort=-creatordate
| head -1` rather than asking for a fixed point. The tag message carries what the
diff cannot: which axes ran, where each finding was fixed, and **what was left
open by decision** — read that before reporting a finding, so a settled question
is not re-raised as a new one. See `docs/agents/code-review.md`.

### Triage labels

The five canonical roles, each label string equal to its name: `needs-triage`, `needs-info`,
`ready-for-agent`, `ready-for-human`, `wontfix`. The `wayfinder:*` and GitHub stock labels are
orthogonal to these. See `docs/agents/triage-labels.md`.

### Framework parity

Any fix, enhancement, eval or test for one **Framework Package** needs an explicit answer
in the PR body for **every other package in `PACKAGES`** — "nothing changes, because a
framework whose claims carry a catalog identifier needs no equivalence judgement" is a
fine answer; silence is not. State the reason as a property of the framework, never as its
name, so it answers for packages nobody has written yet. It runs every way, not outward
from STRIDE.

**Prefer a table keyed by framework over a constant or a branch.** Every gap ASVS exposed
was a name or an `if`; every table was already correct, because a missing key raises.
The rule generalises past frameworks: the eval sweep grew one entry per *measurement* and
paid the same tax until `evals/harness/instruments.py` made that a table too. When
machinery grows an entry per anything, key it — then check the table against its registry,
because a table nobody compares to `PACKAGES` fails as quietly as the branch it replaced.
`tests/test_framework_neutrality.py` holds the decidable half of both. See
`docs/agents/framework-parity.md` for the post-mortem this is derived from.

### One rule, one reader

When two pieces of code answer the same question, they will eventually answer it
differently, and the disagreement is invisible because each one's test agrees
with it. **Give a rule one reader and let every other site call it.**

Where a second reader is unavoidable — an app and an offline gate, a harness
check and a corpus lint — test the two **against each other**, never each
against its own expectation.

Six instances in two audits, and every one survived because the readers were
tested separately: what an UNREVIEWED key is (substring vs `ast`); whether a
finding is answered (`queue.build` vs `Session.remaining`); which UNREVIEWED
table is the table (first assignment vs last); which version keys a ledger row
(`__post_init__` vs `rekey` vs `VERSION_FOR`); what a filled reading document is
(two copies of one line); when an element ID is checked (the rule and the
deriver disagreed about the empty-slug case).

Two corollaries, both from the same audits:

- **A self-sized fence is safe only while its neighbours are fenced too.** Ask
  what sits beside the value, not only what wraps it.
- **A bound that predicts a cost from its inputs is wrong whenever the cost
  turns on which inputs survive a filter.** Spend a budget where the work
  happens.

### Name the shapes before you read the value

New code that reads a value fails on the shape its author never listed.
`unfence` split on `"\n"` and missed U+2028, U+2029 and U+0085, so a payload
carrying one round-tripped corrupted. The roster note called `.get` on an entry
TOML does not require to be a table, and `ada = "contributor"` — the line a
first-timer writes — raised `AttributeError` through a whole preflight.

**Write down every shape the value can take, then handle each one.** The
question is what the *producer* can emit, not what it usually emits: `str` has
more line terminators than `"\n"`, `tomllib` returns a scalar where you expect a
table, and a model emits a name that slugs to empty. Ask the parser's
documentation rather than the sample input.

Two audits, three defects, and each one a shape that was legal all along.

### Provenance

A fact about how an artifact was made belongs in a **field the code reads**, never a
sentence in a guide: `bootstrap` on `case.json` stayed true for a year, while the same
file's prose about a reviewer drifted the moment nobody was one. When a design names a
role, ship the field and the list of what nobody has done before the artifact. Write guides in the
imperative, never the past tense. See `docs/agents/provenance.md`.

### Claim identity

A **Claim**'s identity is a value code computes from its fields — framework,
lane, endpoint-resolved **Element** IDs, and an action verb from the closed set
in `analysis_service.actions` — never from its prose. Which rule keys a package is
a **table**, `VERSION_FOR`, not a default: an open claim set composes an identity
from an action and a place, and a claim naming a catalog requirement composes
one from that requirement and the place it was ruled in. It is **versioned**, and a vote stores its components rather than its hash,
so improving the rule re-keys the whole ledger by recomputation and costs no
re-vote. There is **no model judge**: the rule decides every match, and a
human vote is the only ground truth on whether an unmatched finding is real —
its reason code decides whether it moves an analysis number or a writing one.
See `docs/agents/claim-identity.md`.

### Licensing

Apache-2.0 covers the code. It does not cover the **ASVS** package's text:
`catalog.json` and the 17 lane skill files reproduce ASVS 5.0.0, which OWASP
publishes under CC BY-SA 4.0, so those 18 files carry ShareAlike.

**Never copy a sentence out of a governed file into a file that is not governed.**
Write the point in your own words. A requirement sentence reads like ordinary
prompt text, which is exactly why this is easy to do and invisible in review;
`tests/test_license_lints.py` fingerprints the upstream words and finds them
whatever formatting they arrive in. Citing a standard by identifier carries no
obligation — a short identifier is not the expression it points at.

A package that quotes a published standard inherits that standard's licence, so
it needs a `CONTENT_LICENSE` entry, a `THIRD_PARTY` entry and a `NOTICE`
section. A corpus case converted from somebody else's model records the source
**and the licence** in `provenance`. See `docs/agents/licensing.md`.

### Domain docs

Single-context: one `CONTEXT.md` glossary at the repo root, ADRs in `docs/adr/`.
See `docs/agents/domain.md`.
