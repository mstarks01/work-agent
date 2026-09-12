"""A docstring or a comment describes the code as it is, never how it got here.

Prose that says what a function did before the last change is wrong the day
the next change lands, and nothing fails when it does: a docstring is never
executed. It also
costs every reader a second story to hold beside the code. The repository's
decisions live in ``docs/adr/`` and in the tickets, so a docstring that cites
one points there and describes the current rule.

The scan covers every docstring and every comment under :data:`SEARCHED`, and
nothing else: a string a page serves or a test asserts is data, and an
identifier is not prose. The phrases in :data:`HISTORY` are the ones that only
ever introduce an earlier state of this code. A phrase that also has a
present-tense reading, such as "no longer", is left out on purpose, because a
lint whose hits need sorting by hand is a lint nobody reads.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize

from tests.source_tree import REPO_ROOT, parse, source_files

SEARCHED = ("src", "evals", "webapp", "tests")

#: Each phrase names an earlier state of the code. Case-insensitive.
HISTORY = re.compile(
    r"\b(used to|was written for|before this (\w+ )?(existed|one)|previously"
    r"|had been|until now|the old |now that|in the past|formerly|originally"
    r"|historically)\b",
    re.IGNORECASE,
)

_DOCUMENTED = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _prose(path) -> list[tuple[int, str]]:
    """Every docstring and comment line in one file, with its line number."""
    lines: list[tuple[int, str]] = []
    for node in ast.walk(parse(path)):
        if isinstance(node, _DOCUMENTED) and node.body:
            first = node.body[0]
            docstring = first.value if isinstance(first, ast.Expr) else None
            if isinstance(docstring, ast.Constant) and isinstance(docstring.value, str):
                for offset, text in enumerate(docstring.value.splitlines()):
                    lines.append((first.lineno + offset, text))
    text = path.read_text(encoding="utf-8")
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type == tokenize.COMMENT:
            lines.append((token.start[0], token.string))
    return lines


def history() -> list[str]:
    """Every prose line that narrates an earlier state, as ``path:line phrase``."""
    found: list[str] = []
    for path in source_files(*SEARCHED):
        for lineno, text in _prose(path):
            if match := HISTORY.search(text):
                found.append(
                    f"{path.relative_to(REPO_ROOT)}:{lineno} {match.group(0)!r}"
                )
    return found


def test_no_docstring_or_comment_narrates_an_earlier_state():
    found = history()

    assert not found, (
        f"these lines describe how the code changed rather than what it does: "
        f"{found}. Say the current rule; the decision behind it belongs in an ADR"
        " or the ticket."
    )


def test_the_scan_reads_both_kinds_of_prose():
    """Positive control: a docstring and a comment are each seen, code is not."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "probe.py"
        probe.write_text(
            '"""It used to do this."""\n'
            "x = 'the old one'  # previously that\n"
            "def f():\n"
            '    """A rule."""\n',
            encoding="utf-8",
        )
        lines = [text for _, text in _prose(probe)]

    assert any("used to" in text for text in lines)
    assert any("previously" in text for text in lines)
    assert not any("the old one" in text for text in lines)
