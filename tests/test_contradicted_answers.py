"""An open row the graph contradicts, or another row disputes, takes an answer.

The maintainer's ruling of 2026-11-18 on item 2 (#2270): a contradicted or
disputed row that reaches a graph field is asked as that attribute's Fact
Question, under the attribute key, and the attribute path writes the answer. A
disputed row with no graph field is asked under the key of its place, the
identity its row takes when it holds ``unknown``. Either key stays the same
across a resumed run, so the answer survives it.
"""

from __future__ import annotations

from analysis_service.assertions import (
    ABSENT,
    UNKNOWN,
    Assertion,
    AssertionCatalog,
    AssertionRecord,
    Subject,
    assertion_id,
)
from analysis_service.claims import FrameworkAnalysis, UnknownRef, Verdict
from analysis_service.fact_answers import FactAnswer
from analysis_service.fact_writes import check_fact_answers
from analysis_service.jobs import Checkpoint
from analysis_service.open_facts import source_states
from analysis_service.questions import fact_questions
from analysis_service.report_conditions import conditions, corrected_findings
from analysis_service.system_model import SystemModel
from tests.factories import sample_analysis, sample_threat, valid_model
from tests.test_questions import PRINCIPAL, resume

LOGIN = "flow:entity:customer>process:web-app>login"
MECHANISM = Assertion(
    subject=LOGIN,
    predicate="authentication-mechanism",
    value="a hardware token",
    basis="inferred",
    origin="assertion-pass",
    explanation="the sign-in page asks for a hardware token",
)
CREDENTIAL = Assertion(
    subject=LOGIN,
    predicate="credential-presented",
    value="credential:client-cert",
    basis="inferred",
    origin="assertion-pass",
    explanation="the client presents a certificate",
)
SUBJECTS = [
    Subject(id=LOGIN, type="interaction", label="login"),
    Subject(id="credential:client-cert", type="credential", label="client cert"),
]
ATTRIBUTE_KEY = (LOGIN, "authentication", "", "", "", "")
ANSWER = FactAnswer(key=ATTRIBUTE_KEY, value="a hardware token")

REQUIRED = Assertion(
    subject=PRINCIPAL,
    predicate="mfa-requirement",
    value="required",
    basis="inferred",
    origin="assertion-pass",
    explanation="the policy names two factors",
)
NOT_REQUIRED = REQUIRED.model_copy(
    update={"value": ABSENT, "explanation": "the guide names a password only"}
)
PLACE_KEY = (
    "",
    "",
    assertion_id(REQUIRED.model_copy(update={"value": UNKNOWN})),
    "",
    "",
    "",
)


def compatible() -> AssertionCatalog:
    """Two rows that both state a control on the login flow."""
    return AssertionCatalog(subjects=SUBJECTS, entries=[MECHANISM, CREDENTIAL])


def disputed() -> AssertionCatalog:
    """Two rows that disagree about a principal's second factor."""
    return AssertionCatalog(
        subjects=[Subject(id=PRINCIPAL, type="principal", label="customer accounts")],
        entries=[REQUIRED, NOT_REQUIRED],
    )


def model_with(authentication: str) -> SystemModel:
    model = valid_model()
    for flow in model.data_flows:
        if flow.id == LOGIN:
            flow.authentication = authentication
    return model


def waiting_on(ref: UnknownRef) -> list[FrameworkAnalysis]:
    """One finding that waits on ``ref``."""
    claim = sample_threat(
        "S-01",
        verdict=Verdict(
            status="needs-info",
            reason="The sources disagree about this.",
            related_unknowns=[ref],
        ),
    )
    return [sample_analysis([claim])]


def asked(analyses, model, catalog, earlier=()):
    return [
        question.key
        for question in fact_questions(analyses, model, catalog, earlier)
    ]


class TestAContradictedRow:
    """Two compatible rows beside an extracted ``none``: the graph contradicts
    them, so a finding on one waits on the login's authentication."""

    REF = UnknownRef(assertion=assertion_id(MECHANISM))

    def test_it_is_asked_as_the_graph_attribute(self):
        keys = asked(waiting_on(self.REF), model_with("none"), compatible())

        assert keys == [ATTRIBUTE_KEY]

    def test_the_card_names_the_same_fact(self):
        cards = conditions(
            waiting_on(self.REF), model_with("none"), (), (), (), compatible()
        )

        assert [key for key, _, _ in cards["stride/S-01"]] == [ATTRIBUTE_KEY]

    def test_the_answer_is_admitted(self):
        check_fact_answers([ANSWER], model_with("none"), compatible())

    def test_an_attribute_no_row_holds_open_takes_no_answer(self):
        """The neighbour: the graph states the control the rows state."""
        try:
            check_fact_answers([ANSWER], model_with("session cookie"), compatible())
        except ValueError as error:
            assert "is stated, so it takes no answer" in str(error)
        else:
            raise AssertionError("an answer to a stated attribute was admitted")

    def test_the_sources_do_not_state_the_fact(self):
        """A contradicted row is no statement of the sources, so a Control or
        a question-kind answer may write it (ADR 0125)."""
        assert not source_states(
            model_with("none"), compatible(), LOGIN, "authentication-mechanism"
        )

    def test_a_correction_reaches_the_finding(self):
        corrected = ANSWER.model_copy(update={"value": "a client certificate"})

        reached = corrected_findings(
            waiting_on(self.REF),
            [ANSWER],
            [corrected],
            model_with("none"),
            compatible(),
        )

        assert reached == ("stride/S-01",)


class TestTheAnswerSurvivesAResumedRun:
    """The attribute key names no row, so a second run finds the answer."""

    def runs(self):
        first = resume(
            Checkpoint(
                system_model=model_with("none"),
                assertions=AssertionRecord(proposed=2, catalog=compatible()),
            ),
            [ANSWER],
        )
        second = resume(
            Checkpoint(system_model=first.system_model, assertions=first.assertions),
            earlier=[ANSWER],
        )
        return first, second

    def test_the_model_holds_the_answer_after_each_run(self):
        for report in self.runs():
            (login,) = [f for f in report.system_model.data_flows if f.id == LOGIN]
            assert login.authentication == ANSWER.value

    def test_the_second_run_places_the_answer(self):
        _, second = self.runs()

        codes = [issue.code for issue in second.assertions.issues]
        assert "unmatched-answer" not in codes

    def test_the_second_run_does_not_ask_again(self):
        _, second = self.runs()
        ref = UnknownRef(assertion=assertion_id(MECHANISM))

        keys = asked(
            waiting_on(ref),
            second.system_model,
            second.assertions.catalog,
            earlier=[ANSWER],
        )
        assert ATTRIBUTE_KEY not in keys


class TestADisputedRowWithNoGraphField:
    """Two rows disagree about a principal's second factor, which no graph
    field holds: the question is asked under the key of the place."""

    REF = UnknownRef(assertion=assertion_id(REQUIRED))
    ANSWER = FactAnswer(key=PLACE_KEY, value="required")

    def test_it_is_asked_under_the_key_of_its_place(self):
        keys = asked(waiting_on(self.REF), valid_model(), disputed())

        assert keys == [PLACE_KEY]

    def test_the_answer_is_admitted(self):
        check_fact_answers([self.ANSWER], valid_model(), disputed())

    def test_the_answer_survives_a_resumed_run(self):
        first = resume(
            Checkpoint(
                system_model=valid_model(),
                assertions=AssertionRecord(proposed=2, catalog=disputed()),
            ),
            [self.ANSWER],
        )
        second = resume(
            Checkpoint(system_model=first.system_model, assertions=first.assertions),
            earlier=[self.ANSWER],
        )

        for report in (first, second):
            rows = [
                (row.value, row.origin)
                for row in report.assertions.catalog.entries
                if row.predicate == "mfa-requirement"
            ]
            assert rows == [("required", "answer")]
            codes = [issue.code for issue in report.assertions.issues]
            assert "unmatched-answer" not in codes
