"""The System Model as Mermaid text, and the report page's link to it."""

from __future__ import annotations

import json
import re

from analysis_service.mermaid import model_mermaid
from analysis_service.system_model import UNKNOWN, SystemModel
from tests.factories import report_state, sample_report, sample_threat, valid_model
from tests.test_report_page import run_report_page, two_findings
from webapp.main import render_report

HOSTILE = (
    'end"]; click n0 call alert(1)\n<img src=x onerror=alert(1)> `x` a|b #quot; &'
    " %%{init: {'theme': 'dark'}}%%"
)


def test_each_zone_is_a_subgraph_and_each_kind_its_shape():
    assert model_mermaid(valid_model()) == (
        "flowchart LR\n"
        '    subgraph z0["Internet"]\n'
        '        n0["Customer"]\n'
        "    end\n"
        '    subgraph z1["Internal Network"]\n'
        '        n1("Web App")\n'
        '        n2[("Orders DB")]\n'
        "    end\n"
        '    n0 -->|"Login"| n1\n'
        "    linkStyle 0 stroke:#c0392b,stroke-width:2px\n"
        '    n1 -->|"Store Order"| n2\n'
    )


def test_an_unplaced_element_sits_outside_every_zone_and_its_flow_may_cross():
    model = valid_model().model_copy(deep=True)
    model.data_stores[0].trust_zone = UNKNOWN
    text = model_mermaid(model)
    assert '\n    n2[("Orders DB")]\n' in text
    assert "    linkStyle 1 stroke-dasharray:3 3\n" in text


def test_a_model_with_no_element_writes_nothing():
    assert model_mermaid(SystemModel()) == ""


def test_a_submitted_name_stays_inside_its_label():
    """A name reaches Mermaid only as a quoted label on one line, with each
    character Mermaid or HTML reads as syntax written as an entity code."""
    model = valid_model().model_copy(deep=True)
    model.external_entities[0].name = HOSTILE
    model.trust_boundaries[0].name = HOSTILE
    model.data_flows[0].name = HOSTILE
    text = model_mermaid(model)
    labels = re.findall(r'"([^"]*)"', text)
    hostile = [label for label in labels if label.startswith("end#34;")]
    assert len(hostile) == 3
    for label in hostile:
        assert not set(label) & set("<>`|&%"), label
        assert "#35;quot;" in label
    assert len(text.splitlines()) == len(model_mermaid(valid_model()).splitlines())


def test_the_report_page_carries_the_mermaid_text():
    report = sample_report([sample_threat()])
    (carried,) = re.findall(
        r'id="mermaid"[^>]*>(.*?)</script>',
        render_report(report, report_state(report), previous=None, parent=None).html,
    )
    assert json.loads(carried) == model_mermaid(report.system_model)


def test_the_diagram_links_to_its_mermaid_text():
    drawn = run_report_page(two_findings(), "?view=about")["model-diagram"]
    assert "Download as Mermaid" in drawn
