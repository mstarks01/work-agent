"""The questions one round asks, and the answers it admits.

**One value answers both "what did this job ask" and "may this answer land".**
The HTTP routes, the first-run app and the in-process engine each serve a
job's questions and admit a submitter's answers. A :class:`QuestionSet` is
built once from the model and catalog the questions were asked about, and its
:meth:`~QuestionSet.admit` takes the answers. The facts it admits are the keys
of the questions it holds, so the list a caller serves and the list it accepts
cannot differ.

**A job waiting on answers asks the early list; a finished one asks the
report's list.** Both ask the link questions its catalog raises. A waiting job
may continue with no answers, and a finished one has nothing to continue.

**A waiting job asks in rounds** (ADR 0053). A round shows at most
:data:`ROUND_SIZE` capability questions and as many questions about the
model's elements. A question is shown only at or above its kind's floor, and
one pause asks each kind at most its limit in all: :data:`EARLY_RULES` is the
table. A question answered in part comes back first, outside the limit, and
where the limits hold questions back the job says so
(:attr:`QuestionSet.stop`). A submitter saves a round's answers, which writes
them onto the job and runs no model, and the next round is read off the model
with them in. So an
answer can hide the parts of a capability it rules out, and a named mechanism
lowers the questions that rested on its lead.

**A report offers one follow-up** (ADR 0054). A fact an earlier round
answered is not asked again, and an answer of "I don't know" counts. A report
lists every open fact its conditional findings wait on, so one follow-up can
answer them all. The analysis then runs once more, and the report it writes
is final: it asks nothing and admits no answer. A second follow-up would mostly
answer the facts a reviewer names differently on each run
(``QA-2026-09-26-03-E22``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from analysis_service.assertions import AssertionCatalog
from analysis_service.claims import FrameworkAnalysis, FrameworkName, UnknownKey
from analysis_service.early_questions import EarlyQuestion, early_questions
from analysis_service.links import (
    LinkAnswer,
    LinkQuestion,
    apply_answers,
    check_answers,
    fold,
    link_questions,
    resumed_sources,
)
from analysis_service.questions import (
    FactAnswer,
    FactQuestion,
    answered_keys,
    answered_model,
    fact_questions,
)
from analysis_service.sources import Source
from analysis_service.system_model import SystemModel

__all__ = [
    "EARLY_RULES",
    "ROUND_SIZE",
    "AdmittedRound",
    "EarlyRule",
    "QuestionSet",
    "question_set",
]

#: How many questions of each kind one round at the pause shows.
ROUND_SIZE = 10


@dataclass(frozen=True)
class EarlyRule:
    """Which early questions of one kind a round may show.

    ``floor`` is the score a question needs, and ``limit`` how many of the
    kind one pause asks in all. The two kinds' scores are not on one scale: a
    field question's is findings expected to cite it, and a capability
    question's is units it could settle.
    """

    floor: float
    limit: int


#: The rule for each kind of early question: ``capability`` and ``field``,
#: which is every other kind (``QA-2026-09-26-03-E20``). A field question
#: under 1 is expected to change less than one finding, and the top 30 of a
#: STRIDE list hold 99% of its score at that floor. A capability question
#: under 2 settles one unit, and at ASVS level 2 that leaves 28 of 51.
EARLY_RULES: Mapping[str, EarlyRule] = {
    "capability": EarlyRule(floor=2.0, limit=30),
    "field": EarlyRule(floor=1.0, limit=30),
}


def _early_kind(question: EarlyQuestion) -> str:
    return "capability" if question.kind == "capability" else "field"


@dataclass(frozen=True)
class AdmittedRound:
    """What a resumed job carries: its sources and every round's answers."""

    sources: list[Source]
    links: list[LinkAnswer]
    facts: list[FactAnswer]
    #: Every early question the pause showed, this round's included, which
    #: the report reads to say a follow-up question was skipped before.
    shown: tuple[UnknownKey, ...]


@dataclass(frozen=True)
class QuestionSet:
    """Every question one job asks, and the model and catalog they ask about."""

    model: SystemModel
    catalog: AssertionCatalog | None
    waiting: bool
    early: tuple[EarlyQuestion, ...]
    facts: tuple[FactQuestion, ...]
    links: tuple[LinkQuestion, ...]
    #: True for a report the follow-up wrote: it asks nothing and admits no
    #: answer.
    final: bool
    #: Every early question the pause showed before this round.
    shown: tuple[UnknownKey, ...]
    #: For a waiting job, how many questions of each kind the rounds are still
    #: expected to ask, this round's included. An estimate: answers can add
    #: or take away questions.
    remaining: Mapping[str, int]
    #: For a waiting job, each early question an earlier round answered, with
    #: its answer, so a page can show it and take a new answer.
    answered_early: tuple[tuple[EarlyQuestion, FactAnswer], ...]
    answered_links: tuple[tuple[LinkQuestion, LinkAnswer], ...]
    #: For a waiting job, how many questions the limits of :data:`EARLY_RULES`
    #: hold back from every round.
    withheld: int

    @property
    def stop(self) -> str | None:
        """Why a waiting job asks nothing more, or ``None`` while it asks.

        ``budget-exhausted`` where the limits hold questions back, and
        ``nothing-left`` where no question is left to ask.
        """
        if not self.done:
            return None
        return "budget-exhausted" if self.withheld else "nothing-left"

    @property
    def asked(self) -> frozenset[UnknownKey]:
        """Every fact the questions name, which is every fact that takes an answer."""
        early = frozenset(question.key for question in self.early)
        return early | frozenset(question.key for question in self.facts)

    def to_json(self) -> dict[str, object]:
        return {
            "link_questions": [question.to_json() for question in self.links],
            "fact_questions": [question.to_json() for question in self.facts],
            "early_questions": [question.to_json() for question in self.early],
            "final": self.final,
            "early_remaining": dict(self.remaining),
            "early_withheld": self.withheld,
            "early_stop": self.stop,
            "answered_early": [
                question.to_json() | {"answer": answer.model_dump(mode="json")}
                for question, answer in self.answered_early
            ],
            "answered_links": [
                question.to_json() | {"answer": answer.model_dump(mode="json")}
                for question, answer in self.answered_links
            ],
        }

    @property
    def done(self) -> bool:
        """True where a waiting job has nothing left to ask, so it starts."""
        return self.waiting and not (self.early or self.links)

    def admit(
        self,
        *,
        sources: Sequence[Source],
        earlier_links: Sequence[LinkAnswer],
        earlier_facts: Sequence[FactAnswer],
        links: Sequence[LinkAnswer],
        facts: Sequence[FactAnswer],
        save: bool = False,
    ) -> AdmittedRound:
        """The resumed job this round's answers start, or a ``ValueError``.

        ``sources``, ``earlier_links`` and ``earlier_facts`` are what the
        answered job was given. A refusal names the submitter's own choices,
        so its message is safe to show. A link answer to a job with no catalog
        raises :class:`~analysis_service.links.NoCatalogError`. ``save`` admits
        a round a waiting job keeps, which must answer something and starts
        nothing.
        """
        if save and not self.waiting:
            raise ValueError("only a job waiting on answers saves a round")
        if save and not (links or facts):
            raise ValueError("a saved round answers at least one question")
        if self.final:
            raise ValueError(
                "this report is final: its follow-up has run, so it takes no answers"
            )
        if not (links or facts or self.waiting):
            raise ValueError(
                "no answers were sent; only a job waiting on answers can continue"
                " without them"
            )
        check_answers(
            links,
            facts,
            self.model,
            self.catalog,
            self.asked,
            earlier_facts,
            asked_links=[question.key for question in self.links],
            earlier_links=earlier_links,
        )
        return AdmittedRound(
            *resumed_sources(sources, earlier_links, links, earlier_facts, facts),
            shown=tuple(
                dict.fromkeys([*self.shown, *(question.key for question in self.early)])
            ),
        )


def question_set(
    model: SystemModel,
    catalog: AssertionCatalog | None,
    frameworks: Mapping[FrameworkName, Mapping[str, Any]],
    analyses: Sequence[FrameworkAnalysis],
    *,
    waiting: bool,
    answered: Sequence[FactAnswer],
    answered_links: Sequence[LinkAnswer],
    final: bool,
    shown: Sequence[UnknownKey],
) -> QuestionSet:
    """The questions a job asks: a waiting job's round, or its report's list.

    ``frameworks`` maps each selected framework to its options, and ranks the
    early list; ``analyses`` ranks the report's list. So a waiting job passes
    no analyses, and a finished one's frameworks go unread. ``answered`` and
    ``answered_links`` are the answers of the earlier rounds, which are not
    asked again. ``final`` marks a report the follow-up wrote, and ``shown``
    is every early question the pause showed. A waiting
    job's ``model`` and ``catalog`` are its checkpoint's, and the saved answers
    are written in here, as the resumed run writes them.
    """
    if final:
        return QuestionSet(
            model, catalog, False, (), (), (), True, tuple(shown), {}, (), (), 0
        )
    if not waiting:
        showed = frozenset(shown)
        return QuestionSet(
            model=model,
            catalog=catalog,
            waiting=False,
            early=(),
            facts=tuple(
                replace(question, asked_before=question.key in showed)
                for question in fact_questions(analyses, model, catalog, answered)
            ),
            links=link_questions(catalog, model),
            final=False,
            shown=tuple(shown),
            remaining={},
            answered_early=(),
            answered_links=(),
            withheld=0,
        )
    done = answered_keys(answered)
    held = {answer.key: answer for answer in answered}
    first = early_questions(model, frameworks, catalog)
    asked_links = link_questions(catalog, model)
    view = answered_model(model, answered)
    if catalog is not None:
        catalog = apply_answers(catalog, view, answered_links, answered)[0]
    this_round, remaining, withheld = _round(
        early_questions(view, frameworks, catalog), done, held
    )
    linked = {fold(link.principal): link for link in answered_links}
    return QuestionSet(
        model=view,
        catalog=catalog,
        waiting=True,
        early=this_round,
        facts=(),
        links=link_questions(catalog, view),
        final=False,
        shown=tuple(shown),
        remaining=remaining,
        answered_early=tuple(
            (question, held[question.key]) for question in first if question.key in held
        ),
        answered_links=tuple(
            (question, linked[question.key])
            for question in asked_links
            if question.key in linked
        ),
        withheld=withheld,
    )


def _round(
    listed: Sequence[EarlyQuestion],
    done: frozenset[UnknownKey],
    held: Mapping[UnknownKey, FactAnswer],
) -> tuple[tuple[EarlyQuestion, ...], dict[str, int], int]:
    """This round's questions, how many each kind has left, and how many the limits hold back.

    A question an earlier round answered in full is not asked again. One it
    answered in part comes first, and takes no place under the limit, which
    it already counts toward: each earlier answer counts toward its kind's
    limit, and the limit bounds only the questions not yet answered at all.
    """
    shown: list[EarlyQuestion] = []
    remaining = {}
    withheld = 0
    for kind, rule in EARLY_RULES.items():
        asked = sum(1 for key in held if (kind == "capability") == bool(key[5]))
        left = max(rule.limit - asked, 0)
        eligible = [
            question
            for question in listed
            if _early_kind(question) == kind
            and question.score >= rule.floor
            and question.key not in done
        ]
        started = [question for question in eligible if question.key in held]
        fresh = [question for question in eligible if question.key not in held]
        remaining[kind] = len(started) + min(len(fresh), left)
        withheld += max(len(fresh) - left, 0)
        this_kind = started[:ROUND_SIZE]
        this_kind += fresh[: min(ROUND_SIZE - len(this_kind), left)]
        shown.extend(this_kind)
    return tuple(shown), remaining, withheld
