"""Critic yield, offline, with zero provider calls.

The instrument has one job: say what the critic killed *and* what killing it
cost. These tests hold the two halves apart — a critic that kills a rejected
draft and a critic that kills a must-find draft must never produce the same
number — and pin the two properties the whole measurement rests on: the scored
claim is the same string on both sides, and the post-critic score is exactly
what the scorer alone would have said.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.harness import critic_yield
from evals.harness.critic_yield import aggregate_yield, score_case_with_yield
from evals.harness.fingerprint import components_for, version_for
from evals.harness.ledger import Ledger, cast
from evals.harness.reference import load_case
from evals.harness.scorer import score_case
from stride_service.report import Verdict
from tests.eval_factories import ScriptedMatcher, draft_threat, promote

CORPUS_DIR = Path(__file__).resolve().parents[1] / "evals" / "corpus"
CONTROL_CASE = CORPUS_DIR / "01-payments-checkout"


@pytest.fixture(scope="module")
def case():
    return load_case(CONTROL_CASE)


def _draft_for(reference, sequence: int):
    """A draft aimed squarely at one reference, claiming it verbatim."""
    return draft_threat(
        sequence,
        reference.category,
        reference.claim,
        element_ids=reference.affected_element_ids,
        likelihood=reference.severity.likelihood,
        impact=reference.severity.impact,
    )


def _identity_matcher(drafts):
    """Matches identical claim strings, which is what the drafts carry."""
    return ScriptedMatcher((draft.title, draft.title) for draft in drafts)


def _rejecting(case, *drafts):
    """A ledger holding a substance down-vote on each given draft."""
    flows = {flow.id: (flow.source, flow.destination) for flow in case.model.data_flows}
    return Ledger(
        [
            cast(
                components_for(
                    "stride",
                    draft.category,
                    tuple(draft.affected_element_ids),
                    flows,
                    verb=draft.verb,
                ),
                case.id,
                "down",
                voter="test-reviewer",
                reason="unsupported-by-the-model",
                version=version_for("stride"),
            )
            for draft in drafts
        ]
    )


def _must_find(case):
    return next(ref for ref in case.claims_for("stride") if ref.must_find)


def _expected(case):
    return next(ref for ref in case.claims_for("stride") if not ref.must_find)


def test_scorer_takes_drafts_without_promoting_them(case):
    # The shape decision: drafts score as drafts. A draft carries no verdict,
    # so the needs-info bypass is simply inactive and the threat is keyed
    # like any other unmatched claim.
    drafts = [draft_threat(1, "spoofing", "An attacker forges a webhook.")]

    score = score_case(case, drafts, ScriptedMatcher(), Ledger())

    assert score.produced_ids == ("S-01",)
    assert score.needs_info_unmatched == ()
    assert [entry.threat_id for entry in score.unlisted] == ["S-01"]


def test_needs_info_bypass_still_applies_to_ruled_threats(case):
    # And the post-critic side keeps the rule: a needs-info threat is never
    # keyed as a false positive.
    draft = draft_threat(1, "spoofing", "An attacker forges a webhook.")
    ruled = promote(
        draft,
        verdict=Verdict(
            status="needs-info",
            reason="authentication on this flow is unknown",
            related_unknowns=[
                {
                    "element_id": draft.affected_element_ids[0],
                    "attribute": "authentication",
                }
            ],
        ),
    )

    score = score_case(case, [ruled], ScriptedMatcher(), Ledger())

    assert score.needs_info_unmatched == ("S-01",)
    assert score.unlisted == ()


def test_killing_a_rejected_draft_is_the_critic_earning_its_cost(case):
    kept = _draft_for(_must_find(case), 1)
    junk = draft_threat(
        9, "tampering", "An attacker edits a table that does not exist."
    )
    votes = _rejecting(case, junk)

    scored = score_case_with_yield(
        case, [kept, junk], [promote(kept)], _identity_matcher([kept]), votes
    )
    result = scored.critic_yield

    assert [entry.threat_id for entry in result.killed] == [junk.id]
    assert result.killed[0].disposition == "rejected"
    assert result.rejected_killed == 1
    assert result.matched_killed == 0
    assert result.kill_precision == 1.0
    assert result.rejected_kill_rate == 1.0


def test_killing_a_matched_draft_is_the_number_that_can_veto_the_pattern(case):
    reference = _must_find(case)
    killed = _draft_for(reference, 1)

    scored = score_case_with_yield(
        case, [killed], [], _identity_matcher([killed]), Ledger()
    )
    result = scored.critic_yield

    # The same kill count as the test above, and it means the opposite thing.
    assert result.kill_count == 1
    assert result.rejected_killed == 0
    assert result.matched_killed == 1
    assert result.must_find_killed == 1
    assert result.matched_kill_rate == 1.0
    assert result.killed[0].disposition == "matched-must-find"
    assert result.must_find_before == 1 and result.must_find_after == 0


def test_expected_tier_kills_are_separated_from_must_find(case):
    reference = _expected(case)
    killed = _draft_for(reference, 1)

    result = score_case_with_yield(
        case, [killed], [], _identity_matcher([killed]), Ledger()
    ).critic_yield

    assert result.matched_killed == 1
    assert result.must_find_killed == 0
    assert result.killed[0].disposition == "matched-expected"


def test_the_post_critic_score_is_the_scorer_verbatim(case):
    reference = _must_find(case)
    kept = _draft_for(reference, 1)
    junk = draft_threat(
        9, "tampering", "An attacker edits a table that does not exist."
    )
    threats = [promote(kept)]

    with_yield = score_case_with_yield(
        case, [kept, junk], threats, _identity_matcher([kept]), Ledger()
    ).score
    alone = score_case(case, threats, _identity_matcher([kept]), Ledger())

    # Yield must be free of charge to every metric this harness already
    # reports: the pre-critic pass changes what is measured, never the answer.
    assert with_yield.to_json() == alone.to_json()


def test_the_scored_claim_is_the_same_string_on_both_sides(case):
    reference = _must_find(case)
    draft = _draft_for(reference, 1)
    matcher = _identity_matcher([draft])

    score_case_with_yield(case, [draft], [promote(draft)], matcher, Ledger())

    asked = {pair.candidate_claim for pair in matcher.claim_calls}
    assert asked == {reference.claim}


def test_a_critic_edit_to_the_title_is_a_real_second_question(case):
    # The pair carries the claim string, so a critic that rewrites a title is
    # ruled on the rewrite rather than silently credited with the draft's
    # ruling.
    reference = _must_find(case)
    draft = _draft_for(reference, 1)
    reworded = promote(draft.model_copy(update={"title": "Reworded by the critic."}))
    matcher = _identity_matcher([draft])

    score_case_with_yield(case, [draft], [reworded], matcher, Ledger())

    assert "Reworded by the critic." in {
        pair.candidate_claim for pair in matcher.claim_calls
    }


def test_aggregate_pools_counts_rather_than_averaging_rates(case):
    reference = _must_find(case)
    kept = _draft_for(reference, 1)
    junk = draft_threat(
        9, "tampering", "An attacker edits a table that does not exist."
    )

    big = score_case_with_yield(
        case,
        [kept, junk],
        [promote(kept)],
        _identity_matcher([kept]),
        _rejecting(case, junk),
    ).critic_yield
    small = score_case_with_yield(
        case, [kept], [promote(kept)], _identity_matcher([kept]), Ledger()
    )

    totals = aggregate_yield([big, small.critic_yield])

    assert totals["cases"] == 2
    assert totals["drafts_in"] == 3
    assert totals["killed"] == 1
    assert totals["rejected_killed"] == 1
    # Pooled: 1 kill in 3 drafts, not the mean of 50% and 0%.
    assert totals["kill_rate"] == round(1 / 3, 3)


def test_the_cli_reports_both_sides_per_case_and_pooled(case, capsys):
    # The wiring: one pass over the corpus produces the yield alongside the
    # scores, so a sweep measures it rather than needing a re-run.
    from evals.harness import modes, run

    reference = _must_find(case)
    kept = _draft_for(reference, 1)
    junk = draft_threat(
        9, "tampering", "An attacker edits a table that does not exist."
    )
    analysis = modes.AnalysisRun(
        report=_report_with(case, [promote(kept)]),
        drafts={"stride": (kept, junk)},
    )
    scores, yields = run._score_runs(
        [case], {case.id: analysis}, _identity_matcher([kept]), _rejecting(case, junk)
    )
    critic_yield.render(yields)

    assert [score.case_id for score in scores] == [case.id]
    assert yields[0].rejected_killed == 1
    printed = capsys.readouterr().out
    assert "killed-rejected 1/1" in printed
    assert "killed-real 0/1" in printed


def _report_with(case, threats):
    """A minimal report carrying the given threats, as the modes build one."""
    from datetime import UTC, datetime

    from stride_service.frameworks.stride.record import (
        STRIDE_VERSION,
        StrideAnalysis,
    )
    from stride_service.report import (
        FrameworkSelection,
        InputRef,
        Job,
        NodeRun,
        Report,
    )

    now = datetime.now(UTC)
    claims = list(threats)
    return Report(
        job=Job(
            id=f"eval-{case.id}",
            created_at=now,
            completed_at=now,
            frameworks=[FrameworkSelection(name="stride")],
        ),
        input=InputRef.of(system_name=case.meta.title, sources=case.sources),
        nodes=[NodeRun(node="eval", model=None, duration_ms=0)],
        system_model=case.model,
        boundary_crossings=case.model.boundary_crossings(),
        elements_analyzed=len(case.model.elements()),
        analyses=[
            StrideAnalysis(
                framework="stride",
                framework_version=STRIDE_VERSION,
                disclaimer="Scripted for the critic-yield CLI test.",
                claims=claims,
                rejected_claims=[],
                summary=StrideAnalysis.summarize(claims, []),
            )
        ],
    )


def test_an_untouched_critic_yields_nothing_and_breaks_nothing(case):
    draft = _draft_for(_must_find(case), 1)

    result = score_case_with_yield(
        case, [draft], [promote(draft)], _identity_matcher([draft]), Ledger()
    ).critic_yield

    assert result.killed == ()
    assert result.kill_rate == 0.0
    assert result.rejected_kill_rate == 0.0
    assert result.matched_kill_rate == 0.0
    assert result.to_json()["counts"]["drafts_in"] == 1
