"""Read the before and after extraction sweeps of the vendor-claim measurement."""

import json
import statistics
import sys
from pathlib import Path

REPO = Path("/home/me/work-agent")
OUT = REPO / "evals/runs/20261106T-vendor-claim"
CASE = "12-overclaiming-supplier-portal"


def case12_authentication(artifact: Path) -> list[str]:
    values = []
    for report in sorted(
        (artifact.parent / f"{artifact.stem}.reports").glob(f"{CASE}*")
    ):
        flows = json.loads(report.read_text())["normalized"]["data_flows"]
        values += [
            flow.get("authentication")
            for flow in flows
            if "category-manager" in flow.get("source", "")
        ]
    return values


def kept_leads(artifact: Path) -> tuple[int, int, int, int]:
    """Lead fates over the cases whose extracted model is valid."""
    sys.path[:0] = [str(REPO), str(REPO / "evals/experiments/QA-2026-11-06-02")]
    from lead_loss import lead_fates

    from evals.harness.reference import development_cases, load_corpus
    from evals.harness.run import extractions_from_reports, load_artifact

    cases = development_cases(load_corpus(REPO / "evals/corpus"))
    loaded = load_artifact(artifact)
    present = [case for case in cases if case.id in loaded.cases]
    totals = {"kept": 0, "unfired": 0, "unaligned": 0, "invalid": 0}
    for case in present:
        result = extractions_from_reports(loaded, [case]).get(case.id)
        if result is None or result.extracted is None:
            totals["invalid"] += 1
            continue
        try:
            fates = [fate for _, _, fate in lead_fates(case, result.extracted)]
        except ValueError:
            totals["invalid"] += 1
            continue
        for fate in fates:
            if fate in totals:
                totals[fate] += 1
    return totals["kept"], totals["unfired"], totals["unaligned"], totals["invalid"]


def row(artifact: Path) -> dict:
    data = json.loads(artifact.read_text())
    aggregate = data["attribute_aggregate"]
    recalls = [case["endpoint_recall"] for case in data["mode_output"]]
    kept, unfired, unaligned, invalid = kept_leads(artifact)
    return {
        "sweep": artifact.stem,
        "cases": len(recalls),
        "agreement": aggregate["agreement"],
        "flow_auth": aggregate["by_attribute"]["flow.authentication"]["agreement"],
        "endpoint_recall": round(statistics.mean(recalls), 3),
        "kept": kept,
        "unfired": unfired,
        "unaligned": unaligned,
        "invalid": invalid,
        "charged": round(sum((data.get("node_charges") or {}).values()), 4),
        "case12_manager_auth": case12_authentication(artifact),
    }


def main() -> None:
    rows = [row(path) for path in sorted(OUT.glob("*-r[0-9].json"))]
    for one in rows:
        print(json.dumps(one))
    for metric in ("agreement", "flow_auth", "endpoint_recall", "kept"):
        before = [one[metric] for one in rows if one["sweep"].startswith("before")]
        after = [one[metric] for one in rows if one["sweep"].startswith("after")]
        if len(before) > 1 and after:
            spread = statistics.stdev(before) or float("nan")
            delta = statistics.mean(after) - statistics.mean(before)
            print(
                f"{metric}: before {statistics.mean(before):.3f} sd {spread:.3f}"
                f" after {statistics.mean(after):.3f} delta {delta / spread:+.2f} sd"
            )
    print("charged total:", round(sum(one["charged"] for one in rows), 4))


if __name__ == "__main__":
    main()
