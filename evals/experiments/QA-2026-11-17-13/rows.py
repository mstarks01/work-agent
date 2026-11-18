"""The row-level reading of QA-2026-11-17-13 over one scored sweep.

Usage::

    python rows.py <scored dir> <run dir> <corpus dir> <out dir>

``<scored dir>`` holds one ``run.py score --out`` artifact per case of
``<run dir>``. The script writes ``verb-rows.csv``, ``controls.csv`` and
``summary.txt`` into ``<out dir>``. The ``classification`` column of
``verb-rows.csv`` is the reader's reading of each draft against the
reference and its rulings; it is not a ruling.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

from evals.harness.reference import load_corpus
from evals.harness.verbs import same_action

LANES = (
    "spoofing",
    "tampering",
    "repudiation",
    "information-disclosure",
    "denial-of-service",
    "elevation-of-privilege",
)

#: The reader's classification of each verb row, keyed by case prefix and
#: reference index, with the mechanism the draft states in place of the
#: reference's.
CLASSIFICATION = {
    ("02", 3): (
        "wrong mechanism",
        "on-path",
        "an on-path swap of the firmware in transit, not a bucket writer",
    ),
    ("03", 8): (
        "wrong mechanism",
        "storage-copy",
        "a storage copy on unknown encryption at rest, not a grant holder",
    ),
    ("06", 3): (
        "wrong mechanism",
        "on-path",
        "an on-path change of gameplay traffic, not the player's own client",
    ),
    ("09", 4): (
        "wrong mechanism",
        "on-path",
        "an on-path rewrite of the SQL, not a party that drives the macros",
    ),
    ("10", 0): (
        "no ruling can move it",
        "",
        (
            "S-03 matched reference 16 of its own; reference 0 has a ruled"
            " outside position that S-03 does not state"
        ),
    ),
    ("10", 17): (
        "wrong mechanism",
        "storage-copy",
        "an offline change of a database copy, not stored comment injection",
    ),
    ("12", 17): (
        "wrong mechanism",
        "other",
        (
            "a direct database write past the loader, not a false extract on"
            " the genuine channel"
        ),
    ),
    ("13", 16): (
        "wrong mechanism",
        "other",
        (
            "missing server-side authority, not a cross-site request on the"
            " engineer's session"
        ),
    ),
    ("13", 19): (
        "wrong mechanism",
        "on-path",
        (
            "an on-path read of the traffic, not a cross-origin read through"
            " the engineer's session"
        ),
    ),
}


def _stride(path: Path) -> dict:
    return json.loads(path.read_text())["stride"]


def _report_claims(path: Path) -> list[dict]:
    report = json.loads(path.read_text())
    (analysis,) = [one for one in report["analyses"] if one["framework"] == "stride"]
    return analysis["claims"]


def _ruling(rulings: list[dict], reference, lane: str, draft_verb: str) -> dict | None:
    """The ruling on a reading of ``reference`` under the draft's action, if any."""
    return next(
        (
            ruling
            for ruling in rulings
            if ruling["lane"] == lane
            and ruling["reference"]["verb"] == reference.verb
            and set(ruling["reference"]["affected_element_ids"])
            == set(reference.affected_element_ids)
            and same_action(ruling["reading"]["verb"], draft_verb)
        ),
        None,
    )


def main(scored: Path, run: Path, corpus: Path, out: Path) -> None:
    cases = {case.id: case for case in load_corpus(corpus)}
    causes: Counter[tuple[str, str]] = Counter()
    must_finds: Counter[str] = Counter()
    matched: Counter[str] = Counter()
    volume: Counter[tuple[str, str]] = Counter()
    verb_rows, controls = [], []
    for artifact in sorted(scored.glob("[0-9]*.json")):
        scored_case = json.loads(artifact.read_text())
        (score,) = scored_case["scores"]
        case = cases[score["case"]]
        references = case.stride_claims()
        reports = run / f"{case.id}.reports"
        proposals = _stride(reports / f"{case.id}.proposals.json")
        drafts = _stride(reports / f"{case.id}.drafts.json")
        claims = _report_claims(reports / f"{case.id}.report.json")
        for lane in LANES:
            batch = proposals.get(lane) or {}
            volume[(lane, "proposals")] += len(batch.get("claims") or [])
            volume[(lane, "drafts")] += sum(d["category"] == lane for d in drafts)
            volume[(lane, "report")] += sum(c["category"] == lane for c in claims)
        for index, reference in enumerate(references):
            if reference.must_find:
                must_finds[reference.category] += 1
        by_id = {claim["id"]: claim for claim in claims}
        for pair in score["matched"]:
            if pair["tier"] != "must-find":
                continue
            reference = references[pair["reference_index"]]
            matched[reference.category] += 1
            claim = by_id[pair["threat_id"]]
            controls.append(
                {
                    "case": case.id,
                    "reference": pair["reference_index"],
                    "lane": reference.category,
                    "reference_verb": reference.verb,
                    "claim": claim["id"],
                    "claim_verb": claim["verb"],
                    "claim_title": claim["title"],
                }
            )
        rulings = json.loads((corpus / case.id / "rulings.json").read_text())
        drafts_by_id = {draft["id"]: draft for draft in drafts}
        (losses,) = scored_case["losses"]
        for loss in losses["losses"]:
            if not loss["must_find"]:
                continue
            causes[(loss["lane"], loss["cause"])] += 1
            if loss["cause"] != "verb":
                continue
            index = loss["reference_index"]
            reference = references[index]
            draft = drafts_by_id[loss["draft_id"]]
            ruling = _ruling(
                rulings.get("stride", []), reference, loss["lane"], loss["draft_verb"]
            )
            kind, mechanism, note = CLASSIFICATION[(case.id[:2], index)]
            verb_rows.append(
                {
                    "case": case.id,
                    "reference": index,
                    "lane": loss["lane"],
                    "reference_verb": loss["reference_verb"],
                    "ruled_position": case.ruled_positions.get(index) or "",
                    "draft": loss["draft_id"],
                    "draft_verb": loss["draft_verb"],
                    "place_relation": loss["place_relation"],
                    "draft_title": draft["title"],
                    "in_report": loss["draft_id"] in by_id,
                    "ruling": ruling["verdict"] if ruling else "",
                    "ruling_source": ruling["source"] if ruling else "",
                    "classification": kind,
                    "draft_mechanism": mechanism,
                    "note": note,
                }
            )
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in (("verb-rows.csv", verb_rows), ("controls.csv", controls)):
        with (out / name).open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    named = sorted({cause for _, cause in causes})
    lines = ["lane | must-finds | matched | " + " | ".join(named)]
    for lane in LANES:
        lines.append(
            f"{lane} | {must_finds[lane]} | {matched[lane]} | "
            + " | ".join(str(causes[(lane, cause)]) for cause in named)
        )
    lines.append(
        f"total | {sum(must_finds.values())} | {sum(matched.values())} | "
        + " | ".join(
            str(sum(n for (_, c), n in causes.items() if c == cause)) for cause in named
        )
    )
    lines.append("")
    lines.append("lane | proposals | drafts | report claims")
    for lane in LANES:
        lines.append(
            f"{lane} | {volume[(lane, 'proposals')]} | {volume[(lane, 'drafts')]}"
            f" | {volume[(lane, 'report')]}"
        )
    lines.append(
        "total | "
        + " | ".join(
            str(sum(n for (_, kind), n in volume.items() if kind == part))
            for part in ("proposals", "drafts", "report")
        )
    )
    (out / "summary.txt").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main(*(Path(arg) for arg in sys.argv[1:5]))
