"""One hue for each element of a model, so the report's tags, its diagram and
its Mermaid download colour an element alike.

Each element, in the model's order, takes the next step of the golden angle
round the colour wheel. So two elements next to each other in the list take
hues far apart, and the hues stay the same for one model on every view.
"""

from __future__ import annotations

import colorsys

from analysis_service.system_model import SystemModel

__all__ = ["FILL", "LINE", "element_hues", "hsl_hex"]

#: The step between the hues of two elements, in degrees.
GOLDEN_ANGLE = 137.508
#: The hue of the first element, a blue.
FIRST_HUE = 210
#: The saturation and the lightness, in percent, of an element's fill and of
#: its outline in the light theme. The report page's CSS states the same
#: numbers, and a test checks that the two agree.
FILL = (70, 93)
LINE = (50, 40)


def element_hues(model: SystemModel) -> dict[str, int]:
    """The hue of each element of ``model``, in degrees, by element ID.

    **The one reader of an element's colour.** A trust zone and a flow take
    none: a zone is drawn as a frame, and a flow as its two endpoints.
    """
    return {
        element.id: round(FIRST_HUE + at * GOLDEN_ANGLE) % 360
        for at, element in enumerate(model.zoned_elements())
    }


def hsl_hex(hue: int, saturation: int, lightness: int) -> str:
    """The colour of ``hue`` at ``saturation`` and ``lightness`` percent, as
    ``#rrggbb``."""
    red, green, blue = colorsys.hls_to_rgb(hue / 360, lightness / 100, saturation / 100)
    return "#" + "".join(
        f"{round(channel * 255):02x}" for channel in (red, green, blue)
    )
