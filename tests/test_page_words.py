"""The words a page shows for a closed service set live in one Python table.

Each table of :mod:`webapp.page_words` holds every value of its ``Literal``,
each page carries its tables in a JSON island, and no page script holds a copy
of the words.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import get_args

import pytest

from analysis_service.claims import GroundKind, ScopeState, SeverityLevel, VerdictStatus
from analysis_service.fact_answers import FactStatus
from analysis_service.report_changes import Change, FindingChange
from tests.factories import report_state, sample_report
from tests.test_webapp import _island, page_javascript
from webapp import page_words
from webapp.main import render_report

REPORT_TABLES = {
    "WHY_OPEN": FactStatus,
    "QUESTION_HISTORY": FactStatus,
    "SCOPE_STATE_WORDS": ScopeState,
    "UNIT_RULING_WORDS": page_words.UnitRuling,
    "GROUND_KIND_WORDS": GroundKind,
    "SEVERITY_LABELS": SeverityLevel,
    "RECOMMENDATION_HEADINGS": VerdictStatus,
    "CHANGE_LINES": Change,
}


def _texts(table: object) -> Iterator[str]:
    """Every string a table holds, at any depth."""
    if isinstance(table, str):
        yield table
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


@pytest.mark.parametrize(("table", "vocabulary"), REPORT_TABLES.items())
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
    held = {id(table) for table in page_words.REPORT_WORDS.values()}
    absent = [
        name for name in REPORT_TABLES if id(getattr(page_words, name)) not in held
    ]
    assert absent == ["CHANGE_LINES"]


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
