"""The follow-up orders the comparison reads, on the #1289 review's cases."""

from __future__ import annotations

import pytest

from analysis_service.bands import Band
from analysis_service.questions import FollowUpNeeds
from evals.harness.follow_up_policies import POLICIES

CRITICAL, LOW = 0, -3


def key(name: str) -> tuple[str, ...]:
    return ("", "", "", name, "", "")


def needs(evidence, named=None, waiting=None, bands=None) -> FollowUpNeeds:
    named = named or {}
    waiting = waiting if waiting is not None else {**evidence, **named}
    return FollowUpNeeds(
        evidence=evidence,
        named=named,
        waiting=waiting,
        bands={f: Band(order=o, label=str(o)) for f, o in bands.items()},
        refs={},
    )


def order(policy: str, of: FollowUpNeeds) -> list[str]:
    return [k[3] for _, k in POLICIES[policy](of)]


A, B, C = key("A"), key("B"), key("C")
PAIR = needs(
    {("stride", "crit"): frozenset({A, B}), ("stride", "low"): frozenset({C})},
    bands={("stride", "crit"): CRITICAL, ("stride", "low"): LOW},
)


def test_the_shipped_order_asks_a_low_singleton_before_a_critical_pair():
    """The review's counterexample, as shipped: C, then A and B."""
    assert order("shipped", PAIR) == ["C", "A", "B"]


@pytest.mark.parametrize("policy", ["band-first", "band-first-material"])
def test_band_first_asks_the_critical_pair_first(policy):
    assert order(policy, PAIR) == ["A", "B", "C"]


def test_a_critic_only_critical_fact_waits_behind_evidence_unless_the_order_is_material():
    critic_only = needs(
        {("stride", "low"): frozenset({C})},
        named={("stride", "crit"): frozenset({A})},
        bands={("stride", "crit"): CRITICAL, ("stride", "low"): LOW},
    )
    assert order("band-first", critic_only) == ["C", "A"]
    assert order("band-first-material", critic_only) == ["A", "C"]


def test_a_material_order_keeps_each_question_s_basis():
    critic_only = needs(
        {("stride", "low"): frozenset({C})},
        named={("stride", "crit"): frozenset({A})},
        bands={("stride", "crit"): CRITICAL, ("stride", "low"): LOW},
    )
    labels = {k[3]: basis for basis, k in POLICIES["band-first-material"](critic_only)}
    assert labels == {"A": "critic", "C": "evidence"}


@pytest.mark.parametrize("policy", list(POLICIES))
def test_every_order_asks_each_open_fact_once(policy):
    keys = [k for _, k in POLICIES[policy](PAIR)]
    assert sorted(keys) == sorted({A, B, C})
