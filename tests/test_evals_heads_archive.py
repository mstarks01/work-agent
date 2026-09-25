"""What a sweep keeps beside its artifact, for the modes that keep a catalog.

The writer had its own list of modes and the replay had another, and the
head-only mode landed in one of them: every run archived nothing and the empty
directory was invisible until a replay looked for it. Both now read
:data:`~evals.harness.replay.KEEPS`, and these hold them to it.
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
from evals.harness.bundle import reports_dir, write_assertions


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

    A facts-first head composes its proposal out of a bundle code resolved, so
    a replay holding the proposal alone cannot re-run the resolver over what
    arrived, or attribute a missing fact to a stage.
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
