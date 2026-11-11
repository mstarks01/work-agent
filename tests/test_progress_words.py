"""The progress line of a run comes from one module.

:mod:`webapp.progress` names the Progress Stage and the words of each
:data:`~analysis_service.graph.NodeStep`, and counts the stages of the full
graph of a run's mode. The tests build each graph of each selection, hold its
steps to :func:`~analysis_service.graph.entry_steps`, and hold each node of it
to a row. No page script names a node or chooses the words of a line.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import re
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from typing import NamedTuple, get_args

import pytest
from fastapi.testclient import TestClient

from analysis_service import StubPipelineRunner, graph
from analysis_service.claims import FrameworkName
from analysis_service.frameworks import PACKAGES
from analysis_service.job_state import (
    InFlightJobState,
    RunOrigin,
    RunStart,
    job_state,
    run_origin,
    run_start_of,
)
from analysis_service.jobs import (
    Checkpoint,
    InMemoryJobStore,
    JobEvent,
    JobRecord,
    Resumption,
)
from analysis_service.pipeline import entry_of
from analysis_service.sources import Source
from tests import test_webapp
from tests.factories import (
    DESCRIPTION_TEXT,
    admit,
    sample_report,
    sample_selection,
    scripted_pipeline,
)
from tests.test_page_words import _copies
from tests.test_webapp import (
    LOOPBACK,
    SAME_ORIGIN,
    STATIC_SCRIPTS,
    _island,
    page_javascript,
    posted,
)
from tests.test_webapp_questions import HeldAfterExtract, app_for, client_for
from webapp.main import Workspace
from webapp.progress import (
    INITIAL_PROGRESS_WORDS,
    STEP_STAGES,
    STEP_WORDS,
    ProgressMode,
    ProgressStage,
    progress_line,
    run_stages,
)

tiers = test_webapp.tiers

MODES: tuple[ProgressMode, ...] = get_args(ProgressMode)
STARTS: tuple[RunStart, ...] = get_args(RunStart)

#: Each selection of one package, and the selection of every package.
SELECTIONS: list[tuple[FrameworkName, ...]] = [
    *((name,) for name in PACKAGES),
    tuple(PACKAGES),
]

#: A word of a script that equals a step and names a different thing: the ID
#: of the form, the state of a flag, and the basis of a question.
OTHER_MEANINGS = {
    "first_run.js": {'"analyze"', '"stated"'},
    "report_view.js": {'"critic"'},
}


class BuiltGraph(NamedTuple):
    """One graph that ``build_pipeline`` builds."""

    entry: graph.Entry
    #: Whether the graph holds the assertion pass.
    assertions: bool
    #: Whether the graph holds the capability reader.
    capability_reader: bool
    #: The name of each node but the start.
    nodes: set[str]
    #: The name of each node that no edge leaves.
    ends: set[str]
    #: Each edge, as the names of its two nodes.
    edges: set[tuple[str, str]]


def built_graphs(selection: tuple[FrameworkName, ...]) -> list[BuiltGraph]:
    """Each graph that ``build_pipeline`` builds for ``selection``: one graph
    for each entry and each pair of pass switches that the builder admits."""
    graphs = []
    for entry, assertions, capability_reader in itertools.product(
        get_args(graph.Entry), (False, True), (False, True)
    ):
        try:
            built, _ = scripted_pipeline(
                {},
                frameworks=selection,
                entry=entry,
                assertions=assertions,
                capability_reader=capability_reader,
            )
        except ValueError:
            # The builder refuses a pass that the entry cannot hold.
            continue
        workflow = built.workflow.graph
        assert workflow is not None
        nodes = {node.name for node in workflow.nodes} - {graph.START.name}
        edges = {(edge.from_node.name, edge.to_node.name) for edge in workflow.edges}
        left = {start for start, _ in edges}
        graphs.append(
            BuiltGraph(entry, assertions, capability_reader, nodes, nodes - left, edges)
        )
    return graphs


def built_node_names(selection: tuple[FrameworkName, ...]) -> list[set[str]]:
    """The node names of each graph of ``selection``."""
    return [built.nodes for built in built_graphs(selection)]


def line_after(
    nodes: Sequence[str],
    *,
    mode: ProgressMode = "to-report",
    assertion_pass: bool = True,
    start: RunStart = "description",
    selection: Sequence[FrameworkName] = tuple(PACKAGES),
) -> dict:
    """The progress line of a job in flight after ``nodes``, with no parent."""
    record = JobRecord.model_construct(
        id="job-1",
        frameworks=sample_selection(selection),
        assertion_pass=assertion_pass,
    )
    state = InFlightJobState(asks=mode == "to-questions")
    events = [
        JobEvent.model_construct(seq=seq, node=node)
        for seq, node in enumerate(nodes, 1)
    ]
    line = progress_line(record, state, RunOrigin(start, None, None))(events)
    return {key: line[key] for key in ("stage", "count", "words")}


def sample_names() -> list[FrameworkName]:
    return [each.name for each in sample_selection()]


def test_the_tables_name_each_step():
    assert set(STEP_STAGES) == set(get_args(graph.NodeStep))
    assert set(STEP_WORDS) == set(get_args(graph.NodeStep))
    assert set(INITIAL_PROGRESS_WORDS) == set(STARTS)


@pytest.mark.parametrize("selection", SELECTIONS, ids="+".join)
def test_each_node_of_each_built_graph_has_a_step(selection):
    """A node that the service adds or renames has no step, and fails here."""
    steps = graph.step_by_graph_node(selection)
    graphs = built_node_names(selection)

    assert len(graphs) >= len(get_args(graph.Entry))
    for nodes in graphs:
        assert nodes - set(steps) == set()
    assert set(steps.values()) == set(get_args(graph.NodeStep))


def test_each_name_with_a_step_is_a_node_of_a_built_graph():
    """A shared node is in the graph of a selection only when a package of the
    selection needs it, so the selections are read together."""
    named = set().union(*(graph.step_by_graph_node(each) for each in SELECTIONS))
    built = set().union(
        *(nodes for each in SELECTIONS for nodes in built_node_names(each))
    )

    assert named == built


@pytest.mark.parametrize("selection", SELECTIONS, ids="+".join)
def test_the_steps_of_an_entry_are_the_steps_of_each_graph_it_builds(selection):
    """``entry_steps`` answers without a build, so each build checks it."""
    steps = graph.step_by_graph_node(selection)
    for built in built_graphs(selection):
        assert {steps[node] for node in built.nodes} == graph.entry_steps(
            built.entry,
            assertions=built.assertions,
            capability_reader=built.capability_reader,
        ), built


#: The Progress Stages of a run, by its mode and whether it holds the
#: assertion pass.
STAGES: dict[tuple[ProgressMode, bool], tuple[ProgressStage, ...]] = {
    ("to-questions", True): ("map", "details", "checks", "questions"),
    ("to-questions", False): ("map", "details", "questions"),
    ("to-report", True): ("map", "details", "checks", "findings", "report"),
    ("to-report", False): ("map", "details", "findings", "report"),
}


@pytest.mark.parametrize(("mode", "assertion_pass"), STAGES)
def test_a_run_counts_the_stages_of_the_full_graph_of_its_mode(mode, assertion_pass):
    stages = STAGES[mode, assertion_pass]
    assert run_stages(mode, assertion_pass) == stages
    line = line_after([], mode=mode, assertion_pass=assertion_pass)
    assert line["count"] == len(stages)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("selection", SELECTIONS, ids="+".join)
def test_each_node_of_each_built_graph_has_a_progress_line(selection, mode):
    for node in set().union(*built_node_names(selection)):
        line = line_after([node], mode=mode, selection=selection)
        assert line["stage"] in range(line["count"] + 1), node
        assert line["words"], node


def test_the_stand_in_runner_names_only_shared_nodes():
    """The stand-in runner builds no graph, so its names are held here."""
    assert set(StubPipelineRunner.nodes) <= set(graph.SHARED_NODES)


def test_a_name_that_is_no_node_of_the_selection_raises():
    with pytest.raises(KeyError):
        line_after(["analyze_spoofing"])


@pytest.mark.parametrize("start", STARTS)
@pytest.mark.parametrize("mode", MODES)
def test_a_run_starts_at_no_stage_with_the_words_of_what_it_does_first(mode, start):
    """The start words follow what the run does first, in each mode."""
    line = line_after([], mode=mode, start=start)
    assert (line["stage"], line["words"]) == (0, INITIAL_PROGRESS_WORDS[start])


#: The steps that end a run with no report and no pause: a refused
#: description, and a critic that failed.
FAILED_ENDS = {"reject", "critic_failed"}

#: The entries of a job's run, with the mode of the run that each one builds.
JOB_ENTRIES: dict[graph.Entry, ProgressMode] = {
    graph.ENTRY_HEAD_ONLY: "to-questions",
    graph.ENTRY_EXTRACT: "to-report",
    graph.ENTRY_RESUME: "to-report",
}


@pytest.mark.parametrize("selection", SELECTIONS, ids="+".join)
def test_the_last_step_of_each_graph_of_a_job_fills_the_bar(selection):
    """A graph that pauses ends at the catalog or at the pause, never at both,
    and a graph that writes a report ends at ``assemble``."""
    steps = graph.step_by_graph_node(selection)
    for built in built_graphs(selection):
        pauses = built.nodes & {graph.CATALOG_NODE, graph.PAUSE_NODE}
        assert bool(pauses) == (built.entry == graph.ENTRY_HEAD_ONLY)
        if built.entry not in JOB_ENTRIES:
            continue
        ends = {node for node in built.ends if steps[node] not in FAILED_ENDS}
        assert len(ends) == 1, built
        line = line_after(
            sorted(ends),
            mode=JOB_ENTRIES[built.entry],
            assertion_pass=built.assertions,
            selection=selection,
        )
        assert line["stage"] == line["count"], built


def stages_on_each_path(
    built: BuiltGraph, selection: tuple[FrameworkName, ...]
) -> set[tuple[int, ...]]:
    """The stages that the bar of a run that asks shows on each path of
    ``built`` from the start to an end that is not a failed end, in order.
    ``built`` is a graph with no cycle, as each graph of a run that asks is."""
    steps = graph.step_by_graph_node(selection)
    after: defaultdict[str, list[str]] = defaultdict(list)
    for start, end in built.edges:
        after[start].append(end)

    def paths(node: str, seen: frozenset[str]) -> Iterator[list[str]]:
        assert node not in seen, f"the graph has a cycle through {node}"
        if not after[node]:
            yield []
        for following in after[node]:
            for rest in paths(following, seen | {node}):
                yield [following, *rest]

    shown = set()
    for path in paths(graph.START.name, frozenset()):
        if steps[path[-1]] in FAILED_ENDS:
            continue
        stages = [
            line_after(
                [node],
                mode="to-questions",
                assertion_pass=built.assertions,
                selection=selection,
            )["stage"]
            for node in path
        ]
        shown.add(tuple(dict.fromkeys(itertools.accumulate(stages, max))))
    return shown


@pytest.mark.parametrize("selection", SELECTIONS, ids="+".join)
def test_a_run_that_asks_shows_each_stage_of_its_graph(selection):
    """The ``assert`` step completes the ``checks`` stage. A deployment that
    turns ``ANALYSIS_ASSERTIONS`` off builds no ``assert`` step, so its run
    that asks has one stage less, and the bar shows each stage it has."""
    asking = [
        built
        for built in built_graphs(selection)
        if built.entry == graph.ENTRY_HEAD_ONLY
    ]

    assert {built.assertions for built in asking} == {False, True}
    for built in asking:
        shown = (1, 2, 3, 4) if built.assertions else (1, 2, 3)
        assert stages_on_each_path(built, selection) == {shown}, built.nodes


def test_a_run_to_the_report_reaches_the_pause_only_for_its_controls():
    """The ``to-report`` words of the pause are the words of a run that pauses
    for its Controls alone, so no other run to the report builds a pause."""
    resumption = Resumption.model_construct()
    reaches = {
        (asks, continues, resumed): (
            entry_of(record) == graph.ENTRY_HEAD_ONLY and not record.pauses_to_ask()
        )
        for asks, continues, resumed in itertools.product((False, True), repeat=3)
        for record in [
            JobRecord.model_construct(
                ask_questions=asks,
                continues_at_pause=continues,
                resumption=resumption if resumed else None,
            )
        ]
    }

    assert {key for key, reached in reaches.items() if reached} == {
        (False, True, False)
    }


@pytest.mark.parametrize(
    ("asks", "continues", "resumed"), itertools.product((False, True), repeat=3)
)
def test_a_run_starts_from_a_resumption_where_its_graph_has_the_resume_entry(
    asks, continues, resumed
):
    """``entry_of`` and ``run_start_of`` read one fact of a job."""
    record = JobRecord.model_construct(
        ask_questions=asks,
        continues_at_pause=continues,
        resumption=Resumption.model_construct() if resumed else None,
    )
    parent = JobRecord.model_construct(continues_at_pause=continues)
    start = run_start_of(parent if resumed else None)

    assert (start != "description") == (entry_of(record) == graph.ENTRY_RESUME)


@pytest.mark.parametrize("node", [graph.CATALOG_NODE, graph.PAUSE_NODE])
def test_a_run_that_pauses_for_its_controls_alone_names_the_controls(node):
    lines = {mode: line_after([node], mode=mode)["words"] for mode in MODES}

    assert lines == {
        "to-questions": "Generating questions…",
        "to-report": "Preparing your Controls…",
    }


def test_a_pause_completes_no_stage_of_a_run_to_the_report():
    """A pause for the Controls alone is no stage that the owner sees."""
    nodes = [graph.EXTRACT_NODE, graph.VALIDATE_NODE, graph.ASSERT_NODE, "pause"]
    assert line_after(nodes)["stage"] == 3


def test_the_line_keeps_the_highest_stage_and_the_words_of_the_last_node():
    lane = graph.Lane("stride", next(iter(PACKAGES["stride"].lanes))).node_name
    nodes = ["extract", "validate", "prepare", lane]
    assert line_after(nodes, selection=("stride",)) == {
        "stage": 2,
        "count": 5,
        "words": STEP_WORDS["analyze"]["to-report"],
    }


@pytest.mark.parametrize("script", STATIC_SCRIPTS)
def test_no_page_script_names_a_node_or_copies_the_words_of_a_line(script):
    """A script that reads a node name is a second reader of the graph, and
    a script that holds the words of a line is a second writer of the line."""
    code = page_javascript(script)
    steps = "|".join(sorted(get_args(graph.NodeStep), key=len, reverse=True))
    # A quoted step is a shared node name. A step before an underscore, at the
    # start of a quoted string, is the prefix of a node name of a framework.
    named = set(re.findall(rf"""["'`](?:{steps})(?:["'`]|_)""", code))
    words = [text for row in STEP_WORDS.values() for text in row.values()]

    assert named == OTHER_MEANINGS.get(script, set())
    assert _copies([*words, *INITIAL_PROGRESS_WORDS.values()], code) == []
    assert "progress_start" not in code


def test_the_page_takes_the_count_from_each_line_and_holds_no_start_words(tiers):
    with client_for(tiers, StubPipelineRunner()) as client:
        page = client.get("/").text
    assert '<progress id="analysis-progress" aria-label' in page
    assert "progress_start" not in _island(page, "form_words")
    assert "progress.max = sent.count" in page_javascript("workspace.js")


def lines_of(client, runner: HeldAfterExtract, run: str) -> tuple[str, list[dict]]:
    """The stream of ``run``, opened while the runner holds it, and the data
    of each node frame of the stream."""
    runner.release.clear()
    threading.Timer(0.3, runner.release.set).start()
    stream = client.get(f"/events/{run}").text
    frames = re.findall(r"event: node\ndata: (.*)\n", stream)
    return stream, [json.loads(data) for data in frames]


def assert_lines(
    frames: list[dict],
    nodes: list[str],
    mode: ProgressMode,
    start: RunStart,
    lowest: int = 0,
) -> None:
    """The first frame is the line before any node, each later frame is the
    progress line after its node alone, and a later frame has a larger
    ``seq``. No stage falls below ``lowest``, the stage where the parent of a
    Resumed Job stopped."""
    selection = [item["name"] for item in BODY["frameworks"]]
    sent = [
        {key: frame[key] for key in ("stage", "count", "words")} for frame in frames
    ]
    lines = [
        line_after(done, mode=mode, start=start, selection=selection)
        for done in [[], *([node] for node in nodes)]
    ]
    assert sent == [line | {"stage": max(line["stage"], lowest)} for line in lines]
    seqs = [frame["seq"] for frame in frames]
    assert seqs == sorted(set(seqs))


BODY = posted("A web app talks to a database.")


@pytest.mark.parametrize("mode", MODES)
def test_the_stream_of_a_run_in_progress_carries_each_progress_line_and_no_node_name(
    tiers, mode
):
    runner = HeldAfterExtract()
    with client_for(tiers, runner) as client:
        started = client.post(
            "/analyze",
            json=BODY | {"questions": mode == "to-questions"},
            headers=SAME_ORIGIN,
        )
        assert started.status_code == 200, started.text
        stream, frames = lines_of(client, runner, started.json()["run"])

    assert_lines(frames, runner.sent, mode, "description")
    assert "extract" not in stream


class HeldBeforeExtract(HeldAfterExtract):
    """Holds each run before its first node until the test releases it."""

    async def run(self, job, on_node):
        while not self.release.is_set():
            await asyncio.sleep(0.01)
        return await super().run(job, on_node)


def progress_at_start(client, run: str) -> dict:
    """The progress line of ``run`` in the status answer."""
    return client.get(f"/workspace/runs/{run}").json()["progress"]


@pytest.mark.parametrize(
    ("mode", "assertion_pass"),
    [("to-questions", True), ("to-report", True), ("to-report", False)],
)
def test_a_reload_at_the_start_shows_the_line_of_the_service(
    tiers, mode, assertion_pass
):
    """The status answer at the start carries the start words and the count
    of the run, which follows the deployment's assertion pass."""
    runner = HeldBeforeExtract()
    with client_for(tiers, runner, catalog=assertion_pass) as client:
        started = client.post(
            "/analyze",
            json=BODY | {"questions": mode == "to-questions"},
            headers=SAME_ORIGIN,
        )
        line = progress_at_start(client, started.json()["run"])
        runner.release.set()

    assert line == {
        "stage": 0,
        "count": len(STAGES[mode, assertion_pass]),
        "words": INITIAL_PROGRESS_WORDS["description"],
        "seq": 0,
    }


def resume(
    client, runner: HeldAfterExtract, parent: str, ready: Callable[[dict], bool]
) -> tuple[str, dict]:
    """The Resumed Job that continues ``parent`` with no answers, and the
    first status answer of it that ``ready`` accepts."""
    runner.release.clear()
    runner.sent.clear()
    continued = client.post(
        f"/answer/{parent}", json={"revision": 0}, headers=SAME_ORIGIN
    )
    assert continued.status_code == 200, continued.text
    child = continued.json()["run"]
    deadline = time.monotonic() + 5
    while not ready(read := client.get(f"/workspace/runs/{child}").json()):
        assert time.monotonic() < deadline, read
        time.sleep(0.01)
    return child, read


def test_a_resumed_job_of_a_run_that_asked_gets_the_lines_of_a_run_to_the_report(
    tiers,
):
    """The Resumed Job never stops to ask, so its last node fills the bar."""
    runner = HeldAfterExtract()
    with client_for(tiers, runner) as client:
        started = client.post(
            "/analyze", json=BODY | {"questions": True}, headers=SAME_ORIGIN
        )
        lines_of(client, runner, started.json()["run"])
        selection = [item["name"] for item in BODY["frameworks"]]
        lowest = line_after(runner.sent, selection=selection)["stage"]
        child, read = resume(
            client,
            runner,
            started.json()["run"],
            lambda read: read["progress"]["seq"] > 0,
        )
        _, frames = lines_of(client, runner, child)

    assert_lines(frames, runner.sent, "to-report", "answers", lowest)
    assert read["progress"]["stage"] == max(lowest, 1)
    assert read["pauses"] is False
    assert frames[-1]["stage"] == frames[-1]["count"]


def test_a_parent_with_a_node_of_no_graph_of_this_build_gives_a_bar_at_0(tiers):
    """A parent of an earlier build can hold a node name that no graph of
    this build holds. The bar of its Resumed Job then starts at 0, and the
    status answer and the stream both answer."""
    store = InMemoryJobStore()
    runner = HeldBeforeExtract()
    app = app_for(tiers, runner, workspace=Workspace(store=store))
    with TestClient(app, base_url=LOOPBACK) as client:
        started = client.post(
            "/analyze", json=BODY | {"questions": True}, headers=SAME_ORIGIN
        )
        parent = started.json()["run"]
        lines_of(client, runner, parent)
        store._records[parent].record_node("a_retired_node")
        child, read = resume(client, runner, parent, lambda read: "progress" in read)
        _, frames = lines_of(client, runner, child)

    assert read["progress"] == {
        "stage": 0,
        "count": len(STAGES["to-report", True]),
        "words": INITIAL_PROGRESS_WORDS["answers"],
        "seq": 0,
    }
    assert_lines(frames, runner.sent, "to-report", "answers")


def test_the_stream_of_a_run_that_ended_carries_no_node_frame(tiers):
    """A run that ended before the request can be a record of an earlier
    build, and no graph of this build names its nodes."""
    with client_for(tiers, StubPipelineRunner()) as client:
        started = client.post(
            "/analyze",
            json=posted("A web app talks to a database.") | {"questions": False},
            headers=SAME_ORIGIN,
        )
        run = started.json()["run"]
        client.get(f"/events/{run}")
        ended = client.get(f"/events/{run}").text

    assert "event: node" not in ended
    assert "event: done" in ended


#: The nodes of a parent, by how its Resumed Job starts and the mode of the
#: parent, with the stage where the bar of the Resumed Job starts: the stage
#: where a paused parent stopped, counted in the stages of a run to the
#: report. The parent in the ``to-report`` mode that asked for answers is a
#: finished report, and the bar of its follow-up starts at 0.
PARENT_RUNS: dict[tuple[RunStart, ProgressMode], tuple[list[str], int]] = {
    ("answers", "to-questions"): (
        [graph.EXTRACT_NODE, graph.VALIDATE_NODE, graph.ASSERT_NODE, "catalog"],
        3,
    ),
    ("answers", "to-report"): (
        [graph.EXTRACT_NODE, graph.PREPARE_NODE, graph.ASSEMBLE_NODE],
        0,
    ),
    ("controls", "to-report"): (
        [graph.EXTRACT_NODE, graph.VALIDATE_NODE, graph.ASSERT_NODE, "pause"],
        3,
    ),
}


def resumed_job(
    start: RunStart, mode: ProgressMode
) -> tuple[InMemoryJobStore, JobRecord]:
    """A Resumed Job in flight, with its parent in the store. A parent in the
    ``to-report`` mode that asked for answers is a finished report, and its
    Resumed Job is the follow-up."""
    store = InMemoryJobStore()
    nodes, _ = PARENT_RUNS[start, mode]
    parent = JobRecord.create(
        assertion_pass=True,
        owner_id="alice",
        sources=[Source.description(DESCRIPTION_TEXT)],
        frameworks=sample_selection(),
        ask_questions=mode == "to-questions",
        continues_at_pause=start == "controls",
    )
    parent.transition("running")
    for node in nodes:
        parent.record_node(node)
    report = sample_report([])
    if graph.ASSEMBLE_NODE in nodes:
        parent.report = report
        parent.transition("completed")
    else:
        parent.transition("awaiting-answers")
    asyncio.run(admit(store, parent))
    child = JobRecord.create(
        assertion_pass=True,
        owner_id="alice",
        sources=[Source.description(DESCRIPTION_TEXT)],
        frameworks=sample_selection(),
        resumption=Resumption(
            parent_id=parent.id,
            checkpoint=Checkpoint(system_model=report.system_model, assertions=None),
            follow_up=graph.ASSEMBLE_NODE in nodes,
        ),
        parent_revision=parent.revision,
    )
    child.transition("running")
    asyncio.run(admit(store, child))
    return store, child


def _line(start: RunStart, mode: ProgressMode, nodes: list[str]) -> dict:
    store, child = resumed_job(start, mode)
    events = [
        JobEvent.model_construct(seq=seq, node=node)
        for seq, node in enumerate(nodes, 1)
    ]

    async def read():
        state = await job_state(store, child, runs_here=False)
        return progress_line(child, state, await run_origin(store, child))(events)

    return asyncio.run(read())


@pytest.mark.parametrize(("start", "mode"), PARENT_RUNS)
def test_a_resumed_job_starts_at_the_stage_where_its_parent_stopped(start, mode):
    _, lowest = PARENT_RUNS[start, mode]
    assert _line(start, mode, []) == {
        "stage": lowest,
        "count": len(STAGES["to-report", True]),
        "words": INITIAL_PROGRESS_WORDS[start],
        "seq": 0,
    }


@pytest.mark.parametrize(("start", "mode"), PARENT_RUNS)
def test_the_bar_of_a_resumed_job_never_goes_below_its_lowest_stage(start, mode):
    _, lowest = PARENT_RUNS[start, mode]
    for nodes in ([graph.EXTRACT_NODE], [graph.EXTRACT_NODE, graph.PREPARE_NODE]):
        expected = line_after(nodes, selection=sample_names())["stage"]
        assert _line(start, mode, nodes)["stage"] == max(expected, lowest)


def paused_job(store: InMemoryJobStore, *, asks: bool) -> JobRecord:
    """A job of ``store`` that ran to its pause: to ask a person, or for its
    Controls alone."""
    paused = JobRecord.create(
        assertion_pass=True,
        owner_id="alice",
        sources=[Source.description(DESCRIPTION_TEXT)],
        frameworks=sample_selection(),
        ask_questions=asks,
        continues_at_pause=not asks,
    )
    paused.transition("running")
    for node in (graph.EXTRACT_NODE, graph.VALIDATE_NODE, graph.PAUSE_NODE):
        paused.record_node(node)
    paused.transition("awaiting-answers")
    asyncio.run(admit(store, paused))
    return paused


def test_a_paused_job_this_process_continues_shows_the_line_of_its_continue():
    """The line of a job that this process continues at its pause says what
    the run that continues it does first, at the stage where the job is."""
    store = InMemoryJobStore()
    paused = paused_job(store, asks=False)

    async def read():
        envelope = await store.owned(paused.id, "alice")
        state = await job_state(store, envelope, runs_here=True)
        return progress_line(envelope, state, await run_origin(store, envelope))

    assert asyncio.run(read())(paused.events) == {
        "stage": 2,
        "count": len(STAGES["to-report", True]),
        "words": INITIAL_PROGRESS_WORDS["controls"],
        "seq": paused.events[-1].seq,
    }


def test_a_job_that_is_not_running_has_no_progress_line():
    store = InMemoryJobStore()
    paused = paused_job(store, asks=True)

    async def read():
        envelope = await store.owned(paused.id, "alice")
        state = await job_state(store, envelope, runs_here=False)
        return progress_line(envelope, state, await run_origin(store, envelope))

    with pytest.raises(ValueError, match="not running"):
        asyncio.run(read())
