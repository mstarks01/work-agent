# 41. An unchecked assertion informs, and a rejected one supports nothing

- **Status**: accepted
- **Date**: 2026-09-23
- **Effort**: [#926](https://github.com/mstarks01/work-agent/issues/926), the
  owner's decision on the support gate: option (iii), with corrections
- **Amends**: [ADR 0034](0034-an-assertion-is-a-scoped-fact-with-a-support-span.md)
  (projection version 5) and
  [ADR 0036](0036-a-settled-assertion-is-evidence-a-lane-may-cite.md) (the
  gloss of a cited row)
- **Evidence**: `run.py falsify`'s production-path table, and
  `tests/test_release_blockers.py`

## Context

No deterministic reader can tell a well-quoted wrong fact from a true one.
Driven through analysis preparation, six of #925's corruptions reached a lane
with nobody reviewing them. Four of those also silenced a lead the job would
otherwise have offered: a copied control closed the webhook's open question,
and a signature or destination check answered with a connection control closed
the flow's authentication question. A rule requiring zero wrong facts in an
unreviewed job could never pass. A rule requiring nothing let the audit's
defect through.

## Decision

**Two guarantees, one per review state. Each is about what the job does with
a row's review state, not about whether the row is true, so no truth detector
is needed to keep either.**

1. **Unreviewed: an unchecked assertion informs conditional analysis and
   never closes a lead.** Where an attribute reads as a lead (never stated, or
   stated absent), a projection from rows nobody marked `supported` writes a
   qualified `unknown` naming the value, never the control. The lead stays in
   the Evidence Catalog, and the row is cited as itself, glossed `unchecked`.
   A stated absence over an unknown still writes, because an absence is itself
   a lead.
2. **Reviewed: no reviewer-rejected fact reaches analysis.** A row marked
   `unsupported` or `unresolved` is in neither the evidence nor any attribute.
   After a review, `reassess` recomputes the projection and the evidence
   through the same reader preparation calls. It withdraws every finding whose
   grounds no longer resolve and names every lead the review reopened for
   re-analysis. The promise is **not "no wrong fact"**, because a review can
   miss one.
3. **The critical falsification probes are release blockers**, tested through
   `prepare_analysis` with every framework package selected, not through a
   validator's warning. The unreviewed guarantee fails the four probes above
   with the rule switched off.
4. **The report discloses its review state** in the envelope's disclaimer:
   how many assertions were checked, rejected and unchecked. Disclosure is
   required and is not sufficient.
5. **Promotion needs all of it**: both guarantees (`critical-fixtures`),
   reviewed semantic accuracy over a population frozen before review, required
   fact recall, and the report-quality gates.

## Consequences

**An unchecked fact can no longer turn a question into a control, and that
has a measured price.** Against the 103 archived catalogs on their blessed
models, no projection falls in this case. Against extracted graphs it does.
The measurement pairs every archived proposal with every archived extracted
graph of its case, 1,500 pairs, the pairing `run.py bind` makes. Of 3,705
stated projections, 140 now write a qualified `unknown`:

- **51 are corrections.** The extracted graph read a flow's authentication as
  `none`, an unchecked row stated a mechanism, and the blessed model reads
  `unknown`.
- **89 withhold a value the blessed model agrees with**: 63 exposures and 26
  zone placements. Each reaches the lane as a cited, unchecked row beside an
  open question instead of as the attribute. A zone left `unknown` also leaves
  its crossings undecided (ADR 0039), so those are leads rather than derived
  facts until a reviewer marks the row `supported`.

That is the contract working as decided: a correct unchecked fact is shown
with its uncertainty rather than settled. If the cost to placement is judged
too high, the remedy is a review, not an exception in the rule.

**A reviewed report is recomputed, not rewritten.** A report checks every
ground on load, so the reassessment is a record beside the report. A finding
the rejected row suppressed was never written, and only a re-analysis of the
reopened leads can recover it.
