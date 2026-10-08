"""Why each conditional finding is still open, and what a correction reaches.

A report shows each conditional finding beside the facts it waits on and why
each is still open (#1289, PR 4). A final report takes corrections, and marks
each finding that rests on a corrected fact (ADR 0054). Both are read here.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from analysis_service.claims import FrameworkAnalysis, UnknownKey
from analysis_service.fact_answers import (
    FactAnswer,
    FactStatus,
    SkipKey,
    fact_kind,
    fact_line,
    fact_status,
    needs_of,
)
from analysis_service.open_facts import element_names, label_of
from analysis_service.sources import ANSWERS_LABEL
from analysis_service.system_model import SystemModel

__all__ = [
    "conditions",
    "corrected_findings",
]


def conditions(
    analyses: Sequence[FrameworkAnalysis],
    model: SystemModel,
    answered: Sequence[FactAnswer],
    shown: Sequence[UnknownKey],
    skipped: Sequence[SkipKey] = (),
) -> dict[str, list[tuple[UnknownKey, str, FactStatus]]]:
    """Each conditional finding's open facts, as ``framework/claim``: key, label and status.

    **The one reader of "why is this finding still conditional"**, which a
    report shows beside it. ``answered`` is what the run read, ``shown`` what
    its pause presented and ``skipped`` what its submitter skipped
    (:func:`fact_status`). An answer that does not settle its fact
    (:attr:`~analysis_service.fact_answers.FactAnswer.settles`) is
    ``unknown`` where it says "I don't know", and ``partial`` where it answers
    some facets: the finding is neither confirmed nor cleared, and nothing
    reads the fact as absent.
    """
    names = element_names(model)
    said = {answer.key: answer for answer in answered}
    showed, aside = set(shown), set(skipped)

    def status(key: UnknownKey, facets: Sequence[str]) -> FactStatus:
        return fact_status(key, said, showed, aside, facets)

    found: dict[str, list[tuple[UnknownKey, str, FactStatus]]] = {}
    for block in analyses:
        for claim in block.all_claims():
            if claim.verdict.status != "needs-info":
                continue
            cites = [*claim.unknown_grounds(), *claim.verdict.related_unknowns]
            refs = {ref.key: ref for ref in cites}
            wanted = needs_of(cites)
            found[f"{block.framework}/{claim.id}"] = [
                (key, label_of(ref, names), status(key, wanted[key]))
                for key, ref in refs.items()
            ]
    return found


def corrected_findings(
    analyses: Sequence[FrameworkAnalysis],
    earlier: Sequence[FactAnswer],
    corrections: Sequence[FactAnswer],
) -> tuple[str, ...]:
    """Every finding, as ``framework/claim``, that rests on a fact a correction changed.

    **The one reader of "which findings does a correction reach"** (ADR 0054).
    ``earlier`` is the answers the report's run read. A finding rests on a
    corrected fact where it quotes the fact's line of the answers Source,
    grounds on the same element's attribute, or names the fact as one it waits
    on.
    """
    before = {fact.key: fact for fact in earlier}
    lines = {
        fact.key: " ".join(fact_line(before[fact.key]).split())
        for fact in corrections
        if fact.key in before
    }
    keys = {fact.key for fact in corrections}
    attributes = {key[:2] for key in keys if fact_kind(key) == "attribute"}

    def rests_on(claim: Any) -> bool:
        cited = {
            ref.key
            for ref in [*claim.unknown_grounds(), *claim.verdict.related_unknowns]
        }
        if cited & keys:
            return True
        for ground in claim.grounds:
            if (ground.element_id, ground.attribute) in attributes:
                return True
            quoted = " ".join(ground.text.split())
            if (
                ground.source_label == ANSWERS_LABEL
                and quoted
                and any(quoted in line for line in lines.values())
            ):
                return True
        return False

    return tuple(
        f"{block.framework}/{claim.id}"
        for block in analyses
        for claim in block.all_claims()
        if rests_on(claim)
    )
