"""The vocabulary is a table, so it is checked against its own registry.

A table nobody compares to its registry fails as quietly as the ``if`` it
replaced — ``CLAUDE.md`` states that as a repository rule, and these are the
comparisons that make it decidable for :mod:`evals.harness.verbs`: every verb
has a gloss, every family is disjoint, and every verb named in
:data:`~evals.harness.verbs.EQUIVALENT` is a real one.

Deterministic and free of provider calls, so it gates on every PR.
"""

from __future__ import annotations

import pytest

from evals.harness.verbs import (
    ACTION_VERBS,
    EQUIVALENT,
    FAMILIES,
    GLOSS,
    UNSEPARATED,
    VerbError,
    canonical,
    check_verb,
    family_of,
    same_action,
    unknown_verbs,
)


def test_every_verb_has_a_gloss():
    """A verb with no gloss is a verb two people will assign two ways."""
    assert set(GLOSS) == set(ACTION_VERBS)


def test_families_partition_the_vocabulary():
    """No verb sits in two families, and none sits outside every family."""
    listed = [verb for verbs in FAMILIES.values() for verb in verbs]
    assert len(listed) == len(set(listed)), "a verb appears in two families"
    assert set(listed) == set(ACTION_VERBS)


def test_every_family_is_named_for_a_question_it_answers():
    """Families are a reader's index, so an empty one would index nothing."""
    assert all(verbs for verbs in FAMILIES.values())


def test_equivalence_groups_name_real_verbs():
    """An entry cannot rot into a typo that silently matches nothing."""
    for group in EQUIVALENT:
        assert not unknown_verbs(group), f"{group} names a verb outside the set"
        assert len(group) > 1, "a group of one is not an equivalence"


def test_the_equivalence_table_is_empty_and_that_is_recorded():
    """Pinned, because the emptiness is a measurement and not an oversight.

    Every apparent synonym over the calibration labels resolved to one verb once
    the verb was assigned from the action and its object class. If a later edit
    adds a group, this test is where the reason gets written down.
    """
    assert EQUIVALENT == ()


def test_canonical_is_identity_while_nothing_is_equivalent():
    assert all(canonical(verb) == verb for verb in ACTION_VERBS)
    assert same_action("read", "read")
    assert not same_action("read", "intercept")


def test_an_unknown_verb_fails_closed():
    """Never silently unmatched: a bad verb raises where it is written."""
    with pytest.raises(VerbError, match="is not an action verb"):
        check_verb("exfiltrate")
    with pytest.raises(VerbError):
        canonical("exfiltrate")
    assert unknown_verbs(["read", "exfiltrate", "nope"]) == ("exfiltrate", "nope")


def test_every_verb_resolves_to_a_family():
    assert all(family_of(verb) in FAMILIES for verb in ACTION_VERBS)


def test_the_unseparated_pairs_carry_a_reason_each():
    """Three, each with why — a rate would say the vocabulary is imperfect and
    nothing about which distinction it cannot draw."""
    assert len(UNSEPARATED) == 3
    for case, lane, reason in UNSEPARATED:
        assert case and lane
        assert len(reason) > 80, f"{case}/{lane} needs a reason, not a label"
