"""Turning archived arm sweeps into the file the comparison reads.

The step between the pilot and the report, and it grades nothing of its own:
the replay decides which reference rows a run answered and `ArmRun.of` narrows
them to the stated rows the denominator counts. What these hold is the shape
around that — which arm an artifact is placed on is stated by the caller and
never guessed, a sweep of another mode is refused, and a case whose reference
nobody signed is skipped and named rather than scored against a draft.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from analysis_service.assertions import Assertion, AssertionCatalog, Subject
from evals.harness.arms import ARMS, load_runs
from evals.harness.score_arms import command_score_arms, parse_spec
from tests.eval_factories import sweep_document, write_sweep_document


def sweep(tmp_path: Path, corpus_case, name: str = "A-r1") -> Path:
    """One archived head-only sweep for one case, as the pilot writes them."""
    artifact = write_sweep_document(
        tmp_path / f"{name}.json",
        sweep_document(cases=(corpus_case.id,)) | {"mode": "heads"},
    )
    reports = tmp_path / f"{name}.reports"
    reports.mkdir(exist_ok=True)
    (reports / f"{corpus_case.id}.assertions.json").write_text(
        json.dumps(
            {
                "proposal": {"assertions": []},
                "catalog": AssertionCatalog(
                    subjects=[
                        Subject(id="store:queue", type="component", label="queue")
                    ],
                    entries=[
                        Assertion(
                            subject="store:queue",
                            predicate="storage-encryption",
                            value="absent",
                            basis="stated",
                        )
                    ],
                ).model_dump(mode="json"),
                "issues": [],
            }
        ),
        encoding="utf-8",
    )
    return artifact


class TestTheSpec:
    """Which arm a sweep ran is stated, because no artifact carries it."""

    def test_an_arm_and_a_path(self) -> None:
        assert parse_spec("A=/tmp/a.json") == ("A", 0, Path("/tmp/a.json"))

    def test_an_arm_a_repeat_and_a_path(self) -> None:
        assert parse_spec("D:3=/tmp/d.json") == ("D", 3, Path("/tmp/d.json"))

    @pytest.mark.parametrize("arm", sorted(ARMS))
    def test_every_declared_arm_parses(self, arm: str) -> None:
        assert parse_spec(f"{arm}=/tmp/x.json")[0] == arm

    @pytest.mark.parametrize(
        "spec", ("/tmp/a.json", "A=", "Z=/tmp/a.json", "A:later=/tmp/a.json")
    )
    def test_a_spec_this_cannot_read_is_refused(self, spec: str) -> None:
        with pytest.raises(ValueError):
            parse_spec(spec)


class TestScoring:
    """What reaches the runs file, and what is named as skipped instead."""

    def run(self, tmp_path: Path, *specs: str) -> int:
        return command_score_arms(
            argparse.Namespace(
                artifact=list(specs),
                corpus=Path("evals") / "corpus",
                out=tmp_path / "runs.json",
            )
        )

    def test_a_signed_case_reaches_the_runs_file(
        self, tmp_path: Path, corpus_case
    ) -> None:
        artifact = sweep(tmp_path, corpus_case)
        assert self.run(tmp_path, f"A:2={artifact}") == 0

        runs = load_runs(tmp_path / "runs.json")
        assert [(run.arm, run.repeat, run.case_id) for run in runs] == [
            ("A", 2, corpus_case.id)
        ]
        assert runs[0].required > 0

    def test_two_arms_reach_it_under_their_own_names(
        self, tmp_path: Path, corpus_case
    ) -> None:
        first = sweep(tmp_path, corpus_case, "A-r1")
        second = sweep(tmp_path, corpus_case, "B-r1")
        assert self.run(tmp_path, f"A={first}", f"B={second}") == 0

        runs = load_runs(tmp_path / "runs.json")
        assert sorted(run.arm for run in runs) == ["A", "B"]

    def test_a_sweep_of_another_mode_is_refused(
        self, tmp_path: Path, corpus_case, capsys
    ) -> None:
        artifact = sweep(tmp_path, corpus_case)
        write_sweep_document(
            artifact,
            sweep_document(cases=(corpus_case.id,)) | {"mode": "assertions"},
        )
        assert self.run(tmp_path, f"A={artifact}") == 1
        assert "is not one arm's head" in capsys.readouterr().err

    def test_a_case_the_sweep_ran_and_lost_stays_in_the_denominator(
        self, tmp_path: Path, corpus_case
    ) -> None:
        """#1003: never silently remove a failed case from the denominator."""
        from evals.harness.reference import load_corpus

        other = "04-ml-inference-service"
        second = next(
            case for case in load_corpus(Path("evals") / "corpus") if case.id == other
        )
        # One case archives a catalog; the other ran and lost its model, so the
        # sweep names it and no file sits beside the artifact for it.
        sweep(tmp_path, second)
        artifact = write_sweep_document(
            tmp_path / "A-r1.json",
            sweep_document(cases=(corpus_case.id, other)) | {"mode": "heads"},
        )
        assert self.run(tmp_path, f"A={artifact}") == 0

        runs = {run.case_id: run for run in load_runs(tmp_path / "runs.json")}
        assert set(runs) == {corpus_case.id, other}
        lost = runs[corpus_case.id]
        assert not lost.valid
        assert lost.recall == 0.0
        assert lost.required > 0, "the case's own denominator still counts"

    def test_a_case_nobody_signed_is_named_rather_than_scored(
        self, tmp_path: Path, capsys
    ) -> None:
        """A run graded against a draft reference is graded against nothing."""
        from evals.harness.reference import load_corpus

        drafted = next(
            case
            for case in load_corpus(Path("evals") / "corpus")
            if case.id == "02-iot-fleet-telemetry"
        )
        artifact = sweep(tmp_path, drafted)
        assert self.run(tmp_path, f"A={artifact}") == 1
        assert "no sweep held a case with a signed reference" in capsys.readouterr().err
