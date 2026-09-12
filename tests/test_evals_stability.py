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


def test_two_corpora_are_refused_rather_than_compared(tmp_path, sampling):  # noqa: F811
    """A reference index is a coordinate into one corpus.

    Two runs over two corpus digests share no coordinate system, so their
    matched sets cannot be intersected; #675 D18 reproduced a comparison that
    did exactly that and reported a negative ``never``.
    """
    record = provenance(sampling)
    runs = load_runs(
        [
            write_run(
                tmp_path, "a.json", record, [score("01-payments-checkout", 4, [0])]
            ),
            write_run(
                tmp_path,
                "b.json",
                record,
                [score("01-payments-checkout", 4, [1])],
                corpus_digest="1" * 64,
            ),
        ]
    )

    with pytest.raises(ValueError, match="different corpora"):
        compare_runs(runs)


def test_two_reference_counts_for_one_case_are_refused(tmp_path, sampling):  # noqa: F811
    """One digest should make the count unanimous; the check does not rest on it."""
    record = provenance(sampling)
    runs = load_runs(
        [
            write_run(
                tmp_path, "a.json", record, [score("01-payments-checkout", 1, [0])]
            ),
            write_run(
                tmp_path, "b.json", record, [score("01-payments-checkout", 2, [1])]
            ),
        ]
    )

    with pytest.raises(ValueError, match="disagree about how many references"):
        compare_runs(runs)


def test_a_matched_index_past_the_reference_count_is_refused(tmp_path, sampling):  # noqa: F811
    record = provenance(sampling)
    runs = load_runs(
        [
            write_run(
                tmp_path, "a.json", record, [score("01-payments-checkout", 2, [0])]
            ),
            write_run(
                tmp_path, "b.json", record, [score("01-payments-checkout", 2, [1, 7])]
            ),
        ]
    )

    with pytest.raises(ValueError, match="names a reference the case does not hold"):
        compare_runs(runs)


def test_the_same_artifact_twice_is_one_measurement(tmp_path, sampling):  # noqa: F811
    record = provenance(sampling)
    path = write_run(
        tmp_path, "a.json", record, [score("01-payments-checkout", 4, [0])]
    )

    with pytest.raises(ValueError, match="named more than once"):
        compare_runs(load_runs([path, path]))


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


def losses_block(case: str, causes: dict[int, str]) -> list[dict]:
    """A ``losses`` block charging each index to the cause given."""
    return [
        {
            "case": case,
            "losses": [
                {"reference_index": index, "cause": cause}
                for index, cause in causes.items()
            ],
        }
    ]


def write_reports(
    path: Path, case: str, claims: dict[int, tuple[str, list[str]]]
) -> None:
    """One report beside ``path``: the corpus model of case 01 and one claim per matched index."""
    from evals.harness.bundle import reports_dir
    from evals.harness.reference import load_case

    model = load_case(
        Path(__file__).resolve().parents[1] / "evals" / "corpus" / case
    ).model
    directory = reports_dir(path)
    directory.mkdir(exist_ok=True)
    (directory / f"{case}.report.json").write_text(
        json.dumps(
            {
                "system_model": model.model_dump(mode="json"),
                "analyses": [
                    {
                        "framework": "stride",
                        "claims": [
                            {
                                "id": f"T-{index}",
                                "severity": {"level": level},
                                "affected_element_ids": elements,
                            }
                            for index, (level, elements) in claims.items()
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


class TestCauseStability:
    """A reference every run missed: charged to one cause each time, or to several."""

    CASE = "01-payments-checkout"

    def test_a_cause_that_holds_and_one_that_moves_are_counted_apart(
        self,
        tmp_path,
        sampling,  # noqa: F811
    ):
        record = provenance(sampling)
        a = write_run(
            tmp_path,
            "a.json",
            record,
            [score(self.CASE, 4, [0])],
            losses=losses_block(self.CASE, {1: "verb", 2: "place", 3: "unled"}),
        )
        b = write_run(
            tmp_path,
            "b.json",
            record,
            [score(self.CASE, 4, [0])],
            losses=losses_block(self.CASE, {1: "verb", 2: "unled", 3: "unled"}),
        )
        entry = compare_runs(load_runs([a, b]))[0]

        assert (entry.cause_stable, entry.cause_moving) == (2, 1)
        assert aggregate_stability([entry])["cause_stable"] == 2

    def test_a_reference_missed_in_one_run_only_has_no_cause_to_compare(
        self,
        tmp_path,
        sampling,  # noqa: F811
    ):
        record = provenance(sampling)
        a = write_run(
            tmp_path,
            "a.json",
            record,
            [score(self.CASE, 3, [0, 1])],
            losses=losses_block(self.CASE, {2: "verb"}),
        )
        b = write_run(
            tmp_path,
            "b.json",
            record,
            [score(self.CASE, 3, [0])],
            losses=losses_block(self.CASE, {1: "place", 2: "verb"}),
        )
        entry = compare_runs(load_runs([a, b]))[0]

        assert (entry.cause_stable, entry.cause_moving) == (1, 0)
        assert entry.sometimes == 1

    def test_a_run_with_no_loss_rows_reads_unread_rather_than_agreeing(
        self,
        tmp_path,
        sampling,  # noqa: F811
    ):
        record = provenance(sampling)
        a = write_run(
            tmp_path,
            "a.json",
            record,
            [score(self.CASE, 2, [0])],
            losses=losses_block(self.CASE, {1: "verb"}),
        )
        b = write_run(tmp_path, "b.json", record, [score(self.CASE, 2, [0])])
        entry = compare_runs(load_runs([a, b]))[0]

        assert entry.cause_stable is None and entry.cause_moving is None
        assert aggregate_stability([entry])["cause_stable"] is None

    def test_a_miss_the_losses_block_does_not_charge_is_refused(
        self,
        tmp_path,
        sampling,  # noqa: F811
    ):
        record = provenance(sampling)
        a = write_run(
            tmp_path,
            "a.json",
            record,
            [score(self.CASE, 2, [0])],
            losses=losses_block(self.CASE, {}),
        )
        b = write_run(
            tmp_path,
            "b.json",
            record,
            [score(self.CASE, 2, [0])],
            losses=losses_block(self.CASE, {1: "verb"}),
        )
        with pytest.raises(ValueError, match="charges it to nothing"):
            compare_runs(load_runs([a, b]))


class TestContentStability:
    """A reference two runs matched: one severity band and one place, or not."""

    CASE = "01-payments-checkout"

    def test_a_band_and_a_place_that_hold_and_ones_that_move(
        self,
        tmp_path,
        sampling,  # noqa: F811
    ):
        record = provenance(sampling)
        scores = [score(self.CASE, 3, [0, 1, 2])]
        a = write_run(tmp_path, "a.json", record, scores)
        b = write_run(tmp_path, "b.json", record, scores)
        write_reports(
            a,
            self.CASE,
            {
                0: ("high", ["process:storefront-api"]),
                1: ("high", ["process:storefront-api"]),
                2: ("medium", ["store:orders-db"]),
            },
        )
        write_reports(
            b,
            self.CASE,
            {
                0: ("high", ["process:storefront-api"]),
                # A flow cited on one side and its endpoints on the other is one
                # place, by the same resolution the identity rule applies.
                1: ("critical", ["flow:shopper-to-storefront-api:place-order"]),
                2: ("medium", ["process:order-service"]),
            },
        )
        entry = compare_runs(load_runs([a, b]))[0]

        assert (entry.severity_held, entry.severity_moved) == (2, 1)
        assert (entry.place_held, entry.place_moved) == (1, 2)

    def test_a_reference_one_run_matched_is_not_compared(self, tmp_path, sampling):  # noqa: F811
        record = provenance(sampling)
        a = write_run(tmp_path, "a.json", record, [score(self.CASE, 2, [0, 1])])
        b = write_run(tmp_path, "b.json", record, [score(self.CASE, 2, [0])])
        write_reports(a, self.CASE, {0: ("high", []), 1: ("low", [])})
        write_reports(b, self.CASE, {0: ("high", [])})
        entry = compare_runs(load_runs([a, b]))[0]

        assert (entry.severity_held, entry.severity_moved) == (1, 0)
        assert (entry.place_held, entry.place_moved) == (1, 0)

    def test_a_run_without_its_bundle_reads_unread(self, tmp_path, sampling):  # noqa: F811
        record = provenance(sampling)
        a = write_run(tmp_path, "a.json", record, [score(self.CASE, 2, [0])])
        b = write_run(tmp_path, "b.json", record, [score(self.CASE, 2, [0])])
        write_reports(a, self.CASE, {0: ("high", [])})
        entry = compare_runs(load_runs([a, b]))[0]

        assert entry.severity_held is None and entry.place_held is None
        assert aggregate_stability([entry])["place_held"] is None

    def test_a_bundle_missing_a_matched_claim_is_refused_by_name(
        self,
        tmp_path,
        sampling,  # noqa: F811
    ):
        record = provenance(sampling)
        a = write_run(tmp_path, "a.json", record, [score(self.CASE, 2, [0, 1])])
        write_reports(a, self.CASE, {0: ("high", [])})
        with pytest.raises(ProvenanceError, match="matched content"):
            load_runs([a])
