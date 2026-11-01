"""The run record names the tree, the table and each file an instrument read."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

from evals.harness import run_record as module
from evals.harness.provenance import ProvenanceError
from evals.harness.run_record import run_record, sweep_inputs

EVALS = Path(__file__).resolve().parents[1] / "evals"


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
NOT_A_MEASUREMENT = {
    "harness.audit.append": "adds one experiment row to the ledger, which names its revision",
    "harness.audit.append_grant": "adds one paid-run grant to the ledger",
    "harness.baseline.assemble": "lays sealed sweeps into a Baseline and computes its manifest",
    "harness.bundle.write_reports": "a sweep's case files, beside the artifact that names the commit",
    "harness.bundle.write_assertions": "a sweep's case files, beside the artifact that names the commit",
    "harness.bundle.write_extractions": "a sweep's case files, beside the artifact that names the commit",
    "harness.bundle.write_failures": "a sweep's case files, beside the artifact that names the commit",
    "harness.certify.promote": "re-pins the sampling file and blesses fingerprints",
    "harness.comparison.write": "rebuilds a committed table that a test holds to the data",
    "harness.envelope.apply": "writes one reader's sitting envelope as a submission file",
    "harness.ledger.append": "adds one vote to the vote ledger",
    "harness.ledger._replace_voter_file": "rewrites one voter's file of the vote ledger",
    "harness.near_misses.write": "issues a ballot that near-miss-rulings checks the answers against",
    "harness.near_misses.record": "writes a person's rulings into the corpus",
    "harness.near_misses._append": "writes a person's rulings into the corpus",
    "harness.preflight._case_at": "puts a case at a base revision in a scratch directory",
    "harness.run.command_run": "the sweep artifact names its commit, corpus and fingerprints",
    "harness.run.command_score": "the scored artifact names the commit and corpus it was scored on",
    "harness.run.command_pairing": "a reading page for a person's ruling; it measures nothing",
    "harness.submit.push_commit": "commits a contribution on its own branch",
    "harness.submit._register": "adds the author's roster line",
    "adversarial.verify.write_digests": "stamps each case's source digest",
    "bench.deterministic.main": (
        "--spans is diffed byte for byte between two trees, and a commit in"
        " it would make every pair differ"
    ),
    "build_diagrams.main": "rebuilds the committed diagram of each case",
    "build_review_docs.main": "rebuilds the committed review document of each case",
    "calibration_labels.build_pairs.main": "rebuilds the committed calibration pairs",
    "verify_corpus.write_shas": "stamps each case's source digests",
}

#: The measurements whose file holds no record yet, each with the reason.
UNRECORDED: dict[str, str] = {}

WRITES = {"write_text", "write_bytes"}


def _writes(call: ast.Call) -> bool:
    """Whether ``call`` writes a file: a path write, or an open to write."""
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if name in WRITES:
        return True
    modes = [
        *call.args,
        *(keyword.value for keyword in call.keywords if keyword.arg == "mode"),
    ]
    return name == "open" and any(
        isinstance(mode, ast.Constant)
        and isinstance(mode.value, str)
        and bool(set(mode.value) & set("wax"))
        for mode in modes
    )


def _writers() -> dict[str, bool]:
    """Every eval function that writes a file, and whether it calls the record.

    ``evals/experiments`` is left out: each script there is the frozen record of
    one experiment, and the ledger row names its revision.
    """
    found: dict[str, bool] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                inside = list(ast.walk(child))
                if any(isinstance(n, ast.Call) and _writes(n) for n in inside):
                    found[prefix + child.name] = any(
                        isinstance(n, ast.Name) and n.id == "run_record" for n in inside
                    )
                visit(child, f"{prefix}{child.name}.")
            elif isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")

    for path in sorted(EVALS.rglob("*.py")):
        module = path.relative_to(EVALS).with_suffix("")
        if module.parts[0] != "experiments":
            visit(
                ast.parse(path.read_text(encoding="utf-8")),
                f"{'.'.join(module.parts)}.",
            )
    return found


def test_every_eval_writer_writes_the_record_or_says_why_not():
    """A measurement file with no record names no commit and no inputs (#2029)."""
    writers = _writers()
    unrecorded = {
        name
        for name, recorded in writers.items()
        if not recorded and name not in NOT_A_MEASUREMENT | UNRECORDED
    }
    assert not unrecorded, (
        f"{sorted(unrecorded)} write a file with no run_record; write one, or add"
        " a row with the reason to NOT_A_MEASUREMENT or UNRECORDED"
    )


def test_every_exception_still_names_a_writer_with_no_record():
    """A spent row would excuse the next writer of that name."""
    writers = _writers()
    spent = {name for name in NOT_A_MEASUREMENT | UNRECORDED if writers.get(name, True)}
    assert not spent, f"{sorted(spent)} write no file, or write the record"
    assert not set(NOT_A_MEASUREMENT) & set(UNRECORDED)
