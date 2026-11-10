"""The progress line of a run comes from one Python table.

:data:`webapp.page_words.STEP_PROGRESS` holds the words and the stage count of
each :data:`~analysis_service.graph.NodeStep`. The tests build each graph of
each selection and hold each node of it to a row. No page script names a node.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import re
import threading
import time
from typing import get_args

import pytest

from analysis_service import StubPipelineRunner, graph
from analysis_service.claims import FrameworkName
from analysis_service.frameworks import PACKAGES
from tests import test_webapp
from tests.factories import scripted_pipeline
from tests.test_page_words import _copies
from tests.test_webapp import (
    PROJECT_ROOT,
    SAME_ORIGIN,
    _island,
    page_javascript,
    posted,
)
from tests.test_webapp_questions import PausingRunner, client_for
from webapp.page_words import (
    INITIAL_PROGRESS_WORDS,
    STAGE_COUNT,
    STEP_PROGRESS,
    ProgressMode,
    progress_after,
)

tiers = test_webapp.tiers

MODES: tuple[ProgressMode, ...] = get_args(ProgressMode)

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

#: Each script of ``webapp/static``.
SCRIPTS = sorted(
    path.name for path in (PROJECT_ROOT / "webapp" / "static").glob("*.js")
)


def built_node_names(selection: tuple[FrameworkName, ...]) -> list[set[str]]:
    """The node names of each graph that ``build_pipeline`` builds for
    ``selection``: one graph for each entry and each pair of pass switches
    that the builder admits."""
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
        nodes = built.workflow.graph.nodes  # type: ignore[union-attr]
        graphs.append({node.name for node in nodes} - {graph.START.name})
    return graphs


def test_the_table_names_each_step():
    assert set(STEP_PROGRESS) == set(get_args(graph.NodeStep))


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


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("selection", SELECTIONS, ids="+".join)
def test_each_node_of_each_built_graph_has_a_progress_line(selection, mode):
    for node in set().union(*built_node_names(selection)):
        line = progress_after([node], selection, mode)
        assert line["stage"] in range(STAGE_COUNT + 1), node
        assert line["words"], node


def test_the_stand_in_runner_names_only_shared_nodes():
    """The stand-in runner builds no graph, so its names are held here."""
    assert set(StubPipelineRunner.nodes) <= set(graph.SHARED_NODES)


def test_a_name_that_is_no_node_of_the_selection_raises():
    with pytest.raises(KeyError):
        progress_after(["analyze_spoofing"], tuple(PACKAGES), "to-report")


@pytest.mark.parametrize("mode", MODES)
def test_each_mode_starts_at_no_stage_and_ends_at_the_stage_count(mode):
    assert progress_after([], tuple(PACKAGES), mode) == {
        "stage": 0,
        "words": INITIAL_PROGRESS_WORDS[mode],
    }
    assert STAGE_COUNT == max(row.stages[mode] for row in STEP_PROGRESS.values())


def test_the_line_keeps_the_highest_stage_and_the_words_of_the_last_node():
    lane = graph.Lane("stride", next(iter(PACKAGES["stride"].lanes))).node_name
    nodes = ["extract", "validate", "prepare", lane]
    assert progress_after(nodes, ("stride",), "to-report") == {
        "stage": 2,
        "words": STEP_PROGRESS["analyze"].words,
    }


@pytest.mark.parametrize("script", SCRIPTS)
def test_no_page_script_names_a_node_or_copies_the_words_of_a_step(script):
    """A script that reads a node name is a second reader of the graph."""
    code = page_javascript(script)
    steps = "|".join(sorted(get_args(graph.NodeStep), key=len, reverse=True))
    # A quoted step is a shared node name. A step before an underscore, at the
    # start of a quoted string, is the prefix of a node name of a framework.
    named = set(re.findall(rf"""["'`](?:{steps})(?:["'`]|_)""", code))
    words = [row.words for row in STEP_PROGRESS.values()]

    assert named == OTHER_MEANINGS.get(script, set())
    assert _copies([*words, *INITIAL_PROGRESS_WORDS.values()], code) == []


def test_the_form_page_carries_the_stage_count_and_the_start_words(tiers):
    with client_for(tiers, StubPipelineRunner()) as client:
        page = client.get("/").text
    assert f'<progress id="analysis-progress" value="0" max="{STAGE_COUNT}"' in page
    assert _island(page, "form_words")["progress_start"] == dict(INITIAL_PROGRESS_WORDS)
    assert 'FORM_WORDS.progress_start["to-questions"]' in page_javascript(
        "first_run.js"
    )
    assert "progress.max" in page_javascript("workspace.js")


class HeldAfterExtract(PausingRunner):
    """Holds each run after its first node until the test releases it, and
    records each node that the run sends."""

    def __init__(self) -> None:
        super().__init__(catalog=False)
        self.release = threading.Event()
        self.sent: list[str] = []

    async def run(self, job, on_node):
        async def recorded(node):
            self.sent.append(node)
            await on_node(node)

        await recorded("extract")
        while not self.release.is_set():
            await asyncio.sleep(0.01)
        return await super().run(job, recorded)


def lines_of(client, runner: HeldAfterExtract, run: str) -> tuple[str, list[dict]]:
    """The stream of ``run``, opened while the runner holds it, and the data
    of each node frame of the stream."""
    runner.release.clear()
    threading.Timer(0.3, runner.release.set).start()
    stream = client.get(f"/events/{run}").text
    frames = re.findall(r"event: node\ndata: (.*)\n", stream)
    return stream, [json.loads(data) for data in frames]


def assert_lines(frames: list[dict], nodes: list[str], mode: ProgressMode) -> None:
    """Each frame is the progress line after its node, and a later frame has
    a larger ``seq``."""
    selection = [item["name"] for item in BODY["frameworks"]]
    sent = [{key: frame[key] for key in ("stage", "words")} for frame in frames]
    assert sent == [progress_after([node], selection, mode) for node in nodes]
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

    assert_lines(frames, runner.sent, mode)
    assert "extract" not in stream


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
        runner.release.clear()
        runner.sent.clear()
        continued = client.post(
            f"/answer/{started.json()['run']}",
            json={"revision": 0},
            headers=SAME_ORIGIN,
        )
        assert continued.status_code == 200, continued.text
        child = continued.json()["run"]
        deadline = time.monotonic() + 5
        while "progress" not in (read := client.get(f"/workspace/runs/{child}").json()):
            assert time.monotonic() < deadline, read
            time.sleep(0.01)
        _, frames = lines_of(client, runner, child)

    assert_lines(frames, runner.sent, "to-report")
    assert_lines([read["progress"]], ["extract"], "to-report")
    assert frames[-1]["stage"] == STAGE_COUNT


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
