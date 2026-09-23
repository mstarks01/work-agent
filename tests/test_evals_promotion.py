"""#926's predeclared promotion gates: what they read, and what they refuse to read.

Five groups. The first holds every gate against a real archived artifact, so a
gate cannot read a key nothing writes. The second drives the two readings the
table is built around — a resource gate one pair answers, and a quality gate
that stays unread until repeats measure a spread. The third is the rule the
whole table rests on: an absent figure is not a zero. The fourth keeps span
validity apart from semantic support, and the fifth holds the measurement
design #926 settled as gates nobody can read until it is measured.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from analysis_service.assertions import (
    Assertion,
    AssertionCatalog,
    AssertionRecord,
    CatalogIssue,
    Subject,
)
from analysis_service.sources import text_digest
from evals.harness import promotion
from evals.harness.reference import load_corpus
from evals.harness.replay import signed_reference
from evals.harness.run import COMMANDS

REPO_ROOT = Path(__file__).resolve().parents[1]
FLOW = "flow:entity:shopper>process:storefront-api>place-order"
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

    def test_a_measured_zero_spread_is_a_scale(self) -> None:
        """Repeats that agreed exactly are a measurement, not a missing one."""
        baseline = promotion.BASELINE["must_find_coverage"]
        same = promotion.read_gate(
            "must-find-coverage", self.artifact(0.1, baseline), {}, spread=0.0
        )
        lower = promotion.read_gate(
            "must-find-coverage", self.artifact(0.1, baseline - 0.05), {}, spread=0.0
        )

        assert same.verdict == "pass"
        assert lower.verdict == "fail"

    def test_the_decision_reads_every_gate(self) -> None:
        readings = promotion.decide(self.artifact(0.1, 0.5), {})
        assert {row.gate for row in readings} == {*promotion.GATES, *promotion.PENDING}


class TestAnAbsentFigureIsNotAZero:
    """The rule the table rests on, and the one an artifact can break quietly."""

    def test_an_artifact_without_the_backing_field_is_unread(self, sealed) -> None:
        """The sealed Baseline predates `AssertionBacking`, so it backs nothing."""
        artifact, report = sealed
        reading = promotion.read_gate("backs-claims", artifact, report)

        assert reading.verdict == "inconclusive"
        assert reading.measured is None

    @pytest.mark.parametrize("gate", ["structural-refusals"])
    def test_a_report_that_ran_no_assertion_pass_is_unread(self, sealed, gate) -> None:
        artifact, _ = sealed
        reading = promotion.read_gate(gate, artifact, {})

        assert reading.verdict == "inconclusive"

    def test_a_record_this_schema_refuses_is_unread(self, sealed) -> None:
        """An archived record from another schema is an unread gate, not a crash."""
        artifact, _ = sealed
        report = _report(_row(), proposed=1)
        report["assertions"]["written_by_an_older_tree"] = True
        readings = {row.gate: row for row in promotion.decide(artifact, report)}

        for gate in ("structural-refusals", "required-fact-recall"):
            assert readings[gate].verdict == "inconclusive"
            assert "does not validate" in readings[gate].why


def _report(*entries: Assertion, proposed: int, issues=()) -> dict:
    subject = Subject(id=FLOW, type="interaction", label="place order")
    record = AssertionRecord(
        proposed=proposed,
        catalog=AssertionCatalog(subjects=[subject], entries=list(entries)),
        issues=list(issues),
    )
    return {"assertions": record.model_dump(mode="json")}


def _row(**overrides) -> Assertion:
    fields = {
        "subject": FLOW,
        "predicate": "authentication-mechanism",
        "value": "password",
        "basis": "inferred",
        "explanation": "fixture",
    }
    return Assertion(**{**fields, **overrides})


class TestSupportIsNotSpanValidity:
    """#926's audit: the gate called span validity the unsupported rate."""

    def test_the_structural_rate_counts_rows_not_reasons(self) -> None:
        """One row, three reasons, and a contradiction that refuses nothing."""
        issues = [
            CatalogIssue(code=code, message="", row=0)
            for code in ("unverifiable-span", "ambiguous-span", "missing-scope")
        ]
        issues.append(CatalogIssue(code="graph-contradiction", message=""))
        reading = promotion.read_gate(
            "structural-refusals", {}, _report(proposed=4, issues=issues)
        )

        assert reading.measured == 0.25

    def test_the_four_support_states_are_reported_together(self) -> None:
        rows = [
            _row(),
            _row(value="token", assessment="supported", assessor="human:1"),
            _row(value="mtls", assessment="unsupported", assessor="human:1"),
            _row(value="key", assessment="unresolved", assessor="human:1"),
        ]
        shares = promotion.support_shares(_report(*rows, proposed=4))

        assert shares is not None
        assert tuple(shares) == promotion.SUPPORT_STATES
        assert dict(shares) == dict.fromkeys(promotion.SUPPORT_STATES, 0.25)

    def test_no_catalog_has_no_shares(self) -> None:
        assert promotion.support_shares({}) is None


class TestTheDesignIsDeclaredBeforeItIsMeasured:
    """Option (c) of #926's support-gate decision, as gates nobody can read."""

    def test_a_pending_gate_is_never_also_a_declared_one(self) -> None:
        assert not set(promotion.PENDING) & set(promotion.GATES)

    @pytest.mark.parametrize("name", sorted(promotion.PENDING))
    def test_a_pending_gate_says_what_it_waits_for(self, name) -> None:
        pending = promotion.PENDING[name]
        assert pending.question and pending.measures and pending.unset

    def test_no_support_threshold_is_declared_without_a_reason(self) -> None:
        """Neither 10% unsupported nor 20% unresolved had a rationale."""
        assert promotion.PENDING["support-shares"].limit == ""
        assert "unsupported-assertions" not in promotion.GATES

    def test_a_pending_gate_is_read_as_unread(self, sealed) -> None:
        artifact, report = sealed
        readings = {row.gate: row for row in promotion.decide(artifact, report)}

        for name in promotion.PENDING:
            assert readings[name].verdict == "inconclusive"

    def test_the_rendered_table_names_every_pending_gate(self, sealed) -> None:
        artifact, report = sealed
        printed = promotion.render(promotion.decide(artifact, report))

        for name in promotion.PENDING:
            assert f"`{name}` unread" in printed


class TestRecallIsReadOffTheReport:
    """Required-fact recall, from the catalog a report embeds."""

    def report(self, entries) -> dict:
        case = next(
            case
            for case in load_corpus(promotion.CORPUS)
            if case.id == "01-payments-checkout"
        )
        reference = signed_reference(promotion.CORPUS, case)
        assert reference is not None
        rows = list(entries(reference))
        record = AssertionRecord(
            proposed=len(rows),
            catalog=AssertionCatalog(subjects=reference.subjects, entries=rows),
        )
        return {
            "input": {
                "sources": [{"sha256": text_digest(s.text)} for s in case.sources]
            },
            "assertions": record.model_dump(mode="json"),
        }

    def test_a_perfect_reading_recalls_everything(self) -> None:
        report = self.report(lambda reference: reference.entries)
        assert promotion._required_fact_recall({}, report) == 1.0

    def test_a_missing_fact_lowers_recall(self) -> None:
        report = self.report(lambda reference: reference.entries[1:])
        recall = promotion._required_fact_recall({}, report)
        assert recall is not None and recall < 1.0

    def test_the_pending_gate_prints_the_figure_and_stays_unread(self) -> None:
        report = self.report(lambda reference: reference.entries)
        artifact = TestTheTwoKindsOfGate().artifact(0.1, 0.5)
        readings = {row.gate: row for row in promotion.decide(artifact, report)}

        assert readings["required-fact-recall"].measured == 1.0
        assert readings["required-fact-recall"].verdict == "inconclusive"

    def test_a_report_from_no_corpus_case_is_unread(self) -> None:
        report = {"input": {"sources": [{"sha256": "0" * 64}]}}
        assert promotion._required_fact_recall({}, report) is None
