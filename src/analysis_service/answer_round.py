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
back the job says so (:attr:`QuestionSet.stop`). A submitter saves a round's answers, which writes
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
from dataclasses import dataclass, replace
from functools import cached_property
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import StringConstraints

from analysis_service.assertions import UNKNOWN, AssertionCatalog
from analysis_service.claims import FrameworkAnalysis, FrameworkName, UnknownKey
from analysis_service.early_questions import EarlyQuestion, early_questions
from analysis_service.fact_answers import (
    MAX_FACT_ANSWERS,
    FactAnswer,
    answered_keys,
    fact_label,
    key_ref,
    merged_facts,
    refuse_repeated_facts,
)
from analysis_service.fact_writes import answered_model, check_fact_answers
from analysis_service.links import (
    MAX_LINK_ANSWERS,
    LinkAnswer,
    LinkQuestion,
    apply_answers,
    check_answers,
    fold,
    link_questions,
    resumed_sources,
)
from analysis_service.questions import FactQuestion, fact_questions
from analysis_service.sources import LimitBreach, Source, SourceLimits
from analysis_service.system_model import SystemModel

if TYPE_CHECKING:
    # ``jobs`` imports this module for the skip vocabulary.
    from analysis_service.jobs import Checkpoint

__all__ = [
    "EARLY_RULES",
    "MAX_SKIPS",
    "AdmittedRound",
    "AlreadyResumed",
    "AnswerState",
    "Answers",
    "EarlyRule",
    "MissingRevision",
    "QuestionSet",
    "ResumedJob",
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
    #: For a waiting job, each link question the submitter skipped for now,
    #: kept apart from :attr:`links` as :attr:`skipped` is from :attr:`early`.
    skipped_links: tuple[LinkQuestion, ...]
    #: The job this one's answers started, which holds it: it asks nothing
    #: and admits no answer while that job is in flight or has its report.
    resumed_by: str | None = None

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
            "resumed_by": self.resumed_by,
            "early_remaining": dict(self.remaining),
            "early_withheld": self.withheld,
            "early_stop": self.stop,
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
        a round a waiting job keeps, which must answer or skip something and
        starts nothing. ``skips`` names questions of this round the submitter
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
        if save and not self.waiting:
            raise ValueError("only a job waiting on answers saves a round")
        if save and not (links or facts or skips):
            raise ValueError("a saved round answers or skips at least one question")
        complete: frozenset[SkipKey] = answered_keys(
            merged_facts(earlier_facts, facts)
        ) & {fact.key for fact in facts} | {fold(link.principal) for link in links}
        self._check_skips(skips, complete, save)
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
        if not self.waiting and not _adds_information(
            earlier_links, earlier_facts, links, facts
        ):
            raise ValueError(
                "these answers add nothing the analysis can use: each one is"
                ' "I don\'t know" or repeats an earlier answer. The follow-up'
                " has not run, and it is still available"
            )
        return AdmittedRound(
            *resumed_sources(sources, earlier_links, links, earlier_facts, facts),
            shown=tuple(
                dict.fromkeys([*self.shown, *(question.key for question in self.early)])
            ),
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
        self, skips: Sequence[SkipKey], complete: frozenset[SkipKey], save: bool
    ) -> None:
        """Refuse a skip outside a saved round, of a question it does not show,
        or of a question the same submission answers in full.

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
    is every early question the pause showed. ``skipped`` is every early
    question and link question a waiting job's submitter skipped for now. A waiting
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
            withheld=0,
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
            withheld=0,
            skipped=(),
            skipped_links=(),
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
            skipped_links=(),
        )
    done = answered_keys(answered)
    held = {answer.key: answer for answer in answered}
    first = early_questions(model, frameworks, catalog)
    asked_links = link_questions(catalog, model)
    view = answered_model(model, answered)
    if catalog is not None:
        catalog = apply_answers(catalog, view, answered_links, answered)[0]
    listed = early_questions(view, frameworks, catalog)
    aside: frozenset[SkipKey] = frozenset(skipped)
    this_round, remaining, withheld = next_round(listed, aside | done, held)
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
        skipped_links=tuple(
            question for question in open_links if question.key in aside
        ),
    )


def next_round(
    listed: Sequence[EarlyQuestion],
    done: frozenset[SkipKey],
    held: Mapping[UnknownKey, FactAnswer],
) -> tuple[tuple[EarlyQuestion, ...], dict[str, int], int]:
    """This round's questions, how many each kind has left, and how many the limits hold back.

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
    """
    shown: list[EarlyQuestion] = []
    remaining = {}
    withheld = 0
    for kind, rule in EARLY_RULES.items():
        asked = sum(
            1 for key in held if (kind == "capability") == bool(key_ref(key).capability)
        )
        left = max(rule.limit - asked, 0)
        eligible = [
            question
            for question in listed
            if _early_kind(question) == kind
            and passes_floor(question, listed)
            and question.key not in done
        ]
        started = [question for question in eligible if question.key in held]
        fresh = [question for question in eligible if question.key not in held]
        remaining[kind] = len(started) + min(len(fresh), left)
        withheld += max(len(fresh) - left, 0)
        shown += _take([*started, *by_turn(fresh)[:left]], rule.per_round)
    return tuple(by_turn(shown)), remaining, withheld


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

    ``save`` keeps a waiting job's round and starts nothing. ``skips`` names
    questions of this round the submitter skips for now; only a saved round
    skips. ``revision`` is the round revision the questions were read with,
    which a waiting job requires.
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
    ) -> SavedRound | ResumedJob:
        """The round a waiting job keeps, or the Resumed Job the answers start.

        A refusal raises: :class:`MissingRevision` or :class:`StaleRevision`
        for a waiting job's revision, :class:`AlreadyResumed` where these
        answers already started a job that holds this one,
        :class:`~analysis_service.links.NoCatalogError` for a link answer to a
        job with no catalog, :class:`SourcesOverLimit` where the composed
        sources break ``limits``, and a ``ValueError`` that names the
        submitter's own choices otherwise. ``limits`` is ``None`` only where
        the job holds no sources to bound, as an eval replay of rounds.
        """
        if self.waiting:
            if answers.revision is None:
                raise MissingRevision("send the revision the questions were read with")
            if answers.revision != self.revision:
                raise StaleRevision(
                    "the saved answers changed since these questions were read"
                )
        admitted = self.questions.admit(
            sources=self.sources,
            earlier_links=self.links,
            earlier_facts=self.facts,
            links=answers.links,
            facts=answers.facts,
            save=answers.save,
            skips=answers.skips,
        )
        # Checked for a save too: a saved round that no start could run would
        # hold the job until the submitter shortens an answer.
        breach = None if limits is None else limits.breach(admitted.sources)
        if breach is not None:
            raise SourcesOverLimit(breach)
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
            checkpoint=self.checkpoint,
            follow_up=not self.waiting,
        )

    def correct(self, facts: Sequence[FactAnswer]) -> list[FactAnswer]:
        """Every correction a final report carries once ``facts`` land, or a ``ValueError``."""
        return self.questions.correct(
            earlier_facts=self.facts, corrections=self.corrections, facts=facts
        )
