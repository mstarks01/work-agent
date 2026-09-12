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

from analysis_service.system_model import ModelIndex
from evals.harness import losses
from evals.harness.identity import SubsetVerbIdentity, endpoint_subset
from evals.harness.ledger import Ledger
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


def charge(case, flows, drafts, produced, unreconciled=()):
    score = score_case(case, produced, SubsetVerbIdentity({case.id: flows}), Ledger())
    block = Block(produced, unreconciled_rulings=unreconciled)
    return attribute_case(case, score, drafts, produced, flows, block)


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


def test_two_references_at_one_place_under_one_verb_leave_one_merged(case, flows):
    """Case 01's references 18 and 20: one draft, one verb, one place, two
    references. The scorer assigns the draft to one; the other is not the
    lane's loss, and the row says so."""
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
    assert pair, "case 01 carries such a pair"
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
