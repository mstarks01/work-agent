"""The frozen review population #926's support measurement is read over.

Two groups. The first routes rows with the job's own readers, so a row a lane
read is in the population whatever carried it. The second is the rule the file
exists for: a population is frozen before any review, and never rewritten.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from analysis_service.assertions import (
    ABSENT,
    Assertion,
    AssertionCatalog,
    AssertionRecord,
    CatalogIssue,
    Quarantined,
    Subject,
    assertion_id,
)
from evals.harness import population
from evals.harness.run import COMMANDS
from tests.test_assertions import (
    FLOW,
    SOURCES,
    TestTheProjectionBecomesTheGraphsValue,
    stated,
)

MODEL = TestTheProjectionBecomesTheGraphsValue().model()
SHOPPERS = "principal:shopper-accounts"


def record(*entries: Assertion, issues=()) -> AssertionRecord:
    return AssertionRecord(
        proposed=len(entries) + len(issues),
        catalog=AssertionCatalog(
            subjects=[
                Subject(id=FLOW, type="interaction", label="place order"),
                Subject(id=SHOPPERS, type="principal", label="shopper accounts"),
            ],
            entries=list(entries),
        ),
        issues=list(issues),
    )


def mfa(**overrides) -> Assertion:
    return stated(
        **{
            "subject": SHOPPERS,
            "predicate": "mfa-requirement",
            "value": ABSENT,
            "basis": "inferred",
            "support": [],
            "explanation": "fixture",
            **overrides,
        }
    )


def report(held: AssertionRecord) -> dict:
    return {
        "assertions": held.model_dump(mode="json"),
        "system_model": MODEL.model_dump(mode="json"),
    }


class TestEveryRowIsRoutedByTheJobsReaders:
    def routes(self, held: AssertionRecord) -> dict[str, str]:
        return {
            row.identity: row.route
            for row in population.population("case", held, MODEL)
        }

    def test_a_projected_and_an_offered_row_both_reach(self) -> None:
        held = record(stated(), mfa())
        routes = self.routes(held)

        assert routes[assertion_id(stated())] == "projected"
        assert routes[assertion_id(mfa())] == "offered"
        assert set(routes.values()) <= population.REACHES

    def test_an_unknown_is_open_and_a_conflict_is_set_aside(self) -> None:
        silent = stated(value="unknown", reason="silent", support=[])
        held = record(silent, mfa(), mfa(value="required"))
        routes = self.routes(held)

        assert routes[assertion_id(silent)] == "open"
        assert routes[assertion_id(mfa())] == "set-aside"

    def test_a_refused_row_is_recorded_with_its_codes(self) -> None:
        issues = [
            CatalogIssue(code="unverifiable-span", message="", row=3),
            CatalogIssue(code="missing-scope", message="", row=3),
            CatalogIssue(code="graph-contradiction", message=""),
        ]
        rows = population.population("case", record(issues=issues), MODEL)

        (refused,) = rows
        assert refused.route == "refused"
        assert refused.proposed_row == 3
        assert refused.codes == ("missing-scope", "unverifiable-span")

    def test_a_refused_subject_records_every_row_it_took(self) -> None:
        """A refusal naming a subject is one issue and two refused rows."""
        ghost = "flow:entity:shopper>process:storefront-api>ghost"
        entries = [stated(subject=ghost), stated(subject=ghost, value="company SSO")]
        held = AssertionRecord.over(
            AssertionCatalog(
                subjects=[Subject(id=ghost, type="interaction", label="ghost")],
                entries=entries,
            ),
            MODEL,
            SOURCES,
            proposed=2,
        )
        rows = population.population("case", held, MODEL)

        assert [row.identity for row in rows] == [
            assertion_id(entry) for entry in entries
        ]
        assert {row.codes for row in rows} == {("dangling-binding",)}

    def test_a_refused_catalog_loses_every_proposed_row(self) -> None:
        """Three proposed rows merged into two, then the gate refused the
        catalog: all three are lost, not the two it built."""
        held = AssertionRecord(
            proposed=3,
            catalog=AssertionCatalog(),
            issues=[CatalogIssue(code="too-many-subjects", message="")],
            quarantined=[
                Quarantined(identity=identity, codes=["too-many-subjects"])
                for identity in ("assertion:a", "assertion:b")
            ],
        )
        rows = population.population("case", held, MODEL)

        assert [row.proposed_row for row in rows] == [0, 1, 2]
        assert {row.codes for row in rows} == {("too-many-subjects",)}


def _refused_records() -> list[AssertionRecord]:
    ghost = "flow:entity:shopper>process:storefront-api>ghost"
    return [
        record(
            issues=[
                CatalogIssue(code="unverifiable-span", message="", row=3),
                CatalogIssue(code="missing-scope", message="", row=3),
            ]
        ),
        AssertionRecord.over(
            AssertionCatalog(
                subjects=[Subject(id=ghost, type="interaction", label="ghost")],
                entries=[stated(subject=ghost), stated(subject=ghost, value="SSO")],
            ),
            MODEL,
            SOURCES,
            proposed=2,
        ),
        AssertionRecord(
            proposed=3,
            catalog=AssertionCatalog(),
            issues=[CatalogIssue(code="too-many-subjects", message="")],
            quarantined=[
                Quarantined(identity="assertion:a", codes=["too-many-subjects"])
            ],
        ),
    ]


@pytest.mark.parametrize("held", _refused_records())
def test_the_population_and_the_record_agree_on_what_was_refused(
    held: AssertionRecord,
) -> None:
    """The review population and the promotion gate's refusal rate read one
    rule, so they are held against each other rather than apart."""
    rows = population.population("case", held, MODEL)

    assert sum(row.route == "refused" for row in rows) == held.refused_rows()


class TestAPopulationIsFrozenBeforeReview:
    def test_a_freeze_pins_the_catalog_it_was_taken_from(self) -> None:
        held = record(stated(), mfa())
        frozen = population.freeze(
            {"case": report(held)}, now=datetime(2026, 9, 23, tzinfo=UTC)
        )

        assert frozen["cases"]["case"]["catalog_digest"] == (
            population.catalog_digest(held)
        )
        assert len(population.reached(frozen)) == 2

    def test_a_catalog_already_under_review_is_refused(self) -> None:
        held = record(mfa(assessment="supported", assessor="human:1"))
        with pytest.raises(population.PopulationError, match="already assessed"):
            population.freeze({"case": report(held)})

    def test_a_report_with_no_assertion_pass_is_refused(self) -> None:
        with pytest.raises(population.PopulationError, match="no assertion pass"):
            population.freeze({"case": {"assertions": None}})

    def test_the_command_never_rewrites_a_frozen_file(self, tmp_path) -> None:
        source = tmp_path / "case.report.json"
        source.write_text(json.dumps(report(record(stated()))), encoding="utf-8")
        out = tmp_path / "population.json"
        args = argparse.Namespace(
            reports=[source], assertions=[], corpus=population.CORPUS, out=out
        )

        assert population.command_freeze_population(args) == 0
        first = out.read_text(encoding="utf-8")
        assert population.command_freeze_population(args) == 1
        assert out.read_text(encoding="utf-8") == first
        assert set(json.loads(first)["cases"]) == {str(tmp_path / "case")}

    def test_the_command_is_registered(self) -> None:
        assert COMMANDS["freeze-population"].run is (
            population.command_freeze_population
        )


class TestReassessingAReport:
    """``run.py reassess``: a review applied, and a stray one refused."""

    def write(self, tmp_path, digest=None):
        from tests.test_reassess import ABSENCE, REVIEWER, report

        held = report()
        path = tmp_path / "case.report.json"
        path.write_text(held.model_dump_json(), encoding="utf-8")
        assert held.assertions is not None
        verdicts = tmp_path / "verdicts.json"
        verdicts.write_text(
            json.dumps(
                {
                    "catalog_digest": digest
                    or population.catalog_digest(held.assertions),
                    "verdicts": {
                        assertion_id(ABSENCE): {
                            "assessment": "unsupported",
                            "assessor": REVIEWER,
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        out = tmp_path / "reassessed.json"
        return argparse.Namespace(report=path, verdicts=verdicts, out=out)

    def test_a_review_is_applied_and_written(self, tmp_path) -> None:
        args = self.write(tmp_path)

        assert population.command_reassess(args) == 0
        written = json.loads(args.out.read_text(encoding="utf-8"))
        assert len(written["rejected"]) == 1
        assert len(written["withdrawn"]) == 1

    def test_verdicts_for_another_catalog_are_refused(self, tmp_path) -> None:
        args = self.write(tmp_path, digest="0" * 64)

        assert population.command_reassess(args) == 1
        assert not args.out.exists()

    @pytest.mark.parametrize(
        "verdicts",
        [
            None,
            [],
            {"assertion:x": "unsupported"},
            {"assertion:x": {}},
            {"assertion:x": {"assessment": "unsupported", "assessor": 7}},
        ],
    )
    def test_a_malformed_verdict_is_refused_with_a_message(
        self, verdicts, tmp_path, capsys
    ) -> None:
        args = self.write(tmp_path)
        review = json.loads(args.verdicts.read_text(encoding="utf-8"))
        args.verdicts.write_text(
            json.dumps({**review, "verdicts": verdicts}), encoding="utf-8"
        )

        assert population.command_reassess(args) == 1
        assert "cannot reassess" in capsys.readouterr().err
        assert not args.out.exists()

    def test_a_verdict_file_that_is_not_an_object_is_refused(
        self, tmp_path, capsys
    ) -> None:
        args = self.write(tmp_path)
        args.verdicts.write_text("[]", encoding="utf-8")

        assert population.command_reassess(args) == 1
        assert "not a JSON object" in capsys.readouterr().err

    def test_the_command_is_registered(self) -> None:
        assert COMMANDS["reassess"].run is population.command_reassess


class TestAnAssertionSweepIsFrozenToo:
    """``freeze-population --assertions``: the cheap runs' catalogs, by run and case."""

    SWEEP = (
        Path(__file__).resolve().parents[1]
        / "evals/emissions/20260916T-assert-holdouts/luna-after-r1.json"
    )

    def test_every_case_of_a_sweep_is_frozen_with_its_rows(self, tmp_path) -> None:
        out = tmp_path / "population.json"
        args = argparse.Namespace(
            reports=[], assertions=[self.SWEEP], corpus=population.CORPUS, out=out
        )

        assert population.command_freeze_population(args) == 0
        frozen = json.loads(out.read_text(encoding="utf-8"))
        assert len(frozen["cases"]) == 4
        for key, held in frozen["cases"].items():
            assert key.startswith(str(self.SWEEP.with_suffix("")))
            assert held["rows"]

    def test_two_runs_of_one_case_are_two_populations(self, tmp_path) -> None:
        held = report(record(stated(), mfa()))
        paths = []
        for run in ("off", "on"):
            path = tmp_path / run / "case.report.json"
            path.parent.mkdir()
            path.write_text(json.dumps(held), encoding="utf-8")
            paths.append(path)
        out = tmp_path / "population.json"
        args = argparse.Namespace(
            reports=paths, assertions=[], corpus=population.CORPUS, out=out
        )

        assert population.command_freeze_population(args) == 0
        assert len(json.loads(out.read_text(encoding="utf-8"))["cases"]) == 2
