"""Score both arms of QA-2026-11-05-01-E1 with the readers of QA-2026-11-03-01.

Usage, from the repository root: ``score_arms.py``. The treatment arm is
stage 1 of QA-2026-11-03-01 (``../QA-2026-11-03-01/runs/id-*.json``); the
control arm is ``runs/ctl-*.json``. Each call is read by
``score_stage.read_run`` and pooled by ``score_stage.totals``, so both arms and
the earlier audit share one scorer. Writes ``runs/arms.json``.
"""

import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).parent
STAGE_DIR = HERE.parent / "QA-2026-11-03-01"
ARMS = {
    "treatment": STAGE_DIR / "runs" / "id",
    "control": HERE / "runs" / "ctl",
}


def _score_stage():
    spec = importlib.util.spec_from_file_location(
        "score_stage", STAGE_DIR / "score_stage.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    score_stage = _score_stage()
    result = {}
    for arm, prefix in ARMS.items():
        paths = sorted(prefix.parent.glob(f"{prefix.name}-[0-9]*.json"))
        rows = [score_stage.read_run(path) for path in paths]
        result[arm] = {"rows": rows, "totals": score_stage.totals(rows)}
    (HERE / "runs" / "arms.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    for arm, scored in result.items():
        print(arm, json.dumps(scored["totals"]))
