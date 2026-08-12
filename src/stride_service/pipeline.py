"""Driving the ADK graph for one job: the real :class:`PipelineRunner`.

The implementation behind :class:`~stride_service.jobs.PipelineRunner`: the job
API hands it a :class:`~stride_service.jobs.JobRecord` and a node callback, and
gets back either a report or a structured rejection.

Everything the graph cannot know is stamped here. Job identity, the input
digest, and per-node timings belong to the run rather than to the analysis, so
:mod:`stride_service.graph` stops at an :class:`~stride_service.graph.Analysis`
and this module hands those four facts to
:meth:`~stride_service.graph.Analysis.into_report`. The report's own shape is
that method's, shared with the eval harness, so the two drivers cannot name
different field sets. A node's ``duration_ms`` is measured from the moment its
last predecessor finished — the point the graph could have started it — to the
moment the executor observed its own output.

The submitted sources are untrusted text (OWASP LLM01). This module never
renders them: it hands the job's sources to the executor, which is the single
place a job becomes prompt bytes, and takes back the digest that ties the
report to what was submitted.

Which config this runner is built from is not this module's business: see
:class:`stride_service.deployment.Deployment`, which locates and loads it once
per process and hands back a configured runner.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from google.adk.sessions import BaseSessionService

from stride_service.certification import CertificationGate, CertifyResult
from stride_service.execution import GraphExecutor
from stride_service.graph import (
    STATE_ANALYSIS,
    STATE_REJECTION,
    Analysis,
    Pipeline,
    rejection_issues,
)
from stride_service.jobs import (
    JobRecord,
    NodeCallback,
    PipelineCompleted,
    PipelineOutcome,
    PipelineRejected,
)
from stride_service.report import InputRef, Job, StrideReport

logger = logging.getLogger(__name__)

DEFAULT_APP_NAME = "stride-service"

# Reports need a system name; the front end may not have sent one.
DEFAULT_SYSTEM_NAME = "Unnamed system"


class PipelineError(RuntimeError):
    """The graph finished without producing an analysis or a rejection."""


class AdkPipelineRunner:
    """Runs one job through the ADK Workflow and shapes the outcome.

    One :class:`~stride_service.graph.Pipeline` is built at startup and
    reused: instructions are composed once, so the cacheable prefix every
    node shares is byte-identical across jobs. Driving that graph and stamping
    each node execution is :class:`~stride_service.execution.GraphExecutor`'s,
    shared with the eval harness so both stamp identically; what stays here is
    what only a *job* has — its identity, its input digest, and the
    certification its deployment applies.
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
        self._executor = GraphExecutor(
            pipeline, app_name=app_name, session_service=session_service
        )

    async def run(self, job: JobRecord, on_node: NodeCallback) -> PipelineOutcome:
        """Drive the graph to a terminal state, reporting each node as it lands.

        A node that raises propagates: the job fails loudly rather than
        completing with a report built on a check that did not run. On that
        path the input's digest is logged, so a poison input that repeatedly
        wedges jobs is identifiable across runs without the service ever
        storing the untrusted text.
        """
        input_ref = InputRef.of(
            system_name=job.system_name or DEFAULT_SYSTEM_NAME, sources=job.sources
        )
        try:
            return await self._drive(job, on_node, input_ref)
        except Exception:
            logger.warning(
                "job %s failed in the graph; source_sha256=%s",
                job.id,
                input_ref.source_sha256,
            )
            raise

    async def _drive(
        self, job: JobRecord, on_node: NodeCallback, input_ref: InputRef
    ) -> PipelineOutcome:
        graph_run = await self._executor.run(
            job.sources, user_id=job.owner_subject, on_node=on_node
        )
        state = graph_run.final_state
        if STATE_REJECTION in state:
            return PipelineRejected(issues=rejection_issues(state[STATE_REJECTION]))
        if STATE_ANALYSIS not in state:
            raise PipelineError(
                f"job {job.id}: graph produced neither an analysis nor a rejection"
            )

        analysis = Analysis.from_state(state[STATE_ANALYSIS])
        report = analysis.into_report(
            job=Job(
                id=job.id,
                created_at=job.created_at,
                completed_at=datetime.now(UTC),
            ),
            input_ref=input_ref,
            nodes=graph_run.node_runs,
            pipeline=self._pipeline,
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

        Logged for the operator, never surfaced to the client (OWASP A09):
        hashes and node names, never the report's contents. A client learns
        about certification exactly when its deployment opted into caring.
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
