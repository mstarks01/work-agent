"""E2: the target must-finds each pass matched, re-scored offline from its answers.

Run from the repository root: ``PYTHONPATH=. uv run python
evals/experiments/QA-2026-10-09-03/summarize.py <run directory>``. Each
``<arm>-p<pass>/<case>--<lane>.json`` is one lane's proposals, as
``lane-replay --out`` wrote them, and its ``.log`` carries the reported charge.
"""

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from evals.harness.descendants import FRAMEWORK, lane_must_finds
from evals.harness.lane_replay import prepare_today
from evals.harness.provenance import REPO_ROOT
from evals.harness.reference import DEFAULT_CORPUS, load_case

TARGETS = Path(__file__).parent / "targets.json"
SPENT = re.compile(r"^spent: \$([0-9.]+)", re.MULTILINE)


def main(run_dir):
    targets = json.loads(TARGETS.read_text())
    cases = {}
    prepared = {}
    hits = defaultdict(list)
    spent = 0.0
    calls = 0
    for answer in sorted(run_dir.glob("*-p*/*.json")):
        arm = answer.parent.name.rsplit("-p", 1)[0]
        case_id, lane = answer.stem.split("--")
        if case_id not in cases:
            cases[case_id] = load_case(REPO_ROOT / DEFAULT_CORPUS / case_id)
        if (case_id, arm) not in prepared:
            prepared[case_id, arm] = prepare_today(cases[case_id], FRAMEWORK, arm)
        state = prepared[case_id, arm].state
        batch = json.loads(answer.read_text())
        matched = lane_must_finds(cases[case_id], state, lane, batch)
        wanted = targets[f"{case_id}:{lane}"]
        hits[answer.parent.name] += [f"{case_id}/{i}" for i in matched if i in wanted]
        log = answer.with_suffix(".log").read_text()
        spent += sum(float(value) for value in SPENT.findall(log))
        calls += 1
    for name in sorted(run_dir.glob("*-p*")):
        found = hits[name.name]
        print(f"{name.name}: {len(found)} of 25 targets matched {sorted(found)}")
    print(f"{calls} calls, ${spent:.4f} reported")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
