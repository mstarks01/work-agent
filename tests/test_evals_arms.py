"""#1003's offline arm comparison: the endpoint, the interval and the report.

Four groups. The arms table is held to the graph it names, so an arm whose
variables no longer build the head it claims fails here rather than producing a
number for a route nobody ran. The endpoint group drives the three rules that
make it one number — what the denominator counts, that a failed run scores zero
inside it, and that repeats average before cases do. The interval group holds
the bootstrap to the properties a reader rests on: it resamples cases rather
than runs, it is the same on every machine, and it widens with the family. The
last group drives the file the comparison is re-run from, which fails closed on
a vocabulary it does not know.
"""

import json
from pathlib import Path

import pytest

from analysis_service import graph
from analysis_service.assertions import (
    Assertion,
    AssertionCatalog,
    Subject,
    assertion_id,
)
from analysis_service.deployment import Deployment
from evals.harness.arms import (
    ARMS,
    ARTIFACT_VERSION,
    BOOTSTRAP_SEED,
    COMPARISONS,
    CORRECTED_LEVEL,
    LEVEL,
    ArmRun,
    ArmsError,
    case_recall,
    command_compare_arms,
    comparisons,
    corrected_level,
    failure_rate,
    load_runs,
    macro_recall,
    paired_difference,
    report,
    required_rows,
    to_json,
    unsupported_rate,
    write_runs,
)
from evals.harness.replay import PRODUCED_FATES, ROW_FATES, SignedReference
from tests.test_deployment import VERTEX_ENV
from tests.test_facts_route import FRAMEWORKS
from tests.test_graph import nodes_by_name
from tests.test_source_review import HEAD


def row(value: str, predicate: str = "storage-encryption", **overrides) -> Assertion:
    """One reference row, stated unless a test says otherwise."""
    return Assertion.model_validate(
        {
            "subject": "store:queue",
            "predicate": predicate,
            "value": value,
            "basis": "stated",
            **overrides,
        }
    )


def reference(*rows: Assertion) -> SignedReference:
    """A signed reference over the rows a test names."""
    return SignedReference(
        catalog=AssertionCatalog(
            subjects=[Subject(id="store:queue", type="component", label="queue")],
            entries=list(rows),
        )
    )


def run(
    case_id: str,
    arm: str,
    found: int,
    required: int,
    *,
    repeat: int = 0,
    valid: bool = True,
    unreviewed: int = 0,
    matched: int = 0,
) -> ArmRun:
    """One run with the fates a test is about and zeroes everywhere else."""
    fates: dict[str, int] = dict.fromkeys(ROW_FATES, 0)
    fates["found"] = found
    fates["omitted"] = required - found
    produced: dict[str, int] = dict.fromkeys(PRODUCED_FATES, 0)
    produced["unreviewed"] = unreviewed
    produced["matched"] = matched
    return ArmRun(
        case_id=case_id,
        arm=arm,
        repeat=repeat,
        required=required,
        fates=fates,
        produced=produced,
        valid=valid,
    )


class TestTheArmsTable:
    """Each arm's variables against the head the graph builds for them."""

    @pytest.mark.parametrize("arm", sorted(ARMS))
    def test_an_arm_builds_the_head_it_names(self, arm: str) -> None:
        pipeline = Deployment.from_env(
            env=VERTEX_ENV | dict(ARMS[arm].variables)
        ).pipeline(FRAMEWORKS)
        carried = set(nodes_by_name(pipeline))

        assert [name for name in HEAD if name in carried] == list(ARMS[arm].head)

    def test_every_planned_comparison_names_two_arms(self) -> None:
        for left, right in COMPARISONS:
            assert left in ARMS
            assert right in ARMS
            assert left != right

    def test_the_correction_reads_the_plan(self) -> None:
        """A comparison added to the plan widens every interval."""
        assert CORRECTED_LEVEL == corrected_level(COMPARISONS)
        assert CORRECTED_LEVEL > LEVEL
        assert corrected_level((("B", "A"),)) == LEVEL

    def test_the_report_names_the_level_its_own_plan_earns(self) -> None:
        """The interval and the sentence beside it read one function."""
        runs = [run(case, arm, 5, 10) for case in ("01", "02") for arm in "AB"]
        one = (("B", "A"),)
        assert f"{corrected_level(one):.4f}" in report(runs, one)
        assert f"corrected for {len(one)} planned" in report(runs, one)

    def test_each_arm_answers_a_different_question(self) -> None:
        questions = {rule.question for rule in ARMS.values()}
        assert len(questions) == len(ARMS)


class TestTheEndpoint:
    """What the denominator counts, and what a run recovers out of it."""

    def test_the_denominator_is_the_stated_rows(self) -> None:
        held = reference(
            row("AES-256"),
            row("absent", predicate="internet-exposure"),
            row("unknown", predicate="tenant-ownership", reason="silent"),
            row(
                "internal",
                predicate="internet-exposure",
                basis="inferred",
                explanation="nothing routes to it from outside",
            ),
        )
        required = required_rows(held)

        assert len(required) == 2
        assert set(required) == {
            assertion_id(held.entries[0]),
            assertion_id(held.entries[1]),
        }

    def test_a_stated_absence_is_a_required_fact(self) -> None:
        """ "No MFA" is what the source says, not what it fails to say."""
        held = reference(row("absent"))
        assert required_rows(held) == (assertion_id(held.entries[0]),)

    def test_an_unknown_row_is_not_one(self) -> None:
        held = reference(row("unknown", reason="hedged"))
        assert required_rows(held) == ()

    def test_a_failed_run_recovers_none_of_the_case(self) -> None:
        """The row #1003 asks never to disappear from the denominator."""
        failed = run("01", "A", found=0, required=10, valid=False)
        assert failed.required == 10
        assert failed.recall == 0.0
        assert failure_rate([failed], "A") == 1.0

    def test_repeats_average_before_cases_do(self) -> None:
        """One case run five times is one observation, not five."""
        runs = [
            *(run("01", "A", found=10, required=10, repeat=n) for n in range(5)),
            run("02", "A", found=0, required=10),
        ]
        assert case_recall(runs, "A") == {"01": 1.0, "02": 0.0}
        assert macro_recall(runs, "A") == 0.5

    def test_a_case_with_no_required_row_is_recovered(self) -> None:
        assert run("01", "A", found=0, required=0).recall == 1.0

    def test_unsupported_counts_every_row_no_reference_took(self) -> None:
        runs = [run("01", "A", 1, 1, matched=3, unreviewed=1)]
        assert unsupported_rate(runs, "A") == 0.25
        assert unsupported_rate(runs, "B") == 0.0


class TestTheInterval:
    """What the bootstrap rests on, and what it does not."""

    def paired(self, **kwargs) -> tuple:
        """Two arms over three cases, the left one better on two of them."""
        runs = [
            run("01", "A", 5, 10),
            run("01", "B", 8, 10),
            run("02", "A", 5, 10),
            run("02", "B", 7, 10),
            run("03", "A", 6, 10),
            run("03", "B", 6, 10),
        ]
        return runs, paired_difference(runs, "B", "A", **kwargs)

    def test_the_difference_is_the_mean_of_the_paired_cases(self) -> None:
        _, found = self.paired()
        assert found.cases == 3
        assert found.difference == pytest.approx((0.3 + 0.2 + 0.0) / 3)

    def test_the_interval_brackets_the_difference(self) -> None:
        _, found = self.paired()
        assert found.low <= found.difference <= found.high
        assert found.level == CORRECTED_LEVEL

    def test_the_same_runs_give_the_same_interval(self) -> None:
        """An interval nobody can reproduce is not evidence."""
        _, first = self.paired()
        _, second = self.paired()
        assert (first.low, first.high) == (second.low, second.high)

    def test_a_wider_family_gives_a_wider_interval(self) -> None:
        _, corrected = self.paired()
        _, plain = self.paired(level=LEVEL)
        assert corrected.high - corrected.low >= plain.high - plain.low

    def test_only_cases_both_arms_ran_are_compared(self) -> None:
        runs = [
            run("01", "A", 5, 10),
            run("01", "B", 8, 10),
            run("02", "B", 10, 10),
        ]
        found = paired_difference(runs, "B", "A")
        assert found.cases == 1
        assert found.difference == pytest.approx(0.3)

    def test_two_arms_that_never_met_compare_nothing(self) -> None:
        found = paired_difference([run("01", "A", 5, 10)], "B", "A")
        assert found.cases == 0
        assert (found.difference, found.low, found.high) == (0.0, 0.0, 0.0)

    def test_an_indistinguishable_pair_spans_zero(self) -> None:
        runs = [
            run(case, arm, 5, 10) for case in ("01", "02", "03") for arm in ("A", "B")
        ]
        found = paired_difference(runs, "B", "A")
        assert not found.decisive

    def test_every_planned_comparison_is_reported(self) -> None:
        runs, _ = self.paired()
        found = comparisons(runs)
        assert [(one.left, one.right) for one in found] == list(COMPARISONS)


class TestTheReport:
    """The text a reader takes the gates to, and the file it re-runs from."""

    def runs(self) -> list[ArmRun]:
        return [
            run("01", "A", 5, 10),
            run("01", "B", 8, 10, unreviewed=2, matched=6),
            run("02", "A", 4, 10, valid=False),
            run("02", "B", 9, 10),
        ]

    def test_it_names_every_arm_and_every_case(self) -> None:
        built = report(self.runs())
        assert "| A |" in built
        assert "| B |" in built
        assert "| 01 |" in built
        assert "| 02 |" in built

    def test_it_names_the_seed_the_interval_rests_on(self) -> None:
        assert str(BOOTSTRAP_SEED) in report(self.runs())

    def test_it_names_every_error_class(self) -> None:
        built = report(self.runs())
        for fate in ROW_FATES:
            assert fate in built

    def test_it_reports_no_verdict(self) -> None:
        """The gates are read by a person; this prints what they read."""
        built = report(self.runs()).lower()
        assert "passed" not in built
        assert "verdict" not in built

    def test_a_run_survives_the_file_it_is_written_to(self, tmp_path: Path) -> None:
        path = write_runs(tmp_path / "runs.json", self.runs())
        assert [to_json(one) for one in load_runs(path)] == [
            to_json(one) for one in self.runs()
        ]

    def test_a_file_of_another_version_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "runs.json"
        path.write_text(json.dumps({"artifact_version": 99, "runs": []}))
        with pytest.raises(ArmsError, match="artifact_version"):
            load_runs(path)

    def test_a_fate_vocabulary_this_comparison_does_not_count_is_refused(
        self, tmp_path: Path
    ) -> None:
        """A renamed class must fail rather than read as a column of zeroes."""
        payload = json.loads(
            write_runs(tmp_path / "runs.json", self.runs()).read_text()
        )
        payload["runs"][0]["fates"].pop("worded")
        path = tmp_path / "bad.json"
        path.write_text(json.dumps(payload))
        with pytest.raises(ArmsError, match="keys its fates"):
            load_runs(path)

    def test_an_arm_nobody_declared_is_refused(self, tmp_path: Path) -> None:
        payload = json.loads(
            write_runs(tmp_path / "runs.json", self.runs()).read_text()
        )
        payload["runs"][0]["arm"] = "E"
        path = tmp_path / "bad.json"
        path.write_text(json.dumps(payload))
        with pytest.raises(ArmsError, match="not one of"):
            load_runs(path)

    def test_the_command_writes_and_prints_the_report(
        self, tmp_path: Path, capsys
    ) -> None:
        import argparse

        runs = write_runs(tmp_path / "runs.json", self.runs())
        out = tmp_path / "report.md"
        code = command_compare_arms(argparse.Namespace(runs=runs, out=out))

        assert code == 0
        assert out.read_text() == capsys.readouterr().out
        assert "## Planned comparisons" in out.read_text()

    def test_the_command_refuses_a_file_with_no_run(
        self, tmp_path: Path, capsys
    ) -> None:
        import argparse

        path = tmp_path / "runs.json"
        path.write_text(json.dumps({"artifact_version": ARTIFACT_VERSION, "runs": []}))
        code = command_compare_arms(argparse.Namespace(runs=path, out=None))

        assert code == 1
        assert "records no run" in capsys.readouterr().err


def test_the_command_is_registered() -> None:
    """A command nobody can reach is a report nobody re-runs."""
    from evals.harness.run import COMMANDS

    assert "compare-arms" in COMMANDS
    assert graph.PREPARE_NODE in HEAD
