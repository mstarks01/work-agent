"""The measurement's half of the vocabulary: equivalence, and what it cannot separate.

The vocabulary itself ships in :mod:`stride_service.actions` and is checked by
``tests/test_actions.py`` — every verb glossed, every family disjoint, the menu
covering the set. What is left here is what only a measurement has: which verbs
count as **one** action, and the three corpus pairs the rule cannot tell apart.

A table nobody compares to its registry fails as quietly as the ``if`` it
replaced, which is why :data:`~evals.harness.verbs.EQUIVALENT` is checked
against the shipped set rather than trusted.

Deterministic and free of provider calls, so it gates on every PR.
"""

from __future__ import annotations

from evals.harness.verbs import (
    ACTION_VERBS,
    EQUIVALENT,
    UNSEPARATED,
    canonical,
    same_action,
    unknown_verbs,
)


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


def test_the_unseparated_pairs_carry_a_reason_each():
    """Three, each with why — a rate would say the vocabulary is imperfect and
    nothing about which distinction it cannot draw."""
    assert len(UNSEPARATED) == 3
    for case, lane, reason in UNSEPARATED:
        assert case and lane
        assert len(reason) > 80, f"{case}/{lane} needs a reason, not a label"
