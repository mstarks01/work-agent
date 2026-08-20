"""The closed vocabulary of attacker actions, and what counts as one action.

A **Claim**'s identity turns on what the attacker *does*, and the corpus says so
in its own words: read the ``note`` field on any ``no-match`` pair in
``evals/judge_calibration/build_pairs.py`` and it argues in verbs — "replaying a
stolen session is not credential guessing", "read and write are different
actions", "destruction rather than alteration". Those notes are the source this
vocabulary was derived from, one verb per distinction they draw.

**Why a closed set rather than free text.** An open verb is a second prose field
to compare, which is the problem this exists to remove. A closed set makes the
comparison an equality test, and makes an unrecognised action a schema failure
at the point it is written rather than a silent mismatch at the point it is
scored.

**What one verb means.** The action, never its object and never its
consequence. ``read`` and ``intercept`` are two verbs because reading at rest
and reading on the wire are two findings with two fixes; ``read`` and
``recover-credential`` are two verbs for the same reason, and the corpus
separates that exact pair on case 01. But "reads customer records" and "reads
the whole database" are one verb, because the object is carried by the
**Element** IDs beside it.

Measured, not asserted, and now over the whole corpus. All 243 reference claims
carry a verb. ``tests/test_evals_identity.py``'s ``FRONTIER`` prices the rule on
both errors at once: against ``endpoint subset`` alone it costs **one** more
false split of 200 labelled pairs and removes **twenty** of the 23 false merges
of 287 reference pairs. The three merges it does not break are recorded in
:data:`UNSEPARATED`, with the reason for each.
"""

from __future__ import annotations

from collections.abc import Iterable

#: The vocabulary, grouped by the question each family answers. The grouping is
#: for a reader and for :mod:`evals.harness.queue`'s rendering; nothing dispatches
#: on a family, because a family is a description of the verbs and not a
#: coarser verb. Adding a verb means adding it here and nowhere else.
FAMILIES: dict[str, tuple[str, ...]] = {
    # What did the attacker learn, and from where?
    "disclosure": ("read", "intercept", "elicit"),
    # What did the attacker do with, or to, a credential?
    "credential": ("recover-credential", "guess-credential", "use-credential"),
    # What did the attacker change, and where?
    "integrity": ("alter", "alter-in-transit", "inject", "plant", "delete"),
    # Whose identity did the attacker present?
    "identity": ("impersonate", "forge", "replay", "ride-session"),
    # What did the attacker take away?
    "availability": ("flood", "disable"),
    # What did the attacker reach that they were not entitled to?
    "authorization": ("escalate", "abuse-grant"),
    # Nobody acted: the record cannot say who did.
    "attribution": ("unattributable",),
}

#: Every verb, flattened. The one name any other module imports.
ACTION_VERBS: frozenset[str] = frozenset(
    verb for verbs in FAMILIES.values() for verb in verbs
)

#: One-line gloss per verb, shown to a reviewer beside the finding and to
#: whoever assigns a verb to a reference claim. Required for every verb —
#: ``test_evals_verbs.py`` checks the two sets match, because a verb with no
#: gloss is a verb two people will assign two ways.
GLOSS: dict[str, str] = {
    "read": "reads data where it rests, through a path they can already reach",
    "intercept": "reads data in flight, on the network path that carries it",
    "elicit": "makes the system emit data it holds, by what they send it",
    "recover-credential": "extracts a credential from wherever it is kept",
    "guess-credential": "arrives at a credential by guessing or brute force",
    "use-credential": "acts with a credential they hold but were not issued",
    "alter": "changes data where it rests",
    "alter-in-transit": "changes data in flight, on the path that carries it",
    "inject": "sends content the system interprets rather than stores",
    "plant": "places an artifact the system will later consume as its own",
    "delete": "destroys data, or the thing that data depends on",
    "impersonate": "presents as another principal to a system that believes it",
    "forge": "composes a message or token that verifies as authentic",
    "replay": "re-sends a genuine message the system accepts a second time",
    "ride-session": "acts through a principal's own authenticated session",
    "flood": "exhausts a resource with volume until it stops serving",
    "disable": "stops a component, or destroys what it depends on to run",
    "escalate": "reaches a privilege or a zone their position does not carry",
    "abuse-grant": "uses a grant they legitimately hold, beyond its purpose",
    "unattributable": "nothing in the record can say which principal acted",
}

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


class VerbError(ValueError):
    """A verb is not in the vocabulary, so nothing can be matched on it."""


def check_verb(verb: str) -> str:
    """Return ``verb`` if the vocabulary carries it, and raise if it does not.

    Fail-closed on purpose: a verb nobody recognises would make a claim compare
    equal to nothing, which reads downstream as a tool that found nothing rather
    than as a claim written wrong.
    """
    if verb not in ACTION_VERBS:
        raise VerbError(
            f"{verb!r} is not an action verb; the vocabulary is"
            f" {', '.join(sorted(ACTION_VERBS))}"
        )
    return verb


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


def family_of(verb: str) -> str:
    """Which family a verb belongs to, for rendering and for a reviewer."""
    check_verb(verb)
    return next(name for name, verbs in FAMILIES.items() if verb in verbs)


def unknown_verbs(verbs: Iterable[str]) -> tuple[str, ...]:
    """Every verb in ``verbs`` the vocabulary does not carry, sorted.

    The bulk form of :func:`check_verb`, for a loader checking a whole file: one
    error naming every bad verb beats a run of errors naming one each.
    """
    return tuple(sorted({verb for verb in verbs if verb not in ACTION_VERBS}))
