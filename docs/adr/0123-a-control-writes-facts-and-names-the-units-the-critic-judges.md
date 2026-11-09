# 123. A Control writes facts and names the units the critic judges

- **Status**: accepted
- **Date**: 2026-11-03
- **Effort**: [#2112](https://github.com/mstarks01/work-agent/issues/2112),
  [#2113](https://github.com/mstarks01/work-agent/issues/2113) and
  [#2114](https://github.com/mstarks01/work-agent/issues/2114), on the Controls
  map [#2111](https://github.com/mstarks01/work-agent/issues/2111)
- **Relates to**: [ADR 0010](0010-package-cannot-extend-the-evidence-catalog.md),
  which keeps the Evidence Catalog neutral;
  [ADR 0013](0013-asvs-rules-applicability-and-never-a-pass.md), which rules
  that no state carries a pass;
  [ADR 0039](0039-a-crossing-a-model-cannot-decide-is-still-a-lead.md), whose
  rule 1 says that a match nobody stated is a placement nobody stated
- **Amended by**: [ADR 0124](0124-a-control-s-scope-matches-by-element-id-and-the-owner-confirms-it.md)
  and [ADR 0125](0125-a-control-fills-only-an-open-fact.md), on when the owner
  confirms a match; [ADR 0126](0126-the-critic-judges-each-control-row-in-a-control-step.md),
  on the excerpt ground

## Context

An **Owner** knows protections that no description states on each analysis: a
firewall at the edge, SSO on the API, a rate limit on a route. Today the owner
answers the same question on each job, or the lanes raise the same finding on
each report. The owner also wants to say "this covers ASVS V2.1" once, and
wants the service to refuse that statement where it does not hold.

The service holds no "mitigated" state, by design (ADR 0013). A finding is a
claim with grounds, and the critic is the one judge of a draft. The ASVS record
reads a class set `config`, `code`, `people` in `needs_evidence`, and the #484
research note names a second job input beside Sources for repository evidence.

## Decision

**A Control acts in two places, and never hides a finding on a word.** Before
the lanes, it writes facts through the one writer `answered_model`, as an
answer does. On the findings, it names framework units, and the critic judges
each such row against each draft. The service holds no mechanical row check,
because a second reader of one rule would refuse a process control, such as a
pentest, on shape alone.

**The neutral part is a list of answers in the `FactAnswer` shape.** The scope
supplies the element, so an answer in a Control carries no element ID. A
Control can write a protective control attribute or a Question Kind row about
its part, and only in the answer that makes it a protection: the exposure
`internal`, a known encryption at rest, or the answer that each Question Kind
or facet names in `protects`. The service refuses an answer that states a
weakness, such as "the internet can reach it", or that states nothing, such as
"unknown" or "none". It writes no Capability and no attribute
that describes the system, because those say what the application does, not
what protects it, and the description states them. The
neutral part names no framework, so every Framework Package reads it through
the Evidence Catalog, a future one too.

**A row names one unit from a package's closed set, in a table keyed by
framework.** A framework whose claims carry a catalog identifier offers every
grain its catalog publishes: ASVS offers its 17 chapters, 80 sections and 345
requirements. A framework whose claims carry no catalog identifier offers its
lanes and nothing finer: STRIDE offers six units. Two contract members carry
the rule, `units` and `control_unit_of`. The package gate checks each unit's
lane against `lanes` and each "inside" link against the set at import. The
submit check refuses a framework key outside `PACKAGES` and a unit ID outside
the package's set, and a replay over the Baseline reports under
`evals/baselines/` and each ASVS reference record of the corpus fails when
`control_unit_of` returns an ID outside the set. A row stores the package
version beside the unit ID. A row whose version differs from the carried
package's version is shown unticked at submission, because a published catalog
renumbers its set between releases.

**A row names no element.** The Control's scope supplies the element, the
zone or the whole application, so a row cannot disagree with the scope. One
Control carries many rows over one scope. An owner who needs two lanes on two
elements writes two Controls.

**A Control's document is evidence beside Sources, never a Source.** The
extraction never reads a configuration file as a system description. Each
document carries a class from the closed set `config`, `code`, `people`. The
slot is the one the #484 note named, so a later code scan is a second producer
into the same slot. The service copies an excerpt from a document by location
into a ground; no model writes the excerpt. A document settles nothing on its
own while `CARRIED_EVIDENCE_KINDS` stays `("prose",)`; a row un-defers its unit
(ADR 0126).

> **Amended by [ADR 0126](0126-the-critic-judges-each-control-row-in-a-control-step.md).**
> No ground holds an excerpt. The critic sees each document's service-copied
> excerpt in a view of the draft, and its verdict carries a reason with no
> ground; the report quotes each evidence line that the reason quotes.

**A Control binds to one of three shapes**: the whole application, one zone by
name or one element by name. The service matches the name at submission, and
the owner confirms the match (ADR 0124). A prose match is a lead for a
suggested Control and never the binding.

> **Amended by [ADR 0124](0124-a-control-s-scope-matches-by-element-id-and-the-owner-confirms-it.md)
> and [ADR 0125](0125-a-control-fills-only-an-open-fact.md).** The service
> matches the name against the extracted model, at the pause. The owner
> confirms the match where the questions are on. Where they are off, the job
> pauses and continues at once, and each Control takes its default
> resolution with no check by the owner. `docs/Web-App.md` holds a table of
> the current behaviour.

**The term is Control.** A "control attribute" is a fact on an element, which
a Control may write. The two words name different things, and `GLOSSARY.md`
draws the line.

## Considered options

- **An `exclude` effect that hides a unit.** Rejected: the report cannot then
  say why a unit is absent, and it reads as the pass that ADR 0013 refuses.
- **A mechanical row check before the critic.** Rejected: two readers of one
  rule, and a shape check refuses a process control.
- **Automatic application on a name match.** Rejected: a match nobody saw is
  a placement nobody stated (ADR 0039, rule 1).
- **Requirements only as ASVS units.** Rejected: precise, but twelve rows to
  say "we do password security".
- **A lane on one element as a STRIDE unit.** Rejected: a second element name
  can disagree with the scope.
- **A unit table outside the packages.** Rejected: a unit is what the
  framework judges, and a contract member forces the edit at import.
- **No version on a row.** Rejected: a renumbered ID that still exists passes
  with a new meaning.
- **A `prose` document class.** Rejected: `prose` means more of the same input,
  and that input is a Source, which the Owner File path carries.

## Consequences

`FrameworkPackage` gains two members, and `tests/test_future_frameworks.py`
drives a third package through both. The ASVS unit labels are the standard's
own text under CC BY-SA 4.0, so the page renders them at run time from
`catalog.json` and no label is copied into an ungoverned file. A future
framework declares `units`, or an empty set, and the page offers no rows for an
empty set.
