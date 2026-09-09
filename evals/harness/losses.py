"""What lost each STRIDE reference a run missed: the verb, the critic, the place, or no lead.

The scorer says *which* references a run missed. This says *why*, from the
same rulings, so a fix carries its ceiling before anybody pays for a run: an
exemplar edit can recover only the misses the verb lost, a candidate rule only
the misses no rule led to. On 2026-09-09 an exemplar edit went to a $6 sweep
with no such number; read off the Baseline afterwards, its ceiling was five
must-finds of 129, inside the run-to-run band.

Five causes, decided in this order for one missed reference:

* ``verb``: a surviving claim in the reference's lane cites the same place —
  one endpoint-resolved element set contains the other, the identity rule's
  own element half — and names another action. The lane found the finding and
  wrote a different verb. The pair of verbs rides on the row, because which
  side is wrong is a decision this module does not make.
* ``merged``: a surviving claim cites the place with the reference's own
  verb, and the scorer assigned it to a sibling reference. Two references at
  one place under one action are the merges ``verbs.UNSEPARATED`` records,
  and the loss is the corpus's to rule on rather than the lane's.
* ``critic``: no surviving claim cites the place, but a draft the critic
  rejected did, with the reference's verb. The finding was written and killed.
* ``place``: a candidate rule led the lane to the reference's elements and no
  draft cites them. The lead did not take.
* ``unled``: no rule fired on the reference's elements and no draft cites
  them. Nothing sent the lane there.

Every fact read here is one the harness already holds in a closed form: the
scorer's element relation, the record's verb, the trigger instrument's rule
IDs. No prose is read into any number; the rationale strings the scorer prints
are for a person, and this module does not parse them.

Per reference rather than per draft, and only over misses: a matched reference
has no loss to charge, and an unlisted draft is the queue's to judge.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from analysis_service.frameworks.stride.record import DraftThreat
from evals.harness.identity import FlowMap, endpoint_subset
from evals.harness.reference import GoldenCase
from evals.harness.scorer import CaseScore
from evals.harness.triggers import case_trigger_recall

#: The causes a miss is charged to, in the order they are decided.
Cause = Literal["verb", "merged", "critic", "place", "unled"]
CAUSES: tuple[Cause, ...] = ("verb", "merged", "critic", "place", "unled")


@dataclass(frozen=True)
class Loss:
    """One missed reference and what lost it."""

    reference_index: int
    lane: str
    must_find: bool
    cause: Cause
    reference_verb: str
    #: The surviving claim at the reference's place, for a ``verb`` or a
    #: ``merged`` loss, or the rejected draft there for a ``critic`` one.
    #: Absent otherwise.
    draft_id: str | None = None
    draft_verb: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "reference_index": self.reference_index,
            "lane": self.lane,
            "must_find": self.must_find,
            "cause": self.cause,
            "reference_verb": self.reference_verb,
            "draft_id": self.draft_id,
            "draft_verb": self.draft_verb,
        }


@dataclass(frozen=True)
class CaseLosses:
    case: str
    losses: tuple[Loss, ...] = field(default_factory=tuple)

    @property
    def by_cause(self) -> dict[str, int]:
        counts = Counter(loss.cause for loss in self.losses)
        return {cause: counts[cause] for cause in CAUSES}

    def to_json(self) -> dict[str, Any]:
        return {
            "case": self.case,
            "by_cause": self.by_cause,
            "losses": [loss.to_json() for loss in self.losses],
        }


def _at_place(
    reference_ids: Sequence[str], drafts: Sequence[DraftThreat], flows: FlowMap
) -> list[DraftThreat]:
    """The drafts whose cited place is the reference's, by the identity rule's element half."""
    return [
        draft
        for draft in drafts
        if endpoint_subset(reference_ids, draft.affected_element_ids, flows)
    ]


def attribute_case(
    case: GoldenCase,
    score: CaseScore,
    drafts: Sequence[DraftThreat],
    produced: Sequence[DraftThreat],
    flows: FlowMap,
) -> CaseLosses:
    """Charge each of one case's misses to its cause.

    ``drafts`` are the pre-critic drafts and ``produced`` the report's claims,
    the two sides :mod:`evals.harness.critic_yield` already scores; a draft in
    the first and not the second is one the critic killed.
    """
    references = case.stride_claims()
    must_find = {
        index
        for index, reference in enumerate(references)
        if reference.tier == "must-find"
    }
    hits = case_trigger_recall(case, "stride").hits
    surviving = {claim.id for claim in produced}
    losses: list[Loss] = []
    for index in score.missed:
        reference = references[index]
        lane = reference.category
        verb = reference.verb
        if verb is None:
            # ``tests/test_verb_coverage.py`` holds every STRIDE reference to a
            # verb; a record without one is a corpus the lints did not run on.
            raise ValueError(f"{case.id}: reference {index} carries no verb")
        in_lane = [claim for claim in produced if claim.category == lane]
        at_place = _at_place(reference.affected_element_ids, in_lane, flows)
        if at_place:
            claim = at_place[0]
            losses.append(
                Loss(
                    index,
                    lane,
                    index in must_find,
                    "merged" if claim.verb == verb else "verb",
                    verb,
                    draft_id=claim.id,
                    draft_verb=claim.verb,
                )
            )
            continue
        killed = [
            draft
            for draft in drafts
            if draft.category == lane
            and draft.id not in surviving
            and draft.verb == verb
        ]
        killed_here = _at_place(reference.affected_element_ids, killed, flows)
        if killed_here:
            draft = killed_here[0]
            losses.append(
                Loss(
                    index,
                    lane,
                    index in must_find,
                    "critic",
                    verb,
                    draft_id=draft.id,
                    draft_verb=draft.verb,
                )
            )
            continue
        cause: Cause = "place" if hits[index].rule_ids else "unled"
        losses.append(Loss(index, lane, index in must_find, cause, verb))
    return CaseLosses(case=case.id, losses=tuple(losses))


def pooled(rows: Sequence[CaseLosses]) -> dict[str, Any]:
    """Losses per cause over the corpus, counted rather than averaged, and the verb pairs."""
    totals: Counter[str] = Counter()
    must_find: Counter[str] = Counter()
    pairs: Counter[tuple[str, str]] = Counter()
    pairs_must_find: Counter[tuple[str, str]] = Counter()
    for row in rows:
        for loss in row.losses:
            totals[loss.cause] += 1
            must_find[loss.cause] += loss.must_find
            if loss.cause == "verb" and loss.draft_verb is not None:
                pair = (loss.reference_verb, loss.draft_verb)
                pairs[pair] += 1
                pairs_must_find[pair] += loss.must_find
    return {
        "cases": len(rows),
        "losses": sum(totals.values()),
        "by_cause": {cause: totals[cause] for cause in CAUSES},
        "must_find_by_cause": {cause: must_find[cause] for cause in CAUSES},
        # Reference verb first, then what the lane wrote, most frequent first.
        "verb_pairs": [
            {
                "reference_verb": reference,
                "draft_verb": draft,
                "losses": count,
                "must_find": pairs_must_find[(reference, draft)],
            }
            for (reference, draft), count in sorted(
                pairs.items(), key=lambda item: (-item[1], item[0])
            )
        ],
    }


def render(rows: Sequence[CaseLosses]) -> None:
    """One line per case, one column per cause, then the pooled counts and the verb pairs."""
    if not rows:
        return
    print("\nSTRIDE loss attribution (what lost each missed reference)")
    print(f"{'case':<26} " + " ".join(f"{cause:>8}" for cause in CAUSES))
    for row in rows:
        counts = row.by_cause
        print(f"{row.case:<26} " + " ".join(f"{counts[cause]:>8}" for cause in CAUSES))
    totals = pooled(rows)
    print(
        f"pooled over {totals['cases']} cases: {totals['losses']} losses, "
        + ", ".join(
            f"{cause} {totals['by_cause'][cause]} (must-find"
            f" {totals['must_find_by_cause'][cause]})"
            for cause in CAUSES
        )
        + " (instrument, non-gating)"
    )
    for pair in totals["verb_pairs"][:8]:
        print(
            f"  verb: reference {pair['reference_verb']:<18} lane wrote"
            f" {pair['draft_verb']:<18} {pair['losses']:>3}  must-find {pair['must_find']}"
        )


def artifact(rows: Sequence[CaseLosses]) -> dict[str, Any]:
    return {
        "losses": [row.to_json() for row in rows],
        "losses_aggregate": pooled(rows) if rows else None,
    }
