"""Shares, and how a report shows one that has no value.

A share over an empty denominator is a gap in what was measured, not a
measured zero. :func:`ratio` is the one reader of that rule: it gives ``None``,
and every printer shows :data:`UNAVAILABLE`.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

#: The word :func:`shown_figure` prints for a figure with no value: a gap,
#: never a zero.
UNAVAILABLE = "unavailable"


def shown_figure(value: float | None, spec: str) -> str:
    """A figure in the format ``spec``, or :data:`UNAVAILABLE` where it has none."""
    return UNAVAILABLE if value is None else format(value, spec)


def percent(value: float | None) -> str:
    """A share as a whole percentage, as :func:`shown_figure` prints it."""
    return shown_figure(value, ".0%")


def ratio(numerator: float, denominator: float) -> float | None:
    """``numerator / denominator``, or ``None`` where the denominator is empty.

    An empty denominator is a gap in what was measured. A zero there would
    read as a measured zero, so the share has no value and a report prints
    :data:`UNAVAILABLE`.
    """
    return numerator / denominator if denominator else None


def rounded_figure(value: float | None, places: int) -> float | None:
    """A figure rounded to ``places`` for an artifact, or ``None`` where it has none."""
    return None if value is None else round(value, places)


@dataclass(frozen=True)
class Rate:
    """A share with its counts. ``value`` is ``None`` on an empty denominator."""

    numerator: int
    denominator: int

    @property
    def value(self) -> float | None:
        return ratio(self.numerator, self.denominator)

    def shown(self) -> str:
        """The rate as a report prints it: its counts, then its share."""
        return f"{self.numerator}/{self.denominator} ({percent(self.value)})"

    def rounded(self) -> float | None:
        """:attr:`value` to three places, as an artifact records it."""
        return rounded_figure(self.value, 3)

    def to_json(self) -> dict[str, Any]:
        return {
            "numerator": self.numerator,
            "denominator": self.denominator,
            "value": self.value,
        }

    @classmethod
    def from_json(cls, written: Mapping[str, Any]) -> Rate:
        """The rate :meth:`to_json` wrote; its ``value`` is derived, not read."""
        return cls(written["numerator"], written["denominator"])


def mean(values: Iterable[float | None]) -> float | None:
    """The mean of the values that have one, or ``None`` where none has.

    A value with none is a gap, not a zero, so it adds nothing to the mean.
    """
    held = [value for value in values if value is not None]
    return ratio(sum(held), len(held))


def rounded_difference(left: float | None, right: float | None) -> float | None:
    """``left - right`` to three places, or ``None`` where either has no value."""
    return None if left is None or right is None else round(left - right, 3)


def jaccard(left: Collection[str], right: Collection[str]) -> float:
    """The overlap of two sets over their union.

    Two empty sets agree completely, so their Jaccard is 1.0. That is a
    definition, not a gap: nothing is missing from either side.
    """
    union = set(left) | set(right)
    return 1.0 if not union else len(set(left) & set(right)) / len(union)


#: Whether a set of readings of one figure holds a value: in every reading, in
#: none, or in some and not others.
Presence = Literal["every", "none", "some"]


def presence(values: Sequence[float | None]) -> Presence:
    """Which of :data:`Presence` the readings ``values`` are."""
    held = sum(value is not None for value in values)
    if held == len(values):
        return "every"
    return "none" if held == 0 else "some"


def complete_mean(values: Sequence[float | None]) -> float | None:
    """The mean where every value is held, or ``None`` where any is missing.

    For a figure compared between two artifacts over one selection of cases:
    a mean that skipped a case on one side would cover other cases than the
    mean it is compared with.
    """
    return mean(values) if presence(values) == "every" else None
