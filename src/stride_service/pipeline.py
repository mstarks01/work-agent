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
from google.adk.sessions import BaseSessionService, InMemorySessionService
from google.genai import types

from stride_service.binding import build_tier_adapters, make_resolve_model
from stride_service.certification import (
    CertificationGate,
    CertifyResult,
    load_manifest,
)
from stride_service.graph import (
    STATE_ANALYSIS,
    STATE_INPUT_TEXT,
    STATE_REJECTION,
    TIER_NODE_BY_GRAPH_NODE,
    Analysis,
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
from stride_service.resilience import load_resilience
from stride_service.sampling import (
    load_sampling,
    make_resolve_sampling,
    sampling_fingerprint,
)
from stride_service.vendors import join_served

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
DEFAULT_BLESSED_FINGERPRINTS_PATH = (
    _REPO_ROOT / "config" / "blessed-fingerprints.toml"
)

SKILLS_DIR_VAR = "STRIDE_SKILLS_DIR"
PROMPTS_DIR_VAR = "STRIDE_PROMPTS_DIR"
MODEL_TIERS_VAR = "STRIDE_MODEL_TIERS"
SAMPLING_VAR = "STRIDE_SAMPLING"
RESILIENCE_VAR = "STRIDE_RESILIENCE"
BLESSED_FINGERPRINTS_VAR = "STRIDE_BLESSED_FINGERPRINTS"
REQUIRE_CERTIFIED_VAR = "STRIDE_REQUIRE_CERTIFIED"


class PipelineError(RuntimeError):
    """The graph finished without producing an analysis or a rejection."""


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
        certification: CertificationGate | None = None,
    ) -> None:
        self._pipeline = pipeline
        self._certification = certification
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
                finishes.append(
                    _NodeFinish(
                        node=node,
                        at=event.timestamp,
                        served_model=getattr(event, "model_version", None),
                    )
                )
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
        report = self._build_report(
            job, analysis, self._node_runs(finishes, started_at), source_sha256
        )
        return PipelineCompleted(
            report=report, certification=self._certify(job, report)
        )

    def _certify(self, job: JobRecord, report: StrideReport) -> CertifyResult | None:
        """Certify the finished report against this deployment's manifest.

        Runs **once, after the report is built**: a fingerprint exists only per
        node execution, and the expectation of what should have run is only
        complete once every node has, so certifying earlier would certify a
        partial run.

        Logged for the operator, never surfaced to the client (#17 decision 6,
        OWASP A09): hashes and node names, never the report's contents. A client
        learns about certification exactly when its deployment opted into
        caring.
        """
        if self._certification is None:
            return None
        result = self._certification.check(report, self._pipeline.node_sampling)
        if not result.certified or not result.complete:
            logger.warning(
                "job %s certification: certified=%s uncertified=%s unexercised=%s",
                job.id,
                result.certified,
                [node.to_json() for node in result.uncertified],
                list(result.unexercised),
            )
        return result

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


def _served_route(requested_route: str | None, served_model: str | None) -> str | None:
    """The vendor-prefixed build that answered, or ``None`` if either half is missing.

    A deterministic FunctionNode has no requested route; a node whose event
    carried no ``model_version`` has no served build. Either way there is no
    served identity to record.
    """
    if requested_route is None or served_model is None:
        return None
    return join_served(requested_route, served_model)


def build_default_pipeline(env: Mapping[str, str] | None = None) -> Pipeline:
    """The production graph: repo Markdown, repo config, pinned models.

    Fails closed on a missing or invalid tier, sampling, or resilience config
    rather than starting a service whose nodes would run on whatever model,
    decoding parameters, or retry/timeout behaviour happened to be default.
    Building the tier adapters adds two more build-time gates — the supported-
    param check and the credential check — so an unusable provider selection is
    caught here rather than on node one of a paid-for job.
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
    adapters = build_tier_adapters(tiers, sampling, resilience, env=env)
    return build_pipeline(
        skill_loader=MarkdownLoader(env.get(SKILLS_DIR_VAR, DEFAULT_SKILLS_DIR)),
        prompt_loader=MarkdownLoader(env.get(PROMPTS_DIR_VAR, DEFAULT_PROMPTS_DIR)),
        resolve_model=make_resolve_model(adapters, tiers),
        resolve_sampling=make_resolve_sampling(sampling, tiers.resolve_tier),
        tier_sampling=sampling.tiers,
        resilience=resilience,
    )


def build_certification_gate(
    tiers: ModelTierConfig, env: Mapping[str, str]
) -> CertificationGate:
    """Load this deployment's blessed manifest and its gating policy.

    Located by ``STRIDE_BLESSED_FINGERPRINTS``, defaulting to the repo path —
    the fourth config of the tiers/sampling/resilience kind. A repo default plus
    an env override is **not** the two-layer overlay #10 rejected: exactly one
    file is read, and the variable only picks which.

    Unset-means-disabled was rejected outright — an absent environment variable
    silently switching off a provenance check is precisely the failure mode this
    exists to prevent — so a missing or malformed manifest raises here, at
    startup, rather than at the first completed job.
    """
    manifest = load_manifest(
        env.get(BLESSED_FINGERPRINTS_VAR, DEFAULT_BLESSED_FINGERPRINTS_PATH)
    )

    def tier_of(graph_node: str) -> str:
        return tiers.resolve_tier(TIER_NODE_BY_GRAPH_NODE[graph_node])

    return CertificationGate(
        manifest=manifest,
        tier_of=tier_of,
        require_certified=_flag(env, REQUIRE_CERTIFIED_VAR),
    )


def _flag(env: Mapping[str, str], var: str) -> bool:
    """A boolean env flag, on only for an explicit affirmative."""
    return env.get(var, "").strip().lower() in ("1", "true", "yes", "on")


def default_pipeline_runner(env: Mapping[str, str] | None = None) -> AdkPipelineRunner:
    """The runner the API uses when no other is injected.

    Certification is assembled here rather than in ``build_pipeline``: which
    manifest a deployment blesses against is a runner concern, and the graph
    does not certify.
    """
    if env is None:
        env = os.environ
    tiers = load_model_tiers(
        env.get(MODEL_TIERS_VAR, DEFAULT_MODEL_TIERS_PATH), env=env
    )
    return AdkPipelineRunner(
        build_default_pipeline(env),
        certification=build_certification_gate(tiers, env),
    )


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
