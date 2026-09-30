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

**A waiting job asks in rounds** (ADR 0053). A round shows capability
questions and questions about the model's elements up to
:data:`ROUND_DECISIONS` choices of each kind: a question with facets costs
one choice a facet it still leaves open, any other question one. A question is shown only at or above its kind's floor, and
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

from analysis_service.assertions import UNKNOWN, AssertionCatalog
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
    check_fact_answers,
    fact_questions,
    merged_facts,
    refuse_repeated_facts,
)
from analysis_service.sources import Source
from analysis_service.system_model import SystemModel

__all__ = [
    "EARLY_RULES",
    "ROUND_DECISIONS",
    "AdmittedRound",
    "EarlyRule",
    "QuestionSet",
    "question_set",
]

#: How many choices of each kind one round at the pause asks of a person.
ROUND_DECISIONS = 10


@dataclass(frozen=True)
class EarlyRule:
    """Which early questions of one kind a round may show.

    ``floor`` is the score a question needs, and ``limit`` how many of the
    kind one pause asks in all. The two kinds' scores are not on one scale: a
    field question's is a ranking value read off the prior, and a capability
    question's is units it could settle.
    """

    floor: float
    limit: int


#: The rule for each kind of early question: ``capability`` and ``field``,
#: which is every other kind (``QA-2026-09-26-03-E20``). A field question's
#: floor of 1 is where the ranking cuts: no measurement says how many findings
#: an answer under it changes. The top 30 of a STRIDE list hold 99% of the
#: ranking score at that floor, which is not a share of the report's value. A
#: capability question under 2 settles one unit, and at ASVS level 2 that
#: leaves 28 of 51.
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
    #: Every early question the submitter skipped for now and has not
    #: answered since.
    skipped: tuple[UnknownKey, ...]


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
    #: For a waiting job, each early question the submitter skipped for now.
    #: A skip is not an answer: the fact stays open, no round shows it again,
    #: and an answer to it is still admitted.
    skipped: tuple[EarlyQuestion, ...]

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
        early = frozenset(question.key for question in (*self.early, *self.skipped))
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
            "skipped_early": [question.to_json() for question in self.skipped],
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
        """True where a waiting job has nothing left to ask."""
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
        skips: Sequence[UnknownKey] = (),
    ) -> AdmittedRound:
        """The resumed job this round's answers start, or a ``ValueError``.

        ``sources``, ``earlier_links`` and ``earlier_facts`` are what the
        answered job was given. A refusal names the submitter's own choices,
        so its message is safe to show. A link answer to a job with no catalog
        raises :class:`~analysis_service.links.NoCatalogError`. ``save`` admits
        a round a waiting job keeps, which must answer or skip something and
        starts nothing. ``skips`` names questions of this round the submitter
        skips for now; only a saved round skips. A report's one follow-up is
        refused where its answers add no information
        (:func:`_adds_information`), so it is not spent on a run that reads
        nothing new.
        """
        if save and not self.waiting:
            raise ValueError("only a job waiting on answers saves a round")
        if save and not (links or facts or skips):
            raise ValueError("a saved round answers or skips at least one question")
        self._check_skips(skips, facts, save)
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
            reopen=self.waiting,
        )
        if not self.waiting and not _adds_information(
            earlier_links, earlier_facts, links, facts
        ):
            raise ValueError(
                "these answers add nothing the analysis can use: each one is"
                ' "I don\'t know" or repeats an earlier answer. The follow-up'
                " has not run, and it is still available"
            )
        answered = {fact.key for fact in facts}
        return AdmittedRound(
            *resumed_sources(sources, earlier_links, links, earlier_facts, facts),
            shown=tuple(
                dict.fromkeys([*self.shown, *(question.key for question in self.early)])
            ),
            skipped=tuple(
                dict.fromkeys(
                    key
                    for key in [*(question.key for question in self.skipped), *skips]
                    if key not in answered
                )
            ),
        )

    def correct(
        self,
        *,
        earlier_facts: Sequence[FactAnswer],
        corrections: Sequence[FactAnswer],
        facts: Sequence[FactAnswer],
    ) -> list[FactAnswer]:
        """Every correction a final report carries once ``facts`` land, or a ``ValueError``.

        A final report takes corrections, never answers (ADR 0054): a change
        to a fact its run's answers covered, "I don't know" included. No model
        runs; the corrections are kept beside the report, which says which
        findings rest on them. ``earlier_facts`` is what the run read and
        ``corrections`` what was corrected before.
        """
        if not self.final:
            raise ValueError(
                "only a final report takes corrections; answer its follow-up"
                " to change an answer"
            )
        if not facts:
            raise ValueError("a correction changes at least one answer")
        refuse_repeated_facts(facts)
        current = {f.key: f for f in merged_facts(earlier_facts, corrections)}
        for fact in facts:
            if fact.key not in current:
                raise ValueError(
                    f"only an answer the report read can be corrected: {fact.key!r}"
                )
        check_fact_answers(
            facts, self.model, self.catalog, list(current.values()), reopen=True
        )
        after = {f.key: f for f in merged_facts(list(current.values()), facts)}
        if all(after[f.key].value == current[f.key].value for f in facts):
            raise ValueError("these corrections change no answer")
        return merged_facts(corrections, facts)

    def _check_skips(
        self, skips: Sequence[UnknownKey], facts: Sequence[FactAnswer], save: bool
    ) -> None:
        """Refuse a skip outside a saved round, of a question it does not show,
        or of a question the same submission answers."""
        if not skips:
            return
        if not save:
            raise ValueError("only a saved round skips questions")
        shown = {question.key for question in self.early}
        answered = {fact.key for fact in facts}
        for key in skips:
            if key not in shown:
                raise ValueError(
                    f"only a question this round shows is skipped: {key!r}"
                )
            if key in answered:
                raise ValueError(
                    f"a question is answered or skipped, not both: {key!r}"
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
    skipped: Sequence[UnknownKey] = (),
) -> QuestionSet:
    """The questions a job asks: a waiting job's round, or its report's list.

    ``frameworks`` maps each selected framework to its options, and ranks the
    early list; ``analyses`` ranks the report's list. So a waiting job passes
    no analyses, and a finished one's frameworks go unread. ``answered`` and
    ``answered_links`` are the answers of the earlier rounds, which are not
    asked again. ``final`` marks a report the follow-up wrote, and ``shown``
    is every early question the pause showed. ``skipped`` is every early
    question a waiting job's submitter skipped for now. A waiting
    job's ``model`` and ``catalog`` are its checkpoint's, and the saved answers
    are written in here, as the resumed run writes them.
    """
    if final:
        return QuestionSet(
            model, catalog, False, (), (), (), True, tuple(shown), {}, (), (), 0, ()
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
            skipped=(),
        )
    done = answered_keys(answered)
    held = {answer.key: answer for answer in answered}
    first = early_questions(model, frameworks, catalog)
    asked_links = link_questions(catalog, model)
    view = answered_model(model, answered)
    if catalog is not None:
        catalog = apply_answers(catalog, view, answered_links, answered)[0]
    listed = early_questions(view, frameworks, catalog)
    aside = frozenset(skipped)
    this_round, remaining, withheld = _round(listed, done | aside, held)
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
        skipped=tuple(
            question
            for question in listed
            if question.key in aside and question.key not in done
        ),
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

    A round takes questions in order until the next would pass
    :data:`ROUND_DECISIONS` for its kind, and always takes the first.
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
        budget = ROUND_DECISIONS
        for question in [*started, *fresh[:left]]:
            cost = _open_decisions(question, held.get(question.key))
            if cost > budget and budget < ROUND_DECISIONS:
                break
            shown.append(question)
            budget -= cost
    return tuple(shown), remaining, withheld


def _adds_information(
    earlier_links: Sequence[LinkAnswer],
    earlier_facts: Sequence[FactAnswer],
    links: Sequence[LinkAnswer],
    facts: Sequence[FactAnswer],
) -> bool:
    """True where the answers change what the analysis reads.

    A link answer does where it places a principal anew or elsewhere. A fact
    answer does where its known content changes: "I don't know" is none, and
    of a facet answer only the facets answered otherwise count.
    """
    placed = {fold(link.principal): link.element for link in earlier_links}
    if any(placed.get(fold(link.principal)) != link.element for link in links):
        return True
    before = {fact.key: fact for fact in earlier_facts}
    after = {fact.key: fact for fact in merged_facts(earlier_facts, facts)}
    return any(
        _known_content(after[fact.key]) != _known_content(before.get(fact.key))
        for fact in facts
    )


def _known_content(answer: FactAnswer | None) -> frozenset[tuple[str, str]]:
    """What an answer states, without its "I don't know" parts."""
    if answer is None:
        return frozenset()
    if answer.facets is not None:
        return frozenset(
            (facet, value) for facet, value in answer.facets.items() if value != UNKNOWN
        )
    return frozenset({("", answer.value)}) if answer.known else frozenset()


def _open_decisions(question: EarlyQuestion, answer: FactAnswer | None) -> int:
    """The choices a question still asks: its facets with no answer, else all."""
    if answer is None or not answer.facets:
        return question.decisions
    return sum(1 for facet in question.facets if facet.id not in answer.facets)
