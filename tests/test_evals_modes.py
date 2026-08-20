"""The three eval modes, driven offline against scripted models.

No Vertex endpoint is involved: ``build_pipeline`` takes the model resolver, so
each LLM node is bound to a fake replaying a canned emission. What is under
test is that the *shipped* graph runs from each mode's entry point and yields
the artifact that mode scores, including the analysis mode's blessed-model
injection at ``prepare``.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import fields
from pathlib import Path

import pytest
from google.adk.models.base_llm import BaseLlm
from pydantic import Field

from evals.harness import modes
from evals.harness.reference import load_case
from evals.harness.structural import report_issues
from stride_service.certification import fingerprints_of
from stride_service.evidence import evidence_catalog
from stride_service.frameworks.stride.record import (
    CATEGORY_LETTERS,
    STRIDE_CATEGORIES,
)
from stride_service.graph import (
    ENTRY_EXTRACT,
    ENTRY_EXTRACT_ONLY,
    ENTRY_PREPARE,
    EXTRACT_NODE,
    Analysis,
    analyze_node_name,
    tier_node_by_graph_node,
)
from stride_service.report import (
    AnalysisMarks,
    Mitigation,
    Report,
    Severity,
    SharedElementName,
    Verdict,
)
from stride_service.sampling import load_sampling
from tests.factories import DEFAULT_FRAMEWORKS, ScriptedLlm

REPO_ROOT = Path(__file__).resolve().parents[1]
CASE_DIR = REPO_ROOT / "evals" / "corpus" / "01-payments-checkout"

TIER_NODE_BY_GRAPH_NODE = tier_node_by_graph_node(DEFAULT_FRAMEWORKS)


@pytest.fixture(scope="module")
def case():
    return load_case(CASE_DIR)


def unknown_ref(case) -> str:
    """One ``unknown-attribute`` entry from the blessed model's own catalog.

    Any entry serves — nothing in these tests scores which fact was cited — but
    it must be a real one, because an agent's reference is resolved against the
    catalog the service derives from this same model.
    """
    return next(
        ref for ref in evidence_catalog(case.model) if ref.startswith("unknown:")
    )


def scripted_proposal(case, category) -> dict:
    """One category agent's emission citing an element the blessed model contains.

    Evidence rather than a quote: the corpus case ships real sources, so a
    scripted quote would have to be a verbatim span of one to survive the
    fan-in's ladder, and these tests are about the modes rather than about
    grounding.
    """
    reference = next(
        ref for ref in case.claims_for("stride") if ref.category == category
    )
    return {
        "sequence": 1,
        "title": reference.claim,
        "description": f"{reference.claim} Scripted for the offline mode test.",
        "affected_element_ids": list(reference.affected_element_ids),
        # Taken from the reference the scripted draft is standing in for, so a
        # mode test grades the pipeline rather than this file's verb-picking.
        "verb": reference.verb,
        "evidence_refs": [unknown_ref(case)],
        "severity": Severity(
            likelihood=reference.severity.likelihood,
            impact=reference.severity.impact,
            justification="scripted",
        ).model_dump(mode="json"),
        "mitigations": [Mitigation(summary="Scripted mitigation").model_dump()],
    }


def scripted_ruling(category) -> dict:
    """The critic's ruling on one scripted draft: judgement only, keyed by ID.

    Carries no ``severity``: the draft's rating stands, which is the common
    case and the one the assemble seam merges through.
    """
    return {
        "id": f"{CATEGORY_LETTERS[category]}-01",
        "confidence": "high",
        "verdict": Verdict(status="confirmed").model_dump(mode="json"),
    }


def lane_of(instruction: str, lanes) -> str | None:
    """Which lane's agent this instruction belongs to, or ``None``.

    Matched on the lane skill's own ``# <Lane>`` H1 rather than on the lane
    name appearing anywhere: every lane skill names all six categories in its
    boundaries section, so a bare substring test binds whichever lane is
    mentioned first and silently scripts the wrong emission.

    Shared with :mod:`tests.test_evals_run_grounds`, which needs the same
    discrimination for the same reason — one ``analyze/stride`` tier key now
    serves every lane, so a tier node no longer identifies one.
    """
    for lane in lanes:
        heading = f"# {lane.replace('-', ' ').title()}"
        if heading.lower() in instruction.lower():
            return lane
    return None


class LaneAwareLlm(ScriptedLlm):
    """One adapter serving every lane, replying by the lane it was asked about.

    All six STRIDE lanes run on one ``analyze/stride`` tier key since
    ``model_tiers.toml`` v5, so a tier node no longer identifies a lane and a
    resolver keyed on one cannot script six different emissions. The lane is
    recoverable from the instruction the lane agent was actually built with,
    which is the only place it still distinguishes itself.
    """

    replies: dict[str, str] = Field(default_factory=dict)

    async def generate_content_async(self, llm_request, stream: bool = False):
        instruction = llm_request.config.system_instruction or ""
        default = self.reply
        self.reply = self._reply_for_instruction(instruction, default)
        try:
            async for response in super().generate_content_async(llm_request, stream):
                yield response
        finally:
            self.reply = default

    def _reply_for_instruction(self, instruction: str, default: str) -> str:
        lane = lane_of(instruction, self.replies)
        return self.replies[lane] if lane else default


def build(case, entry, models: dict[str, ScriptedLlm]) -> object:
    def resolve(tier_node: str) -> BaseLlm:
        graph_node = next(
            node for node, tier in TIER_NODE_BY_GRAPH_NODE.items() if tier == tier_node
        )
        models[graph_node] = LaneAwareLlm(
            model="fake-pro-001",
            reply=_reply_for(case, graph_node),
            seen=[],
            replies=_lane_replies(case, graph_node),
        )
        return models[graph_node]

    return modes.build_eval_pipeline(
        entry,
        resolve_model=resolve,
        sampling=load_sampling(REPO_ROOT / "config" / "sampling.toml"),
    )


def _lane_replies(case, graph_node: str) -> dict[str, str]:
    """Per-lane emissions, for the one adapter every lane agent shares."""
    if graph_node not in {
        analyze_node_name("stride", category) for category in STRIDE_CATEGORIES
    }:
        return {}
    return {
        category: json.dumps({"claims": [scripted_proposal(case, category)]})
        for category in STRIDE_CATEGORIES
    }


def _reply_for(case, graph_node: str) -> str:
    if graph_node == "extract":
        return json.dumps(case.model.model_dump(mode="json"))
    if graph_node == "critic_stride":
        return json.dumps(
            {"claims": [scripted_ruling(category) for category in STRIDE_CATEGORIES]}
        )
    for category in STRIDE_CATEGORIES:
        if graph_node == analyze_node_name("stride", category):
            return json.dumps({"claims": [scripted_proposal(case, category)]})
    return '{"claims": []}'


def test_analysis_mode_injects_the_blessed_model_at_prepare(case):
    models: dict[str, ScriptedLlm] = {}
    pipeline = build(case, ENTRY_PREPARE, models)

    report = asyncio.run(modes.run_analysis(case, pipeline)).report

    # No extraction ran, and the category agents saw exactly the blessed model — the
    # whole point of the mode.
    assert "extract" not in models
    assert report.system_model == case.model
    spoofing = models[analyze_node_name("stride", "spoofing")].seen[0]
    assert "flow:shopper-to-storefront-api:place-order" in spoofing


def test_analysis_mode_output_passes_the_tier_1_gates(case):
    pipeline = build(case, ENTRY_PREPARE, {})

    report = asyncio.run(modes.run_analysis(case, pipeline)).report

    assert report_issues(report) == []
    assert len(report.analyses[0].claims) == len(STRIDE_CATEGORIES)


def test_an_eval_report_carries_every_field_production_stamps(case):
    """The eval report is the production shape or it measures a different one.

    The pinned set is the guard, and it is pinned rather than derived on
    purpose: every field an :class:`~stride_service.graph.Analysis` and a
    :class:`~stride_service.report.Report` share is one the eval seam has
    to be *asked* to carry, and a field added to both without a decision here
    is exactly how ``coverage`` came to be computed at the fan-in for a sweep
    that then read an empty list for it.

    The five marks are pinned through
    :class:`~stride_service.report.AnalysisMarks`, which is where an
    ``Analysis`` holds them now.
    """
    pipeline = build(case, ENTRY_PREPARE, {})

    run = asyncio.run(modes.run_analysis(case, pipeline))

    analysis_fields = {field.name for field in fields(Analysis)} | set(
        AnalysisMarks.model_fields
    )
    shared = analysis_fields & Report.model_fields.keys()
    # Only what the envelope itself carries. The eight per-framework fields
    # moved onto the block at schema 3.0, so a field this set still named would
    # be one the envelope no longer has.
    assert shared == {
        "system_model",
        "boundary_crossings",
        "analyses",
        "shared_element_names",
    }
    block = run.report.analyses[0]
    assert len(block.coverage) == len(STRIDE_CATEGORIES)
    assert run.report.shared_element_names == [
        SharedElementName(name_slug=slug, element_ids=ids)
        for slug, ids in run.report.system_model.shared_names().items()
    ]


def test_analysis_mode_scores_against_the_reference_set(case):
    from evals.harness.scorer import score_case
    from tests.eval_factories import ScriptedJudge

    pipeline = build(case, ENTRY_PREPARE, {})
    report = asyncio.run(modes.run_analysis(case, pipeline)).report
    # The scripted threats are titled with reference claims verbatim, so a
    # judge that matches identical strings is the honest stand-in here.
    claims = report.analyses[0].claims
    judge = ScriptedJudge((claim.title, claim.title) for claim in claims)

    score = score_case(case, claims, judge)

    assert len(score.matched) == len(STRIDE_CATEGORIES)
    assert score.element_accuracy == 1.0
    assert score.severity_exact_rate == 1.0


def test_analysis_mode_surfaces_the_pre_critic_drafts(case):
    # The union the critic was handed, read off the state key ``merge_drafts``
    # already writes — no production seam moves for it.
    pipeline = build(case, ENTRY_PREPARE, {})

    run = asyncio.run(modes.run_analysis(case, pipeline))

    assert len(run.merged_drafts) == len(STRIDE_CATEGORIES)
    assert {draft.id for draft in run.merged_drafts} == {
        claim.id for claim in run.report.analyses[0].claims
    }
    # Drafts, not threats: the critic's two rulings are absent by construction.
    assert not any(hasattr(draft, "verdict") for draft in run.merged_drafts)


def test_end_to_end_mode_surfaces_the_pre_critic_drafts(case):
    pipeline = build(case, ENTRY_EXTRACT, {})

    run = asyncio.run(modes.run_end_to_end(case, pipeline))

    assert len(run.merged_drafts) == len(STRIDE_CATEGORIES)


def test_extraction_mode_runs_extract_alone(case):
    models: dict[str, ScriptedLlm] = {}
    pipeline = build(case, ENTRY_EXTRACT_ONLY, models)

    result = asyncio.run(modes.run_extraction(case, pipeline))

    assert set(models) == {"extract"}
    assert result.issues == ()
    assert result.extracted == case.model


def test_extraction_scoring_is_mechanical(case):
    pipeline = build(case, ENTRY_EXTRACT_ONLY, {})
    result = asyncio.run(modes.run_extraction(case, pipeline))

    score = modes.score_extraction(case, result)

    assert score.recall == 1.0
    assert score.precision == 1.0
    assert score.crossings_match is True
    assert score.missing == () and score.extra == ()


def test_extraction_scoring_reports_missing_and_extra_elements(case):
    pipeline = build(case, ENTRY_EXTRACT_ONLY, {})
    result = asyncio.run(modes.run_extraction(case, pipeline))
    trimmed = result.extracted.model_copy(
        update={"processes": result.extracted.processes[:1]}
    )

    score = modes.score_extraction(case, modes.ExtractionResult(case.id, trimmed, ()))

    assert score.missing
    assert score.recall < 1.0
    assert score.precision == 1.0  # nothing invented, only dropped


def score_of(case, model) -> modes.ExtractionScore:
    """Score a hand-mutated model against the blessed one, without a graph run."""
    return modes.score_extraction(case, modes.ExtractionResult(case.id, model, ()))


def edited(model, collection: str, index: int, **update):
    """A copy of one element in one of the model's collections, changed."""
    elements = list(getattr(model, collection))
    elements[index] = elements[index].model_copy(update=update)
    return model.model_copy(update={collection: elements})


def test_a_faithful_extraction_agrees_on_every_scored_attribute(case):
    pipeline = build(case, ENTRY_EXTRACT_ONLY, {})
    result = asyncio.run(modes.run_extraction(case, pipeline))

    score = modes.score_extraction(case, result)

    assert score.attributes  # the case carries scored attributes at all
    assert score.differing == ()
    assert score.attribute_agreement == 1.0


def test_a_mistyped_trust_boundary_moves_no_element_number(case):
    """The failure #195 exists for: every element right, one value wrong.

    A ``kind`` an ``elevation-of-privilege`` rule reads can stop arriving
    without recall, precision or the crossings moving at all — the ID derives
    from the name, and the crossings derive from ``trust_zone``.
    """
    mistyped = edited(case.model, "trust_boundaries", 2, kind="tenant")

    score = score_of(case, mistyped)

    assert score.recall == 1.0
    assert score.precision == 1.0
    assert score.crossings_match is True
    assert [
        (check.element_id, check.blessed, check.extracted) for check in score.differing
    ] == [("boundary:core-services", "network", "tenant")]
    assert score.attribute_agreement < 1.0


def test_an_invented_control_is_caught_where_the_blessed_model_says_unknown(case):
    """`evals/BLESSING.md`'s most repeated extraction failure, as a number."""
    invented = edited(
        case.model,
        "data_flows",
        1,
        authentication="OAuth 2.0 client credentials, rotated quarterly",
    )

    score = score_of(case, invented)

    assert [check.to_json() for check in score.differing] == [
        {
            "element": "flow:card-processor-to-storefront-api:settlement-webhook",
            "attribute": "authentication",
            "blessed": "unverified",
            "extracted": "stated",
        }
    ]


def test_rewording_a_stated_control_is_not_a_disagreement(case):
    """The state is scored, never the wording — which is what keeps the judge out."""
    reworded = edited(
        case.model,
        "data_stores",
        1,
        encryption_at_rest="a KMS key the customer manages",
    )

    assert score_of(case, reworded).differing == ()


def test_a_dropped_asset_tag_is_a_disagreement(case):
    stripped = edited(case.model, "data_stores", 0, assets=["pii"])

    score = score_of(case, stripped)

    assert [
        (check.attribute, check.blessed, check.extracted) for check in score.differing
    ] == [("assets", "financial, pii", "pii")]


def test_a_missing_element_is_not_charged_twice(case):
    """A dropped element is a miss, and its attributes are not read again."""
    dropped = case.model.model_copy(update={"data_stores": case.model.data_stores[:1]})

    score = score_of(case, dropped)

    assert score.missing == ("store:receipt-archive",)
    assert not [
        check
        for check in score.attributes
        if check.element_id == "store:receipt-archive"
    ]


def test_the_case_payload_carries_the_disagreements_and_the_count(case):
    payload = score_of(
        case, edited(case.model, "processes", 0, exposure="internal")
    ).to_json()

    assert payload["attributes_compared"] > 1
    assert payload["attribute_agreement"] < 1.0
    assert payload["attributes_differing"] == [
        {
            "element": "process:storefront-api",
            "attribute": "exposure",
            "blessed": "internet-facing",
            "extracted": "internal",
        }
    ]


def test_the_sweep_aggregate_splits_by_element_type_and_attribute(case):
    """``kind`` names two vocabularies, so the split has to name the type too."""
    clean = score_of(case, case.model)
    mistyped = score_of(case, edited(case.model, "trust_boundaries", 0, kind="other"))

    totals = modes.aggregate_attributes([clean, mistyped])

    assert totals["compared"] == 2 * len(clean.attributes)
    assert totals["agreed"] == totals["compared"] - 1
    # The mistyped boundary lands here and nowhere near the entity kinds.
    assert totals["by_attribute"]["boundary.kind"]["agreed"] == 5
    assert totals["by_attribute"]["boundary.kind"]["compared"] == 6
    assert totals["by_attribute"]["entity.kind"]["agreement"] == 1.0
    # Attribute declaration order, so two sweeps' printed splits line up row by
    # row, with the types sharing one attribute name grouped together.
    assert list(totals["by_attribute"]) == [
        "boundary.kind",
        "entity.kind",
        "process.exposure",
        "boundary.assets",
        "entity.assets",
        "flow.assets",
        "process.assets",
        "store.assets",
        "flow.authentication",
        "flow.encryption_in_transit",
        "store.encryption_at_rest",
        "store.data_classification",
    ]


def test_an_extraction_that_produced_nothing_compares_no_attributes(case):
    score = score_of(case, None)

    assert score.attributes == ()
    assert score.attribute_agreement == 0.0
    assert score.crossings_match is False


def test_end_to_end_mode_runs_the_production_entry(case):
    models: dict[str, ScriptedLlm] = {}
    pipeline = build(case, ENTRY_EXTRACT, models)

    report = asyncio.run(modes.run_end_to_end(case, pipeline)).report

    assert "extract" in models
    assert report_issues(report) == []


# --- Provenance -------------------------------------------------------------
#
# A sweep is one of certification's two callers. Without a stamped NodeRun per
# execution an eval report carries no fingerprint, ``fingerprints_of`` returns an
# empty mapping, and ``certify`` announces every fingerprint blessed having seen
# none.


def test_an_eval_report_stamps_the_nodes_that_actually_ran(case):
    pipeline = build(case, ENTRY_EXTRACT, {})

    report = asyncio.run(modes.run_end_to_end(case, pipeline)).report

    stamped = {node_run.node for node_run in report.nodes}
    assert EXTRACT_NODE in stamped
    assert "critic_stride" in stamped
    assert stamped <= {node.name for node in pipeline.workflow.graph.nodes}
    # The placeholder this replaced.
    assert "eval" not in stamped


def test_an_eval_report_presents_fingerprints_to_certify(case):
    pipeline = build(case, ENTRY_EXTRACT, {})

    report = asyncio.run(modes.run_end_to_end(case, pipeline)).report
    observations = fingerprints_of(report.nodes)

    assert observations, "a sweep with no observations certifies nothing"
    assert all(prints for prints in observations.values())
    assert observations.keys() <= set(TIER_NODE_BY_GRAPH_NODE)


def test_an_eval_reports_fingerprints_recompute_from_its_own_clear_block(case):
    """Evidence, not assertion: the artifact carries what verifies it."""
    pipeline = build(case, ENTRY_EXTRACT, {})

    report = asyncio.run(modes.run_end_to_end(case, pipeline)).report

    assert report.sampling == {
        tier: params.model_dump() for tier, params in pipeline.tier_sampling.items()
    }


def test_an_eval_report_records_the_same_analysis_context_a_job_does(case):
    """One record, two drivers.

    A sweep's report is the artifact a promotion and a comparison read, so a
    block only the service stamped would leave every eval number unattributable
    to the packs and rules the run was actually given. ``Analysis.context`` is
    the single composer for exactly that reason.
    """
    pipeline = build(case, ENTRY_EXTRACT, {})

    report = asyncio.run(modes.run_end_to_end(case, pipeline)).report
    context = report.analysis_context
    fired_rules = report.analyses[0].fired_rules

    assert context.instruction_sha256 == pipeline.instruction_sha256
    # fired_rules names this package's own Candidate rules, so it sits on the
    # block rather than on the envelope's shared context.
    assert fired_rules == sorted(set(fired_rules))


def test_extraction_mode_observes_its_one_node(case):
    """The mode produces no report, and its ``extract`` identity counts anyway."""
    pipeline = build(case, ENTRY_EXTRACT_ONLY, {})

    result = asyncio.run(modes.run_extraction(case, pipeline))
    observations = fingerprints_of(result.node_runs)

    assert set(observations) == {EXTRACT_NODE}


def test_every_mode_maps_to_a_graph_entry():
    assert modes.MODE_ENTRIES == {
        "extraction": ENTRY_EXTRACT_ONLY,
        "analysis": ENTRY_PREPARE,
        "end-to-end": ENTRY_EXTRACT,
    }
