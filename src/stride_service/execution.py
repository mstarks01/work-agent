"""Driving a built graph, and recording what each node execution presented.

A **Graph Run** is one drive of a :class:`~stride_service.graph.Pipeline` to
completion: the final session state, plus one :class:`~stride_service.report.NodeRun`
per node execution. It is deliberately *not* a report — job identity, the input
digest and certification belong to whoever asked for the run, not to the graph.

The graph has exactly two drivers, and this module exists because they were
stamping differently. :class:`~stride_service.pipeline.AdkPipelineRunner` owned
the served-route join, the per-execution fingerprint and the predecessor-relative
duration as private methods, so the eval harness — driving the same graph over a
corpus — could not reach them and stamped a single placeholder ``NodeRun``
instead. Every eval report therefore carried no fingerprints at all, which made
``report_fingerprints`` return an empty mapping, which made
:func:`~stride_service.certification.certify` report ``certified=True`` over
nothing. A sweep printed "all node fingerprints blessed" having blessed nothing.
Stamping lives here so that cannot recur: one implementation, both drivers.

What the graph cannot know stays with the caller. What only the driver can
observe — which node an ADK event is the output for, what build answered it,
when its last predecessor finished — is here.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from google.adk import Runner
from google.adk.apps import App
from google.adk.sessions import BaseSessionService, InMemorySessionService
from google.genai import types

from stride_service.graph import Pipeline
from stride_service.report import NodeRun
from stride_service.sampling import sampling_fingerprint
from stride_service.vendors import join_served

# Awaited with each node name as it lands. Structurally the same callable as
# ``jobs.NodeCallback``, spelled out rather than imported: the job lifecycle is
# one caller of the graph, and this module knows nothing about jobs.
OnNode = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class _NodeFinish:
    """When one graph node produced its output, and what build answered it.

    ``served_model`` is the build the provider says actually ran, read off the
    event rather than assumed from the configured string (#7 decision 2). It is
    ``None`` when the event carries none — an offline stand-in, or a provider
    that did not report one — and a node with no served build gets no
    fingerprint rather than one attesting to a model nobody confirmed.
    """

    node: str
    at: float
    served_model: str | None


@dataclass(frozen=True)
class GraphRun:
    """One drive of the graph: what the session ended holding, and what ran.

    ``node_runs`` is in the order the nodes finished, one entry per node
    *execution* — so the critic on a revise path appears twice, which is what
    makes a build that moves mid-run visible.
    """

    final_state: dict[str, Any]
    node_runs: list[NodeRun]


class GraphExecutor:
    """Drives one built :class:`Pipeline`, stamping every node execution.

    Built once per pipeline and reused across runs: the predecessor map and the
    node-name set are read off the built graph at construction, so a driver that
    runs many jobs against one pipeline does not re-walk its edges each time.
    Each :meth:`run` is independent and holds no cross-run state.
    """

    def __init__(
        self,
        pipeline: Pipeline,
        *,
        app_name: str,
        session_service: BaseSessionService | None = None,
    ) -> None:
        self._pipeline = pipeline
        self._app_name = app_name
        self._session_service = session_service or InMemorySessionService()
        self._runner = Runner(
            app=App(name=app_name, root_agent=pipeline.workflow),
            session_service=self._session_service,
        )
        self._predecessors = _predecessors_of(pipeline)
        self._node_names = _node_names_of(pipeline)

    async def run(
        self,
        state: Mapping[str, Any],
        message: str,
        *,
        user_id: str,
        on_node: OnNode | None = None,
    ) -> GraphRun:
        """Drive the graph to completion, reporting each node as it lands.

        ``state`` seeds the session; ``message`` is the user turn the graph
        starts from. Both carry untrusted submitted text (OWASP LLM01) — they
        enter session state as data for a prompt's fenced block and are never
        concatenated into an instruction here. A node that raises propagates:
        the caller decides what a partial run means, and nothing here converts
        a failure into a run that merely looks short.
        """
        session = await self._session_service.create_session(
            app_name=self._app_name, user_id=user_id, state=dict(state)
        )
        started_at = datetime.now(UTC).timestamp()
        finishes: list[_NodeFinish] = []

        async for event in self._runner.run_async(
            user_id=user_id,
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text=message)]),
        ):
            for node in _finished_nodes(event, self._node_names):
                finishes.append(
                    _NodeFinish(
                        node=node,
                        at=event.timestamp,
                        served_model=getattr(event, "model_version", None),
                    )
                )
                if on_node is not None:
                    await on_node(node)

        final = await self._session_service.get_session(
            app_name=self._app_name, user_id=user_id, session_id=session.id
        )
        return GraphRun(
            final_state=dict(final.state) if final else {},
            node_runs=self._node_runs(finishes, started_at),
        )

    def _node_runs(
        self, finishes: list[_NodeFinish], started_at: float
    ) -> list[NodeRun]:
        """Per-node metadata in the order the nodes finished.

        A node's ``duration_ms`` is measured from the moment its last
        predecessor finished — the point the graph could have started it — to
        the event carrying its own output.
        """
        finished_at = {finish.node: finish.at for finish in finishes}
        runs = []
        for finish in finishes:
            ready_at = max(
                (
                    finished_at[predecessor]
                    for predecessor in self._predecessors[finish.node]
                    if predecessor in finished_at
                ),
                default=started_at,
            )
            requested = self._pipeline.node_models.get(finish.node)
            served = _served_route(requested, finish.served_model)
            runs.append(
                NodeRun(
                    node=finish.node,
                    model=served,
                    requested_model=requested,
                    sampling_fingerprint=self._fingerprint(finish.node, served),
                    duration_ms=max(round((finish.at - ready_at) * 1000), 0),
                )
            )
        return runs

    def _fingerprint(self, node: str, served_route: str | None) -> str | None:
        """This node execution's generation identity, or ``None`` if unknowable.

        Computed per *execution* (#7 decision 2), so 12 cases give one node 12
        hashes and a build that moves mid-run gives it two — which is the drift
        signal, not a defect. Without a served build there is nothing honest to
        hash, so the node carries no fingerprint at all rather than one keyed on
        what was merely requested.
        """
        sampling = self._pipeline.node_sampling.get(node)
        if served_route is None or sampling is None:
            return None
        return sampling_fingerprint(served_route, sampling)


def _served_route(requested_route: str | None, served_model: str | None) -> str | None:
    """The vendor-prefixed build that answered, or ``None`` if either half is missing.

    A deterministic FunctionNode has no requested route; a node whose event
    carried no ``model_version`` has no served build. Either way there is no
    served identity to record.
    """
    if requested_route is None or served_model is None:
        return None
    return join_served(requested_route, served_model)


def _predecessors_of(pipeline: Pipeline) -> dict[str, set[str]]:
    """Who must finish before each node can start, read off the built graph."""
    predecessors: dict[str, set[str]] = defaultdict(set)
    graph = pipeline.workflow.graph
    for edge in graph.edges if graph else []:
        predecessors[edge.to_node.name].add(edge.from_node.name)
    return predecessors


def _node_names_of(pipeline: Pipeline) -> set[str]:
    """Every node in the built graph, by name."""
    graph = pipeline.workflow.graph
    return {node.name for node in graph.nodes} if graph else set()


def _finished_nodes(event, known: set[str]) -> list[str]:
    """The graph nodes whose output this event carries, if any.

    ADK tags an event with the node paths it is the output for; the last
    segment of a path (``stride_pipeline@1/extract@1``) is the node name.
    The terminal node's event also carries the workflow's own path, which is
    not a node the job should hear about — matching against the graph's node
    names drops it.
    """
    node_info = getattr(event, "node_info", None)
    paths = getattr(node_info, "output_for", None) or []
    names = (path.rsplit("/", 1)[-1].split("@", 1)[0] for path in paths)
    return [name for name in names if name in known]
