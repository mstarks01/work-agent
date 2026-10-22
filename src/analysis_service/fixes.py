"""The missing controls a framework's confirmed findings rest on, grouped for a reader.

A confirmed finding often rests on a control the description states is not
there: no authentication on a connection, no encryption on a store. Several
findings often rest on the same one, so a report that lists them one by one
hides that one change answers them all. This groups them the other way round:
each missing control once, with the confirmed findings that rest on it.

**Deterministic, and derived from the report alone.** No model is asked. A
control is missing where a finding cites an ``absent-attribute`` ground, or an
``assertion`` ground whose row states :data:`~analysis_service.assertions.ABSENT`.
A row's control is read through :func:`~analysis_service.critic.row_controls`,
the one reader of which control a row is about. A finding that rests only on a
quote names no control, so it stays outside every group.

**Each finding is placed once.** A finding that rests on several missing
controls is shown under the one that the most findings rest on. Only a control
that two or more findings rest on is a group: a control one finding rests on
says nothing its card does not.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from analysis_service.assertions import ABSENT, AssertionCatalog, assertion_id
from analysis_service.claims import Claim, FrameworkAnalysis
from analysis_service.critic import row_controls
from analysis_service.open_facts import attribute_words, element_names, flow_words
from analysis_service.system_model import DataFlow, SystemModel

#: A missing control: the subject it is missing on, and the control.
ControlKey = tuple[str, str]


@dataclass(frozen=True)
class Fix:
    """One missing control, and the confirmed findings that rest on it."""

    key: ControlKey
    #: What a reader sees: the control to add, and where.
    label: str
    #: Every confirmed finding that rests on this control.
    rests: tuple[str, ...]
    #: The findings shown under this control; each finding is placed once.
    placed: tuple[str, ...]

    def to_json(self) -> dict[str, object]:
        return {
            "key": list(self.key),
            "label": self.label,
            "rests": list(self.rests),
            "placed": list(self.placed),
        }


def absent_rows(assertions: AssertionCatalog | None) -> Mapping[str, ControlKey]:
    """Each row that states a control absent, by its ID: its subject and control."""
    if assertions is None:
        return {}
    controls = row_controls(assertions)
    return {
        row_id: (entry.subject, controls[row_id])
        for entry in assertions.entries
        if entry.value == ABSENT and (row_id := assertion_id(entry)) in controls
    }


def missing_controls(
    claim: Claim, rows: Mapping[str, ControlKey]
) -> frozenset[ControlKey]:
    """The controls a claim's grounds state are missing, each with its subject."""
    return frozenset(
        (ground.element_id, ground.attribute)
        for ground in claim.grounds
        if ground.kind == "absent-attribute"
    ) | frozenset(
        rows[ground.assertion]
        for ground in claim.grounds
        if ground.kind == "assertion" and ground.assertion in rows
    )


def label_of(key: ControlKey, names: Mapping[str, str], model: SystemModel) -> str:
    """The control to add, and where: a flow reads through :func:`flow_words`."""
    subject, control = key
    element = model.get(subject)
    if isinstance(element, DataFlow):
        where = flow_words(element, names)
    else:
        where = names.get(subject, subject)
    return f"Add {attribute_words(control)} to {where}"


def fixes(
    block: FrameworkAnalysis,
    model: SystemModel,
    assertions: AssertionCatalog | None = None,
) -> tuple[Fix, ...]:
    """Every missing control two or more confirmed findings rest on, most first."""
    rows = absent_rows(assertions)
    rests: dict[ControlKey, list[str]] = defaultdict(list)
    needs: dict[str, frozenset[ControlKey]] = {}
    for claim in block.claims:
        if claim.verdict.status != "confirmed":
            continue
        needs[claim.id] = missing_controls(claim, rows)
        for key in needs[claim.id]:
            rests[key].append(claim.id)
    names = element_names(model)
    labels = {key: label_of(key, names, model) for key in rests}
    order = sorted(
        (key for key, ids in rests.items() if len(ids) > 1),
        key=lambda key: (-len(rests[key]), labels[key]),
    )
    rank = {key: position for position, key in enumerate(order)}
    placed: dict[ControlKey, list[str]] = defaultdict(list)
    for claim_id, keys in needs.items():
        grouped = [key for key in keys if key in rank]
        if grouped:
            placed[min(grouped, key=rank.__getitem__)].append(claim_id)
    return tuple(
        Fix(
            key=key,
            label=labels[key],
            rests=tuple(rests[key]),
            placed=tuple(placed[key]),
        )
        for key in order
        if placed[key]
    )


def fixes_by_framework(
    analyses: Sequence[FrameworkAnalysis],
    model: SystemModel,
    assertions: AssertionCatalog | None = None,
) -> dict[str, list[dict[str, object]]]:
    """:func:`fixes` for each block, as the JSON a page is handed."""
    return {
        block.framework: [fix.to_json() for fix in fixes(block, model, assertions)]
        for block in analyses
    }
