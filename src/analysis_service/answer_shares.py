"""A shared answer: one answer a person gives once for each element it covers (ADR 0114).

A page asks a box of same-shape questions once, and a person ticks the elements
the answer covers (ADR 0113). :class:`SharedAnswer` stores that answer with
the exact elements it covers, so a reload, a resume, the API and the export keep
the coverage and its exceptions.

**The service writes each covered element's answer when it admits the share.**
:meth:`SharedAnswer.facts` gives one fact answer for each covered element, and
admission checks each one as it checks any other. So every reader of a job's
fact answers reads a covered element's answer as that element's own, and nothing
else reads a share to learn a fact.

**A covered element holds the share's answer and nothing else.** A submission
that gives a covered element its own answer as well is refused
(:func:`check_shares`). A later answer of an element's own takes the element out
of every earlier share: the exception wins in its own scope
(:func:`merged_shares`). An element that leaves a share loses only the answer
the share wrote.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator

from analysis_service.claims import UnknownKey
from analysis_service.fact_answers import (
    MAX_FACT_ANSWERS,
    FactAnswer,
    key_ref,
    with_element,
)

__all__ = ["SharedAnswer", "check_shares", "merged_shares"]


class SharedAnswer(BaseModel):
    """One answer and the elements it covers.

    ``answer`` carries the question's shape as its key: the key of each
    element's question with the element left empty. ``covers`` names the elements, each
    once. An empty ``covers`` takes the share back from every element it covered.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    answer: FactAnswer
    covers: tuple[str, ...] = Field(max_length=MAX_FACT_ANSWERS)

    @field_validator("answer")
    @classmethod
    def _a_shape(cls, answer: FactAnswer) -> FactAnswer:
        ref = key_ref(answer.key)
        if ref.element_id or not {"attribute", "question"} & set(ref.spellings):
            raise ValueError(
                "a shared answer's key names an attribute or a question kind"
                " and leaves the element empty"
            )
        return answer

    @field_validator("covers")
    @classmethod
    def _each_element_once(cls, covers: tuple[str, ...]) -> tuple[str, ...]:
        if any(not element for element in covers):
            raise ValueError("a shared answer covers named elements")
        twice = sorted(element for element, n in Counter(covers).items() if n > 1)
        if twice:
            raise ValueError(f"a shared answer covers each element once: {twice}")
        return covers

    @property
    def shape(self) -> UnknownKey:
        """The key of the question it answers, with the element empty."""
        return self.answer.key

    def key_of(self, element: str) -> UnknownKey:
        """The key of ``element``'s question of this shape."""
        return with_element(self.shape, element)

    def keys(self) -> frozenset[UnknownKey]:
        """The key of each covered element's question."""
        return frozenset(self.key_of(element) for element in self.covers)

    def facts(self) -> tuple[FactAnswer, ...]:
        """One fact answer for each covered element: the share's answer, as its own."""
        return tuple(
            self.answer.model_copy(update={"key": self.key_of(element)})
            for element in self.covers
        )


def check_shares(shares: Sequence[SharedAnswer], facts: Sequence[FactAnswer]) -> None:
    """Raise a ``ValueError`` where one submission's shares and answers clash.

    Two shares of one shape, or an element a share covers that the submission
    also answers on its own, each leave which answer holds to the order of a
    list, so the service refuses them and names the clash.
    """
    shapes = Counter(share.shape for share in shares)
    twice = sorted(shape for shape, n in shapes.items() if n > 1)
    if twice:
        raise ValueError(f"one submission shares each question once: {twice}")
    covered = frozenset().union(*(share.keys() for share in shares))
    both = sorted(fact.key for fact in facts if fact.key in covered)
    if both:
        raise ValueError(
            f"an element takes either a shared answer or its own answer, not both:"
            f" {both}"
        )


def merged_shares(
    earlier: Sequence[SharedAnswer],
    shares: Sequence[SharedAnswer],
    facts: Sequence[FactAnswer],
) -> tuple[tuple[SharedAnswer, ...], frozenset[UnknownKey]]:
    """The shares after a submission, and the keys whose shared answer it takes back.

    A submitted share replaces the earlier share of its shape; an element the
    earlier share covered and the new one does not loses the answer the share
    wrote. An answer ``facts`` gives an element of its own takes the element out of
    every earlier share, and keeps its own answer. A share that covers nothing
    is dropped.
    """
    replaced = {share.shape: share for share in shares}
    own = {fact.key for fact in facts}
    taken_back: set[UnknownKey] = set()
    kept = []
    for share in earlier:
        if share.shape in replaced:
            taken_back |= share.keys() - replaced[share.shape].keys()
            continue
        covers = tuple(e for e in share.covers if share.key_of(e) not in own)
        kept.append(share.model_copy(update={"covers": covers}))
    merged = tuple(share for share in (*kept, *shares) if share.covers)
    return merged, frozenset(taken_back - own)
