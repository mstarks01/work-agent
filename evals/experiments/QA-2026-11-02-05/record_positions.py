"""Record the returned attacker-position ballot as signed position rulings.

``position-ballot-issued.csv`` shows each STRIDE must-find with a proposed
position. The ruler marks each row ``accept``, ``change`` with the right
position, or ``any`` where the position does not decide the finding.

Usage, from the repository root: ``record_positions.py``. Each ``accept`` and
``change`` row becomes one entry in its case's ``rulings.json`` ``positions``,
signed by the ruler; an ``any`` row writes nothing. Each case is then reloaded,
and any exception restores every rulings file as it was.
"""

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
#: The ballot columns the ruler fills; every other column is the issued text.
Answered = Literal["verdict", "position", "note"]
ANSWERED: tuple[Answered, ...] = get_args(Answered)
Verdict = Literal["accept", "change", "any"]
VERDICTS: tuple[Verdict, ...] = get_args(Verdict)
POSITIONS: tuple[AttackerPosition, ...] = get_args(AttackerPosition)
SIGNER = "mstarks01"
BALLOT = "QA-2026-11-02-05 position ballot"


def answered(
    issued: Sequence[Mapping[str, str]], returned: Sequence[Mapping[str, str]]
) -> list[Mapping[str, str]]:
    """The returned rows, each checked against the row it answers."""
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
                f"row {row['n']}: verdict {verdict!r} is not one of {VERDICTS}"
            )
        if (verdict == "change") != bool(position):
            raise ValueError(f"row {row['n']}: only a change verdict names a position")
        if position and position not in POSITIONS:
            raise ValueError(f"row {row['n']}: {position!r} is not a position")
        if verdict != "accept" and not answer["note"].strip():
            raise ValueError(f"row {row['n']}: a {verdict!r} verdict states its reason")
    return list(returned)


def _shown(claim: ReferenceThreat) -> dict[str, str]:
    """The ballot columns that showed the ruler the reference claim."""
    return {
        "lane": claim.lane,
        "verb": claim.verb or "",
        "elements": "; ".join(claim.affected_element_ids),
        "claim": claim.claim,
    }


def _ruling(row: Mapping[str, str], case_dir: Path) -> dict[str, Any]:
    claims = load_case(case_dir).stride_claims()
    index = int(row["ref"])
    claim = claims[index] if index < len(claims) else None
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
    file as it was, so a partly written ballot never stays in the tree.
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


if __name__ == "__main__":
    count = record(answered(read_rows(ISSUED), read_rows(RETURNED)))
    print(f"{count} position rulings written", file=sys.stderr)
