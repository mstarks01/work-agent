"""The instrument that gives a style objection somewhere to land.

``STYLE_REASONS`` exists so that a reviewer who dislikes a sentence cannot move
recall. That guarantee is worth nothing if the objection then evaporates, which
is what happened before this module: the ledger stored the reason and no
command read it.

So these tests are mostly about the split holding in both directions — a style
down-vote counts here and nowhere else, a substance down-vote counts everywhere
else and not here — and about the denominator being what a person answered.

Offline: a ledger, a report and no provider anywhere.
"""

from __future__ import annotations

import pytest

from analysis_service.claims import FrameworkName
from analysis_service.frameworks.stride.record import StrideCategory
from evals.harness import writing
from evals.harness.content import prose as prose_digest
from evals.harness.fingerprint import key_claim
from evals.harness.ledger import Ledger, Vote
from tests.eval_factories import SAMPLE_CONTENT, produced_threat
from tests.factories import sample_report
from tests.test_asvs import _block as asvs_block
from tests.test_asvs import sample_asvs_claim

FLOWS: dict[str, tuple[str, str]] = {}
CASE = "01-payments-checkout"


def vote(
    lane: str,
    verdict: str,
    reason: str | None = None,
    *,
    voter: str = "ada",
    framework: FrameworkName = "stride",
    verb: str | None = "impersonate",
    identifier: str | None = None,
    element_ids: tuple[str, ...] = ("entity:customer",),
    prose: str | None = None,
) -> Vote:
    """One vote on the finding the same components would key.

    ``prose`` defaults to the words :func:`stride_report` gives that lane's
    claim, which is what a reviewer would have read. Passing another value is
    how a test says the finding has been reworded since.

    The structural digest is fixed and not what these tests are about: a style
    objection binds to the prose digest, and the two are separate values for
    exactly that reason.
    """
    value, components = key_claim(
        framework, CASE, lane, element_ids, FLOWS, verb=verb, identifier=identifier
    )
    return Vote(
        fingerprint=value,
        components=components,
        content=SAMPLE_CONTENT,
        prose=prose if prose is not None else words(lane),
        case=CASE,
        verdict=verdict,  # type: ignore[arg-type]
        voter=voter,
        recorded="2026-08-20T10:00:00Z",
        reason=reason,
    )


def words(lane: str) -> str:
    """The prose digest of the claim :func:`stride_report` puts in ``lane``."""
    index = LANES.index(lane) + 1
    return prose_digest(
        produced_threat(
            index,
            lane,  # type: ignore[arg-type]
            f"Order price rewritten {index}",
            element_ids=("entity:customer",),
        )
    )


#: One lane each, so each threat is its own identity. Five claims in one lane
#: over one element with one verb are one fingerprint, which is the rule
#: working rather than a fixture worth writing.
LANES: tuple[StrideCategory, ...] = (
    "tampering",
    "spoofing",
    "repudiation",
    "information-disclosure",
    "denial-of-service",
)


def stride_report(count: int = 1):
    """A report of ``count`` claims, the first in ``tampering``.

    Every claim cites ``entity:customer`` and carries the factory's default
    verb, ``impersonate``, which is what the votes below key against — the
    identity rule reads the verb, and a vote assigned a different one lands on
    a different finding.
    """
    return sample_report(
        threats=[
            produced_threat(
                index,
                LANES[index - 1],
                f"Order price rewritten {index}",
                element_ids=("entity:customer",),
            )
            for index in range(1, count + 1)
        ]
    )


def measure(report, votes: Ledger) -> writing.CaseWriting:
    block = report.analyses[0]
    return writing.measure_case(
        "01-payments-checkout", block.framework, block.claims, FLOWS, votes
    )


def test_a_style_downvote_is_an_objection():
    row = measure(
        stride_report(), Ledger([vote("tampering", "down", "poorly-written")])
    )

    assert (row.answered, row.objections) == (1, 1)
    assert row.by_reason == {"poorly-written": 1}
    assert row.objection_rate == 1.0


def test_a_substance_downvote_is_answered_but_never_an_objection():
    """The split, in the direction that protects the writing number.

    A reviewer who says the finding is not a threat has said nothing about how
    it reads, and counting them here would let a wrong finding look badly
    written.
    """
    row = measure(stride_report(), Ledger([vote("tampering", "down", "not-a-threat")]))

    assert (row.answered, row.objections) == (1, 0)
    assert row.by_reason == {}


def test_an_upvote_is_answered_and_lowers_the_rate():
    """The rate is over answers, so an up-vote is the other half of it."""
    votes = Ledger(
        [
            vote("tampering", "up"),
            vote("tampering", "down", "too-vague", voter="sam"),
        ]
    )
    row = measure(stride_report(), votes)

    # One finding, two voters: one answered finding, and somebody objected.
    assert (row.answered, row.objections) == (1, 1)


def test_the_denominator_is_what_was_answered_not_what_was_produced():
    """A sweep that writes more must not read as better written.

    Dividing by ``produced`` would fall from 1.0 to 0.2 here without a single
    reviewer changing their mind.
    """
    row = measure(stride_report(5), Ledger([vote("tampering", "down", "too-vague")]))

    assert row.produced == 5
    assert (row.answered, row.objections) == (1, 1)
    assert row.objection_rate == 1.0


def test_an_empty_ledger_answers_nothing_rather_than_objecting_to_nothing():
    row = measure(stride_report(3), Ledger())

    assert (row.produced, row.answered, row.objections) == (3, 0, 0)
    assert row.objection_rate == 0.0


def test_a_superseded_objection_is_not_counted():
    """A reviewer who changes their mind appends; the live verdict is the last."""
    votes = Ledger(
        [
            vote("tampering", "down", "poorly-written"),
            vote("tampering", "up"),
        ]
    )
    row = measure(stride_report(), votes)

    assert (row.answered, row.objections) == (1, 0)


class TestEveryPackageIsGradedOnItsProse:
    """Neutral by construction: prose quality is nobody's framework's property."""

    def test_an_asvs_claim_is_measured_under_its_own_key(self):
        """ASVS keys on its chapter, its elements and its requirement."""
        report = sample_report(analyses=[asvs_block(1, [sample_asvs_claim()])])
        votes = Ledger(
            [
                vote(
                    "authentication",
                    "down",
                    "unhelpful-mitigation",
                    framework="asvs",
                    verb=None,
                    identifier="V6.2.1",
                    element_ids=(),
                    prose=prose_digest(sample_asvs_claim()),
                )
            ]
        )
        row = measure(report, votes)

        assert row.framework == "asvs"
        assert (row.answered, row.objections) == (1, 1)

    def test_a_sweep_of_two_packages_reports_a_row_for_each(self):
        report = sample_report(
            analyses=[
                stride_report().analyses[0],
                asvs_block(1, [sample_asvs_claim()]),
            ]
        )
        rows = [
            writing.measure_case(
                "01-payments-checkout", block.framework, block.claims, FLOWS, Ledger()
            )
            for block in report.analyses
        ]

        assert [row.framework for row in rows] == ["stride", "asvs"]


class TestTheAggregate:
    """What the sweep prints and writes, folded over the rows."""

    @pytest.fixture
    def rows(self):
        return (
            writing.CaseWriting("01", "stride", 10, 4, 2, by_reason={"too-vague": 2}),
            writing.CaseWriting("02", "stride", 6, 2, 0),
            writing.CaseWriting("02", "asvs", 8, 2, 1, by_reason={"wrong-severity": 1}),
        )

    def test_the_rate_is_over_every_answer_in_the_sweep(self, rows):
        totals = writing.aggregate(rows)

        assert (totals["answered"], totals["objections"]) == (8, 3)
        assert totals["objection_rate"] == 0.375

    def test_each_package_keeps_its_own_totals(self, rows):
        by_framework = writing.aggregate(rows)["by_framework"]

        assert by_framework["stride"] == {
            "produced": 16,
            "answered": 6,
            "objections": 2,
            "carried": 0,
        }
        assert by_framework["asvs"]["objections"] == 1

    def test_the_reasons_are_pooled_for_the_printed_list(self, rows):
        assert writing.aggregate(rows)["by_reason"] == {
            "too-vague": 2,
            "wrong-severity": 1,
        }

    def test_an_unreviewed_sweep_says_so_rather_than_printing_a_zero(
        self, rows, capsys
    ):
        """A 0.00 nobody voted on reads as praise. The line has to say why."""
        cold = tuple(
            writing.CaseWriting(row.case_id, row.framework, row.produced, 0, 0)
            for row in rows
        )
        writing.render(cold)

        assert "nobody has voted" in capsys.readouterr().out

    def test_the_artifact_carries_the_rows_and_the_fold(self, rows):
        block = writing.artifact(rows)

        assert [row["case"] for row in block["writing"]] == ["01", "02", "02"]
        assert block["writing_aggregate"]["objections"] == 3


def test_two_asvs_rulings_in_one_chapter_are_not_one_finding():
    """The collapse version 3 closes, read through this instrument.

    Under version 1 both rulings keyed alike, so a style objection to one
    counted the other as answered too.
    """
    report = sample_report(
        analyses=[
            asvs_block(
                1,
                [
                    sample_asvs_claim("v5.0.0-6.2.1"),
                    sample_asvs_claim("v5.0.0-6.2.2"),
                ],
            )
        ]
    )
    votes = Ledger(
        [
            vote(
                "authentication",
                "down",
                "too-vague",
                framework="asvs",
                verb=None,
                identifier="V6.2.1",
                element_ids=(),
                prose=prose_digest(sample_asvs_claim("v5.0.0-6.2.1")),
            )
        ]
    )
    row = measure(report, votes)

    assert (row.produced, row.answered, row.objections) == (2, 1, 1)


class TestAnObjectionIsAboutWordsThatStillExist:
    """A style vote judges prose. A rewrite leaves it judging nothing.

    The 2026-09-09 audit replaced every retained case 01 title and explanation
    and no fingerprint moved, so before this the instrument counted a verdict
    on text that no longer existed.
    """

    def test_a_vote_on_other_words_is_carried_and_not_answered(self):
        row = measure(
            stride_report(),
            Ledger(
                [vote("tampering", "down", "poorly-written", prose=words("spoofing"))]
            ),
        )

        assert (row.answered, row.objections, row.carried) == (0, 0, 1)
        assert row.by_reason == {}

    def test_a_carried_vote_leaves_the_rate_uncomputed_rather_than_perfect(self):
        """Counting it as answered-and-unobjectionable would read as praise."""
        row = measure(
            stride_report(),
            Ledger([vote("tampering", "down", "too-vague", prose=words("spoofing"))]),
        )

        assert row.objection_rate == 0.0
        assert row.carried == 1, "the zero has a reason beside it"

    def test_one_voter_on_these_words_and_one_on_older_words(self):
        votes = Ledger(
            [
                vote("tampering", "down", "poorly-written"),
                vote("tampering", "up", voter="sam", prose=words("spoofing")),
            ]
        )
        row = measure(stride_report(), votes)

        assert (row.answered, row.objections, row.carried) == (1, 1, 0)

    def test_the_aggregate_folds_the_carried_count(self):
        rows = (
            writing.CaseWriting("01", "stride", 10, 4, 2, carried=3),
            writing.CaseWriting("02", "stride", 6, 2, 0, carried=1),
        )

        totals = writing.aggregate(rows)

        assert totals["carried"] == 4
        assert totals["by_framework"]["stride"]["carried"] == 4

    def test_a_sweep_answered_only_on_older_words_says_so(self, capsys):
        writing.render((writing.CaseWriting("01", "stride", 10, 0, 0, carried=2),))

        assert "cast on other words" in capsys.readouterr().out
