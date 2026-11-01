"""The link answers and fact answers that a job keeps together.

A job holds its answers in groups: the answers it was given, a report's draft,
the answers an amendment carried, and the carried answers its questions drop.
Each group has link answers and fact answers, and every reader takes both, so
one set holds them. It also holds the shared answers (ADR 0114).

**A set is in one of two states, and each state has its own type.** An
:class:`AnswerSet` holds each share's answer written as each covered part's
own fact answer, which is what admission stores and what every reader of a
job's answers reads. A :class:`SentAnswers` holds its shares as a request
sent them: a submission's answers, and a report's draft, which admission
checks again at each save and at the run. :meth:`SentAnswers.written` is the
one step from the second state to the first, so the type checker refuses a
draft where a reader needs the written answers.
"""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import AfterValidator, BaseModel, ConfigDict

from analysis_service.answer_shares import SharedAnswer, merged_shares
from analysis_service.fact_answers import MAX_HELD_FACTS, FactAnswer, merged_facts
from analysis_service.links import MAX_HELD_LINKS, LinkAnswer, merged_links


class _Answers(BaseModel):
    """One group of link answers, fact answers and shared answers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    links: tuple[LinkAnswer, ...] = ()
    facts: tuple[FactAnswer, ...] = ()
    shares: tuple[SharedAnswer, ...] = ()

    @property
    def empty(self) -> bool:
        """True where the set holds no answer."""
        return not (self.links or self.facts or self.shares)

    def over(self, earlier: Self) -> Self:
        """These answers over ``earlier``, a set in the same state: each one
        replaces the earlier answer to its question (:func:`merged_links`,
        :func:`merged_facts`), and each share the earlier share of its shape
        (:func:`merged_shares`). An answer a share takes back, and an earlier
        answer of a part a share here covers, leave ``earlier``."""
        shares, taken_back = merged_shares(earlier.shares, self.shares, self.facts)
        gone = taken_back.union(*(share.keys() for share in self.shares))
        kept = [fact for fact in earlier.facts if fact.key not in gone]
        return type(self)(
            links=tuple(merged_links(earlier.links, self.links)),
            facts=tuple(merged_facts(kept, self.facts)),
            shares=shares,
        )


class AnswerSet(_Answers):
    """Answers with each share's answer written as each covered part's own."""


class SentAnswers(_Answers):
    """Answers with each share held as a request sent it."""

    def written(self) -> AnswerSet:
        """These answers with each share's answer written as each covered
        part's own (:meth:`~analysis_service.answer_shares.SharedAnswer.facts`)."""
        written = [fact for share in self.shares for fact in share.facts()]
        return AnswerSet(
            links=self.links,
            facts=tuple(merged_facts(self.facts, written)),
            shares=self.shares,
        )


def within_held_limits(answers: AnswerSet) -> AnswerSet:
    """``answers``, or a ``ValueError`` where they break the limits a job holds.

    **The one reader of the held limits.** A submission and a stored job
    record both ask it. Each part a share covers counts as a fact answer.
    """
    facts = len(answers.facts)
    if (
        facts > MAX_HELD_FACTS
        or len(answers.links) > MAX_HELD_LINKS
        or len(answers.shares) > MAX_HELD_FACTS
    ):
        raise ValueError(
            f"a job holds at most {MAX_HELD_FACTS} fact answers, each part a"
            f" shared answer covers counted, {MAX_HELD_LINKS} link answers and"
            f" {MAX_HELD_FACTS} shared answers; these answers would make"
            f" {facts}, {len(answers.links)} and {len(answers.shares)}"
        )
    return answers


def _sent_within_held_limits(answers: SentAnswers) -> SentAnswers:
    """``answers``, or a ``ValueError`` where their written form breaks the
    limits a job holds (:func:`within_held_limits`)."""
    within_held_limits(answers.written())
    return answers


#: An :class:`AnswerSet` a job record stores, within the limits a job holds.
HeldAnswerSet = Annotated[AnswerSet, AfterValidator(within_held_limits)]

#: A :class:`SentAnswers` a job record stores, within the limits a job holds.
HeldSentAnswers = Annotated[SentAnswers, AfterValidator(_sent_within_held_limits)]


#: The set with no answer, which a holder starts from.
NO_ANSWERS = AnswerSet()

#: The sent set with no answer.
NO_SENT_ANSWERS = SentAnswers()
