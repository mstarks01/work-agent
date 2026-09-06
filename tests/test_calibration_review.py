"""Machine-check the reproducibility claims in calibration-label review 02."""

from __future__ import annotations

import hashlib
import json
import math
import random
from pathlib import Path

from evals.harness.calibration import load_pairs

REVIEW_PATH = Path("evals/calibration_labels/reviews/02.json")


def _fixture_id(pair) -> str:
    material = (
        f"{pair.case}\0{pair.category}\0{pair.reference_claim}\0{pair.candidate_claim}"
    )
    return hashlib.sha256(material.encode()).hexdigest()[:12]


def _manifest_digest(ids: list[str]) -> str:
    return hashlib.sha256("\n".join(ids).encode()).hexdigest()


def _hypergeometric_probability(
    observed: int, population: int, defects: int, sample: int
) -> float:
    if observed < 0 or observed > defects or sample - observed > population - defects:
        return 0.0
    return (
        math.comb(defects, observed)
        * math.comb(population - defects, sample - observed)
        / math.comb(population, sample)
    )


def test_review_02_manifests_resolve_and_random_sample_reproduces():
    review = json.loads(REVIEW_PATH.read_text())
    pairs = load_pairs()
    ids = [_fixture_id(pair) for pair in pairs]
    by_id = dict(zip(ids, pairs, strict=True))

    assert len(ids) == len(set(ids)) == review["fixture_snapshot"]["pairs"]

    boundary = review["boundary_review"]
    sample = review["random_review"]
    assert hashlib.sha256(sample["seed"].encode()).hexdigest() == sample["seed_sha256"]
    assert len(boundary["fixture_ids"]) == boundary["pairs"] == 44
    assert _manifest_digest(boundary["fixture_ids"]) == boundary["manifest_sha256"]
    assert set(boundary["fixture_ids"]) <= by_id.keys()
    assert set(sample["fixture_ids"]) <= by_id.keys()
    assert set(boundary["fixture_ids"]).isdisjoint(sample["fixture_ids"])

    population = [
        fixture_id
        for fixture_id in ids
        if fixture_id not in set(boundary["fixture_ids"])
    ]
    reproduced = random.Random(sample["seed"]).sample(
        population, sample["sample_pairs"]
    )
    assert len(population) == sample["population_pairs"] == 295
    assert reproduced == sample["fixture_ids"]
    assert _manifest_digest(reproduced) == sample["manifest_sha256"]


def test_review_02_changed_random_labels_are_applied():
    review = json.loads(REVIEW_PATH.read_text())
    pairs = {_fixture_id(pair): pair for pair in load_pairs()}
    outcome = review["random_review"]["outcome"]

    assert outcome["agreements_with_original_primary_label"] == 58
    assert outcome["disagreements_with_original_primary_label"] == 2
    assert {
        pairs[fixture_id].label for fixture_id in outcome["changed_fixture_ids"]
    } == {outcome["changed_to"]}


def test_review_02_exact_interval_is_reproducible():
    review = json.loads(REVIEW_PATH.read_text())
    random_review = review["random_review"]
    outcome = random_review["outcome"]
    population = random_review["population_pairs"]
    sample = random_review["sample_pairs"]
    observed = outcome["disagreements_with_original_primary_label"]
    alpha = 1 - outcome["confidence_interval"]["confidence"]

    accepted = []
    for defects in range(population + 1):
        lower_tail = sum(
            _hypergeometric_probability(value, population, defects, sample)
            for value in range(observed + 1)
        )
        upper_tail = sum(
            _hypergeometric_probability(value, population, defects, sample)
            for value in range(observed, sample + 1)
        )
        if lower_tail >= alpha / 2 and upper_tail >= alpha / 2:
            accepted.append(defects)

    bounds = outcome["confidence_interval"]["defective_fixture_count_bounds"]
    assert [min(accepted), max(accepted)] == bounds == [2, 31]
    assert outcome["confidence_interval"]["population_error_rate_bounds"] == [
        bounds[0] / population,
        bounds[1] / population,
    ]
