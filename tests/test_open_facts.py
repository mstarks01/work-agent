"""The open facts a report's conditional findings rest on, grouped for a reader.

Held against real archived reports, because the shapes that matter — a finding
waiting on several facts, a free-text subject cited by many findings, a flow
named by its endpoints — are the ones the critic actually writes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from analysis_service.claims import UnknownRef
from analysis_service.evidence import evidence_catalog
from analysis_service.open_facts import (
    ASSET_WORDS,
    element_names,
    gaps,
    label_of,
    reference_labels,
)
from analysis_service.report import Report
from analysis_service.system_model import CORE_ASSET_TAGS
from tests.factories import valid_model

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


def test_a_flow_reads_as_its_two_endpoints(report):
    from analysis_service.report_conditions import conditions

    names = {element.id: element.name for element in report.system_model.elements()}
    flows = {flow.id: flow for flow in report.system_model.data_flows}
    cards = conditions(report.analyses, report.system_model, [], [])
    for card in cards.values():
        for key, label, _ in card:
            if key[0] in flows:
                flow = flows[key[0]]
                source = names[flow.source]
                destination = names[flow.destination]
                assert source in label and destination in label
                assert label.index(source) < label.index(destination)
                assert "→" not in label
                assert label.endswith("?"), "a fact reads as the question it asks"


def test_a_flow_authentication_question_names_the_initiator():
    from analysis_service.claims import UnknownRef
    from tests.factories import valid_model

    model = valid_model()
    flow = model.data_flows[0]
    names = element_names(model)
    assert label_of(
        UnknownRef(element_id=flow.id, attribute="authentication"), names, model
    ) == (
        f"When {names[flow.source]} initiates a connection to "
        f"{names[flow.destination]}, what authentication, if any, is used?"
    )


def test_two_free_text_questions_are_two_facts():
    """A subject is part of an open fact's identity (#1225).

    Without it every free-text question keyed as ``("", "", "")``, so a report
    grouped all of them under whichever it met first.
    """
    from analysis_service.claims import UnknownRef

    first = UnknownRef(subject="whether queries are parameterized")
    second = UnknownRef(subject="whether an authorization policy is documented")
    assert first.key != second.key


@pytest.mark.parametrize("path", REPORTS, ids=lambda path: path.name[:2])
def test_every_free_text_subject_is_its_own_fact(path):
    from analysis_service.report_conditions import conditions

    report = Report.model_validate_json(path.read_text(encoding="utf-8"))
    for block in report.analyses:
        subjects = {
            ref.subject
            for claim in _needs_info(block)
            for ref in claim.verdict.related_unknowns
            if ref.subject and not ref.names_an_element and not ref.assertion
        }
        cards = conditions(report.analyses, report.system_model, [], [])
        asked = {
            key[3]
            for claim in _needs_info(block)
            for key, _, _ in cards[f"{block.framework}/{claim.id}"]
            if key[3]
        }
        assert asked == subjects


def test_every_asset_tag_has_its_words():
    assert set(ASSET_WORDS) == CORE_ASSET_TAGS


def test_an_attribute_a_model_names_reads_as_words():
    model = valid_model()
    flow = model.data_flows[0]
    labels = reference_labels(model)
    assert labels["encryption_in_transit"] == "encryption in transit"
    assert labels["pii"] == "personal data"
    name = element_names(model)[flow.id]
    assert labels[f"{flow.id}.authentication"] == f"{name}: authentication"
    assert (
        labels[f"authentication: {flow.authentication}"]
        == f"authentication: {flow.authentication}"
    )


#: The backtick spans in the archived claims that have no label: free words,
#: and a slug written without its element type.
UNLABELLED = {
    "audit",
    "audit log",
    "main",
    "unknown",
    "dispatch-requests",
    "live-job-status",
}


def test_every_other_backtick_span_in_the_archive_has_a_label():
    span = re.compile(r"`([^`\n]+)`")
    unlabelled = set()
    for path in REPORTS:
        report = Report.model_validate_json(path.read_text(encoding="utf-8"))
        catalog = report.assertions.catalog if report.assertions else None
        labels = reference_labels(report.system_model, catalog)
        for block in report.analyses:
            for claim in block.claims:
                spans = span.findall(claim.model_dump_json())
                unlabelled |= {text for text in spans if text not in labels}
    assert unlabelled == UNLABELLED


def test_every_unstated_attribute_is_in_one_gap(report):
    unstated = {
        (ground.element_id, ground.attribute)
        for ground in evidence_catalog(report.system_model).values()
        if ground.kind == "unknown-attribute"
    }
    listed = [
        (element_id, gap.attribute)
        for gap in gaps(report.system_model)
        for element_id in gap.element_ids
    ]
    assert sorted(listed) == sorted(unstated)
    counts = [len(gap.element_ids) for gap in gaps(report.system_model)]
    assert counts == sorted(counts, reverse=True)


def test_a_gap_names_each_element_by_its_open_fact_key():
    (gap, *_) = gaps(valid_model())
    (element, *_) = gap.to_json()["elements"]
    ref = UnknownRef(element_id=element["id"], attribute=gap.attribute)
    assert element["key"] == list(ref.key)


def _grant_catalog():
    from analysis_service.assertions import (
        Assertion,
        AssertionCatalog,
        Qualifier,
        Subject,
    )

    subject = Subject(
        id="principal:support-administrators",
        type="principal",
        label="Support administrators",
    )
    row = Assertion(
        subject=subject.id,
        predicate="authorization-grant",
        value="refund orders",
        scope=[
            Qualifier(kind="resource", value="orders"),
            Qualifier(kind="operation", value="refund"),
        ],
        basis="inferred",
        origin="assertion-pass",
        explanation="the description says they refund orders",
    )
    return AssertionCatalog(subjects=[subject], entries=[row]), row


def test_an_assertion_question_reads_as_its_predicate_question_never_its_reference():
    from analysis_service.assertions import UNKNOWN, assertion_id

    catalog, row = _grant_catalog()
    expected = (
        "What may Support administrators do, and to which resource?"
        " (resource: orders; operation: refund)"
    )
    for identity in (
        assertion_id(row),
        assertion_id(row.model_copy(update={"value": UNKNOWN})),
    ):
        label = label_of(UnknownRef(assertion=identity), {}, None, catalog)
        assert label == expected
        assert "assertion:" not in label


def test_an_assertion_the_catalog_does_not_hold_has_no_reference_in_its_label():
    from analysis_service.open_facts import UNWORDED_ASSERTION

    catalog, _ = _grant_catalog()
    identity = "assertion:authorization-grant~principal:customers~any~unknown"
    assert label_of(UnknownRef(assertion=identity), {}, None, catalog) == (
        UNWORDED_ASSERTION
    )
    assert label_of(UnknownRef(assertion=identity), {}) == UNWORDED_ASSERTION


def test_every_predicate_asks_a_question_about_its_subject():
    from analysis_service.assertions import REGISTRY

    for name, predicate in REGISTRY.items():
        assert predicate.question.count("{subject}") == 1, name
        assert predicate.question.endswith("?"), name


def test_assertions_share_one_group_and_subjects_share_another():
    from analysis_service.open_facts import group_of

    assert group_of(UnknownRef(assertion="assertion:a~b~any~unknown")) == group_of(
        UnknownRef(assertion="assertion:c~d~any~unknown")
    )
    assert group_of(UnknownRef(subject="one")) == group_of(UnknownRef(subject="two"))
    assert group_of(UnknownRef(subject="one")) != group_of(
        UnknownRef(assertion="assertion:a~b~any~unknown")
    )
