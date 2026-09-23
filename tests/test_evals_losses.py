"""What lost each STRIDE miss, charged from the scorer's own facts.

The instrument exists so a fix carries its ceiling before a paid run: an
exemplar edit can recover only ``verb`` losses, a candidate rule only ``unled``
ones. Every cause here is decided from a closed fact — the identity rule's
element half, a record's verb, a trigger's rule IDs — and the tests drive each
cause through the real scorer and the real identity rule over case 01.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from analysis_service.claims import Ground
from analysis_service.system_model import ModelIndex
from evals.harness import losses
from evals.harness.content import prose, structural
from evals.harness.fingerprint import key_claim
from evals.harness.identity import SubsetVerbIdentity, endpoint_form, endpoint_subset
from evals.harness.ledger import Ledger, cast
from evals.harness.losses import CAUSES, attribute_case, pooled
from evals.harness.reference import load_case
from evals.harness.scorer import score_case
from evals.harness.triggers import case_trigger_recall
from tests.eval_factories import draft_threat, promote, unreconciled
from tests.test_evals_applicability import Block

CASE_DIR = (
    Path(__file__).resolve().parents[1] / "evals" / "corpus" / "01-payments-checkout"
)


@pytest.fixture(scope="module")
def case():
    return load_case(CASE_DIR)


@pytest.fixture(scope="module")
def flows(case):
    return ModelIndex.of(case.model).flow_endpoints


#: The case that carries two references at one place under one verb, which is
#: the only shape where two claims compete to be charged. Case 01 was the
#: original example; the Case Sitting of 2026-09-21 ruled its references 18 and
#: 20 one finding written twice and both left the corpus. Case 08 carries the
#: shape twice over. :func:`merge_pair` derives the indices rather than pinning
#: them, so this is the only line that moves if it has to move again — and
#: ``next`` raising is the alarm that no case exercises the cause at all.
@pytest.fixture(scope="module")
def merge_case():
    return load_case(CASE_DIR.parent / "08-sso-identity-broker")


@pytest.fixture(scope="module")
def merge_flows(merge_case):
    return ModelIndex.of(merge_case.model).flow_endpoints


def charge(case, flows, drafts, produced, unreconciled=(), votes=None):
    score = score_case(
        case, produced, SubsetVerbIdentity({case.id: flows}), votes or Ledger()
    )
    block = Block(produced, unreconciled_rulings=unreconciled)
    return attribute_case(case, score, drafts, produced, flows, block)


def accepted(case, claim, flows):
    """A ledger holding one ``up`` vote on ``claim``, keyed as the scorer keys it.

    Built through :func:`~evals.harness.ledger.cast` and
    :func:`~evals.harness.fingerprint.key_claim`, which are the callers the
    scorer itself uses. Composing the fingerprint here would make this test a
    second reader of which version keys a claim, and it would agree with the
    scorer until the rule moved.
    """
    value, components = key_claim(
        "stride",
        case.id,
        claim.category,
        tuple(claim.affected_element_ids),
        flows,
        verb=claim.verb,
    )
    vote = cast(
        components,
        case.id,
        "up",
        "tester",
        content=structural(claim),
        prose=prose(claim),
    )
    assert vote.fingerprint == value
    return Ledger([vote])


def at(reference, sequence, verb):
    """A draft at the reference's own place, with the verb given."""
    return draft_threat(
        sequence,
        reference.category,
        f"draft {sequence}",
        element_ids=reference.affected_element_ids,
        verb=verb,
    )


def by_index(charged):
    return {loss.reference_index: loss for loss in charged.losses}


def _one_element_over(case, flows):
    """A reference index, and a draft whose place overlaps it without containing it.

    One resolved element the reference names plus one it does not, which is the
    shape ``_nearby`` looks for and the containment test refuses.
    """
    references = case.stride_claims()
    index, reference = next(
        (position, claim)
        for position, claim in enumerate(references)
        if len(endpoint_form(claim.affected_element_ids, flows)) >= 2
    )
    place = sorted(endpoint_form(reference.affected_element_ids, flows))
    stranger = next(
        element.id
        for element in case.model.elements()
        if element.id not in place and not element.id.startswith(("boundary:", "flow:"))
    )
    draft = draft_threat(
        1,
        reference.category,
        "one element over",
        element_ids=[place[0], stranger],
        verb=reference.verb,
    )
    return index, draft


def test_a_surviving_claim_at_the_place_with_another_verb_is_a_verb_loss(case, flows):
    reference = case.stride_claims()[0]
    other = "guess-credential" if reference.verb != "guess-credential" else "replay"
    draft = at(reference, 1, other)

    charged = charge(case, flows, [draft], [promote(draft)])

    loss = by_index(charged)[0]
    assert loss.cause == "verb"
    assert (loss.reference_verb, loss.draft_verb) == (reference.verb, other)
    assert loss.draft_id == draft.id
    assert loss.must_find is (reference.tier == "must-find")


def test_two_references_at_one_place_under_one_verb_leave_one_merged(
    merge_case, merge_flows
):
    """One draft, one verb, one place, two references. The scorer assigns the
    draft to one; the other is not the lane's loss, and the row says so."""
    case, flows = merge_case, merge_flows
    references = case.stride_claims()
    pair = [
        (i, j)
        for i, left in enumerate(references)
        for j, right in enumerate(references)
        if i < j
        and left.category == right.category
        and left.verb == right.verb
        and endpoint_subset(
            left.affected_element_ids, right.affected_element_ids, flows
        )
    ]
    assert pair, "the merge case carries such a pair"
    first, second = pair[0]
    draft = at(references[first], 1, references[first].verb)

    charged = by_index(charge(case, flows, [draft], [promote(draft)]))

    merged = [i for i in (first, second) if i in charged]
    assert len(merged) == 1
    assert charged[merged[0]].cause == "merged"
    assert charged[merged[0]].draft_id == draft.id


def test_a_killed_draft_at_the_place_with_the_reference_verb_is_the_critics(
    case, flows
):
    reference = case.stride_claims()[0]
    draft = at(reference, 1, reference.verb)

    charged = charge(case, flows, [draft], [])

    loss = by_index(charged)[0]
    assert loss.cause == "critic"
    assert loss.draft_id == draft.id


def test_an_equivalent_verb_is_the_reference_action(case, flows, monkeypatch):
    """The identity rule reads verbs through the equivalence table, so this
    instrument does too. The table is empty today; with a string comparison
    the two agreed until the first entry landed, and then a killed draft under
    an equivalent verb fell through to the lead, and a surviving one at the
    place read as a verb loss the scorer had already forgiven."""
    from evals.harness import verbs

    reference = case.stride_claims()[0]
    other = "guess-credential" if reference.verb != "guess-credential" else "replay"
    monkeypatch.setattr(verbs, "EQUIVALENT", (frozenset({reference.verb, other}),))
    draft = at(reference, 1, other)

    killed = by_index(charge(case, flows, [draft], []))[0]
    assert killed.cause == "critic"
    assert killed.draft_verb == other

    survived = by_index(charge(case, flows, [draft], [promote(draft)]))
    assert 0 not in survived, "the rule matched it, so the reference is not a miss"


def test_a_killed_draft_with_another_verb_is_still_a_verb_loss_only_if_it_survived(
    case, flows
):
    """A rejected draft under another verb would have split anyway, so it
    charges nothing to the critic; the reference falls through to its lead."""
    reference = case.stride_claims()[0]
    other = "guess-credential" if reference.verb != "guess-credential" else "replay"
    draft = at(reference, 1, other)

    charged = charge(case, flows, [draft], [])

    assert by_index(charged)[0].cause in {"place", "unled"}


def test_nothing_at_the_place_charges_the_lead_or_its_absence(case, flows):
    hits = case_trigger_recall(case, "stride").hits
    led = next(i for i, hit in enumerate(hits) if hit.rule_ids)
    unled = next(i for i, hit in enumerate(hits) if not hit.rule_ids)

    charged = by_index(charge(case, flows, [], []))

    assert charged[led].cause == "place"
    assert charged[unled].cause == "unled"
    assert charged[led].draft_id is None


def test_every_miss_is_charged_once_and_a_match_is_not(case, flows):
    reference = case.stride_claims()[0]
    draft = at(reference, 1, reference.verb)

    charged = charge(case, flows, [draft], [promote(draft)])

    assert 0 not in by_index(charged), "a matched reference has no loss"
    assert len(charged.losses) == len(case.stride_claims()) - 1
    assert sum(charged.by_cause.values()) == len(charged.losses)


def test_pooled_counts_and_the_verb_pairs(case, flows):
    reference = case.stride_claims()[0]
    other = "guess-credential" if reference.verb != "guess-credential" else "replay"
    draft = at(reference, 1, other)
    row = charge(case, flows, [draft], [promote(draft)])

    totals = pooled([row, row])

    assert totals["cases"] == 2
    assert totals["losses"] == 2 * len(row.losses)
    assert set(totals["by_cause"]) == set(CAUSES)
    assert totals["by_cause"]["verb"] == 2
    assert totals["verb_pairs"][0] == {
        "reference_verb": reference.verb,
        "draft_verb": other,
        "losses": 2,
        "must_find": 2 * int(reference.tier == "must-find"),
    }


def test_the_artifact_writes_its_two_keys_from_nothing():
    assert losses.artifact([]) == {"losses": [], "losses_aggregate": None}


def test_render_prints_the_causes_in_order(case, flows, capsys):
    losses.render([charge(case, flows, [], [])])
    out = capsys.readouterr().out
    assert "STRIDE loss attribution" in out
    assert (
        out.index(" verb")
        < out.index(" merged")
        < out.index(" critic")
        < out.index(" place")
        < out.index(" unled")
    )


class TestAKillDuringARepairIsChargedApartFromOneTheCriticArgued:
    """#796: a draft the critic reasoned its way to killing and one killed by
    the re-ask that repaired a fumbled first pass are two different things to
    fix, and the cause alone prints the same number for both.

    The join is on the draft's own ID, which is what
    ``unreconciled_rulings`` names, and it is asked through the block's own
    reader so this and the ASVS instrument cannot disagree about what a
    re-asked claim is.
    """

    def test_a_kill_the_first_pass_ruled_cleanly_charges_no_re_ask(self, case, flows):
        reference = case.stride_claims()[0]
        draft = at(reference, 1, reference.verb)

        charged = charge(case, flows, [draft], [])

        assert by_index(charged)[0].re_ask == ()

    def test_a_kill_written_by_the_re_ask_names_the_first_pass_problem(
        self, case, flows
    ):
        reference = case.stride_claims()[0]
        draft = at(reference, 1, reference.verb)

        charged = charge(
            case, flows, [draft], [], unreconciled=[unreconciled(draft.id)]
        )

        loss = by_index(charged)[0]
        assert (loss.cause, loss.re_ask) == ("critic", ("dropped",))

    def test_a_verb_loss_carries_it_too(self, case, flows):
        """Every cause that names a draft can be charged, not only the critic."""
        reference = case.stride_claims()[0]
        other = "guess-credential" if reference.verb != "guess-credential" else "replay"
        draft = at(reference, 1, other)

        charged = charge(
            case,
            flows,
            [draft],
            [promote(draft)],
            unreconciled=[unreconciled(draft.id, "verdict-shape")],
        )

        loss = by_index(charged)[0]
        assert (loss.cause, loss.re_ask) == ("verb", ("verdict-shape",))

    def test_a_reference_no_draft_reached_has_no_ruling_to_charge(self, case, flows):
        """A mark naming a claim no row names annotates nothing."""
        charged = charge(case, flows, [], [], unreconciled=[unreconciled("stride-1")])

        assert all(loss.re_ask == () for loss in charged.losses)

    def test_the_fold_counts_the_rows_and_the_kinds_apart(self, case, flows):
        reference = case.stride_claims()[0]
        draft = at(reference, 1, reference.verb)
        charged = charge(
            case,
            flows,
            [draft],
            [],
            unreconciled=[
                unreconciled(draft.id),
                unreconciled(draft.id, "verdict-shape"),
                unreconciled(draft.id, "verdict-shape"),
            ],
        )

        assert by_index(charged)[0].re_ask == ("dropped", "verdict-shape")
        totals = pooled([charged])
        assert totals["re_asked"] == 1
        assert totals["re_asked_by_cause"]["critic"] == 1
        assert totals["by_re_ask_kind"] == {"dropped": 1, "verdict-shape": 1}

    def test_the_render_says_so_only_when_a_row_carries_one(self, case, flows, capsys):
        reference = case.stride_claims()[0]
        draft = at(reference, 1, reference.verb)

        losses.render([charge(case, flows, [draft], [])])
        assert "first critic pass fumbled" not in capsys.readouterr().out

        losses.render(
            [charge(case, flows, [draft], [], unreconciled=[unreconciled(draft.id)])]
        )
        out = capsys.readouterr().out
        assert "first critic pass fumbled" in out
        assert "dropped 1" in out


class TestADisplacedRow:
    """A ``place`` or ``unled`` row says whether the lane wrote one element over."""

    def test_a_draft_sharing_one_element_but_not_the_place_is_named(self, case, flows):
        index, draft = _one_element_over(case, flows)
        reference = case.stride_claims()[index]
        assert not endpoint_subset(
            reference.affected_element_ids, draft.affected_element_ids, flows
        )
        charged = charge(case, flows, [draft], [promote(draft)])
        loss = by_index(charged)[index]

        assert loss.cause in ("place", "unled")
        assert loss.displaced_draft_id == draft.id
        assert charged.displaced_by_cause[loss.cause] >= 1
        assert pooled([charged])["displaced"] >= 1

    def test_the_ledger_answer_on_that_draft_rides_beside_it(self, case, flows):
        """``displaced_standing`` separates a real finding from a refused one.

        Both spell ``place`` on the row, and the 2026-09-22 audit had to join
        the ledger by hand to tell them apart.
        """
        index, draft = _one_element_over(case, flows)
        claim = promote(draft)

        # One draft can sit one element over from several references, so the
        # pooled counts are per row rather than per draft.
        unvoted = charge(case, flows, [draft], [claim])
        rows = pooled([unvoted])["displaced"]
        assert by_index(unvoted)[index].displaced_standing == "unvoted"
        assert pooled([unvoted])["displaced_standing"] == {"unvoted": rows}

        voted = charge(
            case, flows, [draft], [claim], votes=accepted(case, claim, flows)
        )
        assert by_index(voted)[index].displaced_standing == "pooled"
        assert pooled([voted])["displaced_standing"] == {"pooled": rows}

    def test_the_standing_is_the_scorer_s_own_and_never_a_second_lookup(
        self, case, flows
    ):
        """Every row's standing is the one the scorer recorded, or nothing.

        The field may not invent an answer: a displaced draft the scorer
        matched to a reference of its own is absent from ``unlisted``, and
        ``unvoted`` there would claim a person declined a question nobody put.
        """
        index, draft = _one_element_over(case, flows)
        claim = promote(draft)
        produced = [claim]
        score = score_case(
            case, produced, SubsetVerbIdentity({case.id: flows}), Ledger()
        )
        charged = charge(case, flows, [draft], produced)
        recorded = {threat.threat_id: threat.standing for threat in score.unlisted}

        assert by_index(charged)[index].displaced_draft_id == draft.id
        for loss in charged.losses:
            expected = recorded.get(loss.displaced_draft_id)
            assert loss.displaced_standing == expected

    def test_a_lane_that_wrote_nothing_nearby_names_no_draft(self, case, flows):
        charged = charge(case, flows, [], [])
        assert all(loss.displaced_draft_id is None for loss in charged.losses)
        assert pooled([charged])["displaced"] == 0
        assert all(
            value == 0 for value in pooled([charged])["displaced_by_cause"].values()
        )

    def test_the_other_causes_never_carry_the_field(self, case, flows):
        reference = case.stride_claims()[0]
        other = "guess-credential" if reference.verb != "guess-credential" else "replay"
        draft = at(reference, 1, other)
        charged = charge(case, flows, [draft], [promote(draft)])
        assert by_index(charged)[0].displaced_draft_id is None


class TestAMisfiledMiss:
    """A finding at the place, with the action, filed in another lane."""

    def test_the_scorer_lane_error_is_charged_as_misfiled(self, case, flows):
        reference = next(
            claim for claim in case.stride_claims() if claim.category == "tampering"
        )
        index = case.stride_claims().index(reference)
        other_lane = "spoofing"
        draft = draft_threat(
            1,
            other_lane,
            "filed in the wrong lane",
            element_ids=reference.affected_element_ids,
            verb=reference.verb,
        )
        charged = charge(case, flows, [draft], [promote(draft)])
        loss = by_index(charged)[index]

        assert loss.cause == "misfiled"
        assert loss.draft_id == draft.id
        assert loss.draft_verb == reference.verb
        assert loss.displaced_draft_id is None
        assert charged.by_cause["misfiled"] == 1
        assert pooled([charged])["by_cause"]["misfiled"] == 1

    def test_misfiled_outranks_place_and_unled(self, case, flows):
        """The lane wrote it; that the lead did not take in its own lane is secondary."""
        reference = next(
            claim for claim in case.stride_claims() if claim.category == "tampering"
        )
        index = case.stride_claims().index(reference)
        draft = draft_threat(
            1,
            "spoofing",
            "elsewhere",
            element_ids=reference.affected_element_ids,
            verb=reference.verb,
        )
        charged = charge(case, flows, [draft], [promote(draft)])
        assert by_index(charged)[index].cause not in ("place", "unled")


class TestHowTheTwoPlacesRelate:
    """A verb row says whether the two claims cite one place or nest.

    The element half of the identity rule accepts containment either way, so a
    draft citing one process sits at the place of any reference resolving to a
    set that holds it — whatever the two claims say. Three of the third
    Baseline's five ``abuse-grant`` against ``escalate`` rows were exactly that,
    and each named a draft answering a different question (#871). The row now
    says which kind it is, so a ceiling can be priced on the equal rows.
    """

    def test_one_place_spelled_the_same_way_is_equal(self, case, flows):
        reference = case.stride_claims()[0]
        other = "guess-credential" if reference.verb != "guess-credential" else "replay"
        draft = at(reference, 1, other)
        charged = charge(case, flows, [draft], [promote(draft)])
        loss = by_index(charged)[0]

        assert loss.cause == "verb"
        assert loss.place_relation == "equal"

    def test_a_draft_inside_the_reference_says_the_reference_contains_it(
        self, case, flows
    ):
        references = case.stride_claims()
        index, reference = next(
            (i, claim)
            for i, claim in enumerate(references)
            if len(endpoint_form(claim.affected_element_ids, flows)) >= 2
        )
        inside = sorted(endpoint_form(reference.affected_element_ids, flows))[:1]
        other = "guess-credential" if reference.verb != "guess-credential" else "replay"
        draft = draft_threat(
            1, reference.category, "narrower", element_ids=inside, verb=other
        )
        charged = charge(case, flows, [draft], [promote(draft)])
        loss = by_index(charged)[index]

        assert loss.cause == "verb"
        assert loss.place_relation == "reference-contains"

    def test_a_draft_around_the_reference_says_the_draft_contains_it(self, case, flows):
        reference = case.stride_claims()[0]
        place = sorted(endpoint_form(reference.affected_element_ids, flows))
        wider = next(
            element.id
            for element in case.model.elements()
            if element.id not in place
            and not element.id.startswith(("boundary:", "flow:"))
        )
        other = "guess-credential" if reference.verb != "guess-credential" else "replay"
        draft = draft_threat(
            1, reference.category, "wider", element_ids=[*place, wider], verb=other
        )
        charged = charge(case, flows, [draft], [promote(draft)])
        loss = by_index(charged)[0]

        assert loss.cause == "verb"
        assert loss.place_relation == "draft-contains"

    def test_a_row_naming_no_draft_carries_no_relation(self, case, flows):
        charged = charge(case, flows, [], [])
        assert all(loss.place_relation is None for loss in charged.losses)

    def test_the_fold_counts_the_verb_rows_by_relation(self, case, flows):
        reference = case.stride_claims()[0]
        other = "guess-credential" if reference.verb != "guess-credential" else "replay"
        draft = at(reference, 1, other)
        charged = charge(case, flows, [draft], [promote(draft)])
        totals = pooled([charged])

        assert totals["verb_by_relation"]["equal"] == 1
        assert sum(totals["verb_by_relation"].values()) == totals["by_cause"]["verb"]
        assert (
            totals["verb_must_find_by_relation"]["equal"]
            <= totals["must_find_by_cause"]["verb"]
        )

    def test_the_render_says_how_the_verb_rows_split(self, case, flows, capsys):
        reference = case.stride_claims()[0]
        other = "guess-credential" if reference.verb != "guess-credential" else "replay"
        draft = at(reference, 1, other)
        losses.render([charge(case, flows, [draft], [promote(draft)])])

        assert "1 equal" in capsys.readouterr().out


# --- The cause is a fact about the rule, never about the input order ----------


def merge_pair(case, flows) -> tuple[int, int]:
    """Two references at one place under one verb, as the merge case carries them.

    The scorer matches a claim there to one of the two, so the other is a miss
    with that claim still sitting at its place — which is the only shape where
    two claims compete to be charged. Derived rather than pinned to an index,
    so a corpus edit that renumbers the references does not silently stop
    testing this.
    """
    references = case.stride_claims()
    return next(
        (i, j)
        for i, left in enumerate(references)
        for j, right in enumerate(references)
        if i < j
        and left.category == right.category
        and left.verb == right.verb
        and endpoint_subset(
            left.affected_element_ids, right.affected_element_ids, flows
        )
    )


def test_two_claims_at_one_place_charge_the_same_cause_in_either_order(
    merge_case, merge_flows
):
    """Reversing the report's claims must not move a cause.

    The defect this pins: ``_at_place`` returned the claims in report order and
    the charge read ``[0]``, so the label depended on which claim the report
    happened to list first. Reversing the claims — no finding changed, no match
    changed — moved case 05's reference 9 between ``merged`` and ``verb`` in
    two archived Baselines. An instrument that priced three prompt edits this
    week cannot answer differently when its input is shuffled.
    """
    case, flows = merge_case, merge_flows
    reference = case.stride_claims()[merge_pair(case, flows)[0]]
    other = "guess-credential" if reference.verb != "guess-credential" else "replay"
    same_verb = at(reference, 1, reference.verb)
    other_verb = at(reference, 2, other)
    produced = [promote(same_verb), promote(other_verb)]

    forward = by_index(charge(case, flows, [same_verb, other_verb], produced))
    reversed_ = by_index(
        charge(case, flows, [other_verb, same_verb], list(reversed(produced)))
    )

    # Both claims sit at the place, so exactly one reference of the merge pair
    # is left over and both orders have to charge it the same way.
    assert forward, "the pair leaves a miss for the two claims to compete over"
    assert {index: loss.cause for index, loss in forward.items()} == {
        index: loss.cause for index, loss in reversed_.items()
    }
    assert {index: loss.draft_id for index, loss in forward.items()} == {
        index: loss.draft_id for index, loss in reversed_.items()
    }


def test_a_claim_carrying_the_references_action_wins_over_one_that_does_not(
    merge_case, merge_flows
):
    """The rule behind the tie-break, stated on its own.

    A reference answered by a claim under its own action is a merge the corpus
    must rule on. Charging it to the verb while that claim sits beside it would
    send a reader to fix an exemplar that is already right.
    """
    case, flows = merge_case, merge_flows
    first, second = merge_pair(case, flows)
    reference = case.stride_claims()[first]
    other = "guess-credential" if reference.verb != "guess-credential" else "replay"
    # The wrong-verb claim is listed first, so report order argues for "verb".
    other_verb = at(reference, 1, other)
    same_verb = at(reference, 2, reference.verb)

    charged = by_index(
        charge(
            case,
            flows,
            [other_verb, same_verb],
            [promote(other_verb), promote(same_verb)],
        )
    )

    missed = next(index for index in (first, second) if index in charged)
    assert charged[missed].cause == "merged"
    assert charged[missed].draft_id == same_verb.id


def test_a_displaced_draft_is_named_the_same_way_in_either_order(case, flows):
    """``displaced_draft_id`` names a draft in the artifact, so it is ordered too.

    The third order-sensitive read: ``_nearby`` returned the first overlapping
    draft. Same reasoning, same fix, and a separate test because a reader who
    fixes one of the three does not automatically fix the others.
    """
    hits = case_trigger_recall(case, "stride").hits
    references = case.stride_claims()
    # A reference the rules led to, resolving to two elements, so a draft can
    # share one without the two places containing each other.
    led = next(
        index
        for index, hit in enumerate(hits)
        if hit.rule_ids
        and len(endpoint_form(references[index].affected_element_ids, flows)) >= 2
    )
    reference = references[led]
    here = sorted(endpoint_form(reference.affected_element_ids, flows))
    # Anchors that resolve clear of the reference's own place, and that the
    # identity rule actually reads. A flow whose endpoints are the reference's
    # would make the draft's place *equal* to it, and a ``boundary:`` ID is
    # dropped before any comparison — either one turns the overlap this test
    # needs into a match.
    elsewhere = sorted(
        element.id
        for element in case.model.elements()
        if not element.id.startswith("boundary:")
        and not endpoint_form([element.id], flows) & set(here)
    )
    near_a = draft_threat(
        1,
        reference.category,
        "near a",
        element_ids=[here[0], elsewhere[0]],
        verb=reference.verb,
    )
    near_b = draft_threat(
        2,
        reference.category,
        "near b",
        element_ids=[here[1], elsewhere[1]],
        verb=reference.verb,
    )
    produced = [promote(near_a), promote(near_b)]

    forward = by_index(charge(case, flows, [near_a, near_b], produced))
    reversed_ = by_index(
        charge(case, flows, [near_b, near_a], list(reversed(produced)))
    )

    assert forward[led].cause == "place"
    assert forward[led].displaced_draft_id is not None
    assert forward[led].displaced_draft_id == reversed_[led].displaced_draft_id


class TestWhatTheAssertionLayerBacked:
    """The find side of the table (`QA-2026-09-22-02`).

    Every loss row is a reference nothing found. This is the other side: the
    references a catalog row did find. Without it the table has misses and no
    control, which is why the 2026-09-23 audit could not price the layer.
    """

    @staticmethod
    def resting_on_a_row(reference, sequence=1):
        """A draft at the reference's place, grounded on an assertion row."""
        return at(reference, sequence, reference.verb).model_copy(
            update={
                "grounds": [
                    Ground(
                        kind="assertion",
                        assertion="assertion:mfa-requirement~principal:a~~required",
                    )
                ]
            }
        )

    def test_a_claim_resting_on_a_row_that_matched_is_counted(self, case, flows):
        reference = case.stride_claims()[0]
        draft = self.resting_on_a_row(reference)
        charged = charge(case, flows, [draft], [promote(draft)])

        backed = charged.assertion_backed
        assert backed.resting == 1
        assert backed.matched == 1
        assert backed.must_find == reference.must_find
        assert pooled([charged])["assertion_backed"]["matched"] == 1

    def test_a_claim_resting_on_a_row_that_matched_nothing_is_not_a_find(
        self, case, flows
    ):
        """Resting counts the claim; matched counts only what answered a reference."""
        stranger = draft_threat(
            9, "spoofing", "Nothing the corpus lists.", element_ids=["entity:shopper"]
        ).model_copy(
            update={"grounds": [Ground(kind="assertion", assertion="assertion:x~y~~z")]}
        )
        charged = charge(case, flows, [stranger], [promote(stranger)])

        assert charged.assertion_backed.resting == 1
        assert charged.assertion_backed.matched == 0

    def test_an_unknown_row_counts_because_the_set_is_read_not_spelled(
        self, case, flows
    ):
        """``ASSERTION_GROUNDS`` holds both kinds, and this reads it rather than listing one."""
        reference = case.stride_claims()[0]
        draft = at(reference, 1, reference.verb).model_copy(
            update={
                "grounds": [
                    Ground(kind="unknown-assertion", assertion="assertion:x~y~~z")
                ]
            }
        )
        charged = charge(case, flows, [draft], [promote(draft)])

        assert charged.assertion_backed.resting == 1
        assert charged.assertion_backed.matched == 1

    def test_a_run_with_no_assertion_node_reads_a_measured_zero(self, case, flows):
        """Zero is the reading, not an absence: the layer backed nothing.

        Every sweep this repository has archived reads this, because none ran
        with ``ANALYSIS_ASSERTIONS`` set.
        """
        reference = case.stride_claims()[0]
        draft = at(reference, 1, reference.verb)
        charged = charge(case, flows, [draft], [promote(draft)])

        assert charged.assertion_backed == losses.AssertionBacking()
        assert charged.to_json()["assertion_backed"] == {
            "resting": 0,
            "matched": 0,
            "must_find": 0,
        }
        assert pooled([charged])["assertion_backed"] == {
            "resting": 0,
            "matched": 0,
            "must_find": 0,
        }

    def test_it_rides_inside_the_instrument_s_own_keys(self, case, flows):
        """A new top-level artifact key moves ``ARTIFACT_VERSION`` and re-seals every Baseline."""
        reference = case.stride_claims()[0]
        draft = self.resting_on_a_row(reference)
        block = losses.artifact([charge(case, flows, [draft], [promote(draft)])])

        assert set(block) == {"losses", "losses_aggregate"}
        assert block["losses_aggregate"]["assertion_backed"]["matched"] == 1
