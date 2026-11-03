"""A docstring, a comment or a guide describes the code as it is, never how it got here.

Prose that says what a function did before the last change is wrong the day
the next change lands, and nothing fails when it does: a docstring is never
executed. It also gives every reader a second story to hold beside the code.
The repository's decisions live in ``docs/adr/`` and in the tickets, so prose
that cites one points there and describes the current rule.

The scan reads every Python docstring and comment under :data:`SEARCHED`, every
``//`` comment and every ``/* */`` comment in the scripts of :data:`SCRIPTS`,
every ``/* */`` and ``<!-- -->`` comment in the pages and style sheets of
:data:`MARKUP`, every ``#`` comment in the files of :data:`HASH_COMMENTED` and
:data:`SHELL`, every ``%%`` comment in the diagrams of :data:`DIAGRAMS`, and
every paragraph of the guides and model texts in :data:`GUIDES`. A comment at
the end of a line of code is read as well as a comment on its own line. A
string a page serves or a test asserts is data, and an identifier is not
prose, so neither is read. ``docs/adr/``, ``docs/research/``,
``docs/history/`` and ``evals/experiments/`` are records of a past state, so
they are not read either.

Each docstring, each run of comment lines and each guide paragraph is read as
one text, so a phrase that wraps across two lines is still seen. The phrases in
:data:`HISTORY` are the ones that only ever introduce an earlier state of this
code. A phrase that also has a present-tense reading, such as "no longer", is
left out on purpose, because a lint whose hits need sorting by hand is a lint
nobody reads.
"""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.source_tree import (
    HOOKS,
    MODEL_TEXT,
    PYTHON,
    REPO_ROOT,
    TESTS,
    WHOLE_TREE,
    WORKFLOWS,
    block_comments,
    comment_runs,
    guide_files,
    held_files,
    joined_runs,
    line_comments,
    parse,
    source_files,
    without,
)
from tests.test_five_class_read import HELD_ROWS

if TYPE_CHECKING:
    from five_class_read import HeldRule

pytestmark = pytest.mark.lint

SEARCHED = source_files(*PYTHON, *TESTS)

#: The page scripts and the diagram reader, whose comments are read.
SCRIPTS = source_files(*WHOLE_TREE, suffixes=(".js", ".mjs"))

#: The page templates and style sheets, whose block comments are read.
MARKUP = source_files(*PYTHON, suffixes=(".html", ".css"))

#: The guides a reader follows as a statement of the current state, and every
#: text a model reads: the prompts, each framework package's own text and the
#: Domain Packs.
GUIDES: tuple[Path, ...] = (
    *guide_files(),
    *without(
        source_files(*MODEL_TEXT, suffixes=(".md",)),
        {
            "frameworks/asvs/lanes": (
                "the ASVS lane skills carry a governed licence"
                " (docs/agents/licensing.md), and their 'used to', 'previously'"
                " and 'the old' name a key, a token or a password of the system"
                " under review"
            ),
            "domains/http-api.md": (
                "'the old' names an earlier API version of the system under"
                " review, not an earlier state of this code"
            ),
        },
    ),
)

#: Every TOML file and every workflow, whose ``#`` comments are read.
HASH_COMMENTED: tuple[Path, ...] = (
    *source_files(*WHOLE_TREE, suffixes=(".toml",)),
    *source_files(*WORKFLOWS, suffixes=(".yml",)),
)

#: The shell scripts and the git hooks, whose ``#`` comments are read.
SHELL: tuple[Path, ...] = (
    *source_files(*WHOLE_TREE, suffixes=(".sh",)),
    *held_files(*HOOKS),
)

#: The Mermaid diagram sources, whose ``%%`` comments are read.
DIAGRAMS = without(
    source_files(*WHOLE_TREE, suffixes=(".mmd",)),
    {
        "evals/experiments": (
            "frozen prose, which records an experiment as it ran and never changes"
        ),
    },
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
#:   "can", with at most two words between;
#: * "predecessor" matches only before "to this" or "of this", because a
#:   graph node has predecessors;
#: * a version's possessive matches only with a lower-case "v" before the
#:   number, because an ASVS chapter is "V6";
#: * "came to" matches only after "how" and at most five words, because a
#:   proposal "came to" a result at run time;
#: * "retired" matches only after "when" and a subject, because a key or a
#:   model is retired as a state;
#: * "was removed" matches only before "rather than", because an answer is
#:   removed at run time;
#: * "moved from" matches only after a comma, as a heading of what changed,
#:   because a tier row is moved from one vendor to another;
#: * a commit matches only after a capital "At" that opens a sentence;
#: * "what changed" matches only before "is".
#:
#: "legacy" is not matched, because a vendor's legacy name order and a legacy
#: client are facts of the present. "now" before a verb is not matched,
#: because "the price map now disagrees" and "the part now has another name"
#: describe run-time data.
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
    r"|version \d+ (\([^)]*\) )?"
    r"(adds|added|makes|made|renames|renamed|removed|dropped)"
    r"|bumped (to|from) (version |v)?\d+|bumped nothing"
    r"|hard cutover from|no v\d+ shim|(was|were) (migrated|re-?sealed)"
    r"|(was|were) once|once did"
    r"|the predecessor (to|of) this|(?-i:v\d+'s)|the first version of"
    r"|since (report )?schema \d|before this was enforced|how (\S+ ){1,5}came to"
    r"|when \w+ retired|(was|were) removed rather than|, moved from"
    r"|(?-i:(?<![\w,] )At (?=[0-9a-f]*\d)[0-9a-f]{7,40})|what changed is"
    r"|(was|were) the \w+ here)\b",
    re.IGNORECASE,
)

_DOCUMENTED = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
_FENCE = re.compile(r"^```.*?^```", re.DOTALL | re.MULTILINE)


def _prose(path: Path) -> list[tuple[int, str]]:
    """Every docstring and every run of comment lines in one file, at its line."""
    texts: list[tuple[int, str]] = []
    for node in ast.walk(parse(path)):
        if isinstance(node, _DOCUMENTED) and node.body:
            first = node.body[0]
            docstring = first.value if isinstance(first, ast.Expr) else None
            if isinstance(docstring, ast.Constant) and isinstance(docstring.value, str):
                texts.append((first.lineno, docstring.value))
    return texts + comment_runs(path)


def _script_prose(path: Path) -> list[tuple[int, str]]:
    """Every ``//`` comment in one page script, at its line."""
    return line_comments(path, "//")


def _hash_prose(path: Path) -> list[tuple[int, str]]:
    """Every ``#`` comment in one config file or shell script, at its line."""
    return line_comments(path, "#")


def _diagram_prose(path: Path) -> list[tuple[int, str]]:
    """Every ``%%`` comment in one diagram source, at its line."""
    return line_comments(path, "%%")


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
    return joined_runs(lines)


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
            for hit in _hits(path, block_comments(path))
        ),
        *(
            hit
            for path in (*HASH_COMMENTED, *SHELL)
            for hit in _hits(path, _hash_prose(path))
        ),
        *(hit for path in DIAGRAMS for hit in _hits(path, _diagram_prose(path))),
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
    config comment, a shell comment, a diagram comment and a guide paragraph
    are each seen, a comment at the end of a line of code included, and code,
    a quoted marker and a fenced block are not."""
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
        "const a = 1;\n// This went\n// unnoticed.\n/* A block\n * comment. */ /**/\n"
        'fetch("https://example.test/ // not a comment"); // trailing\n',
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
        'version = 9  # inline\n# Version 9 adds\n# a table.\nname = "a # b"\n',
        encoding="utf-8",
    )
    shell = tmp_path / "hook"
    shell.write_text(
        "# A hook.\n[ $# -eq 0 ] && exit  # done\n",
        encoding="utf-8",
    )
    diagram = tmp_path / "model.mmd"
    diagram.write_text(
        'flowchart LR\n    %% A shape key.\n    n0["a %% b"]\n', encoding="utf-8"
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
        "This went unnoticed.",
        "trailing",
    ]
    assert [(n, " ".join(t.split())) for n, t in block_comments(script)] == [
        (4, "A block comment."),
        (5, ""),
    ]
    assert [(n, " ".join(t.split())) for n, t in block_comments(page)] == [
        (2, "A page comment."),
        (5, "A style comment."),
    ]
    guide_texts = [" ".join(t.split()) for _, t in _guide_prose(guide)]
    assert "It went stale." in guide_texts
    assert not any("used to" in text for text in guide_texts)
    assert [" ".join(t.split()) for _, t in _hash_prose(config)] == [
        "Version 9 adds a table.",
        "inline",
    ]
    assert [" ".join(t.split()) for _, t in _hash_prose(shell)] == [
        "A hook.",
        "done",
    ]
    assert [" ".join(t.split()) for _, t in _diagram_prose(diagram)] == ["A shape key."]
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
        "Version 2 (wayfinder #15) dropped the four backoff knobs.",
        "The predecessor to this rule was an allowlist.",
        "a deployment still carrying v2's ANALYSIS_MODEL_FLASH",
        "The first version of these tests called the override.",
        "Per block since schema 3.0.",
        "Cases that shipped before this was enforced are unread.",
        "That is how the warning came to name the wrong cause.",
        "It broke when Google retired them.",
        "That is why the schedule was removed rather than left to fail.",
        "The truncation failure, moved from request time to build time.",
        "At 2fae7d3 the page kept a part hidden.",
        "What changed is that the score says so.",
        "`endsWith` was the spelling here.",
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
        "A retired model is refused.",
        "A retired key still verifies what it signed.",
        "It waits for every predecessor of the node.",
        "V6's opening section asks for the design.",
        "What one proposal came to against the graph.",
        "Staging-only TLS was removed by an answer.",
        "A tier row moved from one vendor to another keeps its rule.",
        "The ledger is pinned at 2e9d31be.",
        "What changed in the list since the last payload.",
        "The vendor's legacy order puts the generation first.",
        "The price map now disagrees with the Baseline.",
    )
    for text in present:
        assert not HISTORY.search(text), text


#: The money words the maintainer ruled out for a technical fact. A literal
#: money word such as "bill" for a provider charge is not here. Case-insensitive.
MONEY = re.compile(r"\b(debts?|clean bill|ow(e|es|ed|ing))\b", re.IGNORECASE)

#: Every code file of the project and its tests, every guide, the config files
#: and workflows, and every text a model reads, read whole: comments,
#: docstrings, strings and identifiers. A hit in model text that a row of
#: ``.github/scripts/held_wording.toml`` holds for the lint's rule is skipped.
MONEY_SEARCHED = without(
    [
        *source_files(*PYTHON, *TESTS, suffixes=(".py", ".js", ".html", ".css")),
        *guide_files(),
        *HASH_COMMENTED,
        *source_files(*MODEL_TEXT, suffixes=(".md",)),
    ],
    {
        "evals/experiments": (
            "frozen prose, which records an experiment as it ran and never changes"
        ),
        "tests/test_history_prose_lints.py": (
            "this module states the words the lint refuses, so the lint always"
            " finds them here"
        ),
        ".github/scripts/held_wording.toml": (
            "each row quotes a held phrase exactly, and the lints read the rows"
            " to skip that phrase where it is held"
        ),
    },
)


def held(rule: HeldRule) -> dict[str, tuple[str, ...]]:
    """Each file against the phrases that the held rows of ``rule`` hold in it."""
    phrases: dict[str, tuple[str, ...]] = {}
    for row in HELD_ROWS:
        if row["rule"] == rule:
            phrases[row["file"]] = (*phrases.get(row["file"], ()), row["phrase"])
    return phrases


def _wording_hits(
    pattern: re.Pattern[str],
    paths: Iterable[Path],
    phrases: Mapping[str, Iterable[str]],
) -> list[str]:
    """Each match of ``pattern`` in ``paths`` that no phrase of its file in
    ``phrases`` covers on its line, as ``path:line 'match'``."""
    found: list[str] = []
    for path in paths:
        where = path.relative_to(REPO_ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            spans = [
                occurrence.span()
                for phrase in phrases.get(where, ())
                for occurrence in re.finditer(re.escape(phrase), line)
            ]
            found.extend(
                f"{where}:{lineno} {match.group(0)!r}"
                for match in pattern.finditer(line)
                if not any(
                    start <= match.start() and match.end() <= end
                    for start, end in spans
                )
            )
    return found


def test_no_file_uses_a_ruled_out_money_word():
    """A money word for a technical fact is a metaphor that a reader must decode.

    The sentence names the concrete fact instead: what the code drops, refuses,
    runs, needs or gives.
    """
    found = _wording_hits(MONEY, MONEY_SEARCHED, held("money"))

    assert not found, (
        f"these lines use a ruled-out money word: {found}. Say what the code"
        " drops, refuses, runs, needs or gives. In model text, a hit stays only"
        " where a row of .github/scripts/held_wording.toml holds it."
    )


#: What a metaphorical "spend" spends: a thing that is never money. That is a
#: choice, a read, a place, a vote, an answer, a judgement, a pass, a quote, a
#: question, effort, attention, time, seconds or minutes, a turn, a retry or an
#: attempt. Each is counted, used or taken, never paid. Case-insensitive.
_SPENT_THING = (
    r"(?:choices?|reads?|places?|votes?|answers?|judge?ments?|pass(?:es)?|quotes?"
    r"|questions?|efforts?|attention|time|seconds?|minutes?|turns?|retr(?:y|ies)"
    r"|attempts?)"
)

#: A budget that is not money, such as a retry budget or a budget of scan work.
#: A budget of a vendor, a model or tokens buys model calls, so it is money.
_SPENT_BUDGET = r"(?<!vendor )(?<!model )(?<!token )(?<!tokens )budgets?"

#: A word between "spend" and what it spends. "And" opens a new phrase, and a
#: spend "on" or "over" something names what money buys or a period of it.
_BETWEEN = r"(?:(?!(?:and|on|over)\b)[\w'`-]+ )"

SPEND = re.compile(
    rf"\b(?:re-?)?spen(?:d|ds|t|ding) {_BETWEEN}{{0,3}}?"
    rf"(?:{_SPENT_THING}|{_SPENT_BUDGET})\b"
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
    found = _wording_hits(SPEND, SPEND_SEARCHED, held("spend"))

    assert not found, (
        f"these lines spend something that is not money: {found}. Say that the"
        " code counts, uses, takes or makes it. In model text, a hit stays only"
        " where a row of .github/scripts/held_wording.toml holds it."
    )


def test_the_spend_pattern_matches_only_a_metaphor():
    """Positive control: a spend of a thing that is never money matches, and a
    spend of money, of quota or of a vendor's budget does not."""
    for text in (
        "Each save spends at least one choice.",
        "Each case read spends one of its reads.",
        "A vote is spent once.",
        "an `unsure` is a spent answer",
        "the critic's rule against re-spending judgement",
        "it would spend `repair`'s single pass",
        "and it spends a quote",
        "Spend your effort on the flows",
        "spend your attention on a lane",
        "A body that has spent its thirty seconds",
        "it spends minutes on a retry",
        "the model spends a turn on it",
        "the process had spent its retry budget",
        "a run that spends its budget on retried 429s",
        "asking anyway spends an attempt",
        "the scan is cached, and spent against a budget",
    ):
        assert SPEND.search(text), text
    for text in (
        "the audit would spend $0.79 of $1.00",
        "a provider-side spend limit",
        "price a fix before spending and read a band",
        "a reader spent a week on words",
        "the walk takes at most its budget",
        "a save runs nothing and spends nothing",
        "a contributor may spend any amount",
        "a wide filter would spend your vendor budget",
        "a wider filter would spend live model budget",
        "the smoke run spends no quota",
        "nobody has chosen to spend on a second route",
        "the window bounds the spend over time",
        "the budget is spent against the job's bytes",
        "a settled draft: spend nothing on it",
    ):
        assert not SPEND.search(text), text


def test_the_money_words_match_only_the_ruled_out_words():
    """Positive control: each ruled-out word matches, and a word that holds one
    of them inside it does not."""
    for word in ("debt", "Debts", "a clean bill", "owe", "owes", "owed", "owing"):
        assert MONEY.search(word), word
    for word in ("bowed", "lower", "billed", "the provider bill", "indebted"):
        assert not MONEY.search(word), word


def test_the_wording_lints_read_model_text():
    prompt = REPO_ROOT / "prompts" / "critic.md"

    assert prompt in MONEY_SEARCHED
    assert prompt in SPEND_SEARCHED


def test_a_hit_in_model_text_that_no_row_holds_fails(tmp_path, monkeypatch):
    """Positive control: a held phrase frees the hit it covers in its own file,
    and nothing else: not the same words in another file, and not a hit that it
    does not cover."""
    monkeypatch.setattr(sys.modules[__name__], "REPO_ROOT", tmp_path)
    prompt = tmp_path / "prompts" / "probe.md"
    prompt.parent.mkdir()
    prompt.write_text(
        "A rule.\nNever spend a vote on it. Spend a quote.\n", encoding="utf-8"
    )
    vote = f"prompts/probe.md:2 {'spend a vote'!r}"
    quote = f"prompts/probe.md:2 {'Spend a quote'!r}"

    assert _wording_hits(SPEND, [prompt], {}) == [vote, quote]
    assert _wording_hits(
        SPEND, [prompt], {"prompts/probe.md": ["Never spend a vote on it"]}
    ) == [quote]
    assert _wording_hits(
        SPEND, [prompt], {"prompts/other.md": ["Never spend a vote on it"]}
    ) == [vote, quote]
    assert _wording_hits(SPEND, [prompt], {"prompts/probe.md": ["a vote on"]}) == [
        vote,
        quote,
    ]
