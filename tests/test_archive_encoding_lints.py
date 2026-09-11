"""The archive is the bytes its producer writes, and a migration keeps it that way.

A report is written once by ``evals/harness/bundle.py``, through
``Report.model_dump_json(indent=2)``, which emits UTF-8. It is then rewritten
whenever a record change leaves the archive unreadable, and a migration that
reaches for ``json.dumps`` gets ``ensure_ascii=True`` unless it says otherwise.

Two of them did. The #710 and #468 migrations rewrote 78 archived reports with
the default, so every em-dash, curly quote and en-dash in the tree became a
``\\uXXXX`` escape, and the one merged **Baseline**'s file digests were
re-stamped over an edit neither migration meant to make. Nothing failed: the
JSON parses to the same value, and ``verify`` recomputes whatever is on disk.
That is exactly why it needs a lint — the seal that exists to make a silent
edit loud cannot see one that moves with it.

**The check is the producer's own spelling, not a preference.**
``json.dumps(value, ensure_ascii=False, indent=2) + "\\n"`` reproduces
``model_dump_json(indent=2) + "\\n"`` byte for byte, which is the fact the lint
below asserts first — so a report that fails the second assertion was rewritten
by something that is not the producer.

**A sweep artifact has the other producer, and the two spellings are opposite.**
``evals/harness/run.py`` writes an artifact with ``json.dumps(document,
indent=2) + "\\n"`` and the default ``ensure_ascii=True``. So the rule is not
"UTF-8 everywhere" — it is "the bytes the producer writes", and which producer
depends on the file. A migration written from the wrong sibling script escapes
one archive or unescapes the other, and both re-stamp a **Baseline**'s digests
over an edit nobody meant.

Held here because it is currently unobservable: no committed artifact carries a
non-ASCII byte, so the two encodings agree on every file in the tree today. The
first sweep that records an em-dash is what separates them, and by then the
migration is already written.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from analysis_service.report import Report

REPO_ROOT = Path(__file__).resolve().parents[1]


def _tracked_reports() -> list[Path]:
    """Every archived report **git holds**, which is what "the archive" means.

    Read from the index rather than from a walk of the tree. ``evals/runs/`` is
    gitignored working output, so a walk would assert over whatever sweeps this
    machine happens to hold — failing on a developer who ran one and passing on
    a clean checkout. The files a migration rewrites and a Baseline seals are
    the tracked ones.
    """
    found = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.report.json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted(REPO_ROOT / rel for rel in found.stdout.split("\0") if rel)


def _tracked_artifacts() -> list[Path]:
    """Every archived sweep artifact git holds.

    A Baseline directory holds its artifacts beside ``baseline.json``, which is
    the manifest rather than a sweep, and the per-case reports sit under a
    ``.reports`` directory the pattern above already claims.
    """
    found = subprocess.run(
        ["git", "ls-files", "-z", "--", "evals/baselines/*.json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted(
        REPO_ROOT / rel
        for rel in found.stdout.split("\0")
        if rel and Path(rel).name != "baseline.json" and ".reports/" not in rel
    )


#: Sorted so a failure names the same file on every machine.
REPORTS = _tracked_reports()
ARTIFACTS = _tracked_artifacts()


def producer_bytes(value: object) -> str:
    """One JSON value in the encoding ``bundle`` writes a report in."""
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def test_some_report_is_archived():
    """A lint over an empty list passes and protects nothing."""
    assert REPORTS


def test_the_producer_and_this_lint_spell_one_encoding():
    """Held against ``model_dump_json`` rather than against an expectation.

    This is the pair-of-readers check the repository asks for wherever a second
    reader is unavoidable: ``bundle`` writes through pydantic and this lint
    writes through :mod:`json`, so the two are compared to each other. A
    pydantic release that changed its indent or its escaping would fail here
    rather than turn the lint below into noise.
    """
    report = Report.model_validate_json(REPORTS[0].read_text(encoding="utf-8"))

    assert producer_bytes(json.loads(report.model_dump_json())) == (
        report.model_dump_json(indent=2) + "\n"
    )


@pytest.mark.parametrize("path", REPORTS, ids=lambda path: path.name)
def test_an_archived_report_holds_the_bytes_its_producer_writes(path: Path):
    """A migration that escapes the archive fails here, on every file it touched.

    The repair is ``ensure_ascii=False`` in the migration and a re-run, not an
    edit to this file: the bytes on disk are what a reader diffs a fresh sweep
    against, and what a Baseline's digests are cut over.
    """
    text = path.read_text(encoding="utf-8")

    assert text == producer_bytes(json.loads(text)), (
        f"{path.relative_to(REPO_ROOT)} is not in the encoding bundle.py writes;"
        " re-run evals/migrations/2026-09-11-restore-archive-encoding.py"
    )


def artifact_bytes(value: object) -> str:
    """One JSON value in the encoding ``run.py`` writes a sweep artifact in.

    The **opposite** call from :func:`producer_bytes`, deliberately, and the
    reason this pair of functions sits in one file: a reader who sees only one
    of them learns the wrong rule. What the archive holds is whatever its
    producer wrote, and the two producers disagree.
    """
    return json.dumps(value, indent=2) + "\n"


def test_some_artifact_is_archived():
    """A lint over an empty list passes and protects nothing."""
    assert ARTIFACTS


def test_the_two_producers_really_do_spell_it_differently():
    """The premise, asserted so the lint below cannot pass vacuously.

    Every committed artifact is ASCII today, so the two encodings agree on all
    of them and the parametrised test would pass under either rule. This is
    what says the rule is load-bearing: give the writers one non-ASCII
    character and they part.
    """
    value = {"note": "an em-dash \u2014 here"}

    assert artifact_bytes(value) != producer_bytes(value)
    assert "\\u2014" in artifact_bytes(value)


@pytest.mark.parametrize("path", ARTIFACTS, ids=lambda path: path.name)
def test_an_archived_artifact_holds_the_bytes_its_producer_writes(path: Path):
    """A migration that re-encodes an artifact fails here, on every file it touched.

    The repair is to match ``run.py`` in the migration and re-run it, not to
    edit this file. An artifact's own filename is a digest of its bytes, so a
    re-encoding renames the file and re-seals the Baseline over an edit the
    migration never meant to make.
    """
    text = path.read_text(encoding="utf-8")

    assert text == artifact_bytes(json.loads(text)), (
        f"{path.relative_to(REPO_ROOT)} is not in the encoding run.py writes;"
        " a migration that rewrote it used the report spelling instead"
    )
