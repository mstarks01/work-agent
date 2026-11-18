"""Summarize the scorecard and panel outputs of QA-2026-11-17-11, one group each.

Usage: ``python3 summarize.py <out-dir>``. The directory holds the
``card-<group>-<artifact>.json`` and ``panels-<group>-<artifact>.json`` files
that ``run.py scorecard --out`` and ``run.py panels --out`` write. A group is
``sweep`` (one case artifact for each case of one sweep) or a Baseline's
commit prefix. No group is added to another.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

STATUSES = ("matched", "missed", "incompatible", "uncaptured")


def _group(path: Path) -> str:
    return path.stem.split("-")[1]


def _cards(out: Path) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for path in sorted(out.glob("card-*.json")):
        groups[_group(path)].append(json.loads(path.read_text(encoding="utf-8")))
    return groups


def _identities(cards: list[dict]) -> set[tuple[str, str, str]]:
    return {
        (
            card["run"]["run_commit"]["commit"][:8],
            card["run"]["run_corpus"][:12],
            card["run"]["scored"]["corpus_digest"][:12],
        )
        for card in cards
    }


def _card_lines(name: str, cards: list[dict]) -> list[str]:
    status: Counter[str] = Counter()
    compatibility: Counter[str] = Counter()
    lanes: dict[str, Counter[str]] = defaultdict(Counter)
    fan: Counter[str] = Counter()
    assembly: Counter[str] = Counter()
    critic: Counter[str] = Counter()
    left_out: list[str] = []
    for card in cards:
        left_out.extend(card["left_out"])
        for row in card["rows"]:
            status[row["status"]] += 1
            compatibility[row["compatibility"]] += 1
            lanes[row["lane"]][row["status"]] += 1
        check = card["fan_in"]
        for key in ("compatible_blocks", "unread_blocks", "inspected", "accounted"):
            fan[key] += check[key]
        for key in ("unexplained", "extra", "unavailable", "unreadable"):
            fan[key] += check[key]
        fan["capture_faults"] += check["capture_faults"]
        fan["incompatible_blocks"] += len(check["incompatible"])
        assembly["reproduced"] += card["assembly"]["reproduced"]
        assembly["differ"] += len(card["assembly"]["differ"])
        assembly["refused"] += len(card["assembly"]["refused"])
        loss = card["critic"]["reference_loss"]
        critic["killed matched references"] += loss["denominator"]
        critic["of them no reported claim took"] += loss["numerator"]
    compatible = status["matched"] + status["missed"]
    lines = [
        f"== {name}: {len(cards)} artifact(s), run/corpus/scored {sorted(_identities(cards))}",
        f"must-finds {sum(status.values())}: "
        + ", ".join(f"{key} {status[key]}" for key in STATUSES),
        f"compatibility: {dict(sorted(compatibility.items()))}",
        (
            f"matched of compatible {status['matched']}/{compatible};"
            f" matched of all {status['matched']}/{sum(status.values())}"
        ),
    ]
    for lane, counts in sorted(lanes.items()):
        lines.append(
            f"  {lane:<24}" + " ".join(f"{key} {counts[key]}" for key in STATUSES)
        )
    lines.append(f"left out: {sorted(left_out)}")
    lines.append(f"assembly: {dict(assembly)}")
    lines.append(f"fan-in: {dict(fan)}")
    lines.append(f"critic: {dict(critic)}")
    return lines


def _panel_lines(out: Path) -> list[str]:
    lines = ["== panels"]
    for path in sorted(out.glob("panels-*.json")):
        for panel in json.loads(path.read_text(encoding="utf-8"))["panels"]:
            if panel["uncaptured"]:
                continue
            path_fidelity = panel["path"]["fidelity"] if panel["path"] else None
            lines.append(
                f"{path.stem} {panel['phase']}/{panel['node']}: {panel['counts']}"
                f" rates {[(k, (r['numerator'], r['denominator'])) for k, r in panel['rates'].items()]}"
                f" means {panel['means']} unread {panel['unread']} path {path_fidelity}"
            )
    return lines


def main(out: Path) -> None:
    lines: list[str] = []
    for name, cards in sorted(_cards(out).items()):
        lines.extend(_card_lines(name, cards))
    lines.extend(_panel_lines(out))
    print("\n".join(lines))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
