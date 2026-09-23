"""#926's predeclared promotion gates: what they read, and what they refuse to read.

Three groups. The first holds every gate against a real archived artifact, so a
gate cannot read a key nothing writes. The second drives the two readings the
table is built around — a resource gate one pair answers, and a quality gate
that stays unread until repeats measure a spread. The third is the rule the
whole table rests on: an absent figure is not a zero.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.harness import promotion
from evals.harness.run import COMMANDS

REPO_ROOT = Path(__file__).resolve().parents[1]
#: A sealed Baseline: `analysis` mode, scored, with its reports beside it. It
#: predates `AssertionBacking`, which is what makes it the right artifact to
#: hold the readers against — a gate must survive an artifact older than itself.
SEALED = (
    REPO_ROOT
    / "evals"
    / "baselines"
    / "6bff717-gpt-5.6-terra-24dda4db"
    / "mstarks01-e32ad9c5.json"
)


@pytest.fixture(scope="module")
def sealed() -> tuple[dict, dict]:
    if not SEALED.exists():
        pytest.skip("the sealed Baseline this reads is not in the tree")
    artifact = json.loads(SEALED.read_text(encoding="utf-8"))
    report = json.loads(
        (SEALED.with_suffix(".reports") / "01-payments-checkout.report.json").read_text(
            encoding="utf-8"
        )
    )
    return artifact, report


class TestEveryGateReadsARealArtifact:
    """A gate that names a key nothing writes fails here rather than in a run."""

    @pytest.mark.parametrize("name", sorted(promotion.GATES))
    def test_a_gate_reads_without_raising(self, name, sealed) -> None:
        artifact, report = sealed
        assert promotion.read_gate(name, artifact, report).gate == name

    @pytest.mark.parametrize("name", sorted(promotion.GATES))
    def test_a_gate_states_why_its_limit_is_that(self, name) -> None:
        assert promotion.GATES[name].why

    def test_every_ratio_gate_names_a_baseline_figure(self) -> None:
        for name, gate in promotion.GATES.items():
            if gate.unit in {"ratio", "sd"}:
                assert gate.against in promotion.BASELINE, name

    def test_the_baseline_names_the_run_it_was_measured_from(self) -> None:
        assert "evals/runs/" in promotion.BASELINE_RUN

    def test_the_command_is_registered(self) -> None:
        assert COMMANDS["gates"].run is promotion.command_gates

    def test_it_is_not_the_model_promotion_command(self) -> None:
        """`promote` blesses an execution identity; this reads a decision."""
        assert COMMANDS["promote"].run is not promotion.command_gates


class TestTheTwoKindsOfGate:
    """A ratio one pair answers, and a difference no single pair can."""

    def artifact(self, charge: float, coverage: float) -> dict:
        return {
            "node_charges": {"one": charge},
            "node_latency": {"one": {"total_ms": 1000}},
            "scores": [
                {
                    "metrics": {
                        "must_find_coverage": coverage,
                        "reference_coverage": coverage,
                    }
                }
            ],
            "losses_aggregate": {"assertion_backed": {"matched": 4}},
        }

    def test_a_charge_inside_the_budget_passes(self) -> None:
        held = self.artifact(promotion.BASELINE["charge_usd"], 0.5)
        assert promotion.read_gate("charge", held, {}).verdict == "pass"

    def test_a_charge_over_the_budget_fails(self) -> None:
        held = self.artifact(promotion.BASELINE["charge_usd"] * 2, 0.5)
        assert promotion.read_gate("charge", held, {}).verdict == "fail"

    def test_a_quality_gate_without_a_spread_is_unread(self) -> None:
        held = self.artifact(0.1, 0.0)
        reading = promotion.read_gate("must-find-coverage", held, {})

        assert reading.verdict == "inconclusive"
        assert reading.measured == 0.0

    def test_a_spread_gives_the_limit_its_scale(self) -> None:
        held = self.artifact(0.1, promotion.BASELINE["must_find_coverage"] - 0.05)

        inside = promotion.read_gate("must-find-coverage", held, {}, spread=0.1)
        outside = promotion.read_gate("must-find-coverage", held, {}, spread=0.01)

        assert inside.verdict == "pass"
        assert outside.verdict == "fail"

    def test_the_decision_reads_every_gate(self) -> None:
        readings = promotion.decide(self.artifact(0.1, 0.5), {})
        assert {row.gate for row in readings} == set(promotion.GATES)


class TestAnAbsentFigureIsNotAZero:
    """The rule the table rests on, and the one an artifact can break quietly."""

    def test_an_artifact_without_the_backing_field_is_unread(self, sealed) -> None:
        """The sealed Baseline predates `AssertionBacking`, so it backs nothing."""
        artifact, report = sealed
        reading = promotion.read_gate("backs-claims", artifact, report)

        assert reading.verdict == "inconclusive"
        assert reading.measured is None

    def test_a_report_that_ran_no_assertion_pass_is_unread(self, sealed) -> None:
        artifact, _ = sealed
        reading = promotion.read_gate("unsupported-assertions", artifact, {})

        assert reading.verdict == "inconclusive"

    def test_an_unread_gate_is_named_in_the_report(self, sealed) -> None:
        artifact, report = sealed
        printed = promotion.render(promotion.decide(artifact, report))

        assert "unread" in printed
        assert "`backs-claims` unread" in printed
