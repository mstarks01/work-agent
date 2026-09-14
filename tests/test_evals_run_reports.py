"""A finished sweep keeps its reports, not only the numbers it thought to take.

A harness that built a full :class:`~analysis_service.report.Report` per
case, read four things off it and dropped it would leave a plain question — show
me one of the reports — unanswerable on a sweep the provider billed, and
nothing about a sweep is deterministic enough for a re-run to answer it
([#180](https://github.com/mstarks01/work-agent/issues/180)).

Driven offline against the scripted sweep in
:mod:`tests.test_evals_run_grounds`, which produces real reports through the
shipped graph without a provider call.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from analysis_service.report import Report
from analysis_service.system_model import SystemModel
from analysis_service.validation import ValidationIssue, parse_and_validate
from evals.harness.bundle import reports_dir, write_extractions, write_reports
from evals.harness.modes import ExtractionResult, score_extraction
from evals.harness.reference import load_case
from tests.test_evals_run_grounds import CASE_DIR, DEAD, sweep


@pytest.fixture(scope="module")
def case():
    return load_case(CASE_DIR)


def test_the_report_directory_pairs_with_its_own_artifact():
    """One sweep, one name: reports cannot land beside another run's artifact."""
    assert reports_dir("/tmp/run-a.json") == Path("/tmp/run-a.reports")


def test_every_finished_case_keeps_a_report(monkeypatch, case, tmp_path):
    run = sweep(monkeypatch, case, None)
    out = tmp_path / "artifact.json"

    write_reports(str(out), "analysis", run.runs)

    written = sorted(path.name for path in reports_dir(out).iterdir())
    assert written == [
        f"{case.id}.drafts.json",
        f"{case.id}.proposals.json",
        f"{case.id}.report.json",
        "case-second.drafts.json",
        "case-second.proposals.json",
        "case-second.report.json",
    ], "the drafts ride beside the report, because `score` reads both"


def test_a_persisted_report_carries_what_the_artifact_cannot(
    monkeypatch, case, tmp_path
):
    """The claim text, its grounds and the model — none of which reach the
    artifact, and all of which the open questions on #180 ask for."""
    run = sweep(monkeypatch, case, None)
    out = tmp_path / "artifact.json"

    write_reports(str(out), "analysis", run.runs)

    path = reports_dir(out) / f"{case.id}.report.json"
    report = Report.model_validate_json(path.read_text("utf-8"))
    block = report.analyses[0]
    assert block.framework == "stride"
    assert all(claim.grounds for claim in block.claims)
    assert report.system_model.elements()


def test_a_case_that_died_leaves_no_report(monkeypatch, case, tmp_path):
    """No report, nothing to persist — an empty file would read as a finished
    case that found nothing."""
    run = sweep(monkeypatch, case, DEAD)
    out = tmp_path / "artifact.json"

    write_reports(str(out), "analysis", run.runs)

    written = sorted(path.name for path in reports_dir(out).iterdir())
    assert written == [
        "case-second.drafts.json",
        "case-second.proposals.json",
        "case-second.report.json",
    ]


def test_extraction_says_it_has_no_reports(tmp_path, capsys):
    """That mode stops at the validity gate, so it writes nothing and says so."""
    out = tmp_path / "artifact.json"

    write_reports(str(out), "extraction", {})

    assert "produces none" in capsys.readouterr().out
    assert not reports_dir(out).exists()


class TestAnExtractionSweepKeepsItsModels:
    """The same question of the mode that produces no report.

    An extraction sweep's figures are computed offline from two models and the
    case's own sources, so a scorer change can be answered from a finished run
    — but only by a run that kept the model it scored. One that kept its scores
    alone has to be paid for again to answer a figure invented after it ran
    (#925).
    """

    def result(self, case):
        """One extraction, as ``run_extraction`` returns it, with no provider."""
        raw = case.model.model_dump(mode="json")
        model, issues = parse_and_validate(
            raw,
            normalize_ids=True,
            sources={source.label: source.text for source in case.sources},
        )
        return ExtractionResult(
            case_id=case.id, extracted=model, issues=tuple(issues), raw=raw
        )

    def test_the_written_model_re_scores_to_what_the_sweep_reported(
        self, case, tmp_path
    ):
        """The acceptance test for the whole file: read it back and score it.

        Not "a file exists" — a file whose contents cannot reproduce the run's
        own numbers keeps nothing worth keeping.
        """
        out = tmp_path / "artifact.json"
        result = self.result(case)
        live = score_extraction(case, result)

        write_extractions(str(out), "extraction", {case.id: result})

        written = json.loads(
            (reports_dir(out) / f"{case.id}.extraction.json").read_text("utf-8")
        )
        offline = score_extraction(
            case,
            ExtractionResult(
                case_id=case.id,
                extracted=SystemModel.model_validate(written["normalized"]),
                issues=tuple(
                    ValidationIssue.model_validate(issue) for issue in written["issues"]
                ),
            ),
        )

        assert offline.to_json() == live.to_json()

    def test_the_raw_output_is_kept_beside_the_normalized_one(self, case, tmp_path):
        """Normalizing makes a slug decision, and a re-score may want to remake it.

        It is the one part of a run that cannot be recomputed from what else is
        written: the normalized model has already had its IDs derived.
        """
        out = tmp_path / "artifact.json"
        result = self.result(case)
        write_extractions(str(out), "extraction", {case.id: result})

        written = json.loads(
            (reports_dir(out) / f"{case.id}.extraction.json").read_text("utf-8")
        )

        assert written["raw"] == case.model.model_dump(mode="json")

    def test_a_model_the_gate_refused_is_kept_with_its_issues(self, case, tmp_path):
        """A refused extraction is the one most worth reading back."""
        out = tmp_path / "artifact.json"
        raw = case.model.model_dump(mode="json")
        for element in raw["processes"]:
            element["source_excerpt"] = ""
            element["source_label"] = ""
        model, issues = parse_and_validate(
            raw,
            normalize_ids=True,
            sources={source.label: source.text for source in case.sources},
        )
        result = ExtractionResult(
            case_id=case.id, extracted=model, issues=tuple(issues), raw=raw
        )

        write_extractions(str(out), "extraction", {case.id: result})

        written = json.loads(
            (reports_dir(out) / f"{case.id}.extraction.json").read_text("utf-8")
        )
        assert [issue["code"] for issue in written["issues"]] == [
            "missing-citation"
        ] * (len(raw["processes"]))

    def test_another_mode_writes_none_and_says_so(self, case, tmp_path, capsys):
        """An analysis sweep keeps its model inside its report, not here."""
        out = tmp_path / "artifact.json"

        write_extractions(str(out), "analysis", {})

        assert not reports_dir(out).exists()
        assert "no extractions written" in capsys.readouterr().out
