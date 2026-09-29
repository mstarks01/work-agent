"""Capability questions at the pause, and the answers that write capabilities."""

from __future__ import annotations

import pytest

from analysis_service.answer_round import question_set
from analysis_service.capabilities import CAPABILITIES
from analysis_service.claims import UnknownRef
from analysis_service.early_questions import capability_questions
from analysis_service.frameworks.asvs.record import DraftRequirementRuling
from analysis_service.links import with_link_answers
from analysis_service.questions import (
    ANSWERS_LABEL,
    FactAnswer,
    answered_model,
    check_fact_answers,
    fact_line,
)
from analysis_service.sources import Source
from analysis_service.system_model import CapabilityStatement, SystemModel
from analysis_service.validation import validate
from tests.factories import DESCRIPTION_TEXT, valid_model

ASVS_L1 = {"asvs": {"level": 1}}


def _key(capability: str) -> tuple[str, str, str, str, str, str]:
    return UnknownRef(capability=capability).key


def _answer(capability: str, value: str) -> FactAnswer:
    return FactAnswer(key=_key(capability), value=value)


def _stating(model: SystemModel, capability: str, state: str) -> SystemModel:
    statement = CapabilityStatement.model_validate(
        {
            "capability": capability,
            "state": state,
            "source_excerpt": "x",
            "source_label": "description",
        }
    )
    return model.model_copy(update={"capabilities": [statement]})


def _asked(model: SystemModel, frameworks=ASVS_L1) -> list[str]:
    return [question.key[5] for question in capability_questions(model, frameworks)]


class TestWhatIsAsked:
    def test_a_framework_with_no_applicability_rule_asks_none(self):
        assert capability_questions(valid_model(), {"stride": {}}) == ()

    def test_an_asvs_job_asks_each_capability_its_level_needs(self):
        asked = _asked(valid_model())
        assert "oauth" in asked
        assert "browser-frontend" in asked
        # Level 1 reads no WebRTC requirement, so it asks nothing about it.
        assert "webrtc" not in asked
        assert "webrtc" in _asked(valid_model(), {"asvs": {"level": 2}})

    def test_a_stated_capability_is_not_asked(self):
        model = _stating(valid_model(), "browser-frontend", "present")
        assert "browser-frontend" not in _asked(model)

    def test_the_parts_of_a_stated_absence_are_not_asked(self):
        asked = _asked(_stating(valid_model(), "oauth", "absent"))
        assert not {"oauth", "oauth-client", "oauth-authorization-server"} & set(asked)

    def test_a_parent_comes_before_its_parts_and_they_name_it(self):
        questions = capability_questions(valid_model(), ASVS_L1)
        order = [question.key[5] for question in questions]
        parent_of = {question.key[5]: question.parent for question in questions}
        assert order.index("oauth") < order.index("oauth-authorization-server")
        assert parent_of["oauth-authorization-server"] == _key("oauth")
        assert parent_of["oauth"] is None

    def test_a_parent_counts_the_units_its_parts_settle(self):
        questions = {
            question.key[5]: question
            for question in capability_questions(valid_model(), ASVS_L1)
        }
        # V10.4.1-5 read the authorization server role alone, and "no OAuth"
        # settles all five.
        assert "up to 5 units" in questions["oauth"].reasons[0]

    def test_every_question_is_a_yes_or_no_choice_in_one_group(self):
        questions = capability_questions(valid_model(), ASVS_L1)
        assert {question.form for question in questions} == {"choice"}
        assert {question.choices for question in questions} == {("yes", "no")}
        assert {question.group for question in questions} == {"capabilities"}
        for question in questions:
            assert question.label == CAPABILITIES[question.key[5]].question

    def test_a_waiting_job_asks_them_and_admits_their_answers(self):
        asked = question_set(valid_model(), None, ASVS_L1, [], waiting=True)
        assert _key("oauth") in asked.asked
        admitted = asked.admit(
            sources=[Source.description("A system.")],
            earlier_links=[],
            earlier_facts=[],
            links=[],
            facts=[_answer("oauth", "no")],
        )
        assert [fact.key for fact in admitted.facts] == [_key("oauth")]


class TestTheAnswerCheck:
    def test_yes_no_and_i_dont_know_are_admitted(self):
        answers = [
            _answer("oauth", "no"),
            _answer("cookies", "yes"),
            _answer("webrtc", "unknown"),
        ]
        check_fact_answers(answers, valid_model(), None)

    @pytest.mark.parametrize("value", ["maybe", "present", "absent"])
    def test_any_other_value_is_refused(self, value):
        with pytest.raises(ValueError, match="is not one of yes, no"):
            check_fact_answers([_answer("oauth", value)], valid_model(), None)

    def test_a_capability_outside_the_table_is_refused(self):
        with pytest.raises(ValueError, match="no capability"):
            check_fact_answers([_answer("fax-machine", "no")], valid_model(), None)

    def test_a_capability_the_sources_state_takes_no_answer(self):
        model = _stating(valid_model(), "oauth", "absent")
        with pytest.raises(ValueError, match="the sources state"):
            check_fact_answers([_answer("oauth", "yes")], model, None)

    def test_an_earlier_round_may_answer_it_again(self):
        earlier = [_answer("oauth", "no")]
        model = answered_model(valid_model(), earlier)
        check_fact_answers([_answer("oauth", "yes")], model, None, earlier)

    def test_a_yes_under_a_no_in_one_round_is_refused(self):
        answers = [_answer("oauth", "no"), _answer("oauth-client", "yes")]
        with pytest.raises(ValueError, match="is part of 'oauth'"):
            check_fact_answers(answers, valid_model(), None)

    def test_a_no_over_an_earlier_yes_below_it_is_refused(self):
        """Two rounds left OAuth absent and its client present (#1289, F1)."""
        earlier = [_answer("oauth", "yes"), _answer("oauth-client", "yes")]
        asked = question_set(
            answered_model(valid_model(), earlier), None, ASVS_L1, [], waiting=True
        )
        with pytest.raises(ValueError, match="is part of 'oauth'"):
            asked.admit(
                sources=[Source.description("A system.")],
                earlier_links=[],
                earlier_facts=earlier,
                links=[],
                facts=[_answer("oauth", "no")],
            )

    def test_a_revision_that_answers_both_is_admitted(self):
        earlier = [_answer("oauth", "yes"), _answer("oauth-client", "yes")]
        model = answered_model(valid_model(), earlier)
        both = [_answer("oauth", "no"), _answer("oauth-client", "no")]
        check_fact_answers(both, model, None, earlier)

    def test_a_yes_under_a_stated_absence_is_refused(self):
        model = _stating(valid_model(), "oauth", "absent")
        with pytest.raises(ValueError, match="is part of 'oauth'"):
            check_fact_answers([_answer("oauth-client", "yes")], model, None)

    def test_a_contradiction_the_sources_hold_alone_refuses_nothing(self):
        """The sources' own conflict is for a person to settle, not a reason to
        refuse an unrelated answer."""
        model = _stating(valid_model(), "oauth", "absent")
        client = _stating(valid_model(), "oauth-client", "present").capabilities
        model = model.model_copy(
            update={"capabilities": [*model.capabilities, *client]}
        )
        check_fact_answers([_answer("cookies", "yes")], model, None)


class TestTheAnswerIsWritten:
    def test_an_answer_becomes_a_statement_that_quotes_its_line(self):
        answer = _answer("oauth", "no")
        model = answered_model(valid_model(), [answer])
        (statement,) = model.capabilities
        assert (statement.capability, statement.state) == ("oauth", "absent")
        assert statement.source_label == ANSWERS_LABEL
        assert statement.source_excerpt == fact_line(answer)

    def test_the_quote_is_found_in_the_answers_source(self):
        answer = _answer("oauth", "no")
        sources = with_link_answers(
            [Source.description(DESCRIPTION_TEXT)], [], [answer]
        )
        model = answered_model(valid_model(), [answer])
        texts = {source.label: source.text for source in sources}
        assert validate(model, sources=texts) == []

    def test_an_answer_replaces_what_the_sources_stated(self):
        model = _stating(valid_model(), "oauth", "present")
        answered = answered_model(model, [_answer("oauth", "no")])
        assert [s.state for s in answered.capabilities] == ["absent"]

    def test_i_dont_know_writes_nothing(self):
        assert (
            answered_model(valid_model(), [_answer("oauth", "unknown")]).capabilities
            == []
        )

    def test_a_no_rules_the_chapter_out_before_its_lane_runs(self):
        model = answered_model(valid_model(), [_answer("oauth", "no")])
        ruled = DraftRequirementRuling.ruled_out(model, {"level": 1}, "oauth-and-oidc")
        assert sorted(ruled) == [f"V10.4.{n}" for n in range(1, 6)]
        assert (
            'Asked "Does the application use OAuth 2.0 or OpenID Connect?",'
            ' the answer is "no".'
        ) in ruled["V10.4.1"]


def test_a_resumed_asvs_run_reports_the_answered_absence():
    """The real resumed graph: the answer reaches the lanes' scope and the report."""
    import asyncio

    from analysis_service import graph
    from analysis_service.jobs import Checkpoint, JobRecord, Resumption
    from analysis_service.pipeline import AdkPipelineRunner, PipelineCompleted
    from analysis_service.report import FrameworkSelection
    from tests.factories import scripted_pipeline

    answer = _answer("oauth", "no")
    record = JobRecord.create(
        owner_subject="idp|user-1",
        sources=with_link_answers([Source.description(DESCRIPTION_TEXT)], [], [answer]),
        frameworks=[FrameworkSelection(name="asvs", options={"level": 1})],
        facts=[answer],
        resumption=Resumption(
            parent_id="p",
            checkpoint=Checkpoint(system_model=valid_model(), assertions=None),
        ),
    )
    record.transition("running")
    pipeline, _ = scripted_pipeline({}, entry=graph.ENTRY_RESUME, frameworks=("asvs",))

    async def on_node(node: str) -> None:
        return None

    outcome = asyncio.run(AdkPipelineRunner(pipeline).run(record, on_node))

    assert isinstance(outcome, PipelineCompleted)
    (block,) = outcome.report.analyses
    ruled = {
        entry.unit: entry for entry in block.scope if entry.state == "not-applicable"
    }
    assert sorted(ruled) == [f"V10.4.{n}" for n in range(1, 6)]
    assert "the answer is" in ruled["V10.4.1"].reason
    decided = {entry.unit: entry for entry in block.applicability}
    assert len(decided) == 70
    assert decided["V10.4.1"].state == "not-applicable"
    assert block.block_issues(known_element_ids=()) == []


class TestTheReportStatesEachDecision:
    def test_every_selected_requirement_has_one_entry(self):
        from analysis_service.frameworks.asvs.catalog import requirements_for

        for level in (1, 2, 3):
            entries = DraftRequirementRuling.applicability(
                valid_model(), {"level": level}
            )
            assert [entry.unit for entry in entries] == [
                requirement.id for requirement in requirements_for(level)
            ]

    def test_an_unknown_names_the_question_that_would_settle_it(self):
        entries = {
            entry.unit: entry
            for entry in DraftRequirementRuling.applicability(
                valid_model(), {"level": 1}
            )
        }
        entry = entries["V10.4.1"]
        assert entry.state == "unknown"
        assert entry.missing == ["oauth-authorization-server"]
        assert CAPABILITIES["oauth-authorization-server"].question in entry.reason
        assert entries["V1.2.1"].unconditional

    def test_a_negative_carries_the_quote_that_decided_it(self):
        model = answered_model(valid_model(), [_answer("oauth", "no")])
        entries = {
            entry.unit: entry
            for entry in DraftRequirementRuling.applicability(model, {"level": 1})
        }
        (fact,) = entries["V10.4.1"].deciding
        assert (fact.capability, fact.state, fact.derived_from) == (
            "oauth-authorization-server",
            "absent",
            "oauth",
        )
        assert fact.quotes[0].startswith(f"{ANSWERS_LABEL}: Asked")

    def test_ruled_out_is_the_chapter_s_negatives(self):
        model = answered_model(valid_model(), [_answer("oauth", "no")])
        entries = DraftRequirementRuling.applicability(model, {"level": 2})
        ruled = DraftRequirementRuling.ruled_out(model, {"level": 2}, "oauth-and-oidc")
        assert ruled == {
            entry.unit: entry.reason
            for entry in entries
            if entry.state == "not-applicable" and entry.unit.startswith("V10.")
        }

    def test_a_framework_with_no_rule_writes_no_entry(self):
        from analysis_service.frameworks import PACKAGES

        assert PACKAGES["stride"].record.applicability(valid_model(), {}) == []


class TestTheEntryShape:
    @pytest.mark.parametrize(
        "fields",
        [
            {"state": "applicable"},
            {"state": "not-applicable", "unconditional": True},
            {"state": "unknown"},
            {"state": "unknown", "missing": ["oauth"], "unconditional": True},
            {
                "state": "not-applicable",
                "missing": ["oauth"],
                "deciding": [{"capability": "oauth", "state": "absent"}],
            },
        ],
    )
    def test_an_entry_that_says_less_than_its_state_needs_is_refused(self, fields):
        from pydantic import ValidationError

        from analysis_service.claims import ApplicabilityEntry

        with pytest.raises(ValidationError):
            ApplicabilityEntry(unit="V1.1.1", reason="why", **fields)

    def test_the_block_holds_one_answer_per_unit(self):
        from tests.test_asvs import _block

        block = _block(1)
        entries = DraftRequirementRuling.applicability(
            _stating(valid_model(), "oauth", "absent"), {"level": 1}
        )
        with_entries = block.model_copy(update={"applicability": entries})
        issues = with_entries.block_issues(known_element_ids=())
        # The scope rules nothing out, so each negative disagrees with it.
        assert issues == [
            f"unit 'V10.4.{n}' is not applicable in one of scope and"
            " applicability and not in the other"
            for n in range(1, 6)
        ]
        doubled = block.model_copy(update={"applicability": entries[:1] * 2})
        assert "more than one applicability entry" in doubled.block_issues(())[0]
