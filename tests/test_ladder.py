"""The format ladder's step rules, driven with no translator (ADR 0058).

The executor tests in ``test_schema_fallback.py`` drive the same rules through
a built Bedrock adapter. These drive :class:`~analysis_service.ladder.Ladder`
alone, so a rule fails here with nothing between the test and the rule.
"""

from __future__ import annotations

import pytest

from analysis_service.ladder import Ladder

EXTRACT = {"title": "SystemModel", "type": "object"}
CLAIMS = {"title": "Claims", "type": "object"}


def _ladder() -> Ladder:
    return Ladder(("native", "forced_tool", "offered_tool", "prompt"))


def test_an_empty_ladder_is_refused():
    with pytest.raises(ValueError, match="at least one rung"):
        Ladder(())


def test_a_pair_refusal_moves_every_schema_on_the_tier():
    ladder = _ladder()

    moved = ladder.moved_below(0, EXTRACT, "schema_field_refused")

    assert moved == 1
    assert ladder.rung == "forced_tool"
    assert ladder.step_for(CLAIMS) == 1


def test_a_schema_refusal_moves_only_the_refused_schema():
    ladder = _ladder()

    moved = ladder.moved_below(0, EXTRACT, "grammar_too_large")

    assert moved == 1
    assert ladder.rung == "native"
    assert ladder.step_for(EXTRACT) == 1
    assert ladder.step_for(CLAIMS) == 0


def test_a_schema_is_keyed_by_content_not_by_key_order():
    ladder = _ladder()
    ladder.moved_below(0, {"a": 1, "b": 2}, "grammar_too_large")

    assert ladder.step_for({"b": 2, "a": 1}) == 1


def test_a_refusal_on_the_last_rung_moves_nothing():
    ladder = Ladder(("native", "prompt"))
    ladder.moved_below(0, EXTRACT, "tools_refused")

    assert ladder.moved_below(1, EXTRACT, "tools_refused") is None
    assert ladder.rung == "prompt"


def test_a_late_refusal_from_a_higher_step_never_moves_the_tier_back_up():
    """Two lanes refused together: the second reports a step the first passed."""
    ladder = _ladder()
    ladder.moved_below(0, EXTRACT, "schema_field_refused")
    ladder.moved_below(1, EXTRACT, "forced_tool_refused")

    moved = ladder.moved_below(0, CLAIMS, "schema_field_refused")

    assert moved == 2
    assert ladder.rung == "offered_tool"


def test_the_tier_step_wins_over_a_lower_schema_step():
    ladder = _ladder()
    ladder.moved_below(0, EXTRACT, "grammar_too_large")
    ladder.moved_below(1, CLAIMS, "tools_refused")

    assert ladder.step_for(EXTRACT) == 2
