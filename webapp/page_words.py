"""The words a page shows for each value of a closed service set.

Each table is keyed by the ``Literal`` that names the set, and a test holds its
keys to that ``Literal``. A page reads its tables from a JSON island, so a page
script holds no copy of the words and a value the service adds cannot render as
a raw key.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any, Literal, NamedTuple

from analysis_service.answer_state import EmptyListReason
from analysis_service.claims import (
    VERDICT_LABELS,
    FrameworkName,
    GroundKind,
    ScopeState,
    SeverityLevel,
    UnitRuling,
    VerdictStatus,
)
from analysis_service.fact_answers import FactStatus
from analysis_service.frameworks import PreconditionResult
from analysis_service.graph import NodeStep, step_by_graph_node
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

#: Which progress line a run gets: ``to-questions`` where the run stops to ask
#: a person, and ``to-report`` for each other run.
ProgressMode = Literal["to-questions", "to-report"]


#: What the progress line says before the first step of a run completes. A
#: page that starts a run from a description shows the ``to-questions`` words
#: in each mode.
INITIAL_PROGRESS_WORDS: Mapping[ProgressMode, str] = MappingProxyType(
    {
        "to-questions": (
            "Reading your description and identifying the parts of your system…"
        ),
        "to-report": "Preparing the analysis…",
    }
)


class StepProgress(NamedTuple):
    """What the progress line shows when one step completes."""

    #: The work that follows the step.
    words: str
    #: The count of stages that are complete, in each mode.
    stages: Mapping[ProgressMode, int]


def _stages(to_questions: int, to_report: int) -> Mapping[ProgressMode, int]:
    return MappingProxyType({"to-questions": to_questions, "to-report": to_report})


_CHECKING_MAP = "Checking the system map for gaps or conflicting details…"
_NOTING_MEASURES = "Noting the security measures you’ve already described…"
_IDENTIFYING_DETAILS = "Identifying details that would make the analysis more precise…"
_GENERATING_QUESTIONS = "Generating questions…"
_EXAMINING = "Examining your system against the selected frameworks…"
_CHECKING_FINDINGS = "Bringing the results together and checking the findings…"

#: The progress of each step. Each quarter of the bar marks a completed stage,
#: not a share of elapsed time. A run that the ``reject`` step ends shows its
#: refusal, and its row completes no stage.
STEP_PROGRESS: Mapping[NodeStep, StepProgress] = MappingProxyType(
    {
        "extract": StepProgress(_CHECKING_MAP, _stages(1, 1)),
        "repair": StepProgress(_CHECKING_MAP, _stages(2, 2)),
        "validate": StepProgress(_NOTING_MEASURES, _stages(2, 2)),
        "revalidate": StepProgress(_NOTING_MEASURES, _stages(2, 2)),
        "reject": StepProgress("Continuing the analysis…", _stages(0, 0)),
        "capabilities": StepProgress(_IDENTIFYING_DETAILS, _stages(2, 2)),
        "stated": StepProgress(_IDENTIFYING_DETAILS, _stages(2, 2)),
        "read": StepProgress(_IDENTIFYING_DETAILS, _stages(2, 2)),
        "assert": StepProgress(
            "Choosing the relevant checks for your system…", _stages(2, 2)
        ),
        "catalog": StepProgress(_GENERATING_QUESTIONS, _stages(3, 0)),
        "pause": StepProgress(_GENERATING_QUESTIONS, _stages(4, 0)),
        "prepare": StepProgress(_EXAMINING, _stages(0, 2)),
        "analyze": StepProgress(_EXAMINING, _stages(0, 0)),
        "join": StepProgress(_CHECKING_FINDINGS, _stages(0, 3)),
        "merge": StepProgress(_CHECKING_FINDINGS, _stages(0, 3)),
        "critic": StepProgress(_CHECKING_FINDINGS, _stages(0, 3)),
        "router": StepProgress(_CHECKING_FINDINGS, _stages(0, 3)),
        "recritic": StepProgress(_CHECKING_FINDINGS, _stages(0, 3)),
        "rereview": StepProgress(_CHECKING_FINDINGS, _stages(0, 3)),
        "critic_failed": StepProgress(_CHECKING_FINDINGS, _stages(0, 3)),
        "assemble": StepProgress("Putting your report together…", _stages(0, 4)),
    }
)

#: The count of stages of a run, which is the length of the progress bar.
STAGE_COUNT = max(
    stage for progress in STEP_PROGRESS.values() for stage in progress.stages.values()
)


def progress_after(
    nodes: Sequence[str], frameworks: Sequence[FrameworkName], mode: ProgressMode
) -> dict[str, object]:
    """The progress line after ``nodes`` completed, in order, in a run of
    ``frameworks``: the count of complete stages and the words of the work
    that follows. Raises :class:`KeyError` on a name that is not a node of a
    graph of ``frameworks``."""
    steps = step_by_graph_node(frameworks)
    completed = [STEP_PROGRESS[steps[node]] for node in nodes]
    return {
        "stage": max((progress.stages[mode] for progress in completed), default=0),
        "words": completed[-1].words if completed else INITIAL_PROGRESS_WORDS[mode],
    }


#: The keys of the ``form_words`` island, as the form scripts read them.
FormTable = Literal["page_status", "gate_notes", "empty_list", "progress_start"]

#: Every table the form page reads, by its key in the ``form_words`` island.
FORM_WORDS: Mapping[FormTable, Mapping[Any, object]] = MappingProxyType(
    {
        "page_status": PAGE_STATUS_LABELS,
        "gate_notes": GATE_NOTES,
        "empty_list": EMPTY_LIST_REASONS,
        "progress_start": INITIAL_PROGRESS_WORDS,
    }
)


def island_tables(words: Mapping[Any, Mapping[Any, object]]) -> dict[str, dict]:
    """The tables of one island in the form that ``script_json`` takes."""
    return {name: dict(table) for name, table in words.items()}
