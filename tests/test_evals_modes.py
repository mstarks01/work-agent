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
from dataclasses import fields, replace
from pathlib import Path

import pytest
from google.adk.models.base_llm import BaseLlm
from pydantic import Field

from evals.harness import modes
from evals.harness.alignment import Alignment, Pair
from evals.harness.identity import comparable_elements
from evals.harness.reference import load_case, load_corpus
from evals.harness.structural import report_issues

CORPUS = Path(__file__).resolve().parents[1] / "evals" / "corpus"
from analysis_service.analysis import control_state, states_a_protocol
from analysis_service.assertions import ABSENT, projection_fields
from analysis_service.basis import IN_SCOPE
from analysis_service.certification import fingerprints_of
from analysis_service.claims import (
    AnalysisMarks,
    Mitigation,
    Severity,
    SharedElementName,
)
from analysis_service.evidence import evidence_catalog
from analysis_service.frameworks import package_for
from analysis_service.frameworks.stride.record import (
    CATEGORY_LETTERS,
    STRIDE_CATEGORIES,
)
from analysis_service.graph import (
    ENTRY_ASSERT_ONLY,
    ENTRY_EXTRACT,
    ENTRY_EXTRACT_ONLY,
    ENTRY_HEAD_ONLY,
    ENTRY_PREPARE,
    EXTRACT_NODE,
    Analysis,
    Pipeline,
    analyze_node_name,
    tier_node_by_graph_node,
)
from analysis_service.grounding import verify_quote
from analysis_service.report import Report
from analysis_service.sampling import load_sampling
from analysis_service.system_model import (
    FLOW_DELIMITER,
    ZONE_ATTRIBUTE,
    ModelIndex,
    SystemModel,
    flow_label,
    make_flow_id,
    normalize_element_ids,
)
from analysis_service.validation import validate
from tests.factories import DEFAULT_FRAMEWORKS, EVAL_MODEL, ScriptedLlm

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


def reaching_refs(case, element_ids) -> list[str]:
    """The one catalog entry whose place best reaches ``element_ids``.

    The fan-in bounds ``affected_element_ids`` to one hop from the places a
    claim's grounds name (#441), so a scripted draft has to cite a fact that
    reaches the elements it stands in for, or the service drops them. One
    entry, because these tests count grounds per draft; a ``crossing:`` entry
    over an ``unknown:`` where both reach, so a scripted draft rests on a stated
    fact wherever the model offers one and these tests are not all reading one
    kind of ground. Where nothing reaches every element the best partial reach
    is cited, and the drop of the rest is the point.
    """
    from analysis_service.system_model import ModelIndex

    catalog = evidence_catalog(case.model)
    index = ModelIndex.of(case.model)
    wanted = set(element_ids)

    def merit(ref: str) -> tuple[int, bool]:
        ground = catalog[ref]
        reach = index.reach({ground.element_id or ground.flow_id})
        return len(wanted & reach), not ref.startswith("unknown:")

    return [max(catalog, key=merit)]


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
        "evidence_refs": reaching_refs(case, reference.affected_element_ids),
        "severity": Severity(
            likelihood=reference.severity.likelihood,
            impact=reference.severity.impact,
            justification="scripted",
        ).model_dump(mode="json"),
        "mitigations": [Mitigation(summary="Scripted mitigation").model_dump()],
    }


def scripted_ruling(case, category) -> dict:
    """The critic's ruling on one scripted draft: judgement only, keyed by ID.

    Carries no ``severity``: the draft's rating stands, which is the common
    case and the one the assemble seam merges through.

    The verdict follows the draft's grounds, which is one of the several answers
    a critic may legally give: a draft citing an ``unknown-attribute`` ground
    gets a bare ``needs-info`` the service completes with the pairs, and one
    resting on a crossing alone is confirmed. Scripted so these tests read a
    fixed critic; a live one rules on the argument as well.
    """
    reference = next(
        ref for ref in case.claims_for("stride") if ref.category == category
    )
    refs = reaching_refs(case, reference.affected_element_ids)
    conditional = any(ref.startswith("unknown:") for ref in refs)
    return {
        "id": f"{CATEGORY_LETTERS[category]}-01",
        "confidence": "low" if conditional else "high",
        "verdict": {"status": "needs-info" if conditional else "confirmed"},
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


def build(case, entry, models: dict[str, ScriptedLlm]) -> Pipeline:
    def resolve(tier_node: str) -> BaseLlm:
        graph_node = next(
            node for node, tier in TIER_NODE_BY_GRAPH_NODE.items() if tier == tier_node
        )
        models[graph_node] = LaneAwareLlm(
            model=EVAL_MODEL,
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


def scripted_assertions(case) -> str:
    """Two rows a model could emit for this case, quoting its own excerpts.

    An element's ``source_excerpt`` verifies against its source by
    construction, so scripting the quotes from the blessed model exercises the
    span locator on real corpus text rather than on a fixture nobody submitted.
    """
    flow = next(flow for flow in case.model.data_flows if flow.source_excerpt)
    quote = {"source_label": flow.source_label, "quote": flow.source_excerpt}
    return json.dumps(
        {
            "assertions": [
                {
                    "subject_type": "interaction",
                    "subject": flow.id,
                    "predicate": "authentication-mechanism",
                    "value": "a shared account",
                    "basis": "stated",
                    "quotes": [quote],
                },
                {
                    "subject_type": "interaction",
                    "subject": flow.id,
                    "predicate": "mfa-requirement",
                    "value": "absent",
                    "basis": "stated",
                    "quotes": [quote],
                },
                {
                    "subject_type": "interaction",
                    "subject": "flow:nowhere:at-all",
                    "predicate": "mfa-requirement",
                    "value": "required",
                    "basis": "stated",
                    "quotes": [quote],
                },
            ]
        }
    )


def _reply_for(case, graph_node: str) -> str:
    if graph_node == "assert":
        return scripted_assertions(case)
    if graph_node == "extract":
        return json.dumps(case.model.model_dump(mode="json"))
    if graph_node == "critic_stride":
        return json.dumps(
            {
                "claims": [
                    scripted_ruling(case, category) for category in STRIDE_CATEGORIES
                ]
            }
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
    assert "flow:entity:shopper>process:storefront-api>place-order" in spoofing


def test_analysis_mode_output_passes_the_tier_1_gates(case):
    pipeline = build(case, ENTRY_PREPARE, {})

    report = asyncio.run(modes.run_analysis(case, pipeline)).report

    assert report_issues(report) == []
    assert len(report.analyses[0].claims) == len(STRIDE_CATEGORIES)


def test_an_eval_report_carries_every_field_production_stamps(case):
    """The eval report is the production shape or it measures a different one.

    The pinned set is the guard, and it is pinned rather than derived on
    purpose: every field an :class:`~analysis_service.graph.Analysis` and a
    :class:`~analysis_service.report.Report` share is one the eval seam has
    to be *asked* to carry, and a field added to both without a decision here
    is exactly how ``coverage`` came to be computed at the fan-in for a sweep
    that then read an empty list for it.

    The marks are pinned through
    :class:`~analysis_service.claims.AnalysisMarks`, which is the one field
    an ``Analysis`` holds them on.
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
        "model_repair",
        "assertions",
    }
    # The decision for ``model_repair``: an eval run enters the graph past
    # extraction, so no repair pass ran and the envelope says so with ``None``,
    # which is a fact about this run rather than a field the seam forgot.
    assert run.report.model_repair is None
    # The decision for ``assertions``: the harness builds its graphs through
    # the deployment, so a sweep on an install that sets ``ANALYSIS_ASSERTIONS``
    # runs the pass and records the catalog the way a job does. This graph was
    # built without it, and the envelope says so with ``None``.
    assert run.report.assertions is None
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
    assert score.element_agreement == 1.0
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


class TestTheAssertionMode:
    """Mode 4, end to end offline: the node, the resolver, and the counts."""

    def run(self, case, models=None):
        pipeline = build(case, ENTRY_ASSERT_ONLY, {} if models is None else models)
        return asyncio.run(modes.run_assertions(case, pipeline))

    def test_it_runs_assert_alone_over_the_blessed_model(self, case):
        models: dict[str, ScriptedLlm] = {}
        self.run(case, models)

        assert set(models) == {"assert"}

    def test_the_node_reads_the_model_it_is_asked_about(self, case):
        """The seeded model reaches the instruction, rendered by ``read``."""
        models: dict[str, ScriptedLlm] = {}
        self.run(case, models)

        instruction = models["assert"].seen[0]
        assert case.model.data_flows[0].id in instruction

    def test_a_row_the_model_does_not_hold_drops_and_says_so(self, case):
        result = self.run(case)

        assert len(result.catalog.entries) == 2
        assert [issue.code for issue in result.issues] == ["dangling-subject"]

    def test_every_kept_row_carries_a_located_span(self, case):
        result = self.run(case)

        for entry in result.catalog.entries:
            assert entry.support
            span = entry.support[0]
            text = next(
                source.text
                for source in case.sources
                if source.label == span.source_label
            )
            assert verify_quote(span.quote, text[span.start : span.end])

    def test_the_counts_are_taken_over_what_resolved(self, case):
        result = self.run(case)

        score = modes.score_assertions(case, result)

        assert (score.proposed, score.kept, score.rejected) == (3, 2, 1)
        assert score.absences == 1
        assert score.span_backed == 2
        assert score.refused == {"dangling-subject": 1}
        assert score.assessed == {
            "unchecked": 2,
            "supported": 0,
            "unsupported": 0,
            "unresolved": 0,
        }
        assert score.to_json()["predicates"] == [
            "authentication-mechanism",
            "mfa-requirement",
        ]

    def test_the_projection_is_measured_against_the_blessed_model(self, case):
        """What the catalog's rows would put back into the graph, and what they cannot.

        Two rows resolved. One projects into the flow's ``authentication`` and
        reads the same control state the blessed model does. The other is the
        stated absence of MFA, and **it projects into nothing at all** — the
        graph has no field for it, which is the whole of the audit's finding.
        """
        result = self.run(case)

        score = modes.score_assertions(case, result)

        assert (score.kept, score.projected) == (2, 1)
        assert score.agrees_stated == 1
        assert score.agrees_unstated == 0
        assert score.projection_degraded == 0
        assert score.to_json()["agrees_stated"] == 1

    def test_the_projection_is_counted_against_a_denominator(self, case):
        """What was there to reach, not only what the catalog reached.

        The one row that projects lands on the flow's ``authentication``, which
        this fixture's model states, so it is one of one. ``reachable`` is what
        the whole model offers, which is more than one row can reach.
        """
        score = modes.score_assertions(case, self.run(case))
        reachable = modes.reachable_controls(case.model)
        stated = [key for key, stratum in reachable.items() if stratum == "stated"]

        assert score.reached == 1
        assert score.reachable == len(stated)
        assert score.reachable > score.reached
        assert reachable[case.model.data_flows[0].id, "authentication"] == "stated"

    def test_agreeing_that_nothing_is_stated_is_counted_apart(self, case):
        """The #891 split: a free agreement is not a measured one.

        Every pair in the denominator has a blessed value ``control_state``
        reads as ``stated`` or ``absent``, and its stratum is that reading; an
        attribute the model leaves unverified is outside both and an agreement
        there lands in ``agrees_unstated``.
        """
        reachable = modes.reachable_controls(case.model)

        assert reachable
        for (element_id, attribute), stratum in reachable.items():
            element = ModelIndex.of(case.model).get(element_id)
            assert control_state(str(getattr(element, attribute))) == stratum
            assert stratum != "unverified"

    def test_a_stated_absence_is_the_number_the_graph_cannot_carry(self, case):
        """The audit's own fact: ten corpus values hide one of these (#925)."""
        result = self.run(case)
        (absence,) = [
            entry for entry in result.catalog.entries if entry.value == ABSENT
        ]

        assert absence.predicate == "mfa-requirement"
        assert absence.basis == "stated"
        assert absence.predicate not in projection_fields()


class TestAProjectionIsComparedTheWayItsFieldSays:
    """Finding 10 of #961: a state reduction erased the value of an enum.

    ``exposure`` and ``trust_zone`` are compared as written; a mechanism is
    still compared as a state, because no reviewed semantic label exists to
    compare it under. An explicit absence is a second denominator, not a
    share of the stated one and not outside both.
    """

    CASE = "01-payments-checkout"

    def resolved(self, golden, rows):
        from analysis_service.assertions import CatalogProposal, resolve_catalog

        sources = {source.label: source.text for source in golden.sources}
        catalog, _ = resolve_catalog(
            CatalogProposal(assertions=rows), golden.model, sources
        )
        return catalog

    def quoted(self, golden, subject_id):
        element = next(e for e in golden.model.elements() if e.id == subject_id)
        return [{"source_label": element.source_label, "quote": element.source_excerpt}]

    def test_the_opposite_exposure_does_not_agree(self):
        """The audit's probe: the internet-facing storefront asserted internal."""
        golden = load_case(CORPUS / self.CASE)
        subject = "process:storefront-api"
        assert next(e.exposure for e in golden.model.processes if e.id == subject) == (
            "internet-facing"
        )
        catalog = self.resolved(
            golden,
            [
                {
                    "subject_type": "component",
                    "subject": subject,
                    "predicate": "internet-exposure",
                    "value": "internal",
                    "basis": "stated",
                    "quotes": self.quoted(golden, subject),
                }
            ],
        )

        counts = modes._projection_counts(golden, catalog)

        assert counts["reached"] == 1
        assert counts["agrees_stated"] == 0
        assert counts["agrees_unstated"] == 0

    def test_the_stated_exposure_agrees(self):
        golden = load_case(CORPUS / self.CASE)
        subject = "process:storefront-api"
        catalog = self.resolved(
            golden,
            [
                {
                    "subject_type": "component",
                    "subject": subject,
                    "predicate": "internet-exposure",
                    "value": "internet-facing",
                    "basis": "stated",
                    "quotes": self.quoted(golden, subject),
                }
            ],
        )

        assert modes._projection_counts(golden, catalog)["agrees_stated"] == 1

    def test_an_explicit_absence_is_its_own_denominator(self):
        """Case 01's gRPC hop is unauthenticated, and the sources say so."""
        golden = load_case(CORPUS / self.CASE)
        flow = "flow:process:storefront-api>process:order-service>submit-order"
        reachable = modes.reachable_controls(golden.model)

        assert reachable[flow, "authentication"] == "absent"
        counts = modes._projection_counts(golden, self.resolved(golden, []))
        assert counts["reachable_absent"] >= 1
        assert counts["reached_absent"] == 0
        assert counts["agrees_absent"] == 0

    def test_asserting_the_absence_agrees_in_the_absent_stratum(self):
        golden = load_case(CORPUS / self.CASE)
        flow = "flow:process:storefront-api>process:order-service>submit-order"
        catalog = self.resolved(
            golden,
            [
                {
                    "subject_type": "interaction",
                    "subject": flow,
                    "predicate": "authentication-mechanism",
                    "value": ABSENT,
                    "basis": "stated",
                    "quotes": self.quoted(golden, flow),
                }
            ],
        )

        counts = modes._projection_counts(golden, catalog)

        assert counts["reached_absent"] == 1
        assert counts["agrees_absent"] == 1
        assert counts["agrees_stated"] == counts["agrees_unstated"] == 0

    def test_the_corpus_holds_six_explicit_absences(self):
        """The count, pinned: a corpus edit that moves it is the alarm.

        The audit counted seven. The #961 step 3 signing ruled that case 04's
        "Redis has no password on it" states the absence of a password and
        not of every mechanism, so that flow reads unknown and six remain.
        """
        absent = sum(
            1
            for golden in load_corpus(CORPUS)
            for stratum in modes.reachable_controls(golden.model).values()
            if stratum == "absent"
        )

        assert absent == 6

    def test_every_projected_field_has_a_comparison(self):
        """The table is held to the registry, so a sixth field raises here."""
        assert set(projection_fields().values()) <= set(modes.PROJECTION_COMPARED)

    def test_a_closed_vocabulary_compares_by_value_and_a_mechanism_by_state(self):
        assert modes.PROJECTION_COMPARED["exposure"]("internal") == "internal"
        assert modes.PROJECTION_COMPARED["trust_zone"]("boundary:x") == "boundary:x"
        assert modes.PROJECTION_COMPARED["authentication"]("mTLS") == "stated"
        assert modes.PROJECTION_COMPARED["authentication"] is control_state


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
    assert score.scored_field_agreement == 1.0


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
    assert score.scored_field_agreement < 1.0


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
            "element": "flow:entity:card-processor>process:storefront-api>settlement-webhook",
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
    assert payload["scored_field_agreement"] < 1.0
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
        "process.interface_kind",
        "boundary.assets",
        "entity.assets",
        "flow.assets",
        "process.assets",
        "store.assets",
        "flow.operations",
        "flow.protocol",
        "flow.authentication",
        "flow.encryption_in_transit",
        "store.encryption_at_rest",
        "store.data_classification",
    ]


def test_the_two_attributes_the_asvs_precondition_reads_are_scored(case):
    """A non-web process must stay non-web, and a silent protocol silent.

    Both decide whether ASVS runs at all, and neither was compared before
    (#659). The protocol is compared by state rather than wording: two correct
    readings of "over gRPC" spell it differently, and neither is an invention.
    """
    # Found rather than indexed: the case's first flow states no protocol once
    # the reference stops inferring one (#925), and a test pinned to a corpus
    # position reads a different fact after every corpus edit.
    stated = next(
        index
        for index, flow in enumerate(case.model.data_flows)
        if states_a_protocol(flow.protocol)
    )
    web = next(
        index
        for index, process in enumerate(case.model.processes)
        if process.interface_kind == "web"
    )
    reworded = edited(case.model, "data_flows", stated, protocol="HTTP over TLS")
    invented = edited(case.model, "data_flows", stated, protocol="unknown")
    retyped = edited(case.model, "processes", web, interface_kind="non-web")

    assert score_of(case, reworded).differing == ()
    assert [
        (check.attribute, check.blessed, check.extracted)
        for check in score_of(case, invented).differing
    ] == [("protocol", "stated", "silent")]
    assert [
        (check.attribute, check.blessed, check.extracted)
        for check in score_of(case, retyped).differing
    ] == [("interface_kind", "web", "non-web")]


def test_an_extraction_that_produced_nothing_compares_no_attributes(case):
    score = score_of(case, None)

    assert score.attributes == ()
    assert score.scored_field_agreement == 0.0
    assert score.crossings_match is False


def test_end_to_end_mode_runs_the_production_entry(case):
    models: dict[str, ScriptedLlm] = {}
    pipeline = build(case, ENTRY_EXTRACT, models)

    report = asyncio.run(modes.run_end_to_end(case, pipeline)).report

    assert "extract" in models
    assert report_issues(report) == []


class TestAnEndToEndRunKeepsItsFirstPass:
    """#961 finding 14: the report holds the model after repair, and nothing else held the emissions."""

    def broken(self, case):
        raw = case.model.model_dump(mode="json")
        raw["data_flows"][0]["destination"] = "process:does-not-exist"
        return raw

    def test_a_valid_first_pass_is_the_emission_with_no_repair(self, case):
        pipeline = build(case, ENTRY_EXTRACT, {})

        run = asyncio.run(modes.run_end_to_end(case, pipeline))

        assert run.extraction is not None
        assert run.extraction.raw == case.model.model_dump(mode="json")
        assert run.extraction.repair is None
        assert run.extraction.issues == ()

    def test_a_repaired_run_keeps_what_extract_and_repair_each_emitted(self, case):
        models: dict[str, ScriptedLlm] = {}
        pipeline = build(case, ENTRY_EXTRACT, models)
        broken = self.broken(case)
        models["extract"].reply = json.dumps(broken)
        repaired = case.model.model_dump(mode="json")
        models["repair"].reply = json.dumps(repaired)

        run = asyncio.run(modes.run_end_to_end(case, pipeline))

        assert run.report.model_repair is not None
        assert run.extraction is not None
        assert run.extraction.raw == broken
        assert run.extraction.repair == repaired
        assert [issue.code for issue in run.extraction.issues] == ["invalid-reference"]

    def test_a_refused_model_rides_out_with_the_failure(self, case):
        """The failure that is a measurement keeps the emission it measured."""
        models: dict[str, ScriptedLlm] = {}
        pipeline = build(case, ENTRY_EXTRACT, models)
        broken = self.broken(case)
        models["extract"].reply = json.dumps(broken)
        models["repair"].reply = json.dumps(broken)

        with pytest.raises(modes.CaseFailure, match="rejected the model") as raised:
            asyncio.run(modes.run_end_to_end(case, pipeline))

        assert raised.value.extraction is not None
        assert raised.value.extraction.raw == broken
        assert raised.value.extraction.repair == broken

    def test_the_analysis_mode_has_no_first_pass(self, case):
        pipeline = build(case, ENTRY_PREPARE, {})

        run = asyncio.run(modes.run_analysis(case, pipeline))

        assert run.extraction is None


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
        "assertions": ENTRY_ASSERT_ONLY,
        "analysis": ENTRY_PREPARE,
        "end-to-end": ENTRY_EXTRACT,
        "heads": ENTRY_HEAD_ONLY,
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
        """The default is every framework the case declares."""
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
    ``flow:entity:card-processor>process:storefront-api>settlement-webhook`` and both
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
            missing=("flow:process:a>process:b>settlement-webhook",),
            extra=("flow:process:a>process:b>payment-webhook",),
        )

        assert s.recall == 0.0
        assert s.endpoint_recall == 1.0
        assert s.endpoint_missing == frozenset()
        assert s.endpoint_extra == frozenset()

    def test_a_genuinely_missed_flow_stays_missed(self):
        """Folding the label must not forgive an endpoint pair nobody emitted."""
        s = self.score(
            missing=("flow:process:a>process:b>x",),
            extra=("flow:process:c>process:d>y",),
        )

        assert s.endpoint_missing == frozenset({"flow:process:a>process:b"})
        assert s.endpoint_extra == frozenset({"flow:process:c>process:d"})
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
        s = self.score(
            matched=("process:a", "flow:process:a>process:b>x"), missing=("store:c",)
        )

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
        case = load_case(CORPUS / "01-payments-checkout")
        keys = modes.crossing_keys(case.model)

        assert keys
        assert all(key.startswith("flow:") for key in keys)
        # The label is gone and both endpoints survive, asked of the model's own
        # flows rather than of a segment count: what a flow ID's segments are is
        # the identity version's business, and a test counting colons pinned one
        # version's spelling as the property.
        assert set(keys) == {
            f"flow:{flow.source}{FLOW_DELIMITER}{flow.destination}"
            for flow in case.model.data_flows
            if modes._endpoint_key(flow.id) in keys
        }
        assert not any(
            flow_label(flow.id) in key for flow in case.model.data_flows for key in keys
        )

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
        from analysis_service.system_model import SystemModel

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
            matched=("entity:developer",),
            missing=("process:store-server",),
            extra=(),
            crossings_match=False,
            attributes=(),
            alignment=Alignment(
                (Pair("entity:developer", "entity:developer", "exact"),),
                ("process:store-server",),
                (),
            ),
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

        It does not raise — it returns kind ``other`` — and that is the
        argument for the separate branch rather than against it.
        `GroundsFailure` feeds the grounds instrument, whose subject is what a
        case's drafts did. A case whose model was refused produced no drafts, so
        counting it there inflates a grounds number with a non-grounds event.
        """
        from evals.harness.grounds import classify_failure

        filed = classify_failure(
            "09", modes.EvalRunError("09: the graph rejected the model")
        )

        assert filed.kind == "other"

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
        # One reader classifies a failed case however it failed, so the order
        # lives in that reader's body rather than in two except clauses.
        body = source.split("def record_failure(", 1)[1]
        refused = body.index("isinstance(error, modes.EvalRunError)")
        fan_in = body.index("isinstance(error, CAUGHT)")

        assert refused < fan_in
        assert "return" in body[refused:fan_in]


def test_a_node_that_raises_mid_graph_still_hands_the_sweep_what_ran(case, monkeypatch):
    """A raised lane leaves the case priced for what ran (#711).

    The executor carries the finishes before the fault out as
    ``GraphFailed``, and ``run_graph`` turns that into the one failure type the
    sweep prices from, so a case that fails inside a lane joins the usage block
    the same way a case that fails at report validation does (#707). The fault
    is a provider error on one lane, the shape a live sweep meets; a malformed
    reply is the fan-in's to survive and never reaches the executor.
    """
    models: dict[str, ScriptedLlm] = {}
    pipeline = build(case, ENTRY_PREPARE, models)

    async def refused(self, llm_request, stream: bool = False):
        raise RuntimeError("provider refused")
        yield  # an async generator, as the adapter's method is

    monkeypatch.setattr(LaneAwareLlm, "generate_content_async", refused)

    with pytest.raises(modes.CaseFailure, match="provider refused") as raised:
        asyncio.run(modes.run_analysis(case, pipeline))

    ran = {run.node: run for run in raised.value.node_runs}
    lanes = {analyze_node_name("stride", category) for category in STRIDE_CATEGORIES}
    assert "prepare" in ran, "the nodes before the fault are what the provider billed"
    # ADK tags its error event with the failed node's path, so a lane that
    # raised rides out too, with nothing metered on it: the floor the type
    # promises, never a figure that looks like a spend.
    assert all(ran[node].usage is None for node in lanes & set(ran))
    assert isinstance(raised.value.cause, RuntimeError)


class TestZonesAreScoredApartFromCitableElements:
    """A trust zone reaches no claim, so it is not counted as extraction recall.

    ``identity.comparable_elements`` drops every ``boundary:`` ID before any
    claim comparison, so a zone the extraction named differently costs no match
    downstream. Counting one as a miss depressed endpoint recall by eleven
    points over the sweep of 2026-09-13. The zones still get a number, because
    the crossings derive from them.
    """

    def test_a_renamed_zone_does_not_move_endpoint_recall(self, case):
        whole = modes.score_extraction(
            case, modes.ExtractionResult(case.id, case.model, ())
        )
        renamed = _rename_one_boundary(case)
        after = modes.score_extraction(
            case, modes.ExtractionResult(case.id, renamed, ())
        )

        assert whole.endpoint_recall == after.endpoint_recall == 1.0

    def test_but_it_does_move_zone_recall(self, case):
        renamed = _rename_one_boundary(case)
        after = modes.score_extraction(
            case, modes.ExtractionResult(case.id, renamed, ())
        )

        assert after.zone_recall < 1.0


def _rename_one_boundary(case):
    """The case's model with one trust zone renamed, references and all.

    A rename rather than a deletion: a model missing a zone its elements point
    at is invalid, and the question here is what a *differently named* zone
    costs, which is the only thing extraction actually does to them.
    """
    raw = case.model.model_dump()
    old = raw["trust_boundaries"][0]["id"]
    new = old + "-zone"
    raw["trust_boundaries"][0]["id"] = new
    raw["trust_boundaries"][0]["name"] = raw["trust_boundaries"][0]["name"] + " zone"
    for collection in ("external_entities", "processes", "data_stores"):
        for element in raw.get(collection, []):
            if element.get("trust_zone") == old:
                element["trust_zone"] = new
    return type(case.model).model_validate(raw)


class TestInventionIsScoredApartFromTheCorpusGap:
    """An extra element the source never names is the only kind of invention.

    Precision counts three different things as one: a component the model
    invented, a component the corpus omits, and the model's own word for a
    component the corpus paraphrased. Only the first is the model's fault, and
    only the first is decidable — against the submitted bytes (#882).
    """

    def test_an_element_the_source_names_is_not_invention(self, case):
        raw = case.model.model_dump()
        first = raw["processes"][0]
        extra = dict(first, id="process:invented", name=first["name"] + " replica")
        raw["processes"].append(extra)
        widened = type(case.model).model_validate(raw)
        score = modes.score_extraction(
            case, modes.ExtractionResult(case.id, widened, ())
        )
        assert score.extra, "the widened model should carry an extra element"
        # "replica" appears in no corpus source, so this one is invention.
        assert score.name_tokens_absent_from_source == score.extra

    def test_the_reading_is_empty_when_nothing_was_added(self, case):
        score = modes.score_extraction(
            case, modes.ExtractionResult(case.id, case.model, ())
        )
        assert score.extra == ()
        assert score.name_tokens_absent_from_source == ()


def test_the_named_types_are_the_registry_less_flows_and_zones():
    """A table nobody compares to its registry fails as quietly as a branch.

    ``Element`` is the closed set and each type carries its own ``id_prefix``,
    so a sixth added tomorrow belongs to this population unless somebody rules
    it out. Listed by hand it would sit silently outside both readers — the
    invention question and the extra-element split.
    """
    from typing import get_args

    from analysis_service.system_model import DataFlow, Element, TrustBoundary
    from evals.harness.modes import NAMED_TYPES

    every = {element.id_prefix for element in get_args(Element)}
    coined = {DataFlow.id_prefix, TrustBoundary.id_prefix}

    assert set(NAMED_TYPES) == every - coined
    assert coined <= every, "the two coined types are element types"


class TestAnExtraElementIsACandidateOrUnreviewedAndNeverARename:
    """Precision counts every extra alike; what a figure may say about one is little.

    Which blessed element an extra one renames is a reader's ruling, read by
    the alignment. Without one, the most a figure can say is that an extra's
    type has an unaligned blessed element, which makes it a *candidate* — and
    one unaligned store licenses twenty candidates, which is why the list is
    not a count (#961). Every extra the alignment did not pair is
    ``unreviewed``; nothing here calls it an invention or a corpus gap.
    """

    def widened(self, case, collection, element):
        raw = case.model.model_dump()
        raw[collection].append(element)
        return modes.score_extraction(
            case,
            modes.ExtractionResult(case.id, type(case.model).model_validate(raw), ()),
        )

    def test_an_extra_of_a_fully_aligned_type_is_no_candidate(self, case):
        """Every blessed process is present, so an extra process renames nothing."""
        first = case.model.model_dump()["processes"][0]
        score = self.widened(
            case,
            "processes",
            dict(first, id="process:added", name=first["name"] + " replica"),
        )

        assert score.missing == ()
        assert score.same_type_unmatched_candidates == ()
        assert score.extra_status == {"process:added": "unreviewed"}

    def test_an_extra_of_a_displaced_type_is_a_candidate(self, case):
        """One blessed process dropped and another added: the second could be
        the first under a name this scorer may not guess at, and stays
        unreviewed until a reader rules."""
        raw = case.model.model_dump()
        dropped = raw["processes"][0]["id"]
        model = type(case.model).model_validate(raw)
        score = modes.score_extraction(
            case,
            modes.ExtractionResult(case.id, _renamed_process(model, dropped), ()),
        )

        assert dropped in score.alignment.unaligned_reference
        assert score.same_type_unmatched_candidates == score.extra
        assert set(score.extra_status.values()) == {"unreviewed"}

    def test_one_unaligned_element_licenses_every_same_type_extra(self):
        """The audit's probe: one missing store, twenty unrelated extra stores.

        Twenty candidates, because type is all the list reads; and twenty
        ``unreviewed``, because nothing here may call them renames or
        additions (#961 finding 1).
        """
        extras = tuple(f"store:invented-{index}" for index in range(20))
        score = modes.ExtractionScore(
            "synthetic",
            (),
            ("store:real",),
            extras,
            False,
            (),
            alignment=Alignment((), ("store:real",), extras),
        )

        assert len(score.same_type_unmatched_candidates) == 20
        assert set(score.extra_status.values()) == {"unreviewed"}
        assert "additions" not in score.to_json()
        assert "possible_renames" not in score.to_json()

    def test_a_displaced_type_does_not_excuse_another_type(self, case):
        """A missing process says nothing about an extra store."""
        raw = case.model.model_dump()
        dropped = raw["processes"].pop(0)
        store = dict(raw["data_stores"][0], id="store:added", name="added store")
        raw["data_stores"].append(store)
        for flow in list(raw.get("data_flows", ())):
            if dropped["id"] in (flow["source"], flow["destination"]):
                raw["data_flows"].remove(flow)
        score = modes.score_extraction(
            case,
            modes.ExtractionResult(case.id, type(case.model).model_validate(raw), ()),
        )

        assert "store:added" not in score.same_type_unmatched_candidates
        assert score.extra_status["store:added"] == "unreviewed"

    def test_an_aligned_extra_is_equivalent_and_no_candidate(self):
        """An extra the alignment paired is a blessed element under another name."""
        case = load_case(CORPUS / "03-batch-data-pipeline")
        raw = case.model.model_dump()
        for process in raw["processes"]:
            if process["id"] == "process:ingest-scheduler":
                # The name moves with the ID, because the alias rule reads the
                # name and a gate-passing model derives one from the other.
                process["id"] = "process:airflow-scheduler"
                process["name"] = "Airflow scheduler"
        for flow in raw["data_flows"]:
            for end in ("source", "destination"):
                if flow[end] == "process:ingest-scheduler":
                    flow[end] = "process:airflow-scheduler"
        score = modes.score_extraction(
            case,
            modes.ExtractionResult(case.id, type(case.model).model_validate(raw), ()),
        )

        assert score.extra_status["process:airflow-scheduler"] == "equivalent"
        assert "process:airflow-scheduler" not in score.same_type_unmatched_candidates

    def test_a_coined_flow_label_is_not_a_name_a_submitter_writes(self, case):
        """``named_extra`` and ``_name_tokens_absent`` ask one population, one reader.

        107 of the 136 extra elements a run were flows on the sweep of
        2026-09-13, so a split over the whole of ``extra`` reads the labels the
        model coins rather than the corpus.
        """
        raw = case.model.model_dump()
        flow = dict(raw["data_flows"][0])
        flow["id"] = make_flow_id(flow["source"], flow["destination"], "another label")
        flow["name"] = "another label"
        raw["data_flows"].append(flow)
        score = modes.score_extraction(
            case,
            modes.ExtractionResult(case.id, type(case.model).model_validate(raw), ()),
        )

        assert flow["id"] in score.extra
        assert flow["id"] not in score.named_extra
        assert score.name_tokens_absent_from_source == ()


def _renamed_process(model, element_id):
    """The model with one process re-identified, and its flows re-pointed."""
    raw = model.model_dump()
    new = element_id + "-under-another-name"
    for process in raw["processes"]:
        if process["id"] == element_id:
            process["id"] = new
    for flow in raw.get("data_flows", ()):
        for end in ("source", "destination"):
            if flow[end] == element_id:
                flow[end] = new
    return type(model).model_validate(raw)


def test_a_coined_flow_label_is_never_invention(case):
    """A flow's name is the model's label for an interaction, not the text's word.

    Asking whether the source contains `dashboards-query-telemetry-lake` reads
    41 ordinary flow labels a run as invented components. Only an entity, a
    process or a data store is a thing a submitter names.
    """
    raw = case.model.model_dump()
    flow = dict(raw["data_flows"][0])
    flow["id"] = make_flow_id(
        flow["source"], flow["destination"], "a label no source contains"
    )
    flow["name"] = "a label no source contains"
    raw["data_flows"].append(flow)
    widened = type(case.model).model_validate(raw)
    score = modes.score_extraction(case, modes.ExtractionResult(case.id, widened, ()))

    assert score.extra, "the widened model should carry an extra flow"
    assert score.name_tokens_absent_from_source == ()


def test_a_plural_in_the_source_covers_a_singular_name(case):
    """The source's "game servers" sources a model's `game server`.

    Which form to write is the naming rule's question and is already measured
    as recall. Charging it here as invention counts one disagreement twice, and
    it read three ordinary plurals as invented components before this.
    """
    raw = case.model.model_dump()
    first = raw["processes"][0]
    plural = first["name"] if first["name"].endswith("s") else first["name"] + "s"
    raw["processes"].append(dict(first, id="process:pluralised", name=plural))
    widened = type(case.model).model_validate(raw)

    score = modes.score_extraction(case, modes.ExtractionResult(case.id, widened, ()))

    assert score.extra, "the widened model should carry an extra process"
    assert score.name_tokens_absent_from_source == ()


# --- The citation half of the gate, which this mode runs with its sources -----


def _extraction_of(case, model) -> modes.ExtractionResult:
    """Drive ``run_extraction`` over a scripted extraction of ``model``."""
    models: dict[str, ScriptedLlm] = {}
    pipeline = build(case, ENTRY_EXTRACT_ONLY, models)
    models["extract"].reply = json.dumps(model.model_dump(mode="json"))
    return asyncio.run(modes.run_extraction(case, pipeline))


def test_extraction_mode_checks_excerpts_against_the_sources(case):
    """The gate rule production runs, which this mode ran without its input.

    ``run_extraction`` called ``parse_and_validate`` with no ``sources``, and
    :func:`analysis_service.validation._citation_issues` returns early when
    there are none. So an invented quote passed here and failed inside a job,
    and the mode graded extractions against a weaker gate than the one that
    ships.
    """
    invented = edited(
        case.model,
        "processes",
        0,
        source_excerpt="the orbital telemetry relay aboard the spacecraft",
    )

    result = _extraction_of(case, invented)

    assert [issue.code for issue in result.issues] == ["unverifiable-excerpt"]
    assert result.issues[0].element_id == case.model.processes[0].id


def test_a_faithful_extraction_still_passes_the_citation_check(case):
    """The positive control: the blessed model quotes its own sources correctly.

    Without this, a citation check that raised on everything would look exactly
    like one that works.
    """
    result = _extraction_of(case, case.model)

    assert result.issues == ()


def test_an_invented_quote_is_scored_and_is_not_a_tier_one_failure(case):
    """Where a citation failure goes: onto the score, not into the failure list.

    Production routes such a model to ``repair``; this mode stops before that
    pass, so failing the sweep would report the repair pass's ordinary input as
    a malformed model. The number says how much work that pass is left.
    """
    invented = edited(
        case.model, "processes", 0, source_excerpt="a sentence from no source"
    )
    result = _extraction_of(case, invented)

    score = modes.score_extraction(case, result)

    assert [issue.code for issue in score.uncited] == ["unverifiable-excerpt"]
    assert score.to_json()["uncited"][0]["element_id"] == case.model.processes[0].id
    # Nothing else moved: the model is complete and correct apart from the quote.
    assert score.recall == 1.0 and score.precision == 1.0


class TestASupportedNameIsNamedDifferentlyRatherThanMissed:
    """Two standards, and the corpus's `aliases` is the seam between them.

    *Semantic fidelity* asks whether a name identifies the described thing;
    *naming-policy conformity* asks whether it keeps the source's wording. An
    extraction writing `process:airflow-scheduler` where the corpus wrote
    `process:ingest-scheduler` passes the first and fails the second, and one
    number charges it as though it found nothing (#882).
    """

    CASE = "03-batch-data-pipeline"

    def renamed(self, element: dict, element_id: str) -> None:
        """Give one element the ID asked for, **and the name it derives from**.

        A model the gate passed carries an ID its own name derives, and the
        alias rule reads the name. Moving the ID alone would build a model no
        extraction can emit, and would ask the alias rule a question about a
        spelling nothing wrote.
        """
        element["id"] = element_id
        element["name"] = element_id.partition(":")[2].replace("-", " ")

    def scored(self, renames):
        """The blessed model with elements re-named, as a model naming them
        its own way would emit."""
        from evals.harness.reference import load_case

        case = load_case(
            Path(__file__).resolve().parents[1] / "evals" / "corpus" / self.CASE
        )
        raw = case.model.model_dump()
        for collection in ("external_entities", "processes", "data_stores"):
            for element in raw.get(collection, []):
                if element["id"] in renames:
                    self.renamed(element, renames[element["id"]])
        for flow in raw.get("data_flows", []):
            for end in ("source", "destination"):
                flow[end] = renames.get(flow[end], flow[end])
        model = type(case.model).model_validate(raw)
        return case, modes.score_extraction(
            case, modes.ExtractionResult(case.id, model, ())
        )

    ZONE_CASE = "01-payments-checkout"

    def zoned(self, renames):
        """The same, over trust zones, which every zoned element names."""
        from evals.harness.reference import load_case

        case = load_case(
            Path(__file__).resolve().parents[1] / "evals" / "corpus" / self.ZONE_CASE
        )
        raw = case.model.model_dump()
        for boundary in raw.get("trust_boundaries", []):
            if boundary["id"] in renames:
                self.renamed(boundary, renames[boundary["id"]])
        for collection in ("external_entities", "processes", "data_stores"):
            for element in raw.get(collection, []):
                zone = element.get(ZONE_ATTRIBUTE)
                if zone in renames:
                    element[ZONE_ATTRIBUTE] = renames[zone]
        model = type(case.model).model_validate(raw)
        return case, modes.score_extraction(
            case, modes.ExtractionResult(case.id, model, ())
        )

    def test_the_source_s_own_word_is_credited_as_the_same_element(self):
        """`Airflow scheduler` is what the text calls it, in five runs of five."""
        _, score = self.scored(
            {"process:ingest-scheduler": "process:airflow-scheduler"}
        )

        assert "process:ingest-scheduler" in score.missing
        assert score.sourced_recall > score.endpoint_recall
        assert [credit.reference for credit in score.aliased] == [
            "process:ingest-scheduler"
        ]
        assert score.naming_departures == 1

    def test_the_credit_carries_the_words_that_support_it(self):
        """A reader meeting the higher number checks the ruling from the
        artifact rather than opening the corpus at the sweep's commit."""
        _, score = self.scored(
            {"process:ingest-scheduler": "process:airflow-scheduler"}
        )

        assert (
            score.aliased[0].excerpt
            == "An Airflow scheduler running in the landing network"
        )
        assert score.to_json()["aliased"][0]["excerpt"]

    def test_a_plural_is_the_same_name(self):
        """`extract.md` asks for the singular of the source's word, so an alias
        taken from the source is compared in the singular. Charging the plural
        as a different component counts one disagreement twice."""
        _, plural = self.scored({"entity:data-analyst": "entity:analysts"})
        _, singular = self.scored({"entity:data-analyst": "entity:analyst"})

        assert plural.naming_departures == singular.naming_departures == 1

    def test_an_unsupported_name_is_still_missed(self):
        """The mechanism credits a ruled name and nothing else. No fuzzy match."""
        _, score = self.scored({"process:ingest-scheduler": "process:nightly-job"})

        assert score.aliased == ()
        assert score.sourced_recall == score.endpoint_recall

    def test_an_exact_extraction_needs_no_credit(self):
        _, score = self.scored({})

        assert score.aliased == ()
        assert score.sourced_recall == score.endpoint_recall == 1.0

    def test_a_zone_the_reader_ruled_moves_only_the_zone_figure(self):
        """A ruled zone reaches `sourced_zone_recall` and nothing else.

        `comparable_elements` drops every zone, so a credit on one cannot touch
        `sourced_recall` — which leaves `sourced_zone_recall` as the only figure
        a reader's ruling on a zone can move.
        """
        _, score = self.zoned({"boundary:public-internet": "boundary:internet"})

        assert [credit.reference for credit in score.aliased] == [
            "boundary:public-internet"
        ]
        assert score.sourced_zone_recall > score.zone_recall
        assert score.sourced_recall == score.endpoint_recall

    def test_an_unruled_zone_reads_the_strict_zone_number(self):
        _, score = self.zoned({"boundary:core-services": "boundary:middle-earth"})

        assert score.aliased == ()
        assert score.sourced_zone_recall == score.zone_recall

    def test_the_zone_test_and_the_claim_comparison_name_one_population(self):
        """Two readers of "is this a zone", held to each other.

        `modes._is_zone` decides which credits reach the zone figure and
        `identity.comparable_elements` decides which elements a claim can cite.
        A disagreement would put a credited element in neither figure or both.
        """
        from evals.harness.reference import load_corpus

        corpus = Path(__file__).resolve().parents[1] / "evals" / "corpus"
        ids = [
            element.id
            for case in load_corpus(corpus)
            for element in case.model.elements()
        ]
        assert ids

        by_is_zone = {element_id for element_id in ids if modes._is_zone(element_id)}
        by_comparison = set(ids) - set(comparable_elements(ids))

        assert by_is_zone == by_comparison

    def test_a_case_nobody_ruled_on_reads_the_strict_number(self):
        """An absent ruling is not a ruling that every name is the only one.

        The unruled case is found rather than named, because a reader may rule
        on any case at any time and that is not a reason for this to fail. It
        fails loudly when every case carries a ruling, since nothing then
        measures what this says it measures.
        """
        from evals.harness.reference import load_corpus

        corpus = Path(__file__).resolve().parents[1] / "evals" / "corpus"
        case = next(
            (
                candidate
                for candidate in load_corpus(corpus)
                if not candidate.meta.aliases and candidate.model.processes
            ),
            None,
        )
        assert case is not None, "every corpus case now carries a ruling"
        raw = case.model.model_dump()
        raw["processes"][0]["id"] = "process:something-else"
        for flow in raw.get("data_flows", []):
            for end in ("source", "destination"):
                if flow[end] == case.model.processes[0].id:
                    flow[end] = "process:something-else"
        score = modes.score_extraction(
            case,
            modes.ExtractionResult(case.id, type(case.model).model_validate(raw), ()),
        )

        assert score.aliased == ()
        assert score.sourced_recall == score.endpoint_recall


class TestTheFalsificationFixtures:
    """Ten mutations of a blessed model, and what each figure says about them.

    **A scorer is only as good as the wrong answers it refuses.** Every
    mutation here is a model a person would reject on sight, run through
    ``score_extraction`` with the case's real sources. Each is pinned to the
    figure that catches it, so improving the scorer cannot quietly stop
    catching one, and the three nothing catches are pinned as open rather than
    left to be rediscovered (#925).

    The mutations are the audit's own program, which is what makes them a
    regression test rather than a fixture somebody wrote to pass.
    """

    def score(self, case, model):
        result = modes.ExtractionResult(
            case.id,
            model,
            tuple(validate(model, sources={s.label: s.text for s in case.sources})),
        )
        return modes.score_extraction(case, result)

    def case_01(self):
        return load_case(CORPUS / "01-payments-checkout")

    def test_a_deleted_parallel_flow_costs_an_interaction(self):
        """Case 13's console reaches its API two ways; drop one.

        ``endpoint_recall`` folds a flow's label and then takes a *set*, so the
        pair survives and the figure does not move. The lost WebSocket is a
        whole handshake, session and transport surface, not a naming
        difference, and ``interaction_recall`` is the reading that says so.
        """
        case = load_case(CORPUS / "13-dispatch-control-plane")
        model = case.model.model_copy(deep=True)
        model.data_flows = [
            flow for flow in model.data_flows if "live-job-status" not in flow.id
        ]

        score = self.score(case, model)

        assert score.endpoint_recall == 1.0
        assert score.interaction_recall < 1.0
        assert score.crossings_match is False

    def test_renaming_the_hard_elements_no_longer_buys_agreement(self):
        """Agreement cannot be bought by renaming the facts that are hard to get right.

        ``_check_attributes`` joins on the alignment. Renaming case 01's
        five flows once took all 25 of their scored fields out of the
        numerator *and* the denominator, and agreement read perfect over the
        22 left. Rule 6 pairs each renamed flow as the sole flow between its
        found endpoints, so the nonsense on it is compared and charged.
        """
        case = self.case_01()
        model = case.model.model_copy(deep=True)
        for index, flow in enumerate(model.data_flows):
            flow.name = f"interaction {index}"
            flow.protocol = "arbitrary nonsense"
            flow.authentication = "arbitrary nonsense"
            flow.encryption_in_transit = "arbitrary nonsense"
            flow.operations = "unknown"
        model = normalize_element_ids(model)

        score = self.score(case, model)

        assert score.scored_field_agreement < 1.0
        assert score.attributes_compared == score.attributes_comparable
        assert {pair.evidence for pair in score.alignment.pairs} == {"exact", "sole"}

    def test_collapsing_every_zone_moves_the_partition_not_the_name(self):
        """One zone for everything keeps every zone name the reference holds."""
        case = self.case_01()
        model = case.model.model_copy(deep=True)
        for element in model.zoned_elements():
            element.trust_zone = "boundary:public-internet"

        score = self.score(case, model)

        assert score.zone_recall == 1.0
        assert score.sourced_zone_recall == 1.0
        assert score.zone_partition_agreement < 0.5

    def test_a_zone_for_every_element_is_charged_as_invented_crossings(self):
        """The opposite failure, and recall alone rewards it.

        Give each element its own new zone and keep the originals empty: every
        blessed crossing is still derived, so ``crossings_recall`` reads 1.0
        over a model that separated everything from everything.
        """
        case = self.case_01()
        model = case.model.model_copy(deep=True)
        for index, element in enumerate(model.zoned_elements()):
            zone = model.trust_boundaries[0].model_copy(deep=True)
            zone.name = f"zone {index}"
            zone.id = f"boundary:zone-{index}"
            model.trust_boundaries.append(zone)
            element.trust_zone = zone.id

        score = self.score(case, model)

        assert score.zone_recall == 1.0
        assert score.crossings_recall == 1.0
        assert score.crossings_precision < 1.0
        assert score.zone_partition_agreement < 1.0

    def test_erasing_every_citation_fails_the_gate_and_reads_as_unmeasured(self):
        """The composition of schema, gate and diagnostic, tested as one.

        Each piece was correct alone: the gate checks the citations it is
        given, and the diagnostic passes over an element citing nothing because
        the gate refuses that shape. Blanking the fields is what falls between
        them.
        """
        case = self.case_01()
        model = case.model.model_copy(deep=True)
        for element in model.elements():
            element.source_excerpt = ""
            element.source_label = ""
            for attribute in (
                "authentication",
                "encryption_in_transit",
                "encryption_at_rest",
            ):
                value = getattr(element, attribute, None)
                if isinstance(value, str) and control_state(value) == "stated":
                    setattr(element, attribute, "arbitrary nonsense")

        score = self.score(case, model)

        assert [issue.code for issue in score.uncited] == ["missing-citation"] * len(
            model.elements()
        )
        assert score.unbased == ()
        assert score.basis_coverage.measured == 0
        assert score.basis_coverage.uncited == score.basis_coverage.stated

    def test_a_mechanism_of_function_words_reads_as_unmeasured(self):
        """``the`` is a stated control on every mechanical check there is."""
        case = self.case_01()
        model = case.model.model_copy(deep=True)
        for element in model.elements():
            for attribute in (
                "authentication",
                "encryption_in_transit",
                "encryption_at_rest",
            ):
                value = getattr(element, attribute, None)
                if isinstance(value, str) and control_state(value) == "stated":
                    setattr(element, attribute, "the")

        score = self.score(case, model)

        assert score.unbased == ()
        assert score.basis_coverage.measured == 0
        assert score.basis_coverage.tokenless == score.basis_coverage.stated

    def test_an_invented_mechanism_is_flagged_by_the_diagnostic(self):
        """The one semantic failure a mechanical figure does reach."""
        case = self.case_01()
        model = case.model.model_copy(deep=True)
        for element in model.elements():
            for attribute in (
                "protocol",
                "authentication",
                "encryption_in_transit",
                "encryption_at_rest",
                "data_classification",
            ):
                value = getattr(element, attribute, None)
                if isinstance(value, str) and control_state(value) == "stated":
                    setattr(element, attribute, "arbitrary nonsense")

        score = self.score(case, model)

        assert score.scored_field_agreement == 1.0
        assert score.control_state_agreement == 1.0
        assert len(score.unbased) == 5
        assert score.basis_coverage.flagged == 5


class TestThePartitionFigureIsReadThreeWays:
    """Finding 6 of #961: one pooled agreement hid which kind of pair was lost.

    A pair correctly kept apart and a pair correctly kept together count alike
    in ``zone_partition_agreement``, and a pair with a dropped element is
    asked nothing. The three readings beside it are the co-memberships alone,
    the separations alone, and how much of the reference either was asked
    over.
    """

    def score(self, case, model):
        return TestTheFalsificationFixtures().score(case, model)

    def case_01(self):
        return load_case(CORPUS / "01-payments-checkout")

    def test_one_zone_for_everything_loses_the_separations_alone(self):
        case = self.case_01()
        model = case.model.model_copy(deep=True)
        for element in model.zoned_elements():
            element.trust_zone = "boundary:public-internet"

        score = self.score(case, model)

        assert score.same_zone_recall == 1.0
        assert score.different_zone_agreement == 0.0
        assert score.zone_pair_coverage == 1.0

    def test_a_zone_for_every_element_loses_the_co_memberships_alone(self):
        case = self.case_01()
        model = case.model.model_copy(deep=True)
        for index, element in enumerate(model.zoned_elements()):
            zone = model.trust_boundaries[0].model_copy(deep=True)
            zone.name = f"zone {index}"
            zone.id = f"boundary:zone-{index}"
            model.trust_boundaries.append(zone)
            element.trust_zone = zone.id

        score = self.score(case, model)

        assert score.same_zone_recall == 0.0
        assert score.different_zone_agreement == 1.0

    def test_dropping_elements_reads_as_lost_coverage_not_lost_agreement(self):
        """Half the zoned elements gone: a perfect partition over the rest."""
        case = self.case_01()
        model = case.model.model_copy(deep=True)
        zoned = model.zoned_elements()
        dropped = {element.id for element in zoned[: len(zoned) // 2]}
        model.external_entities = [
            e for e in model.external_entities if e.id not in dropped
        ]
        model.processes = [e for e in model.processes if e.id not in dropped]
        model.data_stores = [e for e in model.data_stores if e.id not in dropped]
        model.data_flows = [
            f for f in model.data_flows if not dropped & {f.source, f.destination}
        ]

        score = self.score(case, model)

        assert score.zone_partition_agreement == 1.0
        assert score.zone_pair_coverage < 0.5
        assert score.zone_pairs is not None
        assert score.zone_pairs.reference_total > score.zone_pairs.compared

    def test_an_aliased_element_is_still_asked(self):
        """The alignment reaches the partition too: case 09's ruled name."""
        case = load_case(CORPUS / "09-cookbook-sokify-retail")
        model = case.model.model_copy(deep=True)
        next(
            e for e in model.processes if e.id == "process:catalogue-spreadsheet"
        ).name = "Spreadsheet"
        model = normalize_element_ids(model)

        score = self.score(case, model)

        assert score.zone_pair_coverage == 1.0
        assert score.zone_partition_agreement == 1.0

    def test_the_three_readings_are_serialised_beside_the_pooled_one(self):
        case = self.case_01()
        payload = self.score(case, case.model).to_json()

        assert payload["zone_partition_agreement"] == 1.0
        assert payload["same_zone_recall"] == 1.0
        assert payload["different_zone_agreement"] == 1.0
        assert payload["zone_pair_coverage"] == 1.0

    def test_no_extraction_reads_zero_on_every_reading(self):
        score = modes.ExtractionScore("x", (), (), (), False, ())

        assert score.zone_pairs is None
        assert score.zone_partition_agreement == 0.0
        assert score.same_zone_recall == 0.0
        assert score.different_zone_agreement == 0.0
        assert score.zone_pair_coverage == 0.0


class TestWhatNoFigureHereReaches:
    """Three mutations every number in this module scores as perfect.

    Pinned rather than left implicit. Each needs a judgement about *meaning* —
    whether a value follows from a source, and whether support for one fact
    supports another — and no reduction of a string to a state can supply it.
    The audit's plan puts them behind per-assertion support (#741), and these
    tests are what will fail, loudly, on the day that lands (#925).

    Read as a specification of the remaining gap, not as behaviour worth
    keeping.
    """

    def score(self, case, model):
        return TestTheFalsificationFixtures().score(case, model)

    def case_01(self):
        return load_case(CORPUS / "01-payments-checkout")

    def perfect(self, score):
        return (
            score.recall == 1.0
            and score.scored_field_agreement == 1.0
            and score.control_state_agreement == 1.0
            and score.comparison_coverage == 1.0
            and score.zone_partition_agreement == 1.0
            and score.crossings_precision == 1.0
            and score.interaction_recall == 1.0
            and score.unbased == ()
            and score.uncited == ()
        )

    def test_a_control_reversed_into_its_own_contradiction_still_scores_clean(self):
        """The source says MFA is not rolled out; say the opposite in its words.

        Both values lead with a mechanism rather than a sentinel, so
        ``control_state`` reads both as ``stated``. The words are the source's
        own, so the basis diagnostic finds them.
        """
        case = self.case_01()
        model = case.model.model_copy(deep=True)
        flow = next(f for f in model.data_flows if "place-order" in f.id)
        flow.authentication = "session cookie; MFA enforced for every shopper"

        assert self.perfect(self.score(case, model))

    def test_a_real_quote_about_something_else_still_scores_clean(self):
        """Every excerpt replaced by the source's opening line.

        The quote verifies, because it is really in the source. Whether it has
        anything to do with the element citing it is the question nothing here
        asks.
        """
        case = self.case_01()
        model = case.model.model_copy(deep=True)
        for element in model.elements():
            element.source_excerpt = (
                "Checkout and order-capture path for our storefront."
            )
            element.description = ""
            element.notes = ""
        model.assumptions = []

        assert self.perfect(self.score(case, model))

    def test_a_control_borrowed_from_another_flow_loses_only_two_fields(self):
        """The receipt archive's controls, moved onto the card-processor webhook.

        The diagnostic searches the whole cited source rather than the
        element's own excerpt, so a mechanism the source describes for another
        connection echoes here too. The evidence catalog quietly drops the two
        unknown entries that webhook would otherwise carry.
        """
        case = self.case_01()
        model = case.model.model_copy(deep=True)
        flow = next(f for f in model.data_flows if "settlement-webhook" in f.id)
        flow.authentication = "order service's own service account"
        flow.encryption_in_transit = "TLS"

        score = self.score(case, model)
        lost = set(evidence_catalog(case.model)) - set(evidence_catalog(model))

        assert score.unbased == ()
        assert score.uncited == ()
        assert len(score.differing) == 2
        assert len(lost) == 2


class TestAStateAgreementIsJoinedToItsBasis:
    """#891's reproduction, driven: nonsense agrees, and the join says so.

    A state-reduced comparison folds a whole mechanism to one of three words, so
    a literal string reads ``stated`` and agrees with a real control. The
    ``unbased`` diagnostic already saw that and no figure tied the two together.
    """

    NONSENSE = "arbitrary nonsense"

    def _nonsense_model(self, case):
        """The case's own model with every stated scored value replaced."""
        raw = case.model.model_dump(mode="json")
        replaced = 0
        for group in (
            "external_entities",
            "processes",
            "data_stores",
            "data_flows",
        ):
            for element in raw.get(group, []):
                for field in modes.STATE_REDUCED:
                    if field in element and control_state(str(element[field])) == (
                        "stated"
                    ):
                        element[field] = self.NONSENSE
                        replaced += 1
        assert replaced, "the fixture must replace something"
        return SystemModel.model_validate(raw), replaced

    def _score(self, case, extracted):
        unbased, coverage = modes._basis(case, extracted)
        return replace(
            modes.score_extraction(
                case,
                modes.ExtractionResult(case_id=case.id, extracted=extracted, issues=()),
            ),
            unbased=unbased,
            basis_coverage=coverage,
        )

    def test_the_agreement_still_reads_one(self, case):
        """The defect, kept: a state comparison cannot see the nonsense."""
        nonsense, _ = self._nonsense_model(case)

        score = self._score(case, nonsense)

        assert score.scored_field_agreement == 1.0
        assert score.control_state_agreement == 1.0

    def test_the_join_charges_every_agreement_the_source_does_not_support(self, case):
        nonsense, _ = self._nonsense_model(case)

        score = self._score(case, nonsense)

        assert score.state_agrees_unbased == len(score.unbased)
        assert score.state_agrees_unbased > 0
        assert score.to_json()["state_agrees_unbased"] == score.state_agrees_unbased

    def test_the_blessed_model_charges_nothing(self):
        """A positive control: the reference agrees with itself and is based."""
        for case in load_corpus(CORPUS):
            score = self._score(case, case.model)

            assert score.state_agrees_unbased == 0, case.id

    def test_the_unchecked_fields_are_derived_from_both_registries(self):
        """Named nowhere: a field leaving either registry moves this with it."""
        assert modes.STATE_UNCHECKED == modes.STATE_REDUCED - frozenset(IN_SCOPE)
        assert modes.STATE_UNCHECKED, "a blind spot nobody can see is not reportable"
        assert not modes.STATE_UNCHECKED & frozenset(IN_SCOPE)

    def test_a_field_with_no_basis_check_is_outside_the_join(self):
        """So the count cannot imply a coverage the diagnostic does not have."""
        case = load_corpus(CORPUS)[0]
        nonsense, replaced = self._nonsense_model(case)

        score = self._score(case, nonsense)

        # Every replaced value agreed; only the checkable ones can be charged.
        assert score.state_agrees_unbased < replaced
