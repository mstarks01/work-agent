"""The link answers and fact answers that a job keeps together.

A job holds its answers in groups: the answers it was given, a report's draft,
the answers an amendment carried, and the carried answers its questions drop.
Each group has link answers and fact answers, and every reader takes both, so
one :class:`AnswerSet` holds them. It also holds the shared answers that wrote
some of its fact answers (ADR 0114).
"""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict

from analysis_service.answer_shares import SharedAnswer, merged_shares
from analysis_service.fact_answers import MAX_HELD_FACTS, FactAnswer, merged_facts
from analysis_service.links import MAX_HELD_LINKS, LinkAnswer, merged_links


class AnswerSet(BaseModel):
    """One group of link answers and fact answers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    links: tuple[LinkAnswer, ...] = ()
    facts: tuple[FactAnswer, ...] = ()
    shares: tuple[SharedAnswer, ...] = ()

    @property
    def empty(self) -> bool:
        """True where the set holds no answer."""
        return not (self.links or self.facts or self.shares)

    def over(self, earlier: AnswerSet) -> AnswerSet:
        """These answers over ``earlier``: each one replaces the earlier
        answer to its question (:func:`merged_links`, :func:`merged_facts`),
        and each share the earlier share of its shape (:func:`merged_shares`).
        An answer a share takes back, and an earlier answer of a part a share
        here covers, leave ``earlier``."""
        shares, taken_back = merged_shares(earlier.shares, self.shares, self.facts)
        gone = taken_back.union(*(share.keys() for share in self.shares))
        kept = [fact for fact in earlier.facts if fact.key not in gone]
        return AnswerSet(
            links=tuple(merged_links(earlier.links, self.links)),
            facts=tuple(merged_facts(kept, self.facts)),
            shares=shares,
        )

    def expanded(self) -> AnswerSet:
        """These answers with each share's answer written as each covered
        part's own (:meth:`~analysis_service.answer_shares.SharedAnswer.facts`).

        A held set is written so already; a draft holds its shares as sent.
        """
        written = [fact for share in self.shares for fact in share.facts()]
        return self.model_copy(
            update={"facts": tuple(merged_facts(self.facts, written))}
        )


def within_held_limits(answers: AnswerSet) -> AnswerSet:
    """``answers``, or a ``ValueError`` where they break the limits a job holds.

    **The one reader of the held limits.** A submission and a stored job
    record both ask it. Each part a share covers counts as a fact answer, so
    a draft, which holds its shares as sent, counts as a held set does.
    """
    facts = len(answers.expanded().facts)
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


#: An :class:`AnswerSet` a job record stores, within the limits a job holds.
HeldAnswerSet = Annotated[AnswerSet, AfterValidator(within_held_limits)]


#: The set with no answer, which a holder starts from.
NO_ANSWERS = AnswerSet()
