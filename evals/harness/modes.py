"""The three eval modes over one corpus.

Two artifacts per case buy three modes, and the point of the split is
**attribution**: an end-to-end-only fixture cannot say whether a recall miss
was a category-agent failure or an element ``extract`` never produced.

* **extraction** — source text vs. the blessed model. Runs the shipped
  ``extract`` node alone and puts its emission through the same shipped
  validity gate ``validate`` uses.
* **analysis** — the blessed model injected at ``prepare``, scored against the
  reference threats. Deterministic input, so every threat number is
  attributable to the category agents and critic.
* **end-to-end** — text in, report out. The integration smoke test.

All three drive the *shipped* graph via
:func:`~stride_service.graph.build_pipeline`, differing only in its ``entry``
and the state seeded into the session. Nothing about the topology, the prompts,
the skills, the tier config or the sampling config is eval-specific — grading a
configuration you do not ship is the failure mode this whole design rejects.

Every function here needs live provider credentials, and nothing here runs in
the credential-free PR job: that job scores recorded output through
:mod:`evals.harness.scorer` and :mod:`evals.harness.structural`, both of which
take plain data.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from evals.harness.reference import GoldenCase
from stride_service.deployment import Deployment
from stride_service.execution import GraphExecutor, GraphRun
from stride_service.graph import (
    ENTRY_EXTRACT,
    ENTRY_EXTRACT_ONLY,
    ENTRY_PREPARE,
    STATE_EXTRACTED_MODEL,
    STATE_MERGED_DRAFTS,
    STATE_VALID_MODEL,
    Entry,
    GraphProducedNothing,
    ModelResolver,
    Pipeline,
    Rejected,
    result_of,
)
from stride_service.report import (
    DraftThreat,
    InputRef,
    Job,
    NodeRun,
    StrideReport,
)
from stride_service.sampling import (
    SamplingConfig,
)
from stride_service.sources import Source
from stride_service.system_model import SystemModel
from stride_service.validation import ValidationIssue, parse_and_validate

EVAL_APP_NAME = "stride-evals"
EVAL_USER = "eval-harness"


class EvalRunError(RuntimeError):
    """A graph run produced neither the artifact the mode wanted nor a rejection."""


@dataclass(frozen=True)
class ExtractionResult:
    """One extraction run: what came out, whether it was valid, and what ran.

    ``node_runs`` is carried even though this mode produces no report: the
    ``extract`` execution presented a generation identity like any other, and
    sourcing observations from the report would make exactly the tier this mode
    exercises the one tier it could never certify.
    """

    case_id: str
    extracted: SystemModel | None
    issues: tuple[ValidationIssue, ...]
    node_runs: tuple[NodeRun, ...] = ()


@dataclass(frozen=True)
class AnalysisRun:
    """A graph run's report, plus the draft union the critic was handed.

    The drafts are read straight off ``merged_drafts`` in the final session
    state, which is where :func:`~stride_service.graph.merge_drafts` parks them
    on the way into the critic — so critic yield costs one extra state key here
    and no change to the production seam. Reading them back as
    :class:`DraftThreat` rather than passing the raw dicts on keeps the scorer
    typed against the shipped model, and revalidates on the way out of state
    exactly as :func:`~stride_service.graph.assemble_report` does.
    """

    report: StrideReport
    merged_drafts: tuple[DraftThreat, ...]


@dataclass(frozen=True)
class ExtractionScore:
    """Element-level agreement between an extraction and the blessed model.

    Purely mechanical — element IDs are typed slugs, so set arithmetic answers
    this without a judge. Element *naming* drift shows up as a miss plus a
    spurious element, which is the honest reading: a threat filed against
    ``process:auth-svc`` does not resolve for a reader holding
    ``process:auth-service``.
    """

    case_id: str
    matched: tuple[str, ...]
    missing: tuple[str, ...]
    extra: tuple[str, ...]
    crossings_match: bool

    @property
    def recall(self) -> float:
        total = len(self.matched) + len(self.missing)
        return len(self.matched) / total if total else 0.0

    @property
    def precision(self) -> float:
        total = len(self.matched) + len(self.extra)
        return len(self.matched) / total if total else 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            "case": self.case_id,
            "recall": round(self.recall, 3),
            "precision": round(self.precision, 3),
            "crossings_match": self.crossings_match,
            "missing": list(self.missing),
            "extra": list(self.extra),
        }


def build_eval_pipeline(
    entry: Entry,
    *,
    deployment: Deployment | None = None,
    resolve_model: ModelResolver | None = None,
    sampling: SamplingConfig | None = None,
) -> Pipeline:
    """The shipped graph, entered where the mode needs it.

    Built from a :class:`~stride_service.deployment.Deployment`, which is the
    same thing the service is built from — including the retry and per-request
    timeout, so a scheduled sweep does not die on one 429 after hours of work.
    Locating config through the deployment rather than the repo root is what
    makes "eval and production read from the same place" true rather than
    aspirational: a deployment that redirects a path has its sweeps grading the
    configuration it actually runs.

    ``resolve_model`` short-circuits the tier adapters deliberately: building
    them runs the credential check, which an offline test binding scripted
    models has no credentials to pass and no provider to call. ``sampling``
    overrides the deployment's for a sweep varying the per-tier params.
    """
    deployment = deployment or Deployment.from_env()
    if sampling is not None:
        deployment = replace(deployment, sampling=sampling, _built={})
    return deployment.pipeline(entry=entry, resolve_model=resolve_model)


async def run_graph(
    pipeline: Pipeline,
    sources: Sequence[Source],
    extra_state: Mapping[str, Any] | None = None,
) -> GraphRun:
    """Drive one graph to completion and hand back the Graph Run.

    The shipped :class:`~stride_service.execution.GraphExecutor` drives it, so
    a sweep stamps each node execution exactly as the service does — the served
    build it presented and the generation-identity fingerprint that implies.
    Stamping this here rather than in the harness is what makes the eval CLI a
    real second caller of :func:`~stride_service.certification.certify` rather
    than one certifying an empty observation set.
    """
    executor = GraphExecutor(pipeline, app_name=EVAL_APP_NAME)
    return await executor.run(sources, user_id=EVAL_USER, extra_state=extra_state)


async def run_extraction(case: GoldenCase, pipeline: Pipeline) -> ExtractionResult:
    """Mode 1: the source text through ``extract``, and nothing else."""
    graph_run = await run_graph(pipeline, case.sources)
    state = graph_run.final_state
    if STATE_EXTRACTED_MODEL not in state:
        raise EvalRunError(f"{case.id}: extract produced no model")
    # normalize_ids mirrors the ``validate`` node: blessed models already carry
    # derived IDs, so scoring a candidate's raw IDs by set membership would
    # count an abbreviated slug as one missing element and one extra, on a
    # reading of the source that was correct.
    model, issues = parse_and_validate(state[STATE_EXTRACTED_MODEL], normalize_ids=True)
    return ExtractionResult(
        case_id=case.id,
        extracted=model,
        issues=tuple(issues),
        node_runs=tuple(graph_run.node_runs),
    )


def score_extraction(case: GoldenCase, result: ExtractionResult) -> ExtractionScore:
    """Compare an extraction to the blessed model, mechanically."""
    blessed_ids = {element.id for element in case.model.elements()}
    extracted_ids = (
        {element.id for element in result.extracted.elements()}
        if result.extracted
        else set()
    )
    crossings_match = _crossings_match(case.model, result.extracted)
    return ExtractionScore(
        case_id=case.id,
        matched=tuple(sorted(blessed_ids & extracted_ids)),
        missing=tuple(sorted(blessed_ids - extracted_ids)),
        extra=tuple(sorted(extracted_ids - blessed_ids)),
        crossings_match=crossings_match,
    )


def _crossings_match(blessed: SystemModel, extracted: SystemModel | None) -> bool:
    """Whether the extraction derives the blessed crossings.

    Derivation fails closed on a model whose flow endpoints are not zoned
    elements, which is precisely the kind of extraction this mode exists to
    catch — so a model that cannot derive crossings scores as disagreeing
    rather than crashing the sweep.
    """
    if extracted is None:
        return False
    try:
        return extracted.boundary_crossings() == blessed.boundary_crossings()
    except ValueError:
        return False


async def run_analysis(case: GoldenCase, pipeline: Pipeline) -> AnalysisRun:
    """Mode 2: the blessed model injected at ``prepare``.

    The seeded ``valid_model`` is the blessed one, so the category agents see exactly
    what the SME blessed and nothing depends on that run's extraction.
    """
    graph_run = await run_graph(
        pipeline,
        case.sources,
        {STATE_VALID_MODEL: case.model.model_dump(mode="json")},
    )
    return _run_from_graph(case, graph_run, pipeline)


async def run_end_to_end(case: GoldenCase, pipeline: Pipeline) -> AnalysisRun:
    """Mode 3: text in, report out — the integration smoke test."""
    graph_run = await run_graph(pipeline, case.sources)
    return _run_from_graph(case, graph_run, pipeline)


def _run_from_graph(
    case: GoldenCase, graph_run: GraphRun, pipeline: Pipeline
) -> AnalysisRun:
    """Complete the graph's :class:`Analysis` into a report, as production does.

    The report is built by :meth:`~stride_service.graph.Analysis.into_report`,
    the same method :class:`~stride_service.pipeline.AdkPipelineRunner` calls,
    so a sweep's reports carry every block a job's do. The Tier 1 gates check a
    whole ``StrideReport`` — including the self-containment invariants — and a
    stripped-down payload would test a shape production never emits.

    What the eval supplies is what only this driver knows: a case-derived job
    identity and input reference. The node runs and the per-tier sampling clear
    block ride along with the graph run and the pipeline, without which a
    sweep's reports would carry no fingerprints and its certification verdict
    would be computed over nothing.
    """
    state = graph_run.final_state
    try:
        result = result_of(state)
    except GraphProducedNothing as exc:
        raise EvalRunError(f"{case.id}: {exc}") from exc
    if isinstance(result, Rejected):
        detail = "; ".join(f"{issue.code}: {issue.message}" for issue in result.issues)
        raise EvalRunError(f"{case.id}: the graph rejected the model: {detail}")
    if STATE_MERGED_DRAFTS not in state:
        raise EvalRunError(f"{case.id}: graph produced an analysis with no drafts")

    now = datetime.now(UTC)
    report = result.into_report(
        job=Job(id=f"eval-{case.id}", created_at=now, completed_at=now),
        input_ref=InputRef.of(system_name=case.meta.title, sources=case.sources),
        nodes=graph_run.node_runs,
        pipeline=pipeline,
    )
    drafts = tuple(
        DraftThreat.model_validate(draft) for draft in state[STATE_MERGED_DRAFTS]
    )
    return AnalysisRun(report=report, merged_drafts=drafts)


MODE_ENTRIES: dict[str, Entry] = {
    "extraction": ENTRY_EXTRACT_ONLY,
    "analysis": ENTRY_PREPARE,
    "end-to-end": ENTRY_EXTRACT,
}
