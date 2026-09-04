"""Measure what the System Model's free-text attributes carry (wayfinder #483).

Every number in ``system-model-evolution.md`` comes from this script. Run it
from the repository root::

    uv run python docs/research/probe_model_vocabulary.py

It reads the 13 blessed corpus models under ``evals/corpus/*/model.json`` and
the ASVS presence tests, and reports four things:

1. which attribute answers an ASVS presence test, over every element;
2. how many presence tests survive if ``description`` and ``notes`` are removed;
3. every stated value of each control attribute, so a reader can count the
   distinct facts fused into one string;
4. control values that ``control_state`` reads as ``stated`` while the value
   itself says a fact is not stated.

This is a probe, not a gate. It asserts nothing and fails no build.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from analysis_service.analysis import CONTROL_ATTRIBUTES, control_state, matches_term
from analysis_service.frameworks.asvs.rules import PRESENCE_TESTS
from analysis_service.system_model import SystemModel

CORPUS = Path("evals/corpus")
PROSE_ATTRIBUTES = frozenset({"description", "notes"})

#: Phrases a submitter writes to say a fact is *not* stated. Used only to
#: report the overlap with ``control_state`` — it is not a classifier.
HEDGE = re.compile(
    r"not stated|never verified|not written down|nobody can say|unstated"
    r"|no[bt]? .{0,20}checked",
    re.IGNORECASE,
)


def models() -> dict[str, SystemModel]:
    """Every blessed corpus model, keyed by case directory name."""
    return {
        path.parent.name: SystemModel.model_validate(json.loads(path.read_text()))
        for path in sorted(CORPUS.glob("*/model.json"))
    }


def fired_tests(model: SystemModel, skip: frozenset[str]) -> tuple[set[str], Counter]:
    """Presence tests this model answers, and which attribute answered each."""
    fired: set[str] = set()
    answered: Counter = Counter()
    for test in PRESENCE_TESTS:
        for element in model.elements():
            for attribute in test.attributes:
                if attribute in skip:
                    continue
                value = getattr(element, attribute, "")
                if not isinstance(value, str):
                    continue
                lowered = value.lower()
                if any(matches_term(term, lowered) for term in test.terms):
                    fired.add(test.rule_id)
                    answered[attribute] += 1
                    break
    return fired, answered


def main() -> None:
    corpus = models()
    elements = sum(len(model.elements()) for model in corpus.values())
    print(f"cases {len(corpus)}, elements {elements}\n")

    answered: Counter = Counter()
    with_prose = without_prose = 0
    lost: Counter = Counter()
    for model in corpus.values():
        full, hits = fired_tests(model, frozenset())
        typed, _ = fired_tests(model, PROSE_ATTRIBUTES)
        answered += hits
        with_prose += len(full)
        without_prose += len(typed)
        for rule_id in full - typed:
            lost[rule_id] += 1

    print("-- which attribute answered a presence test --")
    total = sum(answered.values())
    for attribute, count in answered.most_common():
        print(f"  {count:4d}  {100 * count / total:4.0f}%  {attribute}")

    print(
        f"\n-- presence tests fired: {with_prose} with prose,"
        f" {without_prose} without description and notes"
        f" ({with_prose - without_prose} lost) --"
    )
    for rule_id, count in lost.most_common():
        print(f"  {count:2d}/{len(corpus)}  {rule_id}")

    print("\n-- stated control values, by attribute --")
    for attribute in CONTROL_ATTRIBUTES:
        values = sorted(
            {
                value
                for model in corpus.values()
                for element in model.elements()
                if isinstance(value := getattr(element, attribute, ""), str)
                and value
                and control_state(value) == "stated"
            }
        )
        print(f"\n  {attribute}: {len(values)} distinct stated values")
        for value in values:
            print(f"    - {value}")

    print("\n-- read as `stated`, while the value says a fact is not stated --")
    for case, model in corpus.items():
        for element in model.elements():
            for attribute in CONTROL_ATTRIBUTES:
                value = getattr(element, attribute, "")
                if not isinstance(value, str) or control_state(value) != "stated":
                    continue
                if HEDGE.search(value):
                    print(f"  [{case}] {attribute} on {element.id}\n      {value}")


if __name__ == "__main__":
    main()
