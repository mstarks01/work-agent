"""The three eval modes over one corpus.

Two artifacts per case buy three modes, and the point of the split is
attribution. An end-to-end-only fixture cannot say whether a recall miss was a
category-agent failure or an element ``extract`` never produced.

* extraction — source text against the blessed model. It runs the shipped
  ``extract`` node alone, and puts its emission through the same shipped
  validity gate ``validate`` uses.
* analysis — the blessed model injected at ``prepare``, scored against the
  reference threats. The input is deterministic, so every threat number is
  attributable to the category agents and the critic.
* end-to-end — text in, report out. This is the integration smoke test.

All three drive the shipped graph through
:func:`~analysis_service.graph.build_pipeline`, and differ only in its ``entry``
and the state seeded into the session. Nothing about the topology, the prompts,
the skills, the tier config or the sampling config is eval-specific. Grading a
configuration you do not ship is the failure this whole design rejects.

Every function here needs live provider credentials, and nothing here runs in
the credential-free pull-request job. That job scores recorded output through
:mod:`evals.harness.scorer` and :mod:`evals.harness.structural`, both of which
take plain data.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from analysis_service.analysis import control_state
from analysis_service.deployment import Deployment
from analysis_service.execution import GraphExecutor, GraphRun
from analysis_service.frameworks import PACKAGES
from analysis_service.frameworks.stride.record import DraftThreat
from analysis_service.graph import (
    ENTRY_EXTRACT,
    ENTRY_EXTRACT_ONLY,
    ENTRY_PREPARE,
    STATE_EXTRACTED_MODEL,
    STATE_FRAMEWORK_OPTIONS,
    STATE_VALID_MODEL,
    Entry,
    FrameworkNodes,
    GraphProducedNothing,
    ModelResolver,
    Pipeline,
    Rejected,
    result_of,
)
from analysis_service.report import (
    Claim,
    FrameworkName,
    FrameworkSelection,
    InputRef,
    Job,
    NodeRun,
    Report,
)
from analysis_service.sampling import (
    SamplingConfig,
)
from analysis_service.sources import Source
from analysis_service.system_model import SystemModel
from analysis_service.validation import ValidationIssue, parse_and_validate
from evals.harness.reference import GoldenCase

EVAL_APP_NAME = "analysis-evals"
EVAL_USER = "eval-harness"


class EvalRunError(RuntimeError):
    """A graph run produced neither the artifact the mode wanted nor a rejection."""


@dataclass(frozen=True)
class ExtractionResult:
    """One extraction run: what came out, whether it was valid, and what ran.

    ``node_runs`` is carried even though this mode produces no report: the
    ``extract`` execution presented an execution identity like any other, and
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
    :func:`~analysis_service.graph.merge_drafts` parks them on the way into that
    framework's critic — so this costs one extra state key per framework here and
    no change to the production seam. Reading them back through **the package's
    own record** rather than passing the raw dicts on keeps every scorer typed
    against the shipped model, and revalidates on the way out of state exactly as
    :func:`~analysis_service.graph.assemble_report` does.

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
#: exact and needs no interpretation. A free-text control is not, but
#: :func:`~analysis_service.analysis.control_state` reduces it to ``unverified`` /
#: ``absent`` / ``stated`` by its leading token, and *that* is comparable. So
#: this measures the state rather than the wording, which keeps interpretation out
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


def _endpoint_key(element_id: str) -> str:
    """One element ID reduced to what identifies it structurally.

    A flow is ``flow:<source>-to-<target>:<label>`` and the label is the
    describing half, so it drops. Every other type is returned unchanged: there
    is no structural key behind an entity's or a boundary's name.
    """
    parts = element_id.split(":")
    if parts[0] == "flow" and len(parts) > 2:
        return ":".join(parts[:2])
    return element_id


def _endpoint_keys(ids: Iterable[str]) -> frozenset[str]:
    return frozenset(_endpoint_key(element_id) for element_id in ids)


@dataclass(frozen=True)
class ExtractionScore:
    """Agreement between an extraction and the blessed model.

    Purely mechanical — element IDs are typed slugs, so set arithmetic answers
    this mechanically. Element *naming* drift shows up as a miss plus a
    spurious element, which is the honest reading for a **report reader**: a
    threat filed against ``process:auth-svc`` does not resolve for someone
    holding ``process:auth-service``.

    It is the wrong reading for a question about **extraction**, and #293 is
    what showed the difference. Two models on the same corpus both missed
    ``flow:card-processor-to-storefront-api:settlement-webhook`` and both
    emitted the same endpoints under another label — ``payment-webhook`` and
    ``post-webhook``. Identical architecture, one word apart, charged once as a
    miss and again as an invention. Measured over 13 cases, folding the flow
    label alone recovers 24-39% of both.

    So ``endpoint_*`` carries the second reading beside the strict one, and
    neither replaces the other. **A flow's identity is its endpoints; its label
    is descriptive** — the same principle
    :func:`~evals.harness.identity.endpoint_form` applies to a claim, for the
    same reason. Nothing else is folded: an entity, process, store or boundary
    has no structural key behind its name, so ``entity:shopper`` against
    ``entity:shoppers`` stays a miss and a spurious element. Guessing there
    would be a fuzzy match wearing a mechanical number's clothes.

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
    #: The blessed model's pure initiators — elements that only ever start an
    #: interaction. Carried so the reading below needs no second model walk.
    blessed_initiators: tuple[str, ...] = ()
    #: The blessed crossings and the extraction's, each as endpoint-pair keys.
    #: ``extracted_crossings`` is ``None`` where derivation raised — a model
    #: whose endpoints are not zoned has said nothing, not "nothing crosses".
    blessed_crossings: tuple[str, ...] = ()
    extracted_crossings: tuple[str, ...] | None = None

    @property
    def recall(self) -> float:
        total = len(self.matched) + len(self.missing)
        return len(self.matched) / total if total else 0.0

    @property
    def precision(self) -> float:
        total = len(self.matched) + len(self.extra)
        return len(self.matched) / total if total else 0.0

    @property
    def initiators_missing(self) -> frozenset[str]:
        """Pure initiators the blessed model holds and the extraction dropped."""
        return frozenset(self.blessed_initiators) & frozenset(self.missing)

    @property
    def initiator_recall(self) -> float:
        """Of the blessed model's pure initiators, the share the extraction kept.

        Read beside the overall element recall rather than instead of it: the
        two together say whether an extraction is uniformly thin or is dropping
        one *kind* of element. A case with no pure initiator reads 0.0, for the
        reason every other empty denominator here does.
        """
        if not self.blessed_initiators:
            return 0.0
        kept = len(self.blessed_initiators) - len(self.initiators_missing)
        return kept / len(self.blessed_initiators)

    @property
    def crossings_found(self) -> frozenset[str]:
        """Blessed crossings the extraction also separated, by endpoint pair."""
        return frozenset(self.blessed_crossings) & frozenset(
            self.extracted_crossings or ()
        )

    @property
    def crossings_recall(self) -> float:
        """Of the blessed crossings, the share the extraction also derived.

        Names dropped, so this is the reading ``crossings_match`` cannot give.
        It is still bounded by what the extraction found at all: a crossing
        whose flow was never emitted cannot be separated, however it is
        compared. On the 2026-08-23 sweeps that ceiling was 40% and 55%.
        """
        if not self.blessed_crossings:
            return 0.0
        return len(self.crossings_found) / len(self.blessed_crossings)

    @property
    def crossings_derivable(self) -> bool:
        """Whether the extraction was well-formed enough to derive crossings."""
        return self.extracted_crossings is not None

    @property
    def endpoint_matched(self) -> frozenset[str]:
        """The matched set with every flow reduced to its endpoint pair."""
        return _endpoint_keys(self.matched)

    @property
    def endpoint_missing(self) -> frozenset[str]:
        """Missed under endpoint folding: not matched, and not emitted elsewhere.

        Subtracting the emitted keys is what makes this a second *reading*
        rather than a second count. A flow the model produced under another
        label is in ``extra`` strictly and in ``endpoint_matched`` here, so it
        must leave the missing set too or one flow is charged on both sides.
        """
        emitted = self.endpoint_matched | _endpoint_keys(self.extra)
        return _endpoint_keys(self.missing) - emitted

    @property
    def endpoint_extra(self) -> frozenset[str]:
        """Spurious under endpoint folding."""
        blessed = self.endpoint_matched | _endpoint_keys(self.missing)
        return _endpoint_keys(self.extra) - blessed

    @property
    def endpoint_recall(self) -> float:
        found = len(self.endpoint_matched) + len(
            _endpoint_keys(self.extra) & _endpoint_keys(self.missing)
        )
        total = found + len(self.endpoint_missing)
        return found / total if total else 0.0

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
            "endpoint_recall": round(self.endpoint_recall, 3),
            "endpoint_missing": sorted(self.endpoint_missing),
            "endpoint_extra": sorted(self.endpoint_extra),
            "initiator_recall": round(self.initiator_recall, 3),
            "initiators_missing": sorted(self.initiators_missing),
            "crossings_match": self.crossings_match,
            "crossings_recall": round(self.crossings_recall, 3),
            "crossings_derivable": self.crossings_derivable,
            "crossings_missing": sorted(
                frozenset(self.blessed_crossings) - self.crossings_found
            ),
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


def case_framework_options(case: GoldenCase) -> dict[str, dict[str, Any]]:
    """The job-level options this case declares, in the shape the graph seeds.

    **The harness is a driver, and a driver seeds these.** ``prepare_analysis``
    validates every selected framework's options against that package's own
    model and raises ``MissingFrameworkOptions`` when one is absent, because no
    package field carries a default. ``AdkPipelineRunner`` builds this map from
    the job's ``frameworks`` list; this builds the same map from the case's,
    which is where a corpus case has always declared them.

    Missing until #290, and it made ``analysis`` and ``end-to-end`` unrunnable
    for any package with a required option. It went unnoticed because STRIDE
    declares none, so the omission was invisible for as long as STRIDE was the
    only package — and ``tests/test_graph.py`` seeds ``ASVS_OPTIONS`` by hand,
    so no offline test drove the path that omits them.
    """
    return {
        declaration.name: dict(declaration.options)
        for declaration in case.meta.frameworks
    }


def select_frameworks(
    case: GoldenCase, only: Sequence[FrameworkName] = ()
) -> tuple[FrameworkName, ...]:
    """This case's frameworks, narrowed to ``only`` when a sweep asks for it.

    **A pure selection: it names no option and changes no reference set.** A
    case still declares what it is graded for and still carries the options for
    it; this only decides which of those declarations one sweep builds a graph
    for. So a narrowed sweep and a full one measure the same cases the same way
    — the narrowed one just measures fewer frameworks per case.

    Empty ``only`` means every framework the case declares, which is what every
    sweep did before this existed.

    **Why it exists is capacity, not preference.** One job fans out one
    ``strong``-tier request per lane of every framework it names, all at the
    barrier — :func:`~analysis_service.frameworks.widest_fan_out`, 23 today. On a
    200,000 token-per-minute quota that burst is over budget on a single job,
    and no per-caller ceiling helps: ``max_active_jobs`` bounds jobs, and this
    is one job. Narrowing the selection is the only lever inside the harness.

    A case that declares none of ``only`` yields an empty tuple and its caller
    skips it, which is a case the sweep did not measure rather than one that
    measured nothing.
    """
    declared = case_frameworks(case)
    if not only:
        return declared
    return tuple(name for name in declared if name in set(only))


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

    Built from a :class:`~analysis_service.deployment.Deployment`, which is the
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

    The shipped :class:`~analysis_service.execution.GraphExecutor` drives it, so
    a sweep stamps each node execution exactly as the service does — the served
    build it presented, and the execution-identity fingerprint that build is
    one of seven parts of.
    Stamping this here rather than in the harness is what makes the eval CLI a
    real second caller of :func:`~analysis_service.certification.certify` rather
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
        blessed_initiators=tuple(sorted(pure_initiators(case.model))),
        blessed_crossings=crossing_keys(case.model) or (),
        extracted_crossings=crossing_keys(result.extracted),
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

    **Compares names, and that is the reading it is for.** A
    :class:`~analysis_service.report.BoundaryCrossing` carries ``flow_id`` and two
    zones, and both zones hold a boundary *ID*. A reader of the report sees
    those strings, so a crossing naming a zone they do not hold is wrong for
    them. :func:`crossing_keys` is the other reading — see #297.
    """
    if extracted is None:
        return False
    try:
        return extracted.boundary_crossings() == blessed.boundary_crossings()
    except ValueError:
        return False


def pure_initiators(model: SystemModel) -> frozenset[str]:
    """Elements that only ever start an interaction, never receive one.

    **The failure mode this isolates.** Across five gpt-4o runs on an unchanged
    config, these were recalled at 0.421 against 0.600 for every other non-flow
    element — lower in all five, a gap of 0.179. 16 of the 19 distinct dangling
    endpoints in those runs were flow *sources*, and the repeated offenders are
    exactly these: `process:store-server`, `entity:developer`,
    `entity:duty-engineer`.

    In case 07 the extraction writes ``flow source 'process:store-server'`` —
    the exact ID the blessed model uses — and never declares the element. So
    this is neither naming drift nor a conceptual error, but an
    inventory-completeness failure at the elements a source text describes by
    what they *do* rather than by where they sit: "a developer writes a change",
    "every store server asks the deploy controller once a minute".

    Measured against total Tier 1 failures the effect is invisible — that count
    has an sd of 3.27 on an unchanged config. Measured here it is roughly five
    standard deviations of headroom, which is why this reading exists.
    """
    sources = {flow.source for flow in model.data_flows}
    destinations = {flow.destination for flow in model.data_flows}
    return frozenset(sources - destinations)


def crossing_keys(model: SystemModel | None) -> tuple[str, ...] | None:
    """Each crossing as its flow's endpoint pair, or ``None`` if underivable.

    **A crossing means "this interaction has its two endpoints in different
    zones", and that sentence contains no zone name.** Two extractions can
    partition the same elements identically and name the partitions
    differently; compared as lists of
    :class:`~analysis_service.report.BoundaryCrossing`, that reads as total
    disagreement, and on the 2026-08-23 sweeps it did — ``crossings DIFFER`` on
    13 of 13 cases for two models five times apart in price.

    So the zones drop out entirely and membership survives as *set membership*:
    a flow is in this set exactly when the model separated its endpoints. The
    flow itself is keyed by :func:`_endpoint_key`, for the reason #293 gives.

    ``None`` rather than an empty tuple where derivation raises, because a model
    whose flow endpoints are not zoned elements has not said that nothing
    crosses — it has said nothing at all, and scoring that as perfect agreement
    with an empty blessed set would reward the worst extraction in the sweep.
    """
    if model is None:
        return None
    try:
        crossings = model.boundary_crossings()
    except ValueError:
        return None
    return tuple(sorted({_endpoint_key(one.flow_id) for one in crossings}))


async def run_analysis(case: GoldenCase, pipeline: Pipeline) -> AnalysisRun:
    """Mode 2: the blessed model injected at ``prepare``.

    The seeded ``valid_model`` is the blessed one, so the category agents see exactly
    what the corpus blessed and nothing depends on that run's extraction.
    """
    graph_run = await run_graph(
        pipeline,
        case.sources,
        {
            STATE_VALID_MODEL: case.model.model_dump(mode="json"),
            STATE_FRAMEWORK_OPTIONS: case_framework_options(case),
        },
    )
    return _run_from_graph(case, graph_run, pipeline)


async def run_end_to_end(case: GoldenCase, pipeline: Pipeline) -> AnalysisRun:
    """Mode 3: text in, report out — the integration smoke test."""
    graph_run = await run_graph(
        pipeline, case.sources, {STATE_FRAMEWORK_OPTIONS: case_framework_options(case)}
    )
    return _run_from_graph(case, graph_run, pipeline)


def _run_from_graph(
    case: GoldenCase, graph_run: GraphRun, pipeline: Pipeline
) -> AnalysisRun:
    """Complete the graph's :class:`~analysis_service.graph.Analysis` into a report, as production does.

    The report is built by :meth:`~analysis_service.graph.Analysis.into_report`,
    the same method :class:`~analysis_service.pipeline.AdkPipelineRunner` calls,
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
            f"  endpoint {score.endpoint_recall:.2f}"
            f"  precision {score.precision:.2f}"
            f"  crossings {'match' if score.crossings_match else 'DIFFER'}"
            f"/{score.crossings_recall:.2f}"
            f"  attributes {agreed}/{len(score.attributes)}"
        )
    if not scores:
        return
    # Both readings, because the gap between them is the reading. A wide gap is
    # naming drift over the right architecture; a narrow one at a low number is
    # an extraction that found different things.
    strict = sum(score.recall for score in scores) / len(scores)
    endpoint = sum(score.endpoint_recall for score in scores) / len(scores)
    print(
        f"recall: {strict:.2f} strict, {endpoint:.2f} folding the flow label"
        f" — the gap is naming, not extraction (instrument, non-gating)"
    )
    initiator = sum(s.initiator_recall for s in scores) / len(scores)
    dropped = sum(len(s.initiators_missing) for s in scores)
    print(
        f"initiators: {initiator:.2f} recall, {dropped} dropped — an element the"
        f" text describes by what it does rather than where it sits"
    )
    undrivable = [s.case_id for s in scores if not s.crossings_derivable]
    crossings = sum(s.crossings_recall for s in scores) / len(scores)
    print(
        f"crossings: {sum(s.crossings_match for s in scores)}/{len(scores)} match"
        f" by name, {crossings:.2f} recall by endpoint pair"
        + (f"; underivable on {len(undrivable)}" if undrivable else "")
    )
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
