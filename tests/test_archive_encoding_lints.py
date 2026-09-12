"""The archive is the bytes its producer writes, and a rewrite keeps it that way.

A report is written once by ``evals/harness/bundle.py``, through
``Report.model_dump_json(indent=2)``, which emits UTF-8. It is rewritten
whenever a record change leaves the archive unreadable, and a rewrite that
reaches for ``json.dumps`` gets ``ensure_ascii=True`` unless it says otherwise:
every em-dash, curly quote and en-dash in the tree becomes a ``\\uXXXX``
escape, and the merged **Baseline**'s file digests are re-stamped over an edit
nobody meant to make. Nothing fails: the JSON parses to the same value, and
``verify`` recomputes whatever is on disk. That is exactly why it needs a
lint — the seal that exists to make a silent edit loud cannot see one that
moves with it.

**A Baseline holds five kinds of file and three encodings**, and two of the
three sit in one directory: ``bundle`` writes a report in UTF-8 and, three
lines later, the drafts beside it in escaped ASCII. So "the archive is UTF-8"
is a rule a reader learns from whichever writer they happened to open, and it
is wrong for three of the five kinds. :mod:`evals.harness.archive` is the one
table that answers, every producer writes through it, and this file holds the
committed bytes against it.

**Driven by the manifest, not by a glob.** ``baseline.json`` records a digest
for every file its sweep owns, so its ``files`` map is the list of exactly what
a Baseline seals. Reading the file set from there means a sixth kind added to
``bundle`` tomorrow arrives here as a file with no declared producer, and fails,
rather than being quietly unread the way ``*.drafts.json`` and
``*.proposals.json`` were.

The encodings are currently indistinguishable on the tree's own contents: every
committed file is ASCII and every manifest is already key-sorted, so all three
spellings agree on all 42 files today. The premise tests below are what keep
this from passing vacuously — they give the writers one non-ASCII character and
one out-of-order key, and require them to part.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from analysis_service.report import Report
from evals.harness.archive import (
    ARCHIVE_SPELLINGS,
    UnknownArchiveKind,
    archive_bytes,
    kind_of,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _tracked_manifests() -> list[Path]:
    """Every Baseline manifest **git holds**, which is what "the archive" means.

    Read from the index rather than from a walk of the tree. ``evals/runs/`` is
    gitignored working output, so a walk would assert over whatever sweeps this
    machine happens to hold — failing on a developer who ran one and passing on
    a clean checkout. The files a migration rewrites and a Baseline seals are
    the tracked ones.
    """
    found = subprocess.run(
        ["git", "ls-files", "-z", "--", "evals/baselines/*/baseline.json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted(REPO_ROOT / rel for rel in found.stdout.split("\0") if rel)


def _sealed_files() -> list[tuple[Path, str | None]]:
    """Every file a Baseline seals, paired with the kind its producer declared.

    The manifest names the sweep artifact outright in its ``artifact`` field,
    which is the only place that fact is written down: an artifact is
    ``<stem>.json`` and no suffix separates it from any other JSON file. Every
    other file answers to :func:`~evals.harness.archive.kind_of`, and a file
    that answers to neither is carried here as ``None`` so the completeness test
    below can name it.
    """
    sealed: list[tuple[Path, str | None]] = []
    for manifest in _tracked_manifests():
        sealed.append((manifest, "manifest"))
        directory = manifest.parent
        document = json.loads(manifest.read_text(encoding="utf-8"))
        for sweep in document.get("sweeps", []):
            artifact = sweep.get("artifact")
            for relative in sweep.get("files", {}):
                name = Path(relative).name
                kind = "artifact" if name == artifact else kind_of(name)
                sealed.append((directory / relative, kind))
    return sorted(set(sealed))


#: Sorted so a failure names the same file on every machine.
SEALED = _sealed_files()


def test_a_baseline_is_archived():
    """A lint over an empty list passes and protects nothing."""
    assert _tracked_manifests()
    assert SEALED


def test_every_sealed_file_has_a_declared_producer():
    """The completeness half, and the one that was missing.

    ``*.drafts.json`` and ``*.proposals.json`` are 26 of the 42 files a Baseline
    seals, and the lint that held the archive to its producer's bytes read
    neither of them — it read ``*.report.json`` and the artifact, and its own
    comment asserted that the report pattern claimed the whole ``.reports``
    directory. It claims a third of it.

    Driven off the manifest, so this cannot happen again by omission: a file
    kind a Baseline seals and nobody declared a producer for arrives here as
    ``None`` and fails, naming the file.
    """
    undeclared = sorted(
        str(path.relative_to(REPO_ROOT)) for path, kind in SEALED if kind is None
    )

    assert not undeclared, (
        f"a Baseline seals these files and no producer is declared for them:"
        f" {undeclared}. Add the kind to evals/harness/archive.py, which is"
        f" where the archive's encodings are decided, and make the writer ask"
        f" for it by name"
    )


def test_the_pydantic_producer_and_this_table_spell_one_encoding():
    """Held against ``model_dump_json`` rather than against an expectation.

    This is the pair-of-readers check the repository asks for wherever a second
    reader is unavoidable: ``bundle`` writes a report through pydantic and
    :mod:`evals.harness.archive` writes through :mod:`json`, so the two are
    compared to each other. A pydantic release that changed its indent or its
    escaping would fail here rather than turn the lints below into noise.
    """
    reports = [path for path, kind in SEALED if kind == "report"]
    assert reports, "no archived report to compare the two writers on"
    report = Report.model_validate_json(reports[0].read_text(encoding="utf-8"))

    assert archive_bytes("report", json.loads(report.model_dump_json())) == (
        report.model_dump_json(indent=2) + "\n"
    )


def test_the_three_spellings_really_do_differ():
    """The premise, asserted so the lints below cannot pass vacuously.

    Every committed file is ASCII and every manifest is already key-sorted, so
    the three encodings agree on all 42 of them and the parametrised test would
    pass under any one rule. This is what says the table is load-bearing: give
    the writers one non-ASCII character and one out-of-order key, and they part.

    Both axes are checked. Escaping is the one a default dump moves. Key
    order is the one a manifest alone carries, and it is the more
    visible loss: a manifest rewritten in the report's spelling keeps every byte
    of its values and reorders every key in the file.
    """
    value = {"note": "an em-dash — here", "artifact": "a.json"}

    assert "\\u2014" in archive_bytes("drafts", value)
    assert "—" in archive_bytes("report", value)
    assert archive_bytes("drafts", value) != archive_bytes("report", value)

    assert archive_bytes("manifest", value) != archive_bytes("report", value)
    assert list(json.loads(archive_bytes("manifest", value))) == sorted(value)


def test_a_kind_nobody_declared_refuses_rather_than_guessing():
    """The table raises on a miss, which is what makes it self-completing.

    A writer that added a sixth kind and inherited whichever spelling sat
    nearest is how the archive got two encodings in one directory. This is the
    guard against the next one.
    """
    with pytest.raises(UnknownArchiveKind, match="no producer is declared"):
        archive_bytes("summary", {})

    # And a reader gets a plain ``None`` rather than a raise, because a name it
    # cannot place is an ordinary answer: an artifact is ``<stem>.json``.
    assert kind_of("mstarks01-0ebfcca1.json") is None
    assert kind_of("01-payments-checkout.drafts.json") == "drafts"

    # A manifest is matched on its whole name. Under a suffix match this read
    # as one, and the migration that walks every JSON file under ``evals/``
    # would have sorted every key in it.
    assert kind_of("baseline.json") == "manifest"
    assert kind_of("my-baseline.json") is None


def test_every_declared_kind_is_sealed_somewhere():
    """The other direction: a kind in the table that no Baseline holds.

    Not an error — a kind may be declared before the first sweep writes one —
    so this reports rather than fails, and it is here because the table and the
    archive are the two halves that have to be compared to each other.
    """
    held = {kind for _, kind in SEALED}

    assert held <= set(ARCHIVE_SPELLINGS) | {None}
    assert held >= {"manifest", "artifact", "report", "drafts", "proposals"}, (
        f"the archive holds {sorted(held - {None})}, so the kinds"
        f" {sorted(set(ARCHIVE_SPELLINGS) - held)} are declared and untested"
        f" against a real file"
    )


@pytest.mark.parametrize(
    ("path", "kind"),
    [(path, kind) for path, kind in SEALED if kind is not None],
    ids=lambda value: value if isinstance(value, str) else Path(value).name,
)
def test_a_sealed_file_holds_the_bytes_its_producer_writes(path: Path, kind: str):
    """A migration that re-encodes the archive fails here, on every file it touched.

    The repair is to ask :mod:`evals.harness.archive` for the spelling in the
    migration and re-run it, not to edit this file. A Baseline's digests are cut
    over these bytes and an artifact's own filename is a digest of its contents,
    so a re-encoding renames the file and re-seals the Baseline over an edit the
    migration never meant to make.
    """
    text = path.read_text(encoding="utf-8")

    assert text == archive_bytes(kind, json.loads(text)), (
        f"{path.relative_to(REPO_ROOT)} is not in the encoding its producer"
        f" writes for a {kind!r}; a migration that rewrote it picked an"
        f" encoding instead of asking evals/harness/archive.py for one"
    )
