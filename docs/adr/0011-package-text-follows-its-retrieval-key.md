# 11. A document's retrieval key decides whether it lives in a package or the service

- **Status**: accepted
- **Date**: 2026-08-13
- **Effort**: [#169 — the retrieval key decides where knowledge
  lives](https://github.com/mstarks01/work-agent/issues/169), part of map
  [#158](https://github.com/mstarks01/work-agent/issues/158)
- **Amends**: [ADR 0008](0008-retrieval-by-fired-rule.md) by addition. ADR
  0008 is not edited in place — it records a decision taken on 2026-08-11
  under different premises, and nothing here changes what it decided.
- **Relates to**: [ADR 0004](0004-evidence-references.md) /
  [ADR 0010](0010-package-cannot-extend-the-evidence-catalog.md), whose rule
  that a package selects rather than extends applies here too; and
  [#164](https://github.com/mstarks01/work-agent/issues/164), which made
  `rules` a `FrameworkPackage` member.

## Context

ADR 0008 built retrieval as a set intersection against fired **Candidate**
rules: a **Reference Note** or **Worked Case** is selected by which rules a
lane's own analysis fired, ranked and capped, with a **Domain Pack**
retrieved separately by the **Valid System Model**'s `technology`,
`protocol`, `authentication` and `name` fields. That design predates map
#158's decision that STRIDE becomes the first of several framework packages.
With a second package on the way, three artifacts that used to live in one
shared `knowledge/` and `skills/domains/` need a decided home: does a
document move into the package it's read for, or stay a shared service
root?

## Decision

**The retrieval key decides the home.** A document selected by a package's
own fired rules moves into that package; a document selected by reading the
neutral Valid System Model stays the service's, in one shared root.

| Artifact | Home | Retrieval key |
| --- | --- | --- |
| Reference Note | the package — `frameworks/<name>/notes/<id>.md` | the package's own fired **Candidate** rules (ADR 0008's mechanism, unchanged) |
| Worked Case | the package — `frameworks/<name>/cases/<id>.md` | the same fired rules |
| Domain Pack | the service — one shared root, `domains/<name>.md` | the Valid System Model's `technology`, `protocol`, `authentication`, `name` |

`rules` was already a `FrameworkPackage` member (#164); a document only a
package's own rules can select is a service-owned file with no service-side
caller, which is reason enough on its own. A Worked Case additionally
carries a framework's judgement in its substance, not just its selection —
its ruling opens by naming which lane a finding belongs in — so it belongs
in the package on its content as well as its key. A Domain Pack reads only
fields one neutral extraction fills for every framework (#162), so its key
stays neutral by construction and it stays shared.

**A shared Domain Pack may state a technology's facts, failure modes and
questions to ask. It may never name a lane, a category, a requirement ID,
or a verdict state.** Three of today's four packs end in a "Lane
discipline" line naming STRIDE categories; those lines are deleted, because
each already duplicates a `## Scope` section STRIDE's own lane skills
carry. A lint enforces the rule going forward: a shared pack may not contain
any registered package's declared lane name.

**`knowledge` becomes a ninth `FrameworkPackage` member**, holding both the
note table and the case table, required on every package — a package that
ships no corpus writes two empty tables rather than omitting the member,
matching #164's rule that no package field carries a default.

**The standing rule survives untouched for all three artifacts.** None is a
fact about the system under review, none enters the Evidence Catalog, and
nothing may cite one as a `Ground` — ADR 0004's and ADR 0010's boundary
holds regardless of which root a document lives under.

## Consequences

**Sixteen documents move byte-identical, path only.** Ten Reference Notes
and six Worked Cases move from `knowledge/` to `frameworks/stride/{notes,cases}/`
with no content edit — a moved file with an edited body would make the diff
unreviewable against the prior tree. `skills/stride/` already moved under
#164's ruling, so `knowledge/` and `skills/` both end empty; the text roots
drop to three (`domains/`, `prompts/`, the new `frameworks/`), `skills/`
renames to `domains/`, `ANALYSIS_KNOWLEDGE_DIR` is deleted, and
`ANALYSIS_SKILLS_DIR` becomes `ANALYSIS_DOMAINS_DIR`. `CONTEXT.md`'s
**Deployment** entry gets its third edit across this cutover: five config
files, three text roots.

**The gate gains two checks**, on top of #164's ten: every document a
package's `knowledge` member names must exist under that package's text
root, and every rule ID it names must exist in that package's own `rules`.
Same family as #164's check 7 (a lane with no prompt) — a missing note
fails the gate before any model call, not a CI lint over a tree a
deployment may not even be running.

**The quietly-broken evidence-seam test is fixed ahead of the file
moves it would have silently stopped covering.** `test_no_document_is_reachable_from_the_evidence_seam`
in `tests/test_knowledge.py` globbed `src/analysis_service/*.py` flat; once a
package's own modules sit under `src/analysis_service/frameworks/<name>/`,
that glob would stop reaching them and the assertion would keep passing
while checking nothing. It now globs recursively, keyed by path rather than
bare filename, and a second, direct assertion states the property without
depending on the allowlist's shape: `evidence.py` and `critic.py` import
nothing from `analysis_service.knowledge`, checked by name rather than by
what a glob happens to enumerate.

**Per-job corpus cost is now paid per (framework, lane), unmeasured.** ADR
0008 argued its caps against six lanes at roughly 700 tokens per document.
Two frameworks double the worst case; no live sweep has run against this
repository's corpus, so this is a stated cost rather than an observed one.

**What was considered and rejected: a shared `knowledge/` root with a
package-declared selection table**, the shape ADR 0010 gives the Evidence
Catalog. Rejected on the same two grounds a Domain Pack's neutrality avoids:
the service would own documents no service-side caller can select, and most
of today's Reference Notes still end in a STRIDE-specific lane line that
reads as false once an unrelated package's agent is the reader.

**Also rejected: splitting each note into a neutral body and a
per-framework tail.** It buys back some sharing at the cost of a document
format two packages both edit — the coupling this cutover exists to remove.
The accepted cost is smaller than it looks: a Reference Note's "what to
look for" questions are already written for one framework's reading of the
condition, so a second package re-authoring its own version loses little
that was genuinely shared.
