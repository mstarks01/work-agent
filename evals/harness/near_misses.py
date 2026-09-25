"""The near-miss ballot: drafts that may state a missed must-find another way.

A reference claim holds one verb and one place. A lane that writes the same
finding with a neighbouring verb, or one element over, misses it, and only a
person reading both can say whether the two are one finding. This module turns
a scored sweep into the question and the answer back into a ruling.

**It reads the rows the scorer already charged.** :mod:`~evals.harness.losses`
names the surviving draft at a ``verb`` loss's place and the displaced draft
beside a ``place`` or ``unled`` loss, with the vote standing on it. Those are
the near misses; this module selects from them and computes nothing about
places or verbs of its own, so there is one reader of "which draft sits next
to this miss".

**A row is asked only where a ruling can move the figure.** A draft that
matched a reference of its own keeps it, because the scorer assigns every
match a claim's own reading makes before any ruled one (PR #1220). A draft a
person voted down is already answered. A draft in another lane cannot be
ruled, because a ruled reading stays in its claim's lane.

The ruling is the person's: :func:`record` writes the rows the returned ballot
marks ``same`` or ``different``, with the reader's note and login, and never a
verdict of its own. A ``different`` ruling changes no score. It is kept so that
the next ballot does not ask the same question again.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from analysis_service.frameworks.stride.record import DraftThreat
from evals.harness.losses import CaseLosses, Loss
from evals.harness.reference import (
    RULINGS_FILE,
    CorpusError,
    GoldenCase,
    load_case,
)
from evals.harness.scorer import CaseScore
from evals.harness.verbs import same_action

#: What the reader fills in, and the answers the recorder accepts.
VERDICTS = ("same", "different", "unsure")

#: The verdicts that become a signed ruling. ``unsure`` records nothing, so the
#: row is asked again on the next ballot.
RULED = ("same", "different")

#: The standings a displaced draft may carry and still be asked about. A
#: ``rejected`` draft is one a person refused; ``None`` means it matched a
#: reference of its own and nobody was asked.
ASKABLE = frozenset({"pooled", "open", "unvoted", "stale"})

COLUMNS = (
    "n",
    "case",
    "ref",
    "reference_claim",
    "reference_lane",
    "reference_verb",
    "reference_place",
    "draft_id",
    "draft_title",
    "draft_description",
    "draft_lane",
    "draft_verb",
    "draft_place",
    "differs_by",
    "source",
    "verdict",
    "note",
)

#: The columns the reader answers in. Every other column is carried, and a
#: returned ballot that changes one is refused.
ANSWERED = ("verdict", "note")

PLACE_SEP = " ; "


def rows(
    cases: Sequence[GoldenCase],
    scores: Sequence[CaseScore],
    charged: Sequence[CaseLosses],
    produced: Mapping[str, Sequence[DraftThreat]],
    source: str,
) -> list[dict[str, str]]:
    """One ballot row per missed must-find a free same-lane draft may state."""
    by_case = {case.id: case for case in cases}
    matched = {
        score.case_id: {pair.threat_id for pair in score.matched} for score in scores
    }
    ballot: list[dict[str, str]] = []
    for case_losses in charged:
        case = by_case[case_losses.case]
        drafts = {draft.id: draft for draft in produced[case.id]}
        for loss in case_losses.losses:
            draft_id = _near(loss)
            if not loss.must_find or draft_id is None:
                continue
            draft = drafts[draft_id]
            if draft.id in matched[case.id] or draft.category != loss.lane:
                continue
            if _ruled(case, loss.reference_index, draft):
                continue
            reference = case.stride_claims()[loss.reference_index]
            ballot.append(
                {
                    "n": str(len(ballot) + 1),
                    "case": case.id,
                    "ref": str(loss.reference_index),
                    "reference_claim": reference.claim,
                    "reference_lane": reference.lane,
                    "reference_verb": reference.verb or "",
                    "reference_place": PLACE_SEP.join(reference.affected_element_ids),
                    "draft_id": draft.id,
                    "draft_title": draft.title,
                    "draft_description": draft.description,
                    "draft_lane": draft.category,
                    "draft_verb": draft.verb,
                    "draft_place": PLACE_SEP.join(draft.affected_element_ids),
                    "differs_by": _differs_by(loss.cause, draft.verb, reference.verb),
                    "source": f"{source} {draft.id}",
                    "verdict": "",
                    "note": "",
                }
            )
    return ballot


def _ruled(case: GoldenCase, index: int, draft: DraftThreat) -> bool:
    """Whether a person already ruled on this draft's reading of the claim."""
    readings = (
        *case.ruled_readings.get(index, ()),
        *case.refused_readings.get(index, ()),
    )
    place = set(draft.affected_element_ids)
    return any(
        reading.verb == draft.verb and set(reading.affected_element_ids) == place
        for reading in readings
    )


def _near(loss: Loss) -> str | None:
    """The draft a loss row names beside the miss, where one may be asked about."""
    if loss.cause == "verb":
        return loss.draft_id
    if loss.cause in ("place", "unled") and loss.displaced_standing in ASKABLE:
        return loss.displaced_draft_id
    return None


def _differs_by(cause: str, draft_verb: str, reference_verb: str | None) -> str:
    parts = []
    if not same_action(draft_verb, reference_verb or ""):
        parts.append(f"verb: {draft_verb} vs {reference_verb}")
    if cause != "verb":
        parts.append("place: one element over")
    return " | ".join(parts)


def write(ballot: Sequence[Mapping[str, str]], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(ballot)


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def returned_rows(issued: Path, returned: Path) -> list[dict[str, str]]:
    """The issued rows with the reader's answers, or a refusal naming the row.

    The returned file may carry every column or only ``n`` and the answers. A
    carried column it does carry must read exactly as issued, so an answer is
    never recorded against a row the reader was not shown.
    """
    issued_rows = {row["n"]: row for row in _read(issued)}
    answers = _read(returned)
    numbers = [row.get("n", "") for row in answers]
    if sorted(numbers) != sorted(issued_rows) or len(set(numbers)) != len(numbers):
        raise ValueError(
            f"{returned}: rows {sorted(numbers)} do not match the issued"
            f" rows {sorted(issued_rows)}"
        )
    merged = []
    for answer in answers:
        row = issued_rows[answer["n"]]
        changed = [
            column
            for column in COLUMNS
            if column not in ANSWERED
            and column in answer
            and answer[column] != row[column]
        ]
        if changed:
            raise ValueError(f"row {answer['n']}: {', '.join(changed)} changed")
        if answer.get("verdict", "") not in VERDICTS:
            raise ValueError(
                f"row {answer['n']}: verdict {answer.get('verdict')!r} is not one"
                f" of {', '.join(VERDICTS)}"
            )
        if answer["verdict"] in RULED and not answer.get("note", "").strip():
            raise ValueError(
                f"row {answer['n']}: a {answer['verdict']!r} verdict becomes a"
                " ruling, and a ruling states its reason; add a note"
            )
        merged.append(
            {**row, "verdict": answer["verdict"], "note": answer.get("note", "")}
        )
    return merged


def record(
    answered: Sequence[Mapping[str, str]],
    corpus_dir: Path,
    reviewed_by: str,
    ballot_name: str,
) -> int:
    """Write each ``same`` and ``different`` row as a ruling, and reload.

    Returns how many rulings were written; a reading already ruled is skipped.

    A case whose rulings no longer load is restored as it was and the whole
    call refused, so a partly written ballot never stays in the tree.
    """
    written: dict[Path, bytes | None] = {}
    added = 0
    try:
        for row in answered:
            if row["verdict"] not in RULED:
                continue
            case_dir = corpus_dir / row["case"]
            path = case_dir / RULINGS_FILE
            if path not in written:
                written[path] = path.read_bytes() if path.is_file() else None
            added += _append(path, case_dir, row, reviewed_by, ballot_name)
        for path in written:
            load_case(path.parent)
    except (CorpusError, ValueError):
        for path, before in written.items():
            if before is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(before)
        raise
    return added


def _append(
    path: Path,
    case_dir: Path,
    row: Mapping[str, str],
    reviewed_by: str,
    ballot_name: str,
) -> bool:
    claim = load_case(case_dir).stride_claims()[int(row["ref"])]
    data = (
        json.loads(path.read_text(encoding="utf-8"))
        if path.is_file()
        else {"case": case_dir.name, "stride": []}
    )
    ruling = {
        "lane": claim.lane,
        "reference": {
            "verb": claim.verb,
            "affected_element_ids": list(claim.affected_element_ids),
        },
        "reading": {
            "verb": row["draft_verb"],
            "affected_element_ids": row["draft_place"].split(PLACE_SEP),
        },
        "verdict": row["verdict"],
        "ruling": row["note"].strip(),
        "reviewed_by": reviewed_by,
        "source": f"{ballot_name} row {row['n']}: {row['source']}",
    }
    already = [
        entry
        for entry in data["stride"]
        if entry["reference"] == ruling["reference"]
        and entry["reading"] == ruling["reading"]
    ]
    if already:
        return False
    data["stride"].append(ruling)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return True
