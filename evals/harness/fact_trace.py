"""Where a wrong fact on a paired element went wrong: the raw emission or the code after it (#2271).

The extraction replay compares each scored attribute of a paired element with
the signed (blessed) model, after
:func:`~analysis_service.compact.parse_extraction` has turned the raw emission
into a model. That parse is deterministic code. So a wrong fact can come from
two places: ``extract`` emitted it, or the parse changed a value that arrived
right. This module reads the raw value of each check from the emission's
``raw`` block and reduces it with :func:`~evals.harness.modes.reduce_attribute`,
the scorer's own reduction.

**A raw entry is paired with its parsed element by position, never by ID.**
:func:`~analysis_service.validation.parse_and_validate` derives each ID again
from the element's name, so an emitted ID is only a link, and two entries can
carry one ID. The parse keeps the walk order, so the entry at one position of
one :data:`~analysis_service.system_model.ELEMENT_GROUPS` list is the element at
that position of the parsed model's list, which is how the parse pairs its own
two walks. A group whose two lists differ in length pairs nothing.

Each check takes one :data:`Stage`:

* ``agrees``: the raw value and the parsed value both agree with the signed one.
* ``corrected-by-code``: the raw value is wrong, and the parse made it right.
* ``wrong-in-raw``: the parse kept a raw value that is wrong.
* ``changed-by-code``: the raw value is right, and the parse made it wrong.
  This is the one stage that names a deterministic defect, and only a
  reproduction proves one.
* ``changed-and-wrong``: the raw value is wrong, and the parse changed it to
  another wrong value.
* ``raw-unread``: no raw value is read: the group pairs nothing, two parsed
  elements carry the check's ID, or the raw entry holds no value for the
  attribute.

A state-reduced control attribute that is wrong also takes one
:data:`Certainty`, which says how the two states differ. The extraction mode
runs no lane, so whether a fact reached a lane is not read here.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast, get_args

from analysis_service.analysis import ControlState
from analysis_service.system_model import ELEMENT_GROUPS, SystemModel, duplicate_ids
from evals.harness.alignment import Alignment, ProtocolState
from evals.harness.modes import STATE_REDUCED, AttributeCheck, reduce_attribute

#: Where a check's value went wrong, if it did. The module docstring defines
#: each stage.
Stage = Literal[
    "agrees",
    "corrected-by-code",
    "wrong-in-raw",
    "changed-by-code",
    "changed-and-wrong",
    "raw-unread",
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

#: The states the scorer reduces a control attribute or a protocol to, as
#: :func:`~analysis_service.analysis.control_state` and
#: :func:`~evals.harness.alignment.protocol_state` return them.
State = Literal[ControlState, ProtocolState]

#: Each pair of differing states, signed first, and the certainty it shows.
_CERTAINTY: Mapping[tuple[State, State], Certainty] = {
    ("unverified", "stated"): "unjustified-certainty",
    ("unverified", "absent"): "unjustified-certainty",
    ("silent", "stated"): "unjustified-certainty",
    ("absent", "unverified"): "silence-for-absence",
    ("stated", "unverified"): "silence-for-statement",
    ("stated", "silent"): "silence-for-statement",
    ("stated", "absent"): "contradicts",
    ("absent", "stated"): "contradicts",
}


@dataclass(frozen=True)
class FactTrace:
    """One scored attribute of one paired element, raw and parsed."""

    check: AttributeCheck
    #: The raw value, reduced as the scorer reduces it, as
    #: :attr:`~evals.harness.modes.AttributeCheck.blessed` and ``extracted``
    #: are. ``None`` where no raw value is read.
    raw: str | None

    @property
    def stage(self) -> Stage:
        if self.raw is None:
            return "raw-unread"
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
        if self.check.attribute not in STATE_REDUCED:
            return None
        pair = cast(tuple[State, State], (self.check.blessed, self.check.extracted))
        return _CERTAINTY.get(pair)

    def to_json(self) -> dict[str, Any]:
        return {
            **self.check.to_json(),
            "raw": self.raw,
            "stage": self.stage,
            "certainty": self.certainty,
        }


def emitted_entries(
    raw: Mapping[str, Any], parsed: SystemModel
) -> dict[str, Mapping[str, Any]]:
    """Each parsed element's raw entry, by the parsed element's ID.

    Paired by position within each group, as the module docstring says. A
    group whose raw value is not a list, or whose list is not as long as the
    parsed one, pairs nothing, and an entry that is not an object pairs
    nothing. A parsed ID that two elements carry pairs nothing either, because
    a check names its element by that ID and cannot say which one it means.
    """
    shared = duplicate_ids(parsed.elements())
    paired: dict[str, Mapping[str, Any]] = {}
    for group in ELEMENT_GROUPS:
        entries = raw.get(group)
        elements = getattr(parsed, group)
        if not isinstance(entries, list) or len(entries) != len(elements):
            continue
        for entry, element in zip(entries, elements, strict=True):
            if isinstance(entry, dict) and element.id not in shared:
                paired[element.id] = entry
    return paired


def trace(
    raw: Mapping[str, Any],
    parsed: SystemModel | None,
    alignment: Alignment,
    checks: Sequence[AttributeCheck],
) -> tuple[FactTrace, ...]:
    """Each of ``checks`` with the raw value of the element the alignment paired."""
    entries = emitted_entries(raw, parsed) if parsed is not None else {}
    produced_of = alignment.produced_of
    traced = []
    for check in checks:
        entry = entries.get(produced_of.get(check.element_id, ""), {})
        value = entry.get(check.attribute)
        traced.append(
            FactTrace(
                check,
                None if value is None else reduce_attribute(check.attribute, value),
            )
        )
    return tuple(traced)


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
