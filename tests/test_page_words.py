"""The words a page shows for a closed service set live in one Python table.

Each table of :mod:`webapp.page_words` holds every value of its ``Literal``,
each page carries its tables in a JSON island, and no page script holds a copy
of the words.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from typing import get_args

import pytest

from analysis_service.answer_state import EmptyListReason
from analysis_service.claims import (
    GroundKind,
    ScopeState,
    SeverityLevel,
    UnitRuling,
    VerdictStatus,
)
from analysis_service.fact_answers import FACET_ANSWERS, FactStatus
from analysis_service.frameworks import PreconditionResult
from analysis_service.job_state import PageStatus
from analysis_service.report_changes import Change, FindingChange
from tests.factories import report_state, sample_report
from tests.test_webapp import PROJECT_ROOT, _island, page_javascript
from webapp import page_words, review, settings_view
from webapp.main import render_report

FORM_TABLES = {
    "PAGE_STATUS_LABELS": PageStatus,
    "GATE_NOTES": PreconditionResult,
    "EMPTY_LIST_REASONS": EmptyListReason,
    "PROGRESS_PANEL_WORDS": page_words.ProgressPanelLine,
}

REPORT_TABLES = {
    "WHY_OPEN": FactStatus,
    "QUESTION_HISTORY": FactStatus,
    "SCOPE_STATE_WORDS": ScopeState,
    "UNIT_RULING_WORDS": UnitRuling,
    "GROUND_KIND_WORDS": GroundKind,
    "SEVERITY_LABELS": SeverityLevel,
    "RECOMMENDATION_HEADINGS": VerdictStatus,
    "CHANGE_LINES": Change,
}


def _held(words: Mapping[str, object]) -> set[int]:
    """The identity of each table an island holds."""
    return {id(table) for table in words.values()}


def _named(tables: Mapping[str, object]) -> set[int]:
    """The identity of each table a list of this file names, so a table that
    an island holds and no list names fails a test."""
    return {id(getattr(page_words, name)) for name in tables}


def _texts(table: object) -> Iterator[str]:
    """Every string a table holds, at any depth."""
    if isinstance(table, str):
        yield table
    elif table is None:
        return
    elif isinstance(table, dict):
        for value in table.values():
            yield from _texts(value)
    else:
        for value in table:  # type: ignore[attr-defined]
            yield from _texts(value)


def _copies(words: object, script: str) -> list[str]:
    """The texts of ``words`` that ``script`` spells out.

    A text of several words is read as a substring. A single word is read as a
    quoted string, because a script compares a key that spells the same word.
    """
    return [
        text
        for text in _texts(words)
        if text and (text in script if " " in text else f'"{text}"' in script)
    ]


@pytest.mark.parametrize(("table", "vocabulary"), (REPORT_TABLES | FORM_TABLES).items())
def test_each_table_names_each_member_of_its_set(table, vocabulary):
    """A value the table lacks shows as ``undefined`` on the page."""
    assert set(getattr(page_words, table)) == set(get_args(vocabulary))


def test_the_report_page_reads_each_table_from_its_island():
    report = sample_report()
    page = render_report(
        report, report_state(report), previous=None, parent=None, controls=()
    ).html
    sent = _island(page, "report_words")
    script = page_javascript("report_view.js")

    for name, table in page_words.REPORT_WORDS.items():
        assert sent[name] == {
            key: list(words) if isinstance(words, tuple) else words
            for key, words in table.items()
        }
        assert f"WORDS.{name}" in script, name


def test_the_report_words_hold_each_report_table():
    """Each table of this file reaches the page: in the island, or as the
    ``line`` of a change."""
    assert _held(page_words.REPORT_WORDS) == _named(REPORT_TABLES) - {
        id(page_words.CHANGE_LINES)
    }


def test_the_form_words_hold_each_form_table():
    assert _held(page_words.FORM_WORDS) == _named(FORM_TABLES)


def test_the_style_sheet_names_a_colour_for_each_severity_level():
    """``report_view.js`` reads the colour of a level as ``--sev-<level>``."""
    sheet = (PROJECT_ROOT / "webapp" / "report_view.html").read_text(encoding="utf-8")
    assert set(re.findall(r"--sev-([\w-]+):", sheet)) == set(get_args(SeverityLevel))


@pytest.mark.parametrize(
    ("words", "keys"),
    [
        (page_words.REPORT_WORDS, page_words.ReportTable),
        (page_words.FORM_WORDS, page_words.FormTable),
    ],
)
def test_each_island_names_each_of_its_keys(words, keys):
    """A script reads each table by its key in the island."""
    assert set(words) == set(get_args(keys))


def test_the_report_script_copies_no_word_of_a_table():
    script = page_javascript("report_view.js")
    tables = {name: dict(getattr(page_words, name)) for name in REPORT_TABLES}
    # ``new`` and ``unchanged`` spell their own keys, which the script compares.
    del tables["CHANGE_LINES"]["new"], tables["CHANGE_LINES"]["unchanged"]

    assert _copies(tables, script) == []


def test_a_change_line_carries_the_verdict_labels():
    def change(kind, before, after):
        return FindingChange("stride", "T1", "title", kind, before, after)

    assert page_words.change_line(change("new", None, "confirmed")) == "new"
    assert page_words.change_line(change("gone", "confirmed", None)) == (
        "no longer raised"
    )
    assert page_words.change_line(change("changed", "needs-info", "rejected")) == (
        "Needs info → Rejected"
    )


def test_the_report_page_carries_the_facet_answers():
    """The answer editor reads the facet answers on each page that loads it."""
    report = sample_report()
    page = render_report(
        report, report_state(report), previous=None, parent=None, controls=()
    ).html
    assert _island(page, "facet_answers") == list(FACET_ANSWERS)


def test_the_form_scripts_copy_no_word_of_a_table():
    scripts = "".join(
        page_javascript(name)
        for name in ("workspace.js", "first_run.js", "answer_editor.js")
    )
    tables = {name: dict(getattr(page_words, name)) for name in FORM_TABLES}
    sentences = [
        part
        for text in _texts(tables)
        for part in re.split(r"\{\w+\}", text)
        if " " in part.strip()
    ]

    assert _copies(sentences, scripts) == []
    assert _copies([*FACET_ANSWERS], page_javascript("answer_editor.js")) == []


@pytest.mark.parametrize(
    ("table", "vocabulary", "script"),
    [
        (settings_view.ORIGIN_WORDS, settings_view.ValueOrigin, "settings.js"),
        (review.REASON_KIND_HEADINGS, review.ReasonKind, "review_finding.js"),
    ],
)
def test_a_table_that_an_answer_carries_names_its_set_and_has_no_copy(
    table, vocabulary, script
):
    """The words of these tables reach the page in the rows of an answer.
    ``refused`` and ``default`` spell their own keys, which ``settings.js``
    reads, so the copy check reads the texts of several words."""
    assert set(table) == set(get_args(vocabulary))
    assert (
        _copies(
            [text for text in _texts(dict(table)) if " " in text],
            page_javascript(script),
        )
        == []
    )


def test_each_template_holds_only_the_fields_its_script_fills():
    """A script fills ``{name}`` of a gate note, ``{held_back}`` of an
    empty-list reason, and ``{clock}``, ``{complete}`` and ``{count}`` of the
    lines of the progress panel. A field with another name shows as its
    braces."""
    fields = {
        name: set(re.findall(r"\{(\w+)\}", " ".join(_texts(dict(table)))))
        for name, table in page_words.FORM_WORDS.items()
    }
    assert fields == {
        "page_status": set(),
        "gate_notes": {"name"},
        "empty_list": {"held_back"},
        "progress_panel": {"clock", "complete", "count"},
    }
    panel = page_javascript("workspace.js")
    assert '["still-working"].replace("{clock}", clock)' in panel
    assert (
        '["stages-complete"].replace("{complete}", progress.value)'
        '.replace("{count}", progress.max)'
    ) in panel
    script = page_javascript("first_run.js")
    assert 'gate_notes[state].replace("{name}", name)' in script
    assert 'empty_list[data.early_stop].replace("{held_back}", heldBack)' in script
