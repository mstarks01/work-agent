"""In-process entry point for the analysis pipeline: text in, report out.

The HTTP ``/v1`` API (:mod:`analysis_service.api`) is one caller of the analysis
pipeline, and this module is the other. An application that wants to run an
analysis in process, and to swap this pipeline in behind its own analysis-engine
interface, reaches for :class:`Engine`. The alternative is to fabricate a
:class:`~analysis_service.jobs.JobRecord`, an auth subject and a node callback by
hand.

The engine carries none of the HTTP contract's ceremony. There is no bearer
token, no job store and no polling. It builds the pipeline once, so the
expensive shared-prefix composition is amortised across jobs, and it runs each
submission to a terminal state. Success returns a
:class:`~analysis_service.report.Report`. An input the validity gate rejects
returns its :class:`~analysis_service.validation.ValidationIssue`s. An internal
failure raises. Those three outcomes are the job lifecycle's ``completed``,
``rejected`` and ``failed`` with the transport removed.

An engine is built for one framework selection, because a graph is. The
selection is stated once, at construction, rather than per call. Building it per
call would compose a graph per call, and throw away the whole reason this class
exists. An embedder that runs two selections holds two engines, which is the
in-process spelling of what the HTTP path does per submission.

The submitted sources are untrusted text (OWASP LLM01). The engine bounds them
and hands them to the pipeline, which places them in session state as fenced
data for the extraction prompt, and never concatenates them into an instruction.
The engine adds no new trust surface. It re-asserts, for the in-process path,
the bounds the HTTP layer already enforces, from the same deployment config and
in the same order: what one submission may carry, and how long one run may take.
Both halves matter, and only the first used to be here. ``execute_job`` bounded
the job route's duration while :meth:`Engine.analyze` handed straight to the
runner, so the first-run app and every library embedder ran with no time budget
at all. That is the state ``job_deadline_ms`` exists to end.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping, Sequence
from typing import Self

from pydantic import ValidationError

from analysis_service.deployment import Deployment
from analysis_service.frameworks import package_for
from analysis_service.jobs import (
    JobRecord,
    NodeCallback,
    PipelineOutcome,
    PipelineRunner,
)
from analysis_service.report import FrameworkName, FrameworkSelection
from analysis_service.sources import Source, SourceLimits

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


class EngineDeadlineError(TimeoutError):
    """A run was stopped because it exceeded this deployment's time budget.

    A :class:`TimeoutError` subclass so a caller that already handles the
    asyncio spelling keeps working, and a named type so one that wants to tell
    "this deployment stopped it" apart from "something else timed out" can.

    A partial run is never a partial report: nothing is returned on this path,
    for the reason :func:`~analysis_service.jobs.execute_job` gives — every lane of
    every selected framework is what makes the output that method's answer, and
    one that stopped halfway is a different method rather than a shorter answer.
    """


async def _ignore_node(node: str) -> None:
    """Default node callback: the in-process caller wants only the result."""


class Engine:
    """Runs one submission through the analysis pipeline, in process.

    Build once with :meth:`from_config`, naming the frameworks to analyse under,
    and reuse: the pipeline composes its cacheable shared prefix at
    construction, so a fresh engine per call would pay that cost every time.
    Each :meth:`analyze` call is independent and holds no cross-call state, so
    one engine is safe to share across concurrent tasks.
    """

    def __init__(
        self,
        runner: PipelineRunner,
        *,
        limits: SourceLimits,
        deadline_seconds: float,
        frameworks: Sequence[FrameworkSelection],
    ) -> None:
        if not frameworks:
            raise EngineInputError("an engine must be built for at least one framework")
        # The same rung the HTTP route applies, applied to every other caller.
        # A package's options model declares no default, so a selection missing a
        # value it needs is refused here rather than reaching a block that cannot
        # be built — which would fail after every node had been paid for.
        for selection in frameworks:
            try:
                package_for(selection.name).options.model_validate(selection.options)
            except ValidationError as exc:
                raise EngineInputError(
                    f"options for framework {selection.name!r} are invalid: {exc}"
                ) from exc
        self._runner = runner
        self._limits = limits
        self._deadline_seconds = deadline_seconds
        self._frameworks = list(frameworks)

    @classmethod
    def from_config(
        cls,
        frameworks: Sequence[FrameworkName | FrameworkSelection],
        env: Mapping[str, str] | None = None,
    ) -> Self:
        """The production engine: this deployment's Markdown, config and models.

        Fails closed on missing or invalid config, exactly as the HTTP app
        does, rather than running nodes on whatever model or sampling happened
        to be default — and on a framework this install does not carry, before
        any adapter binds.
        """
        return cls.from_deployment(Deployment.from_env(env), frameworks)

    @classmethod
    def from_deployment(
        cls,
        deployment: Deployment,
        frameworks: Sequence[FrameworkName | FrameworkSelection],
    ) -> Self:
        """An engine on an already-resolved deployment, for one selection.

        For callers that need the configuration *and* the engine — the
        first-run app reports which vendor a tier selected when the credential
        check fails, and re-reading the config to find that out is how the two
        could disagree.

        ``frameworks`` takes bare names or full
        :class:`~analysis_service.report.FrameworkSelection`\\ s. A bare name is
        the selection with no options, spelled the short way; it is not a
        default, because there is still no way to leave the argument out.
        """
        selections = [
            entry
            if isinstance(entry, FrameworkSelection)
            else FrameworkSelection(name=entry)
            for entry in frameworks
        ]
        return cls(
            deployment.runner([selection.name for selection in selections]),
            limits=deployment.resilience.source_limits(),
            deadline_seconds=deployment.resilience.deadline_seconds(),
            frameworks=selections,
        )

    async def analyze(
        self,
        sources: Sequence[Source],
        *,
        system_name: str | None = None,
        caller: str = DEFAULT_CALLER,
        on_node: NodeCallback | None = None,
    ) -> PipelineOutcome:
        """Drive one submission to a terminal state.

        ``sources`` is an ordered, non-empty sequence of
        :class:`~analysis_service.sources.Source`. Order is presentation only:
        nothing here or downstream reads an earlier source as outranking a
        later one.

        Returns a :class:`~analysis_service.jobs.PipelineCompleted` carrying the
        :class:`~analysis_service.report.Report` when analysis succeeds, or
        a :class:`~analysis_service.jobs.PipelineRejected` carrying the validity
        gate's issues when the input cannot be modelled. An internal failure
        raises — the engine never returns a partial or best-effort report.
        ``on_node``, if given, is awaited with each node name as it completes,
        for progress or tracing.

        The run is bounded by this deployment's ``job_deadline_ms``, and
        expiry raises :class:`EngineDeadlineError`. The bound belongs here
        rather than to each caller for the reason
        :mod:`analysis_service.resilience` gives: ``timeout_ms`` bounds one HTTP
        request, ``attempts`` multiplies it, the retry budget multiplies it
        again, and five LLM stages run in series on the graph's longest path, so
        the per-call knobs compose to a worst case in the hours while each one
        is individually respected. A provider ``Retry-After`` is deliberately
        uncapped (:mod:`analysis_service.retry`) precisely because a deadline
        above it is what bounds the wait — without one here, an in-process run
        had no bound at all.

        Usage::

            engine = Engine.from_config(["stride"])
            outcome = await engine.analyze(
                [Source.description(text)], system_name="Checkout"
            )
            if isinstance(outcome, PipelineCompleted):
                report = outcome.report
            else:
                issues = outcome.issues
        """
        job = self._build_job(sources, system_name=system_name, caller=caller)
        started = time.monotonic()
        try:
            async with asyncio.timeout(self._deadline_seconds) as bound:
                return await self._runner.run(job, on_node or _ignore_node)
        except TimeoutError as exc:
            # Only *this* bound expiring is a deadline. A TimeoutError raised
            # inside the graph is somebody else's timeout and keeps its own
            # identity rather than being relabelled as the job's budget.
            if not bound.expired():
                raise
            # Node names go to the log rather than into the message, matching
            # ``execute_job``: a deadline that keeps firing at the same node is
            # what sizes ``timeout_ms``, and it is the operator who needs it.
            logger.error(
                "in-process run exceeded the %.0fs deadline after %.1fs",
                self._deadline_seconds,
                time.monotonic() - started,
            )
            raise EngineDeadlineError(
                "the analysis exceeded this deployment's time budget"
                f" of {self._deadline_seconds:.0f}s and was stopped"
            ) from exc

    def analyze_sync(
        self,
        sources: Sequence[Source],
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
            self.analyze(sources, system_name=system_name, caller=caller)
        )

    def _build_job(
        self, sources: Sequence[Source], *, system_name: str | None, caller: str
    ) -> JobRecord:
        """Shape a submission into a job, or refuse it before any model runs.

        Per-source well-formedness is already enforced: constructing a
        :class:`~analysis_service.sources.Source` is what validates a kind, a
        label and non-empty text, so by the time one arrives here the only
        questions left are about the list as a whole.
        """
        if isinstance(sources, str | bytes):
            # A string satisfies Sequence, so the removed contract's
            # ``analyze(text)`` would otherwise iterate characters and report
            # a nonsense source count. This is the call an integrator port
            # makes first, so it says what to write instead.
            raise EngineInputError(
                "analyze takes a sequence of Source, not a string; "
                "pass [Source.description(text)] or [Source.transcript(text)]"
            )
        breach = self._limits.breach(sources)
        if breach is not None:
            raise EngineInputError(breach.message)
        return JobRecord.create(
            owner_subject=caller,
            sources=sources,
            frameworks=self._frameworks,
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
