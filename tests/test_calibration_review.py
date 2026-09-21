"""Machine-check the reproducibility claims in calibration-label review 02."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from evals.harness.calibration import load_pairs

REVIEW_PATH = Path("evals/calibration_labels/reviews/02.json")


def _text_digest(pair) -> str:
    material = f"{pair.reference_claim}\0{pair.candidate_claim}"
    return hashlib.sha256(material.encode()).hexdigest()


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


def test_review_02_manifests_still_verify_against_themselves():
    """What a superseded reading still owes: its own arithmetic.

    The draw itself is gone. The Case Sitting of 2026-09-21 deleted 41 reference
    claims, and a fixture is labelled against a claim by its place, so 51
    fixtures went with them — 18 of them fixtures a person had read. The sample
    was drawn from a population of 295 that no longer exists, so
    ``random.Random(seed).sample`` cannot be re-run here and this test no longer
    pretends it can.

    What survives is checkable and is checked: the seed still hashes to its
    recorded digest, each manifest still digests to the value beside it, the two
    manifests are still disjoint, and every id the record says survives does.
    ``superseded_by_corpus_change`` carries the loss, and
    :func:`test_the_superseded_block_matches_the_tree` holds it to the corpus.
    """
    review = json.loads(REVIEW_PATH.read_text())
    by_id = {pair.fixture_id: pair for pair in load_pairs()}

    boundary = review["boundary_review"]
    sample = review["random_review"]
    gone = set(review["superseded_by_corpus_change"]["boundary_gone"]) | set(
        review["superseded_by_corpus_change"]["random_gone"]
    )

    assert hashlib.sha256(sample["seed"].encode()).hexdigest() == sample["seed_sha256"]
    assert len(boundary["fixture_ids"]) == boundary["pairs"] == 44
    assert _manifest_digest(boundary["fixture_ids"]) == boundary["manifest_sha256"]
    assert _manifest_digest(sample["fixture_ids"]) == sample["manifest_sha256"]
    assert set(boundary["fixture_ids"]).isdisjoint(sample["fixture_ids"])
    assert sample["population_pairs"] == 295

    survived = set(boundary["fixture_ids"] + sample["fixture_ids"]) - gone
    assert survived <= by_id.keys()


def test_the_superseded_block_matches_the_tree():
    """The recorded loss is derived from the corpus, not asserted over it."""
    review = json.loads(REVIEW_PATH.read_text())
    block = review["superseded_by_corpus_change"]
    now = {pair.fixture_id for pair in load_pairs()}
    reidentification = review["reidentification"]

    assert block["pairs_after"] == len(now)
    assert block["fixtures_read_gone"] == sorted(
        fixture_id
        for fixture_id in reidentification["text_digest_at_reidentification"]
        if fixture_id not in now
    )
    assert block["boundary_gone"] == sorted(
        fixture_id
        for fixture_id in review["boundary_review"]["fixture_ids"]
        if fixture_id not in now
    )
    assert block["random_gone"] == sorted(
        fixture_id
        for fixture_id in review["random_review"]["fixture_ids"]
        if fixture_id not in now
    )


def test_review_02_reidentification_maps_every_pinned_fixture():
    review = json.loads(REVIEW_PATH.read_text())
    reidentification = review["reidentification"]
    pinned = set(review["boundary_review"]["fixture_ids"]) | set(
        review["random_review"]["fixture_ids"]
    )

    mapped = reidentification["previous_to_current_fixture_id"]
    assert len(set(mapped.values())) == len(mapped) == len(pinned)
    assert set(mapped.values()) == pinned
    assert set(reidentification["previous_manifest_sha256"]) == {
        "boundary_review",
        "random_review",
    }


def test_the_record_says_its_place_derived_ids_stopped_resolving():
    """A place-derived id survives a reword and does not survive a deletion.

    A ``fixture_id`` is derived from its case, its reference index and its
    candidate ordinal. Rewording a claim leaves all three where they are;
    deleting one renumbers every reference above it, so a surviving id can name
    a different pair. Every case in this corpus has had a claim deleted, so no
    id in this record is a handle on today's fixtures and no digest comparison
    over them means anything.

    The record owes one thing in that state: to say so. That is what this
    asserts. The manifests stay as the record of what a person read.
    """
    review = json.loads(REVIEW_PATH.read_text())
    block = review["superseded_by_corpus_change"]

    assert block["place_derived_ids_did_not_survive"]
    assert review["reidentification"]["text_digest_at_reidentification"]


def test_review_02_changed_random_labels_are_applied():
    review = json.loads(REVIEW_PATH.read_text())
    outcome = review["random_review"]["outcome"]

    assert outcome["agreements_with_original_primary_label"] == 58
    assert outcome["disagreements_with_original_primary_label"] == 2
    # The ids no longer resolve to the pairs they were recorded against — see
    # test_the_record_says_its_place_derived_ids_stopped_resolving — so what is
    # checkable is the record's own arithmetic, not the label behind each id.
    assert (
        len(outcome["changed_fixture_ids"])
        == outcome["disagreements_with_original_primary_label"]
    )


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
