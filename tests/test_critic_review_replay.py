"""The replay harness: what a critic is shown, and how its answer is scored.

Offline and scripted, so the mechanical half of the instrument gates on every
PR. What a *real* critic does with the fixture set is a measurement and costs a
call; that the harness composes the right prompt, refuses malformed output and
scores both halves is a property, and properties belong here.

The baseline this pins is the finding itself: with the drafts a reader signed,
seven of the eight never reach the critic at all.
"""

from __future__ import annotations

import asyncio
import json
from typing import get_args

import pytest

from analysis_service.frameworks import PACKAGES, schemas_for
from analysis_service.markdown_loader import MarkdownLoader
from analysis_service.prompts import compose_critic_prompt
from analysis_service.system_model import SystemModel
from evals.critic_review import replay as R
from evals.critic_review.loading import REPO_ROOT, corpus_model, load_fixtures
from evals.critic_review.model import CriticFixture

PACKAGE = PACKAGES["stride"]
PACKAGE_RULINGS = schemas_for("stride").rulings
RULING_SHAPE = get_args(PACKAGE_RULINGS.model_fields["claims"].annotation)[0]
#: What this package requires of a ruling beyond the neutral two fields.
PACKAGE_REQUIRED = {
    name: "medium"
    for name, field in RULING_SHAPE.model_fields.items()
    if field.is_required() and name not in ("id", "verdict")
}
PACKAGE_LOADER = MarkdownLoader(REPO_ROOT / "frameworks" / "stride")
PROMPT_LOADER = MarkdownLoader(REPO_ROOT / "prompts")


@pytest.fixture(scope="module")
def fixtures() -> list[CriticFixture]:
    return load_fixtures()


@pytest.fixture(scope="module")
def model() -> SystemModel:
    return corpus_model("01-payments-checkout")


def composed(fixtures, model) -> str:
    return R.compose(fixtures, model, PACKAGE, PACKAGE_LOADER, PROMPT_LOADER)


def test_the_harness_fills_exactly_the_placeholders_the_prompt_declares():
    """Two readers of "what does the critic prompt need", checked against each other.

    The graph turns each placeholder into a state key and ADK templates it; this
    module substitutes the block itself, because there is no session here to
    hold one. A placeholder added to ``critic.md`` and not to
    :data:`~evals.critic_review.replay.PLACEHOLDERS` would reach a model as a
    literal brace, and a critic reading ``{boundary_crossings}`` where the
    crossings should be rules on a model it cannot see and never says why.
    """
    declared = set(re_placeholders(compose_critic_prompt(PROMPT_LOADER)))

    assert declared == set(R.PLACEHOLDERS)


def re_placeholders(text: str) -> list[str]:
    import re

    return re.findall(r"\{([a-z_]+)\}", text)


def test_no_placeholder_survives_composition(fixtures, model):
    prompt = composed(fixtures, model)

    assert not re_placeholders(prompt), "a brace reached the model unfilled"


def test_the_prompt_carries_the_package_skills_and_the_shared_steps(fixtures, model):
    prompt = composed(fixtures, model)

    assert "Claim Critic" in prompt
    assert "rejected_because" in prompt


def test_every_signed_draft_reaches_the_critic(fixtures, model):
    """What the review change bought, stated over the set a reader signed.

    Seven of these eight carry an unknown ground. Under the rule that settled
    such a draft in code, one reached the critic — so the nonsense arguments,
    the contradicted claims and the sound conditional findings were all
    disposed of by the same fact, unread, and no measurement over a finished
    report could tell them apart.
    """
    prompt = composed(fixtures, model)

    shown = [fixture for fixture in fixtures if fixture.draft["id"] in prompt]

    assert len(shown) == len(fixtures)
    shielded = [
        fixture
        for fixture in fixtures
        if any(g["kind"] == "unknown-attribute" for g in fixture.draft["grounds"])
    ]
    assert len(shielded) == 7, "the set is mostly drafts that used to skip review"


def test_the_critic_reads_the_recommendations_it_now_rules_on(fixtures, model):
    """The recommendation reading needs the block, and the fixtures carry one each.

    Read off each fixture's own mitigation rather than a string pinned here. A
    pinned string passes while every recommendation the critic reads describes
    this file rather than a claim.
    """
    prompt = composed(fixtures, model)

    for fixture in fixtures:
        # ``mitigations`` is a field of a package whose claims recommend
        # something. A package whose record declares none shows the critic
        # nothing here, and there is nothing to look for.
        for mitigation in fixture.draft.get("mitigations", ()):
            assert mitigation["summary"] in prompt


def rule(claim_id: str, status: str, reason: str = "") -> dict:
    """One scripted ruling, in the shape this package's critic really emits.

    The package's own required fields are filled from its schema, never spelled
    here. A helper emitting the neutral shape alone lets the replay's parser and
    the graph's drift apart while both stay green: the graph reads a
    ``ThreatRuling``, and nothing offline would hand the replay one.
    """
    verdict = {"status": status, "reason": reason}
    if status == "rejected":
        verdict["rejected_because"] = "reasoning"
    return {"id": claim_id, "verdict": verdict, **PACKAGE_REQUIRED}


def run(fixtures, model, payload: dict):
    async def call(_: str) -> str:
        return json.dumps(payload)

    return asyncio.run(
        R.replay(fixtures, model, PACKAGE, PACKAGE_LOADER, PROMPT_LOADER, call)
    )


def test_a_critic_answering_every_fixture_correctly_scores_both_halves(fixtures, model):
    """The shape of a pass: every fate right, every negative reason engaged."""
    payload = {"claims": []}
    for fixture in fixtures:
        claim_id = fixture.draft["id"]
        if fixture.expect.survives:
            payload["claims"].append(rule(claim_id, "needs-info", "open fact"))
        else:
            anchor = fixture.expect.reason_must_name[0]
            payload["claims"].append(
                rule(
                    claim_id, "rejected", f"the draft's step about {anchor} is invented"
                )
            )

    score, _ = run(fixtures, model, payload)

    assert score.unsupported_removed == (5, 5)
    assert score.valid_preserved == (3, 3)
    assert score.rejected_without_engaging == ()


def test_a_critic_that_rejects_everything_fails_the_preserved_half(fixtures, model):
    """The cheapest wrong answer, and the half that catches it.

    Rejecting every conditional draft removes every unsupported finding. A
    measurement reporting only that number would call it a total success.
    """
    payload = {
        "claims": [
            rule(f.draft["id"], "rejected", "the claim rests on an unknown control")
            for f in fixtures
        ]
    }

    score, _ = run(fixtures, model, payload)

    assert score.valid_preserved == (0, 3)
    # And it does not even earn the removals: none of those reasons engages.
    assert score.unsupported_removed == (0, 5)
    assert len(score.rejected_without_engaging) == 5


def test_killing_the_right_draft_for_the_wrong_reason_is_reported_apart(
    fixtures, model
):
    """Right fate, empty reasoning — the row that looks like success on fate alone."""
    payload = {"claims": []}
    for fixture in fixtures:
        claim_id = fixture.draft["id"]
        status = "needs-info" if fixture.expect.survives else "rejected"
        payload["claims"].append(rule(claim_id, status, "unknown control, so no"))

    score, _ = run(fixtures, model, payload)

    assert score.valid_preserved == (3, 3)
    assert score.unsupported_removed == (0, 5)
    assert {o.fixture_id for o in score.rejected_without_engaging} == {
        f.id for f in fixtures if not f.expect.survives
    }


def test_output_that_is_not_the_contract_raises_rather_than_reading_as_survival(
    fixtures, model
):
    """A replay reading zero rulings would report every fixture as surviving."""
    with pytest.raises(ValueError, match="claims"):
        run(fixtures, model, {"rulings": []})


def test_a_dropped_fixture_is_a_review_problem_and_not_a_silent_pass(fixtures, model):
    """``review_issues`` is the graph's own check, run over the same inputs."""
    payload = {
        "claims": [rule(f.draft["id"], "rejected", "invented") for f in fixtures[:-1]]
    }

    _, problems = run(fixtures, model, payload)

    assert problems


def test_the_replay_parses_what_this_package_asks_a_critic_for():
    """The two readers of "what shape is a critic's answer", against each other.

    The harness composes ``frameworks/stride/critic.md``, which asks for a
    ``confidence`` on every ruling. Parsing the answer with the neutral
    ``Ruling``, which forbids one, raises on every real ruling after the paid
    call while scripted rulings carrying no package field pass. Built from the
    package's own schema rather than spelled here, so a package that adds a
    second judgement field is covered the day it lands.
    """
    emission = {"claims": [rule("S-01", "needs-info", "open fact")]}

    parsed = R.parse_rulings(json.dumps(emission), PACKAGE)

    assert PACKAGE_REQUIRED, "this package asks a critic for nothing of its own"
    assert [ruling.id for ruling in parsed] == ["S-01"]
    assert isinstance(parsed[0], RULING_SHAPE)


def test_the_advice_is_scored_apart_from_the_fate(fixtures, model):
    """The third half: what the critic made of the recommendation.

    A critic that read the advice and one that never looked emitted the same
    ruling, so a fixture's `recommendation_sound` could not be checked against
    anything. The package's ruling carries the reading now, and this drives the
    three answers it can give: agreeing with the reader, disagreeing, and
    making no reading at all.
    """
    payload = {"claims": []}
    for fixture in fixtures:
        status = "needs-info" if fixture.expect.survives else "rejected"
        ruling = rule(
            fixture.draft["id"], status, "; ".join(fixture.expect.reason_must_name)
        )
        if fixture.expect.survives:
            # The reader ruled every surviving fixture's advice sound, so an
            # agreeing critic says so here.
            ruling["recommendation"] = {"sound": True, "note": ""}
        payload["claims"].append(ruling)

    score, _ = run(fixtures, model, payload)

    agreed, of_read = score.recommendation_agreed
    assert of_read == sum(1 for f in fixtures if f.expect.survives)
    assert agreed == of_read
    # Every rejected draft owes no reading, so none is counted unread.
    assert score.recommendation_unread == ()


def test_a_surviving_draft_with_no_reading_is_counted_unread(fixtures, model):
    """The state that had no observable, now named rather than read as approval."""
    payload = {
        "claims": [
            rule(
                f.draft["id"],
                "needs-info" if f.expect.survives else "rejected",
                "; ".join(f.expect.reason_must_name),
            )
            for f in fixtures
        ]
    }

    score, _ = run(fixtures, model, payload)

    assert score.recommendation_agreed == (0, 0)
    unread = {o.fixture_id for o in score.recommendation_unread}
    assert unread == {f.id for f in fixtures if f.expect.survives}


def test_a_critic_disagreeing_about_the_advice_does_not_move_the_fate(fixtures, model):
    """Validity and advice are separate outcomes, and the score keeps them so."""
    payload = {"claims": []}
    for fixture in fixtures:
        status = "needs-info" if fixture.expect.survives else "rejected"
        ruling = rule(
            fixture.draft["id"], status, "; ".join(fixture.expect.reason_must_name)
        )
        ruling["recommendation"] = {
            "sound": False,
            "note": "names a control already stated",
        }
        payload["claims"].append(ruling)

    score, _ = run(fixtures, model, payload)

    preserved, of_preserved = score.valid_preserved
    assert preserved == of_preserved, "the fate is unchanged by the advice reading"
    agreed, of_read = score.recommendation_agreed
    assert of_read == len(fixtures)
    assert agreed == sum(1 for f in fixtures if not f.expect.recommendation_sound)
