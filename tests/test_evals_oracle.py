"""The ceiling instrument: what it measures, and what it must never become.

Three groups. The first holds the instrument out of the experiment — it reads
the answers, so a route that ran it would be graded on a reading it was given.
The second is the measurement itself: a perfect reading survives the
deterministic path whole, and a stage that starts losing rows fails here. The
third drives the two authoring rules that decide whether a row can be written
at all.
"""

from __future__ import annotations

from pathlib import Path

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

    def test_the_path_loses_no_required_row(self, charged) -> None:
        """The result #1003's step 4 asks for, held as a regression test.

        A row charged to ``resolution``, ``gate`` or ``scored`` is one the code
        between the model and the catalog dropped from a perfect reading. None
        of those stages may carry a row: if one does, a prompt change cannot
        recover it and the repair belongs in the code.
        """
        lost = [row for case in charged for row in case.lost]
        assert not lost, [
            f"{row.subject} {row.predicate}: {row.stage} ({row.why})" for row in lost
        ]

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
    """The two shapes a bundle cannot carry, each one a finding."""

    def test_an_unknown_graph_reference_is_inexpressible(self, charged) -> None:
        """A signed row saying the zone is unknown cannot be written at all.

        The resolver reads a graph-bound value as a handle, so the sentinel is
        refused as a dangling one. The rows sit outside the primary denominator
        and are counted rather than hidden.
        """
        assert sum(len(case.inexpressible) for case in charged)

    def test_a_component_no_source_places_needs_an_asserted_zone(self, charged) -> None:
        """The graph requires a zone, so something has to assert one."""
        assert sum(case.scaffolding for case in charged)

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

    @pytest.mark.parametrize("sentinel", (UNKNOWN, ABSENT))
    def test_a_sentinel_is_never_written_as_a_handle(self, sentinel: str) -> None:
        """ "Absent" is a value a predicate takes and never the name of a zone.

        A row saying the storage is unencrypted carries the sentinel and is
        written. A row saying the zone is unknown carries it where the resolver
        reads a handle, so the oracle leaves that row out and counts it.
        """
        case = next(one for one in load_corpus(CORPUS) if one.id.startswith("01"))
        reference = signed_reference(CORPUS, case)
        if reference is None:
            pytest.skip("case 01's facts are unsigned")
        written = oracle.author(case, reference)
        bound = [
            fact
            for fact in written.bundle.facts
            if fact.predicate in GRAPH_REFERENTS and fact.value == sentinel
        ]
        assert not bound
        assert written.inexpressible

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
