"""One framework's Critic round: the view, the check, the re-ask and the claims.

A **Critic** is one model call, and the graph may call it twice: the first
pass and one bounded re-ask. Everything code does around those two calls is
this module. :class:`CriticRound` holds what the **Fan-In** produced for one
framework as one value, the drafts, the marks and the job's assertion catalog,
so no caller can hand the Critic fewer facts than the first pass read. From
that value it builds the view the Critic reads, checks the rulings the Critic
returns, builds what the re-ask reads, merges the re-ask onto the first pass
and turns an accepted set into claims.

The round ends in one of three outcomes. :class:`Accepted` carries the claims
and the rejected claims the block holds. :class:`Reask` carries what the
re-ask reads: the problems, the roster, the drafts it must read and the first
pass it is merged onto. :class:`Failed` carries the problems a second look
still did not reconcile. The graph node parks what an outcome carries and
takes the edge it names, and decides nothing else.

The module also renders the request a critic node sends, instruction and
user turn, as ADK renders it from session state. The two critic replays call
:func:`critic_request`, so neither copies the prompt join, the placeholder
substitution or the state templating.

The primitives this module calls, :func:`~analysis_service.critic.critic_view`,
:func:`~analysis_service.critic.review_issues`,
:func:`~analysis_service.critic.merge_retry` and
:func:`~analysis_service.critic.assemble_claims`, have no other caller.
``tests/test_one_reader_lints.py`` refuses a second one.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from google.genai import types

from analysis_service.assertions import AssertionCatalog
from analysis_service.claims import (
    AnalysisMarks,
    Claim,
    FrameworkName,
    RuledClaim,
    Ruling,
    UnreconciledRuling,
)
from analysis_service.critic import (
    CriticOutputError,
    ReviewProblems,
    assemble_claims,
    critic_view,
    merge_retry,
    problem_messages,
    review_issues,
)
from analysis_service.frameworks import (
    FrameworkSchemas,
    schemas_for,
)
from analysis_service.markdown_loader import MarkdownLoader
from analysis_service.node_requests import node_instruction, node_request
from analysis_service.prompts import compose_critic_prompt, compose_recritic_prompt
from analysis_service.skills import compose_critic_skills
from analysis_service.sources import render_fenced
from analysis_service.system_model import SystemModel

if TYPE_CHECKING:
    from analysis_service.graph import FrameworkNodes

# The per-framework artifacts a review prompt reads, as ``{placeholder}`` in
# ``critic.md`` and ``recritic.md`` against the state-key artifact it resolves
# to. N critics run in parallel against one session state, which cannot hold N
# values for one key, so ``{drafts}`` is substituted at build time into
# ``{draft_view_stride}`` and each critic reads only its own framework's.
REVIEW_ARTIFACTS: Mapping[str, str] = MappingProxyType(
    {
        "drafts": "draft_view",
        "previous_review": "previous_review",
        "critic_issues": "critic_issues",
        "draft_roster": "draft_roster",
        "unreconciled_drafts": "unreconciled_drafts",
    }
)


def rulings_of(ruled: Sequence[Any], schemas: FrameworkSchemas) -> list[Ruling]:
    """One critic's emission, revalidated as this framework's own ruling type.

    The one reader of "what shape is a critic's answer". The graph is not its
    only caller: ``evals/critic_review`` and ``evals/harness/critic_replay.py``
    replay a critic outside the graph, and ``evals/harness/follow_up_policies.py``
    reads an archived critic sample, and each has to parse it the same way.
    Parsing with the neutral :class:`~analysis_service.claims.Ruling` instead
    refuses every STRIDE ruling, because ``frameworks/stride/critic.md`` asks
    for a ``confidence`` the neutral model forbids.
    """
    return list(schemas.rulings.model_validate({"claims": list(ruled)}).claims)


@dataclass(frozen=True)
class Accepted:
    """One critic pass that reconciled with its drafts, assembled."""

    #: The actionable claims, ordered as the block carries them.
    claims: list[RuledClaim]
    #: The rejected claims, in the block's audit array.
    rejected: list[RuledClaim]

    @property
    def count(self) -> int:
        """How many rulings the pass returned, for the routing event."""
        return len(self.claims) + len(self.rejected)


@dataclass(frozen=True)
class FirstPass:
    """What the second look merges the re-ask onto.

    ``rulings`` is the first pass in the ruling model's own spelling, which is
    what :func:`~analysis_service.critic.merge_retry` compares: a provider may
    spell one ruling two ways across two calls, an optional key present as
    ``null`` in one and absent in the other, and a comparison over the raw
    payloads would read that as a change. ``repairable`` is every drafted ID a
    problem named, sorted: the whole of what the re-ask may change.
    """

    rulings: list[dict]
    repairable: list[str]


@dataclass(frozen=True)
class Unreconciled:
    """One ruling set that did not reconcile with its drafts."""

    #: One :class:`~analysis_service.claims.UnreconciledRuling` per problem:
    #: the claim, which check found it, and the sentence the re-ask is asked to
    #: fix it by.
    problems: list[UnreconciledRuling]

    @property
    def messages(self) -> list[str]:
        """Each problem's sentence, in the words the re-ask reads."""
        return problem_messages(self.problems)


@dataclass(frozen=True)
class Reask(Unreconciled):
    """One first pass that did not reconcile, and everything the re-ask needs.

    Built in one place so the prompt and the check cannot disagree about which
    claims are in trouble: the messages say what did not reconcile and the view
    carries the drafts those messages name, and both come out of one call over
    one set.
    """

    #: Every drafted ID: the covering set the re-ask must reproduce.
    roster: list[str]
    #: The few drafts a structural fix cannot be made without reading.
    unreconciled: list[dict]
    #: The first pass, for the second look to merge the re-ask onto.
    first: FirstPass


@dataclass(frozen=True)
class Failed(Unreconciled):
    """One ruling set that does not reconcile and gets no further re-ask."""

    @property
    def error(self) -> CriticOutputError:
        """The one spelling of the failure a job reports, every problem at once."""
        return CriticOutputError("; ".join(self.messages))


@dataclass(frozen=True)
class CriticRound:
    """One framework's Critic round over what its Fan-In produced.

    The three evidence fields travel together and none has a default. A view
    built from fewer marks than the first pass read is a second reader of one
    draft (#1082): a critic told once that a quote was invented must not read
    the same quote clean on the second look, and a replay must send what the
    graph sent.
    """

    framework: FrameworkName
    schemas: FrameworkSchemas
    model: SystemModel
    drafts: Sequence[Claim]
    marks: AnalysisMarks
    assertions: AssertionCatalog | None

    @classmethod
    def of(
        cls,
        framework: FrameworkName,
        model: SystemModel,
        drafts: Sequence[Claim],
        marks: AnalysisMarks,
        assertions: AssertionCatalog | None,
    ) -> CriticRound:
        """The round for one framework, with that framework's own schemas."""
        return cls(framework, schemas_for(framework), model, drafts, marks, assertions)

    def view(self, only: Collection[str] | None = None) -> list[dict]:
        """The drafts the Critic reads, every computed fact already on them.

        ``only`` narrows the set to the drafts the re-ask must read; the marks
        and the catalog are the same on both passes by construction.
        """
        return critic_view(
            self.drafts,
            only=only,
            repaired=self.marks.repaired_quotes,
            unverified=self.marks.unverified_grounds,
            assertions=self.assertions,
        )

    def summary(self) -> dict[str, Any]:
        """What the ``merge`` node outputs, and so the user turn its critic receives.

        ADK hands a node's output to the next node through ``to_user_content``,
        so this dictionary is the critic's user turn. The one reader of that
        shape: the node returns it, and :func:`critic_request` builds the turn
        from it.
        """
        return {
            "framework": self.framework,
            "draft_count": len(self.drafts),
            "unverified_count": len(self.marks.unverified_grounds),
            "unresolved_mention_count": len(self.marks.unresolved_mentions),
        }

    def rulings(self, emitted: Sequence[Any]) -> list[Ruling]:
        """A critic's emission as this framework's rulings."""
        return rulings_of(emitted, self.schemas)

    def canonical(self, emitted: Sequence[Any]) -> list[dict]:
        """The same emission in the ruling model's own spelling, one per ruling."""
        return [ruling.model_dump(mode="json") for ruling in self.rulings(emitted)]

    def first(self, emitted: Sequence[Any]) -> Accepted | Reask:
        """Rule on the first pass: the claims, or what the re-ask must read.

        The re-ask sees a roster of IDs plus the few it must read, not the
        whole set again. Its job is structural: cover exactly the drafted IDs,
        once each, with unknowns that resolve, and an ID carries the whole of
        that claim.
        """
        rulings, problems = self._checked(emitted)
        if not problems:
            return self._assembled(rulings)
        return Reask(
            problems=list(problems.problems),
            roster=[draft.id for draft in self.drafts],
            unreconciled=self.view(only=problems.implicated),
            first=FirstPass(
                rulings=[ruling.model_dump(mode="json") for ruling in rulings],
                repairable=sorted(problems.repairable),
            ),
        )

    def merge(
        self, first: FirstPass, emitted: Sequence[Any]
    ) -> tuple[list[Mapping[str, Any]], list[UnreconciledRuling]]:
        """The re-ask merged onto the first pass, and the changes it was not asked for.

        The re-ask's ruling applies only to the IDs a problem named; every
        other draft keeps the first pass's ruling, and a re-ask ruling that
        differs from it is recorded as drift and discarded
        (:func:`~analysis_service.critic.merge_retry`). The merged set is what
        ``reviewed`` holds from here on, so :meth:`outcome` reads it next.
        """
        return merge_retry(
            first.rulings,
            self.canonical(emitted),
            first.repairable,
            [draft.id for draft in self.drafts],
        )

    def outcome(self, emitted: Sequence[Any]) -> Accepted | Failed:
        """The round's end over one ruling set: the claims, or what still fails.

        The second look, ``critic_failed`` and ``assemble`` all read a ruling
        set that gets no further re-ask, so they share this one answer.
        """
        rulings, problems = self._checked(emitted)
        if problems:
            return Failed(problems=list(problems.problems))
        return self._assembled(rulings)

    def _checked(self, emitted: Sequence[Any]) -> tuple[list[Ruling], ReviewProblems]:
        rulings = self.rulings(emitted)
        return rulings, review_issues(self.drafts, rulings, self.model)

    def _assembled(self, rulings: Sequence[Ruling]) -> Accepted:
        claims, rejected = assemble_claims(
            self.drafts, rulings, self.model, self.schemas
        )
        return Accepted(claims=claims, rejected=rejected)


def critic_instruction(
    package_loader: MarkdownLoader,
    prompt_loader: MarkdownLoader,
    nodes: FrameworkNodes,
) -> str:
    """One framework's critic instruction, as the graph builds its critic node."""
    return _review_instruction(
        package_loader,
        prompt_loader,
        nodes,
        compose_critic_prompt(prompt_loader),
    )


def recritic_instruction(
    package_loader: MarkdownLoader,
    prompt_loader: MarkdownLoader,
    nodes: FrameworkNodes,
) -> str:
    """One framework's re-ask instruction, as the graph builds its recritic node."""
    return _review_instruction(
        package_loader,
        prompt_loader,
        nodes,
        compose_recritic_prompt(prompt_loader),
    )


def _review_instruction(
    package_loader: MarkdownLoader,
    prompt_loader: MarkdownLoader,
    nodes: FrameworkNodes,
    prompt: str,
) -> str:
    """One framework's critic or re-ask instruction, with its keys resolved.

    Both share :func:`~analysis_service.skills.compose_critic_skills` byte for
    byte, so the re-ask reads the same rubric, the same ``critic.md`` and the
    same lane digest the critic did, it may have to re-rule a draft it dropped,
    and the two share that cacheable prefix across jobs.

    The placeholders a review prompt carries are this framework's own state
    keys, substituted here because N critics run against one session state.
    The job-varying placeholders stay for ADK to template.
    """
    for placeholder, artifact in REVIEW_ARTIFACTS.items():
        prompt = prompt.replace(f"{{{placeholder}}}", f"{{{nodes.key(artifact)}}}")
    skills = compose_critic_skills(package_loader, nodes.package)
    return node_instruction(skills, prompt)


async def critic_request(
    package_loader: MarkdownLoader,
    prompt_loader: MarkdownLoader,
    nodes: FrameworkNodes,
    shared: Mapping[str, str],
    critic_round: CriticRound,
) -> tuple[str, types.Content]:
    """The request this framework's critic node sends over ``critic_round``.

    ``shared`` holds the job-wide rendered keys ``prepare`` wrote: the model,
    the crossings and the input text. The draft view is rendered here from
    the round, so a replay cannot show the critic a view the ``merge`` node
    would not have written.
    """
    state = {**shared, nodes.key("draft_view"): render_fenced(critic_round.view())}
    template = critic_instruction(package_loader, prompt_loader, nodes)
    return await node_request(template, state, critic_round.summary(), None)
