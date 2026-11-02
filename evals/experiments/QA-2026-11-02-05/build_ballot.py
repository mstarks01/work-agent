"""Build the blind ballot of QA-2026-11-02-05 from its run outputs.

The rows are each case 05 draft at a config store from the E3 runs and each
spoofing draft from the E2 runs, in one shuffled order with a fixed seed. A row
does not name its run; ``ballot-key.json`` does.

Each check has a closed set of verdicts, in ``VERDICTS``. A spoofing draft that
repeats another row's attacker, failed control and fix at one place takes the
verdict ``duplicate`` and names that row in ``duplicate_of``.

Usage, from the repository root: ``build_ballot.py``. The returned ballot is
read by hand into each row's outcome with ``run.py experiments --record``.
"""

import csv
import json
import random
from pathlib import Path

HERE = Path(__file__).parent
RUNS = HERE / "runs"
SEED = 20261102

#: Each check's verdicts. A verdict outside its set is not a ruling.
VERDICTS = {
    "item4": ("same", "different", "unsure"),
    "item2": ("right", "wrong", "duplicate", "unsure"),
}

QUESTIONS = {
    "item4": (
        "Does the draft name an attacker who runs code in the process that reads"
        " this config store, and reads the credential through that process's own"
        " access, with a remedy on that access or on the credential's lifetime?"
        " same: attacker, credential, path and remedy all agree."
    ),
    "item2": (
        "Is this spoofing threat right for the source? duplicate: it repeats"
        " another row's attacker, failed control and fix at the same place;"
        " name that row in duplicate_of."
    ),
}


def drafts() -> list[tuple[str, str, dict]]:
    rows = []
    for path in sorted(RUNS.glob("item4-05-p*.json")):
        for claim in json.loads(path.read_text())["claims"]:
            if any("config" in ref for ref in claim["affected_element_ids"]):
                rows.append(("item4", path.stem, claim))
    for path in sorted(RUNS.glob("item2-*-p*.json")):
        for claim in json.loads(path.read_text())["claims"]:
            rows.append(("item2", path.stem, claim))
    random.Random(SEED).shuffle(rows)
    return rows


def main() -> None:
    rows = drafts()
    key = []
    with (HERE / "ballot-issued.csv").open("w", newline="") as out:
        writer = csv.writer(out)
        writer.writerow(
            [
                "n",
                "check",
                "case",
                "title",
                "description",
                "elements",
                "mitigations",
                "question",
                "verdicts",
                "verdict",
                "duplicate_of",
                "note",
            ]
        )
        for n, (check, run, claim) in enumerate(rows, 1):
            writer.writerow(
                [
                    n,
                    check,
                    run.split("-")[1],
                    claim["title"],
                    claim["description"],
                    "; ".join(claim["affected_element_ids"]),
                    " | ".join(
                        f"{m['summary']}: {m['detail']}"
                        for m in claim.get("mitigations", [])
                    ),
                    QUESTIONS[check],
                    "/".join(VERDICTS[check]),
                    "",
                    "",
                    "",
                ]
            )
            key.append({"n": n, "run": run, "sequence": claim.get("sequence")})
    (HERE / "ballot-key.json").write_text(json.dumps(key, indent=1) + "\n")


if __name__ == "__main__":
    main()
