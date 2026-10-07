"""The questions one round asks, and the answers it admits.

**One value answers "what does this job ask", "may this answer land" and
"what does it start".** The HTTP routes, the first-run app and the eval replay
each build an :class:`AnswerState` from their own job record. Its
:attr:`~AnswerState.questions` is the :class:`QuestionSet` they serve, and its
:meth:`~AnswerState.answer` checks the round revision, admits the answers,
checks the source limits and returns a :class:`SavedRound` or a
:class:`ResumedJob`. The caller only stores the one or starts the other. The
facts it admits are the keys of the questions it holds, so the list a caller
serves and the list it accepts cannot differ.

**A job waiting on answers asks the early list; a finished one asks the
report's list.** Both ask the link questions its catalog raises. A waiting job
may continue with no answers, and a finished one has nothing to continue.

**A waiting job asks in rounds** (ADR 0053). A round shows a fixed number of
questions of each kind, its ``per_round``, so every round asks as many
questions as the one before it, and only the last asks fewer. A question is
shown only at or above its kind's floor, and one pause asks each kind at most
its limit in all: :data:`EARLY_RULES` is the table. Where a job selects
more than one framework, the frameworks take turns, both for the places in a
round and for the order it is shown in (:func:`by_turn`). A question answered in
part is taken before any new question, outside the limit, and where the limits hold questions
back the job says so (:attr:`QuestionSet.stop`). A question that decides whether a
selected framework runs at all comes first in every round, outside the limits
(ADR 0067), and the set says what each framework's precondition reads now
(:attr:`QuestionSet.gates`). A submitter saves a round's answers, which writes
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

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import cached_property
from types import MappingProxyType
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import StringConstraints

from analysis_service.assertions import UNKNOWN, AssertionCatalog
from analysis_service.bands import UNRANKED
from analysis_service.capabilities import lineage
from analysis_service.claims import FrameworkAnalysis, FrameworkName, UnknownKey
from analysis_service.early_questions import (
    EarlyQuestion,
    early_questions,
    framework_gates,
)
from analysis_service.fact_answers import (
    MAX_FACT_ANSWERS,
    MAX_HELD_FACTS,
    FactAnswer,
    answer_facets,
    answered_keys,
    fact_label,
    fact_status,
    key_ref,
    merged_facts,
    refuse_repeated_facts,
)
from analysis_service.fact_writes import answered_model, check_fact_answers
from analysis_service.frameworks import PACKAGES, PreconditionResult
from analysis_service.links import (
    MAX_HELD_LINKS,
    MAX_LINK_ANSWERS,
    LinkAnswer,
    LinkQuestion,
    apply_answers,
    check_answers,
    fold,
    link_questions,
    merged_links,
    resumed_sources,
)
from analysis_service.open_facts import prepared_model
from analysis_service.questions import FactQuestion, fact_questions
from analysis_service.sources import LimitBreach, Source, SourceLimits
from analysis_service.system_model import SystemModel

if TYPE_CHECKING:
    # ``jobs`` imports this module for the skip vocabulary.
    from analysis_service.jobs import Checkpoint

__all__ = [
    "ANSWER_LIMITS",
    "EARLY_RULES",
    "MAX_SKIPS",
    "AdmittedRound",
    "AlreadyResumed",
    "AnswerState",
    "Answers",
    "BandApplicability",
    "EarlyRule",
    "MissingRevision",
    "PauseSummary",
    "QuestionSet",
    "ResumedJob",
    "SavedDraft",
    "SavedRound",
    "SkipKey",
    "SourcesOverLimit",
    "StaleRevision",
    "by_turn",
    "next_round",
    "passes_floor",
    "question_set",
]


@dataclass(frozen=True)
class EarlyRule:
    """Which early questions of one kind a round may show.

    ``floor`` is the score a question needs, ``limit`` how many of the kind
    one pause asks in all, and ``per_round`` how many one round asks. The two
    kinds' scores are not on one scale: a field question's is a ranking value
    read off the prior, and a capability question's is units it could settle.
    """

    floor: float
    limit: int
    per_round: int


#: The rule for each kind of early question: ``capability`` and ``field``,
#: which is every other kind (``QA-2026-09-26-03-E20``). A field question's
#: floor of 1 is where the ranking cuts: no measurement says how many findings
#: an answer under it changes. The top 30 of a STRIDE list hold 99% of the
#: ranking score at that floor, which is not a share of the report's value. A
#: capability question under 2 settles one unit, and at ASVS level 2 that
#: leaves 28 of 51. A capability question is one yes or no, so a round asks
#: 10. A field question can be a table of facets, and 5 a round asks a median
#: of 10 choices over the corpus's STRIDE models, and at most 15, in a median
#: of 6 rounds (ADR 0053).
EARLY_RULES: Mapping[str, EarlyRule] = {
    "capability": EarlyRule(floor=2.0, limit=30, per_round=10),
    "field": EarlyRule(floor=1.0, limit=30, per_round=5),
}


#: What a saved round skips for now: an early question's fact key, or a link
#: question's key, the :func:`~analysis_service.links.fold` of its principal.
#: A skip of a link places nothing and never means "none of these".
SkipKey = UnknownKey | Annotated[str, StringConstraints(min_length=1, max_length=200)]

#: The most skips one save names: every fact and link answer it could carry.
MAX_SKIPS = MAX_FACT_ANSWERS + MAX_LINK_ANSWERS

#: The answer ceilings a client is told, so a page can split its answers into
#: batches the service admits: one request's facts and links, and every
#: answer one job holds across its saves (ADR 0070).
ANSWER_LIMITS: Mapping[str, int] = MappingProxyType(
    {
        "facts": MAX_FACT_ANSWERS,
        "links": MAX_LINK_ANSWERS,
        "held_facts": MAX_HELD_FACTS,
        "held_links": MAX_HELD_LINKS,
    }
)


def _early_kind(question: EarlyQuestion) -> str:
    return "capability" if question.kind == "capability" else "field"


def passes_floor(question: EarlyQuestion, listed: Sequence[EarlyQuestion]) -> bool:
    """Whether a round may show this question: at or above its kind's floor.

    **The one reader of the floor.** A question in the highest band of its
    kind also passes, where the kind's questions span more than one band: in
    an ASVS job above level 1, a capability question that settles one level 1
    requirement is asked, where one that settles only level 2 requirements
    needs the floor (#1289). Where every question shares one band, as in a
    level 1 job, the band tells them nothing and the floor alone decides.
    """
    kind = _early_kind(question)
    if question.score >= EARLY_RULES[kind].floor:
        return True
    bands = {other.band for other in listed if _early_kind(other) == kind}
    return len(bands) > 1 and question.band == max(bands)


class AlreadyResumed(ValueError):
    """Answers to a job whose earlier answers started a job that holds it."""


@dataclass(frozen=True)
class AdmittedRound:
    """What a resumed job carries: its sources and every round's answers."""

    sources: list[Source]
    links: list[LinkAnswer]
    facts: list[FactAnswer]
    #: Every early question the pause showed, this round's included, which
    #: the report reads to say a follow-up question was skipped before.
    shown: tuple[UnknownKey, ...]
    #: Every early question and link question the submitter skipped for now
    #: and has not answered since.
    skipped: tuple[SkipKey, ...]


@dataclass(frozen=True)
class BandApplicability:
    """How many of one framework's units in one band apply, by what the pause knows.

    ``states`` counts the units in each state the framework's applicability
    rule gives them. ``unaskable`` counts the ``unknown`` units that no open
    question can settle any more: every capability that would settle one was
    answered "I don't know", or is asked by no question.
    """

    band: str
    states: Mapping[str, int]
    unaskable: int

    def to_json(self) -> dict[str, object]:
        return {
            "band": self.band,
            "states": dict(self.states),
            "unaskable": self.unaskable,
        }


@dataclass(frozen=True)
class PauseSummary:
    """What a waiting job asked, and what it leaves open (#1542 F2, F5).

    **No count here says a fact is settled that is not.** A stop of
    :attr:`QuestionSet.stop` says why no round asks more; this says what
    remains. The counts are of different things, kept apart:

    * ``introduced`` is the distinct early questions the rounds showed, this
      one's included, and ``choices`` the choices they ask: one a facet, else
      one. Neither is bounded by a limit, which counts answers.
    * ``settled``, ``partial`` and ``unknown`` count the saved early answers:
      every facet known, some facets known, or "I don't know".
    * ``skipped``, ``held_back`` and ``below_floor`` count the open questions
      no round shows: skipped for now, held back by a limit, or under a floor.
    * ``applicability`` counts each runnable framework's units by state and
      band, where its units apply by a rule over capabilities. These are
      units, not findings.
    """

    introduced: int
    choices: int
    settled: int
    partial: int
    unknown: int
    skipped: int
    held_back: int
    below_floor: int
    applicability: Mapping[FrameworkName, tuple[BandApplicability, ...]]

    def to_json(self) -> dict[str, object]:
        return {
            "introduced": self.introduced,
            "choices": self.choices,
            "settled": self.settled,
            "partial": self.partial,
            "unknown": self.unknown,
            "skipped": self.skipped,
            "held_back": self.held_back,
            "below_floor": self.below_floor,
            "applicability": {
                name: [band.to_json() for band in bands]
                for name, bands in self.applicability.items()
            },
        }


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
    #: For a waiting job, each question the limits of :data:`EARLY_RULES`
    #: hold back from every round. No round shows it, and an answer to it is
    #: still admitted, so a submitter can choose to answer it.
    held_back: tuple[EarlyQuestion, ...]
    #: For a waiting job, each early question the submitter skipped for now.
    #: A skip is not an answer: the fact stays open, no round shows it again,
    #: and an answer to it is still admitted.
    skipped: tuple[EarlyQuestion, ...]
    #: For a waiting job, each link question the submitter skipped for now,
    #: kept apart from :attr:`links` as :attr:`skipped` is from :attr:`early`.
    skipped_links: tuple[LinkQuestion, ...]
    #: The job this one's answers started, which holds it: it asks nothing
    #: and admits no answer while that job is in flight or has its report.
    resumed_by: str | None = None
    #: For a waiting job, what each selected framework's precondition reads
    #: with the saved answers in. Only a ``satisfied`` framework will run; a
    #: refuted one asks nothing, and an undecidable one asks only what decides
    #: it (:func:`~analysis_service.early_questions.framework_gates`).
    gates: Mapping[FrameworkName, PreconditionResult] = field(default_factory=dict)
    #: For a waiting job, each open question under its kind's floor
    #: (:func:`passes_floor`). No round shows it, and an answer to it is still
    #: admitted, so a submitter can choose to answer it.
    below_floor: tuple[EarlyQuestion, ...] = ()
    #: For a waiting job, what the pause asked and what it leaves open; ``None``
    #: for a report's set.
    summary: PauseSummary | None = None

    @property
    def withheld(self) -> int:
        """How many questions the limits hold back from every round."""
        return len(self.held_back)

    @property
    def stop(self) -> str | None:
        """Why a waiting job asks nothing more, or ``None`` while it asks.

        **No stop says every fact is settled.** ``budget-exhausted`` where the
        limits hold questions back, ``below-floor`` where only questions under
        a floor are left, and ``nothing-left`` where no open question is left
        to ask. In each case :attr:`summary` says what stays open.
        """
        if not self.done:
            return None
        if self.held_back:
            return "budget-exhausted"
        return "below-floor" if self.below_floor else "nothing-left"

    @property
    def asked(self) -> frozenset[UnknownKey]:
        """Every fact the questions name, which is every fact that takes an answer.

        A question the limits hold back, or one under its floor, is named
        too: no round shows it, but a submitter may choose to answer it.
        """
        early = frozenset(
            question.key
            for question in (
                *self.early,
                *self.skipped,
                *self.held_back,
                *self.below_floor,
            )
        )
        return early | frozenset(question.key for question in self.facts)

    def to_json(self) -> dict[str, object]:
        return {
            "link_questions": [question.to_json() for question in self.links],
            "fact_questions": [question.to_json() for question in self.facts],
            "early_questions": [question.to_json() for question in self.early],
            "final": self.final,
            "resumed_by": self.resumed_by,
            "early_remaining": dict(self.remaining),
            "early_withheld": self.withheld,
            "early_stop": self.stop,
            "early_held_back": [question.to_json() for question in self.held_back],
            "early_below_floor": [question.to_json() for question in self.below_floor],
            "early_summary": None if self.summary is None else self.summary.to_json(),
            "framework_gates": dict(self.gates),
            "skipped_early": [question.to_json() for question in self.skipped],
            "skipped_links": [question.to_json() for question in self.skipped_links],
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
        skips: Sequence[SkipKey] = (),
    ) -> AdmittedRound:
        """The resumed job this round's answers start, or a ``ValueError``.

        ``sources``, ``earlier_links`` and ``earlier_facts`` are what the
        answered job was given. A refusal names the submitter's own choices,
        so its message is safe to show. A link answer to a job with no catalog
        raises :class:`~analysis_service.links.NoCatalogError`. ``save`` admits
        a round a waiting job keeps, or a report's draft (ADR 0070), which must
        answer or skip something and starts nothing. ``skips`` names questions of this round the submitter
        skips for now; only a saved round skips. A report's one follow-up is
        refused where its answers add no information
        (:func:`_adds_information`), so it is not spent on a run that reads
        nothing new.
        """
        if self.resumed_by is not None:
            raise AlreadyResumed(
                f"this job's answers already started job {self.resumed_by};"
                " read that job, and answer here again only if it fails"
            )
        if save and not (links or facts or skips):
            raise ValueError("a saved round answers or skips at least one question")
        merged = merged_facts(earlier_facts, facts)
        complete: frozenset[SkipKey] = answered_keys(merged) & {
            fact.key for fact in facts
        } | {fold(link.principal) for link in links}
        presented = self.presented(merged)
        self._check_skips(skips, complete, save, presented)
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
            asked_links=[
                question.key for question in (*self.links, *self.skipped_links)
            ],
            earlier_links=earlier_links,
            reopen=self.waiting,
        )
        if (
            not self.waiting
            and not save
            and not _adds_information(earlier_links, earlier_facts, links, facts)
        ):
            raise ValueError(
                "these answers add nothing the analysis can use: each one is"
                ' "I don\'t know" or repeats an earlier answer. The follow-up'
                " has not run, and it is still available"
            )
        return AdmittedRound(
            *resumed_sources(sources, earlier_links, links, earlier_facts, facts),
            shown=tuple(dict.fromkeys([*self.shown, *presented])),
            skipped=tuple(
                dict.fromkeys(
                    key
                    for key in [
                        *(question.key for question in self.skipped),
                        *(question.key for question in self.skipped_links),
                        *skips,
                    ]
                    if key not in complete
                )
            ),
        )

    def presented(self, answers: Sequence[FactAnswer]) -> tuple[UnknownKey, ...]:
        """This round's early questions a page shows once ``answers`` are given.

        **The one reader of which part is hidden** (#1542 F4). A part whose
        parent this round also asks shows only once the parent is answered
        "yes", and a part of a hidden part stays hidden, as the page hides
        them. A hidden part was not presented: no history records it as shown,
        and a skip of it is refused. ``answers`` is every answer the job holds
        with this submission's in.
        """
        said = {answer.key: answer.value for answer in answers}
        asked = {question.key for question in self.early}
        hidden: set[UnknownKey] = set()
        # A parent comes before its parts in a round (by_turn keeps each
        # framework's order), so one pass reaches a part of a part.
        for question in self.early:
            parent = question.parent
            if parent in asked and (parent in hidden or said.get(parent) != "yes"):
                hidden.add(question.key)
        return tuple(
            question.key for question in self.early if question.key not in hidden
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
                    "only an answer the report read can be corrected:"
                    f' "{fact_label(fact.key, self.model)}"'
                )
        check_fact_answers(
            facts, self.model, self.catalog, list(current.values()), reopen=True
        )
        after = {f.key: f for f in merged_facts(list(current.values()), facts)}
        if all(after[f.key].value == current[f.key].value for f in facts):
            raise ValueError("these corrections change no answer")
        return merged_facts(corrections, facts)

    def _check_skips(
        self,
        skips: Sequence[SkipKey],
        complete: frozenset[SkipKey],
        save: bool,
        presented: Sequence[UnknownKey],
    ) -> None:
        """Refuse a skip outside a saved round, of a question it does not show,
        of a part hidden under its parent's answer, or of a question the same
        submission answers in full.

        A question with facets answered in part may be skipped with it: the
        facets given are kept, and the rest are set aside for now, so a
        question the owner can answer only in part does not come back first
        in every round.
        """
        if not skips:
            return
        if not save:
            raise ValueError("only a saved round skips questions")
        shown: dict[SkipKey, str] = {
            question.key: question.principal for question in self.links
        }
        shown |= {
            question.key: fact_label(question.key, self.model)
            for question in self.early
        }
        for key in skips:
            if key not in shown:
                named = key if isinstance(key, str) else fact_label(key, self.model)
                raise ValueError(
                    f'only a question this round shows is skipped: "{named}"'
                )
            if not isinstance(key, str) and key not in presented:
                raise ValueError(
                    f'"{shown[key]}" is hidden until its parent is answered "yes",'
                    " so it is not skipped; it is asked once it shows"
                )
            if key in complete:
                raise ValueError(
                    f'a question is answered or skipped, not both: "{shown[key]}"'
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
    skipped: Sequence[SkipKey] = (),
    resumed_by: str | None = None,
) -> QuestionSet:
    """The questions a job asks: a waiting job's round, or its report's list.

    ``frameworks`` maps each selected framework to its options, and ranks the
    early list; ``analyses`` ranks the report's list. So a waiting job passes
    no analyses, and a finished one's frameworks go unread. ``answered`` and
    ``answered_links`` are the answers of the earlier rounds, which are not
    asked again. ``final`` marks a report the follow-up wrote, and ``shown``
    is every early question the pause presented. ``skipped`` is every early
    question and link question the pause's submitter skipped for now; a
    report's list reads both to say what became of each question before the
    analysis (:func:`~analysis_service.fact_answers.fact_status`). A waiting
    job's ``model`` and ``catalog`` are its checkpoint's, and the saved answers
    are written in here, as the resumed run writes them. ``resumed_by`` is the
    job this one's answers started and that holds it, which a store reads
    (:meth:`~analysis_service.jobs.JobStore.resumed_by`); such a job asks
    nothing.
    """
    if resumed_by is not None:
        return QuestionSet(
            model=model,
            catalog=catalog,
            waiting=waiting,
            early=(),
            facts=(),
            links=(),
            final=final,
            shown=tuple(shown),
            remaining={},
            answered_early=(),
            answered_links=(),
            held_back=(),
            skipped=(),
            skipped_links=(),
            resumed_by=resumed_by,
        )
    if final:
        return QuestionSet(
            model=model,
            catalog=catalog,
            waiting=False,
            early=(),
            facts=(),
            links=(),
            final=True,
            shown=tuple(shown),
            remaining={},
            answered_early=(),
            answered_links=(),
            held_back=(),
            skipped=(),
            skipped_links=(),
        )
    if not waiting:
        said = {answer.key: answer for answer in answered}
        showed, set_aside = frozenset(shown), frozenset(skipped)
        return QuestionSet(
            model=model,
            catalog=catalog,
            waiting=False,
            early=(),
            facts=tuple(
                replace(
                    question,
                    history=fact_status(question.key, said, showed, set_aside),
                )
                for question in fact_questions(analyses, model, catalog, answered)
            ),
            links=link_questions(catalog, model),
            final=False,
            shown=tuple(shown),
            remaining={},
            answered_early=(),
            answered_links=(),
            held_back=(),
            skipped=(),
            skipped_links=(),
        )
    done = answered_keys(answered)
    first = early_questions(model, frameworks, catalog)
    asked_links = link_questions(catalog, model)
    view = answered_model(model, answered)
    if catalog is not None:
        catalog = apply_answers(catalog, view, answered_links, answered)[0]
    listed = early_questions(view, frameworks, catalog)
    # An answer to a gate question counts toward no kind's limit, so the key
    # stays exempt once its answer has decided the gate and it is no longer
    # listed as one.
    given = {answer.key: answer for answer in answered}
    gated = {question.key for question in (*first, *listed) if question.gates}
    held = {answer.key: answer for answer in answered if answer.key not in gated}
    aside: frozenset[SkipKey] = frozenset(skipped)
    gates = framework_gates(view, frameworks, catalog)
    this_round, remaining, held_back = next_round(listed, aside | done, held)
    below_floor = tuple(
        question
        for question in listed
        if not question.gates
        and question.key not in aside | done
        and not passes_floor(question, listed)
    )
    linked = {fold(link.principal): link for link in answered_links}
    open_links = link_questions(catalog, view)
    return QuestionSet(
        model=view,
        catalog=catalog,
        waiting=True,
        early=this_round,
        facts=(),
        links=tuple(question for question in open_links if question.key not in aside),
        final=False,
        shown=tuple(shown),
        remaining=remaining,
        answered_early=tuple(
            (question, given[question.key])
            for question in first
            if question.key in given
        ),
        answered_links=tuple(
            (question, linked[question.key])
            for question in asked_links
            if question.key in linked
        ),
        held_back=held_back,
        skipped=tuple(
            question
            for question in listed
            if question.key in aside and question.key not in done
        ),
        skipped_links=tuple(
            question for question in open_links if question.key in aside
        ),
        gates=gates,
        below_floor=below_floor,
        summary=_summary(
            prepared_model(view, catalog),
            frameworks,
            gates,
            listed,
            done,
            answered,
            [*shown, *(question.key for question in this_round)],
            skipped=len(aside),
            held_back=len(held_back),
            below_floor=len(below_floor),
        ),
    )


def _summary(
    prepared: SystemModel,
    frameworks: Mapping[FrameworkName, Mapping[str, Any]],
    gates: Mapping[FrameworkName, PreconditionResult],
    listed: Sequence[EarlyQuestion],
    done: frozenset[UnknownKey],
    answered: Sequence[FactAnswer],
    introduced: Sequence[UnknownKey],
    *,
    skipped: int,
    held_back: int,
    below_floor: int,
) -> PauseSummary:
    """What a waiting job asked and what it leaves open (:class:`PauseSummary`).

    ``prepared`` is the model the lanes read, with the saved answers in.
    ``done`` is every fact a saved answer names, "I don't know" included, and
    ``introduced`` every early question the rounds showed, this one's
    included.
    """
    askable = {
        key_ref(question.key).capability
        for question in listed
        if question.kind == "capability" and question.key not in done
    }
    applicability: dict[FrameworkName, tuple[BandApplicability, ...]] = {}
    for name in sorted(frameworks):
        if gates.get(name) != "satisfied":
            continue
        record = PACKAGES[name].record
        counts: dict[tuple[int, str], Counter[str]] = {}
        for entry in record.applicability(prepared, frameworks[name]):
            band = record.unit_band(entry.unit) or UNRANKED
            held = counts.setdefault((-band.order, band.label), Counter())
            held[entry.state] += 1
            reachable = {
                key for missing in entry.missing for key in (missing, *lineage(missing))
            }
            if entry.state == "unknown" and not reachable & askable:
                held["unaskable"] += 1
        if counts:
            applicability[name] = tuple(
                BandApplicability(
                    band=label,
                    states={
                        state: count
                        for state, count in held.items()
                        if state != "unaskable"
                    },
                    unaskable=held["unaskable"],
                )
                for (_, label), held in sorted(counts.items())
            )
    shown = tuple(dict.fromkeys(introduced))
    content = [_known_content(answer) for answer in answered]
    settles = [answer.settles for answer in answered]
    return PauseSummary(
        introduced=len(shown),
        choices=sum(len(answer_facets(key)) or 1 for key in shown),
        settled=sum(settles),
        partial=sum(
            1
            for known, full in zip(content, settles, strict=True)
            if known and not full
        ),
        unknown=sum(1 for known in content if not known),
        skipped=skipped,
        held_back=held_back,
        below_floor=below_floor,
        applicability=applicability,
    )


def next_round(
    listed: Sequence[EarlyQuestion],
    done: frozenset[SkipKey],
    held: Mapping[UnknownKey, FactAnswer],
) -> tuple[tuple[EarlyQuestion, ...], dict[str, int], tuple[EarlyQuestion, ...]]:
    """This round's questions, how many each kind has left, and the questions the limits hold back.

    A question an earlier round answered in full is not asked again. One it
    answered in part is taken first, and takes no place under the limit, which
    it already counts toward: each earlier answer counts toward its kind's
    limit, and the limit bounds only the questions not yet answered at all.

    A round takes the first ``per_round`` questions of each kind, so the
    number of questions a round opens with never grows from one round to the
    next (:func:`_take`). Each kind's questions are taken with the selected
    frameworks in turn, so each framework's best questions reach the round,
    and the whole round is shown in that order too (:func:`by_turn`). So a
    question answered in part is shown where its framework's turn puts it,
    which is not always first.

    **A question that decides a framework's precondition is taken first**, every
    one still open, outside the limits and the floor (ADR 0067). Its count is
    the ``gate`` entry of the remaining counts.
    """
    gates = [
        question for question in listed if question.gates and question.key not in done
    ]
    shown: list[EarlyQuestion] = []
    remaining = {"gate": len(gates)}
    held_back: list[EarlyQuestion] = []
    for kind, rule in EARLY_RULES.items():
        asked = sum(
            1 for key in held if (kind == "capability") == bool(key_ref(key).capability)
        )
        left = max(rule.limit - asked, 0)
        eligible = [
            question
            for question in listed
            if _early_kind(question) == kind
            and not question.gates
            and passes_floor(question, listed)
            and question.key not in done
        ]
        started = [question for question in eligible if question.key in held]
        fresh = [question for question in eligible if question.key not in held]
        remaining[kind] = len(started) + min(len(fresh), left)
        turns = by_turn(fresh)
        held_back += turns[left:]
        shown += _take([*started, *turns[:left]], rule.per_round)
    return (*gates, *by_turn(shown)), remaining, tuple(held_back)


def by_turn(questions: Sequence[EarlyQuestion]) -> list[EarlyQuestion]:
    """The questions with the selected frameworks taken in turn.

    **The one reader of whose turn it is.** The next question is the first
    one left that serves the framework charged the fewest choices so far, the
    first by name on a tie, and every framework it serves is charged its
    choices (:attr:`~analysis_service.early_questions.EarlyQuestion.frameworks`).
    So a question two frameworks share costs each of them, and asks once.
    Within one framework the list's order holds, so a parent still comes
    before its parts: a part serves no framework its parent does not.
    The list ranks a field question that two frameworks share by the sum of
    their scores, so one framework's prior scale can move that question in
    another framework's turn (ADR 0053, ``QA-2026-10-06-01-E1``).
    With one framework selected the order is the list's order.
    """
    charged: Counter[str] = Counter()
    left = list(questions)
    order: list[EarlyQuestion] = []
    while left:
        served = {name for question in left for name in question.frameworks}
        if not served:
            return order + left
        name = min(served, key=lambda each: (charged[each], each))
        question = next(question for question in left if name in question.frameworks)
        left.remove(question)
        order.append(question)
        for holder in question.frameworks:
            charged[holder] += question.decisions
    return order


def _take(candidates: Sequence[EarlyQuestion], per_round: int) -> list[EarlyQuestion]:
    """The first ``per_round`` questions, with each one's parts beside them.

    **A part waits on its parent, so it takes no place in the round.** A
    question whose parent the round also asks is hidden until the parent is
    answered "yes" (``parent`` on :class:`EarlyQuestion`). It joins the round
    beside its parent, outside the count, so a round opens with as many
    questions as it counts. A part whose parent an earlier round answered is
    an ordinary question and takes a place.
    """
    taken: list[EarlyQuestion] = []
    keys: set[UnknownKey] = set()
    counted = 0
    # A parent comes before its parts in the list (capability_questions), so
    # one pass in order reaches a part of a part too.
    for question in candidates:
        if question.parent in keys:
            taken.append(question)
        elif counted < per_round:
            taken.append(question)
            counted += 1
        else:
            continue
        keys.add(question.key)
    return taken


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


class StaleRevision(Exception):
    """Answers to a waiting job that name an earlier round revision than its own."""


class MissingRevision(ValueError):
    """Answers to a waiting job that name no round revision."""


class SourcesOverLimit(Exception):
    """Answers whose composed sources break the deployment's source limits."""

    def __init__(self, breach: LimitBreach) -> None:
        super().__init__(breach.message)
        self.breach = breach


@dataclass(frozen=True)
class Answers:
    """One submission of answers to a job's questions.

    ``save`` keeps a waiting job's round, or a report's draft, and starts
    nothing. ``skips`` names questions of this round the submitter skips for
    now; only a waiting job's saved round skips. ``revision`` is the revision
    the questions were read with, which a waiting job and every save require.
    """

    links: Sequence[LinkAnswer] = ()
    facts: Sequence[FactAnswer] = ()
    save: bool = False
    skips: Sequence[SkipKey] = ()
    revision: int | None = None


@dataclass(frozen=True)
class SavedRound:
    """What a waiting job keeps after a saved round. No model runs.

    ``revision`` is the round revision the save read. The store writes the
    round only while the job still holds that revision, so of two saves that
    read one revision only the first lands.
    """

    links: list[LinkAnswer]
    facts: list[FactAnswer]
    shown: tuple[UnknownKey, ...]
    skipped: tuple[SkipKey, ...]
    revision: int


@dataclass(frozen=True)
class SavedDraft:
    """What a report keeps after its follow-up saves a batch. No model runs.

    ``links`` and ``facts`` are every draft answer so far, this batch's over
    the earlier ones. The report and its follow-up are unchanged: the run
    that answers them composes the draft with what it is sent (ADR 0070).
    ``revision`` is the revision the save read, which the store checks as it
    does a saved round's.
    """

    links: list[LinkAnswer]
    facts: list[FactAnswer]
    revision: int


@dataclass(frozen=True)
class ResumedJob:
    """The **Resumed Job** a submission starts: what its run reads, and where it starts.

    The run starts at ``prepare`` from ``checkpoint``, so no extraction and no
    assertion pass runs again (#1252). ``follow_up`` is True where the answers
    are to a finished report, so the new job's report is final (ADR 0054).
    """

    sources: list[Source]
    links: list[LinkAnswer]
    facts: list[FactAnswer]
    shown: tuple[UnknownKey, ...]
    #: Every question the pause's submitter skipped and did not answer since,
    #: which the report reads to tell a skip from a blank.
    skipped: tuple[SkipKey, ...]
    checkpoint: Checkpoint
    follow_up: bool


@dataclass(frozen=True)
class AnswerState:
    """The **Answer State** of one job: everything its questions and answers read.

    Each place that serves a job's questions builds one from its own job
    record, and asks it for the questions (:attr:`questions`), for the
    outcome of a submission (:meth:`answer`), or for a final report's
    corrections (:meth:`correct`). It writes nothing: the caller stores a
    :class:`SavedRound` or starts a :class:`ResumedJob`.

    ``checkpoint`` is the model and catalog the questions ask about: a waiting
    job's, or a finished report's. ``analyses`` are the report's findings, and
    empty for a waiting job. ``waiting`` marks a job that waits on answers
    before its analysis, and ``final`` a report its follow-up wrote.
    ``sources``, ``links`` and ``facts`` are what the job was given, a
    waiting job's saved rounds included. ``shown`` is every early question
    the pause showed, ``skipped`` every question its submitter skipped for
    now, and ``corrections`` what a final report's owner corrected since.
    ``revision`` is how many rounds a waiting job saved. ``resumed_by`` names
    the job these answers started, where it holds this one.
    """

    checkpoint: Checkpoint
    frameworks: Mapping[FrameworkName, Mapping[str, Any]]
    analyses: Sequence[FrameworkAnalysis]
    waiting: bool
    final: bool
    sources: Sequence[Source]
    links: Sequence[LinkAnswer]
    facts: Sequence[FactAnswer]
    shown: Sequence[UnknownKey]
    skipped: Sequence[SkipKey]
    corrections: Sequence[FactAnswer]
    revision: int
    resumed_by: str | None
    #: A report's draft answers: what its follow-up saved in batches and has
    #: not yet run. Empty for a waiting job, whose saved rounds are ``facts``.
    draft_links: Sequence[LinkAnswer] = ()
    draft_facts: Sequence[FactAnswer] = ()

    @cached_property
    def questions(self) -> QuestionSet:
        """Every question this job asks."""
        assertions = self.checkpoint.assertions
        return question_set(
            self.checkpoint.system_model,
            None if assertions is None else assertions.catalog,
            self.frameworks,
            self.analyses,
            waiting=self.waiting,
            answered=self.facts,
            answered_links=self.links,
            final=self.final,
            shown=self.shown,
            skipped=self.skipped,
            resumed_by=self.resumed_by,
        )

    def answer(
        self, answers: Answers, *, limits: SourceLimits | None
    ) -> SavedRound | SavedDraft | ResumedJob:
        """The round a waiting job keeps, a report's draft, or the Resumed Job the answers start.

        **A report's follow-up saves in batches** (ADR 0070). A save to a
        report keeps a draft and runs nothing; the run composes every saved
        draft with what it is sent, so a follow-up can answer more than one
        request carries, and earlier-answer edits count in the batch that
        sends them. A save names the revision it read, as a waiting job's
        every answer does; a run that names one is checked against it.

        A refusal raises: :class:`MissingRevision` or :class:`StaleRevision`
        for the revision, :class:`AlreadyResumed` where these answers already
        started a job that holds this one,
        :class:`~analysis_service.links.NoCatalogError` for a link answer to a
        job with no catalog, :class:`SourcesOverLimit` where the composed
        sources break ``limits``, and a ``ValueError`` that names the
        submitter's own choices otherwise. ``limits`` is ``None`` only where
        the job holds no sources to bound, as an eval replay of rounds.
        """
        if (self.waiting or answers.save) and answers.revision is None:
            raise MissingRevision("send the revision the questions were read with")
        if answers.revision is not None and answers.revision != self.revision:
            raise StaleRevision(
                "the saved answers changed since these questions were read"
            )
        links, facts = answers.links, answers.facts
        if not self.waiting:
            if answers.save and not (links or facts):
                raise ValueError("a saved draft answers at least one question")
            links = merged_links(self.draft_links, links)
            facts = merged_facts(self.draft_facts, facts)
        admitted = self.questions.admit(
            sources=self.sources,
            earlier_links=self.links,
            earlier_facts=self.facts,
            links=links,
            facts=facts,
            save=answers.save,
            skips=answers.skips,
        )
        if len(admitted.facts) > MAX_HELD_FACTS or len(admitted.links) > MAX_HELD_LINKS:
            raise ValueError(
                f"a job holds at most {MAX_HELD_FACTS} fact answers and"
                f" {MAX_HELD_LINKS} link answers in all; these answers would"
                f" make {len(admitted.facts)} and {len(admitted.links)}"
            )
        # Checked for a save too: a saved round or draft that no start could
        # run would hold the job until the submitter shortens an answer.
        breach = None if limits is None else limits.breach(admitted.sources)
        if breach is not None:
            raise SourcesOverLimit(breach)
        if answers.save and not self.waiting:
            return SavedDraft(
                links=list(links), facts=list(facts), revision=self.revision
            )
        if answers.save:
            return SavedRound(
                links=admitted.links,
                facts=admitted.facts,
                shown=admitted.shown,
                skipped=admitted.skipped,
                revision=self.revision,
            )
        return ResumedJob(
            sources=admitted.sources,
            links=admitted.links,
            facts=admitted.facts,
            shown=admitted.shown,
            skipped=admitted.skipped,
            checkpoint=self.checkpoint,
            follow_up=not self.waiting,
        )

    def correct(self, facts: Sequence[FactAnswer]) -> list[FactAnswer]:
        """Every correction a final report carries once ``facts`` land, or a ``ValueError``."""
        return self.questions.correct(
            earlier_facts=self.facts, corrections=self.corrections, facts=facts
        )
