"""Reading a head-only sweep back: which arm it ran, and what grades it.

Two tables answer two questions that gave one answer while a mode's reading
node and its kept emission moved together. #1003's head-only mode reads through
`extract` or through `facts` — which of them is the very thing separating its
arms — and keeps a catalog either way. These hold each table to its own
question, and drive the reader that grades such a sweep off the catalog its own
run gated rather than off the blessed model it was never shown.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from analysis_service.assertions import (
    Assertion,
    AssertionCatalog,
    Subject,
)
from evals.harness import modes, replay
from evals.harness.bundle import heads_from_reports


def catalog() -> AssertionCatalog:
    """A catalog naming an element only a head-only run's own model would hold."""
    return AssertionCatalog(
        subjects=[Subject(id="store:job-queue", type="component", label="job queue")],
        entries=[
            Assertion(
                subject="store:job-queue",
                predicate="storage-encryption",
                value="absent",
                basis="stated",
            )
        ],
    )


def sweep(tmp_path: Path, corpus_case) -> Path:
    """One archived head-only sweep: an artifact path and the files beside it."""
    artifact = tmp_path / "heads.json"
    artifact.write_text("{}", encoding="utf-8")
    reports = tmp_path / "heads.reports"
    reports.mkdir()
    (reports / f"{corpus_case.id}.assertions.json").write_text(
        json.dumps(
            {
                "proposal": {"assertions": []},
                "catalog": catalog().model_dump(mode="json"),
                "issues": [],
            }
        ),
        encoding="utf-8",
    )
    return artifact


class TestTheTwoTables:
    """Which node places an arm, and which emission a replay grades."""

    def test_the_head_only_mode_reads_through_either_node(self) -> None:
        assert replay.NODE_OF["heads"] == ("extract", "facts")

    def test_every_replayable_mode_says_what_it_keeps(self) -> None:
        """A mode in one table and not the other is a replay that reads nothing."""
        assert set(replay.KEEPS) == set(replay.NODE_OF)

    def test_what_a_mode_keeps_is_one_of_two_emissions(self) -> None:
        assert set(replay.KEEPS.values()) == {"extraction", "catalog"}

    def test_every_replayable_mode_is_a_mode(self) -> None:
        assert set(replay.NODE_OF) <= set(modes.MODE_ENTRIES)


class TestPlacingAHeadOnlySweep:
    """An arm is the reading node the sweep actually carried."""

    def artifact(self, *nodes: str):
        class Fake:
            mode = "heads"
            path = Path("heads.json")

            def block(self, name: str):
                if name == "instruction":
                    return [
                        {"node": node, "sha256": f"{node}-digest"} for node in nodes
                    ]
                return {
                    "node_runs": {
                        node: [{"requested_model": "a/model"}] for node in nodes
                    }
                }

        return Fake()

    @pytest.mark.parametrize("node", ("extract", "facts"))
    def test_it_is_placed_by_the_node_it_carried(self, node: str) -> None:
        arm = replay.arm_of(self.artifact(node, "repair"))
        assert arm.node == node
        assert arm.instruction == f"{node}-digest"

    def test_a_sweep_carrying_both_is_refused(self) -> None:
        """Two reading nodes is two arms, and a sweep is one."""
        with pytest.raises(ValueError, match="cannot be placed on"):
            replay.arm_of(self.artifact("extract", "facts"))

    def test_a_sweep_carrying_neither_is_refused(self) -> None:
        with pytest.raises(ValueError, match="cannot be placed on"):
            replay.arm_of(self.artifact("repair"))


class TestGradingAHeadOnlySweep:
    """The archived catalog, because the blessed model was never shown."""

    def test_it_reads_the_catalog_the_run_gated(
        self, tmp_path: Path, corpus_case
    ) -> None:
        artifact = sweep(tmp_path, corpus_case)
        found = heads_from_reports(artifact, [corpus_case])

        assert set(found) == {corpus_case.id}
        assert [entry.subject for entry in found[corpus_case.id].catalog.entries] == [
            "store:job-queue"
        ]

    def test_it_keeps_a_row_the_blessed_model_has_no_element_for(
        self, tmp_path: Path, corpus_case
    ) -> None:
        """Re-resolving would bind nothing and read as an arm that found nothing."""
        artifact = sweep(tmp_path, corpus_case)
        found = heads_from_reports(artifact, [corpus_case])
        blessed = {element.id for element in corpus_case.model.elements()}

        assert "store:job-queue" not in blessed
        assert found[corpus_case.id].catalog.entries
