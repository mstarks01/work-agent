"""What counts as one action, and the pairs this corpus cannot separate.

The vocabulary itself is :mod:`analysis_service.actions`, and it lives there
because :class:`~analysis_service.claims.Claim` carries the field: a vocabulary
that validates a shipped model has to ship with it. This module is the
measurement's half. It says which verbs count as one action for matching, and
records what the rule cannot do.

The figures are measured rather than asserted, over the whole corpus. All 243
reference claims carry a verb. ``tests/test_evals_identity.py``'s ``FRONTIER``
prices the rule on every error at once. Against ``endpoint subset`` alone,
the verb adds no false split over 200 labelled pairs, and removes
78 of the 81 false merges of
111 candidate negatives, and twenty of the 23 false merges of 287 reference
pairs. :class:`~evals.harness.identity.SubsetVerbIdentity` scores
294/311 against the recorded labels, where element agreement alone scores
201/311.

The candidate column is the one that argues for the vocabulary. Priced on
reference pairs alone, the element rule merges 23 of 287 and reads as
survivable. Priced on the paraphrases a live run emits, it merges 81 of 111.
"""

from __future__ import annotations

from collections.abc import Sequence

from analysis_service.actions import (
    ACTION_VERBS,
    FAMILIES,
    GLOSS,
    ActionVerb,
    VerbError,
    check_verb,
    family_of,
    unknown_verbs,
)

__all__ = [
    "ACTION_VERBS",
    "EQUIVALENT",
    "FAMILIES",
    "GLOSS",
    "UNSEPARATED",
    "ActionVerb",
    "VerbError",
    "canonical",
    "check_verb",
    "family_of",
    "same_action",
    "unknown_verbs",
]

#: Verbs that count as **one** action for matching, as a set of groups.
#:
#: **One group, and it was priced before it shipped.** The table was empty
#: from the vocabulary's first cut, because every apparent synonym over the
#: calibration labels resolved to one verb once the verb was assigned from the
#: action and its object class, which is what :data:`GLOSS` is for. The first
#: Baseline (#728) then showed a pair the labels cannot separate at any price:
#: ``forge``, ``inject`` and ``plant`` name the same finding in five reference
#: rows and two lane drafts, and ``run.py price-verbs`` measured the merge at
#: zero new false merges over the labelled pairs and zero new reference merges
#: over the corpus, for four more matched references (#730). A merge that
#: costs nothing on either error axis is the one this mechanism exists for.
#:
#: The fingerprint reads a verb through :func:`canonical` at the version
#: :data:`~evals.harness.fingerprint.VERSION_FOR` names, so a vote on a
#: ``forge`` claim reaches an ``inject`` one: the matcher and the vote key are
#: one reader of "one action". ``test_evals_verbs.py`` checks every verb named
#: here is a real one and that the shipped table is still free on both axes.
EQUIVALENT: tuple[frozenset[str], ...] = (frozenset({"forge", "inject", "plant"}),)

#: The reference-claim pairs this vocabulary does **not** separate, with why.
#: Kept whole rather than counted, for the reason
#: :class:`~evals.harness.calibration.Disagreement` is: three is a list a person
#: reads and acts on, and a rate is a number they cannot.
UNSEPARATED: tuple[tuple[str, str, str], ...] = (
    (
        "01-payments-checkout",
        "elevation-of-privilege",
        (
            "Two escalations across one boundary. The corpus itself treats them"
            " as adjacent — its calibration note says assignment decides which"
            " one a threat consumes — so this is arguably a correct merge rather"
            " than a miss, and it is what #201's `mechanism` would rule on."
        ),
    ),
    (
        "08-sso-identity-broker",
        "repudiation",
        (
            "Two absence-of-record conditions. No attacker acts in either, so no"
            " verb applies; what separates them is which control is missing. The"
            " `unattributable` verb names the whole lane rather than one action."
        ),
    ),
    (
        "08-sso-identity-broker",
        "denial-of-service",
        (
            "The source never states the attacker's means for one side, so both"
            " take `disable` conservatively. A corpus wording gap, fixable in a"
            " blessing pass, not a gap in the vocabulary."
        ),
    ),
)


def canonical(verb: str, equivalent: Sequence[frozenset[str]] | None = None) -> str:
    """The representative of ``verb``'s equivalence group, or ``verb`` itself.

    The representative is the alphabetically first member of the group, so the
    answer does not depend on the order :data:`EQUIVALENT` happens to list.

    ``equivalent`` is the shipped table unless a caller passes one. A candidate
    table is how :mod:`evals.harness.verb_pricing` prices a merge before it is
    adopted, through the same reader the shipped rule uses.
    """
    check_verb(verb)
    for group in EQUIVALENT if equivalent is None else equivalent:
        if verb in group:
            return min(group)
    return verb


def same_action(
    left: str, right: str, equivalent: Sequence[frozenset[str]] | None = None
) -> bool:
    """Do these two verbs name one action, through :data:`EQUIVALENT` or ``equivalent``?"""
    return canonical(left, equivalent) == canonical(right, equivalent)
