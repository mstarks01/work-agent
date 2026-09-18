"""The ceiling instrument: what it measures, and what it must never become.

Three groups. The first holds the instrument out of the experiment — it reads
the answers, so a route that ran it would be graded on a reading it was given.
The second is the measurement itself: a perfect reading survives the
deterministic path whole, and a stage that starts losing rows fails here. The
third drives the authoring rules that decide whether a row can be written at
all, and that a perfect reading invents no placement of its own.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest

from analysis_service.assertions import ABSENT, UNKNOWN
from analysis_service.factbundle import GRAPH_REFERENTS
from analysis_service.system_model import TrustBoundary
from evals.harness import oracle
from evals.harness.arms import ARMS
from evals.harness.reference import load_corpus
from evals.harness.replay import signed_reference
from evals.harness.run import COMMANDS

CORPUS = Path(__file__).resolve().parents[1] / "evals" / "corpus"


@pytest.fixture(scope="module")
def charged() -> list[oracle.CaseCharge]:
    found = oracle.charges(CORPUS)
    if not found:
        pytest.skip("no corpus case carries a signed reference")
    return found


class TestItIsNotAnArm:
    """An oracle is a ceiling, and a ceiling is never a score."""

    def test_no_arm_is_the_oracle(self) -> None:
        """#1003 compares routes that read the sources, and this one is given them."""
        assert "oracle" not in ARMS

    def test_it_runs_no_model(self) -> None:
        """The command reads the corpus and computes; nothing binds a provider."""
        source = Path(oracle.__file__).read_text(encoding="utf-8")
        assert "binding" not in source
        assert "Deployment" not in source

    def test_the_command_is_registered(self) -> None:
        assert COMMANDS["oracle"].run is oracle.command_oracle


class TestTheCeiling:
    """What the deterministic path does to a reading that is already right."""

    #: Every required row a perfect reading loses, and the rule that loses it.
    #: Empty: a component the sources never place enters the graph unplaced
    #: under
    #: [ADR 0039](../docs/adr/0039-a-crossing-a-model-cannot-decide-is-still-a-lead.md)
    #: rule 1, so the interaction to it resolves and the fact on that
    #: interaction survives. Listed rather than tolerated — an entry here is the
    #: deterministic path costing a required fact, and the repair belongs in the
    #: code rather than in a prompt.
    PRICED_LOSSES: ClassVar[dict[tuple[str, str], str]] = {}

    def test_the_path_loses_only_the_rows_the_contract_prices(self, charged) -> None:
        """What the code between the model and the catalog drops from a perfect reading.

        A row charged to ``resolution``, ``gate`` or ``scored`` is one no prompt
        change can recover, so the repair would belong in the code. The one
        exception is a loss the placement contract states and prices, which
        :data:`PRICED_LOSSES` names in full.
        """
        lost = {
            (row.subject, row.predicate): row.why
            for case in charged
            for row in case.lost
        }

        assert lost == self.PRICED_LOSSES

    def test_every_required_row_is_charged_exactly_once(self, charged) -> None:
        for case in charged:
            rows = [row.row for row in case.rows]
            assert len(rows) == len(set(rows))
            assert all(row.stage in oracle.STAGES for row in case.rows)

    def test_the_machinery_stages_exclude_the_oracle_s_own_limit(self) -> None:
        """``not-authored`` is this instrument's limit, never the code's."""
        assert "not-authored" not in oracle.MACHINERY
        assert "found" not in oracle.MACHINERY
        assert oracle.MACHINERY == {"resolution", "gate", "scored"}

    def test_it_reports_what_the_fact_endpoint_cannot_see(self, charged) -> None:
        """A case can recover every row and still lose components to placement."""
        assert any(case.elements[0] < case.elements[1] for case in charged)
        rendered = oracle.render(charged)
        assert "| elements | flows |" in rendered


class TestWhatTheOracleCannotWrite:
    """What a bundle cannot carry, and what a perfect reading declines to invent."""

    def test_every_signed_row_can_be_written(self, charged) -> None:
        """Nothing a reviewer signed is outside what a bundle can carry.

        A row saying the zone is unknown is the shape that tests it: the
        sentinel is an epistemic value and the resolver reads it as one, on a
        graph-bound predicate as anywhere else.
        """
        assert not [row for case in charged for row in case.inexpressible]

    def test_a_perfect_reading_asserts_no_placement_of_its_own(self, charged) -> None:
        """ADR 0039 rule 1, at the ceiling: nothing has to invent a zone.

        The oracle writes the reference's own rows and no others, so every
        ``network-membership`` it proposes is one a source states. A component
        the sources place nowhere reaches the graph unplaced.
        """
        references = {
            case.id: signed_reference(CORPUS, case) for case in load_corpus(CORPUS)
        }
        assert all(references.values()), "every corpus case carries a signed reference"

        placements = 0
        signed_rows = 0
        for case in load_corpus(CORPUS):
            reference = references[case.id]
            assert reference is not None
            placements += sum(
                1
                for fact in oracle.author(case, reference).bundle.facts
                if fact.predicate == "network-membership"
            )
            signed_rows += sum(
                1
                for entry in reference.entries
                if entry.predicate == "network-membership"
            )

        assert placements == signed_rows

    def test_every_blessed_element_class_has_a_role(self) -> None:
        """A class or a kind added to the model raises rather than being skipped."""
        for case in load_corpus(CORPUS):
            zones, zoned = oracle._roles(case.model)
            assert zones or zoned

    def test_a_zone_kind_outside_the_table_raises(self) -> None:
        held = TrustBoundary(
            id="boundary:odd",
            name="odd",
            kind="network",
            source_excerpt="odd",
            source_label="note",
        )
        with pytest.raises(KeyError):
            oracle.ROLE_FOR[TrustBoundary.__name__, "not-a-kind"]
        assert oracle.ROLE_FOR[TrustBoundary.__name__, held.kind] == "network-zone"

    def test_an_own_subject_is_named_rather_than_keyed(self) -> None:
        """A bundle names a principal in the source's words; the catalog slugs it."""
        assert oracle.unslugged("principal:shopper-accounts") == "shopper-accounts"
        assert oracle.unslugged("store:orders-db") == "store:orders-db"

    def test_a_phrasing_search_falls_back_to_the_longest_word(self) -> None:
        """Case 04 names its public zone only in "expose on the internet"."""
        tried = oracle.phrasings("public internet")
        assert tried[0] == "public internet"
        assert "internet" in tried


class TestTheSentinelsAreValues:
    """The rule the bundle reads two ways, named where it is tested."""

    def test_a_sentinel_is_written_on_a_graph_bound_predicate(self) -> None:
        """ "We do not know which zone" is a fact, and a bundle carries it.

        Reading the sentinel as the name of a zone refuses the row as a
        dangling one, which loses every signed row of that shape — twenty of
        them across the five signed cases.
        """
        case = next(one for one in load_corpus(CORPUS) if one.id.startswith("01"))
        reference = signed_reference(CORPUS, case)
        if reference is None:
            pytest.skip("case 01's facts are unsigned")
        written = oracle.author(case, reference)
        bound = [
            fact
            for fact in written.bundle.facts
            if fact.predicate in GRAPH_REFERENTS and fact.value == UNKNOWN
        ]
        assert bound
        assert not written.inexpressible

    def test_a_sentinel_on_an_ordinary_predicate_is_written(self) -> None:
        case = next(one for one in load_corpus(CORPUS) if one.id.startswith("01"))
        reference = signed_reference(CORPUS, case)
        if reference is None:
            pytest.skip("case 01's facts are unsigned")
        written = oracle.author(case, reference)
        assert any(
            fact.value in (UNKNOWN, ABSENT) and fact.predicate not in GRAPH_REFERENTS
            for fact in written.bundle.facts
        )
