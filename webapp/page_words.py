"""The words a page shows for each value of a closed service set.

Each table is keyed by the ``Literal`` that names the set, and a test holds its
keys to that ``Literal``. A page reads its tables from a JSON island, so a page
script holds no copy of the words and a value the service adds cannot render as
a raw key.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Literal

from analysis_service.answer_state import EmptyListReason
from analysis_service.claims import (
    VERDICT_LABELS,
    GroundKind,
    ScopeState,
    SeverityLevel,
    UnitRuling,
    VerdictStatus,
)
from analysis_service.fact_answers import FactStatus
from analysis_service.frameworks import PreconditionResult
from analysis_service.report_changes import Change, FindingChange

#: Why an open fact is still open, as the owner reads it under a finding.
WHY_OPEN: Mapping[FactStatus, str] = MappingProxyType(
    {
        "unknown": 'nobody knew: an answer said "I don\'t know"',
        "partial": (
            "you answered part of it, and the parts left open keep it conditional"
        ),
        "skipped": "skipped before the analysis",
        "unanswered": "shown before the analysis and left blank",
        "answered": "you answered it, and the analysis still did not find it settled",
        "open": "not asked yet",
    }
)

#: What became of a question before this report, in the words its row shows.
QUESTION_HISTORY: Mapping[FactStatus, str] = MappingProxyType(
    {
        "open": "New question from this analysis",
        "unanswered": "Earlier answer: left blank",
        "skipped": "Earlier answer: skipped",
        "partial": "Earlier answer: answered in part",
        "unknown": "Earlier answer: I don't know",
        "answered": "Earlier answer: given, and the analysis did not find it settled",
    }
)

#: The label and the meaning of each scope state on a unit's row. No entry
#: says "satisfied", and ``not-raised`` does not read as "applies": the only
#: fact is that no lane filed on the unit (#659).
SCOPE_STATE_WORDS: Mapping[ScopeState, tuple[str, str]] = MappingProxyType(
    {
        "not-raised": (
            "No claim filed",
            (
                "The lane ran and raised nothing on this unit. That is not a"
                " finding that it is met, and not a ruling that it applies."
            ),
        ),
        "not-applicable": ("Does not apply", "Ruled out for a system of this shape."),
        "undecidable": (
            "Never decided",
            (
                "The input never said whether this framework applies at all, so"
                " no lane ran."
            ),
        ),
        "needs-other-evidence": (
            "Needs other evidence",
            (
                "The unit applies and this input cannot settle it. Supplying the"
                " evidence named below can."
            ),
        ),
    }
)

#: The label and the meaning of a rejected claim that rules on its unit, by the
#: step that ended it. The page says which reader ruled.
UNIT_RULING_WORDS: Mapping[UnitRuling, tuple[str, str]] = MappingProxyType(
    {
        "evidence": (
            "Excluded by review",
            "A lane excluded it and the critic agreed. The rule's own answer is above.",
        ),
        "control": (
            "Covered by a Control",
            "The owner states that a Control covers it. The service verified nothing.",
        ),
    }
)

#: Each kind of ground in its own words. The two attribute kinds carry the same
#: fields, and so do the two assertion kinds, so these words tell "nobody said"
#: from "somebody said".
GROUND_KIND_WORDS: Mapping[GroundKind, str] = MappingProxyType(
    {
        "quote": "Quoted from the submission",
        "unknown-attribute": "Unstated in the submission",
        "absent-attribute": "Stated absent in the submission",
        "derived-fact": "Derived from the model",
        "absent-element": "Named nowhere in the submission",
        "assertion": "Stated as a fact in the submission",
        "unknown-assertion": "Left open by the submission",
    }
)

#: The label of each severity level.
SEVERITY_LABELS: Mapping[SeverityLevel, str] = MappingProxyType(
    {"critical": "Critical", "high": "High", "medium": "Medium", "low": "Low"}
)

#: The heading over a finding's recommendations, by its verdict. Those of a
#: conditional finding are not actions yet, and those of a dismissed finding
#: are not actions at all.
RECOMMENDATION_HEADINGS: Mapping[VerdictStatus, str] = MappingProxyType(
    {
        "confirmed": "Recommended actions",
        "needs-info": "Potential mitigations · if confirmed",
        "rejected": "Original proposed mitigations · not active actions",
    }
)

#: What the tally of a follow-up says for each kind of change. ``{before}`` and
#: ``{after}`` stand for the two verdict labels.
CHANGE_LINES: Mapping[Change, str] = MappingProxyType(
    {
        "new": "new",
        "unchanged": "unchanged",
        "changed": "{before} → {after}",
        "gone": "no longer raised",
    }
)


def change_line(change: FindingChange) -> str:
    """The tally line of one finding's change, with its verdict labels."""
    return CHANGE_LINES[change.change].format(
        **{
            name: VERDICT_LABELS[status]
            for name, status in (("before", change.before), ("after", change.after))
            if status is not None
        }
    )


#: The keys of the ``report_words`` island, as ``report_view.js`` reads them.
ReportTable = Literal[
    "why_open",
    "question_history",
    "scope_state",
    "unit_ruling",
    "ground_kind",
    "severity",
    "recommendations",
]

#: Every table the report page reads, by its key in the ``report_words`` island.
REPORT_WORDS: Mapping[ReportTable, Mapping[Any, object]] = MappingProxyType(
    {
        "why_open": WHY_OPEN,
        "question_history": QUESTION_HISTORY,
        "scope_state": SCOPE_STATE_WORDS,
        "unit_ruling": UNIT_RULING_WORDS,
        "ground_kind": GROUND_KIND_WORDS,
        "severity": SEVERITY_LABELS,
        "recommendations": RECOMMENDATION_HEADINGS,
    }
)

#: A job's status as the pages read it.
PageStatus = Literal[
    "running", "awaiting-answers", "completed", "failed", "stopped", "unreadable"
]

#: The label of each page status, in the order the status filter lists them.
PAGE_STATUS_LABELS: Mapping[PageStatus, str] = MappingProxyType(
    {
        "running": "In progress",
        "awaiting-answers": "Questions ready",
        "completed": "Report ready",
        "failed": "Failed",
        "stopped": "Stopped",
        "unreadable": "Cannot be read",
    }
)

#: What the pause says about a selected analysis, by the state of its
#: **Precondition**. ``{name}`` stands for the framework. A satisfied
#: precondition gets no note.
GATE_NOTES: Mapping[PreconditionResult, str | None] = MappingProxyType(
    {
        "satisfied": None,
        "refuted": (
            "The system model rules out the {name} analysis, so it will not run"
            " and none of its questions are asked."
        ),
        "undecidable": (
            "The {name} analysis runs only once the system model shows that it"
            " applies. The questions that decide this come first; its other"
            " questions follow once an answer settles it."
        ),
    }
)

#: Why the pause's list is empty, as the page says it. ``{held_back}`` stands
#: for the count of questions the limit holds back, with its noun and verb.
EMPTY_LIST_REASONS: Mapping[EmptyListReason, str] = MappingProxyType(
    {
        "budget-exhausted": (
            "The question limit is reached, so {held_back} not in the list."
        ),
        "below-floor": "No recommended question is left.",
        "skipped": "You skipped every question that is left.",
        "nothing-left": "No question is left to ask.",
    }
)

#: The keys of the ``form_words`` island, as the form scripts read them.
FormTable = Literal["page_status", "gate_notes", "empty_list"]

#: Every table the form page reads, by its key in the ``form_words`` island.
FORM_WORDS: Mapping[FormTable, Mapping[Any, object]] = MappingProxyType(
    {
        "page_status": PAGE_STATUS_LABELS,
        "gate_notes": GATE_NOTES,
        "empty_list": EMPTY_LIST_REASONS,
    }
)


def island_tables(words: Mapping[Any, Mapping[Any, object]]) -> dict[str, dict]:
    """The tables of one island in the form that ``script_json`` takes."""
    return {name: dict(table) for name, table in words.items()}
