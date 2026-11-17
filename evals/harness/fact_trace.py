"""Where a wrong fact on a paired element went wrong: the raw emission or the code after it (#2271).

The extraction replay compares each scored attribute of a paired element with
the signed (blessed) model, after
:func:`~analysis_service.compact.parse_extraction` has turned the raw emission
into a model. That parse is deterministic code. So a wrong fact can come from
two places: ``extract`` emitted it, or the parse changed a value that arrived
right. This module reads the raw value of each check from the emission's
``raw`` block, by the produced element's ID, and reduces it with
:func:`~evals.harness.modes.reduce_attribute`, the scorer's own reduction.

Each check takes one :data:`Stage`:

* ``agrees``: the raw value and the parsed value both agree with the signed one.
* ``corrected-by-code``: the raw value is wrong, and the parse made it right.
* ``wrong-in-raw``: the parse kept a raw value that is wrong.
* ``changed-by-code``: the raw value is right, and the parse made it wrong.
  This is the one stage that names a deterministic defect, and only a
  reproduction proves one.
* ``changed-and-wrong``: the raw value is wrong, and the parse changed it to
  another wrong value.
* ``uncaptured``: the raw block holds no element under the produced ID, so the
  parse renamed it and the raw value cannot be read here.

A state-reduced control attribute that is wrong also takes one
:data:`Certainty`, which says how the two states differ. The extraction mode
runs no lane, so whether a fact reached a lane is not read here.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, get_args

from evals.harness.alignment import Alignment
from evals.harness.modes import AttributeCheck, reduce_attribute

#: Where a check's value went wrong, if it did. The module docstring defines
#: each stage.
Stage = Literal[
    "agrees",
    "corrected-by-code",
    "wrong-in-raw",
    "changed-by-code",
    "changed-and-wrong",
    "uncaptured",
]
STAGES: tuple[Stage, ...] = get_args(Stage)

#: How the extracted state of a control attribute differs from the signed
#: state. ``unjustified-certainty``: the signed model states nothing and the
#: extraction states a control or its absence. ``silence-for-absence``: the
#: signed model states that the control is absent and the extraction states
#: nothing. ``silence-for-statement``: the signed model states a control and
#: the extraction states nothing. ``contradicts``: the two state opposite
#: things.
Certainty = Literal[
    "unjustified-certainty",
    "silence-for-absence",
    "silence-for-statement",
    "contradicts",
]
CERTAINTIES: tuple[Certainty, ...] = get_args(Certainty)

#: Each pair of differing states, signed first, and the certainty it shows.
#: ``silent`` is a protocol's reading of ``unverified``.
_CERTAINTY: Mapping[tuple[str, str], Certainty] = {
    ("unverified", "stated"): "unjustified-certainty",
    ("unverified", "absent"): "unjustified-certainty",
    ("silent", "stated"): "unjustified-certainty",
    ("absent", "unverified"): "silence-for-absence",
    ("stated", "unverified"): "silence-for-statement",
    ("stated", "silent"): "silence-for-statement",
    ("stated", "absent"): "contradicts",
    ("absent", "stated"): "contradicts",
}

#: The collections of an emission's ``raw`` block that hold elements.
_COLLECTIONS = (
    "external_entities",
    "processes",
    "data_stores",
    "data_flows",
    "trust_boundaries",
)


@dataclass(frozen=True)
class FactTrace:
    """One scored attribute of one paired element, raw and parsed."""

    check: AttributeCheck
    #: The raw value, reduced as the scorer reduces it. ``None`` where the raw
    #: block holds no element under the produced ID.
    raw: str | None

    @property
    def stage(self) -> Stage:
        if self.raw is None:
            return "uncaptured"
        raw_right = self.raw == self.check.blessed
        if self.check.agrees:
            return "agrees" if raw_right else "corrected-by-code"
        if raw_right:
            return "changed-by-code"
        return (
            "wrong-in-raw" if self.raw == self.check.extracted else "changed-and-wrong"
        )

    @property
    def certainty(self) -> Certainty | None:
        """How the two states differ, for a wrong state-reduced attribute."""
        return _CERTAINTY.get((self.check.blessed, self.check.extracted))

    def to_json(self) -> dict[str, Any]:
        return {
            **self.check.to_json(),
            "raw": self.raw,
            "stage": self.stage,
            "certainty": self.certainty,
        }


def raw_elements(raw: Mapping[str, Any]) -> dict[str, list[Mapping[str, Any]]]:
    """Each element of a raw emission, by its ID, in emission order.

    A list, because an emission can name one ID twice. Every shape the
    emission can take is read: a collection that is not a list and an entry
    that is not an object with a string ID hold no element.
    """
    found: dict[str, list[Mapping[str, Any]]] = {}
    for name in _COLLECTIONS:
        entries = raw.get(name)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                found.setdefault(entry["id"], []).append(entry)
    return found


def trace(
    raw: Mapping[str, Any], alignment: Alignment, checks: Sequence[AttributeCheck]
) -> tuple[FactTrace, ...]:
    """Each of ``checks`` with the raw value of the element the alignment paired.

    Where the raw block names the produced ID more than once, the first entry
    is read, and :func:`conflicting_ids` names the ID.
    """
    elements = raw_elements(raw)
    produced_of = alignment.produced_of
    traced = []
    for check in checks:
        entries = elements.get(produced_of.get(check.element_id, ""), [])
        value = entries[0].get(check.attribute) if entries else None
        traced.append(
            FactTrace(
                check,
                None
                if not entries or value is None
                else reduce_attribute(check.attribute, value),
            )
        )
    return tuple(traced)


def conflicting_ids(
    raw: Mapping[str, Any], attributes: Iterable[str]
) -> tuple[str, ...]:
    """The IDs the raw block names more than once with different ``attributes`` values."""
    names = tuple(attributes)
    return tuple(
        sorted(
            element_id
            for element_id, entries in raw_elements(raw).items()
            if len(
                {tuple(repr(entry.get(name)) for name in names) for entry in entries}
            )
            > 1
        )
    )


def pooled(traces: Iterable[FactTrace]) -> dict[str, Any]:
    """Each attribute's checks by stage, and its wrong checks by certainty."""
    stages: dict[str, Counter[Stage]] = {}
    certainties: dict[str, Counter[Certainty]] = {}
    for fact in traces:
        key = fact.check.key
        stages.setdefault(key, Counter())[fact.stage] += 1
        if fact.certainty is not None:
            certainties.setdefault(key, Counter())[fact.certainty] += 1
    return {
        "by_stage": {
            key: {stage: counts[stage] for stage in STAGES}
            for key, counts in sorted(stages.items())
        },
        "by_certainty": {
            key: {certainty: counts[certainty] for certainty in CERTAINTIES}
            for key, counts in sorted(certainties.items())
        },
    }
