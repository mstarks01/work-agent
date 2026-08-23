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

CORPUS = Path(__file__).resolve().parents[1] / "evals" / "corpus"
from stride_service.certification import fingerprints_of
from stride_service.evidence import evidence_catalog
from stride_service.frameworks import package_for
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
    from evals.harness.ledger import Ledger
    from evals.harness.scorer import score_case
    from tests.eval_factories import ScriptedMatcher

    pipeline = build(case, ENTRY_PREPARE, {})
    report = asyncio.run(modes.run_analysis(case, pipeline)).report
    # The scripted threats are titled with reference claims verbatim, so a
    # matcher on identical strings is the honest stand-in here.
    claims = report.analyses[0].claims
    matcher = ScriptedMatcher((claim.title, claim.title) for claim in claims)

    score = score_case(case, claims, matcher, Ledger())

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
    """The state is scored, never the wording — which is what keeps interpretation out."""
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


class TestTheHarnessSeedsFrameworkOptions:
    """The driver's half of the options contract, which #290 found missing.

    ``prepare_analysis`` validates every selected framework's options and raises
    when one is absent, since no package field carries a default.
    ``AdkPipelineRunner`` seeds them from the job; the harness has to seed them
    from the case, and did not.

    **Why no offline test caught it.** ``tests/test_graph.py`` seeds
    ``ASVS_OPTIONS`` by hand, so every test of the graph supplied what the
    harness omits. The gap lived in the one seam nothing drove end to end, and a
    live sweep found it on the first case — for no money, because
    ``prepare_analysis`` is a deterministic node ahead of the fan-out.
    """

    def test_a_declared_option_reaches_the_seeded_state(self):
        """Read off the case, in the shape ``prepare_analysis`` validates."""
        case = load_case(CORPUS / "01-payments-checkout")

        options = modes.case_framework_options(case)

        assert options["asvs"] == {"level": 2}
        assert options["stride"] == {}

    def test_every_declared_framework_gets_an_entry(self):
        """A framework the case names and the map omits is the raise.

        Over the whole corpus rather than one case, because the defect was a
        package with a *required* option arriving beside one with none — so a
        fixture built from either alone would have passed.
        """
        for path in sorted(p for p in CORPUS.iterdir() if p.is_dir()):
            case = load_case(path)
            options = modes.case_framework_options(case)
            assert set(options) == set(modes.case_frameworks(case)), path.name

    def test_the_options_satisfy_every_packages_own_model(self):
        """The check ``prepare_analysis`` runs, run here where it costs nothing.

        This is the assertion that would have failed before the fix, and it
        fails for any package that later declares a required option its corpus
        cases do not carry.
        """
        for path in sorted(p for p in CORPUS.iterdir() if p.is_dir()):
            case = load_case(path)
            options = modes.case_framework_options(case)
            for name in modes.case_frameworks(case):
                package_for(name).options.model_validate(options.get(name) or {})


class TestNarrowingASweepToOneFramework:
    """``--framework`` is a pure selection, added by #291 for capacity.

    One job fans out one ``strong``-tier request per lane of every framework it
    names, all at the barrier — 23 today. Against a 200,000 token-per-minute
    quota that burst is over budget on a single job, and ``max_active_jobs``
    does not help because it bounds *jobs*. Narrowing the selection is the only
    lever inside the harness, and a live sweep is what found that out.
    """

    def test_no_narrowing_runs_every_declared_framework(self):
        """The default is what every sweep did before this existed."""
        case = load_case(CORPUS / "01-payments-checkout")

        assert modes.select_frameworks(case) == modes.case_frameworks(case)

    def test_narrowing_keeps_only_what_was_asked_for(self):
        case = load_case(CORPUS / "01-payments-checkout")

        assert modes.select_frameworks(case, ("stride",)) == ("stride",)
        assert modes.select_frameworks(case, ("asvs",)) == ("asvs",)

    def test_narrowing_preserves_the_packages_declared_order(self):
        """Order is the report's block order, so a selection must not reorder it."""
        case = load_case(CORPUS / "01-payments-checkout")
        declared = modes.case_frameworks(case)

        assert modes.select_frameworks(case, tuple(reversed(declared))) == declared

    def test_a_case_declaring_none_of_the_selection_yields_empty(self):
        """Empty is a case the sweep did not measure, and the caller skips it.

        Case 03 declares STRIDE alone, so an ASVS-only sweep has nothing to run
        on it. That is different from a case that ran and scored zero, and the
        sweep prints the skipped list rather than dropping it.
        """
        case = load_case(CORPUS / "03-batch-data-pipeline")

        assert modes.select_frameworks(case, ("asvs",)) == ()

    def test_narrowing_does_not_touch_the_options(self):
        """A pure selection names no option and changes no reference set.

        The options map still answers for every framework the *case* declares,
        because narrowing decides what a sweep builds rather than what a case
        is graded for.
        """
        case = load_case(CORPUS / "01-payments-checkout")

        assert set(modes.case_framework_options(case)) == set(
            modes.case_frameworks(case)
        )


class TestTheEndpointReadingOfAnExtraction:
    """A flow's identity is its endpoints; its label describes it (#293).

    Two models on the same corpus both missed
    ``flow:card-processor-to-storefront-api:settlement-webhook`` and both
    emitted those exact endpoints under another label. Strictly that is a miss
    *and* an invention for one flow that was found. The strict reading is right
    for a report reader — an ID that does not resolve does not resolve — and
    wrong for a question about extraction, so both are reported.
    """

    def score(self, matched=(), missing=(), extra=()):
        return modes.ExtractionScore(
            case_id="x",
            matched=tuple(matched),
            missing=tuple(missing),
            extra=tuple(extra),
            crossings_match=True,
            attributes=(),
        )

    def test_a_relabelled_flow_stops_being_both_a_miss_and_an_invention(self):
        """The defect the reading exists for, at its smallest."""
        s = self.score(
            missing=("flow:a-to-b:settlement-webhook",),
            extra=("flow:a-to-b:payment-webhook",),
        )

        assert s.recall == 0.0
        assert s.endpoint_recall == 1.0
        assert s.endpoint_missing == frozenset()
        assert s.endpoint_extra == frozenset()

    def test_a_genuinely_missed_flow_stays_missed(self):
        """Folding the label must not forgive an endpoint pair nobody emitted."""
        s = self.score(missing=("flow:a-to-b:x",), extra=("flow:c-to-d:y",))

        assert s.endpoint_missing == frozenset({"flow:a-to-b"})
        assert s.endpoint_extra == frozenset({"flow:c-to-d"})
        assert s.endpoint_recall == 0.0

    def test_nothing_but_flows_is_folded(self):
        """An entity has no structural key behind its name, so it is not folded.

        ``entity:shopper`` against ``entity:shoppers`` is the same architecture
        by eye and there is no mechanical way to say so. Folding it would be a
        fuzzy match wearing a mechanical number's clothes.
        """
        s = self.score(missing=("entity:shopper",), extra=("entity:shoppers",))

        assert s.endpoint_missing == frozenset({"entity:shopper"})
        assert s.endpoint_extra == frozenset({"entity:shoppers"})

    def test_the_two_readings_agree_when_no_flow_was_relabelled(self):
        """The reading adds nothing where naming did not drift, which is the point."""
        s = self.score(matched=("process:a", "flow:a-to-b:x"), missing=("store:c",))

        assert s.recall == s.endpoint_recall

    def test_an_empty_score_reads_zero_on_both(self):
        assert self.score().recall == 0.0
        assert self.score().endpoint_recall == 0.0


class TestTheNameFreeReadingOfCrossings:
    """A crossing means the endpoints are in different zones (#297).

    That sentence contains no zone name. ``crossings_match`` compares
    ``BoundaryCrossing`` lists, and both zone fields hold a boundary *ID*, so an
    extraction that partitions the elements identically and names a zone
    ``boundary:internet`` where the corpus says ``boundary:public-internet``
    fails every crossing in the case. On the 2026-08-23 sweeps that read as
    ``crossings DIFFER`` on 13 of 13 cases for both models.
    """

    def score(self, blessed=(), extracted=None, match=False):
        return modes.ExtractionScore(
            case_id="x",
            matched=(),
            missing=(),
            extra=(),
            crossings_match=match,
            attributes=(),
            blessed_crossings=tuple(blessed),
            extracted_crossings=extracted,
        )

    def test_the_same_partition_under_another_zone_name_scores_full(self):
        """The defect at its smallest: identical separation, different labels."""
        s = self.score(blessed=("flow:a-to-b",), extracted=("flow:a-to-b",))

        assert s.crossings_match is False
        assert s.crossings_recall == 1.0

    def test_a_flow_the_model_did_not_separate_is_missed(self):
        """Set membership *is* the boolean: not separated means not present."""
        s = self.score(
            blessed=("flow:a-to-b", "flow:c-to-d"), extracted=("flow:a-to-b",)
        )

        assert s.crossings_recall == 0.5

    def test_an_underivable_extraction_scores_zero_not_perfect(self):
        """``None`` is "said nothing", which must not read as "nothing crosses".

        A model whose flow endpoints are not zoned elements is the worst
        extraction in a sweep. Scoring it as agreeing with an empty blessed set
        would make it the best.
        """
        s = self.score(blessed=("flow:a-to-b",), extracted=None)

        assert s.crossings_derivable is False
        assert s.crossings_recall == 0.0

    def test_a_case_with_no_blessed_crossing_reads_zero(self):
        """No denominator, so no rate — never a silent 1.0."""
        assert self.score(blessed=(), extracted=()).crossings_recall == 0.0

    def test_the_flow_label_is_folded_here_too(self):
        """Keyed by ``_endpoint_key``, so #293's fold reaches crossings.

        Driven over a real blessed model rather than a fixture: the whole point
        is that the key drops the descriptive third segment a live extraction
        gets wrong, and a hand-built flow ID would not prove the corpus's do.
        """
        keys = modes.crossing_keys(load_case(CORPUS / "01-payments-checkout").model)

        assert keys
        assert all(key.count(":") == 1 for key in keys), keys
        assert all(key.startswith("flow:") for key in keys)

    def test_a_model_with_no_zones_is_underivable_rather_than_empty(self):
        """``None`` comes from the raise, not from a guess about the input."""
        assert modes.crossing_keys(None) is None

    def test_both_readings_are_serialised(self):
        """The strict one is not replaced — a report reader sees zone names."""
        record = self.score(
            blessed=("flow:a-to-b",), extracted=("flow:a-to-b",), match=False
        ).to_json()

        assert record["crossings_match"] is False
        assert record["crossings_recall"] == 1.0
        assert record["crossings_missing"] == []


class TestTheInitiatorReadingOfAnExtraction:
    """Pure initiators are dropped more than other elements (#295).

    Measured over five gpt-4o runs on an unchanged config: **0.421 recall
    against 0.600** for every other non-flow element, lower in all five, a gap
    of 0.179. 16 of the 19 distinct dangling endpoints in those runs were flow
    *sources*, and the repeat offenders are exactly these elements.

    Total Tier 1 failures cannot see it — that count has an sd of 3.27 on an
    unchanged config, so a fix removing most of these would move it by less than
    two standard deviations. This reading is where the effect concentrates.
    """

    def model(self, flows):
        """A blessed model carrying only what the reading walks: its flows."""
        from stride_service.system_model import SystemModel

        case = load_case(CORPUS / "07-cicd-store-deploy")
        return SystemModel.model_validate(
            case.model.model_dump(mode="json") | {"data_flows": flows}
        )

    def test_an_element_that_only_ever_sends_is_an_initiator(self):
        real = load_case(CORPUS / "07-cicd-store-deploy").model

        assert modes.pure_initiators(real) == frozenset(
            {"entity:developer", "process:store-server"}
        )

    def test_an_element_that_also_receives_is_not(self):
        """`process:build-runner` sends four flows and receives one."""
        real = load_case(CORPUS / "07-cicd-store-deploy").model

        assert "process:build-runner" not in modes.pure_initiators(real)

    def test_dropping_an_initiator_shows_here_and_barely_moves_recall(self):
        """The whole reason for a second reading.

        One dropped initiator of two is half the initiator recall, and one
        missing element out of nineteen is a rounding error on the overall one.
        """
        score = modes.ExtractionScore(
            case_id="07",
            matched=(),
            missing=("process:store-server",),
            extra=(),
            crossings_match=False,
            attributes=(),
            blessed_initiators=("entity:developer", "process:store-server"),
        )

        assert score.initiator_recall == 0.5
        assert score.initiators_missing == frozenset({"process:store-server"})

    def test_a_case_with_no_initiator_reads_zero(self):
        """An empty denominator is never a silent 1.00, as everywhere else here."""
        score = modes.ExtractionScore(
            case_id="x",
            matched=(),
            missing=(),
            extra=(),
            crossings_match=True,
            attributes=(),
        )

        assert score.initiator_recall == 0.0

    def test_every_corpus_case_has_an_initiator_to_measure(self):
        """Guards the guard: a metric with no denominator anywhere measures nothing."""
        for path in sorted(p for p in CORPUS.iterdir() if p.is_dir()):
            model = load_case(path).model
            assert modes.pure_initiators(model), path.name


class TestASweepSurvivesARefusedModel:
    """One case's model being refused costs that case, not the sweep (#303).

    A live `--mode end-to-end` run died on case 09 and produced no artifact,
    losing the eight cases that had already run:

        EvalRunError: 09-cookbook-sokify-retail: the graph rejected the model:
        unverifiable-excerpt: source_excerpt '...' is not found in the source

    `_run_mode`'s docstring already states the rule — a case the fan-in rejects
    is "counted and survived rather than allowed to abort the sweep" — and it
    held for two failure classes out of three. The same event is a recorded
    measurement in `extraction` mode and an aborted run in `end-to-end`, which
    is the mode where a refused model is most expected and most expensive.
    """

    def test_routing_it_through_the_draft_classifier_would_miscount_it(self):
        """`classify_failure` accepts it and files it as a *grounds* failure.

        It does not raise — it returns kind ``other`` with ``draft_count=0`` —
        and that is the argument for the separate branch rather than against it.
        `GroundsFailure` feeds the grounds instrument, whose subject is what a
        case's drafts did. A case whose model was refused produced no drafts, so
        counting it there inflates a grounds number with a non-grounds event.
        """
        from evals.harness.grounds import classify_failure

        filed = classify_failure(
            "09", modes.EvalRunError("09: the graph rejected the model")
        )

        assert filed.kind == "other"
        assert filed.draft_count == 0
        assert filed.threat_ids == ()

    def test_the_message_already_names_its_case(self):
        """So the sweep records `str(error)` rather than prefixing it twice."""
        error = modes.EvalRunError("09-cookbook-sokify-retail: the graph rejected")

        assert str(error).startswith("09-cookbook-sokify-retail: ")

    def test_the_sweep_catches_it_before_the_fan_in_classes(self):
        """Order matters: `EvalRunError` is a `RuntimeError`, not a `DraftJoinError`.

        Reading the source rather than driving a live graph, because reaching
        this branch for real needs a provider and a model that fails validation
        — which is what cost the sweep that found it.
        """
        source = (
            Path(__file__).resolve().parents[1] / "evals" / "harness" / "run.py"
        ).read_text(encoding="utf-8")
        body = source.split("else await modes.run_end_to_end(case, pipeline)", 1)[1]

        assert body.index("except modes.EvalRunError") < body.index("except CAUGHT")
        assert "continue" in body[: body.index("except CAUGHT")]
