"""What lost each STRIDE reference a run missed: the verb, the critic, the place, or no lead.

The scorer says *which* references a run missed. This says *why*, from the
same rulings, so a fix carries its ceiling before anybody pays for a run: an
exemplar edit is priced on the misses the verb lost, a candidate rule on the
misses no rule led to. The class is the expected recovery and not a causal
bound; a gain outside it is a signal to read, never noise, and the cause order
below records one observation per miss rather than every contributor. On
2026-09-09 an exemplar edit went to a $6 sweep
with no such number; read off the Baseline afterwards, its ceiling was five
must-finds of 129, inside the run-to-run band.

Five causes, decided in this order for one missed reference:

* ``verb``: a surviving claim in the reference's lane cites the same place —
  one endpoint-resolved element set contains the other, the identity rule's
  own element half — and names another action. The lane found the finding and
  wrote a different verb. The pair of verbs rides on the row, because which
  side is wrong is a decision this module does not make. So does
  :data:`Relation`, which says whether the two places are the same one or
  whether one nests inside the other: containment is how a draft answering
  another question lands on this cause, and a ceiling is priced on the equal
  rows.
* ``merged``: a surviving claim cites the place with the reference's own
  action, read through the equivalence table the identity rule reads, and the
  scorer assigned it to a sibling reference. Two references at
  one place under one action are the merges ``verbs.UNSEPARATED`` records,
  and the loss is the corpus's to rule on rather than the lane's.
* ``misfiled``: a surviving claim in another lane cites the place with the
  reference's action — the scorer's own lane error. The finding was written and
  filed under the wrong category, which is a routing fix and neither a lead nor
  a verb; charging it to ``place`` hid it behind the lane's silence.
* ``critic``: no surviving claim cites the place, but a draft the critic
  rejected did, with the reference's action. The finding was written and killed.
* ``place``: a candidate rule led the lane to the reference's elements and no
  draft cites them. The lead did not take.
* ``unled``: no rule fired on the reference's elements and no draft cites
  them. Nothing sent the lane there.

A ``place`` or ``unled`` row also says whether the lane wrote *near* the
reference: ``displaced_draft_id`` names a surviving claim in the lane that
shares at least one endpoint-resolved element with the reference without
either set containing the other. The second Baseline moved nine verb losses
into these two causes and nothing said whether the lane had gone silent or
had written the finding one element over; those are two different fixes, and
the row now tells them apart.

Every fact read here is one the harness already holds in a closed form: the
scorer's element relation, the identity rule's own answer to whether two verbs
name one action, the trigger instrument's rule IDs. Whether two verbs are one
action is read through :func:`~evals.harness.verbs.same_action` and never
compared as strings: the table is empty today, and a reader that compared
strings would agree with the rule until the day an entry lands, then charge a
``merged`` miss to the verb. No prose is read into any number; the rationale strings the scorer prints
are for a person, and this module does not parse them.

Per reference rather than per draft, and only over misses: a matched reference
has no loss to charge, and an unlisted draft is the queue's to judge.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from analysis_service.claims import FrameworkAnalysis
from analysis_service.frameworks.stride.record import DraftThreat
from evals.harness.identity import FlowMap, endpoint_form, endpoint_subset
from evals.harness.reference import GoldenCase
from evals.harness.scorer import CaseScore
from evals.harness.triggers import case_trigger_recall
from evals.harness.verbs import same_action

#: The causes a miss is charged to, in the order they are decided.
Cause = Literal["verb", "merged", "misfiled", "critic", "place", "unled"]
CAUSES: tuple[Cause, ...] = ("verb", "merged", "misfiled", "critic", "place", "unled")

#: How a named draft's endpoint-resolved place stands to the reference's. The
#: element half of the identity rule accepts containment either way, so a draft
#: citing one process sits "at the place" of any reference that resolves to a
#: set holding it — whatever the two claims say. On the third Baseline three of
#: the five ``abuse-grant`` against ``escalate`` rows were that, and each named
#: a draft answering a different question (#871).
#:
#: Nothing here judges whether two claims are one finding; a rule cannot, and
#: the model judge is retired. This says only how the two places relate, which
#: is already computed and was thrown away, so a reader can sort a verb reading
#: list by strength and a ceiling can be stated over the ``equal`` rows alone.
Relation = Literal["equal", "reference-contains", "draft-contains", "overlap"]
RELATIONS: tuple[Relation, ...] = (
    "equal",
    "reference-contains",
    "draft-contains",
    "overlap",
)


@dataclass(frozen=True)
class Loss:
    """One missed reference and what lost it."""

    reference_index: int
    lane: str
    must_find: bool
    cause: Cause
    reference_verb: str
    #: The surviving claim at the reference's place, for a ``verb``, a
    #: ``merged`` or a ``misfiled`` loss, or the rejected draft there for a
    #: ``critic`` one. Absent otherwise.
    draft_id: str | None = None
    draft_verb: str | None = None
    #: What the *first* critic pass got wrong on ``draft_id``, from
    #: :meth:`~analysis_service.claims.FrameworkAnalysis.re_ask_kinds`. Empty
    #: is the common case and means the ruling that lost this reference is the
    #: one the first pass wrote. Non-empty splits a ``critic`` charge in two: a
    #: kill the critic argued for is priced against its reasoning, and one that
    #: arrived after a repair is priced against the first pass and ``recritic``.
    #: A ``place`` or ``unled`` row names no draft, so it is always empty there.
    re_ask: tuple[str, ...] = ()
    #: How ``draft_id``'s place stands to the reference's, once both are
    #: endpoint-resolved: ``equal`` is the strongest reading, and a containment
    #: says the two claims are written at different grain — which is what lets
    #: a draft about something else share a place. ``None`` where the row names
    #: no draft, and where the two sets share nothing, which only a
    #: ``misfiled`` row can be: its draft comes from the scorer's lane error
    #: rather than from the containment test.
    place_relation: Relation | None = None
    #: For a ``place`` or ``unled`` row, the surviving claim in the lane whose
    #: cited place overlaps the reference's without containing it or being
    #: contained by it — the lane wrote the finding one element over. Absent
    #: where no surviving claim in the lane shares an element with the
    #: reference: the lane went silent there. Always absent on the other three
    #: causes, which already name the draft at the place.
    displaced_draft_id: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "reference_index": self.reference_index,
            "lane": self.lane,
            "must_find": self.must_find,
            "cause": self.cause,
            "reference_verb": self.reference_verb,
            "draft_id": self.draft_id,
            "draft_verb": self.draft_verb,
            "place_relation": self.place_relation,
            "re_ask": list(self.re_ask),
            "displaced_draft_id": self.displaced_draft_id,
        }


@dataclass(frozen=True)
class CaseLosses:
    case: str
    losses: tuple[Loss, ...] = field(default_factory=tuple)

    @property
    def by_cause(self) -> dict[str, int]:
        counts = Counter(loss.cause for loss in self.losses)
        return {cause: counts[cause] for cause in CAUSES}

    @property
    def re_asked_by_cause(self) -> dict[str, int]:
        """The subset of :attr:`by_cause` whose draft the first pass fumbled."""
        counts = Counter(loss.cause for loss in self.losses if loss.re_ask)
        return {cause: counts[cause] for cause in CAUSES}

    @property
    def displaced_by_cause(self) -> dict[str, int]:
        """The subset of the ``place`` and ``unled`` rows the lane wrote near."""
        counts = Counter(
            loss.cause for loss in self.losses if loss.displaced_draft_id is not None
        )
        return {cause: counts[cause] for cause in CAUSES}

    def to_json(self) -> dict[str, Any]:
        return {
            "case": self.case,
            "by_cause": self.by_cause,
            "re_asked_by_cause": self.re_asked_by_cause,
            "displaced_by_cause": self.displaced_by_cause,
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


def _relation(
    reference_ids: Sequence[str], draft_ids: Sequence[str], flows: FlowMap
) -> Relation | None:
    """How two cited places stand to each other, once endpoint-resolved.

    One reader for every row that names a draft, so the four causes cannot
    disagree about what "the same place" was. ``None`` where the two share no
    element: not a relation, and the row says so rather than picking one.
    """
    place = endpoint_form(reference_ids, flows)
    drafted = endpoint_form(draft_ids, flows)
    if place == drafted and place:
        return "equal"
    if place > drafted and drafted:
        return "reference-contains"
    if drafted > place and place:
        return "draft-contains"
    return "overlap" if place & drafted else None


def _nearby(
    reference_ids: Sequence[str], drafts: Sequence[DraftThreat], flows: FlowMap
) -> DraftThreat | None:
    """The first draft sharing an endpoint-resolved element with the reference.

    Read through :func:`~evals.harness.identity.endpoint_form`, the same
    resolution the element half of the identity rule applies, so a flow cited
    on one side and its endpoint on the other count as one shared place.
    Called only for drafts the containment test already refused, so a hit here
    is an overlap and never a match.
    """
    place = endpoint_form(reference_ids, flows)
    for draft in drafts:
        if place & endpoint_form(draft.affected_element_ids, flows):
            return draft
    return None


def attribute_case(
    case: GoldenCase,
    score: CaseScore,
    drafts: Sequence[DraftThreat],
    produced: Sequence[DraftThreat],
    flows: FlowMap,
    block: FrameworkAnalysis,
) -> CaseLosses:
    """Charge each of one case's misses to its cause.

    ``drafts`` are the pre-critic drafts and ``produced`` the report's claims,
    the two sides :mod:`evals.harness.critic_yield` already scores; a draft in
    the first and not the second is one the critic killed.

    ``block`` is the report's own STRIDE block, read for one fact the drafts do
    not carry: whether the ruling on the draft a row names came out of a
    bounded re-ask (#796). Asked through the block's own
    :meth:`~analysis_service.claims.FrameworkAnalysis.re_ask_kinds`, so this
    and the ASVS instrument cannot disagree about what a re-asked claim is.
    """
    references = case.stride_claims()
    must_find = {
        index
        for index, reference in enumerate(references)
        if reference.tier == "must-find"
    }
    hits = case_trigger_recall(case, "stride").hits
    surviving = {claim.id for claim in produced}
    # The scorer's own record of a finding filed in the wrong lane, keyed by
    # the reference it answers; read here rather than re-derived, so this and
    # ``lane_accuracy`` cannot disagree about which misses are misfiled.
    misfiled = {error.reference_index: error for error in score.lane_errors}
    by_id = {claim.id: claim for claim in produced}
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
                    "merged" if same_action(claim.verb, verb) else "verb",
                    verb,
                    draft_id=claim.id,
                    draft_verb=claim.verb,
                    place_relation=_relation(
                        reference.affected_element_ids,
                        claim.affected_element_ids,
                        flows,
                    ),
                    re_ask=block.re_ask_kinds(claim.id),
                )
            )
            continue
        if index in misfiled:
            error = misfiled[index]
            losses.append(
                Loss(
                    index,
                    lane,
                    index in must_find,
                    "misfiled",
                    verb,
                    draft_id=error.threat_id,
                    draft_verb=by_id[error.threat_id].verb,
                    place_relation=_relation(
                        reference.affected_element_ids,
                        by_id[error.threat_id].affected_element_ids,
                        flows,
                    ),
                    re_ask=block.re_ask_kinds(error.threat_id),
                )
            )
            continue
        killed = [
            draft
            for draft in drafts
            if draft.category == lane
            and draft.id not in surviving
            and same_action(draft.verb, verb)
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
                    place_relation=_relation(
                        reference.affected_element_ids,
                        draft.affected_element_ids,
                        flows,
                    ),
                    re_ask=block.re_ask_kinds(draft.id),
                )
            )
            continue
        cause: Cause = "place" if hits[index].rule_ids else "unled"
        nearby = _nearby(reference.affected_element_ids, in_lane, flows)
        losses.append(
            Loss(
                index,
                lane,
                index in must_find,
                cause,
                verb,
                displaced_draft_id=nearby.id if nearby is not None else None,
            )
        )
    return CaseLosses(case=case.id, losses=tuple(losses))


def pooled(rows: Sequence[CaseLosses]) -> dict[str, Any]:
    """Losses per cause over the corpus, counted rather than averaged, and the verb pairs."""
    totals: Counter[str] = Counter()
    must_find: Counter[str] = Counter()
    re_asked: Counter[str] = Counter()
    displaced: Counter[str] = Counter()
    kinds: Counter[str] = Counter()
    pairs: Counter[tuple[str, str]] = Counter()
    pairs_must_find: Counter[tuple[str, str]] = Counter()
    relations: Counter[str] = Counter()
    relations_must_find: Counter[str] = Counter()
    for row in rows:
        for loss in row.losses:
            totals[loss.cause] += 1
            must_find[loss.cause] += loss.must_find
            if loss.re_ask:
                re_asked[loss.cause] += 1
                kinds.update(loss.re_ask)
            if loss.displaced_draft_id is not None:
                displaced[loss.cause] += 1
            if loss.cause == "verb" and loss.draft_verb is not None:
                pair = (loss.reference_verb, loss.draft_verb)
                pairs[pair] += 1
                pairs_must_find[pair] += loss.must_find
                if loss.place_relation is not None:
                    relations[loss.place_relation] += 1
                    relations_must_find[loss.place_relation] += loss.must_find
    return {
        "cases": len(rows),
        "losses": sum(totals.values()),
        "by_cause": {cause: totals[cause] for cause in CAUSES},
        "must_find_by_cause": {cause: must_find[cause] for cause in CAUSES},
        # The subset of each cause's misses whose ruling came out of a re-ask,
        # and which problems the first pass had on those drafts. Counted per
        # miss and per kind rather than summed together: one draft carries more
        # than one kind, so the kinds do not add up to the misses.
        "re_asked_by_cause": {cause: re_asked[cause] for cause in CAUSES},
        "re_asked": sum(re_asked.values()),
        # The ``place`` and ``unled`` rows the lane wrote near rather than
        # skipped. Per cause, because the fix differs: a displaced ``place``
        # row is a lead the lane took to the wrong element, and a displaced
        # ``unled`` row is a finding the lane reached with no lead at all.
        "displaced_by_cause": {cause: displaced[cause] for cause in CAUSES},
        "displaced": sum(displaced.values()),
        "by_re_ask_kind": dict(sorted(kinds.items())),
        # The verb rows by how the two places relate. An ``equal`` row is the
        # strongest verb claim there is: one place, spelled the same way, two
        # actions. A containment says the two claims are written at different
        # grain, and that is where a draft answering another question lands.
        # Price an exemplar edit on the ``equal`` rows and read the rest.
        "verb_by_relation": {relation: relations[relation] for relation in RELATIONS},
        "verb_must_find_by_relation": {
            relation: relations_must_find[relation] for relation in RELATIONS
        },
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
    if totals["re_asked"]:
        print(
            f"  of those, {totals['re_asked']} lost a draft the first critic"
            " pass fumbled: "
            + ", ".join(
                f"{cause} {totals['re_asked_by_cause'][cause]}"
                for cause in CAUSES
                if totals["re_asked_by_cause"][cause]
            )
            + " — first-pass problems: "
            + ", ".join(
                f"{kind} {count}" for kind, count in totals["by_re_ask_kind"].items()
            )
        )
    if totals["displaced"]:
        silent = sum(totals["by_cause"][cause] for cause in ("place", "unled"))
        print(
            f"  of the place and unled rows, {totals['displaced']} have a"
            " surviving draft one element over ("
            + ", ".join(
                f"{cause} {totals['displaced_by_cause'][cause]}"
                for cause in ("place", "unled")
            )
            + f") and {silent - totals['displaced']} have none in the lane nearby"
        )
    if totals["by_cause"]["verb"]:
        print(
            "  of the verb rows, "
            + ", ".join(
                f"{totals['verb_by_relation'][relation]} {relation}"
                f" (must-find {totals['verb_must_find_by_relation'][relation]})"
                for relation in RELATIONS
            )
            + " — an equal place is the strongest verb claim, and a"
            " containment is where a draft about something else lands"
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
