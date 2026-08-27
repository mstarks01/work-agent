"""What a standing does to a number, and what the agreement report says.

Two properties carry this module. **Standing selects, never weighs**: a series
reads a set of voters and nothing about the standing changes what a vote means
inside it. And **the report decides nothing**: it prints a comparison and its
sample size, and a promotion stays a roster edit a person makes.

Deterministic and free of provider calls, so it gates on every PR.
"""

from __future__ import annotations

from evals.harness.fingerprint import Components
from evals.harness.ledger import Ledger, cast
from evals.harness.roster import Roster
from evals.harness.standings import (
    PRIMARY,
    RECOMMENDED_SAMPLE,
    SERIES,
    agreement,
    classify,
    narrow,
    render,
)


def components(target="process:a"):
    return Components("stride", "information-disclosure", (target,), verb="read")


def vote(voter, verdict="up", reason=None, target="process:a"):
    return cast(components(target), "01", verdict, voter, reason=reason)


def roster(**people):
    return Roster(standings=dict(people), aliases={})


class TestTheSeriesTable:
    def test_the_primary_series_is_maintainers_only(self):
        assert PRIMARY == "maintainer"
        assert SERIES[PRIMARY] == ("maintainer",)

    def test_every_series_reads_a_subset_of_the_closed_standings(self):
        for included in SERIES.values():
            assert set(included) <= {"maintainer", "contributor"}

    def test_the_all_series_reads_both(self):
        assert set(SERIES["all"]) == {"maintainer", "contributor"}


class TestNarrowing:
    def table(self):
        return roster(ada="contributor", sam="maintainer")

    def test_the_primary_series_drops_a_contributors_vote(self):
        ledger = Ledger(votes=[vote("ada"), vote("sam", target="store:b")])
        kept = narrow(ledger, self.table(), SERIES[PRIMARY])
        assert [entry.voter for entry in kept] == ["sam"]

    def test_the_all_series_keeps_both(self):
        ledger = Ledger(votes=[vote("ada"), vote("sam", target="store:b")])
        kept = narrow(ledger, self.table(), SERIES["all"])
        assert [entry.voter for entry in kept] == ["ada", "sam"]

    def test_an_unrostered_voter_is_read_by_no_series(self):
        """Deny by default: no standing means no series can state what it read."""
        ledger = Ledger(votes=[vote("stranger")])
        for included in SERIES.values():
            assert not list(narrow(ledger, self.table(), included))

    def test_narrowing_changes_no_vote(self):
        """Standing selects which votes a series reads, and does nothing else."""
        rejection = vote("sam", "down", reason="not-a-threat")
        kept = list(narrow(Ledger(votes=[rejection]), self.table(), SERIES[PRIMARY]))
        assert kept == [rejection]
        assert kept[0].counts_against_analysis


class TestSubstanceClasses:
    def test_a_substance_rejection_is_rejected(self):
        assert classify(vote("ada", "down", reason="not-a-threat")) == "rejected"

    def test_a_style_rejection_is_pooled(self):
        """The finding stays in the pool, so taste cannot look like disagreement."""
        assert classify(vote("ada", "down", reason="poorly-written")) == "pooled"

    def test_an_upvote_is_pooled(self):
        assert classify(vote("ada", "up")) == "pooled"

    def test_unsure_and_needs_evidence_are_open(self):
        assert classify(vote("ada", "unsure")) == "open"
        assert classify(vote("ada", "needs-evidence")) == "open"


class TestTheAgreementReport:
    def table(self):
        return roster(ada="contributor", sam="maintainer")

    def test_two_voters_who_agree_read_as_full_agreement(self):
        ledger = Ledger(votes=[vote("ada"), vote("sam")])
        pair = agreement(ledger, self.table())[0]
        assert (pair.compared, pair.agreed, pair.excluded) == (1, 1, 0)
        assert pair.rate == 1.0

    def test_a_substance_disagreement_counts_against_the_rate(self):
        ledger = Ledger(votes=[vote("ada"), vote("sam", "down", reason="not-a-threat")])
        pair = agreement(ledger, self.table())[0]
        assert (pair.compared, pair.agreed) == (1, 0)
        assert pair.rate == 0.0

    def test_a_style_objection_still_agrees_with_an_upvote(self):
        """Both say the finding is real, so the substance classes match."""
        ledger = Ledger(
            votes=[vote("ada"), vote("sam", "down", reason="poorly-written")]
        )
        assert agreement(ledger, self.table())[0].rate == 1.0

    def test_an_open_answer_is_excluded_rather_than_a_disagreement(self):
        ledger = Ledger(votes=[vote("ada"), vote("sam", "unsure")])
        pair = agreement(ledger, self.table())[0]
        assert (pair.compared, pair.excluded) == (0, 1)
        assert pair.rate is None

    def test_only_the_findings_both_answered_are_compared(self):
        ledger = Ledger(votes=[vote("ada"), vote("sam"), vote("ada", target="store:b")])
        assert agreement(ledger, self.table())[0].compared == 1

    def test_a_pair_with_no_overlap_is_not_reported(self):
        ledger = Ledger(votes=[vote("ada"), vote("sam", target="store:b")])
        assert agreement(ledger, self.table()) == []

    def test_a_correction_is_read_at_its_latest_event(self):
        """The live verdict decides, so a reviewer who changed their mind agrees."""
        ledger = Ledger(
            votes=[
                vote("ada", "down", reason="not-a-threat"),
                vote("ada"),
                vote("sam"),
            ]
        )
        assert agreement(ledger, self.table())[0].rate == 1.0

    def test_an_unrostered_voter_is_left_out(self):
        ledger = Ledger(votes=[vote("stranger"), vote("sam")])
        assert agreement(ledger, self.table()) == []

    def test_a_crossing_pair_is_flagged_for_the_promotion_decision(self):
        ledger = Ledger(votes=[vote("ada"), vote("sam")])
        assert agreement(ledger, self.table())[0].crosses_the_line
        both = roster(ada="maintainer", sam="maintainer")
        assert not agreement(ledger, both)[0].crosses_the_line


class TestTheRendering:
    def test_no_overlap_says_so_rather_than_printing_an_empty_table(self):
        assert "nothing to compare" in render([])

    def test_a_thin_sample_is_named_and_never_refused(self):
        ledger = Ledger(votes=[vote("ada"), vote("sam")])
        text = render(agreement(ledger, roster(ada="contributor", sam="maintainer")))
        assert f"fewer than {RECOMMENDED_SAMPLE}" in text
        assert "weigh it yourself" in text

    def test_the_report_states_that_it_promotes_nobody(self):
        ledger = Ledger(votes=[vote("ada"), vote("sam")])
        text = render(agreement(ledger, roster(ada="contributor", sam="maintainer")))
        assert "Nothing here promotes anybody" in text
        assert "voters.toml" in text
