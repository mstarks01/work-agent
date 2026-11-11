"""The words a page shows for each value of a closed service set.

Each table is keyed by the ``Literal`` that names the set, and a test holds its
keys to that ``Literal``. A page reads its tables from a JSON island, so a page
script holds no copy of the words and a value the service adds cannot render as
a raw key.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType
from typing import Any, Literal, NamedTuple, TypeVar

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
from analysis_service.job_state import (
    JobState,
    PageStatus,
    RunningJobState,
    RunStart,
)
from analysis_service.jobs import (
    JobEvent,
    JobRecord,
    Resumption,
    completed_nodes,
    is_follow_up,
)
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


def progress_mode(asks: bool) -> ProgressMode:
    """The progress mode of a run that stops to ask a person, or of one that
    does not."""
    return "to-questions" if asks else "to-report"


#: What the progress line says before the first step of a run completes. The
#: page reads the same row when it starts or follows a run.
INITIAL_PROGRESS_WORDS: Mapping[RunStart, str] = MappingProxyType(
    {
        "description": (
            "Reading your description and identifying the parts of your system…"
        ),
        "answers": "Using your answers to continue the analysis…",
        "controls": "Continuing with your Controls…",
    }
)


_T = TypeVar("_T")


def _by_mode(to_questions: _T, to_report: _T) -> Mapping[ProgressMode, _T]:
    return MappingProxyType({"to-questions": to_questions, "to-report": to_report})


class StepProgress(NamedTuple):
    """What the progress line shows when one step completes, in each mode."""

    #: The work that follows the step.
    words: Mapping[ProgressMode, str]
    #: The count of stages that are complete.
    stages: Mapping[ProgressMode, int]


def _step(words: str, to_questions: int, to_report: int) -> StepProgress:
    """A step that names the same work in each mode."""
    return StepProgress(_by_mode(words, words), _by_mode(to_questions, to_report))


_CHECKING_MAP = "Checking the system map for gaps or conflicting details…"
_NOTING_MEASURES = "Noting the security measures you’ve already described…"
_IDENTIFYING_DETAILS = "Identifying details that would make the analysis more precise…"
_EXAMINING = "Examining your system against the selected frameworks…"
_CHECKING_FINDINGS = "Bringing the results together and checking the findings…"
#: The work at the end of a run that pauses. A run to the report pauses only
#: for its Controls, and asks no question (ADR 0125).
_PAUSING = _by_mode("Generating questions…", "Preparing your Controls…")

#: The progress of each step. The bar counts completed stages, not a share of
#: elapsed time. A run that the ``reject`` step ends shows its refusal, and its
#: row completes no stage. In a run that asks, ``assert`` completes the third
#: stage. A run that asks ends at ``catalog`` or at ``pause``, never at both, so
#: each of the two fills the bar. A deployment that turns the assertion pass
#: off builds no ``assert`` step, and the bar of its run that asks goes from
#: the second stage to the last.
STEP_PROGRESS: Mapping[NodeStep, StepProgress] = MappingProxyType(
    {
        "extract": _step(_CHECKING_MAP, 1, 1),
        "repair": _step(_CHECKING_MAP, 2, 2),
        "validate": _step(_NOTING_MEASURES, 2, 2),
        "revalidate": _step(_NOTING_MEASURES, 2, 2),
        "reject": _step("Continuing the analysis…", 0, 0),
        "capabilities": _step(_IDENTIFYING_DETAILS, 2, 2),
        "stated": _step(_IDENTIFYING_DETAILS, 2, 2),
        "read": _step(_IDENTIFYING_DETAILS, 2, 2),
        "assert": _step("Choosing the relevant checks for your system…", 3, 2),
        "catalog": StepProgress(_PAUSING, _by_mode(4, 0)),
        "pause": StepProgress(_PAUSING, _by_mode(4, 0)),
        "prepare": _step(_EXAMINING, 0, 2),
        "analyze": _step(_EXAMINING, 0, 0),
        "join": _step(_CHECKING_FINDINGS, 0, 3),
        "merge": _step(_CHECKING_FINDINGS, 0, 3),
        "critic": _step(_CHECKING_FINDINGS, 0, 3),
        "router": _step(_CHECKING_FINDINGS, 0, 3),
        "recritic": _step(_CHECKING_FINDINGS, 0, 3),
        "rereview": _step(_CHECKING_FINDINGS, 0, 3),
        "critic_failed": _step(_CHECKING_FINDINGS, 0, 3),
        "assemble": _step("Putting your report together…", 0, 4),
    }
)

#: The count of stages of a run, which is the length of the progress bar.
STAGE_COUNT = max(
    stage for progress in STEP_PROGRESS.values() for stage in progress.stages.values()
)


def stage_after(
    nodes: Sequence[str], frameworks: Sequence[FrameworkName], mode: ProgressMode
) -> int:
    """The count of stages that are complete after ``nodes`` completed in a
    run of ``frameworks``. Raises :class:`KeyError` on a name that is not a
    node of a graph of ``frameworks``."""
    steps = step_by_graph_node(frameworks)
    return max((STEP_PROGRESS[steps[node]].stages[mode] for node in nodes), default=0)


def words_after(
    nodes: Sequence[str],
    frameworks: Sequence[FrameworkName],
    mode: ProgressMode,
    start: RunStart,
) -> str:
    """The words of the work that follows ``nodes`` in a run of
    ``frameworks`` that does ``start`` first. Raises :class:`KeyError` on a
    name that is not a node of a graph of ``frameworks``."""
    if not nodes:
        return INITIAL_PROGRESS_WORDS[start]
    return STEP_PROGRESS[step_by_graph_node(frameworks)[nodes[-1]]].words[mode]


def lowest_stage(
    resumption: Resumption | None,
    parent: JobRecord | None,
    frameworks: Sequence[FrameworkName],
    mode: ProgressMode,
) -> int:
    """The stage below which the bar of a Resumed Job never goes: the stage
    where its paused parent stopped, counted in ``mode``
    (:func:`stage_after`). ``None`` and ``None`` stand for a job that
    resumes no job, and give 0.

    The bar of a follow-up of a finished report starts at 0. So does the bar
    of a job whose parent holds a node that no graph of ``frameworks`` holds,
    a record of an earlier build. The bar is a display value, so this reader
    answers 0 for such a name. :func:`stage_after` and :func:`words_after`,
    which map a node name to its step, raise :class:`KeyError` for it.
    """
    if parent is None or is_follow_up(resumption):
        return 0
    nodes = completed_nodes(parent.events)
    if not set(nodes) <= step_by_graph_node(frameworks).keys():
        return 0
    return stage_after(nodes, frameworks, mode)


def progress_line(
    record: JobRecord, state: JobState
) -> Callable[[Sequence[JobEvent]], dict[str, object]]:
    """The reader of the progress line of ``record`` after a list of its
    node events (:func:`stage_after` and :func:`words_after`), with the
    ``seq`` of the last event, so the page keeps the latest line it got.

    The bar of a Resumed Job never goes below its lowest stage
    (:func:`lowest_stage`). ``record`` is a job whose ``state`` has the page
    status ``running``: a job in flight, or a paused job that this process
    continues. A paused job resumes no job (:meth:`JobRecord.pauses`).
    """
    mode = progress_mode(state.asks)
    frameworks = [selection.name for selection in record.whole_frameworks()]
    if isinstance(state, RunningJobState):
        start, resumption, parent = state.start, state.resumption, state.parent
    else:
        start, resumption, parent = "description", None, None
    lowest = lowest_stage(resumption, parent, frameworks, mode)

    def line(events: Sequence[JobEvent]) -> dict[str, object]:
        nodes = completed_nodes(events)
        return {
            "stage": max(stage_after(nodes, frameworks, mode), lowest),
            "words": words_after(nodes, frameworks, mode, start),
            "seq": events[-1].seq if events else 0,
        }

    return line


#: A line of the progress panel that the page chooses: the count of complete
#: stages, and the time on one step.
ProgressPanelLine = Literal["stages-complete", "still-working"]

#: The words of each line of the progress panel that the page chooses.
#: ``{complete}`` and ``{count}`` stand for the complete stages and the stages
#: of a run, and ``{clock}`` for the minutes and seconds on the step.
PROGRESS_PANEL_WORDS: Mapping[ProgressPanelLine, str] = MappingProxyType(
    {
        "stages-complete": "{complete} of {count} stages complete",
        "still-working": (
            "Still working on this step · {clock}. Some steps take a few minutes."
        ),
    }
)

#: The keys of the ``form_words`` island, as the form scripts read them.
FormTable = Literal[
    "page_status", "gate_notes", "empty_list", "progress_start", "progress_panel"
]

#: Every table the form page reads, by its key in the ``form_words`` island.
FORM_WORDS: Mapping[FormTable, Mapping[Any, object]] = MappingProxyType(
    {
        "page_status": PAGE_STATUS_LABELS,
        "gate_notes": GATE_NOTES,
        "empty_list": EMPTY_LIST_REASONS,
        "progress_start": INITIAL_PROGRESS_WORDS,
        "progress_panel": PROGRESS_PANEL_WORDS,
    }
)


def island_tables(words: Mapping[Any, Mapping[Any, object]]) -> dict[str, dict]:
    """The tables of one island in the form that ``script_json`` takes."""
    return {name: dict(table) for name, table in words.items()}
