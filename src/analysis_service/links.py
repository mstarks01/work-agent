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
from collections.abc import Sequence
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
from analysis_service.sources import Source, plain_name, text_digest
from analysis_service.system_model import PLAIN_ID_RE, SystemModel

__all__ = [
    "ANSWERS_LABEL",
    "MAX_LINK_ANSWERS",
    "NONE_OF_THESE",
    "LinkAnswer",
    "LinkQuestion",
    "apply_links",
    "fold",
    "link_questions",
    "with_link_answers",
]

#: The answer that says a principal is no element of the model. It is a real
#: answer: "anything that can reach the service" is a population, not a
#: component, and saying so stops the question being asked again.
NONE_OF_THESE = "none"

#: How many answers one submission carries. The archive asks at most five link
#: questions a report, so this bounds the body far above any real use.
MAX_LINK_ANSWERS = 50

#: The label of the Source the answers become. A caller's own source may not
#: use it: the job refuses two sources that share a label.
ANSWERS_LABEL = "Answers to link questions"

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


def _composed(links: Sequence[LinkAnswer]) -> tuple[str, tuple[tuple[int, int], ...]]:
    """The answers Source's text, and where each answer's line sits in it."""
    spans, lines, at = [], [], 0
    for link in links:
        line = _line(link)
        spans.append((at, at + len(line)))
        lines.append(line)
        at += len(line) + 1
    return "\n".join(lines), tuple(spans)


def with_link_answers(
    sources: Sequence[Source], links: Sequence[LinkAnswer]
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
    if not links:
        return list(sources)
    principals = [fold(link.principal) for link in links]
    repeated = sorted({key for key in principals if principals.count(key) > 1})
    if repeated:
        # Refused rather than resolved by order: the service cannot know which
        # of two answers about one principal the submitter meant.
        raise ValueError(f"links answers one principal twice: {', '.join(repeated)}")
    text, _ = _composed(links)
    return [*sources, Source(kind="answers", label=ANSWERS_LABEL, text=text)]


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


def apply_links(
    catalog: AssertionCatalog,
    model: SystemModel,
    links: Sequence[LinkAnswer],
) -> tuple[AssertionCatalog, list[CatalogIssue]]:
    """The catalog with each answer written as a stated ``represented-by`` row.

    An answer reaches every principal subject whose name folds to its own, and
    replaces any ``represented-by`` row already on it. An answer that reaches no
    principal, or names a component this model does not hold, writes nothing
    and comes back as an issue, so a reader sees why it did nothing.
    """
    if not links:
        return catalog, []
    text, spans = _composed(links)
    digest = text_digest(text)
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
