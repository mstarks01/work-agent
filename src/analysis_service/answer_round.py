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

**The rounds end.** A fact an earlier round answered is not asked again, and
an answer of "I don't know" counts, so a submitter who does not know is not
asked the same question in every round. A job lineage takes at most
:data:`MAX_ANSWER_ROUNDS` rounds of answers. Each round after a report runs
the analysis again, and its reviewer can name new facts each time, so without
the limit the rounds need not end. A job that reached the limit asks nothing,
and its report is the final one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from analysis_service.assertions import AssertionCatalog
from analysis_service.claims import FrameworkAnalysis, FrameworkName, UnknownKey
from analysis_service.early_questions import EarlyQuestion, early_questions
from analysis_service.links import (
    LinkAnswer,
    LinkQuestion,
    check_answers,
    link_questions,
    resumed_sources,
)
from analysis_service.questions import (
    FactAnswer,
    FactQuestion,
    answered_keys,
    fact_questions,
)
from analysis_service.sources import Source
from analysis_service.system_model import SystemModel

__all__ = ["MAX_ANSWER_ROUNDS", "AdmittedRound", "QuestionSet", "question_set"]

#: How many rounds of answers one job lineage takes: the answers at the pause
#: and after each report, counted together.
MAX_ANSWER_ROUNDS = 3


@dataclass(frozen=True)
class AdmittedRound:
    """What a resumed job carries: its sources and every round's answers."""

    sources: list[Source]
    links: list[LinkAnswer]
    facts: list[FactAnswer]


@dataclass(frozen=True)
class QuestionSet:
    """Every question one job asks, and the model and catalog they ask about."""

    model: SystemModel
    catalog: AssertionCatalog | None
    waiting: bool
    early: tuple[EarlyQuestion, ...]
    facts: tuple[FactQuestion, ...]
    links: tuple[LinkQuestion, ...]
    #: How many rounds of answers the job's lineage took before this job.
    rounds: int

    @property
    def rounds_left(self) -> int:
        """How many more rounds of answers the lineage takes."""
        return max(MAX_ANSWER_ROUNDS - self.rounds, 0)

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
            "answer_rounds_left": self.rounds_left,
        }

    def admit(
        self,
        *,
        sources: Sequence[Source],
        earlier_links: Sequence[LinkAnswer],
        earlier_facts: Sequence[FactAnswer],
        links: Sequence[LinkAnswer],
        facts: Sequence[FactAnswer],
    ) -> AdmittedRound:
        """The resumed job this round's answers start, or a ``ValueError``.

        ``sources``, ``earlier_links`` and ``earlier_facts`` are what the
        answered job was given. A refusal names the submitter's own choices,
        so its message is safe to show. A link answer to a job with no catalog
        raises :class:`~analysis_service.links.NoCatalogError`.
        """
        if not self.rounds_left:
            raise ValueError(
                f"this job's answers have run {MAX_ANSWER_ROUNDS} rounds, the most"
                " one job lineage takes, so its report is final"
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
            *resumed_sources(sources, earlier_links, links, earlier_facts, facts)
        )


def question_set(
    model: SystemModel,
    catalog: AssertionCatalog | None,
    frameworks: Mapping[FrameworkName, Mapping[str, Any]],
    analyses: Sequence[FrameworkAnalysis],
    *,
    waiting: bool,
    answered: Sequence[FactAnswer],
    rounds: int,
) -> QuestionSet:
    """The questions a job asks: a waiting job's early list, or its report's list.

    ``frameworks`` maps each selected framework to its options, and ranks the
    early list; ``analyses`` ranks the report's list. So a waiting job passes
    no analyses, and a finished one's frameworks go unread. ``answered`` is the
    fact answers of the earlier rounds, which are not asked again, and
    ``rounds`` is how many rounds the lineage took.
    """
    if rounds >= MAX_ANSWER_ROUNDS:
        return QuestionSet(model, catalog, waiting, (), (), (), rounds)
    done = answered_keys(answered)
    return QuestionSet(
        model=model,
        catalog=catalog,
        waiting=waiting,
        early=tuple(
            question
            for question in (
                early_questions(model, frameworks, catalog) if waiting else ()
            )
            if question.key not in done
        ),
        facts=fact_questions(analyses, model, catalog, answered),
        links=link_questions(catalog, model),
        rounds=rounds,
    )
