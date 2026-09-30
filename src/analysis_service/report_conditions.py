"""Why each conditional finding is still open, and what a correction reaches.

A report shows each conditional finding beside the facts it waits on and why
each is still open (#1289, PR 4). A final report takes corrections, and marks
each finding that rests on a corrected fact (ADR 0054). Both are read here.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from analysis_service.claims import FrameworkAnalysis, UnknownKey
from analysis_service.fact_answers import FactAnswer, fact_kind, fact_line
from analysis_service.open_facts import element_names, label_of
from analysis_service.sources import ANSWERS_LABEL
from analysis_service.system_model import SystemModel

__all__ = [
    "FactStatus",
    "conditions",
    "corrected_findings",
]


#: Why an open fact a conditional finding waits on is still open: an answer
#: said "I don't know", an answer gave some facets and left the rest open, the
#: pause showed it and got no answer, an answer was given and the analysis
#: still found the finding open, or nobody was asked.
FactStatus = Literal["unknown", "partial", "skipped", "answered", "open"]


def conditions(
    analyses: Sequence[FrameworkAnalysis],
    model: SystemModel,
    answered: Sequence[FactAnswer],
    shown: Sequence[UnknownKey],
) -> dict[str, list[tuple[UnknownKey, str, FactStatus]]]:
    """Each conditional finding's open facts, as ``framework/claim``: key, label and status.

    **The one reader of "why is this finding still conditional"**, which a
    report shows beside it. ``answered`` is what the run read and ``shown``
    what its pause showed. An answer that does not settle its fact
    (:attr:`~analysis_service.fact_answers.FactAnswer.settles`) is
    ``unknown`` where it says "I don't know", and ``partial`` where it answers
    some facets: the finding is neither confirmed nor cleared, and nothing
    reads the fact as absent.
    """
    names = element_names(model)
    said = {answer.key: answer for answer in answered}
    showed = set(shown)

    def status(key: UnknownKey) -> FactStatus:
        answer = said.get(key)
        if answer is None:
            return "skipped" if key in showed else "open"
        if answer.settles:
            return "answered"
        return "partial" if answer.known else "unknown"

    found: dict[str, list[tuple[UnknownKey, str, FactStatus]]] = {}
    for block in analyses:
        for claim in block.all_claims():
            if claim.verdict.status != "needs-info":
                continue
            refs = {
                ref.key: ref
                for ref in [*claim.unknown_grounds(), *claim.verdict.related_unknowns]
            }
            found[f"{block.framework}/{claim.id}"] = [
                (key, label_of(ref, names), status(key)) for key, ref in refs.items()
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
