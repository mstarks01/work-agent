"""The closed vocabulary of attacker actions a **Claim** may name.

A claim's identity turns on what the attacker does. This is the set of answers a
framework with an open claim set may give, and it is closed on purpose. An open
verb is a second prose field to compare, which is the problem that naming the
action exists to remove. A closed set makes the comparison an equality test, and
makes an unrecognised action a schema failure where the claim is written, rather
than a silent mismatch wherever it is later matched.

One verb means the action, never its object and never its consequence. ``read``
and ``intercept`` are two verbs, because reading at rest and reading on the wire
are two findings with two fixes. ``read`` and ``recover-credential`` are two for
the same reason. "Reads customer records" and "reads the whole database" are one
verb, because the **Element** IDs beside it carry the object.

The vocabulary is service-side rather than eval-side, and that placement is the
point. The field is on :class:`~analysis_service.report.Claim`, so the
vocabulary that validates it has to ship in the same package.
``evals/harness/verbs.py`` reads this module and adds what only a measurement
needs: which verbs count as one action, and the pairs the corpus cannot
separate.

The vocabulary is framework-neutral. A package whose claims carry a catalog
identifier needs no verb at all, because the identifier already decides
identity, and composing a verb would add a field nothing reads. That is why
:class:`~analysis_service.report.Claim` leaves it optional, and why only a
package with an open claim set narrows it.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal, get_args

#: Every action a claim may name. A ``Literal`` rather than a runtime set so a
#: package can use it as a field type and let the schema refuse an unknown verb
#: before any code runs — the same shape ``StrideCategory`` already has.
ActionVerb = Literal[
    # What did the attacker learn, and from where?
    "read",
    "intercept",
    "elicit",
    # What did the attacker do with, or to, a credential?
    "recover-credential",
    "guess-credential",
    "use-credential",
    # What did the attacker change, and where?
    "alter",
    "alter-in-transit",
    "inject",
    "plant",
    "delete",
    # Whose identity did the attacker present?
    "impersonate",
    "forge",
    "replay",
    "ride-session",
    # What did the attacker take away?
    "flood",
    "disable",
    # What did the attacker reach that they were not entitled to?
    "escalate",
    "abuse-grant",
    # Nobody acted: the record cannot say who did.
    "unattributable",
]

#: The same set at runtime, derived from the type rather than spelled twice.
ACTION_VERBS: frozenset[str] = frozenset(get_args(ActionVerb))

#: The grouping a reader and a prompt use, keyed by the question each family
#: answers. Nothing dispatches on a family — a family is a description of the
#: verbs and not a coarser verb — so this exists to be *read*, by a person
#: choosing a verb and by the prompt that lists them.
FAMILIES: dict[str, tuple[ActionVerb, ...]] = {
    "disclosure": ("read", "intercept", "elicit"),
    "credential": ("recover-credential", "guess-credential", "use-credential"),
    "integrity": ("alter", "alter-in-transit", "inject", "plant", "delete"),
    "identity": ("impersonate", "forge", "replay", "ride-session"),
    "availability": ("flood", "disable"),
    "authorization": ("escalate", "abuse-grant"),
    "attribution": ("unattributable",),
}

#: One line per verb, in the second person, because these reach an agent as the
#: menu it picks from and a reviewer as the label beside a finding. Required for
#: every verb — ``tests/test_actions.py`` checks the two sets match, since a verb
#: with no gloss is one two writers will assign two ways.
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


class VerbError(ValueError):
    """A verb is not in the vocabulary, so nothing can be matched on it."""


def check_verb(verb: str) -> str:
    """Return ``verb`` if the vocabulary carries it, and raise if it does not.

    Fail-closed: a verb nobody recognises would make a claim compare equal to
    nothing, which reads downstream as a tool that found nothing rather than as
    a claim written wrong.
    """
    if verb not in ACTION_VERBS:
        raise VerbError(
            f"{verb!r} is not an action verb; the vocabulary is"
            f" {', '.join(sorted(ACTION_VERBS))}"
        )
    return verb


def family_of(verb: str) -> str:
    """Which family a verb belongs to, for rendering and for a reader."""
    check_verb(verb)
    return next(name for name, verbs in FAMILIES.items() if verb in verbs)


def unknown_verbs(verbs: Iterable[str]) -> tuple[str, ...]:
    """Every verb the vocabulary does not carry, sorted and de-duplicated.

    The bulk form of :func:`check_verb`, for a loader checking a whole file: one
    error naming every bad verb beats a run of errors naming one each.
    """
    return tuple(sorted({verb for verb in verbs if verb not in ACTION_VERBS}))


def menu() -> str:
    """The vocabulary as a prompt fragment: one line per family.

    **The glosses are deliberately not here.** Both tiers run
    ``constrain_output = true``, so the response schema carries this ``Literal``
    and the provider cannot return a verb outside it — the prompt does not need
    to spell the options to make them enforceable. What the prompt has to supply
    is the shape of the choice, which is the family split: an agent that knows
    disclosure is three verbs deep looks for which one, where an agent given a
    flat list of twenty picks the first that reads plausibly.

    Built rather than written out in ``frameworks/stride/output.md``, because a
    verb added above and missing from the prompt is a distinction an agent never
    learns it has to draw.
    """
    return "\n".join(
        f"- *{family}*: " + ", ".join(f"`{verb}`" for verb in verbs)
        for family, verbs in FAMILIES.items()
    )
