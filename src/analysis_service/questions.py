"""The open facts a report's conditional findings rest on, asked of the submitter.

A ``needs-info`` verdict names the facts an answer would settle. This module
turns a report's findings into its ranked list of questions (#1225).

**Every open fact is asked, the most important findings' facts first**
(ADR 0056). The findings that wait on an answer decide the order, the highest
band first: a critical finding that waits on two facts comes before a low one
that waits on one. Each question keeps its basis, ``evidence`` where a
draft's own grounds cite it and ``critic`` where only the critic named it.
With the STRIDE lane closing a conditional finding waits on several facts,
and six questions settled about a quarter of them where asking every one
settled all (``QA-2026-09-26-03-E6``). So no cap is chosen here. Each question
states how many findings are covered once it and every question before it is
answered. That count is coverage, not a verdict: only the resumed run rules
on a finding again. The facts no waiting finding needs follow, in their own
section of the page. A submitter answers from the top as far as they choose.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Literal

from analysis_service.answer_forms import (
    AnswerForm,
    answer_choices,
    answer_form,
    answer_limit,
    answer_suggestions,
    facets_json,
)
from analysis_service.assertions import AssertionCatalog
from analysis_service.bands import UNRANKED, Band
from analysis_service.claims import FrameworkAnalysis, UnknownKey, UnknownRef
from analysis_service.fact_answers import (
    FactAnswer,
    FactKind,
    FactStatus,
    answer_facets,
    answered_keys,
    fact_kind,
)
from analysis_service.frameworks import PACKAGES
from analysis_service.open_facts import (
    element_names,
    label_of,
    open_attribute,
    prepared_model,
)
from analysis_service.question_kinds import Facet
from analysis_service.system_model import SystemModel

__all__ = [
    "Basis",
    "FactQuestion",
    "Fallback",
    "Finding",
    "FollowUpNeeds",
    "choices_of",
    "fact_questions",
    "follow_up_needs",
    "follow_up_order",
    "question_fallback",
]


Basis = Literal["evidence", "critic"]


#: One finding: its framework and its claim ID.
Finding = tuple[str, str]


@dataclass(frozen=True)
class FactQuestion:
    """One open fact, the findings that wait on it, and the answers it takes."""

    key: UnknownKey
    kind: FactKind
    #: ``evidence`` where a finding's own grounds cite the fact, which does not
    #: change when the critic is sampled again; ``critic`` where only the
    #: critic's verdict names it, which does.
    basis: Basis
    #: What a reader sees: an element's name and the attribute, or the subject.
    label: str
    #: How many still-open findings cite it when it is asked.
    cited_by: int
    #: For how many findings every fact is answered once it and every question
    #: before it is answered. Coverage, not a verdict.
    covered_so_far: int
    #: The answers it takes, or empty where the answer is free text.
    choices: tuple[str, ...]
    #: How a page takes the answer; see
    #: :data:`~analysis_service.answer_forms.AnswerForm`.
    form: AnswerForm
    #: Common mechanisms a ``control`` answer may start from.
    suggestions: tuple[str, ...]
    #: The parts a ``facets`` answer is given in.
    facets: tuple[Facet, ...]
    #: The longest answer it admits
    #: (:func:`~analysis_service.answer_forms.answer_limit`).
    max_length: int
    #: The label of the highest :class:`~analysis_service.frameworks.Band`
    #: among the findings that wait on it, or empty where none does or its
    #: package grades nothing.
    band: str
    #: Every finding that waits on it, as ``framework/claim``. A finding is
    #: covered once every question that names it has an answer, whichever
    #: questions those are.
    findings: tuple[str, ...]
    #: What became of this fact at the pause, so a page can say it was
    #: skipped, left blank or answered in part before the analysis, or never
    #: shown (:func:`~analysis_service.fact_answers.fact_status`).
    history: FactStatus = "open"

    def to_json(self) -> dict[str, object]:
        return {
            "key": list(self.key),
            "kind": self.kind,
            "basis": self.basis,
            "history": self.history,
            "label": self.label,
            "cited_by": self.cited_by,
            "covered_so_far": self.covered_so_far,
            "choices": list(self.choices),
            "form": self.form,
            "suggestions": list(self.suggestions),
            "facets": facets_json(self.facets),
            "max_length": self.max_length,
            "band": self.band,
            "findings": list(self.findings),
        }


def choices_of(key: UnknownKey) -> int:
    """The choices one question takes: one a facet, else one."""
    return len(answer_facets(key)) or 1


def _by_band(
    open_facts: Mapping[Finding, AbstractSet[UnknownKey]], band: Mapping[Finding, int]
) -> list[UnknownKey]:
    """The facts in the order that serves the most important findings first.

    ``band`` is each finding's :class:`~analysis_service.bands.Band` order.
    The highest band left decides each step. Where a fact completes findings
    of that band, the next is the one that completes the most of them, then
    the most in each band below. Where none does, the next are the facts of
    that band's finding with the fewest choices left, the most cited first,
    asked together. A lower band never comes before a higher one that still
    waits: a critical finding that waits on two facts comes before a low one
    that waits on one. A tie goes to more citations, then to the lower key,
    so one input always gives one order (``QA-2026-09-26-03-E35``).
    """
    left = {finding: set(keys) for finding, keys in open_facts.items() if keys}
    order: list[UnknownKey] = []
    while left:
        cites = Counter(key for keys in left.values() for key in keys)
        top = max(band[finding] for finding in left)
        bands = sorted({band[finding] for finding in left}, reverse=True)
        completes: dict[UnknownKey, Counter[int]] = {}
        for finding, keys in left.items():
            if len(keys) == 1:
                completes.setdefault(next(iter(keys)), Counter())[band[finding]] += 1
        at_top = {key: done for key, done in completes.items() if done[top]}
        if at_top:
            chosen = [
                min(
                    at_top,
                    key=lambda key: (
                        tuple(-at_top[key][level] for level in bands),
                        -cites[key],
                        key,
                    ),
                )
            ]
        else:
            nearest = min(
                (keys for finding, keys in left.items() if band[finding] == top),
                key=lambda keys: (
                    sum(choices_of(key) for key in keys),
                    len(keys),
                    sorted(keys),
                ),
            )
            chosen = sorted(nearest, key=lambda key: (-cites[key], key))
        order.extend(chosen)
        asked = set(chosen)
        left = {finding: keys - asked for finding, keys in left.items() if keys - asked}
    return order


@dataclass(frozen=True)
class FollowUpNeeds:
    """What a report's findings wait on: read once, for the order and the counts."""

    #: Each draft's open grounds, whatever its verdict.
    evidence: Mapping[Finding, frozenset[UnknownKey]]
    #: The open facts a ``needs-info`` verdict names.
    named: Mapping[Finding, frozenset[UnknownKey]]
    #: Each conditional finding's open facts: the findings an answer can cover.
    waiting: Mapping[Finding, frozenset[UnknownKey]]
    #: Each draft's band, as its package ranks it.
    bands: Mapping[Finding, Band]
    #: The reference each open fact was first cited by.
    refs: Mapping[UnknownKey, UnknownRef]


def follow_up_needs(
    analyses: Sequence[FrameworkAnalysis],
    model: SystemModel,
    catalog: AssertionCatalog | None,
    earlier: Sequence[FactAnswer] = (),
) -> FollowUpNeeds:
    """Every open fact each draft waits on, as :func:`fact_questions` reads them.

    A fact ``earlier`` answers in full is not open. A finding that waits on a
    fact ``earlier`` answers without settling it is not conditional, because
    no answer in the list can cover it.
    """
    evidence: dict[Finding, frozenset[UnknownKey]] = {}
    named: dict[Finding, frozenset[UnknownKey]] = {}
    refs: dict[UnknownKey, UnknownRef] = {}
    conditional: set[Finding] = set()
    prepared = prepared_model(model, catalog)
    done = answered_keys(earlier)
    unknown = {answer.key for answer in earlier if not answer.settles} & done
    bands: dict[Finding, Band] = {}
    for block in analyses:
        rank = PACKAGES[block.framework].rank
        for claim in block.all_claims():
            finding = (block.framework, claim.id)
            bands[finding] = rank(claim)
            cites = [*claim.unknown_grounds(), *claim.verdict.related_unknowns]
            stuck = bool(unknown & {ref.key for ref in cites})
            if claim.verdict.status == "needs-info" and not stuck:
                conditional.add(finding)
            grounds = [
                ref
                for ref in claim.unknown_grounds()
                if _open(ref, prepared) and ref.key not in done
            ]
            evidence[finding] = frozenset(ref.key for ref in grounds)
            cited = list(grounds)
            if claim.verdict.status == "needs-info":
                verdict = [
                    ref
                    for ref in claim.verdict.related_unknowns
                    if _open(ref, prepared) and ref.key not in done
                ]
                named[finding] = frozenset(ref.key for ref in verdict)
                cited += verdict
            for ref in cited:
                refs.setdefault(ref.key, ref)
    waiting = {
        finding: evidence.get(finding, frozenset()) | named.get(finding, frozenset())
        for finding in conditional
    }
    return FollowUpNeeds(
        evidence=evidence,
        named=named,
        waiting={finding: keys for finding, keys in waiting.items() if keys},
        bands=bands,
        refs=refs,
    )


def follow_up_order(needs: FollowUpNeeds) -> list[tuple[Basis, UnknownKey]]:
    """The order the follow-up asks its facts in, each with its basis.

    **The findings that wait decide the order** (ADR 0056). The facts the
    conditional findings wait on come first, the most important findings'
    first (:func:`_by_band`), the critic's facts among them. The facts of the
    other drafts follow: a confirmed or rejected draft, and one that waits on
    a fact answered "I don't know", which no answer here can complete. Each
    fact is ``evidence`` where any draft's own grounds cite it, and
    ``critic`` where only a verdict names it.

    The order reads the critic's verdicts, so a critic sampled again moves
    it. Built from one sample and scored on another, it still completed 29%
    more critical and high findings at five choices than the evidence-first
    order, and 42% more at ten (``QA-2026-09-26-03-E35``).
    """
    band = {finding: each.order for finding, each in needs.bands.items()}
    first = _by_band(needs.waiting, band)
    asked = set(first)
    rest = _by_band(
        {
            finding: (
                needs.evidence.get(finding, frozenset())
                | needs.named.get(finding, frozenset())
            )
            - asked
            for finding in needs.bands.keys() - needs.waiting.keys()
        },
        band,
    )
    grounded = {key for keys in needs.evidence.values() for key in keys}
    return [
        ("evidence" if key in grounded else "critic", key) for key in (*first, *rest)
    ]


def fact_questions(
    analyses: Sequence[FrameworkAnalysis],
    model: SystemModel,
    catalog: AssertionCatalog | None,
    earlier: Sequence[FactAnswer] = (),
) -> tuple[FactQuestion, ...]:
    """Every open fact a report's findings rest on, in :func:`follow_up_order`.

    ``earlier`` is the fact answers of the earlier rounds. A fact they answer
    in full (:func:`~analysis_service.fact_answers.answered_keys`) is not
    asked again. A finding that waits on a fact they answer without settling
    it (:attr:`~analysis_service.fact_answers.FactAnswer.settles`), such as
    "I don't know" or a facet answered so, is not counted, because no answer
    in this list can cover it.

    **The findings that wait decide the order** (ADR 0056). The facts the most
    important conditional findings wait on come first, and the facts only the
    critic named sit among them. Each question keeps its basis: ``evidence``
    where a draft's own grounds cite the fact, ``critic`` where only a verdict
    names it, so a page can say which facts can change when the critic is
    sampled again.

    Each question counts the findings of every framework together, because one
    answer settles a fact for every framework that cites it. ``cited_by`` and
    ``covered_so_far`` count only the conditional findings, which are the ones
    waiting on an answer, by their grounds and their verdict. A confirmed
    finding and a rejected draft rank the questions and are not counted: the
    first waits on nothing, and no answer brings the second into the report.

    An attribute the model states is not asked, even where the critic names
    it, because :func:`~analysis_service.fact_writes.check_fact_answers`
    refuses an answer to it.
    """
    needs = follow_up_needs(analyses, model, catalog, earlier)
    waiting = needs.waiting
    bands = needs.bands
    names = element_names(model)
    asked: list[FactQuestion] = []
    answered: set[UnknownKey] = set()
    for basis, key in follow_up_order(needs):
        cited_by = sum(
            1 for facts in waiting.values() if key in facts and not facts <= answered
        )
        answered.add(key)
        asked.append(
            FactQuestion(
                key=key,
                kind=fact_kind(key),
                basis=basis,
                label=label_of(needs.refs[key], names),
                cited_by=cited_by,
                covered_so_far=sum(
                    1 for facts in waiting.values() if facts <= answered
                ),
                choices=answer_choices(key, model, catalog),
                form=answer_form(key, model, catalog),
                suggestions=answer_suggestions(key),
                facets=answer_facets(key),
                max_length=answer_limit(key, model),
                findings=tuple(
                    sorted(
                        f"{framework}/{claim}"
                        for (framework, claim), facts in waiting.items()
                        if key in facts
                    )
                ),
                band=max(
                    (bands[f] for f, facts in waiting.items() if key in facts),
                    key=lambda band: band.order,
                    default=UNRANKED,
                ).label,
            )
        )
    return tuple(asked)


def _open(ref: UnknownRef, model: SystemModel) -> bool:
    """False for an attribute the model states; every other fact is open."""
    element_id, attribute, *_ = ref.key
    return fact_kind(ref.key) != "attribute" or open_attribute(
        model, element_id, attribute
    )


@dataclass(frozen=True)
class Fallback:
    """How the critic named the open facts that have no place in the model.

    ``typed`` names a kind from the question table and an element; ``free_text``
    fell back to a ``subject``. The share that fell back is what says whether
    the table is enough (``QA-2026-09-26-03-E8``): a low, steady share means it
    is, and the fallback texts name the kinds it lacks.
    """

    typed: int
    free_text: int

    @property
    def rate(self) -> float | None:
        total = self.typed + self.free_text
        return None if total == 0 else round(self.free_text / total, 3)

    def to_json(self) -> dict[str, object]:
        return {"typed": self.typed, "free_text": self.free_text, "rate": self.rate}


def question_fallback(analyses: Sequence[FrameworkAnalysis]) -> Fallback:
    """Count a report's typed and free-text open facts across its verdicts."""
    refs = [
        ref
        for block in analyses
        for claim in block.all_claims()
        for ref in claim.verdict.related_unknowns
    ]
    return Fallback(
        typed=sum(1 for ref in refs if ref.spellings == ("question",)),
        free_text=sum(1 for ref in refs if ref.spellings == ("subject",)),
    )
