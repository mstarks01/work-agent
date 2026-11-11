"""The progress line of a run: how many **Progress Stage**\\ s are complete, of
how many, and the words of the work that follows.

Each step names the Progress Stage it completes. A run counts the stages that
the full graph of its mode holds, so the count follows the graph and the
deployment's assertion pass (ADR 0129). A run that stops to ask a person
counts the graph that pauses; each other run counts the graph that writes a
report, also where a pause for its Controls alone splits it into two jobs.
The service writes every line, and a page shows only what it gets.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType
from typing import Literal, get_args

from analysis_service.graph import (
    ENTRY_EXTRACT,
    ENTRY_HEAD_ONLY,
    Entry,
    NodeStep,
    entry_steps,
    step_by_graph_node,
)
from analysis_service.job_state import JobState, RunOrigin, RunStart, run_start_of
from analysis_service.jobs import JobEvent, JobRecord, completed_nodes, is_follow_up

#: Which progress line a run gets: ``to-questions`` where the run stops to ask
#: a person, and ``to-report`` for each other run.
ProgressMode = Literal["to-questions", "to-report"]

#: A part of a run that the bar counts, in the order a run completes them.
ProgressStage = Literal["map", "details", "checks", "questions", "findings", "report"]
STAGE_ORDER: tuple[ProgressStage, ...] = get_args(ProgressStage)

#: The entry of the full graph of each mode, whose stages a run counts.
MODE_ENTRIES: Mapping[ProgressMode, Entry] = MappingProxyType(
    {"to-questions": ENTRY_HEAD_ONLY, "to-report": ENTRY_EXTRACT}
)

#: The Progress Stage that each step completes, or ``None`` for a step that
#: completes none: a refused description, the preparation of the lanes, and
#: each lane, whose end ``join`` marks.
STEP_STAGES: Mapping[NodeStep, ProgressStage | None] = MappingProxyType(
    {
        "extract": "map",
        "repair": "details",
        "validate": "details",
        "revalidate": "details",
        "capabilities": "details",
        "stated": "details",
        "read": "details",
        "assert": "checks",
        "catalog": "questions",
        "pause": "questions",
        "join": "findings",
        "merge": "findings",
        "critic": "findings",
        "router": "findings",
        "recritic": "findings",
        "rereview": "findings",
        "critic_failed": "findings",
        "assemble": "report",
        "reject": None,
        "prepare": None,
        "analyze": None,
    }
)

#: What the progress line says before the first step of a run completes.
INITIAL_PROGRESS_WORDS: Mapping[RunStart, str] = MappingProxyType(
    {
        "description": (
            "Reading your description and identifying the parts of your system…"
        ),
        "answers": "Using your answers to continue the analysis…",
        "controls": "Continuing with your Controls…",
    }
)


def _same(words: str) -> Mapping[ProgressMode, str]:
    """The words of a step that names the same work in each mode."""
    return MappingProxyType({"to-questions": words, "to-report": words})


_CHECKING_MAP = _same("Checking the system map for gaps or conflicting details…")
_NOTING_MEASURES = _same("Noting the security measures you’ve already described…")
_IDENTIFYING_DETAILS = _same(
    "Identifying details that would make the analysis more precise…"
)
_EXAMINING = _same("Examining your system against the selected frameworks…")
_CHECKING_FINDINGS = _same("Bringing the results together and checking the findings…")
#: The work at the end of a run that pauses. A run to the report pauses only
#: for its Controls, and asks no question (ADR 0125).
_PAUSING: Mapping[ProgressMode, str] = MappingProxyType(
    {"to-questions": "Generating questions…", "to-report": "Preparing your Controls…"}
)

#: The words of the work that follows each step, in each mode.
STEP_WORDS: Mapping[NodeStep, Mapping[ProgressMode, str]] = MappingProxyType(
    {
        "extract": _CHECKING_MAP,
        "repair": _CHECKING_MAP,
        "validate": _NOTING_MEASURES,
        "revalidate": _NOTING_MEASURES,
        "reject": _same("Continuing the analysis…"),
        "capabilities": _IDENTIFYING_DETAILS,
        "stated": _IDENTIFYING_DETAILS,
        "read": _IDENTIFYING_DETAILS,
        "assert": _same("Choosing the relevant checks for your system…"),
        "catalog": _PAUSING,
        "pause": _PAUSING,
        "prepare": _EXAMINING,
        "analyze": _EXAMINING,
        "join": _CHECKING_FINDINGS,
        "merge": _CHECKING_FINDINGS,
        "critic": _CHECKING_FINDINGS,
        "router": _CHECKING_FINDINGS,
        "recritic": _CHECKING_FINDINGS,
        "rereview": _CHECKING_FINDINGS,
        "critic_failed": _CHECKING_FINDINGS,
        "assemble": _same("Putting your report together…"),
    }
)


def progress_mode(asks: bool) -> ProgressMode:
    """The progress mode of a run that stops to ask a person, or of one that
    does not."""
    return "to-questions" if asks else "to-report"


def run_stages(mode: ProgressMode, assertion_pass: bool) -> tuple[ProgressStage, ...]:
    """The Progress Stages of a run in ``mode``, in order: each stage that a
    step of the full graph of the mode completes, whether the capability
    reader runs or not."""
    steps = frozenset().union(
        *(
            entry_steps(
                MODE_ENTRIES[mode],
                assertions=assertion_pass,
                capability_reader=reader,
            )
            for reader in (False, True)
        )
    )
    held = {STEP_STAGES[step] for step in steps}
    return tuple(stage for stage in STAGE_ORDER if stage in held)


def progress_line(
    record: JobRecord, state: JobState, origin: RunOrigin
) -> Callable[[Sequence[JobEvent]], dict[str, object]]:
    """The reader of the progress line of ``record`` after a list of its node
    events: the count of complete stages, the count of stages of the run, the
    words of the work that follows, and the ``seq`` of the last event, so the
    page keeps the latest line it got.

    ``record`` is a job whose ``state`` has the page status ``running``: a
    job in flight, or a paused job that this process continues, whose line
    says what the run that continues it does first. ``origin`` is its
    :func:`~analysis_service.job_state.run_origin`. Raises
    :class:`ValueError` for each other state, and :class:`KeyError` for an
    event whose node no graph of the job's selection holds.

    The bar of a Resumed Job starts at the stage where its paused parent
    stopped, and never goes below it. The bar of a follow-up of a finished
    report starts at 0. So does the bar of a job whose parent holds a node
    that no graph of the selection holds, a record of an earlier build.
    """
    if state.page_status != "running":
        raise ValueError(f"job {record.id} is not running, so it has no line")
    mode = progress_mode(state.asks)
    stages = run_stages(mode, record.assertion_pass)
    steps = step_by_graph_node(
        [selection.name for selection in record.whole_frameworks()]
    )

    def complete(nodes: Sequence[str]) -> int:
        reached = (STEP_STAGES[steps[node]] for node in nodes)
        return max(
            (stages.index(stage) + 1 for stage in reached if stage in stages),
            default=0,
        )

    lowest = 0
    parent = origin.parent
    if parent is not None and not is_follow_up(origin.resumption):
        parent_nodes = completed_nodes(parent.events)
        if set(parent_nodes) <= steps.keys():
            lowest = complete(parent_nodes)
    start = run_start_of(record) if state.continuing else origin.start

    def line(events: Sequence[JobEvent]) -> dict[str, object]:
        nodes = completed_nodes(events)
        if nodes and not state.continuing:
            words = STEP_WORDS[steps[nodes[-1]]][mode]
        else:
            words = INITIAL_PROGRESS_WORDS[start]
        return {
            "stage": max(complete(nodes), lowest),
            "count": len(stages),
            "words": words,
            "seq": events[-1].seq if events else 0,
        }

    return line
