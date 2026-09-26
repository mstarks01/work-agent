"""The open facts a framework's conditional findings rest on, grouped for a reader.

A ``needs-info`` verdict names what has to be answered in ``related_unknowns``.
One open fact often holds up several findings, and one finding often waits on
several facts, so a report that lists the findings one by one asks the same
question many times and hides which answer would settle the most. This groups
them the other way round: each open fact once, with the findings that cite it.

**Deterministic, and derived from the report alone.** No model is asked. The
grouping reads each verdict's ``related_unknowns`` through
:attr:`~analysis_service.claims.UnknownRef.key`, the one reader of "are these two
references the same", and labels an element through the report's own System
Model. Nothing here is stored on the report: it is computed wherever a reader
needs it, so it cannot drift from the verdicts it summarises.

**Each finding is placed once.** A finding that waits on several facts is shown
under the one that settles the most findings on its own, and the others it
needs are still listed on its card. The order is by that same count, then by
how many findings cite the fact, then by label, so two reads of one report
agree.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from analysis_service.claims import FrameworkAnalysis, UnknownRef
from analysis_service.system_model import SystemModel

NEEDS_INFO = "needs-info"


@dataclass(frozen=True)
class OpenFact:
    """One open fact, and the conditional findings that wait on it."""

    #: :attr:`UnknownRef.key`, as a list so it serialises as JSON.
    key: tuple[str, str, str]
    #: What a reader sees: an element's name and the attribute, or the subject.
    label: str
    #: Every needs-info finding that cites this fact.
    cited_by: tuple[str, ...]
    #: The findings an answer to this fact alone would settle.
    settles: tuple[str, ...]
    #: The findings shown under this fact; each finding is placed once.
    placed: tuple[str, ...]

    def to_json(self) -> dict[str, object]:
        return {
            "key": list(self.key),
            "label": self.label,
            "cited_by": list(self.cited_by),
            "settles": list(self.settles),
            "placed": list(self.placed),
        }


def _names(model: SystemModel) -> Mapping[str, str]:
    """Each element's display name; a flow reads as its two endpoints."""
    names = {element.id: element.name for element in model.elements()}
    for flow in model.data_flows:
        source = names.get(flow.source, flow.source)
        destination = names.get(flow.destination, flow.destination)
        names[flow.id] = f"{source} → {destination}"
    return names


def _label(ref: UnknownRef, names: Mapping[str, str]) -> str:
    if ref.assertion:
        return ref.assertion
    if ref.names_an_element:
        where = names.get(ref.element_id, ref.element_id)
        return f"{where}: {ref.attribute.replace('_', ' ')}"
    return ref.subject


def open_facts(block: FrameworkAnalysis, model: SystemModel) -> tuple[OpenFact, ...]:
    """Every open fact this block's needs-info findings rest on, best first."""
    names = _names(model)
    needs: dict[str, set[tuple[str, str, str]]] = {}
    labels: dict[tuple[str, str, str], str] = {}
    for claim in block.claims:
        if claim.verdict.status != NEEDS_INFO:
            continue
        refs = claim.verdict.related_unknowns
        needs[claim.id] = {ref.key for ref in refs}
        for ref in refs:
            labels.setdefault(ref.key, _label(ref, names))
    cited: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for claim_id, keys in needs.items():
        for key in keys:
            cited[key].append(claim_id)
    settles = {
        key: tuple(claim_id for claim_id in ids if needs[claim_id] == {key})
        for key, ids in cited.items()
    }
    order = sorted(
        cited, key=lambda key: (-len(settles[key]), -len(cited[key]), labels[key])
    )
    rank = {key: position for position, key in enumerate(order)}
    placed: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for claim_id, keys in needs.items():
        if keys:
            placed[min(keys, key=rank.__getitem__)].append(claim_id)
    return tuple(
        OpenFact(
            key=key,
            label=labels[key],
            cited_by=tuple(cited[key]),
            settles=settles[key],
            placed=tuple(placed[key]),
        )
        for key in order
    )


def open_facts_by_framework(
    analyses: Sequence[FrameworkAnalysis], model: SystemModel
) -> dict[str, list[dict[str, object]]]:
    """:func:`open_facts` for each block, as the JSON a page is handed."""
    return {
        block.framework: [fact.to_json() for fact in open_facts(block, model)]
        for block in analyses
    }
