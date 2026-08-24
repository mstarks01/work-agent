"""Every path and symbol the prose names, checked against the tree it names them in.

## What this can decide, and what it cannot

A document is right about the code in two independent ways. It **names** things
that exist, and it **says true things** about them. Only the first is decidable.

Nothing here reads a sentence. A lint that tried would repeat the mistake
``tests/test_case_review.py`` records: a mechanical check for a claim asserting
something its own model does not hold fires on 231 of 243 claims, because prose
is *supposed* to use words its subject never uses. Whether a paragraph describes
a function correctly is a reading question, and step 6 is the instrument for it.

So this module answers the narrow question: **does the thing the prose names
exist at all?** That catches the drift a deletion leaves behind — a path that
moved, a symbol that was renamed, a module that was retired — which is the class
that survives a rename because no compiler and no import ever touches prose.

Three other layers cover what this one cannot, and none replaces another:

* ``tests/test_docs_lints.py`` — code blocks generated from ``examples/``. The
  strongest guarantee here and the narrowest: an exact-match include.
* A table checked against its registry, per table. ``test_corpus_lints.py``
  holds the README's proximity column to each ``case.json``, and
  ``test_workflow_lints.py`` holds a workflow path filter to the import closure
  it stands for. These catch a *value* that drifted, which a reference check
  cannot see: a table naming the right file with the wrong entry in it passes
  every check in this module.
* A person reading. Everything else.

## The exception table

A document may name something absent **on purpose**, and that is not a defect to
be fixed. ``docs/agents/domain.md`` says there is no ``CONTEXT-MAP.md``, which is
a true sentence about an absent file; ``docs/agents/provenance.md`` is a
post-mortem that names the modules a retirement deleted. Forcing either to
resolve would make the lint demand a falsehood.

So :data:`DELIBERATE` carries them, one reason each, and
:func:`test_no_exception_outlives_its_reason` fails when an entry starts
resolving — an exception that outlives its reason is a hole nobody re-opened.
That is ``UNREVIEWED``'s design in ``test_case_review.py``, for the same reason:
a list nobody prunes stops describing anything.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Trees whose prose describes a state the repository has left, by convention
#: recorded in ``CLAUDE.md`` and ``docs/agents/``. ``.wayfinder/`` holds
#: completed maps that are archived history rather than live, and
#: ``docs/research/`` is frozen evidence — both name files that were real when
#: they were written, and rewriting either would destroy the record.
FROZEN: tuple[str, ...] = (".wayfinder", "docs/research")

#: Directories that hold no prose of this repository's own.
SKIP_DIRS = frozenset(
    {".venv", ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", "__pycache__"}
)

#: A backticked repository path. Anchored on a known extension rather than on
#: a slash, so a bare ``BLESSING.md`` is checked and an ordinary hyphenated
#: word is not.
PATH_REF = re.compile(
    r"`{1,2}([A-Za-z0-9_./-]+\.(?:py|md|toml|json|jsonl|ya?ml|html|txt|cfg))`{1,2}"
)

#: A backticked dotted name inside one of this repository's own packages.
#: Third-party names are out of scope: a lint that resolved them would fail on
#: whatever the lockfile last moved.
SYMBOL_REF = re.compile(
    r"`{1,2}((?:stride_service|evals|webapp|examples)"
    r"(?:\.[a-z_][a-z0-9_]*)*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)`{1,2}"
)

#: ``(document, reference)`` pairs that name something absent on purpose, each
#: with the reason. Not an exemption list to grow past its reasons — a stale
#: entry fails.
DELIBERATE: dict[tuple[str, str], str] = {
    (
        "docs/agents/domain.md",
        "CONTEXT-MAP.md",
    ): "asserts the file's absence — 'There is no `CONTEXT-MAP.md`'. Resolving it"
    " would make the sentence false.",
    (
        "docs/adr/0013-asvs-rules-applicability-and-never-a-pass.md",
        "0x04-Assessment_and_Certification.md",
    ): "a document in the upstream OWASP ASVS repository, not in this tree.",
    (
        "docs/agents/provenance.md",
        "judge.py",
    ): "a post-mortem naming the modules the judge's retirement deleted. See ADR 0003.",
    (
        "docs/agents/provenance.md",
        "judge.toml",
    ): "same post-mortem, same retirement.",
    (
        "docs/agents/provenance.md",
        "evals/prompts/judge_adjudication.md",
    ): "same post-mortem, same retirement.",
    (
        "docs/agents/issue-tracker.md",
        "analyst.md",
    ): "inside a block the document marks as 'not the current state'.",
    (
        "docs/agents/issue-tracker.md",
        "docs/Home.md",
    ): "same block, same marking.",
    (
        "docs/agents/issue-tracker.md",
        "docs/example-report.html",
    ): "same block, same marking.",
    (
        "evals/VOTING.md",
        "evals/review/votes.jsonl",
    ): "the vote ledger, appended at review time. Absent until somebody votes.",
    (
        "evals/review/README.md",
        "votes.jsonl",
    ): "the same ledger, named relative to its own directory.",
    (
        "evals/harness/run.py",
        "evals/review/votes.jsonl",
    ): "the same ledger, named from the module that reads it.",
    (
        "webapp/review.py",
        "evals/review/votes.jsonl",
    ): "the same ledger, named from the app that appends to it.",
    (
        "tests/test_doc_reference_lints.py",
        "CONTEXT-MAP.md",
    ): "this module's own docstring, quoting the exception above it.",
}


def _relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _sources() -> list[Path]:
    """Every file of this repository's own prose: Markdown, and Python.

    Python is included because a docstring drifts exactly as a guide does, and
    the same backticks mark a reference in both. Matching on backticks is what
    keeps ordinary code out: a path in an expression carries none.
    """
    found = []
    for path in REPO_ROOT.rglob("*"):
        if path.suffix not in {".md", ".py"} or not path.is_file():
            continue
        if SKIP_DIRS.intersection(path.parts):
            continue
        if _relative(path).startswith(FROZEN):
            continue
        found.append(path)
    return sorted(found)


SOURCES = _sources()
#: Every file in the tree, as a repository-relative posix path. Built once,
#: because the suffix reading below is a scan.
TREE: frozenset[str] = frozenset(
    _relative(path)
    for path in REPO_ROOT.rglob("*")
    if path.is_file() and not SKIP_DIRS.intersection(path.parts)
)


def _resolves(source: Path, ref: str) -> bool:
    """Whether ``ref``, as written in ``source``, names a file that exists.

    Four readings, because the prose uses all four: a path from the repository
    root, the same under ``src/`` where the package lives, a path relative to
    the naming document, and a **suffix** — ``lanes/authentication/skill.md``
    or a bare ``BLESSING.md``.

    The suffix reading is deliberately loose. Demanding a full path in every
    mention would be a style rule wearing a lint's clothes, and this module
    decides existence rather than house style.
    """
    if (REPO_ROOT / ref).exists() or (REPO_ROOT / "src" / ref).exists():
        return True
    if Path(os.path.normpath(source.parent / ref)).exists():
        return True
    return any(path.endswith(f"/{ref}") for path in TREE)


def _resolves_symbol(ref: str) -> bool:
    """Whether ``ref`` names a module or an attribute of one."""
    try:
        if importlib.util.find_spec(ref) is not None:
            return True
    except (ImportError, ValueError):
        pass
    if "." not in ref:
        return False
    module_name, _, attribute = ref.rpartition(".")
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return False
    return hasattr(module, attribute)


@pytest.mark.parametrize("source", SOURCES, ids=_relative)
def test_every_named_path_exists(source):
    """A path in the prose names a file, or the prose says why it does not."""
    where = _relative(source)
    text = source.read_text(encoding="utf-8", errors="replace")
    broken = sorted(
        {
            ref
            for ref in (match.group(1) for match in PATH_REF.finditer(text))
            if not _resolves(source, ref) and (where, ref) not in DELIBERATE
        }
    )

    assert not broken, (
        f"{where} names files that do not exist: {broken}. Correct the"
        " reference, or — if the prose names an absent thing on purpose — add"
        " it to DELIBERATE in tests/test_doc_reference_lints.py with the"
        " reason."
    )


@pytest.mark.parametrize("source", SOURCES, ids=_relative)
def test_every_named_symbol_imports(source):
    """A dotted name in the prose resolves to a module or one of its attributes."""
    where = _relative(source)
    text = source.read_text(encoding="utf-8", errors="replace")
    broken = sorted(
        {
            ref
            for ref in (match.group(1) for match in SYMBOL_REF.finditer(text))
            if not _resolves_symbol(ref) and (where, ref) not in DELIBERATE
        }
    )

    assert not broken, (
        f"{where} names symbols that do not import: {broken}. A renamed module"
        " or a deleted attribute leaves the prose behind, because nothing else"
        " reads it."
    )


def test_no_exception_outlives_its_reason():
    """An entry that starts resolving is a hole nobody re-opened."""
    stale = sorted(
        f"{document} -> {ref}"
        for document, ref in DELIBERATE
        if (REPO_ROOT / document).exists() and _resolves(REPO_ROOT / document, ref)
    )

    assert not stale, (
        f"these DELIBERATE entries now resolve and must be removed: {stale}."
        " The reference is no longer an exception, so the lint should be"
        " checking it."
    )


def test_every_exception_names_a_document_that_exists():
    """The table names documents, not ghosts — a renamed guide must be re-entered."""
    missing = sorted(
        document for document, _ in DELIBERATE if not (REPO_ROOT / document).exists()
    )

    assert not missing, f"DELIBERATE names documents that do not exist: {missing}"


def test_the_scan_actually_finds_references():
    """Guards the guard: a broken pattern would pass every test above vacuously."""
    paths = sum(
        len(PATH_REF.findall(source.read_text(encoding="utf-8", errors="replace")))
        for source in SOURCES
    )
    symbols = sum(
        len(SYMBOL_REF.findall(source.read_text(encoding="utf-8", errors="replace")))
        for source in SOURCES
    )

    assert paths > 400, f"only {paths} path references found; the pattern is broken"
    assert symbols > 30, (
        f"only {symbols} symbol references found; the pattern is broken"
    )
