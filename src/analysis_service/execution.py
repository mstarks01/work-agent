"""Driving a built graph, and recording what each node execution presented.

A **Graph Run** is one drive of a :class:`~analysis_service.graph.Pipeline` to
completion. It is the final session state, plus one
:class:`~analysis_service.report.NodeRun` per node execution. It is deliberately
not a report: job identity, the input digest and certification belong to whoever
asked for the run, rather than to the graph.

The graph has two drivers, :class:`~analysis_service.pipeline.AdkPipelineRunner`
and the eval harness, and both stamp their node runs here. There is one
implementation, so a sweep cannot certify against fingerprints it never
recorded.

What the graph cannot know stays with the caller. What only the driver can
observe is here: which node an ADK event is the output for, what build answered
it, and when its last predecessor finished.

Rendering the job's sources is here for the same reason. Both drivers cross this
seam, so a render one level up would be two renders that have to agree.
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

from analysis_service.charges import CHARGE_METADATA_KEY
from analysis_service.claims import (
    Claim,
    FrameworkName,
)
from analysis_service.frameworks import package_for
from analysis_service.graph import (
    STATE_INPUT_TEXT,
    STATE_SOURCE_TEXTS,
    FrameworkNodes,
    GraphProducedNothing,
    Pipeline,
    Rejected,
    result_of,
    revise_rounds,
)
from analysis_service.identity import build_identity, execution_fingerprint
from analysis_service.report import (
    InputRef,
    Job,
    NodeRun,
    Report,
    TokenUsage,
)
from analysis_service.retry import ATTEMPTS_METADATA_KEY
from analysis_service.sources import Source, render_sources
from analysis_service.vendors import join_served

# Awaited with each node name as it lands. Structurally the same callable as
# ``jobs.NodeCallback``, spelled out rather than imported: the job lifecycle is
# one caller of the graph, and this module knows nothing about jobs.
OnNode = Callable[[str], Awaitable[None]]


class GraphFailed(Exception):
    """A node raised mid-graph, and this is what had finished before it.

    The provider billed every node that finished, and the one that raised may
    have been billed too, for a call whose usage never reached this driver:
    ADK tags its error event with the node's path, so that node may appear
    here with no usage, or not at all. Either way ``node_runs`` is a floor on
    the spend, never the spend, and it is what the caller has: a sweep prices
    a failed case from it, and the job route keeps its reservation rather than
    settling to a figure it knows is short.
    ``cause`` is the exception the caller classifies, exactly the one it would
    have caught before this wrapper existed.
    """

    def __init__(self, cause: Exception, node_runs: Sequence[NodeRun]) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.node_runs = tuple(node_runs)


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

    ``reported_charge_usd`` is what the provider says it *charged*, which is a
    different fact from what the call spent in tokens and is carried beside it
    rather than instead of it. ``None`` from every vendor that states no charge,
    and from a vendor that states one under an arrangement where the figure
    covers part of a call — :mod:`analysis_service.charges` decides which, and
    the adapter that never captured a figure stamps none.
    """

    node: str
    at: float
    served_model: str | None
    usage: TokenUsage | None = None
    attempts: int = 1
    reported_charge_usd: float | None = None


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

    def report(
        self, *, job: Job, input_ref: InputRef, pipeline: Pipeline
    ) -> Report | Rejected:
        """This run as the report a driver returns, or the rejection it parked.

        **One reader for both drivers.** The service over a job and the eval
        harness over a corpus case each used to read the final state, decide
        which terminal shape it held, stamp the re-ask count and call
        :meth:`~analysis_service.graph.Analysis.into_report` themselves, and a
        fix to one reader reached the other only by hand. What differs between
        the two is what they *do* with a rejection, so that is what each keeps.

        ``job`` carries the identity only a driver knows. Its ``revise_rounds``
        is stamped here from this run's own node runs, because the count is a
        fact about the drive rather than about the job, and a driver that
        forgot it reported a clean run.

        Raises :class:`~analysis_service.graph.GraphProducedNothing` when the
        state holds neither terminal shape; each driver names the run it was
        driving.
        """
        result = result_of(self.final_state)
        if isinstance(result, Rejected):
            return result
        stamped = job.model_copy(
            update={
                "revise_rounds": revise_rounds(
                    self.node_runs, [entry.name for entry in job.frameworks]
                )
            }
        )
        return result.into_report(
            job=stamped, input_ref=input_ref, nodes=self.node_runs, pipeline=pipeline
        )

    def drafts_of(self, framework: FrameworkName) -> list[Claim]:
        """The drafts this framework's fan-in parked for its critic.

        Revalidated as the package's own record, the way the assemble node
        reads them, so a scorer stays typed against the shipped model. A
        framework whose subgraph ran wrote the key; one with no drafts key is a
        graph that never reached this framework's fan-in, which is the
        driver's defect rather than an empty analysis.
        """
        key = FrameworkNodes(framework).key("drafts")
        if key not in self.final_state:
            raise GraphProducedNothing(
                f"graph produced a {framework} analysis with no drafts"
            )
        record = package_for(framework).record
        return [record.model_validate(draft) for draft in self.final_state[key]]

    def proposals_of(self, framework: FrameworkName) -> dict[str, Any]:
        """Each lane's emission as its node dumped it, before the fan-in.

        A draft no longer carries the lane's own answer to *what would settle
        this*; the fan-in strips it once it has routed the proposal. A lane
        that wrote no key contributes nothing here rather than failing, because
        that absence is the fan-in's to refuse.
        """
        return {
            lane.lane: self.final_state[lane.drafts_key]
            for lane in FrameworkNodes(framework).lanes
            if lane.drafts_key in self.final_state
        }


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

        A node that raises propagates as :class:`GraphFailed`, carrying the
        node runs that finished before it: the caller decides what a partial
        run means, and nothing here converts a failure into a run that looks
        short or into one that looks free.
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

        # The session holds the rendered submission, every node's output and
        # the report, so it goes when the run does — completed, failed or
        # cancelled. A session service that kept them would hold one job's
        # text per job for the life of the process.
        try:
            async for event in self._runner.run_async(
                user_id=user_id,
                session_id=session.id,
                new_message=types.Content(
                    role="user", parts=[types.Part(text=rendered)]
                ),
            ):
                observed_at = datetime.now(UTC).timestamp()
                for node in _finished_nodes(event, self._node_names):
                    finishes.append(
                        _NodeFinish(
                            node=node,
                            at=observed_at,
                            served_model=getattr(event, "model_version", None),
                            usage=_usage_of(event),
                            attempts=_attempts_of(event),
                            reported_charge_usd=_reported_charge_of(event),
                        )
                    )
                    if on_node is not None:
                        await on_node(node)

            final = await self._session_service.get_session(
                app_name=self._app_name, user_id=user_id, session_id=session.id
            )
        except Exception as exc:
            raise GraphFailed(exc, self._node_runs(finishes, started_at)) from exc
        finally:
            await self._session_service.delete_session(
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
            fingerprint = self._fingerprint(finish.node, requested, served)
            runs.append(
                NodeRun(
                    node=finish.node,
                    model=served,
                    requested_model=requested,
                    # Stamped with the fingerprint or not at all: the digest is
                    # an input to that hash, and a row carrying one without the
                    # other states an identity it cannot account for.
                    instruction_sha256=(
                        self._pipeline.instruction_sha256 if fingerprint else None
                    ),
                    execution_fingerprint=fingerprint,
                    duration_ms=max(round((finish.at - ready_at) * 1000), 0),
                    usage=finish.usage,
                    attempts=finish.attempts,
                    reported_charge_usd=finish.reported_charge_usd,
                )
            )
            finished_at[finish.node] = finish.at
        return runs

    def _fingerprint(
        self, node: str, requested_route: str | None, served_route: str | None
    ) -> str | None:
        """This node execution's identity hash, or ``None`` if unknowable.

        Computed per *execution*, so 12 cases give one node 12 hashes and a
        build that moves mid-sweep gives it two — which is the drift signal,
        not a defect.

        Three inputs have to be present and any one of them missing means there
        is nothing honest to hash. Without a served build the node ran but
        nobody said on what; without a requested route it is not an LLM node;
        without sampling its tier resolved to nothing. A node short of any of
        them carries no fingerprint rather than one keyed on a partial identity,
        which would certify against a manifest entry it does not share.

        The instruction digest and the build versions come from the pipeline and
        the install, so they are the same for every node in one drive. They are
        in each node's hash anyway, because a fingerprint the manifest blesses
        has to carry everything that decided the answer — a per-run field
        checked separately is a second gate somebody can forget to run.
        """
        sampling = self._pipeline.node_sampling.get(node)
        if served_route is None or requested_route is None or sampling is None:
            return None
        return execution_fingerprint(
            requested_route=requested_route,
            served_route=served_route,
            sampling=sampling.model_dump(),
            instruction_sha256=self._pipeline.instruction_sha256,
            build=build_identity(),
        )


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


def _attempts_of(event) -> int:
    """How many provider calls this event's node took, as the retry driver said.

    One where the event carries no stamp: a deterministic node makes no call,
    and a model that did not pass through the retry driver made exactly one.
    """
    return (getattr(event, "custom_metadata", None) or {}).get(ATTEMPTS_METADATA_KEY, 1)


def _reported_charge_of(event) -> float | None:
    """What the provider said it charged for this event's call, if it said.

    Read off the same ``custom_metadata`` stamp ``attempts`` travels in, and
    absent for the same two reasons a stamp is ever absent: the call did not
    pass through an adapter that captures a charge, or the provider reported
    none. :mod:`analysis_service.charges` owns both the capture and the rule
    about which figures may be recorded, so nothing here decides anything.
    """
    return (getattr(event, "custom_metadata", None) or {}).get(CHARGE_METADATA_KEY)


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
    segment of a path (``analysis_pipeline@1/extract@1``) is the node name.
    The terminal node's event also carries the workflow's own path, which is
    not a node the job should hear about — matching against the graph's node
    names drops it.
    """
    node_info = getattr(event, "node_info", None)
    paths = getattr(node_info, "output_for", None) or []
    names = (path.rsplit("/", 1)[-1].split("@", 1)[0] for path in paths)
    return [name for name in names if name in known]
