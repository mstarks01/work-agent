"""The measurement's half of the vocabulary: equivalence, and what it cannot separate.

The vocabulary itself ships in :mod:`analysis_service.actions` and is checked by
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


def test_the_shipped_equivalence_is_free_on_both_error_axes():
    """The table held one group as of 2026-09-10, and it was priced first.

    The table was empty from the first cut because every apparent synonym
    resolved to one verb once assigned from the action and its object class.
    The first Baseline showed a pair the labels cannot separate: forge, inject
    and plant. A group earns its place by costing nothing on either axis: no
    labelled non-match it merges, no reference pair the corpus records as two
    findings it calls one. Priced here against an empty table, through the
    pricing module's own readers, so a later group is held to the same bar.
    """
    from evals import verify_corpus
    from evals.harness.calibration import load_pairs
    from evals.harness.reference import load_corpus
    from evals.harness.run import _flows_by_case
    from evals.harness.verb_pricing import _labelled, _references

    assert EQUIVALENT == (frozenset({"forge", "inject", "plant"}),)
    corpus = load_corpus(verify_corpus.CORPUS_DIR)
    flows = _flows_by_case(corpus)
    pairs = load_pairs()
    bare_splits, bare_merges = _labelled(pairs, flows, ())
    splits, merges = _labelled(pairs, flows, EQUIVALENT)
    assert splits <= bare_splits
    assert len(merges) == len(bare_merges), "the group merges a labelled non-match"
    assert len(_references(corpus, flows, EQUIVALENT)) == len(
        _references(corpus, flows, ())
    ), "the group merges a reference pair the corpus records as two findings"


def test_canonical_is_identity_outside_the_shipped_group():
    grouped = set().union(*EQUIVALENT)
    assert all(canonical(verb) == verb for verb in ACTION_VERBS if verb not in grouped)
    assert {canonical(verb) for verb in grouped} == {min(grouped)}
    assert same_action("read", "read")
    assert not same_action("read", "intercept")


def test_the_unseparated_pairs_carry_a_reason_each():
    """Three, each with why — a rate would say the vocabulary is imperfect and
    nothing about which distinction it cannot draw."""
    assert len(UNSEPARATED) == 3
    for case, lane, reason in UNSEPARATED:
        assert case and lane
        assert len(reason) > 80, f"{case}/{lane} needs a reason, not a label"
