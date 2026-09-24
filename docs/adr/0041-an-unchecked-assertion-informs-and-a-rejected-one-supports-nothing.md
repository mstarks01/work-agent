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
`run.py guard-cost` pairs every archived proposal with every archived
extracted graph of its case, 1,500 pairs, the pairing `run.py bind` makes. It
projects each catalog twice, with the guard on and off. 602 of the pairs are
over a graph today's gate refuses. No job projects onto such a graph, so the
guard costs nothing there, although it fires 78 times on them. Over the other
898 pairs:

- **44 values are held back**: 38 exposures and 6 zone placements, and the
  blessed model agrees with each one. Each reaches the lane as a cited,
  unchecked row beside an open question instead of as the attribute. A zone
  left `unknown` also leaves its crossings undecided (ADR 0039): decided
  crossings fall from 1,114 to 1,102.
- **18 are corrections.** Extraction read a flow's authentication as `none`,
  an unchecked row stated a mechanism, and the blessed model reads `unknown`.
  The guard writes the qualified `unknown`. Without the guard, the projected
  model fails the gate, every projection of the pair is dropped, and `none`
  stands.
- **Candidates:** 47 are lost, all from three STRIDE rules that read a stated
  exposure or zone, and 31 are gained. One must-find loses every candidate
  that led to it: in case 09, a customer sends the web API the catalogue
  operations, in 11 pairs. The match is by lane and a shared element, so this
  is an upper bound, and the lane still reads the exposure lead and the cited
  row.

A ceiling of one must-find in one case is inside the corpus spread of 3.37
must-finds (`evals/TUNING.md`), so this cost gets no paid run: a sweep could
not tell it from the spread between runs.

That is the contract working as decided: a correct unchecked fact is shown
with its uncertainty rather than settled. If the cost to placement is judged
too high, the remedy is a review, not an exception in the rule.

**The facts-first route takes no exception either.** Its resolver writes no
zone: every component enters at `unknown`, and a placement fact is a catalog
row that the same projection reads. So an unchecked placement leaves the zone
open and is cited to the lane, on both routes. Against the archive this moves
no figure: `run.py replay`, `run.py oracle` and `run.py bottleneck` give the
same output before and after the change.

**A reviewed report is recomputed, not rewritten.** A report checks every
ground on load, so the reassessment is a record beside the report. A finding
the rejected row suppressed was never written, and only a re-analysis of the
reopened leads can recover it.
