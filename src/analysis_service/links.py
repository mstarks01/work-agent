"""Link questions: which element of the model a principal is, asked and answered.

A fact about a principal reaches a graph element only through a stated
``represented-by`` row, and the sources almost never state one: over the
archive, 443 of 444 settled principal rows reached no element
(QA-2026-09-26-03-E2). The lanes still read those rows as evidence, but no
candidate rule and no scope check can place them. So a report asks the
submitter, one principal at a time, and a later job takes the answers back.

**The whole rule lives here, and nothing else reads it.** The questions, the
fold that decides two spellings of a principal are one, the Source the answers
become, and the rows an answer writes are four readers of one rule, so they are
one module.

**An answer is closed.** It names one component of the model, or
:data:`NONE_OF_THESE`. No model reads it: code composes the answers into a
Source so the gate can check the quote like any other, and code writes the
``represented-by`` row that quote supports. An answer to a question this
service asked settles the fact (#1225, the maintainer's decision of
2026-09-25), so the row replaces any ``represented-by`` row the ``assert`` node
wrote for that principal.

**A question is derived, never stored.** :func:`link_questions` reads a
report's own catalog and model, as :mod:`analysis_service.open_facts` does, so
it cannot drift from the rows it asks about.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, field_validator

from analysis_service.assertions import (
    ABSENT,
    SUBJECT_PREFIXES,
    Assertion,
    AssertionCatalog,
    CatalogIssue,
    Subject,
    SupportSpan,
    answer,
    settled,
)
from analysis_service.claims import UnknownKey
from analysis_service.questions import (
    ANSWERS_LABEL,
    FactAnswer,
    check_fact_answers,
    fact_line,
    fact_rows,
    merged_facts,
    refuse_repeated_facts,
)
from analysis_service.sources import Source, plain_name, text_digest
from analysis_service.system_model import PLAIN_ID_RE, SystemModel

__all__ = [
    "ANSWERS_LABEL",
    "MAX_LINK_ANSWERS",
    "NONE_OF_THESE",
    "LinkAnswer",
    "LinkQuestion",
    "NoCatalogError",
    "apply_answers",
    "check_answers",
    "fold",
    "link_questions",
    "merged_facts",
    "merged_links",
    "resumed_sources",
    "with_link_answers",
]

#: The answer that says a principal is no element of the model. It is a real
#: answer: "anything that can reach the service" is a population, not a
#: component, and saying so stops the question being asked again.
NONE_OF_THESE = "none"

#: How many answers one submission carries. The archive asks at most five link
#: questions a report, so this bounds the body far above any real use.
MAX_LINK_ANSWERS = 50

_WORD = re.compile(r"[a-z0-9]+")


def fold(name: str) -> str:
    """One spelling for every way the runs wrote one principal.

    Case, punctuation, a possessive and a plural all vary between runs of the
    ``assert`` node ("calling team", "calling teams"; "model server's service
    account"), and none of them names a different principal.
    """
    words = _WORD.findall(name.lower().replace("'s", ""))
    return " ".join(word.removesuffix("s") if len(word) > 3 else word for word in words)


class LinkAnswer(BaseModel):
    """One submitter answer: this principal is that component, or none of them."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    principal: str = Field(min_length=1, max_length=200)
    element: str = Field(min_length=1, max_length=200)

    @field_validator("principal")
    @classmethod
    def _one_line(cls, value: str) -> str:
        return plain_name(value)

    @field_validator("element")
    @classmethod
    def _a_component_or_none(cls, value: str) -> str:
        prefix = value.split(":", 1)[0]
        if value != NONE_OF_THESE and (
            not PLAIN_ID_RE.fullmatch(value)
            or prefix not in SUBJECT_PREFIXES["component"]
        ):
            raise ValueError(
                f"names a component ({', '.join(sorted(SUBJECT_PREFIXES['component']))})"
                f" by its element ID, or is {NONE_OF_THESE!r}"
            )
        return value


def _line(link: LinkAnswer) -> str:
    target = (
        "none of the elements in the model"
        if link.element == NONE_OF_THESE
        else link.element
    )
    return f'"{link.principal}" is {target}.'


Spans = tuple[tuple[int, int], ...]


def _composed(
    links: Sequence[LinkAnswer], facts: Sequence[FactAnswer] = ()
) -> tuple[str, Spans, Spans]:
    """The answers Source's text, and where each answer's line sits in it."""
    lines = [*map(_line, links), *map(fact_line, facts)]
    spans, at = [], 0
    for line in lines:
        spans.append((at, at + len(line)))
        at += len(line) + 1
    return "\n".join(lines), tuple(spans[: len(links)]), tuple(spans[len(links) :])


def _refuse_repeats(links: Sequence[LinkAnswer]) -> None:
    """Refuse two answers about one principal in one submission.

    Refused rather than resolved by order: the service cannot know which of the
    two the submitter meant.
    """
    principals = [fold(link.principal) for link in links]
    repeated = sorted({key for key in principals if principals.count(key) > 1})
    if repeated:
        raise ValueError(f"links answers one principal twice: {', '.join(repeated)}")


def merged_links(
    earlier: Sequence[LinkAnswer], later: Sequence[LinkAnswer]
) -> list[LinkAnswer]:
    """A resumed job's answers: the parent's, with the new ones over them.

    A later answer about a principal replaces an earlier one, because the
    submitter gave it knowing the report the earlier answer produced.
    """
    _refuse_repeats(later)
    merged = {fold(link.principal): link for link in earlier}
    merged.update({fold(link.principal): link for link in later})
    return list(merged.values())


def with_link_answers(
    sources: Sequence[Source],
    links: Sequence[LinkAnswer],
    facts: Sequence[FactAnswer] = (),
) -> list[Source]:
    """A job's sources, with its link answers composed into one more.

    A caller may not submit an ``answers`` Source of its own. The service
    composes it, so the text an answer's row quotes is always the text code
    wrote for that answer, and an entry point that takes no answers still
    refuses one.
    """
    if any(source.kind == "answers" for source in sources):
        raise ValueError(
            "an answers source is composed by this service from 'links';"
            " submit the answers there"
        )
    if not links and not facts:
        return list(sources)
    _refuse_repeats(links)
    refuse_repeated_facts(facts)
    text, _, _ = _composed(links, facts)
    return [*sources, Source(kind="answers", label=ANSWERS_LABEL, text=text)]


def resumed_sources(
    parent_sources: Sequence[Source],
    parent_links: Sequence[LinkAnswer],
    links: Sequence[LinkAnswer],
    parent_facts: Sequence[FactAnswer] = (),
    facts: Sequence[FactAnswer] = (),
) -> tuple[list[Source], list[LinkAnswer], list[FactAnswer]]:
    """A resumed job's sources and answers, from its parent's and the new ones.

    **The one reader of "what does a resumed job carry".** The HTTP route and
    the in-process engine both resume, and each would otherwise drop the
    parent's answers Source and compose a new one its own way. The new answers
    go over the parent's, and the answers Source is composed from the merged
    set.
    """
    merged = merged_links(parent_links, links)
    answered = merged_facts(parent_facts, facts)
    kept = [source for source in parent_sources if source.kind != "answers"]
    return with_link_answers(kept, merged, answered), merged, answered


@dataclass(frozen=True)
class LinkQuestion:
    """One principal the report cannot place, and what an answer would place."""

    #: :func:`fold` of the principal's name; one question per value.
    key: str
    #: The principal as the report first spelled it.
    principal: str
    #: How many settled rows about this principal an answer would place.
    rows: int
    #: The components an answer may name, in model order.
    options: tuple[str, ...]

    def to_json(self) -> dict[str, object]:
        return {
            "key": self.key,
            "principal": self.principal,
            "rows": self.rows,
            "options": list(self.options),
        }


def _components(model: SystemModel) -> tuple[str, ...]:
    prefixes = SUBJECT_PREFIXES["component"]
    return tuple(
        element.id
        for element in model.elements()
        if element.id.split(":", 1)[0] in prefixes
    )


def link_questions(
    catalog: AssertionCatalog | None, model: SystemModel
) -> tuple[LinkQuestion, ...]:
    """Every principal with a settled fact and no link, the most rows first.

    A principal already answered, by a source or by an earlier answer, holds a
    settled ``represented-by`` row and is not asked again; "none of these"
    settles one as ``absent``.
    """
    if catalog is None:
        return ()
    rows: dict[str, int] = defaultdict(int)
    first: dict[str, str] = {}
    linked: set[str] = set()
    for subject in catalog.subjects:
        if subject.type != "principal":
            continue
        key = fold(subject.label)
        if answer(catalog, subject.id, "represented-by").settled:
            linked.add(key)
            continue
        facts = sum(1 for row in settled(catalog) if row.subject == subject.id)
        if facts:
            rows[key] += facts
            first.setdefault(key, subject.label)
    options = _components(model)
    return tuple(
        LinkQuestion(key=key, principal=first[key], rows=count, options=options)
        for key, count in sorted(rows.items(), key=lambda item: (-item[1], item[0]))
        if key not in linked
    )


def apply_answers(
    catalog: AssertionCatalog,
    model: SystemModel,
    links: Sequence[LinkAnswer],
    facts: Sequence[FactAnswer] = (),
) -> tuple[AssertionCatalog, list[CatalogIssue]]:
    """The catalog with every answer written in: links, then answered open rows.

    A fact answer about an attribute is written onto the model instead, by
    :func:`~analysis_service.questions.answered_model`, and one about a subject
    has no row; both still quote their lines of the answers Source.
    """
    text, link_spans, fact_spans = _composed(links, facts)
    digest = text_digest(text)
    linked, issues = _link_rows(catalog, model, links, text, digest, link_spans)
    spans = [
        SupportSpan(
            source_label=ANSWERS_LABEL,
            digest=digest,
            start=start,
            end=end,
            quote=text[start:end],
        )
        for start, end in fact_spans
    ]
    answered, unmatched = fact_rows(linked, facts, spans)
    return answered, [*issues, *unmatched]


class NoCatalogError(ValueError):
    """A link answer to a job whose checkpoint holds no assertion catalog."""


def check_answers(
    links: Sequence[LinkAnswer],
    facts: Sequence[FactAnswer],
    model: SystemModel,
    catalog: AssertionCatalog | None,
    asked: Collection[UnknownKey],
    earlier: Sequence[FactAnswer] = (),
    *,
    asked_links: Collection[str] = (),
    earlier_links: Sequence[LinkAnswer] = (),
    reopen: bool = False,
) -> None:
    """Refuse an answer that would place nothing, before a resumed run is admitted.

    **The one admission check of a submission's answers.** Its caller is
    :meth:`~analysis_service.answer_round.QuestionSet.admit`. A link answer is
    written by :func:`apply_answers` here, and each issue it would raise is a
    refusal, so a wrong link costs nothing. A link answer with no catalog to
    write it into raises :class:`NoCatalogError`. ``asked`` is the facts the
    job's questions name, and ``asked_links`` the :func:`fold` keys of the
    principals they ask about; ``earlier`` and ``earlier_links`` are the
    answers of the earlier rounds, which a later round may answer again. A
    link to any other principal is refused, so a submission cannot replace a
    ``represented-by`` row the sources stated. ``reopen`` lets an ``unknown``
    answer take back an earlier one (:func:`check_fact_answers`).
    """
    if links and catalog is None:
        raise NoCatalogError(
            "this report carries no assertion catalog, so it asked no link question"
        )
    answerable = {*asked_links, *(fold(link.principal) for link in earlier_links)}
    for link in links:
        if fold(link.principal) not in answerable:
            raise ValueError(f"this job asked no question about {link.principal!r}")
    check_fact_answers(facts, model, catalog, earlier, reopen=reopen)
    answered_before = {fact.key for fact in earlier}
    for fact in facts:
        if fact.key not in asked and fact.key not in answered_before:
            raise ValueError(f"this job asked no question {fact.key!r}")
    if links and catalog is not None:
        _, issues = apply_answers(catalog, model, links)
        if issues:
            raise ValueError(issues[0].message)


def _link_rows(
    catalog: AssertionCatalog,
    model: SystemModel,
    links: Sequence[LinkAnswer],
    text: str,
    digest: str,
    spans: Spans,
) -> tuple[AssertionCatalog, list[CatalogIssue]]:
    """The catalog with each link answer written as a stated ``represented-by`` row.

    An answer reaches every principal subject whose name folds to its own, and
    replaces any ``represented-by`` row already on it. An answer that reaches no
    principal, or names a component this model does not hold, writes nothing
    and comes back as an issue, so a reader sees why it did nothing.
    """
    if not links:
        return catalog, []
    held = set(_components(model))
    subjects = list(catalog.subjects)
    known = {subject.id for subject in subjects}
    principals = defaultdict(list)
    for subject in subjects:
        if subject.type == "principal":
            principals[fold(subject.label)].append(subject.id)
    issues: list[CatalogIssue] = []
    answered: dict[str, Assertion] = {}
    for link, (start, end) in zip(links, spans, strict=True):
        targets = principals.get(fold(link.principal), [])
        if not targets:
            issues.append(
                CatalogIssue(
                    code="unmatched-link",
                    message=f"no principal in this catalog is named"
                    f" {link.principal!r}, so the answer placed nothing",
                )
            )
            continue
        if link.element != NONE_OF_THESE and link.element not in held:
            issues.append(
                CatalogIssue(
                    code="unknown-link-element",
                    message=f"{link.element!r} is not a component of this model,"
                    f" so the answer for {link.principal!r} placed nothing",
                )
            )
            continue
        value = ABSENT if link.element == NONE_OF_THESE else link.element
        if value != ABSENT and value not in known:
            subjects.append(Subject(id=value, type="component", label=value))
            known.add(value)
        span = SupportSpan(
            source_label=ANSWERS_LABEL,
            digest=digest,
            start=start,
            end=end,
            quote=text[start:end],
        )
        for subject_id in targets:
            answered[subject_id] = Assertion(
                subject=subject_id,
                predicate="represented-by",
                value=value,
                basis="stated",
                support=[span],
            )
    kept = [
        entry
        for entry in catalog.entries
        if not (entry.predicate == "represented-by" and entry.subject in answered)
    ]
    return (
        catalog.model_copy(
            update={"subjects": subjects, "entries": [*kept, *answered.values()]}
        ),
        issues,
    )
