"""A docstring, a comment or a guide describes the code as it is, never how it got here.

Prose that says what a function did before the last change is wrong the day
the next change lands, and nothing fails when it does: a docstring is never
executed. It also costs every reader a second story to hold beside the code.
The repository's decisions live in ``docs/adr/`` and in the tickets, so prose
that cites one points there and describes the current rule.

The scan reads every Python docstring and comment under :data:`SEARCHED`, every
full-line comment in the page scripts, and every paragraph of the guides in
:data:`GUIDES`. A string a page serves or a test asserts is data, and an
identifier is not prose, so neither is read. ``docs/adr/``, ``docs/research/``
and ``docs/history/`` are records of a past state, so they are not read either.

Each docstring, each run of comment lines and each guide paragraph is read as
one text, so a phrase that wraps across two lines is still seen. The phrases in
:data:`HISTORY` are the ones that only ever introduce an earlier state of this
code. A phrase that also has a present-tense reading, such as "no longer", is
left out on purpose, because a lint whose hits need sorting by hand is a lint
nobody reads.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

from tests.source_tree import REPO_ROOT, parse, source_files

SEARCHED = ("src", "evals", "webapp", "tests")

#: The page scripts whose comments are read.
SCRIPTS = ("webapp/static",)

#: The guides a reader follows as a statement of the current state.
GUIDES: tuple[Path, ...] = (
    REPO_ROOT / "README.md",
    REPO_ROOT / "AGENTS.md",
    *sorted((REPO_ROOT / "docs").glob("*.md")),
    *sorted((REPO_ROOT / "docs" / "agents").glob("*.md")),
)

#: Each phrase names an earlier state of the code. Case-insensitive.
HISTORY = re.compile(
    r"\b(used to|was written for|before this (\w+ )?(existed|one)|previously"
    r"|had been|until now|the old |now that|in the past|formerly|originally"
    r"|historically|once (had|took|wrote|sat|listed|lacked|missed|floored|ended)"
    r"|before this[,:.]|until #\d+|this replaced|(branch|if`?|list) it replaced"
    r"|went stale|went unnoticed|nothing caught"
    r"|an earlier version of this (note|module|page))\b",
    re.IGNORECASE,
)

_DOCUMENTED = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
_FENCE = re.compile(r"^```.*?^```", re.DOTALL | re.MULTILINE)


def _runs(lines: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Consecutive numbered lines joined into one text, at its first line."""
    runs: list[tuple[int, str]] = []
    for lineno, text in lines:
        if runs and lineno == runs[-1][0] + runs[-1][1].count("\n") + 1:
            runs[-1] = (runs[-1][0], f"{runs[-1][1]}\n{text}")
        else:
            runs.append((lineno, text))
    return runs


def _prose(path: Path) -> list[tuple[int, str]]:
    """Every docstring and every run of comment lines in one file, at its line."""
    texts: list[tuple[int, str]] = []
    for node in ast.walk(parse(path)):
        if isinstance(node, _DOCUMENTED) and node.body:
            first = node.body[0]
            docstring = first.value if isinstance(first, ast.Expr) else None
            if isinstance(docstring, ast.Constant) and isinstance(docstring.value, str):
                texts.append((first.lineno, docstring.value))
    source = path.read_text(encoding="utf-8")
    comments = [
        (token.start[0], token.string.lstrip("#").strip())
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT
    ]
    return texts + _runs(comments)


def _script_prose(path: Path) -> list[tuple[int, str]]:
    """Every run of full-line ``//`` comments in one page script, at its line."""
    comments = [
        (lineno, line.strip().removeprefix("//").strip())
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if line.lstrip().startswith("//")
    ]
    return _runs(comments)


def _guide_prose(path: Path) -> list[tuple[int, str]]:
    """Every paragraph of one guide outside a fenced block, at its first line."""
    text = _FENCE.sub(
        lambda fence: "\n" * fence.group(0).count("\n"),
        path.read_text(encoding="utf-8"),
    )
    lines = [
        (lineno, line)
        for lineno, line in enumerate(text.splitlines(), 1)
        if line.strip()
    ]
    return _runs(lines)


def _hits(path: Path, texts: list[tuple[int, str]]) -> list[str]:
    """Each text that narrates an earlier state, as ``path:line phrase``."""
    where = path.relative_to(REPO_ROOT)
    return [
        f"{where}:{lineno + text[: match.start()].count(chr(10))} {match.group(0)!r}"
        for lineno, text in texts
        for match in [HISTORY.search(" ".join(text.split("\n")))]
        if match
    ]


def history() -> list[str]:
    """Every prose text that narrates an earlier state, as ``path:line phrase``."""
    return [
        *(hit for path in source_files(*SEARCHED) for hit in _hits(path, _prose(path))),
        *(
            hit
            for path in source_files(*SCRIPTS, suffixes=(".js",))
            for hit in _hits(path, _script_prose(path))
        ),
        *(hit for path in GUIDES for hit in _hits(path, _guide_prose(path))),
    ]


def test_no_docstring_comment_or_guide_narrates_an_earlier_state():
    found = history()

    assert not found, (
        f"these lines describe how the code changed rather than what it does: "
        f"{found}. Say the current rule; the decision behind it belongs in an ADR"
        " or the ticket."
    )


def test_the_scan_reads_every_kind_of_prose(tmp_path):
    """Positive control: a docstring, a wrapped comment, a script comment and a
    guide paragraph are each seen, and code and a fenced block are not."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        '"""It used to do this."""\n'
        "x = 'the old one'  # previously that\n"
        "# a phrase that wraps: the list it\n"
        "# replaced\n"
        "def f():\n"
        '    """A rule."""\n',
        encoding="utf-8",
    )
    script = tmp_path / "page.js"
    script.write_text("const a = 1;\n// This went\n// unnoticed.\n", encoding="utf-8")
    guide = tmp_path / "guide.md"
    guide.write_text(
        "A rule.\n\n```\nused to\n```\n\nIt went\nstale.\n", encoding="utf-8"
    )

    texts = [" ".join(text.split()) for _, text in _prose(probe)]
    assert any("used to" in text for text in texts)
    assert any("previously" in text for text in texts)
    assert any("the list it replaced" in text for text in texts)
    assert not any("the old one" in text for text in texts)
    assert [" ".join(t.split()) for _, t in _script_prose(script)] == [
        "This went unnoticed."
    ]
    guide_texts = [" ".join(t.split()) for _, t in _guide_prose(guide)]
    assert "It went stale." in guide_texts
    assert not any("used to" in text for text in guide_texts)
