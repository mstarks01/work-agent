"""Issue the attacker-position ballot, and record its return as signed rulings.

The ballot shows each STRIDE must-find with its claim, its notes and a proposed
position. :data:`PROPOSED` holds the proposals: the ballot author's reading of
each claim's text, written before the ruling. The ruler marks each row
``accept``, ``change`` with the right position, or ``any`` where the position
does not decide the finding.

Usage, from the repository root:

* ``position_ballot.py issue`` writes ``position-ballot-issued.csv``.
* ``position_ballot.py record`` reads ``position-ballot-returned.csv``. Each
  ``accept`` and ``change`` row becomes one entry in its case's
  ``rulings.json`` ``positions``, signed by the ruler; an ``any`` row writes
  nothing. Each case is then reloaded, and any exception restores each
  rulings file it wrote as it was.
"""

import csv
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, get_args

from analysis_service.frameworks.stride.record import AttackerPosition
from evals.harness.artifact import CORPUS_DIR
from evals.harness.near_misses import read_rows
from evals.harness.reference import RULINGS_FILE, ReferenceThreat, load_case

HERE = Path(__file__).parent
ISSUED = HERE / "position-ballot-issued.csv"
RETURNED = HERE / "position-ballot-returned.csv"
#: The ballot columns, in order. The ruler fills the last three.
COLUMNS = (
    "n",
    "case",
    "ref",
    "lane",
    "verb",
    "elements",
    "claim",
    "notes",
    "proposed",
    "verdicts",
    "verdict",
    "position",
    "note",
)
#: The ballot columns the ruler fills; every other column is the issued text.
Answered = Literal["verdict", "position", "note"]
ANSWERED: tuple[Answered, ...] = get_args(Answered)
Verdict = Literal["accept", "change", "any"]
VERDICTS: tuple[Verdict, ...] = get_args(Verdict)
POSITIONS: tuple[AttackerPosition, ...] = get_args(AttackerPosition)
#: How the ``elements`` column joins a claim's place.
PLACE_SEP = "; "
SIGNER = "mstarks01"
BALLOT = "QA-2026-11-02-05 position ballot"

OUT: AttackerPosition = "outside"
PATH: AttackerPosition = "on-path"
COPY: AttackerPosition = "storage-copy"
IN: AttackerPosition = "in-component"
AUTH: AttackerPosition = "authorized-principal"

#: The proposed position of each must-find, keyed ``<case number>/<reference>``.
PROPOSED: Mapping[str, AttackerPosition] = {
    "01/0": OUT,
    "01/1": OUT,
    "01/3": PATH,
    "01/4": OUT,
    "01/7": AUTH,
    "01/10": COPY,
    "01/11": PATH,
    "01/14": OUT,
    "02/0": OUT,
    "02/1": OUT,
    "02/3": OUT,
    "02/5": AUTH,
    "02/6": OUT,
    "02/7": PATH,
    "02/10": OUT,
    "03/0": OUT,
    "03/1": OUT,
    "03/3": OUT,
    "03/8": AUTH,
    "03/9": COPY,
    "03/12": OUT,
    "04/0": OUT,
    "04/1": OUT,
    "04/3": OUT,
    "04/4": OUT,
    "04/8": OUT,
    "04/9": OUT,
    "04/12": AUTH,
    "05/0": OUT,
    "05/1": OUT,
    "05/3": OUT,
    "05/6": IN,
    "05/7": IN,
    "05/10": OUT,
    "06/0": OUT,
    "06/1": OUT,
    "06/3": AUTH,
    "06/4": OUT,
    "06/8": COPY,
    "06/9": PATH,
    "06/12": OUT,
    "06/13": OUT,
    "06/14": OUT,
    "06/15": OUT,
    "07/0": OUT,
    "07/1": OUT,
    "07/5": OUT,
    "07/6": OUT,
    "07/11": AUTH,
    "07/12": OUT,
    "07/18": IN,
    "08/0": OUT,
    "08/2": OUT,
    "08/4": OUT,
    "08/8": AUTH,
    "08/10": OUT,
    "08/15": OUT,
    "08/18": AUTH,
    "08/19": AUTH,
    "09/0": OUT,
    "09/1": OUT,
    "09/3": PATH,
    "09/4": OUT,
    "09/5": OUT,
    "09/10": PATH,
    "09/11": COPY,
    "09/13": OUT,
    "09/18": AUTH,
    "10/0": OUT,
    "10/1": OUT,
    "10/3": PATH,
    "10/4": OUT,
    "10/5": PATH,
    "10/9": PATH,
    "10/15": OUT,
    "10/17": AUTH,
    "11/0": OUT,
    "11/1": OUT,
    "11/2": OUT,
    "11/4": OUT,
    "11/9": COPY,
    "11/10": OUT,
    "11/13": OUT,
    "11/14": AUTH,
    "12/0": OUT,
    "12/1": OUT,
    "12/2": OUT,
    "12/3": OUT,
    "12/4": PATH,
    "12/8": OUT,
    "12/12": OUT,
    "12/13": AUTH,
    "12/14": AUTH,
    "12/17": IN,
    "13/0": OUT,
    "13/1": OUT,
    "13/4": OUT,
    "13/5": PATH,
    "13/10": PATH,
    "13/11": COPY,
    "13/14": OUT,
    "13/16": OUT,
    "13/19": OUT,
    "14/4": OUT,
    "14/7": AUTH,
    "14/8": OUT,
    "14/12": AUTH,
    "15/0": OUT,
    "15/2": AUTH,
    "15/4": AUTH,
    "15/5": OUT,
    "15/6": AUTH,
    "15/9": AUTH,
    "15/10": AUTH,
    "16/0": OUT,
    "16/4": AUTH,
    "16/8": OUT,
    "16/9": OUT,
    "16/10": AUTH,
}


def issue(corpus_dir: Path = CORPUS_DIR) -> list[dict[str, str]]:
    """One ballot row for each STRIDE must-find, in case and reference order."""
    must_finds = [
        (f"{case_dir.name[:2]}/{index}", case_dir, index, claim)
        for case_dir in sorted(path for path in corpus_dir.iterdir() if path.is_dir())
        for index, claim in enumerate(load_case(case_dir).stride_claims())
        if claim.must_find
    ]
    keys = {key for key, *_ in must_finds}
    faults = [
        f"{label} {sorted(missing)}"
        for label, missing in (
            ("no proposal for", keys - set(PROPOSED)),
            ("a proposal for no must-find at", set(PROPOSED) - keys),
        )
        if missing
    ]
    if faults:
        raise ValueError("; ".join(faults))
    rows: list[dict[str, str]] = []
    for key, case_dir, index, claim in must_finds:
        rows.append(
            {
                "n": str(len(rows) + 1),
                "case": case_dir.name,
                "ref": str(index),
                **_shown(claim),
                "notes": claim.notes,
                "proposed": PROPOSED[key],
                "verdicts": "/".join(VERDICTS),
                **dict.fromkeys(ANSWERED, ""),
            }
        )
    return rows


def answered(
    issued: Sequence[Mapping[str, str]], returned: Sequence[Mapping[str, str]]
) -> list[Mapping[str, str]]:
    """The returned rows, each checked against the row it answers."""
    for number, answer in enumerate(returned, start=1):
        if list(answer) != list(COLUMNS) or None in answer.values():
            raise ValueError(
                f"line {number + 1}: the columns are not the issued columns"
            )
    if [row["n"] for row in returned] != [row["n"] for row in issued]:
        raise ValueError("the returned ballot does not hold the issued rows in order")
    for row, answer in zip(issued, returned, strict=True):
        changed = [
            column
            for column in row
            if column not in ANSWERED and answer[column] != row[column]
        ]
        if changed:
            raise ValueError(f"row {row['n']}: {', '.join(changed)} changed")
        verdict, position = answer["verdict"], answer["position"]
        if verdict not in VERDICTS:
            raise ValueError(
                f"row {row['n']}: the verdict {verdict!r} is not one of {VERDICTS}"
            )
        if verdict == "change" and not position:
            raise ValueError(
                f"row {row['n']}: the verdict 'change' needs a position; add one"
            )
        if verdict != "change" and position:
            raise ValueError(
                f"row {row['n']}: the verdict {verdict!r} takes no position; remove it"
            )
        if position and position not in POSITIONS:
            raise ValueError(f"row {row['n']}: {position!r} is not a position")
        if verdict == "change" and position == row["proposed"]:
            raise ValueError(
                f"row {row['n']}: the verdict 'change' needs another position than"
                " the proposal; mark the row accept or name another"
            )
        if verdict != "accept" and not answer["note"].strip():
            raise ValueError(
                f"row {row['n']}: the verdict {verdict!r} needs a note; add the reason"
            )
    return list(returned)


def _shown(claim: ReferenceThreat) -> dict[str, str]:
    """The ballot columns that showed the ruler the reference claim."""
    return {
        "lane": claim.lane,
        "verb": claim.verb or "",
        "elements": PLACE_SEP.join(claim.affected_element_ids),
        "claim": claim.claim,
    }


def _ruling(row: Mapping[str, str], case_dir: Path) -> dict[str, Any]:
    claims = load_case(case_dir).stride_claims()
    index = int(row["ref"])
    claim = claims[index] if 0 <= index < len(claims) else None
    if claim is None or any(
        row[column] != value for column, value in _shown(claim).items()
    ):
        raise ValueError(
            f"row {row['n']}: reference claim {row['ref']} of {case_dir.name} is not"
            " the claim the ballot showed; issue a new ballot"
        )
    position = row["position"] or row["proposed"]
    return {
        "lane": claim.lane,
        "reference": {
            "verb": claim.verb,
            "affected_element_ids": list(claim.affected_element_ids),
        },
        "position": position,
        "ruling": row["note"].strip()
        or f"The ballot proposed {position}, and the ruler accepted it.",
        "reviewed_by": SIGNER,
        "source": f"{BALLOT} row {row['n']}",
    }


def record(rows: Sequence[Mapping[str, str]], corpus_dir: Path = CORPUS_DIR) -> int:
    """Write each ``accept`` and ``change`` row as a position ruling, and reload.

    Returns how many rulings were written. Any exception restores each rulings
    file it wrote as it was, so a partly written ballot never stays in the tree.
    """
    written: dict[Path, bytes | None] = {}
    added = 0
    try:
        for row in rows:
            if row["verdict"] == "any":
                continue
            case_dir = corpus_dir / row["case"]
            path = case_dir / RULINGS_FILE
            if path not in written:
                written[path] = path.read_bytes() if path.is_file() else None
            data: dict[str, Any] = (
                json.loads(path.read_text(encoding="utf-8"))
                if path.is_file()
                else {"case": case_dir.name}
            )
            data.setdefault("positions", []).append(_ruling(row, case_dir))
            path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            added += 1
        for path in written:
            load_case(path.parent)
    except BaseException:
        for path, before in written.items():
            if before is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(before)
        raise
    return added


def _write(rows: Sequence[Mapping[str, str]], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    if sys.argv[1:] == ["issue"]:
        _write(issue(), ISSUED)
    elif sys.argv[1:] == ["record"]:
        count = record(answered(read_rows(ISSUED), read_rows(RETURNED)))
        print(f"{count} position rulings written", file=sys.stderr)
    else:
        sys.exit("usage: position_ballot.py issue|record")
