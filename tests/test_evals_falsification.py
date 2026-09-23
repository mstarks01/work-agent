"""The falsification fixtures #926's acceptance list asks for.

Three groups. The first holds the probe table against the acceptance list it
answers: every criterion has a probe or a recorded reason, and neither table
quietly grows an entry the other does not know about. The second runs every
probe and holds each to what it declared. The third is what the suite exists to
stop — a falsification probe that costs nothing, and a probe mistaken for an
arm.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from analysis_service.assertions import GATE_REFUSALS
from evals.harness import falsify
from evals.harness.arms import ARMS
from evals.harness.replay import ROW_FATES
from evals.harness.run import COMMANDS

CORPUS = Path(__file__).resolve().parents[1] / "evals" / "corpus"


@pytest.fixture(scope="module")
def probed() -> dict[str, falsify.Outcome]:
    return {row.probe: row for row in falsify.outcomes(CORPUS)}


class TestTheTableAnswersTheAcceptanceList:
    """#926's criteria are the registry; a probe that answers none is adrift."""

    def test_every_criterion_is_probed_or_excused(self) -> None:
        assert set(falsify.CRITERIA) == set(falsify.PROBES) | set(falsify.UNPROBED)

    def test_no_criterion_is_both(self) -> None:
        assert not set(falsify.PROBES) & set(falsify.UNPROBED)

    @pytest.mark.parametrize("name", sorted(falsify.PROBES))
    def test_every_probe_declares_a_registered_fate(self, name: str) -> None:
        assert set(falsify.PROBES[name].fates) <= set(ROW_FATES)

    @pytest.mark.parametrize("name", sorted(falsify.PROBES))
    def test_every_probe_declares_a_registered_refusal(self, name: str) -> None:
        assert set(falsify.PROBES[name].refuses) <= GATE_REFUSALS

    def test_a_probe_is_never_an_arm(self) -> None:
        """A probe reads the endpoint; it does not answer #1003's question."""
        assert not set(falsify.PROBES) & set(ARMS)

    def test_the_command_is_registered(self) -> None:
        assert COMMANDS["falsify"].run is falsify.command_falsify


class TestEveryProbeHolds:
    """The instrument's own regression suite, row by row."""

    @pytest.mark.parametrize("name", sorted(falsify.PROBES))
    def test_the_corruption_costs_what_it_declared(self, name, probed) -> None:
        assert probed[name].broke == ()

    def test_a_perfect_reading_costs_nothing(self, probed) -> None:
        """Every other probe's loss is its own corruption's, not the fixture's."""
        perfect = probed["perfect-reading"]
        assert perfect.recovered == perfect.required
        assert perfect.refused == ()

    def test_a_changed_source_is_refused_without_touching_the_catalog(
        self, probed
    ) -> None:
        """The corruption is entirely in what the submitter wrote afterwards."""
        row = probed["changed-source"]

        assert row.refused == ("stale-digest",)
        assert row.recovered == row.required

    def test_the_command_reports_every_probe(self, capsys) -> None:
        assert falsify.command_falsify(_arguments()) == 0
        printed = capsys.readouterr().out
        for name in falsify.PROBES:
            assert f"`{name}`" in printed
        for name in falsify.UNPROBED:
            assert f"no probe for `{name}`" in printed


class TestAProbeThatCostsNothingFails:
    """The one thing this suite cannot let through."""

    def test_a_partly_reached_criterion_says_what_it_misses(self) -> None:
        """A criterion with several halves names the ones no corruption reaches."""
        partial = {name for name, probe in falsify.PROBES.items() if probe.note}
        assert partial == {"identity-and-provenance", "changed-source"}

    def test_only_the_positive_controls_cost_nothing(self) -> None:
        controls = {name for name, probe in falsify.PROBES.items() if probe.control}
        assert controls == {"perfect-reading", "valid-inference"}

    def test_every_corruption_costs_some_instrument(self, probed) -> None:
        for name, probe in falsify.PROBES.items():
            if probe.control:
                continue
            row = probed[name]
            cost = row.lost or row.refused or row.elements
            assert cost or set(row.fates) - {"found"}, f"{name} cost nothing"

    def test_what_required_fact_recall_cannot_see_is_named(self) -> None:
        """Two corruptions the endpoint scores as clean runs, and why.

        `empty-citation` removes a span the matcher never reads, so the gate is
        the only reader that can refuse it. `identity-and-provenance` and
        `changed-source` corrupt a citation the same way — the words a row
        rests on, not the fact it states. `deleted-parallel-interaction`
        deletes an interaction whose every signed fact is a hedged unknown, and
        the endpoint's denominator is the stated rows, so the loss reads in the
        row and element fates and never in recall.
        """
        blind = {name for name, probe in falsify.PROBES.items() if probe.endpoint_blind}
        assert blind == {
            "empty-citation",
            "deleted-parallel-interaction",
            "identity-and-provenance",
            "changed-source",
        }

    def test_a_probe_that_stops_costing_is_a_failure(self, probed) -> None:
        """A declaration the outcome no longer meets breaks, rather than relaxing."""
        weakened = falsify.Probe(
            case_id="01-payments-checkout", corrupt=falsify._mfa_enforced, loses=0
        )
        assert falsify._broke(weakened, probed["mfa-enforced"])


def _arguments():
    import argparse

    return argparse.Namespace(corpus=str(CORPUS))
