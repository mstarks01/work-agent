"""Join each ruled case 05 target draft of E6 and E7 to its cited evidence (E8).

Each draft that a ballot rules ``same`` or ``different`` at a case 05 target
store, with its run and its ``evidence_refs``, read through the ballot key.
Two drafts that cite the same evidence and take opposite rulings show that
the cited evidence does not decide the mechanism. The script writes the rows
to ``runs/ruled-proxy.json``.

Usage, from the repository root: ``ruled_proxy.py``.
"""

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from build_ballot import HERE, RUNS

BALLOTS = (
    ("ballot-returned.csv", "ballot-key.json"),
    ("control-ballot-returned.csv", "control-ballot-key.json"),
)


def main() -> None:
    rows = {}
    for returned, key_name in BALLOTS:
        key = {
            str(entry["n"]): entry
            for entry in json.loads((HERE / key_name).read_text())
        }
        with (HERE / returned).open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                if row["check"] != "item4":
                    continue
                run, sequence = key[row["n"]]["run"], key[row["n"]]["sequence"]
                output = json.loads((RUNS / f"{run}.json").read_text())
                claim = next(c for c in output["claims"] if c["sequence"] == sequence)
                rows[f"{run}/{sequence}"] = {
                    "verdict": row["verdict"],
                    "title": claim["title"],
                    "evidence_refs": sorted(claim["evidence_refs"]),
                }
    (RUNS / "ruled-proxy.json").write_text(json.dumps(rows, indent=1) + "\n")
    print(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
