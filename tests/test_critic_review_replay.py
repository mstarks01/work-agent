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

import pytest

from analysis_service.frameworks import PACKAGES
from analysis_service.markdown_loader import MarkdownLoader
from analysis_service.prompts import compose_critic_prompt
from analysis_service.system_model import SystemModel
from evals.critic_review import replay as R
from evals.critic_review.loading import REPO_ROOT, corpus_model, load_fixtures
from evals.critic_review.model import CriticFixture

PACKAGE = PACKAGES["stride"]
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


def test_seven_of_the_eight_signed_drafts_never_reach_the_critic(fixtures, model):
    """The baseline, and the whole reason the review change is on the table.

    Every fixture but the control carries an unknown ground, and
    ``unsettled_drafts`` drops those before a critic reads anything. So the
    nonsense arguments, the contradicted claims and the sound conditional
    findings are all settled by the same rule, in code, unread — and no
    measurement over a finished report can see the difference.

    This test is expected to change when the review change lands. It is written
    to state the baseline plainly, so that what moves is legible in one diff.
    """
    prompt = composed(fixtures, model)
    shown = [fixture for fixture in fixtures if fixture.draft["id"] in prompt]

    assert len(shown) == 1
    assert shown[0].id == "contradicted-receipt-write-unauthenticated"
    assert not any(
        ground["kind"] == "unknown-attribute" for ground in shown[0].draft["grounds"]
    ), "the one draft shown is the one carrying no unknown ground"


def rule(claim_id: str, status: str, reason: str = "") -> dict:
    verdict = {"status": status, "reason": reason}
    if status == "rejected":
        verdict["rejected_because"] = "reasoning"
    return {"id": claim_id, "verdict": verdict}


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
