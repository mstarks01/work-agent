"""The questions one job asks, and the answers it admits.

**One value answers "what does this job ask", "may this answer land" and
"what does it start".** The HTTP routes, the first-run app and the eval replay
each build an :class:`AnswerState` from their own job record. Its
:attr:`~AnswerState.questions` is the :class:`QuestionSet` they serve, and its
:meth:`~AnswerState.answer` checks the revision, admits the answers,
checks the source limits and returns a :class:`SavedAnswers` or a
:class:`ResumedJob`. The caller only stores the one or starts the other. The
facts it admits are the keys of the questions it holds, so the list a caller
serves and the list it accepts cannot differ.

**A job waiting on answers asks the early list; a finished one asks the
report's list.** Both ask the link questions its catalog raises. A waiting job
may continue with no answers, and a finished one has nothing to continue.

**A waiting job asks one adaptive list** (ADR 0078). The list holds every
recommended question of the pause at once: a question is listed only at or
above its kind's floor, and one pause asks capability questions up to their
limit for each selected framework (:data:`EARLY_RULES` is the table). Where a job
selects more than one framework, the frameworks take turns for the places
(:func:`by_turn`), and within a framework's turn the list's own order holds:
the band and the units a capability could settle, the prior per choice for a
field question. What each answer would settle breaks a tie, and is published
on the row (:mod:`analysis_service.answer_effects`). A question answered in part is
listed before any new question, outside the limit, and where the limits hold
questions back the job says so (:attr:`QuestionSet.stop`). A question that
decides whether a selected framework runs at all comes first, outside the
limits (ADR 0067), and the set says what each framework's precondition reads
now (:attr:`QuestionSet.gates`). A part of a capability waits on its parent
(:attr:`QuestionSet.blocked`), and a question an answer made unnecessary is
listed with the reason (:attr:`QuestionSet.unnecessary`). A submitter saves
answers, which writes them onto the job and runs no model, and the list is
read again off the model with them in. So an answer removes the parts of a
capability it rules out, and a named mechanism lowers the questions that
rested on its lead. A save records only the questions the page says it
presented, checked against the list (:class:`Answers`), so a question the
service returned and the page never showed is not recorded as shown.

**A report offers one follow-up** (ADR 0054). A fact an earlier save
answered is not asked again, and an answer of "I don't know" counts. A report
lists every open fact its conditional findings wait on, so one follow-up can
answer them all. The analysis then runs once more, and the report it writes
is final: it asks nothing and admits no answer. A second follow-up would mostly
answer the facts a reviewer names differently on each run
(``QA-2026-09-26-03-E22``).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Container, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import cached_property, partial
from types import MappingProxyType
from typing import TYPE_CHECKING, Annotated, Any, Literal, TypeVar

from pydantic import Field

from analysis_service.answer_effects import Effect, capability_effects
from analysis_service.answer_sets import NO_ANSWERS, AnswerSet, within_held_limits
from analysis_service.assertions import UNKNOWN, AssertionCatalog
from analysis_service.bands import UNRANKED
from analysis_service.capabilities import CapabilityFact, lineage, resolve
from analysis_service.claims import FrameworkAnalysis, FrameworkName, UnknownKey
from analysis_service.early_questions import (
    EarlyQuestion,
    early_questions,
    framework_gates,
    waiting_parts,
)
from analysis_service.fact_answers import (
    MAX_DETAIL_CHARS,
    MAX_FACT_ANSWERS,
    MAX_HELD_FACTS,
    FactAnswer,
    SkipKey,
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
    resumed_sources,
)
from analysis_service.open_facts import (
    CAPABILITY_GROUP,
    group_of,
    open_attribute,
    prepared_model,
)
from analysis_service.questions import FactQuestion, fact_questions
from analysis_service.sources import LimitBreach, Source, SourceLimits
from analysis_service.system_model import ModelIndex, SystemModel

if TYPE_CHECKING:
    # ``jobs`` imports this module for the skip vocabulary.
    from analysis_service.jobs import Checkpoint

__all__ = [
    "AMENDMENT_LABEL",
    "ANSWER_LIMITS",
    "EARLY_RULES",
    "MAX_SKIPS",
    "POLICY_VERSION",
    "AdmittedAnswers",
    "AlreadyResumed",
    "AmendedJob",
    "AnswerState",
    "Answers",
    "BandApplicability",
    "Carried",
    "EarlyRule",
    "MissingRevision",
    "PauseSummary",
    "PolicyVersion",
    "QuestionSet",
    "Reason",
    "ResumedJob",
    "Revision",
    "SavedAnswers",
    "SavedDraft",
    "SourcesOverLimit",
    "StaleRevision",
    "Stop",
    "by_turn",
    "passes_floor",
    "question_set",
    "recommended",
    "whose_turn",
]


@dataclass(frozen=True)
class EarlyRule:
    """Which early questions of one kind the list may hold.

    ``floor`` is the score a question needs, and ``limit`` how many places
    of the kind one pause holds for each selected framework, or ``None`` for
    no limit. A group of field questions takes one place and a capability
    question one (:func:`limit_place`, ADR 0081). A place counts toward the limit of every
    framework its questions serve, so a second framework takes no place from
    the first. The two kinds' scores are not on one scale: a field
    question's is a ranking value read off the prior, and a capability
    question's is units it could settle.
    """

    floor: float
    limit: int | None


#: The rule for each kind of early question: ``capability`` and ``field``,
#: which is every other kind (``QA-2026-09-26-03-E20``). A field question's
#: floor of 1 is where the ranking cuts: no measurement says how many findings
#: an answer under it changes. Field questions have no limit (ADR 0088): a
#: field place is a group (ADR 0081), one for each attribute or kind the prior
#: names, so the floor decides which field questions a pause asks. A
#: capability question under 2 settles one unit, and at ASVS level 2 that
#: leaves 28 of 51.
EARLY_RULES: Mapping[str, EarlyRule] = {
    "capability": EarlyRule(floor=2.0, limit=30),
    "field": EarlyRule(floor=1.0, limit=None),
}


#: Why a waiting job asks nothing more (:attr:`QuestionSet.stop`, ADR 0068).
Stop = Literal["budget-exhausted", "below-floor", "skipped", "nothing-left"]

#: Why a question is not in the recommended list (ADR 0078). A client maps
#: each one to its own words. ``parent-undecided`` and ``gate-undecided``
#: hold a question back until an answer shows it; ``parent-absent``,
#: ``gate-satisfied``, ``gate-refuted``, ``fact-settled`` and
#: ``no-longer-needed-by-selected-units`` say why an answer made it
#: unnecessary; ``skipped``, ``answer-limit`` and ``below-floor`` name the
#: lists a submitter may still answer from.
Reason = Literal[
    "parent-undecided",
    "gate-undecided",
    "parent-absent",
    "gate-satisfied",
    "gate-refuted",
    "fact-settled",
    "no-longer-needed-by-selected-units",
    "skipped",
    "answer-limit",
    "below-floor",
]

#: The selection policy a question set was built by, which a client reads to
#: know what the list's order and counts mean.
PolicyVersion = Literal["adaptive-1"]
POLICY_VERSION: PolicyVersion = "adaptive-1"

#: The most skips one save names: every fact and link answer it could carry.
MAX_SKIPS = MAX_FACT_ANSWERS + MAX_LINK_ANSWERS

#: The answer ceilings a client is told, so a page can split its answers into
#: batches the service admits: one request's facts, links and skips, every
#: answer one job holds across its saves (ADR 0070), and the longest detail
#: one answer carries (ADR 0073).
ANSWER_LIMITS: Mapping[str, int] = MappingProxyType(
    {
        "facts": MAX_FACT_ANSWERS,
        "links": MAX_LINK_ANSWERS,
        "skips": MAX_SKIPS,
        "held_facts": MAX_HELD_FACTS,
        "held_links": MAX_HELD_LINKS,
        "detail": MAX_DETAIL_CHARS,
    }
)


def _early_kind(question: EarlyQuestion) -> str:
    return "capability" if question.kind == "capability" else "field"


def passes_floor(question: EarlyQuestion, listed: Sequence[EarlyQuestion]) -> bool:
    """Whether the list may show this question: at or above its kind's floor.

    **The one reader of the floor.** A question in the highest band of one of
    its frameworks also passes, where that framework's questions of the kind
    span more than one band: in an ASVS job above level 1, a capability
    question that settles one level 1 requirement is asked, where one that
    settles only level 2 requirements needs the floor (#1289). Where every one
    of a framework's questions shares one band, as in a level 1 job, the band
    tells them nothing and the floor alone decides. **Each framework's bands
    are read apart** (ADR 0071): a framework with more bands, selected beside
    it, cannot move another framework's questions over the floor.
    """
    kind = _early_kind(question)
    if question.score >= EARLY_RULES[kind].floor:
        return True
    for name, band in question.framework_bands:
        bands = {
            other_band
            for other in listed
            if _early_kind(other) == kind
            for served, other_band in other.framework_bands
            if served == name
        }
        if len(bands) > 1 and band == max(bands):
            return True
    return False


class AlreadyResumed(ValueError):
    """Answers to a job whose earlier answers started a job that locks it."""


@dataclass(frozen=True)
class AdmittedAnswers:
    """What a resumed job carries: its sources and every saved answer."""

    sources: list[Source]
    answers: AnswerSet
    #: Every early question the pause showed, this save's included, which
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
    :attr:`QuestionSet.stop` says why the list asks nothing more; this says
    what remains. The counts are of different things, kept apart:

    * ``recommended`` is the questions the list holds now, and
      ``recommended_choices`` the choices left to answer them: one a facet
      not yet answered, else one. An answer can raise either count, where it
      shows a part or satisfies a gate, and the page says so instead of
      claiming the work only shrinks.
    * ``introduced`` is the distinct early questions the page presented so
      far, and ``choices`` the choices they ask. Neither is bounded by a
      limit, which counts answers.
    * ``settled``, ``partial`` and ``unknown`` count the saved early answers:
      every facet known, some facets known, or "I don't know".
    * ``skipped``, ``held_back`` and ``below_floor`` count the open questions
      the list leaves out: skipped for now, held back by a limit, or under a
      floor. ``blocked`` counts the questions that wait on a parent or a
      gate, and ``unnecessary`` the ones an answer made unnecessary.
    * ``applicability`` counts each runnable framework's units by state and
      band, where its units apply by a rule over capabilities. These are
      units, not findings.
    """

    recommended: int
    recommended_choices: int
    introduced: int
    choices: int
    settled: int
    partial: int
    unknown: int
    skipped: int
    held_back: int
    below_floor: int
    blocked: int
    unnecessary: int
    applicability: Mapping[FrameworkName, tuple[BandApplicability, ...]]

    def to_json(self) -> dict[str, object]:
        return {
            "recommended": self.recommended,
            "recommended_choices": self.recommended_choices,
            "introduced": self.introduced,
            "choices": self.choices,
            "settled": self.settled,
            "partial": self.partial,
            "unknown": self.unknown,
            "skipped": self.skipped,
            "held_back": self.held_back,
            "below_floor": self.below_floor,
            "blocked": self.blocked,
            "unnecessary": self.unnecessary,
            "applicability": {
                name: [band.to_json() for band in bands]
                for name, bands in self.applicability.items()
            },
        }


def _element_name(index: ModelIndex, element_id: str) -> str:
    """The element's name, or empty where the ID names no element."""
    return getattr(index.get(element_id), "name", "")


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
    #: Every early question the page presented before this read.
    shown: tuple[UnknownKey, ...]
    #: For a waiting job, each early question an earlier save answered, with
    #: its answer, so a page can show it and take a new answer.
    answered_early: tuple[tuple[EarlyQuestion, FactAnswer], ...]
    answered_links: tuple[tuple[LinkQuestion, LinkAnswer], ...]
    #: For a waiting job, each question the limits of :data:`EARLY_RULES`
    #: hold back from the list. The list leaves it out, and an answer to it is
    #: still admitted, so a submitter can choose to answer it.
    held_back: tuple[EarlyQuestion, ...]
    #: For a waiting job, each early question the submitter skipped for now.
    #: A skip is not an answer: the fact stays open, the list leaves it out,
    #: and an answer to it is still admitted.
    skipped: tuple[EarlyQuestion, ...]
    #: For a waiting job, each link question the submitter skipped for now,
    #: kept apart from :attr:`links` as :attr:`skipped` is from :attr:`early`.
    skipped_links: tuple[LinkQuestion, ...]
    #: The job this one's answers started, which locks it: it asks nothing
    #: and admits no answer while that job is in flight or has its report.
    resumed_by: str | None = None
    #: For a waiting job, what each selected framework's precondition reads
    #: with the saved answers in. Only a ``satisfied`` framework will run; a
    #: refuted one asks nothing, and an undecidable one asks only what decides
    #: it (:func:`~analysis_service.early_questions.framework_gates`).
    gates: Mapping[FrameworkName, PreconditionResult] = field(default_factory=dict)
    #: For a waiting job, each open question under its kind's floor
    #: (:func:`passes_floor`). The list leaves it out, and an answer to it is
    #: still admitted, so a submitter can choose to answer it.
    below_floor: tuple[EarlyQuestion, ...] = ()
    #: For a waiting job, what the pause asked and what it leaves open; ``None``
    #: for a report's set.
    summary: PauseSummary | None = None
    #: For a waiting job, each question that waits on another: a part whose
    #: parent the list asks and is not yet answered "yes", or a question of a
    #: framework whose precondition an answer reopened. It takes no place
    #: under a limit, and an answer to it is still admitted.
    blocked: tuple[tuple[EarlyQuestion, Reason], ...] = ()
    #: For a waiting job, each question of the first list that a saved answer
    #: proved unnecessary, with the rule that proved it.
    unnecessary: tuple[tuple[EarlyQuestion, Reason], ...] = ()
    #: For a waiting job, what each possible answer to a listed capability
    #: question proves about the others (:mod:`analysis_service.answer_effects`).
    effects: Mapping[UnknownKey, Effect] = field(default_factory=dict)

    @property
    def stop(self) -> Stop | None:
        """Why a waiting job asks nothing more, or ``None`` while it asks.

        **No stop says every fact is settled.** ``budget-exhausted`` where the
        limits hold questions back, ``below-floor`` where only questions under
        a floor are left, ``skipped`` where every question left was skipped,
        and ``nothing-left`` where no open question is left to ask. In each
        case :attr:`summary` says what stays open.
        """
        if not self.done:
            return None
        if self.held_back:
            return "budget-exhausted"
        if self.below_floor:
            return "below-floor"
        return "skipped" if self.skipped or self.skipped_links else "nothing-left"

    @property
    def asked(self) -> frozenset[UnknownKey]:
        """Every fact the questions name, which is every fact that takes an answer.

        A question the limits hold back, one under its floor, or one that
        waits on its parent is named too: the list leaves it out, but a
        submitter may choose to answer it.
        """
        early = frozenset(
            question.key
            for question in (
                *self.early,
                *self.skipped,
                *self.held_back,
                *self.below_floor,
                *(question for question, _ in self.blocked),
            )
        )
        return early | frozenset(question.key for question in self.facts)

    @staticmethod
    def _early_row(index: ModelIndex, question: EarlyQuestion) -> dict[str, object]:
        """The question with each choice's element name and its element's source words.

        A page has no report to look a name up in, so the names travel with
        the question. Every string is untrusted and lands on a page as text.
        """
        element = index.get(key_ref(question.key).element_id)
        return question.to_json() | {
            "choices": [
                {"id": choice, "name": _element_name(index, choice)}
                for choice in question.choices
            ],
            "excerpt": "" if element is None else element.source_excerpt,
        }

    @staticmethod
    def _link_row(index: ModelIndex, question: LinkQuestion) -> dict[str, object]:
        """The question with each option's element name beside its ID."""
        return question.to_json() | {
            "options": [
                {"id": option, "name": _element_name(index, option)}
                for option in question.options
            ]
        }

    def to_json(self) -> dict[str, object]:
        """Every question with what a page shows beside it.

        **The one payload**: the ``/v1`` questions route and the form page
        both serve it, so a question has one shape on every surface.
        """
        index = ModelIndex.of(self.model)
        early = partial(self._early_row, index)
        link = partial(self._link_row, index)

        def listed(question: EarlyQuestion, reason: Reason | None) -> dict[str, object]:
            effect = self.effects.get(question.key)
            return early(question) | {
                "reason": reason,
                "rank": None if effect is None else effect.to_json(),
            }

        return {
            "link_questions": [link(question) for question in self.links],
            "fact_questions": [question.to_json() for question in self.facts],
            "early_questions": [listed(question, None) for question in self.early],
            "final": self.final,
            "resumed_by": self.resumed_by,
            "policy_version": POLICY_VERSION,
            "early_stop": self.stop,
            "early_blocked": [listed(*blocked) for blocked in self.blocked],
            "early_unnecessary": [listed(*moot) for moot in self.unnecessary],
            "early_held_back": [
                listed(question, "answer-limit") for question in self.held_back
            ],
            "early_below_floor": [
                listed(question, "below-floor") for question in self.below_floor
            ],
            "early_summary": None if self.summary is None else self.summary.to_json(),
            "framework_gates": dict(self.gates),
            "skipped_early": [listed(question, "skipped") for question in self.skipped],
            "skipped_links": [link(question) for question in self.skipped_links],
            "answered_early": [
                early(question) | {"answer": answer.model_dump(mode="json")}
                for question, answer in self.answered_early
            ],
            "answered_links": [
                link(question) | {"answer": answer.model_dump(mode="json")}
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
        earlier: AnswerSet,
        given: AnswerSet,
        save: bool = False,
        skips: Sequence[SkipKey] = (),
        presented: Sequence[UnknownKey] = (),
    ) -> AdmittedAnswers:
        """The resumed job these answers start, or a ``ValueError``.

        ``sources`` and ``earlier`` are what the answered job was given, and
        ``given`` is this submission's answers. A refusal names the submitter's own
        choices, so its message is safe to show. A link answer to a job with no
        catalog raises :class:`~analysis_service.links.NoCatalogError`. ``save`` admits
        answers a waiting job keeps, or a report's draft (ADR 0070), which must
        answer or skip something and starts nothing. ``skips`` names questions
        the submitter skips for now; only a save skips. ``presented`` names
        the early questions the page showed, which is what the record keeps
        as shown and what a skip must be of (:meth:`presented`). A report's one follow-up is
        refused where its answers add no information
        (:func:`_adds_information`), so it is not spent on a run that reads
        nothing new.
        """
        if self.resumed_by is not None:
            raise AlreadyResumed(
                f"job {self.resumed_by} locks this job;"
                " read that job, and answer here again only if it fails"
            )
        links, facts = given.links, given.facts
        if save and not (links or facts or skips):
            raise ValueError("a save answers or skips at least one question")
        merged = merged_facts(earlier.facts, facts)
        complete: frozenset[SkipKey] = answered_keys(merged) & {
            fact.key for fact in facts
        } | {fold(link.principal) for link in links}
        shown = self._check_presented(presented)
        self._check_skips(skips, complete, save, shown)
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
            earlier.facts,
            asked_links=[
                question.key for question in (*self.links, *self.skipped_links)
            ],
            earlier_links=earlier.links,
            reopen=self.waiting,
        )
        if not self.waiting and not save and not _adds_information(earlier, given):
            raise ValueError(
                "these answers add nothing the analysis can use: each one is"
                ' "I don\'t know" or repeats an earlier answer. The follow-up'
                " has not run, and it is still available"
            )
        composed, all_links, all_facts = resumed_sources(
            sources, earlier.links, links, earlier.facts, facts
        )
        return AdmittedAnswers(
            sources=composed,
            answers=AnswerSet(links=tuple(all_links), facts=tuple(all_facts)),
            shown=tuple(dict.fromkeys([*self.shown, *shown])),
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

    def _check_presented(
        self, presented: Sequence[UnknownKey]
    ) -> tuple[UnknownKey, ...]:
        """The reported questions, or a ``ValueError`` where one is not a listed
        question (ADR 0078).

        **A page cannot record a question as shown that the service did not
        list.** So the shown history holds only questions the service offered
        and the page says it showed. A part never sits in the list beside its
        open parent (:func:`recommended`), so the list itself is what a page
        may show.
        """
        listed = {question.key for question in self.early}
        for key in presented:
            if key not in listed:
                raise ValueError(
                    "only a question this list holds is recorded as shown:"
                    f' "{fact_label(key, self.model)}"'
                )
        return tuple(dict.fromkeys(presented))

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
        """Refuse a skip outside a save, of a question the page did not present
        (ADR 0069), or of a question the same submission answers in full.

        ``presented`` is what the page reported and the list holds
        (:meth:`_check_presented`), so a question the service returned and
        the page never showed is not skipped. A question with facets answered
        in part may be skipped with it: the facets given are kept, and the
        rest are set aside for now, so a question the submitter can answer only
        in part does not stay first in the list.
        """
        if not skips:
            return
        if not save:
            raise ValueError("only a save skips questions")
        shown: dict[SkipKey, str] = {
            question.key: question.principal for question in self.links
        }
        shown |= {key: fact_label(key, self.model) for key in presented}
        for key in skips:
            if key not in shown:
                named = key if isinstance(key, str) else fact_label(key, self.model)
                raise ValueError(
                    f'only a question the page presented is skipped: "{named}"'
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
    answered: AnswerSet,
    final: bool,
    shown: Sequence[UnknownKey],
    skipped: Sequence[SkipKey] = (),
    resumed_by: str | None = None,
) -> QuestionSet:
    """The questions a job asks: a waiting job's list, or its report's list.

    ``frameworks`` maps each selected framework to its options, and ranks the
    early list; ``analyses`` ranks the report's list. So a waiting job passes
    no analyses, and a finished one's frameworks go unread. ``answered`` holds
    the saved answers, whose questions are not asked again. ``final`` marks a report the follow-up wrote, and ``shown``
    is every early question the pause presented. ``skipped`` is every early
    question and link question the pause's submitter skipped for now; a
    report's list reads both to say what became of each question before the
    analysis (:func:`~analysis_service.fact_answers.fact_status`). A waiting
    job's ``model`` and ``catalog`` are its checkpoint's, and the saved answers
    are written in here, as the resumed run writes them. ``resumed_by`` is the
    job this one's answers started and that locks it, which a store reads
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
            answered_early=(),
            answered_links=(),
            held_back=(),
            skipped=(),
            skipped_links=(),
        )
    if not waiting:
        said = {answer.key: answer for answer in answered.facts}
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
                for question in fact_questions(analyses, model, catalog, answered.facts)
            ),
            links=link_questions(catalog, model),
            final=False,
            shown=tuple(shown),
            answered_early=(),
            answered_links=(),
            held_back=(),
            skipped=(),
            skipped_links=(),
        )
    done = answered_keys(answered.facts)
    first = early_questions(model, frameworks, catalog)
    asked_links = link_questions(catalog, model)
    view = answered_model(model, answered.facts)
    if catalog is not None:
        catalog = apply_answers(catalog, view, answered.links, answered.facts)[0]
    listed = early_questions(view, frameworks, catalog)
    # An answer to a gate question counts toward no kind's limit, so the key
    # stays exempt once its answer has decided the gate and it is no longer
    # listed as one.
    given = {answer.key: answer for answer in answered.facts}
    gated = {question.key for question in (*first, *listed) if question.gates}
    # An answer counts toward the limit of each framework its question served,
    # and of every selected framework where no list holds its question.
    served = {question.key: question.frameworks for question in (*first, *listed)}
    answered_for = {
        answer.key: served.get(answer.key, tuple(frameworks))
        for answer in answered.facts
        if answer.key not in gated
    }
    aside: frozenset[SkipKey] = frozenset(skipped)
    gates = framework_gates(view, frameworks, catalog)
    prepared = prepared_model(view, catalog)
    runnable = {
        name: frameworks[name] for name, state in gates.items() if state == "satisfied"
    }
    open_keys = {question.key for question in listed if question.key not in done}
    waiting_on = {
        question.key: question
        for question in first
        if question.key not in open_keys and question.key not in done
    }
    held: list[tuple[EarlyQuestion, Reason]] = []
    moot: list[tuple[EarlyQuestion, Reason]] = []
    facts = resolve(prepared.capability_facts())
    for question in waiting_on.values():
        reason = _left_out(question, gates, facts, prepared)
        (held if reason == "gate-undecided" else moot).append((question, reason))
    # What each undecidable framework would ask once its gate is satisfied:
    # listed as waiting on the gate, so a page can say how much a gate answer
    # unlocks. A question another framework lists already is not repeated.
    undecided = [name for name, state in gates.items() if state == "undecidable"]
    if undecided:
        listed_keys = {question.key for question in listed} | {
            question.key for question, _ in held
        }
        would_ask = early_questions(
            view,
            {name: frameworks[name] for name in undecided},
            catalog,
            assume=undecided,
        )
        held += [
            (question, "gate-undecided")
            for question in would_ask
            if not question.gates
            and question.key not in listed_keys
            and question.key not in done
            and passes_floor(question, would_ask)
        ]
    effects = capability_effects(
        prepared,
        runnable,
        [
            question
            for question in _rankable(listed, aside | done)
            if question.kind == "capability"
        ],
        _settled(aside | done),
    )
    active, blocked, held_back = recommended(
        listed, aside | done, answered_for, effects
    )
    below_floor = tuple(
        question
        for question in listed
        if not question.gates
        and question.key not in aside | done
        and not passes_floor(question, listed)
    )
    linked = {fold(link.principal): link for link in answered.links}
    open_links = link_questions(catalog, view)
    return QuestionSet(
        model=view,
        catalog=catalog,
        waiting=True,
        early=active,
        facts=(),
        links=tuple(question for question in open_links if question.key not in aside),
        final=False,
        shown=tuple(shown),
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
        blocked=(*blocked, *held),
        unnecessary=tuple(moot),
        effects=effects,
        summary=_summary(
            prepared,
            frameworks,
            gates,
            listed,
            done,
            answered.facts,
            shown,
            recommended=active,
            skipped=len(aside),
            held_back=len(held_back),
            below_floor=len(below_floor),
            blocked=len(blocked) + len(held),
            unnecessary=len(moot),
        ),
    )


def _left_out(
    question: EarlyQuestion,
    gates: Mapping[FrameworkName, PreconditionResult],
    facts: Mapping[str, CapabilityFact],
    prepared: SystemModel,
) -> Reason:
    """Why a question the pause first listed is open and no longer listed.

    **Each reason names the rule that removed the question.** A gate
    question leaves once a precondition it decides is read; another question
    leaves with its frameworks' gates, with the parent that an answer made
    absent (``facts`` is :func:`~analysis_service.capabilities.resolve`'s
    answer), with the fact a later answer or the catalog states, or with the
    units that no longer need it. A question whose framework a reopened
    answer left undecidable waits, and is not unnecessary.
    """
    states = {gates.get(name) for name in question.frameworks}
    if question.gates:
        if "satisfied" in states:
            return "gate-satisfied"
        if states == {"refuted"}:
            return "gate-refuted"
        return "fact-settled"
    if "satisfied" not in states:
        return "gate-undecided" if "undecidable" in states else "gate-refuted"
    ref = key_ref(question.key)
    if ref.capability:
        fact = facts.get(ref.capability)
        if fact is None or fact.state == "unknown":
            return "no-longer-needed-by-selected-units"
        return "parent-absent" if fact.derived_from else "fact-settled"
    if ref.attribute and not open_attribute(prepared, ref.element_id, ref.attribute):
        return "fact-settled"
    return "no-longer-needed-by-selected-units"


def _settled(done: Iterable[SkipKey]) -> set[str]:
    """The capabilities whose questions ``done`` holds."""
    return {key_ref(key).capability for key in done if not isinstance(key, str)}


def _rankable(
    listed: Sequence[EarlyQuestion], done: frozenset[SkipKey]
) -> list[EarlyQuestion]:
    """The listed questions a list may rank: not a gate, at or above its
    kind's floor, not done, and not waiting on a parent.

    **The one reader of which question a list may rank.** :func:`recommended`
    takes from it, and the effects that break its ties are computed over its
    capability questions, so no rankable question goes without its effect.
    """
    waiting = _waiting(listed, _settled(done))
    return [
        question
        for question in listed
        if not question.gates
        and passes_floor(question, listed)
        and question.key not in done
        and question.key not in waiting
    ]


def _waiting(
    listed: Sequence[EarlyQuestion], settled: Container[str]
) -> frozenset[UnknownKey]:
    """The listed questions that wait on a parent, by the one reader of the
    rule (:func:`~analysis_service.early_questions.waiting_parts`)."""
    names = {
        key_ref(question.key).capability: question.key
        for question in listed
        if question.kind == "capability"
    }
    return frozenset(names[name] for name in waiting_parts(names.keys(), settled))


def _choices_left(question: EarlyQuestion, answer: FactAnswer | None) -> int:
    """The choices a person still makes to answer it: one a facet without an
    answer, else one."""
    if question.facets and answer is not None and answer.facets:
        return sum(1 for facet in question.facets if facet.id not in answer.facets)
    return question.decisions


def _summary(
    prepared: SystemModel,
    frameworks: Mapping[FrameworkName, Mapping[str, Any]],
    gates: Mapping[FrameworkName, PreconditionResult],
    listed: Sequence[EarlyQuestion],
    done: frozenset[UnknownKey],
    answered: Sequence[FactAnswer],
    introduced: Sequence[UnknownKey],
    *,
    recommended: Sequence[EarlyQuestion],
    skipped: int,
    held_back: int,
    below_floor: int,
    blocked: int,
    unnecessary: int,
) -> PauseSummary:
    """What a waiting job asked and what it leaves open (:class:`PauseSummary`).

    ``prepared`` is the model the lanes read, with the saved answers in.
    ``done`` is every fact a saved answer names, "I don't know" included,
    ``introduced`` every early question the page presented so far, and
    ``recommended`` the list as it stands.
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
            tally = counts.setdefault((-band.order, band.label), Counter())
            tally[entry.state] += 1
            reachable = {
                key for missing in entry.missing for key in (missing, *lineage(missing))
            }
            if entry.state == "unknown" and not reachable & askable:
                tally["unaskable"] += 1
        if counts:
            applicability[name] = tuple(
                BandApplicability(
                    band=label,
                    states={
                        state: count
                        for state, count in tally.items()
                        if state != "unaskable"
                    },
                    unaskable=tally["unaskable"],
                )
                for (_, label), tally in sorted(counts.items())
            )
    shown = tuple(dict.fromkeys(introduced))
    content = [_known_content(answer) for answer in answered]
    settles = [answer.settles for answer in answered]
    given = {answer.key: answer for answer in answered}
    return PauseSummary(
        recommended=len(recommended),
        recommended_choices=sum(
            _choices_left(question, given.get(question.key)) for question in recommended
        ),
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
        blocked=blocked,
        unnecessary=unnecessary,
        applicability=applicability,
    )


def recommended(
    listed: Sequence[EarlyQuestion],
    done: frozenset[SkipKey],
    answered_for: Mapping[UnknownKey, Sequence[FrameworkName]],
    effects: Mapping[UnknownKey, Effect],
) -> tuple[
    tuple[EarlyQuestion, ...],
    tuple[tuple[EarlyQuestion, Reason], ...],
    tuple[EarlyQuestion, ...],
]:
    """The list as the page shows it, the questions that wait on a parent, and the questions the limits hold back.

    **The one reader of the list's order** (ADR 0078). A question a save
    answered in full is not asked again. One it answered in part is listed
    first, and takes no place under the limit, which it already counts
    toward: each saved answer counts toward its kind's limit, and the limit
    bounds only the questions not yet answered at all. ``answered_for`` maps
    each answered question to the frameworks it served. ``done`` is every
    question answered or skipped.

    **Each framework has its own limit.** A question counts toward the limit
    of every framework it serves, and a new question is taken while one of
    them has a place left. So the questions one framework asks alone keep
    their places when a second framework is selected (``QA-2026-10-09-01-E1``).

    **A part waits on its parent.** A capability question whose parent is
    still open is not listed and takes no place: it is listed once the parent
    is answered "yes", and leaves once the parent is "no". So the list shows
    no hidden row, and a page needs no rule of its own for which part shows.

    **Within a kind, the list's own order is taken first**: the band and the
    units a capability could settle, or the prior per choice for a field
    question (:mod:`analysis_service.early_questions`). Where two questions
    tie, the one whose answer settles the most other questions comes first
    (``effects``), then the one whose least answer settles the most. An
    order that put the effects first decided 13% to 37% fewer units at five
    choices on the archive (``QA-2026-10-12-02-E1``). A field question has
    no proven effect on another question. The selected frameworks then take
    turns over both kinds (:func:`by_turn`).

    **A question that decides a framework's precondition is listed first**,
    every one still open, outside the limits and the floor (ADR 0067).

    **Each question kind is listed once** (:func:`grouped`, ADR 0080): its
    rows follow its first row, so a lower row of a kind can move ahead of
    another kind's row. A capability question keeps its own place.
    """
    gates = [
        question for question in listed if question.gates and question.key not in done
    ]
    waiting = _waiting(listed, _settled(done))
    undecided: Reason = "parent-undecided"
    blocked = tuple(
        (question, undecided)
        for question in listed
        if question.key not in done and question.key in waiting
    )
    shown: list[EarlyQuestion] = []
    held_back: list[EarlyQuestion] = []
    # What a question answered in part costs again where it is placed. The
    # saved answers charged it already, so its place charges it once.
    again: Counter[str] = Counter()
    place = {question.key: index for index, question in enumerate(listed)}
    # What the saved answers charged each framework: a question's choices,
    # to every framework it served. The turns start from here, not from zero.
    spent: Counter[str] = Counter()
    for key, served in answered_for.items():
        for name in served:
            spent[name] += len(answer_facets(key)) or 1

    def rank(question: EarlyQuestion) -> tuple[float, float, float, int, int]:
        # The list's own order first: a capability's standing, a field
        # question's prior per choice, which its place in the list holds. The
        # effects break a tie, and the list's place breaks the rest.
        if question.kind != "capability":
            return (0, 0.0, 0.0, 0, place[question.key])
        effect = effects.get(question.key)
        if effect is None:
            return (*question.standing, 0.0, 0, place[question.key])
        return (
            *question.standing,
            -effect.potential / question.decisions,
            -effect.least,
            place[question.key],
        )

    rankable = _rankable(listed, done)
    for kind, rule in EARLY_RULES.items():
        # The places each framework's limit has counted: a group of field
        # questions takes one place, and a capability question its own.
        counted: dict[str, set[UnknownKey | str]] = defaultdict(set)
        for key, served in answered_for.items():
            if (kind == "capability") == bool(key_ref(key).capability):
                for name in served:
                    counted[name].add(limit_place(key))
        eligible = [question for question in rankable if _early_kind(question) == kind]
        started = [question for question in eligible if question.key in answered_for]
        taken: list[EarlyQuestion] = []
        fresh = sorted(
            (question for question in eligible if question.key not in answered_for),
            key=rank,
        )
        for question in by_turn(fresh, spent):
            slot = limit_place(question.key)
            if rule.limit is None or any(
                slot in counted[name] or len(counted[name]) < rule.limit
                for name in question.frameworks
            ):
                taken.append(question)
                for name in question.frameworks:
                    counted[name].add(slot)
            else:
                held_back.append(question)
        shown += [*started, *taken]
        for question in started:
            again.update(dict.fromkeys(question.frameworks, question.decisions))
    return grouped((*gates, *by_turn(shown, spent - again))), blocked, tuple(held_back)


def limit_place(key: UnknownKey) -> UnknownKey | str:
    """The place a question takes under its framework's limit (ADR 0081).

    **The one reader of what the limit counts.** A field question's place is
    its group, so a kind asked of six elements is one question; a page asks it
    once with one shared answer. A capability question is its own place.
    """
    ref = key_ref(key)
    return key if ref.capability else group_of(ref)[0]


def grouped(questions: Sequence[EarlyQuestion]) -> tuple[EarlyQuestion, ...]:
    """The list with each question kind's rows together (ADR 0080).

    A group takes the place of its first row, and its other rows follow it in
    the list's order, so a page shows each kind once. A capability question
    keeps its own place: every capability shares one group, and gathering them
    took the frameworks' turns away (``QA-2026-10-13-02-E2``).
    """
    places: dict[int | str, list[EarlyQuestion]] = {}
    for index, question in enumerate(questions):
        place = index if question.group == CAPABILITY_GROUP else question.group
        places.setdefault(place, []).append(question)
    return tuple(question for rows in places.values() for question in rows)


def by_turn(
    questions: Sequence[EarlyQuestion], charged: Mapping[str, int] | None = None
) -> list[EarlyQuestion]:
    """The questions with the selected frameworks taken in turn.

    The frameworks take turns one question at a time, as :func:`_in_turn`
    states: the next question is the first one left that serves the
    framework charged the fewest choices so far, the first by name on a tie,
    and every framework it serves is charged its choices
    (:attr:`~analysis_service.early_questions.EarlyQuestion.frameworks`).
    So a question two frameworks share costs each of them, and asks once.
    Within one framework the list's order holds, so a parent still comes
    before its parts: a part serves no framework its parent does not.
    The list ranks a field question that two frameworks share by the sum of
    their scores, so one framework's prior scale can move that question in
    another framework's turn (ADR 0053, ``QA-2026-10-06-01-E1``).
    With one framework selected the order is the list's order.

    ``charged`` is what each framework was charged by the saved answers, so
    the turns carry over the saves: a submitter who answers the first question
    and saves meets the other framework's question next, not the same
    framework's again (``QA-2026-10-12-02-E2``).
    """
    return _in_turn(
        questions,
        serves=lambda question: set(question.frameworks),
        choices=lambda question: question.decisions,
        charged=charged,
    )


_Item = TypeVar("_Item")
_Name = TypeVar("_Name", bound=str)


def whose_turn(waiting: Iterable[_Name], charged: Mapping[_Name, int]) -> _Name:
    """The framework whose turn it is, of the ones with something left.

    **The one reader of the turn rule**: the framework charged the fewest
    choices so far goes next, and on a tie the first by name goes first.
    :func:`_in_turn` and the offline order policies both ask it.
    """
    return min(waiting, key=lambda each: (charged.get(each, 0), each))


def _in_turn(
    items: Sequence[_Item],
    *,
    serves: Callable[[_Item], set[str]],
    choices: Callable[[_Item], int],
    charged: Mapping[str, int] | None = None,
) -> list[_Item]:
    """The items with the selected frameworks taking turns.

    The order :func:`by_turn` reads. The next item is the first one left that
    serves the framework :func:`whose_turn` names. Every framework the
    item serves is charged its choices. An item that serves no framework
    keeps its place after every item that does.
    """
    charged = Counter(charged or {})
    left = list(items)
    order: list[_Item] = []
    while left:
        served = set().union(*map(serves, left))
        if not served:
            return order + left
        name = whose_turn(served, charged)
        item = next(item for item in left if name in serves(item))
        left.remove(item)
        order.append(item)
        for holder in serves(item):
            charged[holder] += choices(item)
    return order


def _adds_information(earlier: AnswerSet, given: AnswerSet) -> bool:
    """True where the answers change what the analysis reads.

    A link answer does where it places a principal anew or elsewhere. A fact
    answer does where its known content changes: "I don't know" is none, and
    of a facet answer only the facets answered otherwise count. A changed
    detail counts too (ADR 0073).
    """
    placed = {fold(link.principal): link.element for link in earlier.links}
    if any(placed.get(fold(link.principal)) != link.element for link in given.links):
        return True
    before = {fact.key: fact for fact in earlier.facts}
    after = {fact.key: fact for fact in merged_facts(earlier.facts, given.facts)}
    return any(
        _known_content(after[fact.key]) != _known_content(before.get(fact.key))
        or _detail(after[fact.key]) != _detail(before.get(fact.key))
        for fact in given.facts
    )


def _detail(answer: FactAnswer | None) -> str:
    """An answer's detail, which is new information wherever it changes."""
    return "" if answer is None else answer.detail


def _known_content(answer: FactAnswer | None) -> frozenset[tuple[str, str]]:
    """What an answer states, without its "I don't know" parts."""
    if answer is None:
        return frozenset()
    if answer.facets is not None:
        return frozenset(
            (facet, value) for facet, value in answer.facets.items() if value != UNKNOWN
        )
    return frozenset({("", answer.value)}) if answer.known else frozenset()


#: The revision a request names: a whole number from zero, and never ``true``.
#: **The one reader of a posted revision.** The HTTP models declare their
#: ``revision`` fields with it, and the first-run app validates a posted value
#: with it.
Revision = Annotated[int, Field(ge=0, strict=True)]


class StaleRevision(Exception):
    """A save, an amendment or a correction that names an earlier revision
    than the job's own."""


class MissingRevision(ValueError):
    """A save, an amendment or a correction that names no revision where it
    must."""


class SourcesOverLimit(Exception):
    """Answers whose composed sources break the deployment's source limits."""

    def __init__(self, breach: LimitBreach) -> None:
        super().__init__(breach.message)
        self.breach = breach


@dataclass(frozen=True)
class Answers:
    """One submission of answers to a job's questions.

    ``given`` is the answers. ``save`` keeps a waiting job's answers, or a
    report's draft, and starts nothing. ``skips`` names questions the
    submitter skips for now; only a waiting job's save skips. ``presented``
    names the early questions the page showed, which the record keeps as
    shown and a skip must be of; a question the service listed and the page
    did not show is not recorded. ``revision`` is the revision the questions
    were read with, which a waiting job and every save require.
    """

    given: AnswerSet = NO_ANSWERS
    save: bool = False
    skips: Sequence[SkipKey] = ()
    presented: Sequence[UnknownKey] = ()
    revision: int | None = None


@dataclass(frozen=True)
class SavedAnswers:
    """What a waiting job keeps after a save. No model runs.

    ``revision`` is the revision the save read. The store writes the
    answers only while the job still holds that revision, so of two saves that
    read one revision only the first lands.
    """

    answers: AnswerSet
    shown: tuple[UnknownKey, ...]
    skipped: tuple[SkipKey, ...]
    revision: int


#: The label an amendment's description carries, with the lowest number after
#: it that no source label of the job already uses. A caller names its own
#: sources, so no label counts the amendments.
AMENDMENT_LABEL = "Amendment"


@dataclass(frozen=True)
class Carried:
    """The answers a job an amendment started carried from its paused job.

    ``taken`` is the ones its questions take; ``dropped`` is the ones they do
    not, which a page lists.
    """

    taken: AnswerSet
    dropped: AnswerSet

    def dropped_json(self, model: SystemModel) -> dict[str, list[dict[str, object]]]:
        """The answers its questions do not take, as every surface lists them:
        each fact with the label its question shows, and each link."""
        return {
            "carried_dropped": [
                {"label": fact_label(fact.key, model)} | fact.model_dump(mode="json")
                for fact in self.dropped.facts
            ],
            "carried_dropped_links": [
                link.model_dump(mode="json") for link in self.dropped.links
            ],
        }


@dataclass(frozen=True)
class AmendedJob:
    """The job an amendment starts: what it extracts, and what it carries."""

    sources: list[Source]
    answers: AnswerSet


@dataclass(frozen=True)
class SavedDraft:
    """What a report keeps after its follow-up saves a batch. No model runs.

    ``draft`` is every draft answer so far, this batch's over the earlier
    ones. The report and its follow-up are unchanged: the run
    that answers them composes the draft with what it is sent (ADR 0070).
    ``revision`` is the revision the save read, which the store checks as it
    does a save's.
    """

    draft: AnswerSet
    revision: int


@dataclass(frozen=True)
class ResumedJob:
    """The **Resumed Job** a submission starts: what its run reads, and where it starts.

    The run starts at ``prepare`` from ``checkpoint``, so no extraction and no
    assertion pass runs again (#1252). ``follow_up`` is True where the answers
    are to a finished report, so the new job's report is final (ADR 0054).
    """

    sources: list[Source]
    answers: AnswerSet
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
    :class:`SavedAnswers` or starts a :class:`ResumedJob`.

    ``checkpoint`` is the model and catalog the questions ask about: a waiting
    job's, or a finished report's. ``analyses`` are the report's findings, and
    empty for a waiting job. ``waiting`` marks a job that waits on answers
    before its analysis, and ``final`` a report its follow-up wrote.
    ``sources`` and ``answers`` are what the job was given, a waiting job's
    saved answers included. ``shown`` is every early question
    the pause showed, ``skipped`` every question its submitter skipped for
    now, and ``corrections`` what a final report's owner corrected since.
    ``revision`` is how many saves a waiting job made. ``resumed_by`` names
    the job these answers started, where it locks this one.
    """

    checkpoint: Checkpoint
    frameworks: Mapping[FrameworkName, Mapping[str, Any]]
    analyses: Sequence[FrameworkAnalysis]
    waiting: bool
    final: bool
    sources: Sequence[Source]
    answers: AnswerSet
    shown: Sequence[UnknownKey]
    skipped: Sequence[SkipKey]
    corrections: Sequence[FactAnswer]
    revision: int
    resumed_by: str | None
    #: A report's draft answers: what its follow-up saved in batches and has
    #: not yet run. Empty for a waiting job, whose saved answers are ``answers``.
    draft: AnswerSet = NO_ANSWERS
    #: The answers a paused job held when its amendment started this job
    #: (ADR 0072). This job's questions take each one they still ask.
    carried_in: AnswerSet = NO_ANSWERS

    def _question_set(self, answers: AnswerSet) -> QuestionSet:
        assertions = self.checkpoint.assertions
        return question_set(
            self.checkpoint.system_model,
            None if assertions is None else assertions.catalog,
            self.frameworks,
            self.analyses,
            waiting=self.waiting,
            answered=answers,
            final=self.final,
            shown=self.shown,
            skipped=self.skipped,
            resumed_by=self.resumed_by,
        )

    @cached_property
    def carried(self) -> Carried:
        """The carried answers this job's questions take, and those they do not.

        **Each carried answer passes the answer check of this job's own
        questions**, as a submitter's answer does, so an answer about a part
        the amended model no longer has, or about a fact it now states, is not
        taken. An answer this job's own saves gave since replaces the carried
        one.
        """
        if self.carried_in.empty:
            return Carried(taken=NO_ANSWERS, dropped=NO_ANSWERS)
        base = self._question_set(self.answers)
        saved = {answer.key for answer in self.answers.facts}
        placed = {fold(link.principal) for link in self.answers.links}
        facts = [a for a in self.carried_in.facts if a.key not in saved]
        links = [
            link for link in self.carried_in.links if fold(link.principal) not in placed
        ]
        taken_facts = tuple(
            a for a in facts if self._admits(base, AnswerSet(facts=(a,)))
        )
        taken_links = tuple(
            link for link in links if self._admits(base, AnswerSet(links=(link,)))
        )
        return Carried(
            taken=AnswerSet(links=taken_links, facts=taken_facts),
            dropped=AnswerSet(
                links=tuple(link for link in links if link not in taken_links),
                facts=tuple(a for a in facts if a not in taken_facts),
            ),
        )

    def _admits(self, base: QuestionSet, given: AnswerSet) -> bool:
        try:
            base.admit(
                sources=self.sources, earlier=self.answers, given=given, save=True
            )
        except ValueError:
            return False
        return True

    @property
    def held(self) -> AnswerSet:
        """Every answer this job holds: the carried ones its questions take,
        under the ones its own saves gave."""
        return self.answers.over(self.carried.taken)

    @cached_property
    def questions(self) -> QuestionSet:
        """Every question this job asks."""
        if self.carried.taken.empty:
            return self._question_set(self.answers)
        return self._question_set(self.held)

    def _check_revision(self, revision: int | None, *, required: bool) -> None:
        """Refuse a request read off an older revision, or one with none where
        the request must name it."""
        if required and revision is None:
            raise MissingRevision("send the revision the questions were read with")
        if revision is not None and revision != self.revision:
            raise StaleRevision(
                "the saved answers changed since these questions were read"
            )

    def amend(
        self, text: str, *, revision: int | None, limits: SourceLimits | None
    ) -> AmendedJob:
        """The job an amendment to a paused job's description starts (ADR 0072).

        **An amendment is extracted again, not answered.** A person who sees
        that the model misses a component or a flow, or reads a stated fact
        wrongly, writes what is true. The new job extracts the paused job's
        sources with the amendment added as one more description, and pauses
        again. It carries the answers this job holds, and its own questions
        take each one they still ask (:attr:`carried`). The amendment names
        the revision it read, as a save does.
        """
        if self.resumed_by is not None:
            raise AlreadyResumed(
                f"job {self.resumed_by} locks this job;"
                " read that job, and amend here again only if it fails"
            )
        if not self.waiting:
            raise ValueError(
                "only a job waiting on answers takes an amendment; submit the"
                " corrected description as a new job"
            )
        self._check_revision(revision, required=True)
        kept = [source for source in self.sources if source.kind != "answers"]
        taken = {source.label for source in kept}
        number = 1
        while f"{AMENDMENT_LABEL} {number}" in taken:
            number += 1
        sources = [
            *kept,
            Source.description(text, label=f"{AMENDMENT_LABEL} {number}"),
        ]
        breach = None if limits is None else limits.breach(sources)
        if breach is not None:
            raise SourcesOverLimit(breach)
        return AmendedJob(sources=sources, answers=self.held)

    def answer(
        self, answers: Answers, *, limits: SourceLimits | None
    ) -> SavedAnswers | SavedDraft | ResumedJob:
        """The answers a waiting job keeps, a report's draft, or the Resumed Job the answers start.

        **A report's follow-up saves in batches** (ADR 0070). A save to a
        report keeps a draft and runs nothing; the run composes every saved
        draft with what it is sent, so a follow-up can answer more than one
        request carries, and earlier-answer edits count in the batch that
        sends them. A save names the revision it read, as a waiting job's
        every answer does; a run that names one is checked against it.

        A refusal raises: :class:`MissingRevision` or :class:`StaleRevision`
        for the revision, :class:`AlreadyResumed` where these answers already
        started a job that locks this one,
        :class:`~analysis_service.links.NoCatalogError` for a link answer to a
        job with no catalog, :class:`SourcesOverLimit` where the composed
        sources break ``limits``, and a ``ValueError`` that names the
        submitter's own choices otherwise. ``limits`` is ``None`` only where
        the job holds no sources to bound, as an eval replay of saves.
        """
        self._check_revision(answers.revision, required=self.waiting or answers.save)
        given = answers.given
        if not self.waiting:
            if answers.save and given.empty:
                raise ValueError("a saved draft answers at least one question")
            given = given.over(self.draft)
        admitted = self.questions.admit(
            sources=self.sources,
            earlier=self.held,
            given=given,
            save=answers.save,
            skips=answers.skips,
            presented=answers.presented,
        )
        held = within_held_limits(admitted.answers)
        # Checked for a save too: saved answers or a draft that no start could
        # run would hold the job until the submitter shortens an answer.
        breach = None if limits is None else limits.breach(admitted.sources)
        if breach is not None:
            raise SourcesOverLimit(breach)
        if answers.save and not self.waiting:
            return SavedDraft(draft=given, revision=self.revision)
        if answers.save:
            return SavedAnswers(
                answers=held,
                shown=admitted.shown,
                skipped=admitted.skipped,
                revision=self.revision,
            )
        return ResumedJob(
            sources=admitted.sources,
            answers=held,
            shown=admitted.shown,
            skipped=admitted.skipped,
            checkpoint=self.checkpoint,
            follow_up=not self.waiting,
        )

    def correct(
        self, facts: Sequence[FactAnswer], *, revision: int | None
    ) -> list[FactAnswer]:
        """Every correction a final report carries once ``facts`` land, or a ``ValueError``.

        A correction names the revision it read, as a save does (ADR 0090).
        """
        self._check_revision(revision, required=True)
        return self.questions.correct(
            earlier_facts=self.answers.facts, corrections=self.corrections, facts=facts
        )
