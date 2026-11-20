"""One hue for each element of a model, so the report's tags, its diagram and
its Mermaid download colour an element alike.

The hues of a model's elements are spread evenly round the colour wheel, one
for each element, so no two elements of a model of up to 360 elements take
the same hue. The
elements take them in an order that steps far round the wheel each time, so
two elements next to each other in the list take hues far apart. The hues stay
the same for one model on every view.
"""

from __future__ import annotations

import colorsys
from math import gcd

from analysis_service.system_model import SystemModel

__all__ = ["FILL", "LINE", "element_hues", "hsl_hex"]

#: The part of the wheel one step goes round, near the golden ratio's. The
#: step is prime to the count, so it reaches every slot once.
GOLDEN_FRACTION = 0.382
#: The hue of the first element, a blue.
FIRST_HUE = 210
#: The saturation and the lightness, in percent, of an element's fill and of
#: its outline in the light theme. ``webapp/static/workspace.css`` states the same
#: numbers, and a test checks that the two agree.
FILL = (25, 96)
LINE = (25, 55)


def element_hues(model: SystemModel) -> dict[str, int]:
    """The hue of each element of ``model``, in degrees, by element ID.

    The report's tags, its diagram and its Mermaid download read this map. A
    trust zone and a flow take none: a zone is drawn as a frame, and a flow as
    its two endpoints. The step is prime to the number of elements, so each
    element takes a slot of its own.
    """
    elements = model.zoned_elements()
    count = len(elements)
    step = max(1, round(count * GOLDEN_FRACTION))
    while gcd(step, count) != 1:
        step += 1
    return {
        element.id: round(FIRST_HUE + 360 * (at * step % count) / count) % 360
        for at, element in enumerate(elements)
    }


def hsl_hex(hue: int, saturation: int, lightness: int) -> str:
    """The colour of ``hue`` at ``saturation`` and ``lightness`` percent, as
    ``#rrggbb``."""
    red, green, blue = colorsys.hls_to_rgb(hue / 360, lightness / 100, saturation / 100)
    return "#" + "".join(
        f"{round(channel * 255):02x}" for channel in (red, green, blue)
    )
