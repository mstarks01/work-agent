"""The run record names the tree, the table and each file an instrument read."""

from __future__ import annotations

import hashlib

import pytest

from evals.harness import run_record as module
from evals.harness.provenance import ProvenanceError
from evals.harness.run_record import run_record, sweep_inputs


def test_the_record_names_the_tree_the_table_and_each_input(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "checkout_state", lambda: ("f" * 40, False))
    report = tmp_path / "01.report.json"
    report.write_text("{}")
    blessed = tmp_path / "model.json"
    blessed.write_text("[]")
    selections = {"alone": {"asvs": {"level": 2}}}

    record = run_record(
        "early-rounds", {"budgets": (5, 10)}, {report: selections, blessed: {}}
    )

    assert record == {
        "command": "early-rounds",
        "commit": "f" * 40,
        "clean": False,
        "table": {"budgets": (5, 10)},
        "inputs": [
            {
                "path": str(report),
                "sha256": hashlib.sha256(b"{}").hexdigest(),
                "selections": selections,
            },
            {
                "path": str(blessed),
                "sha256": hashlib.sha256(b"[]").hexdigest(),
                "selections": {},
            },
        ],
    }


def test_a_tree_with_no_checkout_writes_no_record(tmp_path, monkeypatch):
    """An output file that cannot name its commit is not reproducible."""

    def no_checkout():
        raise ProvenanceError("no checkout")

    monkeypatch.setattr(module, "checkout_state", no_checkout)
    with pytest.raises(ProvenanceError):
        run_record("early-rounds", {}, {})


def test_a_sweep_is_its_artifact_and_every_file_beside_it(tmp_path):
    """The artifact holds no digest of its case files, so each is named."""
    artifact = tmp_path / "sweep.json"
    artifact.write_text("{}")
    reports = tmp_path / "sweep.reports"
    reports.mkdir()
    for name in ("02.report.json", "01.drafts.json"):
        (reports / name).write_text("[]")

    assert list(sweep_inputs([artifact])) == [
        artifact,
        reports / "01.drafts.json",
        reports / "02.report.json",
    ]


#: The eval writers whose file is not a measurement, each with the reason.
