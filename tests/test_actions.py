"""The shipped action vocabulary: its invariants, where its docstring says they are.

:mod:`analysis_service.actions` ships — `Claim` carries the field it validates —
so the checks on it belong in a test named for it rather than beside a
measurement. The measurement's own half, which verbs count as one action and
what the corpus cannot separate, stays in ``tests/test_evals_verbs.py``.

The chain this guards has three links, and each one has a test somewhere:

1. every verb has a gloss and one family (**here**)
2. ``menu()`` covers the whole vocabulary (**here**)
3. ``frameworks/stride/output.md`` carries exactly ``menu()``
   (``test_prompt_lints.py``)

Break any link and a verb reaches the response schema — so a provider accepts
it — without reaching the prompt, so no agent learns the distinction exists.

Deterministic and free of provider calls, so it gates on every PR.
"""

from __future__ import annotations

import pytest

from analysis_service.actions import (
    ACTION_VERBS,
    FAMILIES,
    GLOSS,
    ActionVerb,
    VerbError,
    check_verb,
    family_of,
    menu,
    unknown_verbs,
)


def test_the_runtime_set_is_the_type():
    """Derived rather than spelled twice, so the two cannot disagree."""
    from typing import get_args

    assert ACTION_VERBS == frozenset(get_args(ActionVerb))


def test_every_verb_has_a_gloss():
    """A verb with no gloss is one two writers will assign two ways."""
    assert set(GLOSS) == set(ACTION_VERBS)


def test_families_partition_the_vocabulary():
    """No verb sits in two families, and none sits outside every family."""
    listed = [verb for verbs in FAMILIES.values() for verb in verbs]
    assert len(listed) == len(set(listed)), "a verb appears in two families"
    assert set(listed) == set(ACTION_VERBS)


def test_every_family_carries_verbs():
    """Families are a reader's index, so an empty one would index nothing."""
    assert all(verbs for verbs in FAMILIES.values())


def test_every_verb_resolves_to_a_family():
    assert all(family_of(verb) in FAMILIES for verb in ACTION_VERBS)


def test_an_unknown_verb_fails_closed():
    """Never silently unmatched: a bad verb raises where it is written."""
    with pytest.raises(VerbError, match="is not an action verb"):
        check_verb("exfiltrate")
    assert unknown_verbs(["read", "exfiltrate", "nope"]) == ("exfiltrate", "nope")


def test_the_menu_names_every_verb():
    """Link 2 of the chain, and the one nothing else would catch.

    ``test_prompt_lints.py`` asserts the prompt matches ``menu()``. If ``menu()``
    itself dropped a family, that test would happily require the prompt to match
    the shortened menu and both would be wrong together.
    """
    rendered = menu()
    missing = sorted(verb for verb in ACTION_VERBS if f"`{verb}`" not in rendered)
    assert not missing, f"menu() omits {', '.join(missing)}"
    assert all(family in rendered for family in FAMILIES)


def test_the_menu_is_one_line_per_family():
    """Its size is a prompt budget line item; a shape change is a budget change."""
    lines = menu().splitlines()
    assert len(lines) == len(FAMILIES)
    assert all(line.startswith("- *") for line in lines)


def test_a_claim_refuses_a_verb_outside_the_vocabulary():
    """The end of the chain: the shipped model is what actually enforces it."""
    from pydantic import ValidationError

    from analysis_service.frameworks.stride.record import ThreatProposal

    common = {
        "sequence": 1,
        "title": "t",
        "description": "d",
        "affected_element_ids": ["process:a"],
        "evidence_refs": ["e"],
        "severity": {"likelihood": "high", "impact": "high", "justification": "j"},
    }
    assert ThreatProposal(**common, verb="read").verb == "read"
    with pytest.raises(ValidationError):
        ThreatProposal(**common, verb="exfiltrate")
