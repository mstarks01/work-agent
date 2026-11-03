"""Read a stage of QA-2026-11-03-01: do the drafts state the ruled position?

Usage, from the repository root: ``score_stage.py id`` reads stage 1 and
``score_stage.py fix`` reads stage 2. Each ``runs/<prefix>-*.json`` holds one
information-disclosure lane call. The case is prepared again as the
call prepared it, and :func:`~evals.harness.descendants.lane_drafts` turns its
proposals into drafts. A ruled reference of the lane is reached when a draft
matches it by verb and place with no ruled position, and it agrees when a
reaching draft states the ruled position. Writes ``runs/<prefix>-read.json``.
"""

import json
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

from analysis_service.frameworks import schemas_for
from analysis_service.system_model import SystemModel
from evals.harness.artifact import CORPUS_DIR
from evals.harness.descendants import lane_drafts
from evals.harness.identity import SubsetVerbIdentity
from evals.harness.lane_replay import STATE_VALID_MODEL, prepare_today
from evals.harness.ledger import Ledger
from evals.harness.reference import flows_by_case, load_case
from evals.harness.scorer import _match_in_lane, score_case

HERE = Path(__file__).parent
RUNS = HERE / "runs"
LANE = "information-disclosure"
FALSIFIER = 0.70


def _case_dir(number: str) -> Path:
    (found,) = (path for path in CORPUS_DIR.iterdir() if path.name[:2] == number)
    return found


def _must_finds(score: Any, case: Any) -> list[int]:
    references = case.stride_claims()
    return sorted(
        pair.reference_index
        for pair in score.matched
        if references[pair.reference_index].must_find
        and references[pair.reference_index].lane == LANE
    )


def read_run(path: Path) -> dict[str, Any]:
    """One call's fill, reached references, agreement and coverage."""
    run = json.loads(path.read_text(encoding="utf-8"))
    case = load_case(_case_dir(path.stem.split("-")[1]))
    arm = run["record"]["table"]["prepare_today"]
    batch = schemas_for("stride").proposals.model_validate(
        {"claims": run["claims"], "invalid": run["invalid"]}
    )
    state = prepare_today(case, "stride", arm).state
    drafts = lane_drafts(state, LANE, batch)
    model = SystemModel.model_validate(state[STATE_VALID_MODEL])
    matcher = SubsetVerbIdentity(flows_by_case([case], {case.id: model}))
    unruled = replace(case, ruled_positions=MappingProxyType({}))
    own, by_reading = _match_in_lane(unruled, drafts, matcher, [])
    references = []
    for index, position in sorted(case.ruled_positions.items()):
        if case.stride_claims()[index].lane != LANE:
            continue
        reaching = [*own[index], *by_reading[index]]
        stated = sorted({str(drafts[i].attacker_position) for i in reaching})
        references.append(
            {
                "reference": index,
                "ruled": position,
                "reached": bool(reaching),
                "agrees": position in stated,
                "stated": stated,
            }
        )
    return {
        "case": case.id,
        "arm": arm,
        "proposals": len(run["claims"]) + len(run["invalid"]),
        "invalid": len(run["invalid"]),
        "invalid_for_position": sum(
            "attacker_position" in json.dumps(item) for item in run["invalid"]
        ),
        "drafts": len(drafts),
        "positions_stated": dict(
            Counter(str(draft.attacker_position) for draft in drafts)
        ),
        "references": references,
        "must_finds_with_rule": _must_finds(
            score_case(case, drafts, matcher, Ledger()), case
        ),
        "must_finds_without_rule": _must_finds(
            score_case(unruled, drafts, matcher, Ledger()), case
        ),
    }


def totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The pooled figures the decision table reads."""
    references = [ref for row in rows for ref in row["references"]]
    reached = [ref for ref in references if ref["reached"]]
    agreeing = [ref for ref in reached if ref["agrees"]]
    proposals = sum(row["proposals"] for row in rows)
    by_position: dict[str, dict[str, int]] = {}
    for ref in reached:
        tally = by_position.setdefault(ref["ruled"], {"reached": 0, "agrees": 0})
        tally["reached"] += 1
        tally["agrees"] += ref["agrees"]
    return {
        "calls": len(rows),
        "proposals": proposals,
        "invalid_for_position": sum(row["invalid_for_position"] for row in rows),
        "ruled_references": len(references),
        "reached": len(reached),
        "agreeing": len(agreeing),
        "agreement": len(agreeing) / len(reached) if reached else None,
        "by_position": by_position,
        "must_finds_with_rule": sum(len(row["must_finds_with_rule"]) for row in rows),
        "must_finds_without_rule": sum(
            len(row["must_finds_without_rule"]) for row in rows
        ),
        "falsifier": f"agreement under {FALSIFIER:.0%}",
    }


if __name__ == "__main__":
    (prefix,) = sys.argv[1:]
    rows = [read_run(path) for path in sorted(RUNS.glob(f"{prefix}-[0-9]*.json"))]
    result = {"rows": rows, "totals": totals(rows)}
    (RUNS / f"{prefix}-read.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result["totals"], indent=2))
