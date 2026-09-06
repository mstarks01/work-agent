"""What counts as one action, and the pairs this corpus cannot separate.

The vocabulary itself is :mod:`analysis_service.actions`, and it lives there
because :class:`~analysis_service.report.Claim` carries the field: a vocabulary
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
#: **Deliberately empty, and that is a finding rather than a stub.** The obvious
#: candidates — "recovers the partner keys" against "reads the partner keys",
#: "erases the log records" against "deletes them" — turn out not to need an
#: entry: both sides resolve to one verb once the verb is assigned from the
#: action and its object class, which is what :data:`GLOSS` is for. Every pair
#: that looked like a synonym in the screen over the calibration labels was an
#: assignment that had not been made yet, not two verbs meaning one thing.
#:
#: The mechanism stays because the next vocabulary edit may need it and a table
#: added under pressure is a table added wrong. ``test_evals_verbs.py`` checks
#: every verb named here is a real one, so an entry cannot rot into a typo.
EQUIVALENT: tuple[frozenset[str], ...] = ()

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


def canonical(verb: str) -> str:
    """The representative of ``verb``'s equivalence group, or ``verb`` itself.

    The representative is the alphabetically first member of the group, so the
    answer does not depend on the order :data:`EQUIVALENT` happens to list.
    """
    check_verb(verb)
    for group in EQUIVALENT:
        if verb in group:
            return min(group)
    return verb


def same_action(left: str, right: str) -> bool:
    """Do these two verbs name one action, through :data:`EQUIVALENT`?"""
    return canonical(left) == canonical(right)
