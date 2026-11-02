"""Every eval instrument writes the run record, or says why its file is not a
measurement (#2029)."""

from __future__ import annotations

import ast

import pytest

from tests.source_tree import PYTHON, REPO_ROOT, parse, source_files, without

pytestmark = pytest.mark.lint

#: The parts of :data:`~tests.source_tree.PYTHON` this lint does not read.
LEFT_OUT = {
    "src": "the service writes job records, never an eval measurement file",
    "webapp": "the first-run app writes settings and owner files, never a measurement",
    "examples": "the examples call the service and write no eval measurement file",
    ".github/scripts": "the CI scripts check a pull request and write no measurement",
    "evals/experiments": (
        "each script there is the frozen record of one experiment, and the"
        " ledger row names its revision"
    ),
}

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
    """Every eval function that writes a file, and whether it calls the record."""
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

    for path in without(source_files(*PYTHON), LEFT_OUT):
        module = path.relative_to(REPO_ROOT / "evals").with_suffix("")
        visit(parse(path), f"{'.'.join(module.parts)}.")
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
