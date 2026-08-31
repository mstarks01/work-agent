"""The verb the exemplars work against the verb the corpus grades.

Two sides of one number, and both move often: a blessing pass edits a reference
set, a prompt pass edits an exemplar. The counts below are pinned so that either
edit shows up as a failing test rather than as a quietly different figure in a
sweep artifact nobody diffed.

**A pinned count moving is not a defect.** When one moves, re-read the sweep,
write the new number into :mod:`evals.harness.exemplar_verbs`'s docstring, and
update the pin here. What the pin buys is that the docstring cannot rot.

Deterministic and free of provider calls, so it gates on every PR.
"""

from __future__ import annotations

import pytest

from analysis_service.actions import family_of
from analysis_service.frameworks import PACKAGES, package_for
from evals import verify_corpus
from evals.harness.exemplar_verbs import (
    collisions,
    corpus_undemonstrated,
    lane_verbs,
    undemonstrated,
    verb_keyed_frameworks,
)
from evals.harness.fingerprint import IDENTIFIER_OF
from evals.harness.reference import load_corpus


@pytest.fixture(scope="module")
def corpus():
    return load_corpus(verify_corpus.CORPUS_DIR)


@pytest.fixture(scope="module")
def swept(corpus):
    return corpus_undemonstrated(corpus)


def test_every_package_is_swept_or_skipped_by_its_own_declaration():
    """No package can be silently left out of the sweep.

    A package composes a verb or carries an identifier, and
    ``IDENTIFIER_OF`` is where it says which. Reading the answer from there
    rather than from a list here is what makes a third package's arrival an
    edit to one table instead of two.
    """
    swept = set(verb_keyed_frameworks())
    skipped = {name for name in PACKAGES if IDENTIFIER_OF[name] is not None}
    assert swept | skipped == set(PACKAGES)
    assert not swept & skipped


def test_a_skipped_package_is_skipped_because_its_claims_carry_an_identifier():
    """The reason, asserted rather than trusted: a package left out of the sweep
    has to be one whose claims are identified by a catalog requirement, because
    such a claim composes no verb for an exemplar to disagree with."""
    for name in PACKAGES:
        if name in verb_keyed_frameworks():
            continue
        assert IDENTIFIER_OF[name] is not None


def test_every_lane_of_every_swept_package_works_at_least_one_draft():
    """A lane with no parsed draft would report perfect agreement, having
    compared nothing."""
    for framework in verb_keyed_frameworks():
        entries = {entry.lane: entry for entry in lane_verbs(framework)}
        assert set(entries) == set(package_for(framework).lanes)
        for lane, entry in entries.items():
            assert entry.exemplars, f"{framework} {lane} works no draft"


def test_a_sibling_is_a_demonstrated_verb_of_the_same_family(corpus):
    """``siblings`` is what makes a near miss near, so it has to mean that."""
    for entry in corpus_undemonstrated(corpus):
        for sibling in entry.siblings:
            assert family_of(sibling) == family_of(entry.verb)
            assert sibling != entry.verb


def test_the_undemonstrated_count_is_what_the_sweep_measured(swept):
    """47 of 243, 23 of them must-find. See this module's docstring when it
    moves."""
    assert len(swept) == 47
    assert sum(1 for entry in swept if entry.must_find) == 23


def test_the_two_populations_still_split_where_they_did(swept):
    """32 near misses and 15 with no neighbour. The split is the finding: a near
    miss wants a draft demonstrating the member, and the rest want a draft
    demonstrating the family at all."""
    near = [entry for entry in swept if entry.near_miss]
    assert len(near) == 32
    assert len(swept) - len(near) == 15


def test_every_case_carries_at_least_one_disagreement(corpus, swept):
    """The reach is why this is not three rows to re-bless by hand."""
    graded = {
        case.id
        for case in corpus
        if any(framework in case.frameworks for framework in verb_keyed_frameworks())
    }
    assert {entry.case for entry in swept} == graded


def test_a_swept_package_disagrees_with_its_own_corpus_and_no_other(corpus):
    """Each package's exemplars are compared to that package's reference set."""
    for framework in verb_keyed_frameworks():
        entries = undemonstrated(corpus, framework)
        assert {entry.framework for entry in entries} == {framework}


def test_the_exemplars_hold_exactly_one_collision():
    """One place and two actions, inside one lane's shipped drafts.

    Pinned at one because a second would mean a prompt edit introduced a pair
    the identity rule splits, which is the failure this whole module measures
    arriving from the side nobody grades.
    """
    found = [
        collision
        for framework in verb_keyed_frameworks()
        for collision in collisions(framework)
    ]
    assert len(found) == 1
    collision = found[0]
    assert collision.lane == "elevation-of-privilege"
    assert set(collision.verbs) == {"abuse-grant", "escalate"}
    assert collision.shared_element_ids


def test_a_collision_names_two_actions_of_one_family():
    """Two verbs of two families over one element set are two findings, not one
    disagreement about how to name a finding."""
    for framework in verb_keyed_frameworks():
        for collision in collisions(framework):
            left, right = collision.verbs
            assert left != right
            assert family_of(left) == family_of(right)
