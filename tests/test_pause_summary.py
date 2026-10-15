"""What a paused job says it leaves open (#1542 F2 and F5, ADR 0068).

At 2fae7d3, a level 1 pause whose owner answered every capability "yes" ended
with ``nothing-left``, no question withheld and 22 requirements still unknown;
the page said "No question is left". A STRIDE pause skipped save by save
showed 40 distinct field questions under a limit of 30. These tests drive the
same paths through the production question set.
"""

from __future__ import annotations

import pytest

from analysis_service.answer_sets import AnswerSet
from analysis_service.answer_state import question_set
from analysis_service.fact_answers import FactAnswer
from analysis_service.system_model import SystemModel
from tests.factories import PROJECT_ROOT, valid_model


def _paused(model, selection, answers=(), shown=(), skipped=()):
    return question_set(
        model,
        None,
        selection,
        (),
        waiting=True,
        answered=AnswerSet(facts=tuple(answers)),
        final=False,
        shown=list(shown),
        skipped=list(skipped),
    )


def _save(asked, earlier=(), facts=(), skips=(), presented=None):
    """One save. The page presents every listed row, unless the test says
    what it presented."""
    if presented is None:
        presented = [question.key for question in asked.early]
    return asked.admit(
        sources=(),
        earlier=AnswerSet(facts=tuple(earlier)),
        given=AnswerSet(facts=tuple(facts)),
        save=True,
        skips=list(skips),
        presented=list(presented),
    )


def _all_yes(level):
    """The issue's F2 run: every offered capability "yes", every other
    question "I don't know", through production admission, to the stop."""
    model = valid_model()
    selection = {"asvs": {"level": level}}
    held, shown = [], ()
    for _ in range(30):
        asked = _paused(model, selection, held, shown)
        if asked.done:
            return asked
        facts = [
            FactAnswer(key=q.key, value="yes" if q.kind == "capability" else "unknown")
            for q in asked.early
        ]
        admitted = _save(asked, held, facts)
        held, shown = admitted.answers.facts, admitted.shown
    pytest.fail("the saves never ended")


def _bands(asked):
    return {
        band.band: (dict(band.states), band.unaskable)
        for band in asked.summary.applicability["asvs"]
    }


class TestTheStopSaysWhatIsLeft:
    def test_a_level_1_pause_discloses_the_requirements_it_leaves_unknown(self):
        asked = _all_yes(1)
        assert asked.stop == "below-floor", "not nothing-left: questions remain"
        assert len(asked.below_floor) == 24 and asked.held_back == ()
        assert _bands(asked) == {"level 1": ({"applicable": 48, "unknown": 22}, 0)}
        summary = asked.summary.to_json()
        assert (summary["settled"], summary["below_floor"]) == (14, 24)

    def test_a_higher_level_still_shows_the_lower_levels_unknowns(self):
        """Selecting level 2 does not hide whether level 1 is settled: each
        band is counted on its own. Thirty "yes" answers leave 18 level 1
        requirements unknown: the effects break ties toward the parents with
        the most parts, whose "yes" lists parts that take the thirty places
        (ADR 0078). The summary discloses them."""
        asked = _all_yes(2)
        assert asked.stop == "budget-exhausted"
        bands = _bands(asked)
        assert list(bands) == ["level 1", "level 2"], "the highest band first"
        assert bands["level 1"][0]["unknown"] == 18

    def test_a_question_under_the_floor_takes_an_answer(self):
        asked = _all_yes(1)
        [low, *_] = asked.below_floor
        assert low.key in asked.asked
        earlier = [answer for _, answer in asked.answered_early]
        admitted = _save(asked, earlier, [FactAnswer(key=low.key, value="yes")])
        after = _paused(valid_model(), {"asvs": {"level": 1}}, admitted.answers.facts)
        assert low.key not in {q.key for q in after.below_floor}

    def test_a_unit_no_open_question_can_settle_is_counted(self):
        """An "I don't know" leaves a unit unknown that no question asks any
        more; until then, the unit is only unknown."""
        model = valid_model()
        selection = {"asvs": {"level": 1}}
        first = _paused(model, selection)
        before = _bands(first)["level 1"]
        assert before[1] == 0
        unknown = [
            FactAnswer(key=q.key, value="unknown")
            for q in (
                *first.early,
                *first.held_back,
                *first.below_floor,
                *(part for part, _ in first.blocked),
            )
            if q.kind == "capability"
        ]
        after = _paused(model, selection, unknown)
        states, unaskable = _bands(after)["level 1"]
        assert unaskable == states["unknown"] > 0

    def test_a_framework_with_no_applicability_rule_counts_no_unit(self):
        asked = _paused(valid_model(), {"stride": {}})
        assert asked.summary.applicability == {}


class TestTheExposureIsCountedApartFromTheLimit:
    @pytest.mark.parametrize(
        "case", ["02-iot-fleet-telemetry", "08-sso-identity-broker"]
    )
    def test_a_pause_skipped_save_by_save_counts_every_question_it_showed(self, case):
        """``introduced`` counts each question the pause showed once, however
        many saves showed it, and ``choices`` a choice a facet."""
        path = PROJECT_ROOT / "evals/corpus" / case / "model.json"
        model = SystemModel.model_validate_json(path.read_text())
        selection = {"stride": {}}
        shown, skipped, seen = (), [], {}
        for _ in range(60):
            asked = _paused(model, selection, (), shown, skipped)
            seen |= {q.key: q.decisions for q in asked.early}
            if asked.done:
                break
            admitted = _save(asked, [], skips=[q.key for q in asked.early])
            shown, skipped = admitted.shown, list(admitted.skipped)
        else:
            pytest.fail("the saves never ended")
        summary = asked.summary
        assert summary.introduced == len(seen) > 0
        assert summary.choices == sum(seen.values()) > summary.introduced
        assert summary.skipped == len(seen)
        assert (summary.settled, summary.partial, summary.unknown) == (0, 0, 0)


def test_the_summary_counts_only_the_questions_the_page_presented():
    """A listed question the page did not show is not introduced, and a part
    that waits on its parent cannot be reported as shown (ADR 0078)."""
    asked = _paused(valid_model(), {"asvs": {"level": 2}})
    assert asked.summary.introduced == 0
    assert asked.blocked, "a control: a part waits on its parent"
    shown = [q.key for q in asked.early[:2]]
    saved = _save(asked, skips=shown, presented=shown)
    after = _paused(
        valid_model(), {"asvs": {"level": 2}}, (), saved.shown, saved.skipped
    )
    assert after.summary.introduced == 2
    part, _ = asked.blocked[0]
    with pytest.raises(ValueError, match="only a question this list holds"):
        _save(asked, skips=shown, presented=[*shown, part.key])


def test_a_pause_whose_questions_left_were_all_skipped_says_so():
    """A skipped question stays open and takes an answer, so the stop is not
    ``nothing-left`` (ADR 0068, checkpoint review c4)."""
    path = PROJECT_ROOT / "evals/corpus/07-cicd-store-deploy/model.json"
    model = SystemModel.model_validate_json(path.read_text())
    selection = {"asvs": {"level": 1}}
    shown, skipped = (), []
    for _ in range(30):
        asked = _paused(model, selection, (), shown, skipped)
        if asked.done:
            break
        admitted = _save(asked, [], skips=[question.key for question in asked.early])
        shown, skipped = admitted.shown, list(admitted.skipped)
    else:
        pytest.fail("the saves never ended")
    assert (asked.held_back, asked.below_floor) == ((), ()), "a control"
    assert asked.summary.skipped > 0
    assert asked.stop == "skipped"
