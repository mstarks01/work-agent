"""How often one shared answer is wrong for a part its box lists (#2029 package 2).

The early list asks a box of same-shape questions once (ADR 0081): two or more
rows of one attribute or kind, with one form and one set of choices. Matching
shape does not prove that one control covers every part. This command reads,
over the corpus, how often the parts of such a box have different true values.

**The truth is the case's blessed model.** Each attribute the early list can
ask of an element (a field the prior rates for its type, read from
:data:`~analysis_service.early_questions.QUESTION_PRIOR`) that the blessed
model states is made unknown, so the list asks it, and its blessed value is
the true answer. A question kind writes nothing onto the model and no blessed
model states one, so a box of question kinds has no truth and is counted
apart.

**A box is built as the page builds it**: the questions the paused list shows,
taken in order, a new box for each run of one ``group``, shared where it holds
two or more rows that :func:`shareable` pairs. :func:`shareable` is the page's
``shareable`` rule in ``question_list.js``, and a test holds the two readers
to each other.

**Two owners are read.** One answers the box once with the most common true
value and opens no exception, as the box offers (ADR 0081): each part with
another true value is an over-claim. One picks the parts each answer covers
(ADR 0113): the shared answer covers the parts with the most common value, one
tick each, and every other part takes its own answer. Effort is choices: one
for the shared answer, one a tick, one a part answered on its own.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from itertools import groupby
from pathlib import Path
from typing import Any

from analysis_service.analysis import control_state
from analysis_service.claims import FrameworkName
from analysis_service.early_questions import QUESTION_PRIOR, EarlyQuestion
from analysis_service.frameworks import PACKAGES
from analysis_service.system_model import SystemModel
from evals.harness.early_rounds import paused_state
from evals.harness.run_record import run_record

#: The options each framework is read with, keyed by framework. ASVS sets no
#: default level, so the instrument names one.
OPTIONS: Mapping[FrameworkName, Mapping[str, Any]] = {
    "stride": {},
    "asvs": {"level": 2},
}

#: Each case is read with each framework selected alone. Self-completing: a
#: package missing from :data:`OPTIONS` raises here.
SELECTIONS: Mapping[str, Mapping[FrameworkName, Mapping[str, Any]]] = {
    name: {name: OPTIONS[name]} for name in PACKAGES
}


def shareable(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    """Whether a page asks the two served questions once: the page's rule."""
    return (
        bool(a["key"][0])
        and bool(b["key"][0])
        and a["key"][1] == b["key"][1]
        and a["key"][4] == b["key"][4]
        and a["form"] == b["form"]
        and a["choices"] == b["choices"]
    )


def blanked(blessed: SystemModel) -> tuple[SystemModel, dict[tuple[str, str], str]]:
    """The blessed model with each stated attribute the early list can ask made
    unknown, and the true value of each."""
    model = blessed.model_copy(deep=True)
    truth: dict[tuple[str, str], str] = {}
    for element in model.elements():
        fields = {
            field
            for row in QUESTION_PRIOR.values()
            for field in row.rates.get(type(element).__name__, {})
        } & set(type(element).model_fields)
        for field in sorted(fields):
            value = getattr(element, field)
            if isinstance(value, str) and control_state(value) != "unverified":
                truth[(element.id, field)] = value
                setattr(element, field, "unknown")
    return model, truth


def shared_boxes(listed: Sequence[EarlyQuestion]) -> list[list[EarlyQuestion]]:
    """The boxes a page asks once, in the list's order."""
    boxes = []
    for _, run in groupby(listed, key=lambda question: question.group):
        rows = list(run)
        served = [row.to_json() for row in rows]
        if len(rows) >= 2 and all(shareable(one, served[0]) for one in served):
            boxes.append(rows)
    return boxes


@dataclass(frozen=True)
class Box:
    """One shared box of one case under one selection."""

    case: str
    selected: str
    group: str
    parts: int
    #: Parts with a true value; the rest are question kinds or unstated.
    known: int
    distinct: int
    #: Parts whose true value is not the most common one.
    unlike: int
    #: Choices each owner spends on the parts with a true value.
    choices_shared: int
    choices_picked: int


def boxes(case: str, blessed: SystemModel) -> list[Box]:
    """Every shared box the paused list asks of ``case``, under each selection."""
    model, truth = blanked(blessed)
    found = []
    for selected, frameworks in SELECTIONS.items():
        listed = paused_state(model, frameworks, (), ()).questions.early
        for rows in shared_boxes(listed):
            values = [
                truth[place]
                for row in rows
                if (place := (row.key[0], row.key[1])) in truth
            ]
            top = Counter(values).most_common(1)[0][1] if values else 0
            unlike = len(values) - top
            found.append(
                Box(
                    case=case,
                    selected=selected,
                    group=rows[0].group,
                    parts=len(rows),
                    known=len(values),
                    distinct=len(set(values)),
                    unlike=unlike,
                    choices_shared=1 if values else 0,
                    choices_picked=(1 + top + unlike) if values else 0,
                )
            )
    return found


def sweep(corpus: Path) -> tuple[list[Box], list[Path]]:
    """Every case's shared boxes, and the blessed models read."""
    paths = sorted(corpus.glob("*/model.json"))
    found = [
        box
        for path in paths
        for box in boxes(
            path.parent.name,
            SystemModel.model_validate_json(path.read_text(encoding="utf-8")),
        )
    ]
    return found, paths


#: The counts a summary sums over boxes.
SUMMED = ("parts", "known", "unlike", "choices_shared", "choices_picked")


def summary(found: Sequence[Box]) -> dict[str, dict[str, int]]:
    """Sums by selection: boxes, boxes with a truth, boxes of one true value,
    and :data:`SUMMED`."""
    sums: dict[str, Counter[str]] = {}
    for box in found:
        row = sums.setdefault(box.selected, Counter())
        row["boxes"] += 1
        row["boxes with a truth"] += bool(box.known)
        row["boxes of one true value"] += box.distinct == 1
        for field in SUMMED:
            row[field] += getattr(box, field)
    return {selected: dict(sorted(row.items())) for selected, row in sums.items()}


def arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--corpus", type=Path, default=Path("evals/corpus"), help="the corpus"
    )
    parser.add_argument(
        "--out", type=Path, help="write the run record, the summary and each box"
    )


def command_shared_answers(args: argparse.Namespace) -> int:
    """Read every case's shared boxes, and print the sums."""
    found, paths = sweep(args.corpus)
    if not paths:
        print("no blessed model under the corpus", file=sys.stderr)
        return 1
    table = summary(found)
    print(json.dumps(table, indent=2))
    if args.out is not None:
        record = run_record(
            "shared-answers",
            {"selections": dict(SELECTIONS)},
            {path: {} for path in paths},
        )
        args.out.write_text(
            json.dumps(
                {
                    "record": record,
                    "summary": table,
                    "boxes": [asdict(box) for box in found],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return 0
