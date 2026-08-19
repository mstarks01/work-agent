# CLAUDE.md

Project instructions for the STRIDE threat-modeling service.

## Agent skills

### Issue tracker

Issues live as GitHub issues on `mstarks01/work-agent`, driven through the `gh` CLI;
wayfinder maps use native sub-issues and issue dependencies. Completed local-markdown
maps under `.wayfinder/` are archived history, not live. See `docs/agents/issue-tracker.md`.

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

### Provenance

A fact about how an artifact was made belongs in a **field the code reads**, never a
sentence in a guide: `bootstrap` on `case.json` stayed true for a year, while the same
file's prose about a reviewer drifted the moment nobody was one. When a design names a
role, ship the field and its debt list before the artifact. Write guides in the
imperative, never the past tense. See `docs/agents/provenance.md`.

### Claim identity

A **Claim**'s identity is a value code computes from its fields — framework,
lane, endpoint-resolved **Element** IDs, and an action verb from a closed set —
never from its prose. It is **versioned**, and a vote stores its components
rather than its hash, so improving the rule re-keys the whole ledger by
recomputation and costs no re-vote. That is what a judge change cannot offer.
A human vote is the only ground truth here, and its reason code decides whether
it moves an analysis number or a writing one. See `docs/agents/claim-identity.md`.

### Domain docs

Single-context: one `CONTEXT.md` glossary at the repo root, ADRs in `docs/adr/`.
See `docs/agents/domain.md`.
