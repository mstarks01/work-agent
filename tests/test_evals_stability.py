"""Run-to-run stability read back off finished artifacts.

Credential-free by construction — the artifacts hold every reference each sweep
matched — so these drive the real loader over real artifact shapes rather than
a hand-built object graph.
"""

import json
from pathlib import Path

import pytest

from evals.harness.artifact import ARTIFACT_VERSION, DECLARED_KEYS
from evals.harness.provenance import ProvenanceError
from evals.harness.stability import (
    aggregate_stability,
    comparability_warnings,
    compare_runs,
    load_runs,
)
from tests.test_evals_provenance import provenance, sampling  # noqa: F401


def score(case: str, references: int, matched: list[int]) -> dict:
    """One case's score block, as ``CaseScore.to_json`` writes it."""
    return {
        "case": case,
        "counts": {"references": references, "matched": len(matched)},
        "metrics": {"recall": round(len(matched) / references, 3)},
        "matched": [
            {"reference_index": index, "threat_id": f"T-{index}"} for index in matched
        ],
    }


def write_run(tmp_path, name, record, scores, **overrides) -> Path:
    """A complete artifact, the way a sweep writes one.

    Every declared key is present, because every sweep writes every declared
    key — an instrument that measured nothing writes an empty block rather than
    dropping one. A fixture carrying only the keys its own assertions read would
    be a shape no sweep produces, and the loader is strict about that on purpose.
    """
    payload = {
        **dict.fromkeys(DECLARED_KEYS),
        "artifact_version": ARTIFACT_VERSION,
        "mode": "analysis",
        "cases": sorted({entry["case"] for entry in scores}),
        "trusted": True,
        "structural_failures": [],
        "repo_commit": {"commit": "0" * 40, "clean": True},
        "corpus_digest": "0" * 64,
        "provenance": record.to_json(),
        "models": {"tiers": {"strong": "openai/gpt-4.1"}, "tiers_config_version": "3"},
        "scores": scores,
        "applicability": [],
    } | overrides
    path = tmp_path / name
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


@pytest.fixture
def repeat(tmp_path, sampling):  # noqa: F811
    """The same corpus twice: one reference found in both, one in neither."""
    record = provenance(sampling)
    first = write_run(
        tmp_path, "a.json", record, [score("01-payments-checkout", 4, [0, 1])]
    )
    second = write_run(
        tmp_path, "b.json", record, [score("01-payments-checkout", 4, [0, 2])]
    )
    return load_runs([first, second])


def test_the_three_buckets_partition_the_references(repeat):
    (case,) = compare_runs(repeat)

    assert case.always == 1  # reference 0, matched by both runs
    assert case.sometimes == 2  # references 1 and 2, one run each
    assert case.never == 1  # reference 3, found by neither
    assert case.always + case.sometimes + case.never == case.references


def test_the_spread_is_what_a_one_sweep_number_can_move_by(repeat):
    (case,) = compare_runs(repeat)

    assert case.recalls == (0.5, 0.5)
    assert case.recall_spread == 0.0
    # Equal recall, different references: the overlap is the finding the
    # recall number cannot show on its own.
    assert case.mean_jaccard == pytest.approx(1 / 3)
    assert case.volatile_rate == 0.5


def test_two_runs_that_both_found_nothing_agree_completely(tmp_path, sampling):  # noqa: F811
    record = provenance(sampling)
    runs = load_runs(
        [
            write_run(
                tmp_path, "a.json", record, [score("01-payments-checkout", 3, [])]
            ),
            write_run(
                tmp_path, "b.json", record, [score("01-payments-checkout", 3, [])]
            ),
        ]
    )

    (case,) = compare_runs(runs)

    assert case.mean_jaccard == 1.0
    assert case.never == 3


def test_a_case_only_one_run_scored_is_excluded_and_named(tmp_path, sampling):  # noqa: F811
    record = provenance(sampling)
    runs = load_runs(
        [
            write_run(
                tmp_path,
                "a.json",
                record,
                [score("01-payments-checkout", 2, [0]), score("02-iot-fleet", 2, [1])],
            ),
            write_run(
                tmp_path, "b.json", record, [score("01-payments-checkout", 2, [0])]
            ),
        ]
    )

    compared = compare_runs(runs)

    assert [entry.case_id for entry in compared] == ["01-payments-checkout"]
    assert any("02-iot-fleet" in warning for warning in comparability_warnings(runs))


def test_a_changed_model_is_warned_about_rather_than_refused(tmp_path, sampling):  # noqa: F811
    """Comparing two configurations is often the question; it must be a chosen one.

    Every field of the ``models`` record is compared, not a named list of them,
    so a record that gains or loses a field keeps being compared without this
    function changing. That is what let the judge field leave it silently."""
    record = provenance(sampling)
    scores = [score("01-payments-checkout", 2, [0])]
    runs = load_runs(
        [
            write_run(tmp_path, "a.json", record, scores),
            write_run(
                tmp_path,
                "b.json",
                record,
                scores,
                models={
                    "tiers": {"strong": "vertex_ai/gemini-2.5-pro"},
                    "tiers_config_version": "3",
                },
            ),
        ]
    )

    warnings = comparability_warnings(runs)

    assert any("tiers" in warning for warning in warnings)
    assert compare_runs(runs)  # still compared


def test_a_sweep_with_no_scoring_is_refused_rather_than_read_as_a_zero(
    tmp_path,
    sampling,  # noqa: F811
):
    path = write_run(tmp_path, "a.json", provenance(sampling), [])

    with pytest.raises(ProvenanceError, match="no scores or applicability block"):
        load_runs([path])


def test_one_run_is_not_a_stability_measurement(tmp_path, sampling):  # noqa: F811
    runs = load_runs(
        [
            write_run(
                tmp_path,
                "a.json",
                provenance(sampling),
                [score("01-payments-checkout", 2, [0])],
            )
        ]
    )

    with pytest.raises(ValueError, match="at least two"):
        compare_runs(runs)


def test_the_aggregate_pools_over_references_not_over_cases(repeat):
    totals = aggregate_stability(compare_runs(repeat))

    assert totals["runs"] == 2
    assert totals["references"] == 4
    assert totals["always_rate"] == 0.25
    assert totals["volatile_rate"] == 0.5
    assert totals["worst_case_recall_spread"] == 0.0


def test_a_malformed_score_block_is_refused_by_name(tmp_path, sampling):  # noqa: F811
    """A file to re-produce, not a defect in the comparison."""
    path = write_run(
        tmp_path,
        "a.json",
        provenance(sampling),
        [{"case": "01-payments-checkout", "matched": []}],
    )

    with pytest.raises(ProvenanceError, match="malformed score block"):
        load_runs([path])
