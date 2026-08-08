"""Driving a built graph, and recording what each node execution presented.

A **Graph Run** is one drive of a :class:`~stride_service.graph.Pipeline` to
completion: the final session state, plus one :class:`~stride_service.report.NodeRun`
per node execution. It is deliberately *not* a report — job identity, the input
digest and certification belong to whoever asked for the run, not to the graph.

The graph has two drivers — :class:`~stride_service.pipeline.AdkPipelineRunner`
and the eval harness — and both stamp their node runs here. One implementation,
so a sweep cannot certify against fingerprints it never recorded.

What the graph cannot know stays with the caller. What only the driver can
observe — which node an ADK event is the output for, what build answered it,
when its last predecessor finished — is here.

Rendering the job's sources is here for the same reason: both drivers cross
this seam, so a render that happened one level up would be two renders that
have to agree.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from google.adk import Runner
from google.adk.apps import App
from google.adk.sessions import BaseSessionService, InMemorySessionService
from google.genai import types

from stride_service.graph import (
    STATE_INPUT_TEXT,
    STATE_SOURCE_TEXTS,
    Pipeline,
)
from stride_service.report import NodeRun, TokenUsage
from stride_service.sampling import sampling_fingerprint
from stride_service.sources import Source, render_sources
from stride_service.vendors import join_served

# Awaited with each node name as it lands. Structurally the same callable as
# ``jobs.NodeCallback``, spelled out rather than imported: the job lifecycle is
# one caller of the graph, and this module knows nothing about jobs.
OnNode = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class _NodeFinish:
    """When one graph node produced its output, and what build answered it.

    ``at`` is when this driver **observed** the event, not ``event.timestamp``.
    An LlmAgent's response event is constructed before the request goes out
    (``base_llm_flow`` builds it, then calls the model) and
    ``_finalize_model_response_event`` copies that timestamp onto the event it
    yields, so ``event.timestamp`` marks when a node's request was *issued*.
    Measuring from it charged every LLM node's latency to its successor: a
    21-second extraction was reported as 5 ms on ``extract`` and 21,757 ms on
    the ``validate`` FunctionNode that ran after it. Observation time is the
    completion time by construction — the event does not reach this loop until
    the node is done — and it is read from the same clock as ``started_at``,
    which ``event.timestamp`` was not.

    ``served_model`` is the build the provider says actually ran, read off the
    event rather than assumed from the configured string. It is ``None`` when
    the event carries none — an offline stand-in, or a provider that did not
    report one — and a node with no served build gets no fingerprint rather
    than one attesting to a model nobody confirmed.

    ``usage`` is what the provider says the call cost, on the same terms: read
    off the event, ``None`` when the event carries none.
    """

    node: str
    at: float
    served_model: str | None
    usage: TokenUsage | None = None


@dataclass(frozen=True)
class GraphRun:
    """One drive of the graph: what the session ended holding, and what ran.

    ``node_runs`` is in the order the nodes finished, one entry per node
    *execution*. The graph cannot loop and ``critic``/``recritic`` are distinct
    nodes, so one drive gives each node at most one entry; multiplicity for a
    single node name appears only where a caller accumulates several drives,
    as an eval sweep does across its corpus.
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
        sources: Sequence[Source],
        *,
        user_id: str,
        extra_state: Mapping[str, Any] | None = None,
        on_node: OnNode | None = None,
    ) -> GraphRun:
        """Drive the graph to completion, reporting each node as it lands.

        The job's sources are rendered **here**, once, and the result seeds both
        the session state and the user turn the graph starts from. Taking the
        sources rather than a rendered string is what makes it impossible for
        the service and the eval harness to show one job to a model differently:
        there is no way to express seeding raw text.

        Everything the render produces is untrusted submitted text (OWASP
        LLM01). It enters as data inside per-source fences and is never
        concatenated into an instruction here.

        ``extra_state`` seeds keys the graph needs that are not the input — an
        eval mode injecting an already-blessed model at a later entry point. It
        may not carry the input text: that key is this method's to write.

        A node that raises propagates: the caller decides what a partial run
        means, and nothing here converts a failure into a run that looks short.
        """
        rendered = render_sources(sources)
        seed: dict[str, Any] = dict(extra_state or {})
        if STATE_INPUT_TEXT in seed:
            raise ValueError(
                f"{STATE_INPUT_TEXT} is rendered from the job's sources, "
                "not seeded by the caller"
            )
        seed[STATE_INPUT_TEXT] = rendered
        # The same bytes, structured: the validity gate checks each element's
        # citation against these labels, and the draft fan-in checks each
        # finding's quote against the text under the label it names. Both travel
        # beside the rendered copy because both are facts about the job rather
        # than about the model, and both are the executor's to write. Keyed by
        # label safely — a job with two sources sharing one is refused before it
        # reaches here, since a citation naming two sources at once resolves
        # while pointing nowhere.
        seed[STATE_SOURCE_TEXTS] = {source.label: source.text for source in sources}

        session = await self._session_service.create_session(
            app_name=self._app_name, user_id=user_id, state=seed
        )
        started_at = datetime.now(UTC).timestamp()
        finishes: list[_NodeFinish] = []

        async for event in self._runner.run_async(
            user_id=user_id,
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text=rendered)]),
        ):
            observed_at = datetime.now(UTC).timestamp()
            for node in _finished_nodes(event, self._node_names):
                finishes.append(
                    _NodeFinish(
                        node=node,
                        at=observed_at,
                        served_model=getattr(event, "model_version", None),
                        usage=_usage_of(event),
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
        the moment this driver observed the node's own output. See
        :class:`_NodeFinish` for why observation time rather than the event's
        own timestamp.

        Predecessors resolve per *execution*, not per name: ``finished_at``
        is built as the walk proceeds, so each finish sees only what landed
        before it. A name-keyed map of every finish would hand a repeated
        predecessor's *last* time to an earlier successor, dating it after its
        own finish and reporting the clamped 0 ms this module exists to stop.
        """
        finished_at: dict[str, float] = {}
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
                    usage=finish.usage,
                )
            )
            finished_at[finish.node] = finish.at
        return runs

    def _fingerprint(self, node: str, served_route: str | None) -> str | None:
        """This node execution's generation identity, or ``None`` if unknowable.

        Computed per *execution*, so 12 cases give one node 12 hashes and a
        build that moves mid-sweep gives it two — which is the drift signal,
        not a defect. Without a served build there is nothing honest to hash, so
        the node carries no fingerprint at all rather than one keyed on what
        was merely requested.
        """
        sampling = self._pipeline.node_sampling.get(node)
        if served_route is None or sampling is None:
            return None
        return sampling_fingerprint(served_route, sampling)


# The provider's spelling for each vendor-neutral TokenUsage field. This is the
# whole of the mapping, and it lives here rather than on the model because
# reading a provider's vocabulary off an event is the driver's job — the same
# reason ``model_version`` is read here and not in ``report``.
_USAGE_FIELDS: Mapping[str, str] = {
    "prompt_tokens": "prompt_token_count",
    "cached_prompt_tokens": "cached_content_token_count",
    "completion_tokens": "candidates_token_count",
    "reasoning_tokens": "thoughts_token_count",
    "total_tokens": "total_token_count",
}


def _usage_of(event) -> TokenUsage | None:
    """What the provider says this event's call cost, or ``None`` if it said nothing.

    A field the provider withheld arrives as ``None`` and is recorded as 0,
    which is the honest reading only because the all-withheld case is caught
    first: an event with no usage block at all, or one whose every counter is
    absent, yields ``None`` rather than a zeroed record. That distinction is
    the same one ``merge_drafts`` draws between a lane that ran and found
    nothing and a lane that never ran — a free call and an unmeasured call
    are not the same fact, and a summed report cannot tell them apart after
    the fact.

    Counters are read defensively because this runs against every vendor the
    ``strong`` and ``base`` tiers can independently select, and the usage block
    is the least uniform part of a completion response.
    """
    metadata = getattr(event, "usage_metadata", None)
    if metadata is None:
        return None
    counts = {
        field: getattr(metadata, provider_field, None)
        for field, provider_field in _USAGE_FIELDS.items()
    }
    if all(count is None for count in counts.values()):
        return None
    return TokenUsage(**{field: count or 0 for field, count in counts.items()})


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
