"""Read the returned ballot of QA-2026-11-02-05 into the counts its rows need.

The returned file is ``ballot-returned.csv``. Its header must be ``COLUMNS``,
each ``n`` must appear once, each issued column must be unchanged, each verdict
must be in its check's set, and ``duplicate_of`` and ``pair_of`` must name
another row of the same check and the same group, and a pair's two drafts
must share a flow of the case's model. A duplicate names a row that is not a
duplicate itself. A row may be in one pair at most, and a link written on both rows counts once. A file that breaks one of
these, or a row with more or fewer cells than the header, is refused by
name. The file may carry the byte-order mark a spreadsheet
writes.

The counts are the ones the E2 and E3 falsifiers read:

* E3 targets, in target places by runs: a target store of a run counts once
  where at least one of the run's drafts at it is ruled ``same``. The
  denominator is the target stores by the runs on disk.
* E3 controls: the ``yes`` rulings of each control in each run, with 0 for a
  run that has none. A copy control fails in a run with 0; a no-read control
  fails in a run with more than 0.
* E2: the ``right``, ``wrong``, ``duplicate`` and ``unsure`` rulings, and the
  pairs and the duplicates by group.

Usage, from the repository root: ``read_ballot.py``.
"""

import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).parent))
from build_ballot import CHECKS, COLUMNS, Check, Column, runs

from evals.harness.reference import load_case

HERE = Path(__file__).parent
#: The columns the ruler fills in; every other column must come back unchanged.
RULED: tuple[Column, ...] = ("verdict", "duplicate_of", "pair_of", "note")


class BallotError(ValueError):
    """A returned ballot that breaks one of the rules of this module."""


def _rows(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != COLUMNS:
            raise BallotError(f"{path.name}: the header is not {list(COLUMNS)}")
        rows: dict[str, dict[str, str]] = {}
        for row in reader:
            if None in row or None in row.values():
                raise BallotError(
                    f"{path.name}: a row has more or fewer cells than the header"
                )
            if row["n"] in rows:
                raise BallotError(f"{path.name}: row {row['n']} appears twice")
            rows[row["n"]] = row
    return rows


def _linked(rows, row, column: str) -> dict[str, str] | None:
    target = row[column].strip()
    if not target:
        return None
    if target == row["n"]:
        raise BallotError(f"row {row['n']}: {column} names the row itself")
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
    claims = {
        (run, claim["sequence"]): claim
        for check in CHECKS
        for run, output in runs(check)
        for claim in output["claims"]
    }

    def flows_of(case: str) -> set[str]:
        model = load_case(f"evals/corpus/{case}").model
        return {flow.id for flow in model.data_flows}

    def elements_of(n: str) -> set[str]:
        claim = claims[(key[n]["run"], key[n]["sequence"])]
        return set(claim["affected_element_ids"])

    pairs: set[frozenset[str]] = set()
    for n, row in rows.items():
        changed = [c for c in COLUMNS if c not in RULED and row[c] != sent[n][c]]
        if changed:
            raise BallotError(f"row {n}: the issued columns {changed} changed")
        check = cast(Check, row["check"])
        if row["verdict"] not in CHECKS[check].verdicts:
            raise BallotError(f"row {n}: {row['verdict']!r} is not a {check} verdict")
        duplicate = _linked(rows, row, "duplicate_of")
        if duplicate is not None and duplicate["verdict"] == "duplicate":
            raise BallotError(
                f"row {n}: duplicate_of names row {duplicate['n']}, which is a"
                " duplicate too; name the row it repeats"
            )
        if (duplicate is None) != (row["verdict"] != "duplicate"):
            raise BallotError(
                f"row {n}: a duplicate names its row, and only a duplicate"
            )
        pair = _linked(rows, row, "pair_of")
        if pair is not None:
            if (row["verdict"], pair["verdict"]) != ("right", "right"):
                raise BallotError(f"row {n}: a pair links two right rows")
            flows = elements_of(n) & elements_of(pair["n"]) & flows_of(row["case"])
            if not flows:
                raise BallotError(f"row {n}: a pair links two drafts at one flow")
            pairs.add(frozenset((n, pair["n"])))
    paired = Counter(member for pair in pairs for member in pair)
    if twice := sorted(n for n, count in paired.items() if count > 1):
        raise BallotError(f"rows {twice} are each in more than one pair")

    targets = CHECKS["item4"].stores
    assert targets is not None
    places = {
        (key[n]["run"], store)
        for n, row in rows.items()
        if row["check"] == "item4" and row["verdict"] == "same"
        for store in targets & elements_of(n)
    }
    return {
        "verdicts": {
            check: dict(
                Counter(r["verdict"] for r in rows.values() if r["check"] == check)
            )
            for check in CHECKS
        },
        "e3_target_places": {
            "same": len(places),
            "of": len(targets) * len(runs("item4")),
        },
        "e3_control_yes_by_run": {
            check: {
                run: sum(
                    1
                    for n, r in rows.items()
                    if r["check"] == check
                    and key[n]["run"] == run
                    and r["verdict"] == "yes"
                )
                for run, _ in runs(check)
            }
            for check, spec in CHECKS.items()
            if spec.control
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
