"""A framework added later gets an intake path with no edit to the planner (#1542 F).

A third package, built here and registered for one test, has what the two
shipped packages do not: three bands, a dense catalog where each capability
settles many units, an empty question prior, capabilities it shares with ASVS
and capabilities of its own, and a precondition that reads a field no prior
names (a flow's ``operations``). The tests drive the production planner with
two and three packages selected.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import replace
from typing import Any, ClassVar

import pytest

from analysis_service.answer_round import EARLY_RULES, question_set
from analysis_service.answer_sets import AnswerSet
from analysis_service.capabilities import CapabilityNeed
from analysis_service.claims import UnknownRef
from analysis_service.early_questions import QUESTION_PRIOR, PriorRow
from analysis_service.fact_answers import FactAnswer
from analysis_service.frameworks import PACKAGES
from analysis_service.frameworks.asvs.record import DraftRequirementRuling
from analysis_service.system_model import SystemModel
from tests.factories import carrying, valid_model

THIRD = "third"
SHARED = ("authentication", "oauth")
#: Top-level capabilities ASVS level 1 never asks, so only this package does.
OWN = ("cache-service", "administrative-interface")


class ThirdRecord(DraftRequirementRuling):
    """A record whose units are dense: each capability settles 40 of them, in
    one of three bands."""

    NEEDS: ClassVar[Mapping[str, CapabilityNeed]] = {
        "authentication": CapabilityNeed(units=40, band=0),
        "oauth": CapabilityNeed(units=40, band=-1),
        "cache-service": CapabilityNeed(units=40, band=-2),
        "administrative-interface": CapabilityNeed(units=1, band=0),
    }

    @classmethod
    def open_capabilities(
        cls, model: SystemModel, options: Mapping[str, Any]
    ) -> dict[str, CapabilityNeed]:
        del options
        known = model.capability_facts()
        return {
            key: need
            for key, need in cls.NEEDS.items()
            if key not in known or known[key].state == "unknown"
        }

    @classmethod
    def idle(cls, model: SystemModel, options: Mapping[str, Any], lane: str) -> bool:
        del model, options, lane
        return True


def _operations_stated(model: SystemModel) -> str:
    unstated = [flow for flow in model.data_flows if flow.operations == "unknown"]
    return "undecidable" if unstated else "satisfied"


def _operations_facts(model: SystemModel) -> tuple[UnknownRef, ...]:
    return tuple(
        UnknownRef(element_id=flow.id, attribute="operations")
        for flow in model.data_flows
        if flow.operations == "unknown"
    )


@pytest.fixture
def third(monkeypatch):
    package = replace(
        PACKAGES["asvs"],
        name=THIRD,
        bands=("high", "middle", "low"),
        record=ThirdRecord,
        precondition=_operations_stated,
        precondition_facts=_operations_facts,
    )
    carrying(monkeypatch, package)
    # The planner's modules read the registry by the name they imported.
    from analysis_service import answer_round, early_questions
    from analysis_service.frameworks import PACKAGES as registered

    held = {**registered, THIRD: package}
    monkeypatch.setattr(early_questions, "PACKAGES", held)
    monkeypatch.setattr(answer_round, "PACKAGES", held)
    return package


PRIOR: dict[str, PriorRow] = {name: row for name, row in QUESTION_PRIOR.items()}
PRIOR[THIRD] = PriorRow(runs=(), revision=None, cases=0, rates={})
ALL = {"stride": {}, "asvs": {"level": 2}, THIRD: {"level": 2}}


def _paused(selection, answers=(), model=None):
    from analysis_service import early_questions

    # The question set reads the planner's default prior; the third package's
    # row is an empty one, as ``run.py question-prior`` writes for a package
    # with no archived run.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            early_questions.early_questions,
            "__defaults__",
            (PRIOR,),
        )
        return question_set(
            model or valid_model(),
            None,
            selection,
            (),
            waiting=True,
            answered=AnswerSet(facts=tuple(answers)),
            final=False,
            shown=(),
        )


def _answer_the_gate(selection):
    first = _paused(selection)
    return [FactAnswer(key=q.key, value="write") for q in first.early if q.gates]


def test_an_undecidable_package_asks_its_gate_first_and_nothing_else(third):
    asked = _paused(ALL)
    assert dict(asked.gates) == {
        "asvs": "satisfied",
        "stride": "satisfied",
        THIRD: "undecidable",
    }
    gate = [q for q in asked.early if q.gates]
    assert [q.key[1] for q in gate] == ["operations"]
    assert asked.early[0] in gate, "the gate question comes first"
    assert not any(THIRD in q.frameworks and not q.gates for q in asked.early)
    assert any("asvs" in q.frameworks for q in asked.early), "the others still ask"


def test_its_gate_answer_lets_it_ask_and_shares_a_question_once(third):
    selection = {"asvs": {"level": 1}, THIRD: {"level": 1}}
    asked = _paused(selection, _answer_the_gate(selection))
    assert asked.gates[THIRD] == "satisfied"
    listed = {
        q.key[-1]: q.frameworks
        for q in (*asked.early, *asked.held_back, *asked.below_floor)
        if q.kind == "capability"
    }
    for key in SHARED:
        assert set(listed[key]) == {"asvs", THIRD}, key
    for key in OWN:
        assert listed[key] == (THIRD,), key
    keys = [q.key for q in (*asked.early, *asked.held_back, *asked.below_floor)]
    assert len(keys) == len(set(keys)), "a shared fact is asked once"


def test_no_selected_package_is_starved_of_the_first_round(third):
    """Each package with a question is served in the first round, though they
    share one limit of each kind."""
    asked = _paused(ALL, _answer_the_gate(ALL))
    served = Counter(name for q in asked.early for name in q.frameworks)
    assert served["stride"] and served["asvs"] and served[THIRD]
    capabilities = [q for q in asked.early if q.kind == "capability" and not q.parent]
    assert len(capabilities) <= EARLY_RULES["capability"].per_round


def test_a_package_with_an_empty_prior_asks_no_field_question(third):
    selection = {THIRD: {"level": 2}}
    asked = _paused(selection, _answer_the_gate(selection))
    assert asked.early, "a control: its capabilities are asked"
    assert all(q.kind == "capability" for q in asked.early)
    assert not asked.below_floor or all(
        q.kind == "capability" for q in asked.below_floor
    )


def test_two_packages_ask_what_three_ask_for_them(third):
    """Adding a package that is not runnable takes nothing from the others."""
    two = _paused({"stride": {}, "asvs": {"level": 2}})
    three = _paused(ALL)
    assert [q.key for q in three.early if not q.gates] == [q.key for q in two.early]


def test_another_package_s_bands_do_not_move_a_framework_s_floor(third):
    """With ASVS level 1 alone, 24 capability questions that only ASVS asks sit
    under the floor. Before ADR 0071, selecting this package with its three
    bands moved every one of them over it. Each framework's bands are now read
    apart."""

    def under(asked):
        return {
            q.key[-1]
            for q in asked.below_floor
            if q.kind == "capability" and q.frameworks == ("asvs",)
        }

    alone = _paused({"asvs": {"level": 1}})
    selection = {"asvs": {"level": 1}, THIRD: {"level": 1}}
    both = _paused(selection, _answer_the_gate(selection))
    assert len(under(alone)) == 24, "a control: questions sit under the floor"
    assert under(both) == under(alone)
    own_low = [q for q in both.below_floor if q.frameworks == (THIRD,)]
    assert not own_low, "the package's own highest band still passes"
