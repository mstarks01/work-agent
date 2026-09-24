"""Run the signed fixture set against a live critic and print both halves.

    python -m evals.critic_review --accept-cost unknown

One model call. The set is nine drafts and a critic reads them together, which
is also how it reads a real job — so this costs about what one critic node of
one case costs, and is the narrowest instrument that can see the review change.

It prints the three measures and never one. A critic that rejects every draft
removes every unsupported finding and destroys the report, and the preserved
half is what says so. A rejection that engages with none of the anchors the
reader named is counted apart, because on fate alone it is indistinguishable
from having read the argument.

The third measure is the advice: whether the critic read each surviving
threat's recommendation the way the reader ruled it, and which it did not read
at all. Validity and advice are separate outcomes, so a critic never rejects a
claim for its recommendation and this number never moves the other two.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from analysis_service.deployment import Deployment
from analysis_service.frameworks import PACKAGES, FrameworkName, schemas_for
from analysis_service.markdown_loader import MarkdownLoader
from evals.critic_review.loading import REPO_ROOT, corpus_model, load_fixtures
from evals.critic_review.model import CriticFixture
from evals.critic_review.replay import replay, user_turn
from evals.harness.node_call import node_call


def _one_framework(fixtures: list[CriticFixture]) -> FrameworkName:
    """The package this run grades, refusing a mixed file rather than guessing.

    One call reads one package's drafts under that package's own critic skills,
    so a file naming two would compose a prompt for one and score both.
    """
    named = {fixture.framework for fixture in fixtures}
    if len(named) != 1:
        raise SystemExit(f"fixtures name {sorted(named)}; run one package at a time")
    return named.pop()


def _one_case(fixtures: list[CriticFixture]) -> str:
    """The model every draft is written against, refused the same way."""
    named = {fixture.case for fixture in fixtures}
    if len(named) != 1:
        raise SystemExit(f"fixtures name {sorted(named)}; run one case at a time")
    return named.pop()


def _critic_node(framework: FrameworkName) -> str:
    """This package's critic node, as the *graph* names it.

    :meth:`~analysis_service.deployment.Deployment.tier_of` takes this spelling
    and is the one place the walk to a tier is written — graph node to
    canonical tier node to tier. The tiers file spells the same node
    ``critic/<framework>``, so a caller that asked
    ``tiers.resolve_tier`` with this name is a second reader of that walk, and
    a wrong one.
    """
    return f"critic_{framework}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evals.critic_review", description=__doc__)
    parser.add_argument(
        "--accept-cost",
        required=True,
        help="the spend you accept for one critic call, or 'unknown' on a"
        " gateway route that prices nothing before the call",
    )
    parser.add_argument("--out", help="write the full per-fixture record here")
    args = parser.parse_args(argv)

    fixtures = load_fixtures()
    unsigned = [entry.id for entry in fixtures if entry.reviewed_by is None]
    if unsigned:
        # Not refused: running an unsigned set is how a reader sees what they
        # are being asked to sign. What it may not do is print as though the
        # numbers settled anything.
        print(
            f"WARNING: {len(unsigned)} fixture(s) are unsigned, so this run says"
            " what a critic did with what an agent proposed. It is not evidence"
            f" about the review change. Unsigned: {unsigned}",
            file=sys.stderr,
        )

    framework = _one_framework(fixtures)
    package = PACKAGES[framework]
    model = corpus_model(_one_case(fixtures))
    # The node's own route, sampling and output schema, through the one helper
    # every replay uses, and the user turn a real critic receives: its fan-in's
    # summary, built by the function the merge node returns it from.
    node = node_call(
        Deployment.from_env(), _critic_node(framework), schemas_for(framework).rulings
    )
    turn = user_turn(fixtures, package)

    async def call(instruction: str) -> str:
        return await node(instruction, turn)

    score, problems = asyncio.run(
        replay(
            fixtures,
            model,
            package,
            MarkdownLoader(REPO_ROOT / "frameworks" / framework),
            MarkdownLoader(REPO_ROOT / "prompts"),
            call,
        )
    )

    removed, of_removed = score.unsupported_removed
    preserved, of_preserved = score.valid_preserved
    agreed, of_read = score.recommendation_agreed
    print(f"unsupported removed : {removed}/{of_removed}")
    print(f"valid preserved     : {preserved}/{of_preserved}")
    print(f"recommendation read : {agreed}/{of_read} agree with the reader")
    if not score.recommendation_informative:
        why = (
            "the critic rejected the fixture whose advice the reader ruled"
            " unsound, so no surviving draft disagrees with the rest"
            if score.set_carries_both_answers
            else "no fixture survives while carrying advice the reader ruled unsound"
        )
        print(
            f"  WARNING: {why}. Every draft this critic kept expects the same"
            " answer, so one that never opened the block scores full marks"
            " here. This number says nothing about this run.",
            file=sys.stderr,
        )
    if score.recommendation_unread:
        print(
            "  survived with the advice unread: "
            + ", ".join(o.fixture_id for o in score.recommendation_unread)
        )
    if score.unknown_not_dismissed:
        # Printed, never scored. A draft that rests on the fact it cites
        # deserves a needs-info dismissing nothing, so a correct critic appears
        # here. What a reader checks is the row whose unknown the reader ruled
        # immaterial: an empty dismissal there is a critic that never answered
        # "does this claim depend on it".
        print(
            "  survived citing an unknown, none set aside: "
            + ", ".join(o.fixture_id for o in score.unknown_not_dismissed)
        )
    if score.rejected_without_engaging:
        print(
            "rejected without engaging the reader's anchors: "
            + ", ".join(o.fixture_id for o in score.rejected_without_engaging)
        )
    for outcome in score.outcomes:
        mark = "pass" if outcome.passes else "FAIL"
        print(f"  [{mark}] {outcome.fixture_id}: {outcome.status} — {outcome.reason}")
        if outcome.recommendation_read is False:
            # The words behind the verdict on the advice. Printed only where
            # the critic called it unsound, because that is the reading a
            # person has to check: whether it found the flaw the reader meant
            # or some other one.
            note = outcome.recommendation_note or "(no note)"
            print(f"           advice ruled unsound: {note}")
    for problem in problems:
        print(f"REVIEW PROBLEM: {problem}", file=sys.stderr)

    if args.out:
        Path(args.out).write_text(
            json.dumps(score.to_json(), indent=2) + "\n", encoding="utf-8"
        )
        print(f"record written to {args.out}")
    return 0 if all(outcome.passes for outcome in score.outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
