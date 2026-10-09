"""The link answers and fact answers that a job keeps together.

A job holds its answers in groups: the answers it was given, a report's draft,
the answers an amendment carried, and the carried answers its questions drop.
Each group has link answers and fact answers, and every reader takes both, so
one :class:`AnswerSet` holds them.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict

from analysis_service.fact_answers import MAX_HELD_FACTS, FactAnswer, merged_facts
from analysis_service.links import MAX_HELD_LINKS, LinkAnswer, merged_links


class AnswerSet(BaseModel):
    """One group of link answers and fact answers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    links: tuple[LinkAnswer, ...] = ()
    facts: tuple[FactAnswer, ...] = ()

    @property
    def empty(self) -> bool:
        """True where the set holds no answer."""
        return not (self.links or self.facts)

    def over(self, earlier: AnswerSet) -> AnswerSet:
        """These answers over ``earlier``: each one replaces the earlier
        answer to its question (:func:`merged_links`, :func:`merged_facts`)."""
        return AnswerSet(
            links=tuple(merged_links(earlier.links, self.links)),
            facts=tuple(merged_facts(earlier.facts, self.facts)),
        )


def within_held_limits(answers: AnswerSet) -> AnswerSet:
    """``answers``, or a ``ValueError`` where they break the limits a job holds.

    **The one reader of the held limits.** A submission and a stored job
    record both ask it.
    """
    if len(answers.facts) > MAX_HELD_FACTS or len(answers.links) > MAX_HELD_LINKS:
        raise ValueError(
            f"a job holds at most {MAX_HELD_FACTS} fact answers and"
            f" {MAX_HELD_LINKS} link answers; these answers would make"
            f" {len(answers.facts)} and {len(answers.links)}"
        )
    return answers


#: An :class:`AnswerSet` a job record stores, within the limits a job holds.
HeldAnswerSet = Annotated[AnswerSet, AfterValidator(within_held_limits)]


#: The set with no answer, which a holder starts from.
NO_ANSWERS = AnswerSet()
