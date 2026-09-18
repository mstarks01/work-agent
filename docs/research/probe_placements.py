"""Score an extraction sweep's placements against the signed reference facts.

A reference row's ``basis`` decides what the model owes it. A ``stated`` zone is
one the sources name, so declining it is a real loss. An ``inferred`` zone is one
the reviewer inferred because the schema demanded it, so declining it is what
ADR 0039 rule 1 asks for. ``unknown`` is the reference saying the sources place
this nowhere.
"""
import json, pathlib, sys
from collections import Counter

CORPUS = pathlib.Path("evals/corpus")
UNKNOWN = "unknown"


def reference_zones(case_dir: pathlib.Path) -> dict[str, tuple[str, str]]:
    rows = json.loads((case_dir / "facts.json").read_text())["rows"]
    return {
        r["assertion"]["subject"]: (r["assertion"]["value"], r["assertion"]["basis"])
        for r in rows
        if r["assertion"]["predicate"] == "network-membership"
    }


def score(reports_dir: pathlib.Path) -> Counter:
    tally: Counter = Counter()
    for report in sorted(reports_dir.glob("*.extraction.json")):
        case = report.name.split(".")[0]
        case_dir = CORPUS / case
        if not (case_dir / "facts.json").exists():
            continue
        wanted = reference_zones(case_dir)
        model = json.loads(report.read_text())["normalized"]
        for group in ("external_entities", "processes", "data_stores"):
            for element in model.get(group, []):
                entry = wanted.get(element["id"])
                if entry is None:
                    tally["not-in-reference"] += 1
                    continue
                want, basis = entry
                got = element["trust_zone"]
                if want == UNKNOWN:
                    tally["honest-absence" if got == UNKNOWN else "invented"] += 1
                elif basis != "stated":
                    tally["declined-an-inferred-zone" if got == UNKNOWN else "kept-an-inferred-zone"] += 1
                elif got == UNKNOWN:
                    tally["LOST-a-stated-zone"] += 1
                elif got == want:
                    tally["placed"] += 1
                else:
                    tally["placed-elsewhere"] += 1
        tally["elements"] += sum(
            len(model.get(g, []))
            for g in ("external_entities", "processes", "data_stores")
        )
        tally["flows"] += len(model.get("data_flows", []))
    return tally


for path in sys.argv[1:]:
    print(path.split("/")[-1], dict(sorted(score(pathlib.Path(path)).items())))
