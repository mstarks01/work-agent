"""What a prompt edit did, read back off two finished artifacts.

Credential-free by construction, like the stability tests beside it: both
artifacts already hold what their nodes were told and what their scores were, so
these drive the real loader over real artifact shapes rather than a hand-built
object graph.

## What is under test

ADR 0016 said a raise that improves findings and a deletion that costs them look
alike to a sweep. #279 recorded the independent variable; this is the reading
that puts it beside the dependent ones. So the assertions below are about
**joining** two sweeps correctly — which nodes moved, which numbers moved, and
what happens when the two sweeps do not describe the same graph.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.harness.artifact import ARTIFACT_VERSION, DECLARED_KEYS, load_artifact
from evals.harness.instruction_delta import (
    artifact as delta_artifact,
)
from evals.harness.instruction_delta import (
    measurement_deltas,
    node_deltas,
)
from evals.harness.provenance import RunProvenance


def row(framework: str, node: str, tokens: int, sha: str) -> dict:
    """One instruction row, as ``NodeInstruction.to_json`` writes it."""
    return {
        "framework": framework,
        "node": node,
        "tokens": tokens,
        "sha256": sha * 64,
    }


def write_run(tmp_path: Path, name: str, instruction: list[dict], **overrides) -> Path:
    """A complete artifact, the way a sweep writes one.

    Every declared key is present because every sweep writes every declared key.
    A fixture carrying only what its own assertions read would be a shape no
    sweep produces, and the loader is strict about that on purpose.
    """
    payload = {
        **dict.fromkeys(DECLARED_KEYS),
        "artifact_version": ARTIFACT_VERSION,
        "mode": "analysis",
        "cases": ["01-payments-checkout"],
        "trusted": True,
        "structural_failures": [],
        "repo_commit": {"commit": "0" * 40, "clean": True},
        "corpus_digest": "0" * 64,
        "provenance": RunProvenance(
            build={},
            sampling_config_version=1,
            tiers_config_version=1,
            sampling={},
            node_runs={},
        ).to_json(),
        "models": {"tiers": {"strong": "openai/gpt-4.1"}, "tiers_config_version": "3"},
        "instruction": instruction,
        "instruction_totals": {},
        "scores": [],
        "applicability": [],
    } | overrides
    path = tmp_path / name
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


@pytest.fixture
def one_lane_grew(tmp_path):
    """One lane's instruction gains 300 tokens; the rest of the graph stands still."""
    before = write_run(
        tmp_path,
        "before.json",
        [
            row("stride", "analyze_stride_spoofing", 7_983, "a"),
            row("stride", "critic_stride", 4_326, "b"),
            row("(shared)", "extract", 2_383, "c"),
        ],
        coverage_totals={"drafts": 40},
    )
    after = write_run(
        tmp_path,
        "after.json",
        [
            row("stride", "analyze_stride_spoofing", 8_283, "d"),
            row("stride", "critic_stride", 4_326, "b"),
            row("(shared)", "extract", 2_383, "c"),
        ],
        coverage_totals={"drafts": 44},
    )
    return load_artifact(before), load_artifact(after)


class TestTheInstructionSide:
    def test_only_the_edited_node_is_reported_as_moved(self, one_lane_grew):
        """The point of the reading: which node, not merely that something changed."""
        before, after = one_lane_grew
        moved = [row for row in node_deltas(before, after) if row.moved]

        assert [row.node for row in moved] == ["analyze_stride_spoofing"]
        assert moved[0].delta == 300

    def test_an_unchanged_node_reports_no_delta(self, one_lane_grew):
        before, after = one_lane_grew
        rows = {row.node: row for row in node_deltas(before, after)}

        assert rows["critic_stride"].delta == 0
        assert not rows["critic_stride"].moved

    def test_a_reworded_node_moves_without_growing(self, tmp_path):
        """``moved`` is the digest, not the count, and the two differ.

        An edit that swaps a sentence for one the same length changes what the
        agent reads and not how much of it. A reading keyed on tokens alone
        would call that sweep unchanged.
        """
        before = load_artifact(
            write_run(tmp_path, "b.json", [row("stride", "critic_stride", 4_326, "a")])
        )
        after = load_artifact(
            write_run(tmp_path, "a.json", [row("stride", "critic_stride", 4_326, "z")])
        )

        (only,) = node_deltas(before, after)
        assert only.moved
        assert only.delta == 0

    def test_a_node_on_one_side_only_is_reported(self, tmp_path):
        """The union, because a graph that gained a lane is the largest change.

        A package registering, or a lane arriving, adds nodes that no
        intersection would show — and that is exactly the edit whose effect a
        reader most wants beside the numbers.
        """
        before = load_artifact(
            write_run(tmp_path, "b.json", [row("stride", "critic_stride", 4_326, "a")])
        )
        after = load_artifact(
            write_run(
                tmp_path,
                "a.json",
                [
                    row("stride", "critic_stride", 4_326, "a"),
                    row("asvs", "critic_asvs", 4_055, "q"),
                ],
            )
        )

        rows = {row.node: row for row in node_deltas(before, after)}
        assert rows["critic_asvs"].before is None
        assert rows["critic_asvs"].moved
        assert rows["critic_asvs"].delta is None


class TestTheMeasurementSide:
    def test_a_moved_number_is_reported_with_both_sides(self, one_lane_grew):
        """What moved beside the edit, which is the whole reason for the pairing."""
        before, after = one_lane_grew
        moved = measurement_deltas(before, after)

        drafts = [row for row in moved if row.path == "coverage_totals.drafts"]
        assert len(drafts) == 1
        assert (drafts[0].before, drafts[0].after, drafts[0].delta) == (40.0, 44.0, 4.0)

    def test_the_instruction_keys_are_not_reported_as_measurements(self, one_lane_grew):
        """The independent variable is not one of the things that moved with it.

        ``instruction`` holds numbers, so a generic numeric walk would flatten
        3 nodes' token counts into the score table and read them as outcomes.
        """
        before, after = one_lane_grew
        paths = {row.path for row in measurement_deltas(before, after)}

        assert not [path for path in paths if path.startswith("instruction")]

    def test_two_identical_sweeps_report_nothing(self, tmp_path):
        """No edit, no delta — the reading a healthy re-run gives."""
        rows = [row("stride", "critic_stride", 4_326, "a")]
        before = load_artifact(write_run(tmp_path, "b.json", rows))
        after = load_artifact(write_run(tmp_path, "a.json", rows))

        assert not [item for item in node_deltas(before, after) if item.moved]
        assert not measurement_deltas(before, after)


class TestTheRecord:
    def test_the_artifact_carries_only_what_moved(self, one_lane_grew):
        """A reader wants the edit, not the graph. The denominator rides beside it."""
        before, after = one_lane_grew
        record = delta_artifact(
            node_deltas(before, after), measurement_deltas(before, after)
        )

        assert [row["node"] for row in record["instruction_delta"]] == [
            "analyze_stride_spoofing"
        ]
        assert record["instruction_nodes"] == 3
        assert record["measurement_delta"]
