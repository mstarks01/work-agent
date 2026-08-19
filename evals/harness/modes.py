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

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from evals.harness.reference import GoldenCase
from stride_service.analysis import control_state
from stride_service.deployment import Deployment
from stride_service.execution import GraphExecutor, GraphRun
from stride_service.frameworks import PACKAGES
from stride_service.frameworks.stride.record import DraftThreat
from stride_service.graph import (
    ENTRY_EXTRACT,
    ENTRY_EXTRACT_ONLY,
    ENTRY_PREPARE,
    STATE_EXTRACTED_MODEL,
    STATE_VALID_MODEL,
    Entry,
    FrameworkNodes,
    GraphProducedNothing,
    ModelResolver,
    Pipeline,
    Rejected,
    result_of,
)
from stride_service.report import (
    Claim,
    FrameworkName,
    FrameworkSelection,
    InputRef,
    Job,
    NodeRun,
    Report,
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
    """A graph run's report, plus the draft union each critic was handed.

    The drafts are read straight off each framework's ``merged_drafts`` in the
    final session state, which is where
    :func:`~stride_service.graph.merge_drafts` parks them on the way into that
    framework's critic — so this costs one extra state key per framework here and
    no change to the production seam. Reading them back through **the package's
    own record** rather than passing the raw dicts on keeps every scorer typed
    against the shipped model, and revalidates on the way out of state exactly as
    :func:`~stride_service.graph.assemble_report` does.

    ``drafts`` is keyed by framework because the drafts are: two frameworks'
    subgraphs never touch, each has its own fan-in and its own critic, and a
    pooled draft list would ask one scorer to read another package's record.
    ``merged_drafts`` stays as STRIDE's, because the scorer and the critic-yield
    instrument both read a :class:`DraftThreat`'s ``category`` and ``severity``,
    which only that record carries.
    """

    report: Report
    drafts: Mapping[FrameworkName, tuple[Claim, ...]]

    @property
    def merged_drafts(self) -> tuple[DraftThreat, ...]:
        """STRIDE's half, at the record its own scorers are typed against."""
        return tuple(
            draft
            for draft in self.drafts.get("stride", ())
            if isinstance(draft, DraftThreat)
        )


def _tags(value: list[str]) -> str:
    """One element's asset tags as a comparable string, order removed."""
    return ", ".join(sorted(value))


#: The attributes an extraction is measured on, each with the function that
#: reduces it to a comparable value. Declaration order, because the per-sweep
#: aggregate prints in it.
#:
#: **Two kinds of attribute, and nothing else.** A closed vocabulary — ``kind``,
#: ``exposure``, the asset tags — is set arithmetic against the blessed model,
#: exact and judge-free. A free-text control is not, but
#: :func:`~stride_service.analysis.control_state` reduces it to ``unverified`` /
#: ``absent`` / ``stated`` by its leading token, and *that* is comparable. So
#: this measures the state rather than the wording, which keeps the judge out
#: and still catches the corpus's most repeated extraction failure: a control
#: invented where the blessed model says ``unknown``.
#:
#: What is deliberately absent is every other free-text attribute —
#: ``technology``, ``protocol``, ``data_description``. Two correct readings of
#: one sentence word them differently, so an exact test on them reports
#: disagreement that is not there. ``trust_zone`` is absent for the opposite
#: reason: :attr:`ExtractionScore.crossings_match` already reads it, derived
#: rather than compared string by string.
_SCORED_ATTRIBUTES: Mapping[str, Callable[[Any], str]] = {
    "kind": str,
    "exposure": str,
    "assets": _tags,
    "authentication": control_state,
    "encryption_in_transit": control_state,
    "encryption_at_rest": control_state,
    "data_classification": control_state,
}


@dataclass(frozen=True)
class AttributeCheck:
    """One attribute of one element, as each model states it.

    ``blessed`` and ``extracted`` hold the *reduced* values that
    :data:`_SCORED_ATTRIBUTES` compared, never the raw text: a report saying
    ``unverified -> stated`` names the failure, where the two sentences behind
    it would only name the wording.
    """

    element_id: str
    attribute: str
    blessed: str
    extracted: str

    @property
    def agrees(self) -> bool:
        return self.blessed == self.extracted

    @property
    def key(self) -> str:
        """What this check is about, as ``<element type>.<attribute>``.

        The type is carried because ``kind`` names two different closed
        vocabularies — an entity's ``human``/``external-system`` and a
        boundary's four — and a sweep-wide split that pooled them would report
        a drift without saying which one drifted. The type is read off the ID's
        prefix, which is where an element ID always carries it.
        """
        return f"{self.element_id.split(':', 1)[0]}.{self.attribute}"

    def to_json(self) -> dict[str, str]:
        return {
            "element": self.element_id,
            "attribute": self.attribute,
            "blessed": self.blessed,
            "extracted": self.extracted,
        }


@dataclass(frozen=True)
class ExtractionScore:
    """Agreement between an extraction and the blessed model.

    Purely mechanical — element IDs are typed slugs, so set arithmetic answers
    this without a judge. Element *naming* drift shows up as a miss plus a
    spurious element, which is the honest reading: a threat filed against
    ``process:auth-svc`` does not resolve for a reader holding
    ``process:auth-service``.

    ``attributes`` carries the second half, on the elements both models hold:
    the values a Candidate rule reads. Without it an extraction that types
    every Trust Boundary ``network`` scores exactly like one that picks
    ``privilege`` and ``tenant`` correctly, and a value the live pipeline
    stopped producing leaves every number here flat
    ([#195](https://github.com/mstarks01/work-agent/issues/195)). It is
    **reported, never gated**: it carries no threshold, because a low number is
    not a defect on its own, and adding a scored metric would move every
    baseline this repo tracks.
    """

    case_id: str
    matched: tuple[str, ...]
    missing: tuple[str, ...]
    extra: tuple[str, ...]
    crossings_match: bool
    attributes: tuple[AttributeCheck, ...]

    @property
    def recall(self) -> float:
        total = len(self.matched) + len(self.missing)
        return len(self.matched) / total if total else 0.0

    @property
    def precision(self) -> float:
        total = len(self.matched) + len(self.extra)
        return len(self.matched) / total if total else 0.0

    @property
    def differing(self) -> tuple[AttributeCheck, ...]:
        """The checks the two models answered differently, in model order."""
        return tuple(check for check in self.attributes if not check.agrees)

    @property
    def attribute_agreement(self) -> float:
        agreed = len(self.attributes) - len(self.differing)
        return agreed / len(self.attributes) if self.attributes else 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            "case": self.case_id,
            "recall": round(self.recall, 3),
            "precision": round(self.precision, 3),
            "crossings_match": self.crossings_match,
            "missing": list(self.missing),
            "extra": list(self.extra),
            "attribute_agreement": round(self.attribute_agreement, 3),
            "attributes_compared": len(self.attributes),
            # The disagreements alone. An agreeing check is a number, and the
            # count above carries it; writing all of them out would bury the
            # few lines a reader opens this file for.
            "attributes_differing": [check.to_json() for check in self.differing],
        }


def aggregate_attributes(scores: Sequence[ExtractionScore]) -> dict[str, Any]:
    """The whole sweep's attribute agreement, and the same split per attribute.

    Both, because they answer different questions. The total says whether
    anything drifted; the split says *which value stopped arriving*, which is
    the question [#184](https://github.com/mstarks01/work-agent/issues/184)
    needed a hand-run count of candidates by lane to answer.
    """
    checks = [check for score in scores for check in score.attributes]
    compared = Counter(check.key for check in checks)
    agreed = Counter(check.key for check in checks if check.agrees)
    order = list(_SCORED_ATTRIBUTES)
    keys = sorted(compared, key=lambda key: (order.index(key.split(".", 1)[1]), key))
    return {
        "compared": len(checks),
        "agreed": sum(agreed.values()),
        "agreement": round(sum(agreed.values()) / len(checks), 3) if checks else 0.0,
        "by_attribute": {
            key: {
                "compared": compared[key],
                "agreed": agreed[key],
                "agreement": round(agreed[key] / compared[key], 3),
            }
            for key in keys
        },
    }


#: The frameworks a sweep runs for a case that declares none, and the fallback
#: for a caller that names no case. A sweep grades a framework's own claim set
#: (#167), so which frameworks ran is a property of the *case* rather than of the
#: harness — see :func:`case_frameworks`, which is what the sweep actually reads.
EVAL_FRAMEWORKS: tuple[FrameworkName, ...] = ("stride",)


def case_frameworks(case: GoldenCase) -> tuple[FrameworkName, ...]:
    """The frameworks to build this case's graph for: the ones it declares.

    Read off ``case.json`` rather than fixed for the sweep. A case declares a
    framework when that framework's **Precondition** allows one and it carries a
    reference set, so running the declaration is what makes "every record the
    corpus holds is graded" true — and what stops a sweep paying for ASVS's 17
    ``strong``-tier lanes on a case that has nothing to score them against.

    A case declaring nothing falls back to :data:`EVAL_FRAMEWORKS`, which keeps
    a hand-built fixture case runnable without a frameworks array.
    """
    declared = tuple(declaration.name for declaration in case.meta.frameworks)
    return declared or EVAL_FRAMEWORKS


def case_selections(case: GoldenCase, pipeline: Pipeline) -> list[FrameworkSelection]:
    """The job selection, taken from the built graph and dressed in the case's options.

    **The names come from the pipeline, never from the case.** The envelope
    checks that a report's blocks answer the job's own selection, so a driver
    that named the case's declaration while the graph ran something else would
    fail that check rather than record what happened — which is exactly what a
    test binding a STRIDE-only pipeline to a case declaring two frameworks does.

    The *options* come from the case, because they are load-bearing and the
    graph does not carry them: an **ASVS Level** decides which requirements a run
    rules on, so a job that omitted it is rejected on the input ladder and one
    that guessed it grades against the wrong slice of the catalog.
    """
    options = {
        declaration.name: dict(declaration.options)
        for declaration in case.meta.frameworks
    }
    return [
        FrameworkSelection(name=name, options=options.get(name, {}))
        for name in pipeline.frameworks
    ]


def build_eval_pipeline(
    entry: Entry,
    *,
    deployment: Deployment | None = None,
    resolve_model: ModelResolver | None = None,
    sampling: SamplingConfig | None = None,
    frameworks: Sequence[FrameworkName] = EVAL_FRAMEWORKS,
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
    return deployment.pipeline(frameworks, entry=entry, resolve_model=resolve_model)


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
        attributes=_check_attributes(case.model, result.extracted),
    )


def _check_attributes(
    blessed: SystemModel, extracted: SystemModel | None
) -> tuple[AttributeCheck, ...]:
    """Compare every scored attribute of the elements both models carry.

    Matched elements only. An attribute of a missing element is already
    counted, as the miss, and reading it a second time here would charge one
    dropped element twice.

    A matched ID implies a matched type — an element ID leads with its type
    prefix — so the attributes one side declares are the attributes the other
    declares, and the walk needs no per-type branch.
    """
    if extracted is None:
        return ()
    counterparts = {element.id: element for element in extracted.elements()}
    checks = []
    for element in blessed.elements():
        counterpart = counterparts.get(element.id)
        if counterpart is None:
            continue
        for attribute, reduce_value in _SCORED_ATTRIBUTES.items():
            if attribute not in type(element).model_fields:
                continue
            checks.append(
                AttributeCheck(
                    element_id=element.id,
                    attribute=attribute,
                    blessed=reduce_value(getattr(element, attribute)),
                    extracted=reduce_value(getattr(counterpart, attribute)),
                )
            )
    return tuple(checks)


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
    what the corpus blessed and nothing depends on that run's extraction.
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
    whole ``Report`` — including the self-containment invariants — and a
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
    # Every framework the graph ran, each through its own state key. Grading is
    # per framework (#167), so a scorer reads its own package's drafts against
    # its own reference set and two packages' records never meet.
    drafts: dict[FrameworkName, tuple[Claim, ...]] = {}
    for name in pipeline.frameworks:
        key = FrameworkNodes(name).key("drafts")
        if key not in state:
            raise EvalRunError(
                f"{case.id}: graph produced a {name} analysis with no drafts"
            )
        record = PACKAGES[name].record
        drafts[name] = tuple(record.model_validate(draft) for draft in state[key])

    now = datetime.now(UTC)
    report = result.into_report(
        job=Job(
            id=f"eval-{case.id}",
            created_at=now,
            completed_at=now,
            # Read off the built graph rather than restated, so the blocks
            # answer the job's own selection exactly as the envelope requires;
            # the case supplies only the options the graph does not carry.
            frameworks=case_selections(case, pipeline),
        ),
        input_ref=InputRef.of(system_name=case.meta.title, sources=case.sources),
        nodes=graph_run.node_runs,
        pipeline=pipeline,
    )
    return AnalysisRun(report=report, drafts=drafts)


MODE_ENTRIES: dict[str, Entry] = {
    "extraction": ENTRY_EXTRACT_ONLY,
    "analysis": ENTRY_PREPARE,
    "end-to-end": ENTRY_EXTRACT,
}

#: The modes whose graph ends in a :class:`Report`. ``extraction`` stops at the
#: validity gate and returns an :class:`ExtractionResult`, so a sweep of it has
#: no report to persist and says so rather than writing an empty file
#: ([#180](https://github.com/mstarks01/work-agent/issues/180)).
REPORTING_MODES: frozenset[str] = frozenset({"analysis", "end-to-end"})


def render_extraction(scores: Sequence[ExtractionScore]) -> None:
    """What an extraction sweep found, per case and then per attribute.

    The per-attribute split is the line this instrument exists for. An element
    recall of 1.00 says the extraction named the right things; it says nothing
    about whether it typed them, and a rule reads the type. So a sweep whose
    ``boundary.kind`` row reads 40% has found a real regression behind two
    perfect element numbers.

    Every number here is **non-gating**. A low agreement is a question to take
    to the source text, not a defect on its own
    ([#179](https://github.com/mstarks01/work-agent/issues/179)).
    """
    for score in scores:
        agreed = len(score.attributes) - len(score.differing)
        print(
            f"{score.case_id:<26} extraction recall {score.recall:.2f}"
            f"  precision {score.precision:.2f}"
            f"  crossings {'match' if score.crossings_match else 'DIFFER'}"
            f"  attributes {agreed}/{len(score.attributes)}"
        )
    if not scores:
        return
    totals = aggregate_attributes(scores)
    print(
        f"attributes: {totals['agreed']}/{totals['compared']} agree"
        f" ({totals['agreement']:.0%}) (instrument, non-gating)"
    )
    for name, split in totals["by_attribute"].items():
        print(
            f"  {name:28} {split['agreed']:5,}/{split['compared']:<7,}"
            f" {split['agreement']:.0%}"
        )


def artifact_extraction(scores: Sequence[ExtractionScore]) -> dict[str, Any]:
    """This instrument's artifact key.

    The per-case attribute numbers ride in ``mode_output`` beside the element
    ones; this is the sweep-wide fold, which is where a value the pipeline
    stopped producing shows up as a column rather than as one line per case
    (#195). ``None`` outside the extraction mode, so an unmeasured attribute set
    never reads as a fully agreeing one.
    """
    return {"attribute_aggregate": aggregate_attributes(scores) if scores else None}
