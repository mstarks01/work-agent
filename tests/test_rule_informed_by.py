"""Each lane names the predicates whose facts inform its rules (ADR 0117).

A rule names a predicate in one of two ways: its package's
``PREDICATE_READERS`` names the rule that fires on a row of it, or the rule's
``informed_by`` names a predicate whose answer bears on the rule's finding.
The early list ranks a question kind by the second (:func:`reads`).
"""

from __future__ import annotations

import pytest

from analysis_service.assertions import REGISTRY
from analysis_service.candidates import Candidate
from analysis_service.claims import FrameworkName
from analysis_service.early_questions import load_prior, reads
from analysis_service.frameworks import PACKAGES, unprojected_predicates
from analysis_service.question_kinds import QUESTION_KINDS, parse_question_kind

#: Each predicate of a part of a question kind.
PARTS = frozenset(
    predicate for kind in QUESTION_KINDS.values() for _, predicate in kind.parts
)

#: Each predicate with no graph field that no rule of any package names. Each
#: package's ``PREDICATE_READERS`` gives the reason.
UNREAD = frozenset(
    {
        "administrative-authority",
        "authorization-grant",
        "credential-custody",
        "credential-lifetime",
        "represented-by",
        "tenant-ownership",
    }
)

#: Whether each package's prior names a question kind, so that its
#: ``informed_by`` rows move the early order. Where a value is False, the
#: package's ``INFORMED_BY`` comment, the ``early_questions`` docstring,
#: ``GLOSSARY.md`` and ADR 0117 say that its rows do not move the order.
MOVES_THE_EARLY_ORDER: dict[FrameworkName, bool] = {"stride": True, "asvs": False}


def lane_predicates(name: FrameworkName) -> dict[str, set[str]]:
    """Each lane of the package ``name``, with every predicate its rules name."""
    package = PACKAGES[name]
    lane_of = {rule.rule_id: rule.lane for rule in package.rules}
    named: dict[str, set[str]] = {lane: set() for lane in package.lanes}
    for rule in package.rules:
        named[rule.lane] |= rule.informed_by
    for predicate, reader in package.predicate_readers.items():
        if isinstance(reader, str):
            named[lane_of[reader]].add(predicate)
    return named


@pytest.mark.parametrize("name", sorted(PACKAGES))
def test_every_lane_names_a_predicate(name):
    assert [lane for lane, named in lane_predicates(name).items() if not named] == []


@pytest.mark.parametrize("name", sorted(PACKAGES))
def test_a_rule_names_only_registered_predicates(name):
    for rule in PACKAGES[name].rules:
        assert rule.informed_by <= REGISTRY.keys(), rule.rule_id


def test_every_part_informs_a_stride_rule():
    """The STRIDE table holds every part, so each kind can rank in its lanes.

    Only ``informed_by`` ranks a kind, so a ``PREDICATE_READERS`` rule does
    not count here."""
    named = set().union(*(rule.informed_by for rule in PACKAGES["stride"].rules))
    assert sorted(PARTS - named) == []


def test_every_predicate_with_no_graph_field_is_named_or_listed_unread():
    named = {
        predicate
        for name in PACKAGES
        for lane in lane_predicates(name).values()
        for predicate in lane
    }
    assert unprojected_predicates() - named == UNREAD


def test_a_kind_is_read_by_a_rule_that_names_one_of_its_parts():
    rule = next(
        rule
        for rule in PACKAGES["stride"].rules
        if rule.rule_id == "denial-of-service-internet-exposed-process"
    )
    candidate = Candidate(rule_id=rule.rule_id, lane=rule.lane, element_ids=("p",))
    assert reads(candidate, rule.informed_by, "capacity-limits")
    assert not reads(candidate, rule.informed_by, "audit-evidence")


def test_a_package_moves_the_early_order_only_where_its_prior_names_a_kind():
    prior = load_prior()
    names_a_kind = {
        name: any(
            parse_question_kind(field) is not None
            for rates in prior[name].rates.values()
            for field in rates
        )
        for name in PACKAGES
    }
    assert names_a_kind == MOVES_THE_EARLY_ORDER
