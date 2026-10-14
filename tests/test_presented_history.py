"""A skip is the submitter's own act, of a question the page presented (#1542 F4).

At 2fae7d3 the page kept a part hidden under its parent in the round's rows,
so "Skip the rest" sent its key as a skip, and the service admitted it. With
the parent answered "I don't know" and later changed to "yes", the part stayed
in the skipped list and out of the round. These tests drive the shipped page
script and production admission over the same payload.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from analysis_service.answer_round import question_set
from analysis_service.answer_sets import NO_ANSWERS, AnswerSet
from analysis_service.early_questions import early_questions
from analysis_service.fact_answers import FactAnswer
from tests.factories import valid_model
from tests.test_pause_summary import _save
from tests.test_webapp_questions import _run_form_script, _text_row

LEVEL_2 = {"asvs": {"level": 2}}


def _paused(answers=(), skipped=(), shown=()):
    return question_set(
        valid_model(),
        None,
        LEVEL_2,
        (),
        waiting=True,
        answered=AnswerSet(facts=tuple(answers)),
        final=False,
        shown=list(shown),
        skipped=list(skipped),
    )


def _chain(*names):
    """The production questions for these capabilities, in list order."""
    listed = early_questions(valid_model(), LEVEL_2, None)
    by_name = {question.key[-1]: question for question in listed}
    return tuple(by_name[name] for name in names)


def _round(*names):
    """A list that holds exactly these capabilities, parents first, as the
    page held a part beside its parent before ADR 0078 listed it apart."""
    return replace(_paused(), early=_chain(*names))


def _sent_by_the_page(parent_value):
    """What the shipped script sends for "Skip the questions left blank" with
    the parent at ``parent_value`` and its part listed or waiting."""
    parent_key = ["", "", "", "", "", "oauth"]
    child_key = ["", "", "", "", "", "oauth-client"]
    shape = {
        "kind": "capability",
        "form": "choice",
        "choices": [{"id": "yes", "name": ""}, {"id": "no", "name": ""}],
    }
    # The service lists a part only once its parent is "yes"; until then it
    # waits in ``early_blocked``, and the page draws what the service lists.
    parent = _text_row(parent_key, "OAuth?") | shape
    child = _text_row(child_key, "A client?") | shape | {"parent": parent_key}
    listed = [parent, child] if parent_value == "yes" else [parent]
    blocked = [] if parent_value == "yes" else [child | {"reason": "parent-undecided"}]
    steps = f"""
await ids.analyze.listeners.submit({{ preventDefault() {{}} }}); await settle();
streams[0].listeners.questions({{ data: JSON.stringify({{ run: "r1",
  link_questions: [], early_questions: {json.dumps(listed)}, early_blocked: {json.dumps(blocked)},
  answered_early: [], answered_links: [], revision: 0 }}) }});
const parent = ids.questions.querySelectorAll("select")[0];
parent.value = {json.dumps(parent_value)}; parent.listeners.change();
globalThis.fetch = async (url, init) => {{
  calls.push({{url, body: JSON.parse(init.body)}});
  return {{ok: false, json: async () => ({{message: "stop after capture"}})}};
}};
await ids.skip.listeners.click(); await settle();
"""
    return _run_form_script(steps)["calls"][-1]["body"], child_key


class TestThePageSkipsOnlyWhatItShows:
    @pytest.mark.parametrize("parent", ["", "unknown", "no"])
    def test_a_hidden_part_is_not_sent_as_a_skip(self, parent):
        sent, child = _sent_by_the_page(parent)
        assert child not in sent["skip"]

    def test_a_shown_part_left_blank_is_skipped(self):
        sent, child = _sent_by_the_page("yes")
        assert child in sent["skip"]

    @pytest.mark.parametrize("parent", ["", "unknown", "no", "yes"])
    def test_production_admits_what_the_page_sends(self, parent):
        """The page's payload and the service's rule agree, so no answer the
        page sends is refused for a skip it should not have made."""
        sent, _ = _sent_by_the_page(parent)
        facts = [FactAnswer.model_validate(fact) for fact in sent["facts"]]
        skips = [tuple(key) for key in sent["skip"]]
        presented = [tuple(key) for key in sent["presented"]]
        if not (facts or skips):
            return
        _save(
            _round("oauth", "oauth-client"),
            facts=facts,
            skips=skips,
            presented=presented,
        )

    @pytest.mark.parametrize("parent", ["", "unknown", "no", "yes"])
    def test_the_page_reports_only_the_rows_it_shows(self, parent):
        sent, child = _sent_by_the_page(parent)
        assert (child in sent["presented"]) == (parent == "yes")


class TestTheServiceDecidesWhatWasPresented:
    def test_a_skip_of_a_question_the_page_did_not_show_is_refused(self):
        parent_q, child = _chain("oauth", "oauth-client")
        yes = [FactAnswer(key=parent_q.key, value="yes")]
        with pytest.raises(ValueError, match="only a question the page presented"):
            _save(
                _round("oauth", "oauth-client"),
                facts=yes,
                skips=[child.key],
                presented=[parent_q.key],
            )

    def test_a_part_the_owner_never_saw_is_asked_once_its_parent_is_yes(self):
        """The issue's F4b: the parent first "I don't know", then "yes". The
        part comes to the round with no recovery from the skipped list."""
        parent, child = _chain("oauth", "oauth-client")
        first = _save(
            _round("oauth", "oauth-client"),
            facts=[FactAnswer(key=parent.key, value="unknown")],
        )
        middle = _paused(first.answers.facts, first.skipped, first.shown)
        changed = _save(
            middle, first.answers.facts, facts=[FactAnswer(key=parent.key, value="yes")]
        )
        held, skipped, shown = changed.answers.facts, changed.skipped, changed.shown
        for _ in range(10):
            after = _paused(held, skipped, shown)
            assert child.key not in {q.key for q in after.skipped}
            if child.key in {q.key for q in after.early}:
                return
            assert not after.done, "the list ended without asking the part"
            # A later list: skip what it shows, which spends no limit.
            saved = _save(after, held, skips=[q.key for q in after.early])
            held, skipped, shown = saved.answers.facts, saved.skipped, saved.shown
        pytest.fail("the part never came to the list")


def test_a_resumed_job_carries_the_skips_its_report_reads():
    """The report tells a skip from a blank only where the resumed job keeps
    the pause's skips beside what it presented."""
    from analysis_service.answer_round import Answers, AnswerState, ResumedJob
    from analysis_service.jobs import Checkpoint

    skip = _paused().early[0].key
    state = AnswerState(
        checkpoint=Checkpoint(system_model=valid_model(), assertions=None),
        frameworks=LEVEL_2,
        analyses=(),
        waiting=True,
        final=False,
        sources=(),
        answers=NO_ANSWERS,
        shown=(),
        skipped=(skip,),
        corrections=(),
        revision=1,
        resumed_by=None,
    )
    resumed = state.answer(Answers(revision=1), limits=None)
    assert isinstance(resumed, ResumedJob)
    assert resumed.skipped == (skip,)
