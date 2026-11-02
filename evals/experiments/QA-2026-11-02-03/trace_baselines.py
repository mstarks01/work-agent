"""Trace case 05 STRIDE references 6 and 7 through each committed STRIDE Baseline.

Run from the repository root with ``ANALYSIS_OFFLINE=1``. The one argument is a
folder holding one subfolder per Baseline, named as the Baseline is, each with
a ``rescored.json`` that ``run.py score <run> --corpus evals/corpus --out``
wrote. The Baselines were scored on older reference sets, so their own
``losses`` and ``matched`` do not index today's references.
"""

import json
import sys
from pathlib import Path

from analysis_service.graph import FrameworkNodes, framework_lane_inputs
from analysis_service.system_model import SystemModel
from tests.test_prompt_lints import PACKAGE_LOADERS

CASE = "05-cookbook-queue-webapp"
REFS = {6: "store:worker-config", 7: "store:web-application-config"}

rescored_root = Path(sys.argv[1])
claims = json.loads(Path(f"evals/corpus/{CASE}/claims/stride.json").read_text())

for run in sorted(Path("evals/baselines").glob("*/mstarks01-*.json")):
    baseline = run.parent.name
    reports = run.with_suffix(".reports")
    report = json.loads((reports / f"{CASE}.report.json").read_text())
    drafts = json.loads((reports / f"{CASE}.drafts.json").read_text())["stride"]
    rescored = json.loads((rescored_root / baseline / "rescored.json").read_text())
    score = next(s for s in rescored["scores"] if s["case"] == CASE)
    matched = {m["reference_index"]: m["threat_id"] for m in score["matched"]}
    losses = {
        loss["reference_index"]: loss
        for loss in next(x for x in rescored["losses"] if x["case"] == CASE)["losses"]
    }
    print(f"\n######## {baseline}  job {report['job']['created_at'][:10]}")
    print("  run-time catalog:", report.get("assertions"))
    print(
        "  run-time store-readers lead recorded:",
        "information-disclosure-store-readers" in json.dumps(report),
    )
    model = SystemModel.model_validate(report["system_model"])
    inputs = framework_lane_inputs(
        FrameworkNodes("stride"), model, {}, None, PACKAGE_LOADERS["stride"]
    )
    fenced = inputs.artifacts["information-disclosure"]["candidates"]
    leads = json.loads(fenced.split("\n", 1)[1].rsplit("\n", 1)[0])
    for index, store in REFS.items():
        print(f"--- ref {index}: {claims[index]['claim']}")
        flows = [
            (flow.source, flow.operations)
            for flow in model.data_flows
            if flow.destination == store
        ]
        print("  extracted inbound flows:", flows)
        for candidate in leads["candidates"]:
            if candidate["element_ids"][0] == store:
                print("  today's lead:", candidate["rule_id"], candidate["facts"])
        for draft in drafts:
            if store in draft["affected_element_ids"]:
                print(
                    f"  draft {draft['id']} [{draft['category']}/{draft.get('verb')}]"
                    f" {draft['title']}"
                )
                print(f"      {draft['description'][:400]}")
        if index in matched:
            print("  ruling today: matched by verb and place,", matched[index])
        else:
            loss = losses[index]
            print(
                "  ruling today:",
                {k: loss.get(k) for k in ("cause", "draft_id", "draft_verb")},
            )
