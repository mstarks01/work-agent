"""Shares, and how a report shows one that has no value.

A share over an empty denominator is a gap in what was measured, not a
measured zero. :func:`ratio` is the one reader of that rule: it gives ``None``,
and every printer shows :data:`UNAVAILABLE`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

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


def rounded_share(value: float | None, places: int) -> float | None:
    """A share rounded to ``places`` for an artifact, or ``None`` where it has none."""
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
        return rounded_share(self.value, 3)

    def to_json(self) -> dict[str, Any]:
        return {
            "numerator": self.numerator,
            "denominator": self.denominator,
            "value": self.value,
        }


def mean(values: Iterable[float | None]) -> float | None:
    """The mean of the values that have one, or ``None`` where none has.

    A value with none is a gap, not a zero, so it adds nothing to the mean.
    """
    held = [value for value in values if value is not None]
    return ratio(sum(held), len(held))


def difference(left: float | None, right: float | None) -> float | None:
    """``left - right`` to three places, or ``None`` where either has no value."""
    return None if left is None or right is None else round(left - right, 3)
