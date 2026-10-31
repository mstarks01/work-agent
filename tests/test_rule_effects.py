"""Each rule says what an answer about a fact it reads informs (#2029 package 8)."""

from __future__ import annotations

import pytest

from analysis_service.candidates import Rule, with_effects
from analysis_service.frameworks import PACKAGES


def _rule(rule_id: str) -> Rule:
    return Rule(
        rule_id=rule_id, lane="spoofing", question="?", find=lambda *_: iter(())
    )


@pytest.mark.parametrize("name", sorted(PACKAGES))
def test_every_rule_of_every_package_has_an_effect_unlike_its_question(name):
    for rule in PACKAGES[name].rules:
        assert rule.effect.startswith("Informs "), rule.rule_id
        assert rule.effect != rule.question


def test_a_rule_with_no_effect_is_refused():
    with pytest.raises(ValueError, match="no effect sentence"):
        with_effects((_rule("a"), _rule("b")), {"a": "Informs a."})


def test_an_effect_for_no_rule_is_refused():
    with pytest.raises(ValueError, match="for no rule"):
        with_effects((_rule("a"),), {"a": "Informs a.", "gone": "Informs b."})
