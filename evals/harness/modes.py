"""The three eval modes over one corpus (ticket 009 decision 1).

Two artifacts per case buy three modes, and the point of the split is
**attribution**: an end-to-end-only fixture cannot say whether a recall miss
was an analyst failure or an element ``extract`` never produced.

* **extraction** — source text vs. the blessed model. Runs the shipped
  ``extract`` node alone and puts its emission through the same shipped
  validity gate ``validate`` uses.
* **analysis** — the blessed model injected at ``prepare``, scored against the
  reference threats. Deterministic input, so every threat number is
  attributable to the analysts and critic.
* **end-to-end** — text in, report out. The integration smoke test.

All three drive the *shipped* graph via
:func:`~stride_service.graph.build_pipeline`, differing only in its ``entry``
and the state seeded into the session. Nothing about the topology, the prompts,
the skills, the tier config or the sampling config is eval-specific — grading a
configuration you do not ship is the failure mode this whole design rejects.

Every function here needs live Vertex credentials, and nothing here runs in the
credential-free PR job (decision 17): that job scores recorded output through
:mod:`evals.harness.scorer` and :mod:`evals.harness.structural`, both of which
take plain data.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from google.adk import Runner
from google.adk.apps import App
from google.adk.sessions import InMemorySessionService
from google.genai import types

from evals.harness.reference import GoldenCase
from stride_service.binding import build_tier_adapters, make_resolve_model
from stride_service.graph import (
    ENTRY_EXTRACT,
    ENTRY_EXTRACT_ONLY,
    ENTRY_PREPARE,
    STATE_ANALYSIS,
    STATE_EXTRACTED_MODEL,
    STATE_INPUT_TEXT,
    STATE_MERGED_DRAFTS,
    STATE_REJECTION,
    STATE_VALID_MODEL,
    Analysis,
    Entry,
    ModelResolver,
    Pipeline,
    build_pipeline,
    rejection_issues,
)
from stride_service.markdown_loader import MarkdownLoader
from stride_service.model_tiers import load_model_tiers
from stride_service.report import (
    DraftThreat,
    InputRef,
    Job,
    NodeRun,
    StrideReport,
)
from stride_service.resilience import load_resilience
from stride_service.sampling import (
    SamplingConfig,
    load_sampling,
    make_resolve_sampling,
)
from stride_service.system_model import SystemModel
from stride_service.validation import ValidationIssue, parse_and_validate

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_APP_NAME = "stride-evals"
EVAL_USER = "eval-harness"


class EvalRunError(RuntimeError):
    """A graph run produced neither the artifact the mode wanted nor a rejection."""


@dataclass(frozen=True)
class ExtractionResult:
    """One extraction run: what came out, and whether it was even valid."""

    case_id: str
    extracted: SystemModel | None
    issues: tuple[ValidationIssue, ...]


@dataclass(frozen=True)
class AnalysisRun:
    """A graph run's report, plus the draft union the critic was handed.

    The drafts are read straight off ``merged_drafts`` in the final session
    state, which is where :func:`~stride_service.graph.merge_drafts` parks them
    on the way into the critic — so critic yield (ticket 028) costs one extra
    state key here and no change to the production seam. Reading them back as
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
    resolve_model: ModelResolver | None = None,
    sampling: SamplingConfig | None = None,
) -> Pipeline:
    """The shipped graph, entered where the mode needs it.

    Repo prompts, repo skills, repo tier config, repo sampling config, repo
    resilience config: the defaults are production's — including the retry and
    per-request timeout (ticket 038), so a scheduled sweep does not die on one
    429 after hours of work — and the overrides exist for offline tests that
    bind scripted models.

    ``resolve_model`` short-circuits the tier adapters deliberately: building
    them runs the credential check, which an offline test binding scripted
    models has no credentials to pass and no provider to call.
    """
    tiers = load_model_tiers(REPO_ROOT / "config" / "model_tiers.toml")
    resilience = load_resilience(REPO_ROOT / "config" / "resilience.toml")
    sampling = sampling or load_sampling(REPO_ROOT / "config" / "sampling.toml")
    return build_pipeline(
        skill_loader=MarkdownLoader(REPO_ROOT / "skills"),
        prompt_loader=MarkdownLoader(REPO_ROOT / "prompts"),
        resolve_model=resolve_model
        or make_resolve_model(
            build_tier_adapters(tiers, sampling, resilience), tiers
        ),
        resolve_sampling=make_resolve_sampling(sampling, tiers.resolve_tier),
        tier_sampling=sampling.tiers,
        resilience=resilience,
        entry=entry,
    )


async def run_graph(
    pipeline: Pipeline, state: Mapping[str, Any], message: str
) -> dict[str, Any]:
    """Drive one graph to completion and hand back its final session state."""
    session_service = InMemorySessionService()
    runner = Runner(
        app=App(name=EVAL_APP_NAME, root_agent=pipeline.workflow),
        session_service=session_service,
    )
    session = await session_service.create_session(
        app_name=EVAL_APP_NAME, user_id=EVAL_USER, state=dict(state)
    )
    async for _event in runner.run_async(
        user_id=EVAL_USER,
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=message)]),
    ):
        pass
    final = await session_service.get_session(
        app_name=EVAL_APP_NAME, user_id=EVAL_USER, session_id=session.id
    )
    return dict(final.state) if final else {}


async def run_extraction(case: GoldenCase, pipeline: Pipeline) -> ExtractionResult:
    """Mode 1: the source text through ``extract``, and nothing else."""
    state = await run_graph(
        pipeline, {STATE_INPUT_TEXT: case.source_text}, case.source_text
    )
    if STATE_EXTRACTED_MODEL not in state:
        raise EvalRunError(f"{case.id}: extract produced no model")
    # normalize_ids mirrors the ``validate`` node (ticket 037): blessed models
    # already carry derived IDs, so scoring a candidate's raw IDs by set
    # membership would count an abbreviated slug as one missing element and one
    # extra, on a reading of the source that was correct.
    model, issues = parse_and_validate(state[STATE_EXTRACTED_MODEL], normalize_ids=True)
    return ExtractionResult(case_id=case.id, extracted=model, issues=tuple(issues))


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

    The seeded ``valid_model`` is the blessed one, so the analysts see exactly
    what the SME blessed and nothing depends on that run's extraction.
    """
    state = await run_graph(
        pipeline,
        {STATE_VALID_MODEL: case.model.model_dump(mode="json")},
        case.source_text,
    )
    return _run_from_state(case, state)


async def run_end_to_end(case: GoldenCase, pipeline: Pipeline) -> AnalysisRun:
    """Mode 3: text in, report out — the integration smoke test."""
    state = await run_graph(
        pipeline, {STATE_INPUT_TEXT: case.source_text}, case.source_text
    )
    return _run_from_state(case, state)


def _run_from_state(case: GoldenCase, state: Mapping[str, Any]) -> AnalysisRun:
    """Complete the graph's :class:`Analysis` into a report, as production does.

    The eval stamps the same job/input/node metadata
    :class:`~stride_service.pipeline.AdkPipelineRunner` stamps, because the
    Tier 1 gates check a whole ``StrideReport`` — including the
    self-containment invariants — and a stripped-down payload would test a
    shape production never emits.
    """
    if STATE_REJECTION in state:
        issues = rejection_issues(state[STATE_REJECTION])
        detail = "; ".join(f"{issue.code}: {issue.message}" for issue in issues)
        raise EvalRunError(f"{case.id}: the graph rejected the model: {detail}")
    if STATE_ANALYSIS not in state:
        raise EvalRunError(f"{case.id}: graph produced neither analysis nor rejection")
    if STATE_MERGED_DRAFTS not in state:
        raise EvalRunError(f"{case.id}: graph produced an analysis with no drafts")

    analysis = Analysis.from_state(state[STATE_ANALYSIS])
    now = datetime.now(UTC)
    report = StrideReport(
        job=Job(id=f"eval-{case.id}", created_at=now, completed_at=now),
        input=InputRef(
            system_name=case.meta.title,
            source_sha256=hashlib.sha256(
                case.source_text.encode("utf-8")
            ).hexdigest(),
        ),
        nodes=[NodeRun(node="eval", model=None, duration_ms=0)],
        system_model=analysis.system_model,
        boundary_crossings=analysis.boundary_crossings,
        threats=analysis.threats,
        rejected_threats=analysis.rejected_threats,
        summary=analysis.summary,
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
