"""The Critic's round as a value: the first pass, the merge and the outcome.

Each behaviour here was once asserted by driving ``graph.route_review`` twice
against a fake context and reading state keys back. The round decides every
one of them, so the tests ask the round, and nothing here parks or routes.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from analysis_service import critic
from analysis_service.claims import (
    AnalysisMarks,
    ProposedVerdict,
    UnknownRef,
    UnverifiedGround,
)
from analysis_service.critic import CriticOutputError
from analysis_service.critic_round import (
    Accepted,
    CriticRound,
    Failed,
    FirstPass,
    Revision,
    critic_request,
    node_request,
    rulings_of,
)
from analysis_service.frameworks import schemas_for
from analysis_service.graph import (
    STATE_BOUNDARY_CROSSINGS,
    STATE_INPUT_TEXT,
    STATE_SYSTEM_MODEL,
    FrameworkNodes,
)
from analysis_service.markdown_loader import MarkdownLoader
from tests.factories import sample_draft, sample_ruling, valid_model
from tests.test_graph import PROJECT_ROOT

NO_MARKS = AnalysisMarks()


def emitted(*rulings) -> list[dict]:
    """A critic's emission, as the node's ``claims`` list holds it."""
    return [ruling.model_dump(mode="json") for ruling in rulings]


def round_over(*drafts, marks: AnalysisMarks = NO_MARKS) -> CriticRound:
    return CriticRound.of("stride", valid_model(), list(drafts), marks, None)


def two_drafts():
    return [sample_draft("S-01"), sample_draft("T-01", category="tampering")]


def revised(drafts, *rulings) -> Revision:
    """The first look over ``rulings``, which must not reconcile."""
    outcome = round_over(*drafts).first(emitted(*rulings))
    assert isinstance(outcome, Revision)
    return outcome


def second_look(drafts, first: FirstPass, *rulings):
    """The re-ask returned: merge it onto the first pass and rule on the set."""
    critic_round = round_over(*drafts)
    merged, drift = critic_round.merge(first, emitted(*rulings))
    return merged, drift, critic_round.outcome(merged)


def verdicts(merged) -> dict[str, str]:
    return {
        ruling["id"]: ProposedVerdict.model_validate(ruling["verdict"]).status
        for ruling in merged
    }


class TestTheFirstLook:
    """The check and the re-ask view come out of one call, over one set."""

    def test_a_pass_that_reconciles_is_accepted_with_its_claims(self):
        outcome = round_over(sample_draft("S-01")).first(emitted(sample_ruling("S-01")))

        assert isinstance(outcome, Accepted)
        assert outcome.count == 1
        assert [claim.id for claim in outcome.claims] == ["S-01"]
        assert outcome.rejected == []

    def test_a_dropped_ruling_revises_and_names_what_the_re_ask_may_change(self):
        outcome = revised(two_drafts(), sample_ruling("S-01"))

        assert outcome.messages
        assert outcome.first.repairable == ["T-01"]
        assert [ruling["id"] for ruling in outcome.first.rulings] == ["S-01"]

    def test_the_roster_is_every_drafted_id_and_the_view_is_not(self):
        """The re-ask reproduces rulings, not drafts: an ID carries the whole
        of a claim it need not read, so only the drafts a structural fix cannot
        be made without reading travel in full."""
        outcome = revised(two_drafts(), sample_ruling("S-01"))

        assert outcome.roster == ["S-01", "T-01"]
        assert [draft["id"] for draft in outcome.unreconciled] == ["T-01"]

    def test_every_draft_the_view_carries_is_one_a_message_names(self):
        outcome = revised(two_drafts())

        shown = {draft["id"] for draft in outcome.unreconciled}
        assert shown
        for draft_id in shown:
            assert any(draft_id in message for message in outcome.messages), (
                f"{draft_id} travels in full but no message says why"
            )

    def test_the_re_ask_reads_the_marks_the_first_pass_read(self):
        """Both passes get both mark kinds, so a critic told once that a quote
        was not found does not read the same quote clean on the second look."""
        marks = AnalysisMarks(
            unverified_grounds=[
                UnverifiedGround(claim_id="T-01", index=0, reason="not in the source")
            ]
        )
        critic_round = round_over(*two_drafts(), marks=marks)

        (first_pass,) = [row for row in critic_round.view() if row["id"] == "T-01"]
        outcome = critic_round.first(emitted(sample_ruling("S-01")))

        assert isinstance(outcome, Revision)
        assert first_pass["unverified_quotes"]
        assert outcome.unreconciled == [first_pass]

    def test_the_view_is_the_critic_views_over_the_rounds_marks(self):
        drafts = two_drafts()

        assert round_over(*drafts).view() == critic.critic_view(drafts)


class TestTheSecondLook:
    def test_a_re_ask_may_change_only_the_rulings_the_problems_named(self):
        """The retry prompt says to carry every other ruling across byte-identical,
        and the merge enforces it against the first pass. A re-ask that repairs
        the dropped draft and also flips a confirmed ruling to rejected keeps the
        first ruling, and the drift is recorded exactly once (#659)."""
        drafts = two_drafts()
        first = revised(drafts, sample_ruling("S-01")).first
        flipped = sample_ruling(
            "S-01",
            verdict=ProposedVerdict(reason="re-decided", rejected_because="reasoning"),
        )

        merged, drift, outcome = second_look(
            drafts, first, flipped, sample_ruling("T-01")
        )

        assert isinstance(outcome, Accepted)
        assert verdicts(merged) == {"S-01": "confirmed", "T-01": "confirmed"}
        assert [(mark.claim_id, mark.kind) for mark in drift] == [
            ("S-01", "unbriefed-change")
        ]

    def test_a_re_ask_that_repairs_only_what_was_named_leaves_no_drift(self):
        drafts = two_drafts()
        first = revised(drafts, sample_ruling("S-01")).first

        _, drift, outcome = second_look(
            drafts, first, sample_ruling("S-01"), sample_ruling("T-01")
        )

        assert isinstance(outcome, Accepted)
        assert drift == []

    def test_a_re_ask_that_returns_only_the_named_rulings_reconciles(self):
        """#1476: the re-ask returns the rulings the problems name, and the merge
        keeps the first pass's for every other draft, so the set is whole again."""
        drafts = two_drafts()
        first = revised(drafts, sample_ruling("S-01")).first

        merged, drift, outcome = second_look(drafts, first, sample_ruling("T-01"))

        assert isinstance(outcome, Accepted)
        assert verdicts(merged) == {"S-01": "confirmed", "T-01": "confirmed"}
        assert drift == []

    def test_an_empty_re_ask_still_fails_the_second_look(self):
        """#1476: an empty return repairs nothing, so the named draft stays dropped."""
        drafts = two_drafts()
        first = revised(drafts, sample_ruling("S-01")).first

        _, _, outcome = second_look(drafts, first)

        assert isinstance(outcome, Failed)
        assert any("T-01" in message for message in outcome.messages)
        with pytest.raises(CriticOutputError, match="dropped draft 'T-01'"):
            raise outcome.error

    def test_a_re_ask_that_respells_a_ruling_is_not_drift(self):
        """A provider may spell one ruling two ways across two calls: an optional
        key present as ``null`` in one pass and absent in the next. The merge
        compares the ruling model's own spelling, so a respelling is not a change
        and earns no mark."""
        drafts = two_drafts()
        first = revised(drafts, sample_ruling("S-01")).first
        sparse = sample_ruling("S-01").model_dump(mode="json", exclude_defaults=True)
        assert sparse != sample_ruling("S-01").model_dump(mode="json")
        critic_round = round_over(*drafts)

        merged, drift = critic_round.merge(
            first, [sparse, sample_ruling("T-01").model_dump(mode="json")]
        )

        assert isinstance(critic_round.outcome(merged), Accepted)
        assert drift == []

    def test_an_invented_ruling_in_the_re_ask_still_fails_the_second_look(self):
        """The merge resolves what the first pass got right; it does not excuse a
        re-ask that invents. That is a loud failure."""
        drafts = two_drafts()
        first = revised(drafts, sample_ruling("S-01")).first

        _, _, outcome = second_look(
            drafts,
            first,
            sample_ruling("S-01"),
            sample_ruling("T-01"),
            sample_ruling("X-99"),
        )

        assert isinstance(outcome, Failed)
        assert any("X-99" in message for message in outcome.messages)

    def test_a_duplicated_id_is_the_re_asks_to_repair(self):
        """A duplicate ID names no draft to read, but it is the re-ask's to fix, so
        the merge takes the re-ask's ruling for it rather than the first pass's pair."""
        drafts = [sample_draft("S-01")]
        revision = revised(drafts, sample_ruling("S-01"), sample_ruling("S-01"))
        assert revision.first.repairable == ["S-01"]
        assert revision.unreconciled == []

        merged, _, outcome = second_look(drafts, revision.first, sample_ruling("S-01"))

        assert isinstance(outcome, Accepted)
        assert [ruling["id"] for ruling in merged] == ["S-01"]


class TestTheOutcome:
    def test_an_unresolved_unknown_fails_naming_the_claim(self):
        ruling = sample_ruling(
            "S-01",
            verdict=ProposedVerdict(
                reason="encryption unstated",
                related_unknowns=[UnknownRef(element_id="store:ghost", attribute="x")],
            ),
        )

        outcome = round_over(sample_draft("S-01")).outcome(emitted(ruling))

        assert isinstance(outcome, Failed)
        assert [problem.claim_id for problem in outcome.problems] == ["S-01"]
        assert "store:ghost" in str(outcome.error)

    def test_an_empty_round_reconciles(self):
        """A framework that drafted nothing, ruled on by nobody, assembles empty."""
        outcome = round_over().outcome([])

        assert outcome == Accepted(claims=[], rejected=[])

    def test_the_rulings_are_read_as_the_packages_own(self):
        """``frameworks/stride/critic.md`` asks for a ``confidence`` the neutral
        ruling forbids, so the round parses with the package's own type."""
        (ruling,) = rulings_of(emitted(sample_ruling("S-01")), schemas_for("stride"))

        assert type(ruling) is type(sample_ruling("S-01"))


class TestTheRequest:
    """The request a critic node sends, rendered off the graph."""

    @pytest.fixture
    def shared(self) -> dict[str, str]:
        return {
            STATE_SYSTEM_MODEL: "THE MODEL",
            STATE_BOUNDARY_CROSSINGS: "THE CROSSINGS",
            STATE_INPUT_TEXT: "THE SOURCES",
        }

    def test_the_instruction_carries_the_shared_keys_and_the_view(self, shared):
        drafts = two_drafts()

        instruction, turn = asyncio.run(
            critic_request(
                MarkdownLoader(PROJECT_ROOT / "frameworks" / "stride"),
                MarkdownLoader(PROJECT_ROOT / "prompts"),
                FrameworkNodes("stride"),
                shared,
                round_over(*drafts),
            )
        )

        for text in shared.values():
            assert text in instruction
        assert instruction.count('"id": "S-01"') == 1
        assert instruction.count('"id": "T-01"') == 1
        assert "{" not in instruction.split("## Procedure")[0].split("```")[-1]
        assert turn.role == "user"
        assert json.loads(turn.parts[0].text) == round_over(*drafts).summary()
        assert len(turn.parts) == 1, "a critic reads no closing"

    def test_the_summary_is_the_critics_user_turn(self):
        marks = AnalysisMarks(
            unverified_grounds=[UnverifiedGround(claim_id="S-01", index=0, reason="r")]
        )

        assert round_over(sample_draft("S-01"), marks=marks).summary() == {
            "framework": "stride",
            "draft_count": 1,
            "unverified_count": 1,
            "unresolved_mention_count": 0,
        }

    def test_a_closing_is_the_last_part_of_the_user_turn(self):
        instruction, turn = asyncio.run(
            node_request("Read {key}.", {"key": "this"}, {"a": 1}, "Last words.")
        )

        assert instruction == "Read this."
        assert [part.text for part in turn.parts] == ['{"a": 1}', "Last words."]
