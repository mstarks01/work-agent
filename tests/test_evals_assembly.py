"""The assembly replay: a critic's rulings rebuilt from a report and assembled again.

Driven by a scripted sweep, so the archive is one the shipped graph wrote. The
report-omission fixture is the one #1091 asked for: a finding the critic kept
that the report lacks, found by nothing earlier in the pipeline.
"""

from __future__ import annotations

from analysis_service.claims import CLAIM_BOUND_MARKS
from analysis_service.report import Report
from evals.harness import assembly
from evals.harness.bundle import reports_dir
from tests.test_evals_descendants import archive  # noqa: F401  (fixture)
from tests.test_evals_run_grounds import case  # noqa: F401  (fixture)


def _report_path(archive, case):  # noqa: F811
    return reports_dir(archive) / f"{case.id}.report.json"


def test_an_archive_assembles_to_its_own_report(archive, case):  # noqa: F811
    (row,) = assembly.replay_artifact(archive, [case])

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

    (row,) = assembly.replay_artifact(archive, [case])

    assert row.omitted == (gone,)
    assert row.unexplained == ()


def test_a_claim_assembly_files_elsewhere_is_unexplained(
    archive,  # noqa: F811
    case,  # noqa: F811
    monkeypatch,
):
    """Assembly changed under the archive: it now rejects what the critic kept."""
    real = assembly.assemble_claims

    def rejects_first(drafts, rulings, model, schemas):
        claims, rejected = real(drafts, rulings, model, schemas)
        return claims[1:], [*rejected, claims[0]]

    monkeypatch.setattr(assembly, "assemble_claims", rejects_first)

    (row,) = assembly.replay_artifact(archive, [case])

    assert len(row.unexplained) == 1
    assert row.omitted == ()
