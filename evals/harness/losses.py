"""What lost each STRIDE reference a run missed, charged to one of nine causes.

The scorer says *which* references a run missed. This says *why*, from the
same rulings, so a fix carries its ceiling before anybody pays for a run: an
exemplar edit is priced on the misses the verb lost, a candidate rule on the
misses no rule led to. The class is the expected recovery and not a causal
bound; a gain outside it is a signal to read, never noise, and the cause order
below records one observation per miss rather than every contributor. Read the
ceiling before a paid sweep. On the first Baseline, one exemplar edit had a
ceiling of five must-finds of 129, inside the run-to-run band.

Nine causes. For one missed reference, each stage below is asked in this
order, and the first that finds the finding charges it; ``position`` and
``unstated`` are the two causes a stage charges in place of its own:

* ``verb``: a surviving claim in the reference's lane cites the same place —
  one endpoint-resolved element set contains the other, the identity rule's
  own element half — and names another action. The lane found the finding and
  wrote a different verb. The pair of verbs rides on the row, because which
  side is wrong is a decision this module does not make. So does
  :data:`Relation`, which says whether the two places are the same one or
  whether one nests inside the other: containment is how a draft answering
  another question lands on this cause, and a ceiling is priced on the equal
  rows.
* ``position``: claims cite the place with a reading's action, and a
  person ruled the reference an attacker position that none of them states.
  The lane wrote the action against another attacker, which the scorer's
  position gate refuses. Each later stage reads the same rule: a draft that
  left the report and a proposal the fan-in lost answer the reference only where
  they state its ruled position, so a stage that finds the action at the place
  and only other positions charges ``position``, not itself.
* ``unstated``: as ``position``, except that none of those items states any
  position. The archive holds them with no ``attacker_position``, so it
  cannot read the position rule there, and the row is not a lane's
  choice of attacker.
* ``merged``: a surviving claim cites the place with a reading's action,
  read through the equivalence table the identity rule reads, and the scorer
  assigned it to a sibling reference. Two references at one place under one
  action are the merges ``verbs.UNSEPARATED`` records,
  and the loss is the corpus's to rule on rather than the lane's.
* ``misfiled``: a surviving claim in another lane cites the place with the
  reference's action — the scorer's own lane error. The finding was written and
  filed under the wrong category, which is a routing fix and neither a lead nor
  a verb; charging it to ``place`` hid it behind the lane's silence.
* ``critic``: no surviving claim cites the place, but a draft the critic
  rejected did, with a reading's action. The finding was written and killed.
* ``fan-in``: no draft cites the place, but the lane *proposed* a claim there
  with a reading's action. The finding was written and lost before the
  critic saw it, in one of two ways, which :data:`FanInEffect` names. The fan-in
  **removed** the proposal — a reference outside the catalog, a quote the
  source does not carry, a claim left citing nothing — or it kept a draft and
  **narrowed** its elements off the reference's place, which is #441's rule
  that a claim cites only what its grounds reach. The row names the proposal's
  title, the draft where one was kept, and the reasons the block's own marks
  recorded. Read off the lanes' archived proposals, which every finished case
  carries. A case whose lanes proposed nothing has no ``fan-in`` row, and
  :attr:`CaseLosses.proposed_nothing` says so.
* ``place``: a candidate rule led the lane to the reference's elements and no
  draft cites them. The lead did not take. A rule leads when today's rule
  code fires it on the reference's elements of the run's own model, and the
  run's archived ``fired_rules`` lists it
  (:func:`~evals.harness.triggers.archived_lead_rules`), so a rule added after
  the run leads nowhere in it.
* ``unled``: no rule fired on the reference's elements and no draft cites
  them. Nothing sent the lane there.

A ``place`` or ``unled`` row also says whether the lane wrote *near* the
reference: ``displaced_draft_id`` names a surviving claim in the lane that
shares at least one endpoint-resolved element with the reference without
either set containing the other. The row tells a silent lane apart from a
lane that wrote the finding one element over, because those are two
different fixes.

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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Generic, Literal, TypeVar, get_args

from analysis_service.candidates import CandidateSet
from analysis_service.claims import ASSERTION_GROUNDS, DroppedClaim, FrameworkAnalysis
from analysis_service.frameworks.stride.record import DraftThreat
from analysis_service.parsing import strings
from evals.harness.identity import CaseFlows, endpoint_form, endpoint_subset
from evals.harness.reference import GoldenCase
from evals.harness.scorer import CaseScore, VoteStatus, holds_position, none_stated
from evals.harness.triggers import archived_lead_rules
from evals.harness.verbs import same_action

#: The causes a miss is charged to, in the order of the stages that decide
#: them. ``position`` and ``unstated`` can come from the surviving-claim,
#: ``critic`` or ``fan-in`` stage.
Cause = Literal[
    "verb",
    "position",
    "unstated",
    "merged",
    "misfiled",
    "critic",
    "fan-in",
    "place",
    "unled",
]
#: The causes the scorer's position gate gives a stage whose items all fail
#: the ruled position.
PositionRefusal = Literal["position", "unstated"]

#: The cause that says an archive holds no position to read.
UNREAD_CAUSE: PositionRefusal = "unstated"

#: The causes that name no draft and no proposal at the reference's place:
#: the lane was led there and wrote nothing, or nothing led it there.
SILENT: frozenset[Cause] = frozenset({"place", "unled"})

CAUSES: tuple[Cause, ...] = (
    "verb",
    "position",
    "unstated",
    "merged",
    "misfiled",
    "critic",
    "fan-in",
    "place",
    "unled",
)

#: What the fan-in did to a proposal a ``fan-in`` row names. ``removed``: no
#: draft carries the proposal's title and action. ``narrowed``: a draft does,
#: and the fan-in cut the elements that put it at the reference's place.
FanInEffect = Literal["removed", "narrowed"]

#: How a named draft's endpoint-resolved place stands to the reference's. The
#: element half of the identity rule accepts containment either way, so a draft
#: citing one process sits "at the place" of any reference that resolves to a
#: set holding it — whatever the two claims say. On the third Baseline three of
#: the five ``abuse-grant`` against ``escalate`` rows were that, and each named
#: a draft answering a different question (#871).
#:
#: Nothing here judges whether two claims are one finding; a rule cannot, and
#: the model judge is retired. This says only how the two places relate, which
#: the scorer computes and this keeps, so a reader can sort a verb reading
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
    #: The vote ledger's answer on ``displaced_draft_id``, read from the
    #: scorer's own :attr:`~evals.harness.scorer.CaseScore.unlisted` by draft
    #: ID rather than keyed again here: the scorer already asked the ledger for
    #: every unmatched claim, and a second lookup would be a second reader of
    #: which version keys a claim. ``None`` where the row names no displaced
    #: draft, and where that draft matched a reference of its own, so nobody
    #: was ever asked about it.
    #:
    #: This is what separates two defects a ``place`` row otherwise spells the
    #: same way. ``pooled`` means a person read the neighbouring claim and
    #: accepted it, so the lane found something real and wrote it one element
    #: over. ``rejected`` means the lane wrote something a person refused.
    displaced_status: VoteStatus | None = None
    #: For a ``fan-in`` row: the title of the lane's proposal at the place,
    #: what the fan-in did to it, and why, read from the block's own marks —
    #: the ``dropped_claims`` reason for a removal, and one entry per element
    #: cut from a narrowed draft, from ``unresolved_references``. Empty where
    #: the fan-in left no mark, which is itself worth reading.
    proposal_title: str | None = None
    fan_in_effect: FanInEffect | None = None
    fan_in_reasons: tuple[str, ...] = ()

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
            "displaced_standing": self.displaced_status,
            "proposal_title": self.proposal_title,
            "fan_in_effect": self.fan_in_effect,
            "fan_in_reasons": list(self.fan_in_reasons),
        }


@dataclass(frozen=True)
class AssertionBacking:
    """How much of one case's *find* rested on the **Assertion** catalog.

    The crossing the 2026-09-23 audit could not make. Every row of
    :class:`CaseLosses` is a reference nothing found; this is the other side —
    the references a catalog row did find — and without it the table has misses
    and no control.

    It is computed here rather than beside the grounds instrument for a reason
    the ``score`` path makes concrete: grounds are a fact about a run and are
    never recomputed on a re-score, so a crossing published there would read
    empty on the only path this repository can run for free. The loss pass has
    the claims and the score in hand, and is recomputed every time.

    ``resting`` counts claims rather than grounds, because one claim citing two
    rows is one finding the layer contributed to. Which kinds count is
    :data:`~analysis_service.claims.ASSERTION_GROUNDS`, read rather than spelled
    here, so a kind added to that set reaches this number too.
    """

    #: Claims carrying at least one assertion ground.
    resting: int = 0
    #: Of those, the ones the scorer assigned to a reference.
    matched: int = 0
    #: Of those matches, the ones against a must-find reference.
    must_find: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "resting": self.resting,
            "matched": self.matched,
            "must_find": self.must_find,
        }


def _backing(score: CaseScore, produced: Sequence[DraftThreat]) -> AssertionBacking:
    """One case's crossing, from the claims the report kept and the score's pairs."""
    resting = {
        claim.id
        for claim in produced
        if any(ground.kind in ASSERTION_GROUNDS for ground in claim.grounds)
    }
    pairs = [pair for pair in score.matched if pair.threat_id in resting]
    return AssertionBacking(
        resting=len(resting),
        matched=len(pairs),
        must_find=sum(1 for pair in pairs if pair.must_find),
    )


@dataclass(frozen=True)
class CaseLosses:
    case: str
    losses: tuple[Loss, ...] = field(default_factory=tuple)
    #: What the assertion layer found in this case, beside what was lost. Zero
    #: on every deployment that ran no assertion node, and that *is* the
    #: reading: the layer backed nothing.
    assertion_backed: AssertionBacking = field(default_factory=AssertionBacking)
    #: Whether the archived proposals hold no STRIDE lane batch. ``True``
    #: means the lanes proposed nothing, so a miss here is never a ``fan-in``
    #: row: the lanes wrote no proposal for the fan-in to remove.
    proposed_nothing: bool = False

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
            "proposed_nothing": self.proposed_nothing,
            "by_cause": self.by_cause,
            "re_asked_by_cause": self.re_asked_by_cause,
            "displaced_by_cause": self.displaced_by_cause,
            "assertion_backed": self.assertion_backed.to_json(),
            "losses": [loss.to_json() for loss in self.losses],
        }


def _at_place(
    reference_ids: Sequence[str], drafts: Sequence[DraftThreat], flows: CaseFlows
) -> list[DraftThreat]:
    """The drafts whose cited place is the reference's, by the identity rule's element half."""
    return [
        draft
        for draft in drafts
        if endpoint_subset(reference_ids, draft.affected_element_ids, flows)
    ]


_Item = TypeVar("_Item")


def _drafted_at(
    drafts: Sequence[DraftThreat],
    flows: CaseFlows,
    verb: str,
    reference_ids: Sequence[str],
) -> list[DraftThreat]:
    """The drafts at one reading's place under that reading's action."""
    return [
        draft
        for draft in _at_place(reference_ids, drafts, flows)
        if same_action(draft.verb, verb)
    ]


def _draft_position(draft: DraftThreat) -> str | None:
    return draft.attacker_position


def _proposal_position(proposal: Mapping[str, Any]) -> object:
    return proposal.get("attacker_position")


def _holding(
    position: str | None,
    items: Sequence[_Item],
    position_of: Callable[[_Item], object],
) -> list[_Item]:
    """The items that state the reference's ruled position, or all of them.

    A reference with no ruled position takes any position, so every item holds.
    Where a person ruled one, only an item that states it can answer the
    reference, because the scorer's gate refuses every other: an empty result
    is a ``position`` or ``unstated`` loss (see :func:`_refused`) at whichever
    stage found the action at the place.
    """
    return [item for item in items if holds_position(position, position_of(item))]


def _refused(
    position: str | None,
    items: Sequence[_Item],
    position_of: Callable[[_Item], object],
) -> PositionRefusal:
    """The cause of a stage whose items all fail the reference's ruled position.

    :data:`UNREAD_CAUSE` where :func:`~evals.harness.scorer.none_stated` reads
    no item as stating any position, because then the archive cannot read the
    rule; ``position`` where one states another.
    """
    if none_stated(position, (position_of(item) for item in items)):
        return UNREAD_CAUSE
    return "position"


def _proposed_at(
    lane_batch: object,
    flows: CaseFlows,
    verb: str,
    reference_ids: Sequence[str],
) -> list[Mapping[str, Any]]:
    """The lane's proposals at one reading's place under that reading's action.

    A proposal is the lane's output before any check, so every shape it can
    take is read: a batch that is not an object, a ``claims`` entry that is not
    a list, a claim missing its elements or its verb. Each of those is a
    proposal the fan-in removed for its shape and cannot be at a place, so it
    is skipped rather than raised on.
    """
    claims = lane_batch.get("claims") if isinstance(lane_batch, dict) else None
    if not isinstance(claims, list):
        return []
    return [
        claim
        for claim in claims
        if isinstance(claim, dict)
        and isinstance(claim.get("verb"), str)
        and strings(claim.get("affected_element_ids"))
        and same_action(claim["verb"], verb)
        and endpoint_subset(reference_ids, claim["affected_element_ids"], flows)
    ]


@dataclass(frozen=True)
class _AnsweredReading(Generic[_Item]):
    """One reading of a reference, and the items at its place that carry its action."""

    read_verb: str
    place: Sequence[str]
    #: The items at the reading's place under the reading's action.
    with_action: list[_Item]
    #: Of those, the items the position gate takes: the ones that state the
    #: reference's ruled position, or all of them where none is ruled.
    held: list[_Item]
    #: ``None`` where an item holds the position; otherwise the cause the
    #: scorer's position gate gives.
    refused: PositionRefusal | None

    @property
    def charged_items(self) -> list[_Item]:
        """The items a miss is charged among: the holding ones, if any."""
        return self.held or self.with_action


def _answered_reading(
    readings: Sequence[tuple[str, Sequence[str]]],
    answers: Callable[[str, Sequence[str]], list[_Item]],
    position: str | None,
    position_of: Callable[[_Item], object],
) -> _AnsweredReading[_Item] | None:
    """The reading one stage charges a miss through, or ``None``.

    **The one reader of which reading a loss stage charges.** The
    surviving-claim, critic and fan-in stages of :func:`attribute_case` charge
    through this choice wherever an item carries a reading's action; only the
    surviving-claim stage's ``verb`` charge, where none does, reads the places
    alone. ``answers`` gives the stage's items at a
    reading's place under that reading's action, so a reading with no such
    item is skipped: a reference answered by an item under its own action is
    the stage's cause, and a ``verb`` charge beside it names the wrong fix.
    A reading whose items state the ruled position wins over one whose items
    do not, because the scorer's gate takes only those, so a ``position``
    charge beside one names the wrong fix too. ``min`` keeps the first of
    equal readings, so the reference's own reading wins a tie.
    """
    answered = []
    for read_verb, place in readings:
        items = answers(read_verb, place)
        if not items:
            continue
        held = _holding(position, items, position_of)
        refused = None if held else _refused(position, items, position_of)
        answered.append(_AnsweredReading(read_verb, place, items, held, refused))
    return min(answered, key=lambda reading: not reading.held, default=None)


def _charged_to(claims: Sequence[DraftThreat]) -> DraftThreat:
    """Which of several drafts a loss row names.

    **The rule decides, never the input order.** Each caller passes drafts in
    whatever order the report lists them, so the lowest draft ID decides: it
    is arbitrary but stable, and the row a reader sees does not move between
    two runs over one report.
    """
    return min(claims, key=lambda claim: claim.id)


def relation(
    reference_ids: Sequence[str], cited_ids: Sequence[str], flows: CaseFlows
) -> Relation | None:
    """How a place cited by a draft or a candidate stands to the reference's.

    Both places are endpoint-resolved first. One reader for every row that
    names a draft, so the four causes cannot disagree about what "the same
    place" was, and for :mod:`evals.harness.delivery`, which reads a
    candidate's place the same way. In each :data:`Relation`, the "draft" side
    is ``cited_ids``. ``None`` where the two share no element: not a relation,
    and the row says so rather than picking one.
    """
    place = endpoint_form(reference_ids, flows)
    cited = endpoint_form(cited_ids, flows)
    if place == cited and place:
        return "equal"
    if place > cited and cited:
        return "reference-contains"
    if cited > place and place:
        return "draft-contains"
    return "overlap" if place & cited else None


def _nearby(
    reference_ids: Sequence[str], drafts: Sequence[DraftThreat], flows: CaseFlows
) -> DraftThreat | None:
    """The draft sharing an endpoint-resolved element with the reference.

    Read through :func:`~evals.harness.identity.endpoint_form`, the same
    resolution the element half of the identity rule applies, so a flow cited
    on one side and its endpoint on the other count as one shared place.
    Called only for drafts the containment test already refused, so a hit here
    is an overlap and never a match.

    Lowest ID among the drafts that overlap, for the reason
    :func:`_charged_to` gives: this names a draft in the artifact, and which
    one it names must not depend on the order the report listed them in.
    """
    place = endpoint_form(reference_ids, flows)
    overlapping = [
        draft
        for draft in drafts
        if place & endpoint_form(draft.affected_element_ids, flows)
    ]
    return _charged_to(overlapping) if overlapping else None


def attribute_case(
    case: GoldenCase,
    score: CaseScore,
    drafts: Sequence[DraftThreat],
    produced: Sequence[DraftThreat],
    flows: CaseFlows,
    block: FrameworkAnalysis,
    *,
    proposed: Mapping[str, Any],
    candidates: Mapping[str, CandidateSet],
) -> CaseLosses:
    """Charge each of one case's misses to its cause.

    ``proposed`` is the lanes' archived STRIDE proposals, keyed by lane. An
    empty mapping means that the lanes proposed nothing.

    ``candidates`` is each lane's candidates as today's rules write them for
    the run's own model, from
    :func:`~evals.harness.triggers.rebuilt_candidates`. A lane it lacks raises
    ``KeyError``: every STRIDE lane has an entry, so a missing one is a defect.
    Beside the block's ``fired_rules``, they decide ``place`` against
    ``unled``.

    ``drafts`` are the pre-critic drafts and ``produced`` the report's claims,
    the two sides :mod:`evals.harness.critic_yield` already scores; a draft in
    the first and not the second left the report, with a verdict or not, and is
    charged to the critic.

    ``block`` is the report's own STRIDE block, read for one fact the drafts do
    not carry: whether the ruling on the draft a row names came out of a
    bounded re-ask (#796). Asked through the block's own
    :meth:`~analysis_service.claims.FrameworkAnalysis.re_ask_kinds`, so this
    and the ASVS instrument cannot disagree about what a re-asked claim is.
    """
    references = case.stride_claims()
    must_find = {
        index for index, reference in enumerate(references) if reference.must_find
    }
    surviving = {claim.id for claim in produced}
    # The scorer's own record of a finding filed in the wrong lane, keyed by
    # the reference it answers; read here rather than re-derived, so this and
    # ``lane_accuracy`` cannot disagree about which misses are misfiled.
    misfiled = {error.reference_index: error for error in score.lane_errors}
    by_id = {claim.id: claim for claim in produced}
    # The ledger's answer per unmatched claim, as the scorer recorded it. Read
    # rather than keyed again, for the reason ``misfiled`` above gives: the
    # scorer applied the fingerprint rule, and a claim the scorer matched to a
    # reference of its own is absent here because nobody was asked about it.
    statuses = {threat.threat_id: threat.status for threat in score.unlisted}
    # Keyed by the title as the drop mark cut it, through the mark's own cut,
    # so a long proposal title finds its mark.
    dropped = {mark.title: mark.reason for mark in block.dropped_claims}
    cut_from: dict[str, list[str]] = {}
    for mark in block.unresolved_references:
        cut_from.setdefault(mark.claim_id, []).append(
            f"{mark.element_id}: {mark.reason}"
        )
    losses: list[Loss] = []
    for index in score.missed:
        reference = references[index]
        lane = reference.category
        position = case.ruled_positions.get(index)
        verb = reference.verb
        if verb is None:
            # ``tests/test_verb_coverage.py`` holds every STRIDE reference to a
            # verb; a record without one is a corpus the lints did not run on.
            raise ValueError(f"{case.id}: reference {index} carries no verb")
        # Every reading the scorer accepts, its own first (``stride_readings``),
        # so a ruled reading at another place charges the stage that lost it.
        readings = [
            (read_verb or verb, place)
            for read_verb, place in case.stride_readings(index)
        ]
        in_lane = [claim for claim in produced if claim.category == lane]
        answered = _answered_reading(
            readings,
            partial(_drafted_at, in_lane, flows),
            position,
            _draft_position,
        )
        charge: tuple[Cause, Sequence[str], DraftThreat] | None
        if answered is not None:
            charge = (
                answered.refused or "merged",
                answered.place,
                _charged_to(answered.charged_items),
            )
        else:
            # With no claim under a reading's action, the first reading with
            # any claim at its place charges the verb.
            charge = next(
                (
                    ("verb", place, _charged_to(here))
                    for _, place in readings
                    if (here := _at_place(place, in_lane, flows))
                ),
                None,
            )
        if charge is not None:
            charged, place, claim = charge
            losses.append(
                Loss(
                    index,
                    lane,
                    index in must_find,
                    charged,
                    verb,
                    draft_id=claim.id,
                    draft_verb=claim.verb,
                    place_relation=relation(place, claim.affected_element_ids, flows),
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
                    place_relation=relation(
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
            if draft.category == lane and draft.id not in surviving
        ]
        killed_answer = _answered_reading(
            readings,
            partial(_drafted_at, killed, flows),
            position,
            _draft_position,
        )
        if killed_answer is not None:
            draft = _charged_to(killed_answer.charged_items)
            losses.append(
                Loss(
                    index,
                    lane,
                    index in must_find,
                    killed_answer.refused or "critic",
                    verb,
                    draft_id=draft.id,
                    draft_verb=draft.verb,
                    place_relation=relation(
                        killed_answer.place, draft.affected_element_ids, flows
                    ),
                    re_ask=block.re_ask_kinds(draft.id),
                )
            )
            continue
        batch = proposed.get(lane)
        offered = _answered_reading(
            readings,
            partial(_proposed_at, batch, flows),
            position,
            _proposal_position,
        )
        if offered is not None:
            read_verb, place = offered.read_verb, offered.place
            proposal = min(
                offered.charged_items,
                key=lambda claim: str(claim.get("title", "")),
            )
            title = str(proposal.get("title", ""))
            # The draft the fan-in made of this proposal, if it kept one: the
            # fan-in keeps a proposal's title and action, and assigns the ID.
            kept = [
                draft
                for draft in drafts
                if draft.category == lane
                and draft.title == title
                and same_action(draft.verb, read_verb)
            ]
            kept_draft = _charged_to(kept) if kept else None
            if kept_draft is not None:
                effect: FanInEffect = "narrowed"
                reasons = tuple(cut_from.get(kept_draft.id, ()))
            else:
                effect = "removed"
                cut = DroppedClaim.of(claim_id="-", title=title, reason="-").title
                reasons = (dropped[cut],) if cut in dropped else ()
            losses.append(
                Loss(
                    index,
                    lane,
                    index in must_find,
                    offered.refused or "fan-in",
                    verb,
                    draft_id=kept_draft.id if kept_draft is not None else None,
                    draft_verb=proposal["verb"],
                    place_relation=relation(
                        place, proposal["affected_element_ids"], flows
                    ),
                    proposal_title=title,
                    fan_in_effect=effect,
                    fan_in_reasons=reasons,
                )
            )
            continue
        led = archived_lead_rules(
            candidates[lane], reference.affected_element_ids, block.fired_rules
        )
        cause: Cause = "place" if led else "unled"
        nearby = _nearby(reference.affected_element_ids, in_lane, flows)
        losses.append(
            Loss(
                index,
                lane,
                index in must_find,
                cause,
                verb,
                displaced_draft_id=nearby.id if nearby is not None else None,
                displaced_status=(
                    statuses.get(nearby.id) if nearby is not None else None
                ),
            )
        )
    return CaseLosses(
        case=case.id,
        losses=tuple(losses),
        assertion_backed=_backing(score, produced),
        proposed_nothing=not proposed,
    )


def pooled(rows: Sequence[CaseLosses]) -> dict[str, Any]:
    """Losses per cause over the corpus, counted rather than averaged, and the verb pairs."""
    totals: Counter[str] = Counter()
    must_find: Counter[str] = Counter()
    re_asked: Counter[str] = Counter()
    displaced: Counter[str] = Counter()
    displaced_status: Counter[str] = Counter()
    displaced_status_must_find: Counter[str] = Counter()
    kinds: Counter[str] = Counter()
    pairs: Counter[tuple[str, str]] = Counter()
    pairs_must_find: Counter[tuple[str, str]] = Counter()
    relations: Counter[str] = Counter()
    relations_must_find: Counter[str] = Counter()
    effects: Counter[str] = Counter()
    for row in rows:
        for loss in row.losses:
            totals[loss.cause] += 1
            if loss.fan_in_effect is not None:
                effects[loss.fan_in_effect] += 1
            must_find[loss.cause] += loss.must_find
            if loss.re_ask:
                re_asked[loss.cause] += 1
                kinds.update(loss.re_ask)
            if loss.displaced_draft_id is not None:
                displaced[loss.cause] += 1
                status = loss.displaced_status or "matched"
                displaced_status[status] += 1
                displaced_status_must_find[status] += loss.must_find
            if loss.cause == "verb" and loss.draft_verb is not None:
                pair = (loss.reference_verb, loss.draft_verb)
                pairs[pair] += 1
                pairs_must_find[pair] += loss.must_find
                if loss.place_relation is not None:
                    relations[loss.place_relation] += 1
                    relations_must_find[loss.place_relation] += loss.must_find
    return {
        "cases": len(rows),
        # How many cases had no lane proposal, and so no ``fan-in`` row.
        "proposed_nothing": sum(row.proposed_nothing for row in rows),
        "losses": sum(totals.values()),
        # The ``fan-in`` rows by what the fan-in did: a proposal it removed, or
        # a draft it narrowed off the reference's place. Two different fixes.
        "fan_in_by_effect": {
            effect: effects[effect] for effect in get_args(FanInEffect)
        },
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
        # The find side. Pooled rather than averaged, like every count here.
        "assertion_backed": {
            "resting": sum(row.assertion_backed.resting for row in rows),
            "matched": sum(row.assertion_backed.matched for row in rows),
            "must_find": sum(row.assertion_backed.must_find for row in rows),
        },
        # What a person said about the claim the lane wrote instead. ``pooled``
        # is the reading that matters: somebody accepted the neighbouring
        # finding, so the lane saw the system and chose another place. The key
        # ``matched`` is the displaced draft that answered a reference of its
        # own, which the ledger was never asked about.
        "displaced_standing": dict(sorted(displaced_status.items())),
        "displaced_standing_must_find": dict(
            sorted(displaced_status_must_find.items())
        ),
        "by_re_ask_kind": dict(sorted(kinds.items())),
        # The verb rows by how the two places relate. An ``equal`` row is the
        # strongest verb claim there is: one place, spelled the same way, two
        # actions. A containment says the two claims are written at different
        # grain, and that is where a draft answering another question lands.
        # Price an exemplar edit on the ``equal`` rows and read the rest.
        "verb_by_relation": {kind: relations[kind] for kind in RELATIONS},
        "verb_must_find_by_relation": {
            kind: relations_must_find[kind] for kind in RELATIONS
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
        silent = sum(totals["by_cause"][cause] for cause in sorted(SILENT))
        print(
            f"  of the place and unled rows, {totals['displaced']} have a"
            " surviving draft one element over ("
            + ", ".join(
                f"{cause} {totals['displaced_by_cause'][cause]}"
                for cause in sorted(SILENT)
            )
            + f") and {silent - totals['displaced']} have none in the lane nearby"
        )
        print(
            "  what a person said about the draft written instead: "
            + ", ".join(
                f"{status} {count} (must-find"
                f" {totals['displaced_standing_must_find'][status]})"
                for status, count in totals["displaced_standing"].items()
            )
            + " — pooled is the lane finding something real at another place"
        )
    backed = totals["assertion_backed"]
    if backed["resting"]:
        print(
            f"  the assertion layer backed {backed['matched']} of the"
            f" {backed['resting']} claim(s) that rest on a catalog row,"
            f" {backed['must_find']} of them a must-find — the find side of the"
            " table above"
        )
    if totals["by_cause"]["verb"]:
        print(
            "  of the verb rows, "
            + ", ".join(
                f"{totals['verb_by_relation'][kind]} {kind}"
                f" (must-find {totals['verb_must_find_by_relation'][kind]})"
                for kind in RELATIONS
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
