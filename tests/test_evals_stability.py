"""Run-to-run stability read back off finished artifacts.

Credential-free by construction — the artifacts hold every reference each sweep
matched — so these drive the real loader over real artifact shapes rather than
a hand-built object graph.
"""

import json
from pathlib import Path

import pytest

from evals.harness.artifact import ARTIFACT_VERSION, DECLARED_KEYS, load_artifact
from evals.harness.provenance import ProvenanceError
from evals.harness.stability import (
    Band,
    aggregate_stability,
    band,
    comparability_warnings,
    compare_runs,
    load_runs,
)
from tests.test_evals_provenance import provenance, sampling  # noqa: F401


def score(
    case: str, references: int, matched: list[int], must_find: set[int] | None = None
) -> dict:
    """One case's score block, as ``CaseScore.to_json`` writes it.

    ``must_find`` names the reference indices this package counts as must-find.
    It marks the tier on each matched row and names no missed reference, which
    is the half of a must-find fate this record carries.
    """
    tiers = must_find if must_find is not None else set(matched)
    return {
        "case": case,
        "counts": {"references": references, "matched": len(matched)},
        "metrics": {"reference_coverage": round(len(matched) / references, 3)},
        "matched": [
            {
                "reference_index": index,
                "threat_id": f"T-{index}",
                "tier": "must-find" if index in tiers else "expected",
            }
            for index in matched
        ],
    }


def applicability(
    case: str, expected: int, matched: list[str], must_find_missed: list[str]
) -> dict:
    """One case's applicability block, as a catalog-identified package writes it.

    The other half of the answer: no tier on a matched row, and the missed
    must-finds named by catalog identifier.
    """
    return {
        "case": case,
        "framework": "asvs",
        "expected": expected,
        "recall": round(len(matched) / expected, 3),
        "matched": matched,
        "must_find_missed": must_find_missed,
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

    def test_a_run_that_omits_the_block_reads_unread_rather_than_refusing(
        self,
        tmp_path,
        sampling,  # noqa: F811
    ):
        """The shape of the first merged Baseline: a sweep taken before #729,
        which carries this version and omits a key the version declares.

        The two readers are driven against each other here, because that is how
        the defect survived: ``block`` was tested for raising and ``_causes``
        for returning ``None``, and the second was driven by a ``losses`` value
        of ``None`` rather than by an artifact with no such key. Between them
        sat a handler for an exception nobody raises, so the whole comparison
        died on the one artifact that has this shape.
        """
        record = provenance(sampling)
        a = write_run(
            tmp_path,
            "a.json",
            record,
            [score(self.CASE, 2, [0])],
            losses=losses_block(self.CASE, {1: "verb"}),
        )
        b = write_run(tmp_path, "b.json", record, [score(self.CASE, 2, [0])])
        raw = json.loads(b.read_text(encoding="utf-8"))
        del raw["losses"]
        b.write_text(json.dumps(raw, indent=2), encoding="utf-8")

        (older,) = load_runs([b])
        assert older.causes is None
        with pytest.raises(ProvenanceError, match="predates the instrument"):
            load_artifact(b).block("losses")
        assert load_artifact(b).carries("losses") is False

        entry = compare_runs(load_runs([a, b]))[0]

        # Recall needs no losses block, and reads across both runs.
        assert entry.always == 1
        assert entry.cause_stable is None and entry.cause_moving is None

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


def test_a_second_composed_package_is_not_reported_as_a_malformed_artifact(tmp_path):
    """The registry's defect names the registry, never the file it was reading.

    ``read_run`` wraps its block reads in a handler that calls the artifact
    malformed, and ``ProvenanceError`` is a ``ValueError``, so a registry
    question asked inside that handler came back as
    ``<path>: malformed score block``. The path named a file with nothing wrong
    with it, which is the wrong error naming the wrong cause — the failure
    ``artifact.load_artifact`` documents at length for its own version field.
    """
    from unittest import mock

    from evals.harness import stability

    artifact = mock.Mock()
    artifact.path = tmp_path / "sweep.json"
    artifact.block.return_value = []

    with (
        mock.patch.dict(stability.IDENTIFIER_OF, {"asvs": None}, clear=False),
        pytest.raises(ProvenanceError) as raised,
    ):
        stability.read_run(artifact)

    assert "malformed" not in str(raised.value)
    assert "needs a framework field" in str(raised.value)


class TestTheBandIsReadOffTheFates:
    """The spread of the must-find total, from runs already paid for.

    The expensive reading is five sweeps of one configuration and the sample
    deviation of five totals, which buys a deviation on four degrees of
    freedom. This sums each reference's own variance instead, so a pair of
    sweeps answers — the sum is over the references, not over the runs (#879).
    """

    CASE = "01-payments-checkout"

    def runs(self, tmp_path, sampling, matched_per_run, must_find):  # noqa: F811
        record = provenance(sampling)
        return load_runs(
            [
                write_run(
                    tmp_path,
                    f"r{n}.json",
                    record,
                    [score(self.CASE, 10, matched, must_find)],
                )
                for n, matched in enumerate(matched_per_run)
            ]
        )

    def test_runs_that_agree_on_every_must_find_have_no_spread(
        self,
        tmp_path,
        sampling,  # noqa: F811
    ):
        """Nothing moved, so nothing can be attributed to movement."""
        measured = band(self.runs(tmp_path, sampling, [[0, 1], [0, 1]], {0, 1}))

        assert measured.volatile == 0
        assert measured.floor_variance == 0.0
        assert measured.sd == 0.0

    def test_one_reference_moving_between_two_runs_is_half_a_variance(
        self,
        tmp_path,
        sampling,  # noqa: F811
    ):
        """``m(k-m)/(k(k-1))`` at k=2, m=1. Two runs are enough because the sum
        runs over the references rather than over the runs."""
        measured = band(self.runs(tmp_path, sampling, [[0, 1], [0]], {0, 1}))

        assert (measured.references, measured.volatile) == (2, 1)
        assert measured.floor_variance == pytest.approx(0.5)
        assert measured.sd == pytest.approx(0.5**0.5)

    def test_an_expected_reference_is_not_in_the_band(
        self,
        tmp_path,
        sampling,  # noqa: F811
    ):
        """The gate is on the must-find tier, so the band is over that tier."""
        measured = band(self.runs(tmp_path, sampling, [[0, 1], [0]], {0}))

        assert measured.references == 1
        assert measured.floor_variance == 0.0

    def test_five_runs_sum_each_reference_s_own_variance(
        self,
        tmp_path,
        sampling,  # noqa: F811
    ):
        """One reference matched in 3 of 5 and one in 5 of 5:
        3*2/(5*4) = 0.3, and nothing from the one that never moved."""
        runs = self.runs(
            tmp_path,
            sampling,
            [[0, 1], [0, 1], [0, 1], [0], [0]],
            {0, 1},
        )
        measured = band(runs)

        assert measured.floor_variance == pytest.approx(0.3)

    def test_the_floor_and_the_observed_spread_are_read_against_each_other(
        self,
        tmp_path,
        sampling,  # noqa: F811
    ):
        """The two readings of one question, which is what makes the inflation
        a measurement rather than an assumption.

        Three runs where two references move together: every run matches both
        or neither. The totals are 2, 2, 0, whose variance is 4/3. The floor
        treats the two as independent and reads 2 * (2*1/(3*2)) = 2/3, so the
        inflation is exactly 2.
        """
        runs = self.runs(tmp_path, sampling, [[0, 1], [0, 1], []], {0, 1})
        measured = band(runs, calibration=[runs])

        assert measured.floor_variance == pytest.approx(2 / 3)
        assert measured.observed_variance == pytest.approx(4 / 3)
        assert measured.inflation == pytest.approx(2.0)
        assert measured.sd == pytest.approx((4 / 3) ** 0.5)
        assert measured.calibration_freedom == 2

    def test_a_pair_calibrates_nothing(self, tmp_path, sampling):  # noqa: F811
        """Two runs give a variance on one degree of freedom, which says
        nothing. Ignored rather than counted as agreement."""
        runs = self.runs(tmp_path, sampling, [[0, 1], [0]], {0, 1})
        measured = band(runs, calibration=[runs])

        assert measured.inflation is None
        assert measured.calibration_freedom == 0
        assert measured.sd == pytest.approx(0.5**0.5)  # the floor, unmultiplied

    def test_naming_rows_prices_them_and_not_the_corpus(
        self,
        tmp_path,
        sampling,  # noqa: F811
    ):
        """What the guide asks for: a fix that targets known references is
        measured on them, because the corpus total is the noisiest reading."""
        runs = self.runs(tmp_path, sampling, [[0, 1, 2], [0]], {0, 1, 2})
        whole = band(runs)
        narrowed = band(runs, rows={(self.CASE, "1")})

        assert whole.floor_variance == pytest.approx(1.0)
        assert narrowed.references == 1
        assert narrowed.floor_variance == pytest.approx(0.5)

    def test_the_calibration_reads_everything_even_when_rows_narrow(
        self,
        tmp_path,
        sampling,  # noqa: F811
    ):
        """How much the references co-move is a property of a run, not of the
        rows being priced. Narrowing the calibration read it as absent."""
        repeat = self.runs(tmp_path, sampling, [[0, 1], [0, 1], []], {0, 1})
        narrowed = band(repeat, calibration=[repeat], rows={(self.CASE, "0")})

        assert narrowed.references == 1
        assert narrowed.inflation == pytest.approx(2.0)

    def test_a_case_one_run_skipped_is_left_out(self, tmp_path, sampling):  # noqa: F811
        """The rule ``compare_runs`` states, applied here for its own reason.

        Reproduced from the shape that found it: three runs where the third
        skipped case 02, which the other two agree on completely. Folding it in
        read the third as a run that found nothing there, so the totals went
        4, 3, 2 instead of 2, 1, 2 — an observed variance of 1.0 against a
        floor of 0.33, an inflation of **3.0** where the truth is 1.0, and a
        band of 1.00 where it is 0.58.
        """
        record = provenance(sampling)

        def both(matched):
            return [
                score("01", 4, matched, {0, 1}),
                score("02", 4, [0, 1], {0, 1}),
            ]

        runs = load_runs(
            [
                write_run(tmp_path, "a.json", record, both([0, 1])),
                write_run(tmp_path, "b.json", record, both([0])),
                write_run(tmp_path, "c.json", record, [score("01", 4, [0, 1], {0, 1})]),
            ]
        )
        measured = band(runs, calibration=[runs])

        # Only case 01's two must-finds are in the population.
        assert measured.references == 2
        assert measured.floor_variance == pytest.approx(1 / 3)
        assert measured.observed_variance == pytest.approx(1 / 3)
        assert measured.inflation == pytest.approx(1.0)

    def test_runs_sharing_no_case_are_refused(self, tmp_path, sampling):  # noqa: F811
        """An empty intersection is not a spread of zero."""
        record = provenance(sampling)
        runs = load_runs(
            [
                write_run(tmp_path, "a.json", record, [score("01", 4, [0], {0})]),
                write_run(tmp_path, "b.json", record, [score("02", 4, [0], {0})]),
            ]
        )

        with pytest.raises(ValueError, match="share no scored case"):
            band(runs)

    def test_a_lone_run_is_refused(self, tmp_path, sampling):  # noqa: F811
        with pytest.raises(ValueError, match="two runs or more"):
            band(self.runs(tmp_path, sampling, [[0]], {0}))

    @pytest.mark.parametrize(
        ("variance", "effect", "expected"),
        [(4.0, 4.0, 2), (4.0, 10.0, 1), (16.0, 4.0, 8), (0.0, 1.0, 1)],
    )
    def test_runs_needed_is_eight_variances_over_the_effect_squared(
        self, variance, effect, expected
    ):
        """``n >= 8v/effect**2``, and never fewer than one: a comparison needs
        a before and an after however small the spread."""
        measured = Band(references=1, volatile=1, runs=2, floor_variance=variance)

        assert measured.runs_needed(effect) == expected

    def test_an_effect_of_nothing_is_refused(self):
        measured = Band(references=1, volatile=1, runs=2, floor_variance=1.0)

        with pytest.raises(ValueError, match="an effect of nothing"):
            measured.runs_needed(0)


class TestBothPackagesAnswerTheBand:
    """Two packages name different halves of a must-find fate, and both answer.

    One marks the tier on each matched row and cannot name a reference nobody
    matched. The other marks no tier and lists the missed must-finds by catalog
    identifier. Neither half has to be complete, because a reference every run
    agreed on contributes no spread.
    """

    CASE = "01-payments-checkout"

    def test_a_package_that_marks_the_tier_answers(self, tmp_path, sampling):  # noqa: F811
        record = provenance(sampling)
        runs = load_runs(
            [
                write_run(tmp_path, "a.json", record, [score(self.CASE, 4, [0, 1])]),
                write_run(tmp_path, "b.json", record, [score(self.CASE, 4, [0])]),
            ]
        )

        assert band(runs).floor_variance == pytest.approx(0.5)

    def test_a_package_that_names_the_missed_ones_answers(
        self,
        tmp_path,
        sampling,  # noqa: F811
    ):
        """``V1.2.4`` matched in one run and named as missed in the other is
        the same movement, read from the other side of the record."""
        record = provenance(sampling)
        runs = load_runs(
            [
                write_run(
                    tmp_path,
                    "a.json",
                    record,
                    [],
                    applicability=[
                        applicability(self.CASE, 4, ["V1.2.4", "V2.1.1"], [])
                    ],
                ),
                write_run(
                    tmp_path,
                    "b.json",
                    record,
                    [],
                    applicability=[applicability(self.CASE, 4, ["V2.1.1"], ["V1.2.4"])],
                ),
            ]
        )
        measured = band(runs)

        # V2.1.1 matched in both and is never named a must-find, so it is not
        # in the population; V1.2.4 is, and it moved.
        assert (measured.references, measured.volatile) == (1, 1)
        assert measured.floor_variance == pytest.approx(0.5)
