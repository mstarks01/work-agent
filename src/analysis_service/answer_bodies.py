"""The one reader of each body a job's answer routes take.

``POST /v1/jobs/{id}/answers``, ``/amendments`` and ``/corrections``, and the
first-run app's ``/answer``, ``/amend`` and ``/correct``, each take a JSON body.
Both drivers hand the parsed body to one of :func:`read_answers`,
:func:`read_amendment` and :func:`read_corrections`, and get back a typed
value or a :class:`BodyRefused`. So one site applies the rules a body meets:
the keys it may carry, the shape and bound of each field, and the strictness
of ``save`` and ``revision``. Each driver only maps a refusal to its own
transport: the ``/v1`` routes to a ``422`` problem that lists each place, the
first-run app to a page message.

**The split with admission.** This module reads shape and names the
**Question** a refused fact answer answers; it reads no job. Whether the
presented questions are listed, whether a skip is of a presented question,
whether the revision is current, and whether an answer fits its question are
rules of the job, and :class:`~analysis_service.job_answers.JobAnswers` applies
them after the body is read. The reader bounds each list and each field; it
does not bound the body's size. ``/v1`` bounds that with its body limit
middleware before the body is parsed, and the first-run app, which serves
only its own loopback page, applies no body limit on these routes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from analysis_service.answer_sets import SentAnswers
from analysis_service.answer_shares import SharedAnswer
from analysis_service.answer_state import MAX_SKIPS, Answers, Revision
from analysis_service.carried_renames import MAX_RENAMES, ElementRename
from analysis_service.claims import UnknownKey
from analysis_service.errors import refusal_text
from analysis_service.fact_answers import (
    MAX_FACT_ANSWERS,
    MAX_HELD_FACTS,
    FactAnswer,
    SkipKey,
)
from analysis_service.links import MAX_LINK_ANSWERS, LinkAnswer

__all__ = [
    "AmendmentSubmission",
    "AnswersSubmission",
    "BodyRefused",
    "CorrectionsSubmission",
    "read_amendment",
    "read_answers",
    "read_corrections",
]


class AnswersSubmission(BaseModel):
    """Answers to a job's questions, sent against that job.

    ``links`` answers which element a principal is, and ``facts`` answers the
    open facts a report's conditional findings rest on. Both empty is legal
    only for a job waiting on answers, where it means "continue without
    answers"; against a finished report it runs the follow-up on the saved
    draft alone. ``save`` keeps a waiting job's answers without starting the
    analysis (ADR 0078), or a finished report's draft without starting its
    follow-up (ADR 0070), and ``skip`` names questions of a waiting job's
    list the submitter skips for now: a fact by its key, a link question by
    its ``key`` (:data:`SkipKey`). ``presented`` names the early questions
    the page showed: the job records them as shown, and a skip is taken only
    of one of them (ADR 0069). ``revision`` is the revision the answers
    were read against, which a waiting job and every save require. ``shares``
    answers a question once for each element it covers (ADR 0114); each
    covered element counts as one fact answer toward the bound. ``renames`` is every
    rename a waiting job holds after a save: each maps an element a carried
    answer names to an element of this model (ADR 0116). Left out, the job
    keeps the renames it holds. One request carries at
    most the ``answer_limits`` the questions route publishes; a follow-up
    larger than that saves its draft in batches.
    """

    model_config = ConfigDict(extra="forbid")

    links: list[LinkAnswer] = Field(default_factory=list, max_length=MAX_LINK_ANSWERS)
    facts: list[FactAnswer] = Field(default_factory=list, max_length=MAX_FACT_ANSWERS)
    shares: list[SharedAnswer] = Field(
        default_factory=list, max_length=MAX_FACT_ANSWERS
    )
    # Strict, so that ``true`` is no revision and ``"yes"`` is no save.
    save: bool = Field(default=False, strict=True)
    skip: list[SkipKey] = Field(default_factory=list, max_length=MAX_SKIPS)
    presented: list[UnknownKey] = Field(default_factory=list, max_length=MAX_HELD_FACTS)
    revision: Revision | None = None
    renames: list[ElementRename] | None = Field(default=None, max_length=MAX_RENAMES)

    @model_validator(mode="after")
    def _within_the_fact_bound(self) -> AnswersSubmission:
        parts = len(self.facts) + sum(len(share.covers) for share in self.shares)
        if parts > MAX_FACT_ANSWERS:
            raise ValueError(
                f"one request answers at most {MAX_FACT_ANSWERS} facts, each part"
                f" a shared answer covers counted; this one answers {parts}"
            )
        return self


class AmendmentSubmission(BaseModel):
    """An amendment to a paused job's description (ADR 0072): what is true
    that the model misses or reads wrongly, in the submitter's own words.
    ``revision`` is the revision the questions were read with."""

    model_config = ConfigDict(extra="forbid")

    text: str
    revision: Revision


class CorrectionsSubmission(BaseModel):
    """Corrections to a final report's answers (ADR 0054): changed values, or
    "I don't know" in place of an answer that was a guess."""

    model_config = ConfigDict(extra="forbid")

    facts: list[FactAnswer] = Field(max_length=MAX_FACT_ANSWERS)
    #: The revision the questions were read with (ADR 0090).
    revision: Revision


class BodyRefused(Exception):
    """A body the reader refuses.

    ``errors`` are the pydantic errors, each with its place and rule and none
    with the input, so a driver can list them. ``question`` is the key of the
    fact Question the first refused fact answer answers, where that answer
    carries a well-formed key; a driver that knows the job names the question
    by its label. ``str()`` is the refusal as one sentence per place.
    """

    def __init__(self, error: ValidationError, question: UnknownKey | None) -> None:
        super().__init__(refusal_text(error))
        self.errors: list[Mapping[str, Any]] = [
            dict(detail) for detail in error.errors(include_input=False)
        ]
        self.question = question

    @property
    def reason(self) -> str:
        """What the rule said of the first refused place, in the rule's words."""
        return str(self.errors[0]["msg"]).removeprefix("Value error, ")

    @property
    def over_a_bound(self) -> bool:
        """Whether a list of the body is longer than the body admits."""
        return any(
            error["type"] == "too_long" and len(error["loc"]) == 1
            for error in self.errors
        )


_UNKNOWN_KEY: TypeAdapter[UnknownKey] = TypeAdapter(UnknownKey)


def _question_of(raw: object, error: Mapping[str, Any]) -> UnknownKey | None:
    """The key of the fact answer ``error`` sits in, where it is well formed."""
    loc = error["loc"]
    if len(loc) < 2 or loc[0] != "facts" or not isinstance(loc[1], int):
        return None
    facts = raw["facts"] if isinstance(raw, dict) else None
    fact = facts[loc[1]] if isinstance(facts, list) else None
    key = fact.get("key") if isinstance(fact, dict) else None
    try:
        return _UNKNOWN_KEY.validate_python(key)
    except ValidationError:
        return None


M = TypeVar("M", bound=BaseModel)


def _read(model: type[M], raw: object) -> M:
    try:
        return model.model_validate(raw)
    except ValidationError as error:
        raise BodyRefused(error, _question_of(raw, error.errors()[0])) from None


def read_answers(raw: object) -> Answers:
    """The answers a body carries, or a :class:`BodyRefused`."""
    body = _read(AnswersSubmission, raw)
    return Answers(
        given=SentAnswers(
            links=tuple(body.links), facts=tuple(body.facts), shares=tuple(body.shares)
        ),
        save=body.save,
        skips=tuple(body.skip),
        presented=tuple(body.presented),
        revision=body.revision,
        renames=None if body.renames is None else tuple(body.renames),
    )


def read_amendment(raw: object) -> AmendmentSubmission:
    """The amendment a body carries, or a :class:`BodyRefused`."""
    return _read(AmendmentSubmission, raw)


def read_corrections(raw: object) -> CorrectionsSubmission:
    """The corrections a body carries, or a :class:`BodyRefused`."""
    return _read(CorrectionsSubmission, raw)
