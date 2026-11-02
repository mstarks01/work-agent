"""Read the returned ballot of QA-2026-11-02-05 into the counts its rows need.

The returned file is ``ballot-returned.csv``. Each issued column must be
unchanged, each verdict must be in its check's set, and ``duplicate_of`` and
``pair_of`` must name a row of the same check and the same group. A file that
breaks one of these is refused by name.

The counts are the ones the E2 and E3 falsifiers read:

* E3 targets, in target places by runs: a target store of a run counts once
  where at least one of the run's drafts at it is ruled ``same``. Two stores
  and two runs give four.
* E3 controls: the ``yes``, ``no`` and ``unsure`` rulings of each control.
* E2: the ``right``, ``wrong``, ``duplicate`` and ``unsure`` rulings, the pairs
  (each unordered ``pair_of`` link counted once) and the duplicates, by group.

Usage, from the repository root: ``read_ballot.py``.
"""

import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).parent))
from build_ballot import COLUMNS, DRAFTS, VERDICTS, Check

HERE = Path(__file__).parent
RULED = ("verdict", "duplicate_of", "pair_of", "note")


class BallotError(ValueError):
    """A returned ballot that breaks one of the rules of this module."""


def _rows(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="") as handle:
        return {row["n"]: row for row in csv.DictReader(handle)}


def _linked(rows, row, column: str) -> dict[str, str] | None:
    target = row[column].strip()
    if not target:
        return None
    other = rows.get(target)
    if (
        other is None
        or other["check"] != row["check"]
        or other["group"] != row["group"]
    ):
        raise BallotError(
            f"row {row['n']}: {column} {target!r} names no row of check"
            f" {row['check']} in group {row['group']}"
        )
    return other


def read(returned: Path, issued: Path, key_path: Path) -> dict:
    rows = _rows(returned)
    sent = _rows(issued)
    key = {str(entry["n"]): entry for entry in json.loads(key_path.read_text())}
    if rows.keys() != sent.keys():
        raise BallotError("the returned rows are not the issued rows")
    for n, row in rows.items():
        changed = [c for c in COLUMNS if c not in RULED and row[c] != sent[n][c]]
        if changed:
            raise BallotError(f"row {n}: the issued columns {changed} changed")
        if row["check"] not in VERDICTS:
            raise BallotError(f"row {n}: {row['check']!r} is not a check")
        check = cast(Check, row["check"])
        if row["verdict"] not in VERDICTS[check]:
            raise BallotError(
                f"row {n}: {row['verdict']!r} is not a {row['check']} verdict"
            )
        duplicate = _linked(rows, row, "duplicate_of")
        if (duplicate is None) != (row["verdict"] != "duplicate"):
            raise BallotError(
                f"row {n}: a duplicate names its row, and only a duplicate"
            )
        pair = _linked(rows, row, "pair_of")
        if pair is not None and (row["verdict"], pair["verdict"]) != ("right", "right"):
            raise BallotError(f"row {n}: a pair links two right rows")

    targets = DRAFTS["item4"][1]
    assert targets is not None
    places = {
        (key[n]["run"], store)
        for n, row in rows.items()
        if row["check"] == "item4" and row["verdict"] == "same"
        for store in targets & set(row["elements"].split("; "))
    }
    pairs = {
        frozenset((n, row["pair_of"].strip()))
        for n, row in rows.items()
        if row["check"] == "item2" and row["pair_of"].strip()
    }
    return {
        "verdicts": {
            check: dict(
                Counter(r["verdict"] for r in rows.values() if r["check"] == check)
            )
            for check in VERDICTS
        },
        "e3_target_places": {
            "same": len(places),
            "of": len(targets)
            * len({key[n]["run"] for n, r in rows.items() if r["check"] == "item4"}),
        },
        "e2_pairs_by_group": dict(Counter(rows[min(p)]["group"] for p in pairs)),
        "e2_duplicates_by_group": dict(
            Counter(
                r["group"]
                for r in rows.values()
                if r["check"] == "item2" and r["verdict"] == "duplicate"
            )
        ),
    }


if __name__ == "__main__":
    print(
        json.dumps(
            read(
                HERE / "ballot-returned.csv",
                HERE / "ballot-issued.csv",
                HERE / "ballot-key.json",
            ),
            indent=1,
        )
    )
