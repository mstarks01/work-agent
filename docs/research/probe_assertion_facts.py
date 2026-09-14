"""How many facts does one control attribute carry? (wayfinder #926)

Run from the repository root::

    uv run python docs/research/probe_assertion_facts.py

Reads the 13 blessed corpus models. Prints three tables:

1. what the graph's five control attributes hold, by ``control_state``;
2. every stated mechanism value, with two mechanical marks;
3. what the evidence catalog offers for the marked values.

It rules nothing. It measures how much of a source's statement survives the
one string a control attribute holds.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from analysis_service.analysis import CONTROL_ATTRIBUTES, control_state
from analysis_service.basis import IN_SCOPE
from analysis_service.evidence import evidence_catalog
from analysis_service.system_model import attribute_names
from evals.harness.reference import load_case

CORPUS = Path("evals/corpus")

# A stated value that also states an absence or a gap. The words are English
# negations, not a vocabulary fitted to what the corpus turned out to hold:
# `analysis_service.grounding._NEGATIONS` is the same list, and it was written
# for the repair rung's meaning check long before this probe.
NEGATION = re.compile(
    r"\b(no|not|never|none|nor|neither|without|cannot)\b",
    re.IGNORECASE,
)

# A stated value that carries more than one clause. A separator is where a
# writer joined two statements; it says nothing about how many facts each
# clause holds, so this is a floor under the count and never the count.
SEPARATOR = re.compile(r"[;,]| and ")


def cases():
    for directory in sorted(CORPUS.iterdir()):
        if directory.is_dir():
            yield load_case(directory)


def main() -> None:
    states: dict[str, Counter[str]] = {a: Counter() for a in CONTROL_ATTRIBUTES}
    stated: list[tuple[str, str, str, str]] = []
    elements = sources = 0

    for case in cases():
        sources += len(case.sources)
        for element in case.model.elements():
            elements += 1
            for attribute in attribute_names(element):
                if attribute not in states:
                    continue
                value = getattr(element, attribute)
                state = control_state(value)
                states[attribute][state] += 1
                if state == "stated" and attribute in IN_SCOPE:
                    stated.append((case.id, element.id, attribute, value))

    print(f"13 blessed cases: {elements} elements, {sources} sources\n")

    print("What the five control attributes hold")
    print(f"{'attribute':24s} {'stated':>7s} {'absent':>7s} {'unverified':>11s}")
    for attribute, counter in states.items():
        print(
            f"{attribute:24s} {counter['stated']:7d} {counter['absent']:7d}"
            f" {counter['unverified']:11d}"
        )

    marked = [row for row in stated if NEGATION.search(row[3])]
    split = [row for row in stated if SEPARATOR.search(row[3])]
    print(
        f"\nEvery stated mechanism value ({len(stated)} of them):"
        f" {len(marked)} state an absence, {len(split)} carry a separator"
    )
    for case_id, element_id, attribute, value in stated:
        marks = "".join(
            (
                "A" if NEGATION.search(value) else " ",
                "S" if SEPARATOR.search(value) else " ",
            )
        )
        print(f"  {marks}  {case_id[:2]} {attribute:22s} {value}")

    print("\nWhat the evidence catalog offers for the values that state an absence")
    offered = 0
    for case in cases():
        catalog = evidence_catalog(case.model)
        for case_id, element_id, attribute, _ in marked:
            if case_id != case.id:
                continue
            refs = [
                ref
                for ref in catalog
                if ref.endswith(f":{element_id}:{attribute}")
                or ref == f"unknown:{element_id}:{attribute}"
            ]
            offered += len(refs)
            print(f"  {case_id[:2]} {element_id} {attribute}: {refs or 'nothing'}")
    print(f"  total entries offered: {offered}")


if __name__ == "__main__":
    main()
