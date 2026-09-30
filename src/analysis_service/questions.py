"""The open facts a report's conditional findings rest on, asked of the submitter.

A ``needs-info`` verdict names the facts an answer would settle. This module
turns a report's findings into its ranked list of questions (#1225).

**Every open fact is asked, in the order that completes the most findings.**
The facts a finding's own grounds cite come first, in an order the critic
cannot change; the facts only the critic named follow
(``QA-2026-09-26-03-E7``). With the STRIDE lane closing a conditional finding
waits on several facts, and six questions settled about a quarter of them
where asking every one settled all (``QA-2026-09-26-03-E6``). So no cap is
chosen here. The next question is the one that completes the most findings, so
that each has every fact it waits on answered (``QA-2026-09-26-03-E17``). Each
question states how many findings are covered once it and every question
before it is answered. That count is coverage, not a verdict: only the resumed
run rules on a finding again. A rejected draft still ranks the questions, and
is left out of every count. A submitter answers from the top as far as they
choose.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
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
    "FactQuestion",
    "Fallback",
    "fact_questions",
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
    #: True where the pause showed this question and got no answer, so a page
    #: can say it was skipped before the analysis.
    asked_before: bool = False

    def to_json(self) -> dict[str, object]:
        return {
            "key": list(self.key),
            "kind": self.kind,
            "basis": self.basis,
            "asked_before": self.asked_before,
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


def _greedy(
    open_facts: Mapping[Finding, set[UnknownKey]], band: Mapping[Finding, int]
) -> list[UnknownKey]:
    """The facts in the order that completes the most important findings first.

    ``band`` is each finding's :class:`~analysis_service.frameworks.Band`
    order. Where one question completes findings, the next is the one that
    completes the most in the highest band, then in the next band down, and so
    on: one critical finding before three low ones, with no weights. Where
    none does, the next are the facts of the highest-band finding with the
    fewest left, the most cited first. A tie goes to more citations, then to
    the lower key, so one input always gives one order. Measured against
    asking the most cited fact first, completing the most findings covers more
    at every depth (``QA-2026-09-26-03-E17``).
    """
    left = {finding: set(keys) for finding, keys in open_facts.items() if keys}
    order: list[UnknownKey] = []
    while left:
        cites = Counter(key for keys in left.values() for key in keys)
        bands = sorted({band[finding] for finding in left}, reverse=True)
        completes: dict[UnknownKey, Counter[int]] = {}
        for finding, keys in left.items():
            if len(keys) == 1:
                completes.setdefault(next(iter(keys)), Counter())[band[finding]] += 1
        if completes:
            chosen = [
                min(
                    completes,
                    key=lambda key: (
                        tuple(-completes[key][level] for level in bands),
                        -cites[key],
                        key,
                    ),
                )
            ]
        else:
            nearest = min(
                left.items(),
                key=lambda item: (-band[item[0]], len(item[1]), sorted(item[1])),
            )[1]
            chosen = sorted(nearest, key=lambda key: (-cites[key], key))
        order.extend(chosen)
        asked = set(chosen)
        left = {finding: keys - asked for finding, keys in left.items() if keys - asked}
    return order


def fact_questions(
    analyses: Sequence[FrameworkAnalysis],
    model: SystemModel,
    catalog: AssertionCatalog | None,
    earlier: Sequence[FactAnswer] = (),
) -> tuple[FactQuestion, ...]:
    """Every open fact a report's findings rest on, the most completing first.

    ``earlier`` is the fact answers of the earlier rounds. A fact they answer
    in full (:func:`~analysis_service.fact_answers.answered_keys`) is not
    asked again. A finding that waits on a fact they answer without settling
    it (:attr:`~analysis_service.fact_answers.FactAnswer.settles`), such as
    "I don't know" or a facet answered so, is not counted, because no answer
    in this list can cover it.

    **Two sections, and the first does not depend on the critic.** The
    evidence section ranks the open facts each finding's own grounds cite, for
    every finding the lanes wrote, rejected ones included. A critic that is
    sampled again rules differently about a third of the time, and a list read
    from its verdicts shared 7 of 18 questions with the list from a second
    sample; a list read from the grounds is the same for both, and settled 82%
    as many findings as each sample's own list (``QA-2026-09-26-03-E7``). The
    critic section follows, with the facts only the critic named: its
    free-text subjects, mostly, which change from one sample to the next.

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
    evidence: dict[Finding, set[UnknownKey]] = {}
    named: dict[Finding, set[UnknownKey]] = {}
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
            evidence[finding] = {ref.key for ref in grounds}
            cited = list(grounds)
            if claim.verdict.status == "needs-info":
                verdict = [
                    ref
                    for ref in claim.verdict.related_unknowns
                    if _open(ref, prepared) and ref.key not in done
                ]
                named[finding] = {ref.key for ref in verdict}
                cited += verdict
            for ref in cited:
                refs.setdefault(ref.key, ref)
    # Every draft ranks by its band, whatever its verdict, so the evidence
    # section still does not depend on the critic: a band is the lane's
    # rating or the catalog's, and a ruling sets neither.
    order = {finding: band.order for finding, band in bands.items()}
    first = _greedy(evidence, order)
    asked_first = set(first)
    later = _greedy({f: keys - asked_first for f, keys in named.items()}, order)
    waiting = {
        finding: evidence.get(finding, set()) | named.get(finding, set())
        for finding in evidence.keys() | named.keys()
    }
    waiting = {
        finding: keys
        for finding, keys in waiting.items()
        if keys and finding in conditional
    }
    names = element_names(model)
    asked: list[FactQuestion] = []
    answered: set[UnknownKey] = set()
    sections: tuple[tuple[Basis, list[UnknownKey]], ...] = (
        ("evidence", first),
        ("critic", later),
    )
    for basis, keys in sections:
        for key in keys:
            cited_by = sum(
                1
                for facts in waiting.values()
                if key in facts and not facts <= answered
            )
            answered.add(key)
            asked.append(
                FactQuestion(
                    key=key,
                    kind=fact_kind(key),
                    basis=basis,
                    label=label_of(refs[key], names),
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
