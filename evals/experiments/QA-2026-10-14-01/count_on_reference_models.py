"""Lever C's effect on each development case's reference model, offline."""

import collections
import json
import sys
from pathlib import Path

from analysis_service.answer_sets import AnswerSet
from analysis_service.answer_state import question_set
from analysis_service.field_capabilities import with_field_capabilities
from analysis_service.frameworks import PACKAGES
from analysis_service.system_model import CapabilityStatement, SystemModel

D = Path("evals/experiments/QA-2026-10-14-01")


def page(model, level):
    qs = question_set(
        model,
        None,
        {"asvs": {"level": level}},
        [],
        waiting=True,
        answered=AnswerSet(),
        final=False,
        shown=[],
    )
    listed = [q for q in qs.early if q.kind == "capability"]
    off = [q for q in (*qs.held_back, *qs.below_floor) if q.kind == "capability"]
    applicable = sum(
        1
        for e in PACKAGES["asvs"].record.applicability(model, {"level": level})
        if e.state == "applicable"
    )
    return len(listed), len(off), applicable


def with_reader(model, kept, label):
    updated = model.model_copy(deep=True)
    named = {s.capability for s in updated.capabilities}
    for entry in kept:
        if entry["capability"] not in named:
            updated.capabilities.append(
                CapabilityStatement(
                    capability=entry["capability"],
                    state="present",
                    source_excerpt=entry["quote"],
                    source_label=label,
                )
            )
            named.add(entry["capability"])
    return updated


runs = json.loads((D / "case-runs-v2.json").read_text())["results"]
out: dict[str, dict] = {}
for run in runs:
    case = run["case"]
    model = SystemModel.model_validate_json(
        Path("evals/corpus", case, "model.json").read_text()
    )
    label = next((e.source_label for e in model.elements() if e.source_label), "Source")
    base = with_field_capabilities(model)
    both = with_reader(base, run["kept"], label)
    out[case] = {
        f"L{level}": {
            "lever A": page(base, level),
            "lever A + reader": page(both, level),
        }
        for level in (1, 2)
    }
total: collections.Counter[str] = collections.Counter()
for case, levels in out.items():
    a, b = levels["L2"]["lever A"], levels["L2"]["lever A + reader"]
    print(
        f"{case:34} L2 listed {a[0]:>2}->{b[0]:>2}  off {a[1]:>3}->{b[1]:>3}  applicable {a[2]:>3}->{b[2]:>3}"
    )
    for i, k in enumerate(("listed", "off", "applicable")):
        total[f"{k} before"] += a[i]
        total[f"{k} after"] += b[i]
print(dict(total))
Path(sys.argv[1]).write_text(json.dumps(out, indent=1) + "\n")
