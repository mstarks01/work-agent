"""The instruction instrument reads what was built, for every package.

## What this pins

ADR 0016 made the token caps drift alarms and said plainly what it left open:
nothing measured prompt size, so a raise that improved findings and a deletion
that cost them looked alike to a sweep. This instrument is the answer, and what
makes it worth having is that the number is **the built instruction's**, not a
recomposition that could drift from it.

So the tests below build the real graph — repo prompts, repo package text, real
topology, scripted models — and assert the instrument reports what that graph
actually carries.

## Why these run offline

``scripted_pipeline`` binds every LLM node to a stand-in, so no credential and
no provider is involved. The instruction is composed at build time from the
repo's own text, which is exactly the thing under measurement.
"""

from __future__ import annotations

import pytest
from google.adk.agents import LlmAgent

from analysis_service.frameworks import PACKAGES
from analysis_service.graph import Pipeline
from analysis_service.markdown_loader import estimate_tokens
from analysis_service.report import FrameworkName
from evals.harness.instruction import SHARED, NodeInstruction, artifact, collect, totals
from tests.factories import scripted_pipeline


def pipeline_for(*frameworks: FrameworkName) -> Pipeline:
    """The real graph for one selection, with scripted models bound to it."""
    built, _ = scripted_pipeline({}, frameworks=list(frameworks))
    return built


class TestTheNumberIsTheBuiltInstructions:
    def test_every_row_matches_the_node_it_describes(self):
        """The size reported is the size of the text the node carries.

        The instrument could have recomposed the instruction from the loaders
        and produced a number that is right until composition changes. This is
        the assertion that says it did not: every row is checked against the
        built node's own ``instruction`` string.
        """
        built = pipeline_for(*PACKAGES)
        graph = built.workflow.graph
        nodes = {
            node.name: node.instruction
            for node in (graph.nodes if graph else ())
            if isinstance(node, LlmAgent)
        }

        assert nodes, "the built graph exposed no LLM node to check against"
        for row in collect([built]):
            assert row.tokens == estimate_tokens(nodes[row.node])

    def test_no_llm_node_goes_unmeasured(self):
        """Every node the graph built has a row, so nothing is silently missed."""
        built = pipeline_for(*PACKAGES)
        measured = {row.node for row in collect([built])}

        assert measured == set(built.node_instructions)


class TestEveryPackageIsRead:
    """Parity: the instrument answers for every registered package.

    Driven from ``PACKAGES`` rather than from a list of names, so a framework
    added to the registry is a framework this test starts requiring rows for.
    A reading that covered one package would be the gap
    ``docs/agents/framework-parity.md`` exists to catch, and it is the exact
    gap the token caps had before ADR 0016.
    """

    @pytest.mark.parametrize("framework", sorted(PACKAGES))
    def test_a_packages_own_nodes_carry_its_name(self, framework):
        rows = collect([pipeline_for(framework)])
        owned = [row for row in rows if row.framework == framework]

        assert owned, f"{framework} produced no instruction rows"
        # Its lanes, its critic and its re-ask: one row each, none missing.
        assert len(owned) == len(PACKAGES[framework].lanes) + 2

    @pytest.mark.parametrize("framework", sorted(PACKAGES))
    def test_a_packages_static_instruction_is_reported_whole(self, framework):
        """The fold is the framework's own total, and it is not empty."""
        fold = totals(collect([pipeline_for(framework)]))

        assert fold[framework]["nodes"] == len(PACKAGES[framework].lanes) + 2
        assert fold[framework]["tokens"] > 0
        assert fold[framework]["largest"] > 0

    def test_a_sweep_of_one_package_reports_no_other(self):
        """A framework nobody ran has no instruction to report.

        The opposite of the coverage table's rule, and deliberately: a silent
        lane is a finding, while a framework the sweep never built was never
        instructed at all, and a zero row would claim it was.
        """
        one, *rest = sorted(PACKAGES)
        reported = set(totals(collect([pipeline_for(one)])))

        assert one in reported
        assert not reported & set(rest)


class TestTheSharedNodesCarryNoFramework:
    def test_extraction_is_filed_as_shared(self):
        """One extraction serves every framework a job selected.

        Attributing it to a framework would double-count it the moment a sweep
        runs two, and picking one of them would be a claim the graph does not
        make.
        """
        rows = collect([pipeline_for(*PACKAGES)])
        shared = {row.node for row in rows if row.framework == SHARED}

        assert "extract" in shared

    def test_attribution_comes_from_the_tier_key(self):
        """Read off the registry the graph used, never parsed from a node name.

        A package whose nodes are named some new way still lands under the
        right heading, because the tier key is what the graph itself resolved
        the node on.
        """
        built = pipeline_for(*PACKAGES)

        for row in collect([built]):
            tier_key = built.tier_nodes[row.node]
            if row.framework == SHARED:
                assert "/" not in tier_key
            else:
                assert tier_key.endswith(f"/{row.framework}")


class TestOneNodeIsRecordedOnce:
    def test_a_node_built_under_two_selections_yields_one_row(self):
        """A sweep builds one graph per framework set; shared nodes repeat.

        ``extract`` is built by every selection. Recording it once per graph
        would report a sweep of two selections as carrying twice the extraction
        instruction it carries.
        """
        selections = [pipeline_for(name) for name in sorted(PACKAGES)]
        rows = collect(selections)
        names = [row.node for row in rows]

        assert len(names) == len(set(names))
        assert names.count("extract") == 1

    def test_two_graphs_disagreeing_about_a_node_raises(self):
        """The property the cacheable prefix rests on, asserted rather than assumed.

        One node's instruction is a fact about the built node, not about the
        selection that caused it to be built. If two graphs disagreed, the
        deduplication above would report whichever was seen last and hide the
        far more interesting finding.
        """
        first = pipeline_for(*PACKAGES)
        altered = first.node_instructions | {
            "extract": type(first.node_instructions["extract"])(
                tokens=1, sha256="0" * 64
            )
        }
        second = type(first)(
            workflow=first.workflow,
            node_models=first.node_models,
            tier_sampling=first.tier_sampling,
            node_sampling=first.node_sampling,
            instruction_sha256=first.instruction_sha256,
            node_instructions=altered,
            frameworks=first.frameworks,
            tier_nodes=first.tier_nodes,
        )

        with pytest.raises(ValueError, match="two different instructions"):
            collect([first, second])


class TestTheArtifactIsComparableAcrossSweeps:
    def test_a_prompt_edit_moves_one_row_and_leaves_the_rest(self):
        """The whole point: which node moved, and by how much.

        The pipeline-wide ``instruction_sha256`` answers *something changed*.
        This is the reading that says which node, which is what a reader
        holding two artifacts either side of a cap raise needs.
        """
        before = artifact(collect([pipeline_for(*PACKAGES)]))
        edited = [
            NodeInstruction(
                framework=row["framework"],
                node=row["node"],
                tokens=row["tokens"] + (500 if row["node"] == "extract" else 0),
                sha256=("f" * 64) if row["node"] == "extract" else row["sha256"],
            )
            for row in before["instruction"]
        ]
        after = artifact(edited)

        moved = [
            row["node"]
            for row, was in zip(
                after["instruction"], before["instruction"], strict=True
            )
            if row["sha256"] != was["sha256"]
        ]
        assert moved == ["extract"]
        assert after["instruction_totals"][SHARED]["tokens"] == (
            before["instruction_totals"][SHARED]["tokens"] + 500
        )

    def test_an_empty_sweep_still_writes_both_keys(self):
        """An absent block and an empty one are different claims to a reader."""
        assert artifact([]) == {"instruction": [], "instruction_totals": {}}
