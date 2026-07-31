"""In-process entry point for the analysis pipeline: text in, report out.

The HTTP ``/v1`` API (:mod:`stride_service.api`) is one caller of the analysis
pipeline; this module is the other. An application that wants to run STRIDE
analysis in process — swapping this pipeline in behind its own analysis-engine
interface — reaches for :class:`StrideEngine` instead of fabricating a
:class:`~stride_service.jobs.JobRecord`, an auth subject, and a node callback by
hand.

The engine carries none of the HTTP contract's ceremony: no bearer token, no
job store, no polling. It builds the pipeline once — the expensive
shared-prefix composition is amortised across jobs — and runs each submission
to a terminal state. Success returns a
:class:`~stride_service.report.StrideReport`, an input the validity gate
rejects returns its :class:`~stride_service.validation.ValidationIssue`s, and
an internal failure raises. That trichotomy is the job lifecycle's ``completed
| rejected | failed`` with the transport removed.

The submitted description is untrusted text (OWASP LLM01): the engine caps its
size and hands it to the pipeline, which places it in session state as fenced
data for the extraction prompt and never concatenates it into an instruction.
The engine adds no new trust surface — it only re-asserts, for the in-process
path, the input bounds the HTTP layer already enforces.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Self

from stride_service.deployment import Deployment
from stride_service.jobs import (
    MAX_DESCRIPTION_BYTES,
    JobRecord,
    NodeCallback,
    PipelineOutcome,
    PipelineRunner,
)

logger = logging.getLogger(__name__)

# owner_subject on an in-process job is only the ADK session's user_id: it
# isolates session state and carries none of the token-subject meaning it has
# on the HTTP path. Callers embedding the engine per tenant may pass their own.
DEFAULT_CALLER = "in-process"

# Mirrors the HTTP contract's system_name bound. Optional metadata, so an
# over-long name is a caller error rather than a model-judged rejection.
MAX_SYSTEM_NAME_CHARS = 200


class EngineInputError(ValueError):
    """A submission broke the engine's input contract before any model ran."""


async def _ignore_node(node: str) -> None:
    """Default node callback: the in-process caller wants only the result."""


class StrideEngine:
    """Runs one submission through the analysis pipeline, in process.

    Build once with :meth:`from_config` and reuse: the pipeline composes its
    cacheable shared prefix at construction, so a fresh engine per call would
    pay that cost every time. Each :meth:`analyze` call is independent and
    holds no cross-call state, so one engine is safe to share across
    concurrent tasks.
    """

    def __init__(
        self,
        runner: PipelineRunner,
        *,
        max_description_bytes: int = MAX_DESCRIPTION_BYTES,
    ) -> None:
        self._runner = runner
        self._max_description_bytes = max_description_bytes

    @classmethod
    def from_config(cls, env: Mapping[str, str] | None = None) -> Self:
        """The production engine: this deployment's Markdown, config and models.

        Fails closed on missing or invalid config, exactly as the HTTP app
        does, rather than running nodes on whatever model or sampling happened
        to be default.
        """
        return cls.from_deployment(Deployment.from_env(env))

    @classmethod
    def from_deployment(cls, deployment: Deployment) -> Self:
        """An engine on an already-resolved deployment.

        For callers that need the configuration *and* the engine — the
        first-run app reports which vendor a tier selected when the credential
        check fails, and re-reading the config to find that out is how the two
        could disagree.
        """
        return cls(deployment.runner())

    async def analyze(
        self,
        description: str,
        *,
        system_name: str | None = None,
        caller: str = DEFAULT_CALLER,
        on_node: NodeCallback | None = None,
    ) -> PipelineOutcome:
        """Drive one submission to a terminal state.

        Returns a :class:`~stride_service.jobs.PipelineCompleted` carrying the
        :class:`~stride_service.report.StrideReport` when analysis succeeds, or
        a :class:`~stride_service.jobs.PipelineRejected` carrying the validity
        gate's issues when the input cannot be modelled. An internal failure
        raises — the engine never returns a partial or best-effort report.
        ``on_node``, if given, is awaited with each node name as it completes,
        for progress or tracing.

        Usage::

            outcome = await engine.analyze(text, system_name="Checkout")
            if isinstance(outcome, PipelineCompleted):
                report = outcome.report
            else:
                issues = outcome.issues
        """
        job = self._build_job(description, system_name=system_name, caller=caller)
        return await self._runner.run(job, on_node or _ignore_node)

    def analyze_sync(
        self,
        description: str,
        *,
        system_name: str | None = None,
        caller: str = DEFAULT_CALLER,
    ) -> PipelineOutcome:
        """Blocking wrapper around :meth:`analyze` for non-async callers.

        Refuses to run inside an already-running event loop, where
        ``asyncio.run`` would raise anyway — ``await analyze()`` there instead.
        """
        if _event_loop_running():
            raise RuntimeError(
                "analyze_sync cannot run inside an active event loop; "
                "await analyze() instead"
            )
        return asyncio.run(
            self.analyze(description, system_name=system_name, caller=caller)
        )

    def _build_job(
        self, description: str, *, system_name: str | None, caller: str
    ) -> JobRecord:
        if not description or not description.strip():
            raise EngineInputError("description must be non-empty")
        size = len(description.encode("utf-8"))
        if size > self._max_description_bytes:
            raise EngineInputError(
                f"description is {size} bytes, over the "
                f"{self._max_description_bytes} byte cap"
            )
        return JobRecord.create(
            owner_subject=caller,
            description=description,
            system_name=_clean_system_name(system_name),
        )


def _clean_system_name(system_name: str | None) -> str | None:
    """Empty or whitespace-only names fall through to the report default."""
    if system_name is None:
        return None
    trimmed = system_name.strip()
    if not trimmed:
        return None
    if len(trimmed) > MAX_SYSTEM_NAME_CHARS:
        raise EngineInputError(
            f"system_name exceeds {MAX_SYSTEM_NAME_CHARS} characters"
        )
    return trimmed


def _event_loop_running() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True
