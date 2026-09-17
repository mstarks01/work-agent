"""The bounded source review: what the graph carries, and what the nodes write.

#1003's arms C and D are one pass over two heads, so the first group drives all
four arms out of the deployment and reads the node list each one builds. The
second holds what the builder refuses. The third drives the three nodes — the
reading that renders what the pass sees, the applicator that patches or
discards, and the seam ``prepare`` resolves through — and the last says the
seam gates an injected record rather than trusting it.
"""

from typing import Any

import pytest

from analysis_service import graph
from analysis_service.assertions import (
    Assertion,
    AssertionCatalog,
    AssertionRecord,
    Subject,
    assertion_id,
)
from analysis_service.deployment import (
    ASSERTIONS_VAR,
    FACTS_FIRST_EXTRACTION_VAR,
    SOURCE_REVIEW_VAR,
    Deployment,
)
from analysis_service.evidence import render_rows
from analysis_service.factbundle import FactProposal, MentionProposal
from analysis_service.graph import FACTS_FIRST
from analysis_service.patch import Operation, PatchBatch, Precondition
from analysis_service.prompts import compose_reread_prompt
from analysis_service.system_model import SystemModel
from tests.test_deployment import VERTEX_ENV
from tests.test_facts_route import (
    FRAMEWORKS,
    LABEL,
    NOTE,
    PROMPTS,
    built,
    bundle,
    quote,
)
from tests.test_graph import KEYS, FakeContext, nodes_by_name

SOURCES = {LABEL: NOTE}

#: Every node the head of a graph can carry, so a topology reads as a list.
HEAD = (
    graph.EXTRACT_NODE,
    graph.FACTS_NODE,
    graph.RESOLVE_NODE,
    graph.READ_MODEL_NODE,
    graph.ASSERT_NODE,
    graph.READ_CATALOG_NODE,
    graph.REREAD_NODE,
    graph.APPLY_NODE,
    graph.PREPARE_NODE,
)


def arm(**flags: str) -> list[str]:
    """The head one install builds, in the order a valid model passes through."""
    pipeline = Deployment.from_env(env=VERTEX_ENV | flags).pipeline(FRAMEWORKS)
    carried = set(nodes_by_name(pipeline))
    return [name for name in HEAD if name in carried]


def resolved(**overrides: Any):
    """One bundle resolved, for the model and the proposal a review reads."""
    from analysis_service.factbundle import resolve_bundle

    return resolve_bundle(bundle(), SOURCES, **overrides)


def reading_context():
    """A context holding what ``reading`` binds, with that node already run."""
    resolution = resolved()
    ctx = FakeContext(
        valid_model=resolution.model.model_dump(mode="json"),
        assertion_proposal=resolution.proposal.model_dump(mode="json"),
        source_texts=SOURCES,
    )
    graph.read_for_review(ctx, KEYS, ctx.state["valid_model"], SOURCES)
    return ctx


class TestTheFourArms:
    """Each arm's head, driven out of the variables an operator sets."""

    def test_arm_a_is_the_route_with_the_assertion_pass(self) -> None:
        assert arm(**{ASSERTIONS_VAR: "true"}) == [
            graph.EXTRACT_NODE,
            graph.READ_MODEL_NODE,
            graph.ASSERT_NODE,
            graph.PREPARE_NODE,
        ]

    def test_arm_b_reads_facts_first_and_reviews_nothing(self) -> None:
        assert arm(**{FACTS_FIRST_EXTRACTION_VAR: "true"}) == [
            graph.FACTS_NODE,
            graph.RESOLVE_NODE,
            graph.PREPARE_NODE,
        ]

    def test_arm_c_adds_the_review_to_the_assertion_pass(self) -> None:
        assert arm(**{ASSERTIONS_VAR: "true", SOURCE_REVIEW_VAR: "true"}) == [
            graph.EXTRACT_NODE,
            graph.READ_MODEL_NODE,
            graph.ASSERT_NODE,
            graph.READ_CATALOG_NODE,
            graph.REREAD_NODE,
            graph.APPLY_NODE,
            graph.PREPARE_NODE,
        ]

    def test_arm_d_adds_the_same_review_to_the_facts_first_head(self) -> None:
        assert arm(
            **{FACTS_FIRST_EXTRACTION_VAR: "true", SOURCE_REVIEW_VAR: "true"}
        ) == [
            graph.FACTS_NODE,
            graph.RESOLVE_NODE,
            graph.READ_CATALOG_NODE,
            graph.REREAD_NODE,
            graph.APPLY_NODE,
            graph.PREPARE_NODE,
        ]

    def test_c_and_d_carry_the_same_three_review_nodes(self) -> None:
        """One pass for both arms, so a comparison measures the head alone."""
        review = {graph.READ_CATALOG_NODE, graph.REREAD_NODE, graph.APPLY_NODE}
        c = set(arm(**{ASSERTIONS_VAR: "true", SOURCE_REVIEW_VAR: "true"}))
        d = set(arm(**{FACTS_FIRST_EXTRACTION_VAR: "true", SOURCE_REVIEW_VAR: "true"}))
        assert review <= c
        assert review <= d
        assert c - d == {graph.EXTRACT_NODE, graph.READ_MODEL_NODE, graph.ASSERT_NODE}

    def test_the_review_resolves_on_the_repair_tier(self) -> None:
        """It is a bounded repair pass, so it runs where the other one runs."""
        tiers = graph.tier_node_by_graph_node(FRAMEWORKS)
        assert tiers[graph.REREAD_NODE] == tiers[graph.REPAIR_NODE]

    def test_the_prompt_carries_both_rendered_tables(self) -> None:
        composed = compose_reread_prompt(PROMPTS)
        assert "## The roles" in composed
        assert "## The predicates" in composed
        assert "retract-assertion" in composed


class TestWhatTheBuilderRefuses:
    """A review nothing feeds and a review nothing reads."""

    def test_a_graph_that_produces_no_catalog(self) -> None:
        with pytest.raises(ValueError, match="builds no node that produces one"):
            built(source_review=True)

    def test_an_entry_that_ends_in_no_catalog(self) -> None:
        with pytest.raises(ValueError, match="ends in no catalog"):
            built(
                entry=graph.ENTRY_EXTRACT_ONLY,
                extraction_strategy=FACTS_FIRST,
                source_review=True,
            )

    def test_an_install_asking_for_one_on_a_route_with_no_catalog(self) -> None:
        """The deployment computes the builder's rule rather than tripping it."""
        assert arm(**{SOURCE_REVIEW_VAR: "true"}) == [
            graph.EXTRACT_NODE,
            graph.PREPARE_NODE,
        ]


class TestTheReadingNode:
    """What the review is shown, and what it leaves for the applicator."""

    def test_it_renders_the_model_and_every_row(self) -> None:
        ctx = reading_context()
        assert graph.STATE_SYSTEM_MODEL in ctx.state
        rows = ctx.state[graph.STATE_ASSERTION_ROWS]
        assert "network-membership" in rows
        assert "1 statements" in rows

    def test_it_parks_a_record_the_applicator_patches(self) -> None:
        ctx = reading_context()
        record = AssertionRecord.model_validate(
            ctx.state[graph.STATE_ASSERTION_CATALOG]
        )
        assert record.issues == []
        assert [entry.subject for entry in record.catalog.entries] == ["store:queue"]

    def test_a_missing_proposal_is_named(self) -> None:
        resolution = resolved()
        ctx = FakeContext(source_texts=SOURCES)
        with pytest.raises(graph.SilentNodeError, match="assertion_proposal"):
            graph.read_for_review(
                ctx, KEYS, resolution.model.model_dump(mode="json"), SOURCES
            )

    def test_every_row_is_keyed_by_the_identity_a_patch_names(self) -> None:
        resolution = resolved()
        catalog = resolution.record.catalog
        rendered = render_rows(catalog)
        for entry in catalog.entries:
            assert assertion_id(entry) in rendered


class TestTheApplyNode:
    """What lands, what does not, and what the artifacts hold either way."""

    def batch(self, *operations: Operation) -> dict:
        return PatchBatch(operations=list(operations)).model_dump(mode="json")

    def adds_the_worker(self) -> tuple[Operation, Operation]:
        """The two operations that add the component the base graph never named."""
        return (
            Operation(
                handle="o1",
                kind="add-element",
                reason="the source names a worker and the graph has none",
                preconditions=[Precondition(state="absent", target="process:worker")],
                element=MentionProposal(
                    handle="ignored",
                    text="worker",
                    roles=["process"],
                    quotes=quote("A worker"),
                ),
            ),
            Operation(
                handle="o2",
                kind="add-assertion",
                reason="the source places the worker",
                assertion=FactProposal(
                    handle="ignored",
                    subject_kind="mention",
                    subject="o1",
                    predicate="network-membership",
                    value="boundary:core-network",
                    basis="stated",
                    quotes=quote(NOTE),
                ),
            ),
        )

    def test_it_patches_the_model_and_the_record(self) -> None:
        ctx = reading_context()
        report = graph.apply_source_review(
            ctx,
            KEYS,
            ctx.state["valid_model"],
            self.batch(*self.adds_the_worker()),
            SOURCES,
        )
        patched = SystemModel.model_validate(ctx.state[graph.STATE_VALID_MODEL])

        assert report["rolled_back"] is False
        assert report["applied"] == 2
        assert patched.get("process:worker") is not None
        record = AssertionRecord.model_validate(
            ctx.state[graph.STATE_ASSERTION_CATALOG]
        )
        assert {entry.subject for entry in record.catalog.entries} == {
            "store:queue",
            "process:worker",
        }

    def test_an_empty_batch_leaves_the_artifacts_alone(self) -> None:
        ctx = reading_context()
        before = ctx.state[graph.STATE_ASSERTION_CATALOG]
        graph.apply_source_review(
            ctx, KEYS, ctx.state["valid_model"], self.batch(), SOURCES
        )
        after = AssertionRecord.model_validate(ctx.state[graph.STATE_ASSERTION_CATALOG])
        assert after.catalog == AssertionRecord.model_validate(before).catalog
        assert ctx.state[graph.STATE_PATCH_OUTCOMES]["outcomes"] == []

    def test_a_refused_operation_is_reported_and_changes_nothing(self) -> None:
        ctx = reading_context()
        stale = Operation(
            handle="o1",
            kind="add-element",
            reason="the source names a worker",
            preconditions=[Precondition(state="present", target="process:worker")],
            element=MentionProposal(
                handle="ignored",
                text="worker",
                roles=["process"],
                quotes=quote("A worker"),
            ),
        )
        graph.apply_source_review(
            ctx, KEYS, ctx.state["valid_model"], self.batch(stale), SOURCES
        )
        outcomes = ctx.state[graph.STATE_PATCH_OUTCOMES]

        assert outcomes["rolled_back"] is False
        assert [row["code"] for row in outcomes["outcomes"]] == ["stale-precondition"]
        patched = SystemModel.model_validate(ctx.state[graph.STATE_VALID_MODEL])
        assert patched.get("process:worker") is None

    def test_a_silent_review_is_named(self) -> None:
        ctx = reading_context()
        with pytest.raises(graph.SilentNodeError, match="patch_batch"):
            graph.apply_source_review(
                ctx, KEYS, ctx.state["valid_model"], None, SOURCES
            )


class TestTheSeam:
    """``prepare`` gates whatever reaches it, whichever node produced it."""

    def test_an_injected_record_is_gated_rather_than_trusted(self) -> None:
        """A row naming an element the model does not hold comes back refused."""
        resolution = resolved()
        planted = AssertionRecord(
            proposed=1,
            catalog=AssertionCatalog(
                subjects=[Subject(id="store:ledger", type="component", label="ledger")],
                entries=[
                    Assertion(
                        subject="store:ledger",
                        predicate="storage-encryption",
                        value="AES-256",
                        basis="inferred",
                        explanation="planted",
                    )
                ],
            ),
        )
        ctx = FakeContext(
            assertion_catalog=planted.model_dump(mode="json"), source_texts=SOURCES
        )
        record = graph._resolve_assertions(KEYS.state(ctx), resolution.model)

        assert "dangling-binding" in {issue.code for issue in record.issues}

    def test_a_proposal_still_resolves_where_no_record_was_left(self) -> None:
        resolution = resolved()
        ctx = FakeContext(
            assertion_proposal=resolution.proposal.model_dump(mode="json"),
            source_texts=SOURCES,
        )
        record = graph._resolve_assertions(KEYS.state(ctx), resolution.model)

        assert record.issues == []
        assert len(record.catalog.entries) == 1
