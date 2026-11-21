"""How important one claim is, on one scale that every framework shares.

**Each package's highest band ranks equal to every other package's** (ADR
0055). A package names its bands from the highest down, and a band's order
counts down from 0 at the top. So the highest band of every package has order
0, whatever the number of bands a package declares. A job that selects two
packages ranks their findings in one list, and this is the rule that compares
them: the position from the top, never a number that a package chose.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import NamedTuple

__all__ = ["UNRANKED", "Band", "band_of"]


class Band(NamedTuple):
    """How important one claim is, as its package ranks it.

    ``order`` ranks the bands, the higher first, and is 0 for a package's
    highest band. ``label`` is what a page shows, and empty for a package
    that grades nothing.
    """

    order: int
    label: str


def band_of(bands: Sequence[str], label: str) -> Band:
    """The band ``label`` names, where ``bands`` lists a package's bands from the highest.

    **The one reader of a band's order.** A label the list does not hold
    raises, so a package cannot rank a claim into a band it did not declare.
    """
    return Band(-bands.index(label), label)


#: The band of a question that no finding waits on: below every package's.
UNRANKED = Band(-sys.maxsize, "")
