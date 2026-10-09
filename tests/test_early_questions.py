"""The open facts a paused job asks before its analysis (``QA-2026-09-26-03-E13``).

Held against the shared model for what is asked and in which order, against
the answer rules for what an answer to each may be, against the prior table
for a row per framework, and against the questions route for a paused job.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from analysis_service.answer_forms import YES_NO, answer_choices
from analysis_service.answer_round import EARLY_RULES, passes_floor
from analysis_service.answer_sets import NO_ANSWERS, AnswerSet
from analysis_service.candidates import generate_candidates
from analysis_service.claims import UnknownRef
from analysis_service.early_questions import (
    PRIOR_REASON,
    QUESTION_PRIOR,
    PriorRow,
    early_questions,
    element_type,
)
from analysis_service.fact_answers import FactAnswer
from analysis_service.fact_writes import check_fact_answers
from analysis_service.frameworks import PACKAGES
from analysis_service.open_facts import group_of
from analysis_service.question_kinds import QUESTION_KINDS
from analysis_service.system_model import (
    UNKNOWN,
    ZONE_ATTRIBUTE,
    Assumption,
    SystemModel,
    all_attribute_names,
    attribute_names,
)
from tests.factories import valid_model
from tests.test_api import auth
from tests.test_pause import catalog_client, waiting

STORE = "store:orders-db"
PROCESS = "process:web-app"


def prior_of(rates) -> dict[str, PriorRow]:
    """A STRIDE prior with these rates, and an empty row for every other package."""
    empty = PriorRow(runs=(), revision=None, cases=0, rates={})
    return {
        name: PriorRow(runs=("test",), revision="t", cases=1, rates=rates)
        if name == "stride"
        else empty
        for name in PACKAGES
    }


def top_band(question) -> int:
    """The band a capability is ordered by: its highest over its frameworks."""
    return max(band for _, band in question.framework_bands)


def keys(rates, model=None):
    asked = early_questions(
        model or valid_model(), {"stride": {}}, None, prior_of(rates)
    )
    return [question.key for question in asked]


class TestThePriorTable:
    def test_it_has_a_row_for_every_package(self):
        assert set(QUESTION_PRIOR) == set(PACKAGES)

    def test_every_rate_names_an_element_type_and_an_attribute_or_a_kind(self):
        types = {element_type(element) for element in valid_model().elements()}
        attributes = {
            name
            for element in valid_model().elements()
            for name in attribute_names(element)
        }
        for row in QUESTION_PRIOR.values():
            for kind, fields in row.rates.items():
                assert kind in types
                assert set(fields) <= attributes | set(QUESTION_KINDS)

    def test_a_row_that_counted_a_run_says_which(self):
        for row in QUESTION_PRIOR.values():
            assert bool(row.runs) == bool(row.rates) == bool(row.cases)


class TestWhatIsAsked:
    def test_an_unknown_attribute_is_asked_and_a_stated_one_is_not(self):
        asked = keys({"DataStore": {"encryption_at_rest": 1.0, "technology": 1.0}})
        assert asked == [(STORE, "encryption_at_rest", "", "", "", "")]

    def test_every_kind_the_prior_names_is_asked(self):
        asked = keys({"DataStore": {"audit-evidence": 1.0}})
        assert asked == [(STORE, "", "", "", "audit-evidence", "")]

    def test_a_zone_is_asked_only_where_the_service_inferred_it(self):
        rates = {"DataStore": {ZONE_ATTRIBUTE: 1.0}}
        assert keys(rates) == []
        model = valid_model()
        model.assumptions.append(
            Assumption(
                assumption="the store sits in the internal network",
                element_id=STORE,
                attribute=ZONE_ATTRIBUTE,
                basis="the sources are silent",
            )
        )
        assert keys(rates, model) == [(STORE, ZONE_ATTRIBUTE, "", "", "", "")]

    def test_a_qualified_unknown_is_asked(self):
        """The reading the evidence catalog uses, not an exact match (#1289, Q4)."""
        model = valid_model()
        model.get(STORE).encryption_at_rest = "unknown; the sources are silent"
        asked = keys({"DataStore": {"encryption_at_rest": 1.0}}, model)
        assert asked == [(STORE, "encryption_at_rest", "", "", "", "")]

    def test_an_unknown_zone_is_asked_without_an_assumption(self):
        model = valid_model()
        model.get(STORE).trust_zone = "unknown"
        assert keys({"DataStore": {ZONE_ATTRIBUTE: 1.0}}, model) == [
            (STORE, ZONE_ATTRIBUTE, "", "", "", "")
        ]

    def test_a_framework_whose_row_counted_no_run_asks_only_capabilities(self):
        from dataclasses import replace

        empty = replace(QUESTION_PRIOR["asvs"], runs=(), cases=0, rates={})
        prior = {**QUESTION_PRIOR, "asvs": empty}
        asked = early_questions(valid_model(), {"asvs": {"level": 1}}, None, prior)
        assert asked
        assert {question.kind for question in asked} == {"capability"}

    def test_the_asvs_row_asks_attributes_and_no_question_kind(self):
        """Counted from an ASVS sweep older than the question kinds (#1284)."""
        model = valid_model()
        for flow in model.data_flows:
            flow.authentication = "unknown"
        asked = early_questions(model, {"asvs": {"level": 1}}, None)
        assert QUESTION_PRIOR["asvs"].runs
        assert "attribute" in {question.kind for question in asked}
        assert "question" not in {question.kind for question in asked}


class TestTheOrder:
    def test_the_rules_that_fire_on_an_element_raise_its_questions(self):
        """Equal rates, so the element more candidates name is asked first."""
        stride = PACKAGES["stride"]
        found = generate_candidates(valid_model(), stride.lanes, stride.rules)
        named = Counter(
            element_id
            for group in found.values()
            for candidate in group.candidates
            for element_id in set(candidate.element_ids)
        )
        flows = [flow.id for flow in valid_model().data_flows]
        assert len({named[flow] for flow in flows}) == len(flows)
        asked = keys({"DataFlow": {"audit-evidence": 1.0}})
        expected = sorted(flows, key=lambda element: -named[element])
        assert [key[0] for key in asked] == expected

    def test_a_question_says_which_rules_read_its_fact(self):
        (question,) = early_questions(
            valid_model(),
            {"stride": {}},
            None,
            prior_of({"DataStore": {"encryption_at_rest": 1.0}}),
        )
        rules = {
            rule.question
            for rule in PACKAGES["stride"].rules
            if rule.rule_id == "information-disclosure-store-at-rest-unverified"
        }
        assert set(question.reasons) == rules

    def test_a_fact_no_rule_reads_gives_the_prior_as_its_reason(self):
        """A rule that only names the element says nothing about this fact."""
        (question,) = early_questions(
            valid_model(),
            {"stride": {}},
            None,
            prior_of({"DataStore": {"audit-evidence": 1.0}}),
        )
        assert question.reasons == (PRIOR_REASON,)


def test_the_framework_order_changes_no_question():
    """The reasons do not follow the framework selection order (#1289)."""
    model = valid_model()
    forward = early_questions(model, {"stride": {}, "asvs": {"level": 2}}, None)
    backward = early_questions(model, {"asvs": {"level": 2}, "stride": {}}, None)
    assert forward == backward
    at_rest = next(q for q in forward if q.key[1] == "encryption_at_rest")
    stride = {rule.question for rule in PACKAGES["stride"].rules}
    assert set(at_rest.reasons) <= stride
    assert at_rest.frameworks == ("stride",)


CORPUS_MODELS = sorted(Path("evals/corpus").glob("*/model.json"))


@pytest.mark.parametrize("level", [1, 2, 3])
def test_an_idle_lane_leads_no_early_question(level):
    """At level 1 the logging and WebRTC lanes hold no requirement, so their
    agents are never called. The early list still scored their candidates and
    gave a logging rule's question as the reason on 14 of 15 corpus models
    (#1289, B3)."""
    package = PACKAGES["asvs"]
    options = {"level": level}
    led = 0
    for path in CORPUS_MODELS:
        model = SystemModel.model_validate_json(path.read_text())
        idle = {
            lane for lane in package.lanes if package.record.idle(model, options, lane)
        }
        found = generate_candidates(model, package.lanes, package.rules, None)
        led += sum(len(found[lane].candidates) for lane in idle)
        idle_asks = {rule.question for rule in package.rules if rule.lane in idle}
        asked = early_questions(model, {"asvs": options}, None)
        assert not [q.key for q in asked if set(q.reasons) & idle_asks], path
    # The reproduction needs an idle lane whose rules fire; above level 1 the
    # corpus has none.
    assert led if level == 1 else led == 0


def test_a_question_names_every_framework_it_serves():
    model = valid_model()
    questions = early_questions(model, {"stride": {}, "asvs": {"level": 2}}, None)
    shared = [q for q in questions if q.frameworks == ("asvs", "stride")]
    assert shared
    assert all(q.frameworks for q in questions)


def test_every_early_question_takes_an_answer_the_answer_rules_accept():
    """The early list and the answers route are two readers of one model."""
    model = valid_model()
    for question in early_questions(model, {"stride": {}}, None):
        if question.facets:
            answer = FactAnswer(key=question.key, facets={question.facets[0].id: "yes"})
        else:
            value = question.choices[0] if question.choices else "stated by them"
            answer = FactAnswer(key=question.key, value=value)
        check_fact_answers([answer], model, None)


def test_the_shipped_prior_puts_an_unknown_attribute_near_the_top():
    asked = [
        question.key
        for question in early_questions(valid_model(), {"stride": {}}, None)
    ]
    assert asked[0][1] or asked[0][4]
    assert (STORE, "encryption_at_rest", "", "", "", "") in asked[:3]
    assert all(
        getattr(valid_model().get(key[0]), key[1]) == UNKNOWN
        for key in asked
        if key[1] and key[1] != ZONE_ATTRIBUTE
    )


class TestTheRoute:
    def test_a_waiting_job_ranks_its_open_facts(self):
        client, store = catalog_client()
        job = waiting(store)
        body = client.get(f"/v1/jobs/{job}/questions", headers=auth()).json()
        listed = early_questions(
            valid_model(), {"stride": {}}, store_catalog(store, job)
        )
        eligible = [
            question
            for question in listed
            if passes_floor(question, listed) and question.kind != "capability"
        ]
        expected = eligible[: EARLY_RULES["field"].per_round]
        assert body["early_questions"] == [q.to_json() for q in expected]
        assert body["early_questions"]

    def test_a_finished_job_asks_nothing_early(self):
        from tests.test_questions import TestTheRoutes

        client, store = catalog_client()
        job = TestTheRoutes().completed(store)
        body = client.get(f"/v1/jobs/{job}/questions", headers=auth()).json()
        assert body["early_questions"] == []


def store_catalog(store, job):
    import asyncio

    record = asyncio.run(store.get(job))
    return record.checkpoint.assertions.catalog


class TestTheGroups:
    """A page asks each kind or attribute once, with a row per element (#1289)."""

    def test_every_kind_heading_names_no_element(self):
        for name in QUESTION_KINDS:
            _, heading = group_of(UnknownRef(element_id=PROCESS, question=name))
            assert "{" not in heading
            assert heading.endswith("?")

    def test_an_attribute_groups_by_its_name(self):
        ref = UnknownRef(element_id=STORE, attribute="encryption_at_rest")
        assert group_of(ref) == ("encryption_at_rest", "Encryption at rest")

    def test_each_question_carries_its_group_and_its_element(self):
        (question,) = early_questions(
            valid_model(),
            {"stride": {}},
            None,
            prior_of({"DataStore": {"audit-evidence": 1.0}}),
        )
        assert question.group == "audit-evidence"
        assert question.element == valid_model().get(STORE).name

    def test_a_case_asks_few_groups_however_many_elements_it_holds(self):
        """About 18 groups on every corpus case, where the list held 60-126."""
        groups = {q.group for q in early_questions(valid_model(), {"stride": {}}, None)}
        assert len(groups) <= len(QUESTION_KINDS) + len(all_attribute_names())


class TestTheYesNoKinds:
    def test_a_yes_no_kind_opens_with_a_verb_that_asks_whether(self):
        """The field follows the reviewed wording, so the two cannot drift.

        A kind that asks whether several things hold is answered in facets.
        """
        for name, kind in QUESTION_KINDS.items():
            asks_whether = kind.template.split()[0] in {"Are", "Can", "Does"}
            if kind.answer == "yes-no":
                assert asks_whether, name
            if asks_whether:
                assert kind.answer in {"yes-no", "facets"}, name

    def test_a_yes_no_kind_takes_yes_no_or_unknown(self):
        key = UnknownRef(element_id=STORE, question="stored-copy-integrity").key
        assert answer_choices(key, valid_model(), None) == YES_NO
        for value in (*YES_NO, "unknown"):
            check_fact_answers([FactAnswer(key=key, value=value)], valid_model(), None)
        with pytest.raises(ValueError, match="not one of"):
            check_fact_answers(
                [FactAnswer(key=key, value="sometimes")], valid_model(), None
            )

    def test_a_faceted_kind_has_no_choices_of_its_own(self):
        key = UnknownRef(element_id=STORE, question="audit-evidence").key
        assert answer_choices(key, valid_model(), None) == ()


class TestCapabilityBands:
    """An ASVS job above level 1 dropped capability questions that settle a
    level 1 requirement, and kept ones that settle only level 2 (#1289)."""

    def question(self, score, band, kind="capability", framework="asvs"):
        from types import SimpleNamespace

        bands = ((framework, band),) if kind == "capability" else ()
        return SimpleNamespace(kind=kind, score=score, framework_bands=bands)

    def test_another_framework_s_bands_leave_this_one_s_floor_alone(self):
        """One framework's bands never decide another's floor (ADR 0071)."""
        one = self.question(1.0, 0, framework="asvs")
        listed = [
            one,
            self.question(5.0, 0, framework="asvs"),
            self.question(5.0, 0, framework="other"),
            self.question(5.0, -2, framework="other"),
        ]
        assert not passes_floor(one, listed)

    def test_the_top_band_passes_the_floor_where_bands_differ(self):
        top, low = self.question(1.0, 3), self.question(1.0, 2)
        listed = [top, low, self.question(5.0, 2)]
        assert passes_floor(top, listed)
        assert not passes_floor(low, listed)

    def test_one_band_leaves_the_floor_alone(self):
        one = self.question(1.0, 3)
        assert not passes_floor(one, [one, self.question(5.0, 3)])

    def test_a_field_question_is_held_to_its_floor(self):
        field = self.question(0.5, 0, kind="attribute")
        assert not passes_floor(field, [field, self.question(2.0, 0, kind="attribute")])

    def test_a_level_2_job_asks_level_1_capabilities_first(self):
        asked = early_questions(valid_model(), {"asvs": {"level": 2}}, None)
        capabilities = [q for q in asked if q.kind == "capability"]
        roots = [q for q in capabilities if q.parent is None]
        assert {top_band(q) for q in capabilities} == {-1, 0}, "a control: two bands"
        assert [top_band(q) for q in roots] == sorted(
            map(top_band, roots), reverse=True
        )

    def test_a_level_1_job_has_one_band(self):
        asked = early_questions(valid_model(), {"asvs": {"level": 1}}, None)
        assert {top_band(q) for q in asked if q.kind == "capability"} == {0}

    @pytest.mark.parametrize("level", [1, 2, 3])
    def test_every_parent_is_asked_before_its_children(self, level):
        asked = early_questions(valid_model(), {"asvs": {"level": level}}, None)
        order = [q.key for q in asked if q.kind == "capability"]
        for question in asked:
            if question.parent is not None:
                assert order.index(question.parent) < order.index(question.key)
        assert any(q.parent for q in asked), "a control: some question has a parent"


def test_a_field_question_ranks_by_its_score_per_choice():
    """Ranked by score alone, a STRIDE-only pause opened with facet tables, and
    ten choices completed 517 findings on the archive; per choice, 1,488
    (QA-2026-09-26-03-E33)."""
    fields = [
        q
        for q in early_questions(valid_model(), {"stride": {}}, None)
        if q.kind != "capability"
    ]
    per_choice = [q.score / q.decisions for q in fields]
    assert per_choice == sorted(per_choice, reverse=True)
    assert len({q.decisions for q in fields}) > 1, "the fixture mixes costs"


def _with_interfaces(kind, *, protocols=None):
    """The shared model with every process presenting ``kind``, and every
    flow carrying ``protocols`` where it is given."""
    model = valid_model()
    for process in model.processes:
        process.interface_kind = kind
    if protocols is not None:
        for flow in model.data_flows:
            flow.protocol = protocols
    return model


def _paused(model, selection, answers=()):
    from analysis_service.answer_round import question_set

    return question_set(
        model,
        None,
        selection,
        (),
        waiting=True,
        answered=list(answers),
        answered_links=(),
        final=False,
        shown=(),
    )


LEVEL_2 = {"asvs": {"level": 2}}


class TestTheFrameworkGates:
    """A paused job reads each framework's precondition before it asks for it
    (#1542 F1, ADR 0067). At 2fae7d3 a model whose processes are all
    ``non-web`` refutes ASVS, and a level 2 pause still asked 15 questions; a
    model whose interfaces and protocols are unknown is undecidable, and no
    question asked its ``interface_kind``."""

    def test_a_refuted_framework_asks_nothing(self):
        asked = _paused(_with_interfaces("non-web"), LEVEL_2)
        assert dict(asked.gates) == {"asvs": "refuted"}
        assert asked.early == () and asked.withheld == 0
        assert asked.to_json()["framework_gates"] == {"asvs": "refuted"}

    def test_a_refuted_framework_leaves_the_other_frameworks_questions_as_they_are(
        self,
    ):
        model = _with_interfaces("non-web")
        both = _paused(model, {**LEVEL_2, "stride": {}})
        alone = _paused(model, {"stride": {}})
        assert [q.key for q in both.early] == [q.key for q in alone.early]
        assert all(q.frameworks == ("stride",) for q in both.early)
        assert dict(both.gates) == {"asvs": "refuted", "stride": "satisfied"}

    def test_an_undecidable_framework_asks_only_what_decides_it(self):
        model = _with_interfaces("unknown", protocols="unknown")
        asked = _paused(model, LEVEL_2)
        assert dict(asked.gates) == {"asvs": "undecidable"}
        assert [(q.key, q.gates) for q in asked.early] == [
            (
                UnknownRef(element_id=process.id, attribute="interface_kind").key,
                ("asvs",),
            )
            for process in model.processes
        ]
        assert asked.remaining == {"gate": 1, "capability": 0, "field": 0}
        [gate] = asked.early
        assert gate.choices == ("web", "non-web")
        assert gate.reasons == ("Whether the asvs analysis runs depends on this fact.",)

    def test_the_questions_that_decide_a_framework_come_before_the_others(self):
        model = _with_interfaces("unknown", protocols="unknown")
        asked = _paused(model, {**LEVEL_2, "stride": {}})
        gated = [bool(q.gates) for q in asked.early]
        assert gated[0] and not any(gated[1:]), "a control: stride asks after it"

    @pytest.mark.parametrize(
        ("answer", "state"), [("web", "satisfied"), ("non-web", "refuted")]
    )
    def test_the_offered_question_decides_the_gate_prepare_reads(self, answer, state):
        """The answer is written where the precondition reads, as the resumed
        job's ``prepare`` reads it: through the one writer of answers into a
        run, and the gate it runs."""
        from analysis_service.frameworks import PACKAGES, run_precondition
        from analysis_service.graph import STATE_VALID_MODEL
        from analysis_service.pipeline import answered_state

        model = _with_interfaces("unknown", protocols="unknown")
        asked = _paused(model, LEVEL_2)
        facts = [FactAnswer(key=q.key, value=answer) for q in asked.early]
        admitted = asked.admit(
            sources=(),
            earlier=NO_ANSWERS,
            given=AnswerSet(facts=tuple(facts)),
            save=True,
        )
        after = _paused(model, LEVEL_2, admitted.answers.facts)
        seeded = SystemModel.model_validate(
            answered_state(model, [], admitted.answers.facts)[STATE_VALID_MODEL]
        )
        assert run_precondition(PACKAGES["asvs"], seeded) == state
        assert dict(after.gates) == {"asvs": state}
        capabilities = [q for q in after.early if q.kind == "capability"]
        assert bool(capabilities) == (state == "satisfied")
        assert [q.key for q, _ in after.answered_early] == [q.key for q in asked.early]

    def test_an_unknown_answer_leaves_the_framework_undecidable_and_asks_no_more(
        self,
    ):
        model = _with_interfaces("unknown", protocols="unknown")
        asked = _paused(model, LEVEL_2)
        unknown = [FactAnswer(key=q.key, value="unknown") for q in asked.early]
        after = _paused(model, LEVEL_2, unknown)
        assert dict(after.gates) == {"asvs": "undecidable"}
        assert after.early == () and after.done

    def test_a_gate_answer_counts_toward_no_kinds_limit(self):
        """Once an answer satisfies the gate, the rounds hold the questions a
        model that stated the interface would hold. Twenty-nine earlier field
        answers leave one place, which a counted gate answer would take."""
        from analysis_service.answer_round import EARLY_RULES

        selection = {**LEVEL_2, "stride": {}}
        model = _with_interfaces("unknown", protocols="unknown")
        filler = [
            FactAnswer(key=("", "", "", f"subject {n}", "", ""), value="unknown")
            for n in range(EARLY_RULES["field"].limit - 1)
        ]
        gates = [
            FactAnswer(key=q.key, value="web")
            for q in _paused(model, selection).early
            if q.gates
        ]
        after = _paused(model, selection, [*filler, *gates])
        stated = _paused(
            _with_interfaces("web", protocols="unknown"), selection, filler
        )
        assert after.remaining == stated.remaining
        assert after.remaining["field"] == 1, "a control: one place is left"
        assert [q.key for q in after.early] == [q.key for q in stated.early]

    def test_a_framework_whose_precondition_is_total_asks_no_gate_question(self):
        for case in sorted(Path("evals/corpus").iterdir()):
            model = SystemModel.model_validate_json((case / "model.json").read_text())
            asked = early_questions(model, {"stride": {}}, None)
            assert not any(q.gates for q in asked), case.name

    def test_a_model_with_no_process_asks_each_flow_that_states_no_protocol(self):
        from analysis_service.frameworks.asvs.rules import asvs_precondition_facts

        model = valid_model()
        flows = model.model_copy(update={"processes": []})
        for flow in flows.data_flows:
            flow.protocol = "unknown"
        assert asvs_precondition_facts(flows) == tuple(
            UnknownRef(element_id=flow.id, attribute="protocol")
            for flow in flows.data_flows
        )
