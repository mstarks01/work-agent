"""Run only the box-order policies, with each report's rounds built once."""

import json
import sys
from pathlib import Path

from evals.harness import early_policies as ep

keep = [
    "shipped-rounds",
    "grouped-rounds",
    "boxes-in-turn",
    "boxes-in-turn-smaller",
    "capabilities-last",
    "capabilities-at-middle",
    "capability-boxes",
]
ep.POLICIES = {name: ep.POLICIES[name] for name in keep}
built = ep._rounds
cache: dict[tuple, tuple] = {}


def rounds(model, frameworks):
    key = (id(model), tuple(sorted(frameworks)))
    if key not in cache:
        cache[key] = (model, built(model, frameworks))
    return cache[key][1]


ep._rounds = rounds
inner = ep.readings


def readings(*a, **k):
    cache.clear()
    return inner(*a, **k)


ep.readings = readings
found = ep.sweep(Path("evals"))
Path(sys.argv[1]).write_text(
    json.dumps(
        {
            "summary": ep.summary(found),
            "by_case": {
                c: ep.summary([f for f in found if f.case == c])
                for c in sorted({f.case for f in found})
            },
        },
        indent=2,
    )
)
