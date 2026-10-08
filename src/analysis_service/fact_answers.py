"""What an answer to one open fact is: its key, its kind, and its line.

A ``needs-info`` verdict names the facts an answer would settle (#1225). A
:class:`FactAnswer` answers one of them, and every surface that takes answers
reads it through this module.

**Five kinds of fact, one key.** A key names one fact in one of five
spellings, as :attr:`~analysis_service.claims.UnknownRef.spellings` reads
them, and :func:`fact_kind` says which.

* An **attribute** of an element the model left ``unknown``. The answer is
  written onto the model the resumed run analyses, and the element's notes say
  the submitter answered it.
* An **assertion** row the sources left open. The answer replaces the row with
  a stated one, quoting its line of the answers Source.
* A **question** of one of the kinds in
  :data:`~analysis_service.question_kinds.QUESTION_KINDS`, about one element,
  or a free-text **subject** where no kind fits. Nothing structured can hold
  either answer, so it reaches the lanes only as its line of the answers
  Source.
* A **capability** of the whole application, answered yes or no. The answer
  replaces what the sources stated about it with a statement on the model
  that quotes its line of the answers Source.

**An answer's line fits the span that quotes it.** :func:`fact_line` writes
each answer as one line of the answers Source, and the admission check
refuses a line longer than
:data:`~analysis_service.assertions.MAX_QUOTE_CHARS`.

**An answer of** ``unknown`` **says the submitter does not know.** It writes
nothing structured, the fact stays open, and it covers no finding. It reaches
the lanes as its line of the answers Source.

An answer to a question this service asked settles the fact, even against the
sources (the maintainer's decision of 2026-09-25 on #1225).
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from analysis_service.assertions import (
    UNKNOWN,
    Assertion,
    AssertionCatalog,
    answered,
    assertion_id,
)
from analysis_service.capabilities import CAPABILITIES
from analysis_service.claims import UnknownKey, UnknownRef
from analysis_service.open_facts import element_names, label_of
from analysis_service.question_kinds import QUESTION_KINDS, Facet
from analysis_service.sources import plain_name
from analysis_service.system_model import SystemModel

__all__ = [
    "FACET_ANSWERS",
    "MAX_FACT_ANSWERS",
    "MAX_HELD_FACTS",
    "FactAnswer",
    "FactKind",
    "FactStatus",
    "answer_facets",
    "answered_keys",
    "answered_row",
    "covers",
    "fact_kind",
    "fact_label",
    "fact_line",
    "fact_status",
    "key_ref",
    "merged_facts",
    "needed_facets",
    "needs_of",
    "refuse_repeated_facts",
]


#: How many fact answers one submission carries. The largest of the 202
#: archived reports asks 97 fact questions, so this bounds the body above any
#: real use. A report
#: can ask more than this, so its follow-up saves its answers in batches
#: (ADR 0070).
MAX_FACT_ANSWERS = 200

#: How many fact answers one job holds across every save: the answers the
#: pause and the report's follow-up gathered, and the facts its pause showed.
#: The largest archived job can hold 359: 262 questions a pause lists (case 09,
#: both frameworks) and 97 a report asks. At this ceiling one list of the
#: widest answers is 1.7 MB and validates in 0.11 s; at ten times it, 16.7 MB
#: and 1.1 s. A job record holds four such lists, and each read validates
#: them. The deployment's source limits bound the answers' text as well,
#: because every answer is a line of the answers Source.
MAX_HELD_FACTS = 5 * MAX_FACT_ANSWERS

#: The longest detail one answer carries: a sentence, not a description.
MAX_DETAIL_CHARS = 300


FactKind = Literal["attribute", "assertion", "question", "subject", "capability"]


#: The fields of :class:`~analysis_service.claims.UnknownRef`, in key order.
_KEY_FIELDS = (
    "element_id",
    "attribute",
    "assertion",
    "subject",
    "question",
    "capability",
)


#: The answers a facet takes, beside ``unknown``.
FACET_ANSWERS: tuple[str, ...] = ("yes", "no", "not applicable")


class FactAnswer(BaseModel):
    """One submitter answer to one open fact, named by the fact's key."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: UnknownKey
    value: str = Field(min_length=1, max_length=1000)
    #: A ``facets`` kind's answer: each facet's ID and one of
    #: :data:`FACET_ANSWERS` or ``unknown``. The service writes ``value`` from
    #: it, so a submission sends one or the other.
    facets: dict[str, str] | None = None
    #: The submitter's own words beside a closed answer: an exception, a scope
    #: or a detail the answer alone would overstate, such as "yes, except the
    #: public status endpoint" (ADR 0073). It is part of the answer's line of
    #: the answers Source, so the analysis reads it as the submitter's words;
    #: no code reads a fact out of it.
    detail: str = Field(default="", max_length=MAX_DETAIL_CHARS)

    @model_validator(mode="before")
    @classmethod
    def _facet_value(cls, data: Any) -> Any:
        """Write ``value`` from ``facets``, one facet a clause, in the kind's order."""
        if not isinstance(data, dict) or data.get("facets") is None:
            return data
        facets, key = data["facets"], data.get("key")
        question = ""
        if isinstance(key, list | tuple) and len(key) == len(_KEY_FIELDS):
            question = dict(zip(_KEY_FIELDS, key, strict=True))["question"]
        kind = QUESTION_KINDS.get(question)
        if kind is None or kind.answer != "facets":
            raise ValueError("facets answer only a question kind that has facets")
        if not isinstance(facets, dict) or not facets:
            raise ValueError("a facet answer names at least one facet")
        ids = {facet.id for facet in kind.facets}
        for facet, answer in facets.items():
            if facet not in ids:
                raise ValueError(f"no facet {facet!r} of {question!r}")
            if answer not in (*FACET_ANSWERS, UNKNOWN):
                raise ValueError(
                    f"{answer!r} is not one of {', '.join(FACET_ANSWERS)} or"
                    f" {UNKNOWN} for {facet!r}"
                )
        given = [facet for facet in kind.facets if facet.id in facets]
        known = any(facets[facet.id] != UNKNOWN for facet in given)
        value = (
            "; ".join(f"{facet.question} {facets[facet.id]}" for facet in given)
            if known
            else UNKNOWN
        )
        if data.get("value") not in (None, value):
            raise ValueError("the service writes a facet answer's value")
        return {**data, "value": value}

    @field_validator("key", mode="before")
    @classmethod
    def _one_spelling(cls, key: Any) -> Any:
        """Bound each part, and blank every part the fact's spelling does not read.

        Two keys that differ only in a part nobody reads name one fact, and
        :func:`merged_facts` would keep both. A key that
        uses more than one spelling is left as sent, for the check to refuse.
        """
        if not isinstance(key, list | tuple) or len(key) != 6:
            return key
        ref = UnknownRef.model_validate(dict(zip(_KEY_FIELDS, key, strict=True)))
        match ref.spellings:
            case ("question",):
                return (ref.element_id, "", "", "", ref.question, "")
            case ("attribute",):
                return (ref.element_id, ref.attribute, "", "", "", "")
            case ("assertion",):
                return ("", "", ref.assertion, "", "", "")
            case ("subject",):
                return ("", "", "", ref.subject, "", "")
            case ("capability",):
                return ("", "", "", "", "", ref.capability)
        return ref.key

    @field_validator("value")
    @classmethod
    def _one_line(cls, value: str) -> str:
        return plain_name(value)

    @field_validator("detail")
    @classmethod
    def _one_line_detail(cls, detail: str) -> str:
        return plain_name(detail) if detail.strip() else ""

    @property
    def kind(self) -> FactKind:
        return fact_kind(self.key)

    @property
    def known(self) -> bool:
        """False where the submitter answered that they do not know."""
        return self.value != UNKNOWN

    @property
    def settles(self) -> bool:
        """True where the answer settles its fact for a finding that waits on it.

        A facet answer settles it only once every facet has an answer other
        than "I don't know": a finding names the kind, not a facet, so a
        facet left unknown can be the one it waits on.
        """
        if self.facets is None:
            return self.known
        return all(
            self.facets.get(facet.id, UNKNOWN) != UNKNOWN
            for facet in answer_facets(self.key)
        )


def key_ref(key: UnknownKey) -> UnknownRef:
    """The key as an unvalidated :class:`~analysis_service.claims.UnknownRef`.

    **The one reader of "which part of a key is which".** A caller reads the
    key's parts by name, or its :attr:`~analysis_service.claims.UnknownRef.spellings`.
    """
    element_id, attribute, assertion, subject, question, capability = key
    return UnknownRef.model_construct(
        element_id=element_id,
        attribute=attribute,
        assertion=assertion,
        subject=subject,
        question=question,
        capability=capability,
    )


def fact_kind(key: UnknownKey) -> FactKind:
    """Which of the five kinds of fact this key names."""
    element_id, attribute, assertion, _, question, capability = key
    if capability:
        return "capability"
    if question:
        return "question"
    if assertion:
        return "assertion"
    return "attribute" if element_id or attribute else "subject"


def fact_label(key: UnknownKey, model: SystemModel) -> str:
    """What a person reads for one fact: the label its question shows.

    A refusal names a fact by this rather than by its six-part key, which
    means nothing on a page.
    """
    return label_of(key_ref(key), element_names(model))


def fact_line(fact: FactAnswer) -> str:
    """The answer as its line of the answers Source, which its span quotes.

    A detail follows the answer in the submitter's own words (ADR 0073).
    """
    element_id, attribute, assertion, subject, question, capability = fact.key
    if fact.kind == "capability":
        asked = CAPABILITIES[capability].question
        line = f'Asked "{asked}", the answer is "{fact.value}".'
    elif fact.kind == "question":
        kind = QUESTION_KINDS[question]
        asked = kind.template.format(element=element_id)
        line = f'Asked "{asked}", the answer is "{fact.value}".'
    elif fact.kind == "attribute":
        line = f'The {attribute} of {element_id} is "{fact.value}".'
    elif fact.kind == "assertion":
        line = f'The open question {assertion} is answered "{fact.value}".'
    else:
        line = f'Asked "{subject}", the answer is "{fact.value}".'
    if fact.detail:
        line += f' The submitter adds: "{fact.detail}".'
    return line


def answer_facets(key: UnknownKey) -> tuple[Facet, ...]:
    """The facets a question kind is answered in, or empty."""
    kind = (
        QUESTION_KINDS.get(key_ref(key).question)
        if fact_kind(key) == "question"
        else None
    )
    return () if kind is None else kind.facets


def answered_row(catalog: AssertionCatalog, identity: str) -> Assertion | None:
    """The row an answer to the open row ``identity`` writes over, if any.

    **The one reader of "which row does this answer settle".** That is the open
    row itself, or the stated row an earlier answer wrote in its place. The
    second is the row a resumed job's checkpoint holds, and a later round
    writes it again, so that its quote reads the answers Source this round
    composed. A row the sources settled is neither, so no answer names it.
    """
    for entry in catalog.entries:
        if entry.value == UNKNOWN:
            reopened = entry
        elif answered(entry):
            reopened = entry.model_copy(update={"value": UNKNOWN})
        else:
            continue
        if assertion_id(reopened) == identity:
            return entry
    return None


def refuse_repeated_facts(facts: Sequence[FactAnswer]) -> None:
    """Refuse two answers to one open fact in one submission.

    Refused rather than resolved by order: the service cannot know which of the
    two the submitter meant.
    """
    keys = [fact.key for fact in facts]
    if len(set(keys)) != len(keys):
        raise ValueError("facts answers one open fact twice")


def merged_facts(
    earlier: Sequence[FactAnswer], later: Sequence[FactAnswer]
) -> list[FactAnswer]:
    """A resumed job's fact answers: the parent's, with the new ones over them.

    **A facet answer adds to the earlier one.** A page sends only the facets
    the submitter chose, so a facet the later answer leaves out keeps its
    earlier answer, and a later answer to a facet replaces it. Every other
    answer replaces the earlier answer to its fact.
    """
    refuse_repeated_facts(later)
    merged = {fact.key: fact for fact in earlier}
    for fact in later:
        before = merged.get(fact.key)
        if fact.facets is not None and before is not None and before.facets:
            # A later facet answer that adds facets keeps the earlier detail
            # unless it gives one of its own.
            fact = FactAnswer.model_validate(
                {
                    "key": fact.key,
                    "facets": {**before.facets, **fact.facets},
                    "detail": fact.detail or before.detail,
                }
            )
        merged[fact.key] = fact
    return list(merged.values())


def answered_keys(answers: Sequence[FactAnswer]) -> frozenset[UnknownKey]:
    """The facts these answers answer in full, which a later round does not ask.

    An answer of "I don't know" answers its fact: asking it again in every
    round would ask a submitter who does not know until they stop. A facet
    answer answers its fact once every facet has an answer, "I don't know"
    included, so a later round still asks the facets left out.
    """
    return frozenset(
        answer.key
        for answer in answers
        if answer.facets is None
        or {facet.id for facet in answer_facets(answer.key)} <= answer.facets.keys()
    )


#: What became of one fact before the analysis: an answer said "I don't
#: know", an answer gave some facets and left the rest open, the submitter
#: skipped it, the pause showed it and it was left blank, an answer was given
#: and the analysis still found the finding open, or it was never shown.
FactStatus = Literal["unknown", "partial", "skipped", "unanswered", "answered", "open"]


def fact_status(
    key: UnknownKey,
    said: Mapping[UnknownKey, FactAnswer],
    shown: Collection[UnknownKey],
    skipped: Collection[object],
    facets: Sequence[str] = (),
) -> FactStatus:
    """What became of one fact before the analysis (#1542 F4).

    **The one reader of a fact's history**, which a report's conditions and
    its follow-up questions both read. ``said`` is every answer the run read,
    by key, ``shown`` every early question a page presented, and ``skipped``
    every question the submitter skipped and did not answer since. A skip is
    the submitter's act and is told apart from a question left blank, and a
    part hidden under its parent's answer was never presented, so it reads
    ``open``. ``facets`` are the parts a finding needs (:func:`needs_of`): an
    answer that covers them is ``answered`` for that finding, though other
    parts stay open.
    """
    answer = said.get(key)
    if answer is None:
        if key in skipped:
            return "skipped"
        return "unanswered" if key in shown else "open"
    if covers(answer, facets):
        return "answered"
    return "partial" if answer.known else "unknown"


def needed_facets(ref: UnknownRef) -> tuple[str, ...]:
    """The facets of its question kind a dependency waits on, in the kind's order.

    **The one reader of "which part of a question does a finding need"** (ADR
    0073). The facets the reference names that its kind has; where it names
    none of them, every facet the kind has, so the finding waits on the whole
    question. Empty for a fact answered in one value.
    """
    every = tuple(facet.id for facet in answer_facets(ref.key))
    named = tuple(facet for facet in every if facet in ref.facets)
    return named or every


def needs_of(refs: Sequence[UnknownRef]) -> dict[UnknownKey, tuple[str, ...]]:
    """Each fact these references name, with the facets a finding needs of it.

    A fact named twice needs the facets of both, so a reference to the whole
    question makes the finding wait on the whole question.
    """
    needs: dict[UnknownKey, set[str]] = {}
    for ref in refs:
        needs.setdefault(ref.key, set()).update(needed_facets(ref))
    return {
        key: tuple(facet.id for facet in answer_facets(key) if facet.id in held)
        for key, held in needs.items()
    }


def covers(answer: FactAnswer | None, facets: Sequence[str]) -> bool:
    """True where ``answer`` gives every one of ``facets`` an answer other than
    "I don't know"; with no facets, where it settles its fact."""
    if answer is None:
        return False
    if not facets or answer.facets is None:
        return answer.settles
    return all(answer.facets.get(facet, UNKNOWN) != UNKNOWN for facet in facets)
