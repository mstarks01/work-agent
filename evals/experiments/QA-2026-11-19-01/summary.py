"""Summarize the scorecard and loss outputs of QA-2026-11-19-01, one run.

Usage: ``PYTHONPATH=. python summary.py <out-dir>``. The directory holds the
``card-<case>.json`` files that ``run.py scorecard --out`` writes and the
``score-<case>.json`` files that ``run.py score --out`` writes, one of each
for each case artifact of the run. It imports the status and cause sets from
``evals.harness``, so it runs with the checkout on ``PYTHONPATH``.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from evals.harness.losses import CAUSES
from evals.harness.scorecard import STATUSES


def _load(out: Path, prefix: str) -> dict[str, dict]:
    return {
        path.stem.removeprefix(prefix): json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(out.glob(f"{prefix}*.json"))
    }


def _identity(card: dict) -> str:
    run = card["run"]
    return (
        f"run {run['run_commit']['commit'][:8]}"
        f" (clean {run['run_commit']['clean']}), run corpus {run['run_corpus'][:12]},"
        f" scored on corpus {run['scored']['corpus_digest'][:12]}"
        f" at {run['scored']['repo_commit']['commit'][:8]}"
    )


def _case_lines(cards: dict[str, dict], scores: dict[str, dict]) -> list[str]:
    lines = ["case  must-finds  matched  missed  incompatible  cost_usd"]
    totals: Counter[str] = Counter()
    cost = 0.0
    for case, card in cards.items():
        status = Counter(row["status"] for row in card["rows"])
        charge = sum(scores[case]["node_charges"].values())
        cost += charge
        totals.update(status)
        totals["all"] += len(card["rows"])
        lines.append(
            f"{case}    {len(card['rows']):>4}  {status['matched']:>7}"
            f"  {status['missed']:>6}  {status['incompatible']:>12}  {charge:.4f}"
        )
    lines.append(
        f"all   {totals['all']:>4}  {totals['matched']:>7}  {totals['missed']:>6}"
        f"  {totals['incompatible']:>12}  {cost:.4f}"
    )
    return lines


def _lane_lines(cards: dict[str, dict]) -> list[str]:
    lanes: dict[str, Counter[str]] = {}
    for card in cards.values():
        for row in card["rows"]:
            lanes.setdefault(row["lane"], Counter())[row["status"]] += 1
    lines = ["lane  " + "  ".join(STATUSES)]
    for lane, status in lanes.items():
        counts = "  ".join(str(status[name]) for name in STATUSES)
        lines.append(f"{lane}  {counts}")
    return lines


def _losses(scores: dict[str, dict]) -> list[tuple[str, dict]]:
    return [
        (case, loss)
        for case, score in scores.items()
        for block in score["losses"]
        for loss in block["losses"]
    ]


def _cause_lines(losses: list[tuple[str, dict]]) -> list[str]:
    every = Counter(loss["cause"] for _, loss in losses)
    must = Counter(loss["cause"] for _, loss in losses if loss["must_find"])
    lines = ["cause  all_losses  must_find_losses"]
    lines.extend(f"{cause}  {every[cause]}  {must[cause]}" for cause in CAUSES)
    lines.append(f"all  {sum(every.values())}  {sum(must.values())}")
    return lines


def _row_lines(losses: list[tuple[str, dict]], causes: tuple[str, ...]) -> list[str]:
    header = (
        "case  reference  lane  must_find  cause  reference_verb  draft_verb"
        "  draft  place_relation"
    )
    lines = [header]
    for case, loss in losses:
        if loss["cause"] in causes:
            lines.append(
                f"{case}  {loss['reference_index']}  {loss['lane']}"
                f"  {loss['must_find']}  {loss['cause']}  {loss['reference_verb']}"
                f"  {loss['draft_verb']}  {loss['draft_id']}  {loss['place_relation']}"
            )
    return lines


def main() -> int:
    out = Path(sys.argv[1])
    cards = _load(out, "card-")
    scores = _load(out, "score-")
    losses = _losses(scores)
    must_find = [(case, loss) for case, loss in losses if loss["must_find"]]
    sections = [
        ["identity", *sorted({_identity(card) for card in cards.values()})],
        ["must-finds and cost by case", *_case_lines(cards, scores)],
        ["must-finds by lane", *_lane_lines(cards)],
        ["losses by cause (run.py score)", *_cause_lines(losses)],
        ["must-find losses", *_row_lines(must_find, CAUSES)],
        ["verb rows, every reference", *_row_lines(losses, ("verb",))],
    ]
    print("\n\n".join("\n".join(section) for section in sections))
    return 0


if __name__ == "__main__":
    sys.exit(main())
