"""The corpus diagrams are what ``evals/build_diagrams.py`` writes now."""

from __future__ import annotations

from evals import build_diagrams
from evals.harness.reference import diagnosable, load_corpus


def test_each_diagram_is_what_the_generator_writes():
    written = build_diagrams.diagrams()
    held = {
        path.stem: path.read_text(encoding="utf-8")
        for path in build_diagrams.DIAGRAMS.glob("*.mmd")
    }
    assert held == written, "run 'python evals/build_diagrams.py'"


def test_a_diagram_draws_each_element_and_flow_of_its_case():
    texts = build_diagrams.diagrams()
    for case in load_corpus(build_diagrams.CORPUS):
        if not diagnosable(case):
            assert case.meta.id not in texts
            continue
        lines = texts[case.meta.id].splitlines()
        nodes = [
            line
            for line in lines
            if line.lstrip().startswith("n") and "-->" not in line
        ]
        arrows = [line for line in lines if "-->" in line]
        assert len(nodes) == len(case.model.zoned_elements()), case.meta.id
        assert len(arrows) == len(case.model.data_flows), case.meta.id
