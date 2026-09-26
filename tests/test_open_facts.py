"""The open facts a report's conditional findings rest on, grouped for a reader.

Held against real archived reports, because the shapes that matter — a finding
waiting on several facts, a free-text subject cited by many findings, a flow
named by its endpoints — are the ones the critic actually writes.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from analysis_service.open_facts import open_facts, open_facts_by_framework
from analysis_service.report import Report

REPORTS = sorted(
    Path(__file__)
    .resolve()
    .parents[1]
    .glob("evals/baselines/6bff717-*/*.reports/*.report.json")
)


@pytest.fixture(scope="module", params=REPORTS[:4], ids=lambda path: path.name[:2])
def report(request):
    return Report.model_validate_json(request.param.read_text(encoding="utf-8"))


def _needs_info(block):
    return [claim for claim in block.claims if claim.verdict.status == "needs-info"]


def test_the_archive_is_there_to_read():
    assert len(REPORTS) >= 4


def test_every_conditional_finding_is_placed_exactly_once(report):
    for block in report.analyses:
        placed = Counter(
            claim_id
            for fact in open_facts(block, report.system_model)
            for claim_id in fact.placed
        )
        with_unknowns = {
            claim.id for claim in _needs_info(block) if claim.verdict.related_unknowns
        }
        assert set(placed) == with_unknowns
        assert set(placed.values()) <= {1}


def test_a_fact_settles_only_the_findings_that_wait_on_it_alone(report):
    for block in report.analyses:
        needs = {
            claim.id: {ref.key for ref in claim.verdict.related_unknowns}
            for claim in _needs_info(block)
        }
        for fact in open_facts(block, report.system_model):
            assert all(needs[claim_id] == {fact.key} for claim_id in fact.settles)
            assert set(fact.settles) <= set(fact.cited_by)
            assert all(fact.key in needs[claim_id] for claim_id in fact.cited_by)


def test_the_order_puts_the_answer_that_settles_most_first(report):
    for block in report.analyses:
        facts = open_facts(block, report.system_model)
        ranks = [
            (-len(fact.settles), -len(fact.cited_by), fact.label) for fact in facts
        ]
        assert ranks == sorted(ranks)


def test_a_flow_reads_as_its_two_endpoints(report):
    names = {element.id: element.name for element in report.system_model.elements()}
    flows = {flow.id: flow for flow in report.system_model.data_flows}
    for block in report.analyses:
        for fact in open_facts(block, report.system_model):
            element_id, attribute, _ = fact.key
            if element_id in flows:
                flow = flows[element_id]
                assert fact.label.startswith(
                    f"{names[flow.source]} → {names[flow.destination]}: "
                )
                assert attribute.replace("_", " ") in fact.label


def test_the_page_payload_is_json_per_framework(report):
    payload = open_facts_by_framework(report.analyses, report.system_model)
    assert set(payload) == {block.framework for block in report.analyses}
    for facts in payload.values():
        for fact in facts:
            assert set(fact) == {"key", "label", "cited_by", "settles", "placed"}
