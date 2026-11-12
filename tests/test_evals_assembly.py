"""The assembly replay: a critic's rulings rebuilt from a report and assembled again.

Driven by a scripted sweep, so the archive is one the shipped graph wrote. The
report-omission fixture is the one #1091 asked for: a finding the critic kept
that the report lacks, found by nothing earlier in the pipeline.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from analysis_service.assertions import AssertionCatalog, AssertionRecord
from analysis_service.claims import CLAIM_BOUND_MARKS
from analysis_service.report import Report
from evals.harness import assembly
from evals.harness.artifact import load_artifact
from evals.harness.bundle import reports_dir
from evals.harness.reference import load_case
from evals.harness.run import main
from tests.source_tree import REPO_ROOT
from tests.test_assertion_use import ABSENCE, SHOPPERS
from tests.test_evals_descendants import archive  # noqa: F401  (fixture)
from tests.test_evals_run_grounds import case  # noqa: F401  (fixture)

#: A committed Baseline whose critic rejected a claim as ``duplicate``, a step
#: ADR 0059 removed, in case ``02-iot-fleet-telemetry`` on claim ``D-01``.
OLD_BASELINE = (
    REPO_ROOT / "evals/baselines/6bff717-gpt-5.6-terra-24dda4db/mstarks01-e32ad9c5.json"
)
DUPLICATE_CASE = "02-iot-fleet-telemetry"


def _report_path(archive, case):  # noqa: F811
    return reports_dir(archive) / f"{case.id}.report.json"


def test_an_archive_assembles_to_its_own_report(archive, case):  # noqa: F811
    (row,) = assembly.replay_artifact(load_artifact(archive), [case]).rows

    assert row.reported and row.replayed == row.reported
    assert row.rejected_replayed == row.rejected_reported
    assert (row.omitted, row.unexplained, row.refused) == ((), (), "")


def _without_claim(report: Report) -> tuple[Report, str]:
    """``report`` with its last STRIDE claim gone and the block still consistent.

    A real omission would leave a report its own validators accept, so the
    fixture removes the claim, every mark naming it, and recomputes the summary
    with the block's own builder. The draft stays in the archived drafts file.
    """
    block = report.analyses[0]
    gone = block.claims[-1].id
    kept = block.claims[:-1]
    marks = {
        name: [mark for mark in getattr(block, name) if mark.claim_id != gone]
        for name in CLAIM_BOUND_MARKS
        if hasattr(block, name)
    }
    rebuilt = block.model_copy(
        update={
            "claims": kept,
            "summary": type(block).summarize(kept, block.rejected_claims),
            **marks,
        }
    )
    return report.model_copy(update={"analyses": [rebuilt]}), gone


def test_a_ruled_draft_the_report_lacks_is_an_omission(archive, case):  # noqa: F811
    """The report-omission fixture: the claim leaves the report, its draft stays."""
    path = _report_path(archive, case)
    report, gone = _without_claim(Report.model_validate_json(path.read_text()))
    path.write_text(report.model_dump_json())

    (row,) = assembly.replay_artifact(load_artifact(archive), [case]).rows

    assert row.omitted == (gone,)
    assert row.unexplained == ()


def test_a_claim_assembly_files_elsewhere_is_unexplained(
    archive,  # noqa: F811
    case,  # noqa: F811
    monkeypatch,
):
    """Assembly changed under the archive: it now rejects what the critic kept."""
    real = assembly.CriticRound.outcome

    def rejects_first(self, emitted):
        outcome = real(self, emitted)
        return replace(
            outcome,
            claims=outcome.claims[1:],
            rejected=[*outcome.rejected, outcome.claims[0]],
        )

    monkeypatch.setattr(assembly.CriticRound, "outcome", rejects_first)

    (row,) = assembly.replay_artifact(load_artifact(archive), [case]).rows

    assert len(row.unexplained) == 1
    assert row.omitted == ()


def test_a_finished_case_with_no_report_is_named(archive, case):  # noqa: F811
    """The artifact records the case as finished, so its report was lost."""
    _report_path(archive, case).unlink()

    with pytest.raises(assembly.EvalRunError, match=f"{case.id}: .* no report"):
        assembly.replay_artifact(load_artifact(archive), [case])


def test_a_finished_case_with_no_drafts_is_named(archive, case):  # noqa: F811
    """The artifact records the case as finished, so its drafts were lost."""
    (reports_dir(archive) / f"{case.id}.drafts.json").unlink()

    with pytest.raises(assembly.EvalRunError, match=f"{case.id}: .* no drafts"):
        assembly.replay_artifact(load_artifact(archive), [case])


def test_a_ruling_today_s_critic_may_not_write_refuses_its_block():
    """A ``duplicate`` rejection rebuilds a ruling today's ``ThreatRuling`` refuses."""
    old_case = load_case(REPO_ROOT / "evals/corpus" / DUPLICATE_CASE)

    (row,) = assembly.replay_artifact(load_artifact(OLD_BASELINE), [old_case]).rows

    assert row.refused.startswith(
        "D-01: today's ThreatRuling refuses verdict.rejected_because holds 'duplicate'"
    )
    assert (row.replayed, row.rejected_replayed) == ((), ())


def test_the_command_names_the_refused_block_and_reads_the_rest(capsys):
    code = main(
        [
            "assembly",
            str(OLD_BASELINE),
            "--case",
            DUPLICATE_CASE,
            "--case",
            "01-payments-checkout",
        ]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert f"{DUPLICATE_CASE:34} stride refused by today's check:" in out
    assert "01-payments-checkout" in out and " same" in out
    assert out.splitlines()[-1] == (
        "2 block(s), 0 differ, 1 refused by today's check;"
        " 0 report(s) refused by today's Report"
    )


#: The case whose report :func:`unreadable_sweep` rewrites.
UNREADABLE_CASE = "01-payments-checkout"


@pytest.fixture
def unreadable_sweep(tmp_path) -> Path:
    """A copy of :data:`OLD_BASELINE` whose :data:`UNREADABLE_CASE` report
    holds a catalog entry with no ``origin``, a field today's Report requires."""
    sweep = tmp_path / OLD_BASELINE.name
    shutil.copy(OLD_BASELINE, sweep)
    shutil.copytree(reports_dir(OLD_BASELINE), reports_dir(sweep))
    path = reports_dir(sweep) / f"{UNREADABLE_CASE}.report.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    catalog = AssertionCatalog(subjects=[SHOPPERS], entries=[ABSENCE])
    payload["assertions"] = AssertionRecord(proposed=1, catalog=catalog).model_dump(
        mode="json"
    )
    del payload["assertions"]["catalog"]["entries"][0]["origin"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    return sweep


def test_the_command_names_an_unreadable_report_and_reads_the_rest(
    unreadable_sweep, capsys
):
    code = main(
        [
            "assembly",
            str(unreadable_sweep),
            "--case",
            DUPLICATE_CASE,
            "--case",
            UNREADABLE_CASE,
        ]
    )

    out = capsys.readouterr().out
    report = reports_dir(unreadable_sweep) / f"{UNREADABLE_CASE}.report.json"
    assert code == 0
    assert (
        f"{report}: today's Report refuses"
        " assertions.catalog.entries.0.origin: Field required"
    ) in out.splitlines()
    assert f"{DUPLICATE_CASE:34} stride refused by today's check:" in out
    assert out.splitlines()[-1] == (
        "1 block(s), 0 differ, 1 refused by today's check;"
        " 1 report(s) refused by today's Report"
    )
