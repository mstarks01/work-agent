"""Replay each archived report alone and with every archived framework.

Every capability is answered "yes". A framework the report selected keeps the
report's options, as in ``run.py early-rounds``. Run from the repository root:
``python evals/experiments/QA-2026-10-09-01/paired_replay.py <out.json>``.
"""

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from analysis_service.claims import FrameworkName
from analysis_service.report import Report
from analysis_service.system_model import SystemModel
from evals.harness.early_rounds import replay


def main(out: Path, root: Path = Path("evals")) -> None:
    corpus = root / "corpus"
    paths = sorted(root.rglob("*.report.json"))
    every: dict[FrameworkName, Any] = {}
    for path in paths:
        for selection in Report.model_validate_json(path.read_text()).job.frameworks:
            every.setdefault(selection.name, selection.options)
    rows = []
    for path in paths:
        case = path.name.removesuffix(".report.json")
        blessed_path = corpus / case / "model.json"
        if not blessed_path.is_file():
            continue
        report = Report.model_validate_json(path.read_text())
        blessed = SystemModel.model_validate_json(blessed_path.read_text())
        own = {selection.name: selection.options for selection in report.job.frameworks}
        alone = replay(case, report.system_model, blessed, own, "capability-yes")
        joint = replay(
            case, report.system_model, blessed, every | own, "capability-yes"
        )
        rows.append({"path": str(path), "alone": asdict(alone), "joint": asdict(joint)})
    out.write_text(json.dumps(rows), encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
