"""A finished sweep keeps its reports, not only the numbers it thought to take.

The harness used to build a full :class:`~analysis_service.report.Report` per
case, read four things off it and drop it. So a plain question — show me one of
the reports — was unanswerable on a sweep that had already been paid for, and
nothing about a sweep is deterministic enough for a re-run to answer it
([#180](https://github.com/mstarks01/work-agent/issues/180)).

Driven offline against the scripted sweep in
:mod:`tests.test_evals_run_grounds`, which produces real reports through the
shipped graph without a provider call.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from analysis_service.report import Report
from evals.harness.reference import load_case
from evals.harness.run import _write_reports, reports_dir
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

    _write_reports(str(out), "analysis", run.runs)

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

    _write_reports(str(out), "analysis", run.runs)

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

    _write_reports(str(out), "analysis", run.runs)

    written = sorted(path.name for path in reports_dir(out).iterdir())
    assert written == [
        "case-second.drafts.json",
        "case-second.proposals.json",
        "case-second.report.json",
    ]


def test_extraction_says_it_has_no_reports(tmp_path, capsys):
    """That mode stops at the validity gate, so it writes nothing and says so."""
    out = tmp_path / "artifact.json"

    _write_reports(str(out), "extraction", {})

    assert "produces none" in capsys.readouterr().out
    assert not reports_dir(out).exists()
