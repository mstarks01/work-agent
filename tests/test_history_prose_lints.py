"""A docstring, a comment or a guide describes the code as it is, never how it got here.

Prose that says what a function did before the last change is wrong the day
the next change lands, and nothing fails when it does: a docstring is never
executed. It also gives every reader a second story to hold beside the code.
The repository's decisions live in ``docs/adr/`` and in the tickets, so prose
that cites one points there and describes the current rule.

The scan reads every Python docstring and comment under :data:`SEARCHED`, every
full-line ``//`` comment and every ``/* */`` comment in the scripts of
:data:`SCRIPTS`, every ``/* */`` and ``<!-- -->`` comment in the pages and
style sheets of :data:`MARKUP`, every full-line ``#`` comment in the files of
:data:`HASH_COMMENTED`, and every paragraph of the guides in :data:`GUIDES`. A
string a page serves or a test asserts is data, and an identifier is not
prose, so neither is read. ``docs/adr/``, ``docs/research/``
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

import pytest

from tests.source_tree import (
    PYTHON,
    REPO_ROOT,
    TESTS,
    WHOLE_TREE,
    WORKFLOWS,
    guide_files,
    parse,
    prompt_files,
    source_files,
    without,
)

pytestmark = pytest.mark.lint

SEARCHED = source_files(*PYTHON, *TESTS)

#: The page scripts and the diagram reader, whose comments are read.
SCRIPTS = source_files(*WHOLE_TREE, suffixes=(".js", ".mjs"))

#: The page templates and style sheets, whose block comments are read.
MARKUP = source_files(*PYTHON, suffixes=(".html", ".css"))

#: The guides a reader follows as a statement of the current state, and the
#: prompts a model reads as its instructions.
GUIDES: tuple[Path, ...] = (
    *guide_files(),
    *without(
        prompt_files(),
        {
            "frameworks": (
                "a lane skill's passive 'used' phrases read as history to this"
                " scan, and the ASVS ones carry a governed licence"
                " (docs/agents/licensing.md)"
            ),
        },
    ),
)

#: Every TOML file and every workflow, whose full-line ``#`` comments are read.
HASH_COMMENTED: tuple[Path, ...] = (
    *source_files(*WHOLE_TREE, suffixes=(".toml",)),
    *source_files(*WORKFLOWS, suffixes=(".yml",)),
)

#: Each phrase names an earlier state of the code. Case-insensitive. A phrase
#: that also describes run-time data is held to its history reading:
#:
#: * "as it was before" and "rather than before" are not matched;
#: * a singular "an earlier version of" matches only before "this file",
#:   "this module", "this note" or "this page", because a finding and a
#:   target have versions too;
#: * "replaced" matches only between "which", "that", "this" or "it" and
#:   "the", or in the sentence of a cutover;
#: * a rename matches only as "renamed from";
#: * a "now it" phrase matches only at the start of a sentence, and only
#:   before "is", "was" or "does";
#: * "any more" matches only after a negated "does", "do", "is", "are" or
#:   "can", with at most two words between.
HISTORY = re.compile(
    r"\b(used to|was written for|before this (\w+ )?(existed|one)|previously"
    r"|had been|until now|the old |now that|in the past|formerly|originally"
    r"|historically|once (had|took|wrote|sat|listed|lacked|missed|floored|ended)"
    r"|before this[,:.]|until #\d+|this replaced|(branch|if`?|list) it replaced"
    r"|went stale|went unnoticed|nothing caught"
    r"|an earlier version of this (note|module|page|file)"
    r"|earlier versions of|of earlier versions|as before|(?<!rather )than before"
    r"|(which|that|this|it) replaced the|cutover[^.]{0,80}\breplaced"
    r"|(was|were) renamed from|(?<![\w,] )now it (is|was|does)"
    r"|(does|do|is|are|can)(n't| not) (\w+ ){0,2}any ?more|anymore"
    r"|version \d+ (adds|added|makes|made|renames|renamed|removed)"
    r"|bumped (to|from) (version |v)?\d+|bumped nothing"
    r"|hard cutover from|no v\d+ shim|(was|were) (migrated|re-?sealed)"
    r"|(was|were) once|once did)\b",
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


_BLOCK = re.compile(r"/\*(.*?)\*/|<!--(.*?)-->", re.DOTALL)


def _block_prose(path: Path) -> list[tuple[int, str]]:
    """Every ``/* */`` and ``<!-- -->`` comment in one script, page or style
    sheet, at its first line."""
    source = path.read_text(encoding="utf-8")
    return [
        (
            source.count("\n", 0, block.start()) + 1,
            "\n".join(
                line.strip().removeprefix("*").strip()
                for line in block.group(block.lastindex or 0).splitlines()
            ),
        )
        for block in _BLOCK.finditer(source)
    ]


def _hash_prose(path: Path) -> list[tuple[int, str]]:
    """Every run of full-line ``#`` comments in one config file, at its line."""
    comments = [
        (lineno, line.strip().removeprefix("#").strip())
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if line.lstrip().startswith("#")
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
        *(hit for path in SEARCHED for hit in _hits(path, _prose(path))),
        *(hit for path in SCRIPTS for hit in _hits(path, _script_prose(path))),
        *(
            hit
            for path in (*SCRIPTS, *MARKUP)
            for hit in _hits(path, _block_prose(path))
        ),
        *(hit for path in HASH_COMMENTED for hit in _hits(path, _hash_prose(path))),
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
    """Positive control: a docstring, a wrapped comment, a script comment, a
    config comment and a guide paragraph are each seen, and code and a fenced
    block are not."""
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
    script.write_text(
        "const a = 1;\n// This went\n// unnoticed.\n/* A block\n * comment. */ /**/\n",
        encoding="utf-8",
    )
    page = tmp_path / "page.html"
    page.write_text(
        '<p title="as before">x</p>\n<!-- A page\n     comment. -->\n'
        "<style>\n  /* A style\n     comment. */\n</style>\n",
        encoding="utf-8",
    )
    config = tmp_path / "config.toml"
    config.write_text(
        "version = 9  # inline\n# Version 9 adds\n# a table.\n", encoding="utf-8"
    )
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
    assert [(n, " ".join(t.split())) for n, t in _block_prose(script)] == [
        (4, "A block comment."),
        (5, ""),
    ]
    assert [(n, " ".join(t.split())) for n, t in _block_prose(page)] == [
        (2, "A page comment."),
        (5, "A style comment."),
    ]
    guide_texts = [" ".join(t.split()) for _, t in _guide_prose(guide)]
    assert "It went stale." in guide_texts
    assert not any("used to" in text for text in guide_texts)
    assert [" ".join(t.split()) for _, t in _hash_prose(config)] == [
        "Version 9 adds a table."
    ]
    assert HISTORY.search("Version 9 adds a table.")
    assert not HISTORY.search("Version 1 drops the type prefix.")
    assert not HISTORY.search("an earlier reference bumped to a different position")


def test_the_history_phrases_hold_to_their_history_reading():
    """Positive control: each phrase matches where it narrates an earlier state,
    and the same words that describe run-time data do not."""
    history = (
        "A record naming none behaves exactly as before.",
        "It is faster than before.",
        "Earlier versions of this file shipped a model.",
        "The list replaced the single description of earlier versions.",
        "The cutover (#8, 2026-07-28) replaced it with one adapter.",
        "The field was renamed from kind.",
        "It was evicted (F4). Now it is refused.",
        "The page does not read it any more.",
        "Nothing reads it anymore.",
    )
    for text in history:
        assert HISTORY.search(text), text
    present = (
        "It puts each element back as it was before the repair.",
        "It runs on every merge rather than before a release.",
        "Every vote cast on an earlier version of the finding.",
        "The note replaced the agent's wording.",
        "A flow whose endpoint was renamed under a ruling.",
        "The page reads it now, and now it is shown.",
        "A record that names no finding any more is dropped.",
    )
    for text in present:
        assert not HISTORY.search(text), text


#: The money words the maintainer ruled out for a technical fact. A literal
#: money word such as "bill" for a provider charge is not here. Case-insensitive.
MONEY = re.compile(r"\b(debts?|clean bill|ow(e|es|ed|ing))\b", re.IGNORECASE)

#: Every code file of the project and its tests, every guide, and the config
#: files and workflows, read whole: comments, docstrings, strings and
#: identifiers. The prompts are not here: a prompt edit changes model input.
MONEY_SEARCHED = without(
    [
        *source_files(*PYTHON, *TESTS, suffixes=(".py", ".js", ".html", ".css")),
        *guide_files(),
        *HASH_COMMENTED,
    ],
    {
        "evals/experiments": (
            "frozen prose, which records an experiment as it ran and never changes"
        ),
        "tests/test_history_prose_lints.py": (
            "this module states the words the lint refuses, so the lint always"
            " finds them here"
        ),
    },
)


def test_no_file_uses_a_ruled_out_money_word():
    """A money word for a technical fact is a metaphor that a reader must decode.

    The sentence names the concrete fact instead: what the code drops, refuses,
    runs, needs or gives.
    """
    found = [
        f"{path.relative_to(REPO_ROOT)}:{lineno} {match.group(0)!r}"
        for path in MONEY_SEARCHED
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        for match in MONEY.finditer(line)
    ]

    assert not found, (
        f"these lines use a ruled-out money word: {found}. Say what the code"
        " drops, refuses, runs, needs or gives."
    )


#: What a metaphorical "spend" spends: a choice, a read, a place, a vote, an
#: answer, a judgement, a pass, a quote or a question. Each is counted, used or
#: taken, never paid. A literal spend of money, CPU time or wall time names
#: none of these, so it is not matched. Case-insensitive.
_SPENT_THING = (
    r"(?:choices?|reads?|places?|votes?|answers?|judge?ments?|pass|quote|question)"
)
SPEND = re.compile(
    rf"\b(?:re-?)?spen(?:d|ds|t|ding) (?:(?!and\b)\S+ ){{0,3}}?{_SPENT_THING}\b"
    rf"|\b{_SPENT_THING} (?:is|are|was|were) (?:\w+ )?spent\b"
    rf"|\bspent {_SPENT_THING}\b",
    re.IGNORECASE,
)

#: The files the spend lint reads: those of the money lint, but the prompt
#: outcome table, whose rows quote prompt sentences word for word.
SPEND_SEARCHED = without(
    MONEY_SEARCHED,
    {
        ".github/scripts/prompt_outcomes.toml": (
            "each outcome row quotes a prompt sentence exactly, and a prompt edit"
            " changes model input"
        ),
    },
)


def test_no_file_spends_a_choice_a_read_or_a_vote():
    """A choice, a read or a vote is counted or used, never spent.

    The sentence says what the code does with it: counts it, uses it, takes it
    or makes it.
    """
    found = [
        f"{path.relative_to(REPO_ROOT)}:{lineno} {match.group(0)!r}"
        for path in SPEND_SEARCHED
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        for match in SPEND.finditer(line)
    ]

    assert not found, (
        f"these lines spend something that is not money: {found}. Say that the"
        " code counts, uses, takes or makes it."
    )


def test_the_spend_pattern_matches_only_a_metaphor():
    """Positive control: a spend of a choice, a read, a vote or a pass matches,
    and a spend of money, of time or of a retry token does not."""
    for text in (
        "Each save spends at least one choice.",
        "Each case read spends one of its reads.",
        "A vote is spent once.",
        "an `unsure` is a spent answer",
        "the critic's rule against re-spending judgement",
        "it would spend `repair`'s single pass",
        "and it spends a quote",
    ):
        assert SPEND.search(text), text
    for text in (
        "the audit would spend $0.79 of $1.00",
        "a provider-side spend limit",
        "price a fix before spending and read a band",
        "a reader spent a week on words",
        "the walk takes at most its budget",
    ):
        assert not SPEND.search(text), text


def test_the_money_words_match_only_the_ruled_out_words():
    """Positive control: each ruled-out word matches, and a word that holds one
    of them inside it does not."""
    for word in ("debt", "Debts", "a clean bill", "owe", "owes", "owed", "owing"):
        assert MONEY.search(word), word
    for word in ("bowed", "lower", "billed", "the provider bill", "indebted"):
        assert not MONEY.search(word), word
