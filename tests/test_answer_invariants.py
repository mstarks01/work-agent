"""An attribute answer is the effective fact for every reader, whatever the catalog held.

The questions audit (#1289, Q1) found an answer that the model took and the
catalog then wrote over in ``prepare``, and every function's own test agreed
with itself. So this module drives the real resume graph from ``prepare`` once
for each shape a catalog can hold about one attribute, and asks every reader
the same question: the report's model, its catalog, and the view a later
reader such as the post-review reassessment prepares from them.

**The table is keyed by** :data:`~analysis_service.assertions.PROJECTION_EFFECT`.
A projection reason added tomorrow has no row here, and the completeness test
fails until one is written.
"""

from __future__ import annotations

import pytest

from analysis_service.assertions import (
    ABSENT,
    PROJECTION_EFFECT,
    UNKNOWN,
    Assertion,
    AssertionCatalog,
    AssertionRecord,
    Qualifier,
    Subject,
    project,
    projected_attribute,
    support_span,
)
from analysis_service.evidence import prepared_view
from analysis_service.jobs import Checkpoint
from analysis_service.questions import FactAnswer
from analysis_service.sources import Source
from tests.factories import DESCRIPTION_TEXT, valid_model
from tests.test_questions import resume

FLOW = valid_model().data_flows[0].id
CREDENTIAL = "credential:session-cookie"
FACTS = Source(
    kind="description",
    label="Flow facts",
    text=(
        "TLS protects the order flow. The order flow is not encrypted."
        " We are not sure how the order flow is protected."
        " Shoppers sign in with a password and present a session cookie."
    ),
)
SOURCES = [Source.description(DESCRIPTION_TEXT), FACTS]


def row(predicate: str, value: str, quote: str | None, **overrides) -> Assertion:
    """One row about the flow, quoting ``quote`` from :data:`FACTS`."""
    support = [] if quote is None else [support_span(quote, FACTS.label, FACTS.text)]
    fields = {
        "subject": FLOW,
        "predicate": predicate,
        "value": value,
        "basis": "stated",
        "support": support,
    }
    return Assertion(**{**fields, **overrides})


def tls(**overrides) -> Assertion:
    return row(
        "transport-encryption", "TLS", "TLS protects the order flow", **overrides
    )


def mechanism(value: str) -> Assertion:
    return row("authentication-mechanism", value, "Shoppers sign in with a password")


def credential() -> Assertion:
    return row("credential-presented", CREDENTIAL, "present a session cookie")


#: Each projection reason, as the rows that make it, and the attribute they reach.
SHAPES: dict[str, tuple[str, list[Assertion]]] = {
    "stated": ("encryption_in_transit", [tls()]),
    "absent": (
        "encryption_in_transit",
        [
            row(
                "transport-encryption",
                ABSENT,
                "The order flow is not encrypted",
            )
        ],
    ),
    "unknown": (
        "encryption_in_transit",
        [row("transport-encryption", UNKNOWN, None, reason="silent")],
    ),
    "hedged": (
        "encryption_in_transit",
        [
            row(
                "transport-encryption",
                UNKNOWN,
                "We are not sure how the order flow is protected",
                reason="hedged",
            )
        ],
    ),
    "scoped": (
        "encryption_in_transit",
        [tls(scope=[Qualifier(kind="environment", value="staging")])],
    ),
    "several-values": (
        "encryption_in_transit",
        [
            tls(),
            row("transport-encryption", ABSENT, "The order flow is not encrypted"),
        ],
    ),
    "several-predicates": ("authentication", [mechanism(ABSENT), credential()]),
    "compatible": ("authentication", [mechanism("a password"), credential()]),
    "unsupported": (
        "encryption_in_transit",
        [tls(assessment="unsupported", assessor="audit")],
    ),
    "legacy": ("encryption_in_transit", [tls(basis="legacy", support=[])]),
}

#: Each answer an attribute can take: a value, the opposite, and a revision.
ANSWERS = {
    "encryption_in_transit": ("TLS 1.3", "none"),
    "authentication": ("mutual TLS", "none"),
}


def checkpoint(rows: list[Assertion]) -> Checkpoint:
    catalog = AssertionCatalog(
        subjects=[
            Subject(id=FLOW, type="interaction", label="place order"),
            Subject(id=CREDENTIAL, type="credential", label="session cookie"),
        ],
        entries=rows,
    )
    model = valid_model()
    for attribute in ANSWERS:
        setattr(model.data_flows[0], attribute, UNKNOWN)
    return Checkpoint(
        system_model=model,
        assertions=AssertionRecord(proposed=len(rows), catalog=catalog),
    )


def answer(attribute: str, value: str) -> FactAnswer:
    return FactAnswer(key=(FLOW, attribute, "", "", ""), value=value)


def reaching(report, attribute: str) -> list[Assertion]:
    return [
        entry
        for entry in report.assertions.catalog.entries
        if entry.subject == FLOW
        and projected_attribute(entry.predicate, entry.subject) == attribute
    ]


def effective(report, attribute: str) -> str:
    """What every reader of the report sees for the attribute."""
    model = report.system_model
    prepared, _, _ = prepared_view(model, report.assertions.catalog)
    held = getattr(model.data_flows[0], attribute)
    again = getattr(prepared.data_flows[0], attribute)
    assert held == again, "the report's model and a later reader's view disagree"
    return held


def test_every_projection_reason_has_a_shape():
    assert set(SHAPES) == set(PROJECTION_EFFECT)


@pytest.mark.parametrize("reason", sorted(SHAPES))
def test_each_shape_projects_its_reason_without_an_answer(reason):
    """A control: the fixture holds the shape it is named after."""
    attribute, rows = SHAPES[reason]
    report = resume(checkpoint(rows), given=SOURCES)

    reasons = {
        p.reason
        for p in project(report.assertions.catalog)
        if (p.element_id, p.attribute) == (FLOW, attribute)
    }
    assert reason in reasons


@pytest.mark.parametrize("value_index", [0, 1], ids=["value", "opposite"])
@pytest.mark.parametrize("reason", sorted(SHAPES))
def test_an_answer_is_the_fact_every_reader_sees(reason, value_index):
    attribute, rows = SHAPES[reason]
    given = answer(attribute, ANSWERS[attribute][value_index])

    report = resume(checkpoint(rows), [given], given=SOURCES)

    assert effective(report, attribute) == given.value
    assert reaching(report, attribute) == []
    superseded = [
        issue
        for issue in report.assertions.issues
        if issue.code == "superseded-by-answer"
    ]
    assert len(superseded) == len(rows)


@pytest.mark.parametrize("reason", sorted(SHAPES))
def test_the_answer_holds_in_a_later_round_and_a_revision_replaces_it(reason):
    attribute, rows = SHAPES[reason]
    first_value, second_value = ANSWERS[attribute]
    first = answer(attribute, first_value)
    report = resume(checkpoint(rows), [first], given=SOURCES)
    parent = Checkpoint(system_model=report.system_model, assertions=report.assertions)

    kept = resume(parent, earlier=[first], given=SOURCES)
    revised = resume(parent, [answer(attribute, second_value)], [first], SOURCES)

    assert effective(kept, attribute) == first_value
    assert effective(revised, attribute) == second_value
    for later in (kept, revised):
        assert reaching(later, attribute) == []
        assert "superseded-by-answer" in {i.code for i in later.assertions.issues}


@pytest.mark.parametrize("reason", sorted(SHAPES))
def test_i_do_not_know_changes_nothing_the_catalog_says(reason):
    attribute, rows = SHAPES[reason]
    silent = resume(checkpoint(rows), given=SOURCES)

    report = resume(checkpoint(rows), [answer(attribute, UNKNOWN)], given=SOURCES)

    assert effective(report, attribute) == effective(silent, attribute)
    assert reaching(report, attribute) == reaching(silent, attribute)
    assert "superseded-by-answer" not in {i.code for i in report.assertions.issues}
