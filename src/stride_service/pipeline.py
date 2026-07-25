"""Driving the ADK graph for one job: the real :class:`PipelineRunner`.

This is what sits behind the seam ticket 018 left for it. The job API knows
nothing new: it still hands a :class:`~stride_service.jobs.JobRecord` and a
node callback to something with a ``run`` method, and gets back either a
report or a structured rejection.

Everything the graph cannot know is stamped here. Job identity, the input
digest, and per-node timings belong to the run rather than to the analysis,
so :mod:`stride_service.graph` stops at an :class:`~stride_service.graph.Analysis`
and this module completes the :class:`~stride_service.report.StrideReport`
around it. A node's ``duration_ms`` is measured from the moment its last
predecessor finished — the point the graph could have started it — to the
event carrying its own output.

The submitted description is untrusted text (OWASP LLM01): it enters session
state as data for the extraction prompt's fenced ``{input_text}`` block and
is never concatenated into an instruction here.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from google.adk import Runner
from google.adk.apps import App
from google.adk.models.base_llm import BaseLlm
from google.adk.models.google_llm import Gemini
from google.adk.sessions import BaseSessionService, InMemorySessionService
from google.genai import types

from stride_service.graph import (
    STATE_ANALYSIS,
    STATE_INPUT_TEXT,
    STATE_REJECTION,
    Analysis,
    ModelResolver,
    Pipeline,
    build_pipeline,
    rejection_issues,
)
from stride_service.jobs import (
    JobRecord,
    NodeCallback,
    PipelineCompleted,
    PipelineOutcome,
    PipelineRejected,
)
from stride_service.markdown_loader import MarkdownLoader
from stride_service.model_tiers import ModelTierConfig, load_model_tiers
from stride_service.report import InputRef, Job, NodeRun, StrideReport
from stride_service.resilience import ResilienceConfig, load_resilience
from stride_service.sampling import load_sampling, make_resolve_sampling

logger = logging.getLogger(__name__)

DEFAULT_APP_NAME = "stride-service"

# Reports need a system name; the front end may not have sent one.
DEFAULT_SYSTEM_NAME = "Unnamed system"

# The repo layout baked into the image (ticket 006): Markdown next to the
# package, not fetched at run time. Overridable for a different image layout,
# never for content the image does not carry.
_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SKILLS_DIR = _REPO_ROOT / "skills"
DEFAULT_PROMPTS_DIR = _REPO_ROOT / "prompts"
DEFAULT_MODEL_TIERS_PATH = _REPO_ROOT / "config" / "model_tiers.toml"
DEFAULT_SAMPLING_PATH = _REPO_ROOT / "config" / "sampling.toml"
DEFAULT_RESILIENCE_PATH = _REPO_ROOT / "config" / "resilience.toml"

SKILLS_DIR_VAR = "STRIDE_SKILLS_DIR"
PROMPTS_DIR_VAR = "STRIDE_PROMPTS_DIR"
MODEL_TIERS_VAR = "STRIDE_MODEL_TIERS"
SAMPLING_VAR = "STRIDE_SAMPLING"
RESILIENCE_VAR = "STRIDE_RESILIENCE"


class PipelineError(RuntimeError):
    """The graph finished without producing an analysis or a rejection."""


@dataclass(frozen=True)
class _NodeFinish:
    """When one graph node produced its output."""

    node: str
    at: float


class AdkPipelineRunner:
    """Runs one job through the ADK Workflow and shapes the outcome.

    One :class:`~stride_service.graph.Pipeline` is built at startup and
    reused: instructions are composed once, so the cacheable prefix every
    node shares is byte-identical across jobs.
    """

    def __init__(
        self,
        pipeline: Pipeline,
        *,
        session_service: BaseSessionService | None = None,
        app_name: str = DEFAULT_APP_NAME,
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

    async def run(self, job: JobRecord, on_node: NodeCallback) -> PipelineOutcome:
        """Drive the graph to a terminal state, reporting each node as it lands.

        A node that raises propagates: the job fails loudly rather than
        completing with a report built on a check that did not run. On that
        path the input's digest is logged (ticket 038 decision 5) so a poison
        input that repeatedly wedges jobs is identifiable across runs without
        the service ever storing the untrusted text.
        """
        source_sha256 = hashlib.sha256(job.description.encode("utf-8")).hexdigest()
        try:
            return await self._drive(job, on_node, source_sha256)
        except Exception:
            logger.warning(
                "job %s failed in the graph; source_sha256=%s", job.id, source_sha256
            )
            raise

    async def _drive(
        self, job: JobRecord, on_node: NodeCallback, source_sha256: str
    ) -> PipelineOutcome:
        session = await self._session_service.create_session(
            app_name=self._app_name,
            user_id=job.owner_subject,
            state={STATE_INPUT_TEXT: job.description},
        )
        started_at = datetime.now(UTC).timestamp()
        finishes: list[_NodeFinish] = []

        async for event in self._runner.run_async(
            user_id=job.owner_subject,
            session_id=session.id,
            new_message=types.Content(
                role="user", parts=[types.Part(text=job.description)]
            ),
        ):
            for node in _finished_nodes(event, self._node_names):
                finishes.append(_NodeFinish(node=node, at=event.timestamp))
                await on_node(node)

        final = await self._session_service.get_session(
            app_name=self._app_name, user_id=job.owner_subject, session_id=session.id
        )
        state = dict(final.state) if final else {}
        if STATE_REJECTION in state:
            return PipelineRejected(issues=rejection_issues(state[STATE_REJECTION]))
        if STATE_ANALYSIS not in state:
            raise PipelineError(
                f"job {job.id}: graph produced neither an analysis nor a rejection"
            )

        analysis = Analysis.from_state(state[STATE_ANALYSIS])
        return PipelineCompleted(
            report=self._build_report(
                job, analysis, self._node_runs(finishes, started_at), source_sha256
            )
        )

    def _node_runs(
        self, finishes: list[_NodeFinish], started_at: float
    ) -> list[NodeRun]:
        """Per-node metadata in the order the nodes finished."""
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
            runs.append(
                NodeRun(
                    node=finish.node,
                    model=self._pipeline.node_models.get(finish.node),
                    sampling_fingerprint=self._pipeline.node_fingerprints.get(
                        finish.node
                    ),
                    duration_ms=max(round((finish.at - ready_at) * 1000), 0),
                )
            )
        return runs

    def _build_report(
        self,
        job: JobRecord,
        analysis: Analysis,
        nodes: list[NodeRun],
        source_sha256: str,
    ) -> StrideReport:
        return StrideReport(
            job=Job(
                id=job.id,
                created_at=job.created_at,
                completed_at=datetime.now(UTC),
            ),
            input=InputRef(
                system_name=job.system_name or DEFAULT_SYSTEM_NAME,
                source_sha256=source_sha256,
            ),
            nodes=nodes,
            sampling={
                tier: params.model_dump()
                for tier, params in self._pipeline.tier_sampling.items()
            },
            system_model=analysis.system_model,
            boundary_crossings=analysis.boundary_crossings,
            threats=analysis.threats,
            rejected_threats=analysis.rejected_threats,
            summary=analysis.summary,
        )


def resilient_resolver(
    tiers: ModelTierConfig, resilience: ResilienceConfig
) -> ModelResolver:
    """A resolver that binds each node's pinned model with its retry policy.

    ``ModelTierConfig.resolve_model`` returns the pinned *string*; production
    wraps it in a :class:`Gemini` carrying the client-level retry (ticket 038
    decision 2) so a single 429 no longer kills a paid-for job. ``build_pipeline``
    accepts a ``BaseLlm`` here, and ``_model_name`` unwraps it back to the
    pinned string, so the report's ``nodes`` array is unchanged.
    """
    retry_options = resilience.to_retry_options()

    def resolve(node: str) -> BaseLlm:
        return Gemini(model=tiers.resolve_model(node), retry_options=retry_options)

    return resolve


def build_default_pipeline(env: Mapping[str, str] | None = None) -> Pipeline:
    """The production graph: repo Markdown, repo config, pinned models.

    Fails closed on a missing or invalid tier, sampling, or resilience config
    rather than starting a service whose nodes would run on whatever model,
    decoding parameters, or retry/timeout behaviour happened to be default.
    """
    if env is None:
        env = os.environ
    tiers = load_model_tiers(
        env.get(MODEL_TIERS_VAR, DEFAULT_MODEL_TIERS_PATH), env=env
    )
    resilience = load_resilience(
        env.get(RESILIENCE_VAR, DEFAULT_RESILIENCE_PATH), env=env
    )
    sampling = load_sampling(env.get(SAMPLING_VAR, DEFAULT_SAMPLING_PATH), env=env)
    return build_pipeline(
        skill_loader=MarkdownLoader(env.get(SKILLS_DIR_VAR, DEFAULT_SKILLS_DIR)),
        prompt_loader=MarkdownLoader(env.get(PROMPTS_DIR_VAR, DEFAULT_PROMPTS_DIR)),
        resolve_model=resilient_resolver(tiers, resilience),
        resolve_sampling=make_resolve_sampling(sampling, tiers.resolve_tier),
        tier_sampling=sampling.tiers,
        resilience=resilience,
    )


def default_pipeline_runner(env: Mapping[str, str] | None = None) -> AdkPipelineRunner:
    """The runner the API uses when no other is injected."""
    return AdkPipelineRunner(build_default_pipeline(env))


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
