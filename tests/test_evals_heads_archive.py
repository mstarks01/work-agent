"""What a sweep keeps beside its artifact, for the modes that keep a catalog.

The writer and the replay both read :data:`~evals.harness.replay.KEEPS`, and
these tests hold them to it. Two lists of modes can disagree, and then a run
archives nothing and the empty directory stays invisible until a replay looks
for it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from analysis_service.assertions import (
    Assertion,
    AssertionCatalog,
    AssertionRecord,
    Subject,
)
from evals.harness import modes, replay
from evals.harness.bundle import FINISHED_KINDS, reports_dir, write_assertions


def result(case_id: str = "01-payments-checkout", **overrides) -> modes.AssertionResult:
    return modes.AssertionResult(
        case_id=case_id,
        proposal={"assertions": []},
        record=AssertionRecord(
            proposed=1,
            catalog=AssertionCatalog(
                subjects=[Subject(id="store:queue", type="component", label="queue")],
                entries=[
                    Assertion(
                        subject="store:queue",
                        predicate="storage-encryption",
                        value="absent",
                        basis="stated",
                        origin="assertion-pass",
                    )
                ],
            ),
        ),
        **overrides,
    )


@pytest.mark.parametrize(
    "mode", sorted(mode for mode, kept in replay.KEEPS.items() if kept == "catalog")
)
def test_every_catalog_mode_archives_its_run(tmp_path: Path, mode: str) -> None:
    """A mode whose replay reads a catalog is a mode whose sweep writes one."""
    out = str(tmp_path / "sweep.json")
    write_assertions(out, mode, {"01-payments-checkout": result()})
    written = reports_dir(out) / "01-payments-checkout.assertions.json"

    assert written.exists(), f"a {mode} sweep archived nothing for a replay to read"
    held = json.loads(written.read_text(encoding="utf-8"))
    assert held["catalog"]["entries"]


def test_a_run_keeps_every_stage_it_wrote(tmp_path: Path) -> None:
    """The catalog alone cannot say what the model emitted or what was lost.

    A head's catalog is read beside the graph its rows bound against, so a
    reader can attribute a missing fact to the reading or to the gate.
    """
    stages = {key: {"kept": key} for key in modes.ARCHIVED_STATE}
    out = str(tmp_path / "sweep.json")
    write_assertions(out, "heads", {"01-payments-checkout": result(stages=stages)})

    held = json.loads(
        (reports_dir(out) / "01-payments-checkout.assertions.json").read_text("utf-8")
    )
    assert held["stages"] == stages
    assert set(held["stages"]) == set(modes.ARCHIVED_STATE)


def test_a_head_that_wrote_no_stage_archives_none(tmp_path: Path) -> None:
    out = str(tmp_path / "sweep.json")
    write_assertions(out, "heads", {"01-payments-checkout": result()})

    held = json.loads(
        (reports_dir(out) / "01-payments-checkout.assertions.json").read_text("utf-8")
    )
    assert held["stages"] == {}


@pytest.mark.parametrize(
    "mode", sorted(mode for mode, kept in replay.KEEPS.items() if kept != "catalog")
)
def test_a_mode_that_keeps_an_extraction_writes_no_catalog(
    tmp_path: Path, mode: str
) -> None:
    out = str(tmp_path / "sweep.json")
    write_assertions(out, mode, {"01-payments-checkout": result()})

    assert not (reports_dir(out) / "01-payments-checkout.assertions.json").exists()


def test_a_mode_the_replay_does_not_read_archives_nothing(tmp_path: Path) -> None:
    """The writer answers the replay's table, so an unreplayable mode writes none."""
    unreplayable = set(modes.MODE_ENTRIES) - set(replay.KEEPS)
    assert unreplayable
    out = str(tmp_path / "sweep.json")
    for mode in unreplayable:
        write_assertions(out, mode, {"01-payments-checkout": result()})
        assert not (reports_dir(out) / "01-payments-checkout.assertions.json").exists()


def test_every_mode_says_which_files_a_finished_case_writes() -> None:
    """A mode with no row is a sweep whose lost case file a Baseline cannot see."""
    assert set(FINISHED_KINDS) == set(modes.MODE_ENTRIES), (
        "FINISHED_KINDS and MODE_ENTRIES disagree: give each mode one row in"
        " evals/harness/bundle.py, and no other."
    )


@pytest.mark.parametrize("mode", sorted(modes.MODE_ENTRIES))
def test_each_row_agrees_with_the_writers(mode: str) -> None:
    """Each row answers the gate of each writer that ``run._run_mode`` calls."""
    kinds = FINISHED_KINDS[mode]
    reporting = mode in modes.REPORTING_MODES
    assert {"report", "drafts", "proposals", "lanes"} <= kinds or not reporting
    assert not {"report", "drafts", "proposals", "lanes"} & kinds or reporting
    assert ("assertions" in kinds) == (replay.KEEPS.get(mode) == "catalog")
    assert "extraction" not in kinds or mode in modes.EXTRACTING_MODES
