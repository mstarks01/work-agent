"""The three early-list orders of QA-2026-11-13-01 that were not shipped.

E2 lets only STRIDE rules rank a question kind. E3 and E4 change the turn rule
that ``answer_state.by_turn`` applies: each takes a question kind's rows as one
unit, as ``grouped()`` lays them. E3 gives the next unit to the framework that
``whose_turn`` names. E4 adds the cost of each framework's next unit to its
charge before it asks ``whose_turn``. Build the folder of reports the
comparison read, and run it::

    while read -r path; do
        mkdir -p "<root>/$(dirname "$path")" && ln -s "$PWD/$path" "<root>/$path"
    done < evals/experiments/QA-2026-11-13-01/reports.txt
    python evals/experiments/QA-2026-11-13-01/variants.py <root>

It reads each order with ``early-policies``'s walk and prints each figure that
differs from ``variants.json``.
"""

import json
import sys
from collections import Counter
from itertools import groupby
from pathlib import Path
from unittest import mock

import analysis_service.early_questions as early_module
from analysis_service import answer_state
from analysis_service.frameworks import PACKAGES
from analysis_service.question_kinds import parse_question_kind
from evals.harness.early_policies import _each, _shipped_list, paired, summary, sweep

SHIPPED_READS = early_module.reads
ASVS_LANES = frozenset(PACKAGES["asvs"].lanes)


def _stride_reads(candidate, informed_by, field):
    """The shipped reader, with no ASVS rule reading a question kind."""
    if parse_question_kind(field) is not None and candidate.lane in ASVS_LANES:
        return False
    return SHIPPED_READS(candidate, informed_by, field)


def _units(questions):
    """Each kind's rows as one unit, in the order ``grouped()`` gives.

    A capability question is a unit of its own, as it keeps its own place there.
    """
    units = []
    for group, rows in groupby(answer_state.grouped(questions), lambda q: q.group):
        if group == answer_state.CAPABILITY_GROUP:
            units += [[row] for row in rows]
        else:
            units.append(list(rows))
    return units


def _by_unit(lookahead):
    """A turn rule that takes one unit at a time."""

    def by_turn(questions, charged):
        charged = Counter(charged or {})
        left = _units(questions)
        order = []
        while left:
            served = {name for unit in left for q in unit for name in q.frameworks}
            if not served:
                return order + [q for unit in left for q in unit]
            first = {
                name: next(u for u in left if any(name in q.frameworks for q in u))
                for name in served
            }
            cost = {
                name: charged[name]
                + (sum(q.decisions for q in unit) if lookahead else 0)
                for name, unit in first.items()
            }
            unit = first[answer_state.whose_turn(served, cost)]
            left.remove(unit)
            order += unit
            for question in unit:
                for holder in question.frameworks:
                    charged[holder] += question.decisions
        return order

    return by_turn


def stride_rules_only(model, chosen):
    """E2: only STRIDE rules rank a question kind."""
    with mock.patch.object(early_module, "reads", _stride_reads):
        return _each(_shipped_list(model, chosen))


def unit_turns(model, chosen):
    """E3: a kind's rows take one turn."""
    with mock.patch.object(answer_state, "by_turn", _by_unit(lookahead=False)):
        return _each(_shipped_list(model, chosen))


def lookahead_turns(model, chosen):
    """E4: a kind's rows take one turn, and the turn reads the next unit's cost."""
    with mock.patch.object(answer_state, "by_turn", _by_unit(lookahead=True)):
        return _each(_shipped_list(model, chosen))


def main(root: Path) -> None:
    """Read E2, E3 and E4 over ``root`` and print each figure unlike the record."""
    variants = {
        "stride-rules-only": stride_rules_only,
        "unit-turns": unit_turns,
        "lookahead-turns": lookahead_turns,
    }
    with mock.patch("evals.harness.early_policies.POLICIES", variants):
        found = summary(sweep(paired(root)))
    recorded = json.loads(Path(__file__).with_name("variants.json").read_text())
    differ = [
        (group, row)
        for group, rows in found.items()
        for row, figures in rows.items()
        if recorded["summary"][group].get(row) != figures
    ]
    print(f"{len(differ)} figures differ from variants.json", *differ, sep="\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
