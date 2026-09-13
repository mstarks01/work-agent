"""Run the signed fixture set against a live critic and print both halves.

    python -m evals.critic_review --accept-cost unknown

One model call. The set is eight drafts and a critic reads them together, which
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

from google.adk.models.llm_request import LlmRequest
from google.genai import types

from analysis_service.binding import build_tier_adapters
from analysis_service.deployment import Deployment
from analysis_service.frameworks import PACKAGES, FrameworkName, schemas_for
from analysis_service.markdown_loader import MarkdownLoader
from evals.critic_review.loading import REPO_ROOT, corpus_model, load_fixtures
from evals.critic_review.model import CriticFixture
from evals.critic_review.replay import replay


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
    deployment = Deployment.from_env()
    # The tier the critic node runs on, read from the node map rather than
    # named here: a deployment that moves the critic moves this run with it.
    # Through ``tier_of``, which is the one reader of the two-step walk.
    tier = deployment.tier_of(_critic_node(framework))
    adapter = build_tier_adapters(
        deployment.tiers,
        deployment.sampling,
        deployment.resilience,
        env=deployment.env,
    )[tier]

    # The node's own sampling and the node's own output schema. ADK gives a
    # real critic node both through its ``LlmAgent``: the tier's
    # ``generate_content_config``, and a ``response_format`` derived from the
    # agent's ``output_schema`` — ADK's LiteLLM adapter builds it from
    # ``config.response_schema``. A request built without them is a shape this
    # service never sends — ``ExecutedLlm`` says every node binds a schema and
    # so never streams — and an unconstrained gpt-5.6 answered one with no text
    # at all, which the replay could only report as a parse failure.
    sampling = deployment.sampling.for_tier(tier)
    schema = schemas_for(framework).rulings

    async def call(instruction: str) -> str:
        """One turn through the adapter the graph would have used.

        The instruction goes in as the system instruction and the user turn is
        the single word the graph's own critic turn carries, so what reaches
        the provider has the shape a real critic call has.
        """
        config = sampling.to_generate_content_config()
        config.system_instruction = instruction
        config.response_schema = schema
        request = LlmRequest(
            model=adapter.model,
            contents=[types.Content(role="user", parts=[types.Part(text="Rule.")])],
            config=config,
        )
        chunks = []
        async for response in adapter.generate_content_async(request, False):
            for part in (response.content.parts if response.content else []) or []:
                if part.text:
                    chunks.append(part.text)
        return "".join(chunks)

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
        print(
            "  WARNING: every fixture that can carry a reading expects the same"
            " answer, so a critic that never opens the block scores full marks"
            " here. This number says nothing until a fixture survives while"
            " carrying advice the reader ruled unsound.",
            file=sys.stderr,
        )
    if score.recommendation_unread:
        print(
            "  survived with the advice unread: "
            + ", ".join(o.fixture_id for o in score.recommendation_unread)
        )
    if score.rejected_without_engaging:
        print(
            "rejected without engaging the reader's anchors: "
            + ", ".join(o.fixture_id for o in score.rejected_without_engaging)
        )
    for outcome in score.outcomes:
        mark = "pass" if outcome.passes else "FAIL"
        print(f"  [{mark}] {outcome.fixture_id}: {outcome.status} — {outcome.reason}")
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
