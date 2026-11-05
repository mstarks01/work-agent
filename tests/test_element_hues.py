"""One hue for each element, shared by the report's tags, its diagram and its
Mermaid download."""

from __future__ import annotations

import re
from itertools import pairwise
from pathlib import Path

from analysis_service.element_hues import FILL, LINE, element_hues, hsl_hex
from analysis_service.mermaid import model_mermaid, styled_mermaid
from analysis_service.sources import Source
from tests.factories import valid_model


def test_each_element_takes_a_hue_and_a_zone_or_flow_takes_none():
    model = valid_model()
    hues = element_hues(model)
    assert list(hues) == [element.id for element in model.zoned_elements()]
    assert not {zone.id for zone in model.trust_boundaries} & set(hues)
    assert not {flow.id for flow in model.data_flows} & set(hues)


def test_two_elements_next_in_the_list_take_far_hues():
    hues = list(element_hues(valid_model()).values())
    for first, second in pairwise(hues):
        assert min(abs(first - second), 360 - abs(first - second)) >= 90


def test_no_two_elements_of_a_large_model_share_a_hue():
    model = valid_model()
    stores = [
        model.data_stores[0].model_copy(update={"id": f"store:s{n}", "name": f"S{n}"})
        for n in range(40)
    ]
    hues = sorted(
        element_hues(model.model_copy(update={"data_stores": stores})).values()
    )
    gaps = [later - earlier for earlier, later in pairwise([*hues, hues[0] + 360])]
    assert len(set(hues)) == len(hues)
    assert min(gaps) >= 360 // len(hues) - 1


def test_a_colour_is_written_as_hex():
    assert hsl_hex(0, 100, 50) == "#ff0000"
    assert hsl_hex(120, 100, 25) == "#008000"


def test_the_page_and_the_service_state_one_light_tag():
    page = Path("webapp/static/workspace.css").read_text(encoding="utf-8")
    light = re.search(
        r"--tag-fill-s:(\d+)%; --tag-fill-l:(\d+)%;\s*--tag-line-s:(\d+)%;"
        r" --tag-line-l:(\d+)%;",
        page,
    )
    assert light is not None
    assert tuple(map(int, light.groups())) == (*FILL, *LINE)


def test_the_system_dark_theme_and_the_toggle_state_one_dark_tag():
    page = Path("webapp/static/workspace.css").read_text(encoding="utf-8")
    tags = [
        tuple(map(int, found))
        for found in re.findall(
            r"--tag-fill-s: ?(\d+)%; --tag-fill-l: ?(\d+)%;\s*--tag-line-s: ?(\d+)%;"
            r" --tag-line-l: ?(\d+)%;",
            page,
        )
    ]
    assert len(tags) == 3
    assert tags[1] == tags[2] != (*FILL, *LINE)


def test_the_download_styles_each_element_in_its_colour():
    model = valid_model()
    plain = model_mermaid(model)
    styled = styled_mermaid(model)
    assert styled.startswith(plain)
    hues = element_hues(model)
    assert styled[len(plain) :].splitlines() == [
        f"    style n{at} fill:{hsl_hex(hues[element.id], *FILL)},"
        f"stroke:{hsl_hex(hues[element.id], *LINE)}"
        for at, element in enumerate(model.zoned_elements())
    ]
    Source(kind="diagram", label="Model", text=styled)
