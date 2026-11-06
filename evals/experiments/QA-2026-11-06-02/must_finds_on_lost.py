"""The must-find claims that name an element the replay lost in at least one sweep."""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


def main(replay_path, corpus_dir):
    targets = json.loads(Path(replay_path).read_text())["arms"][0]["extraction"][
        "targets"
    ]
    claims_on = defaultdict(list)
    totals = Counter()
    for path in Path(corpus_dir).glob("*/claims/*.json"):
        case, framework = path.parent.parent.name, path.stem
        for index, claim in enumerate(json.loads(path.read_text())):
            if claim.get("tier") != "must-find":
                continue
            totals[framework] += 1
            for element in claim.get("affected_element_ids", []):
                claims_on[f"{case}/{element}"].append((framework, index))
    touched = set()
    for target in sorted(
        targets, key=lambda t: (-len(claims_on[t["element"]]), -t["losses"])
    ):
        named = claims_on[target["element"]]
        if named:
            print(
                f"{target['losses']}/5 lost  {len(named)} must-find  {target['element']}  {target['by_fate']}"
            )
        touched.update((target["element"].split("/")[0], *claim) for claim in named)
    by_framework = Counter(framework for _, framework, _ in touched)
    print(
        "must-finds touching a lost element:",
        len(touched),
        dict(by_framework),
        "of",
        dict(totals),
    )


if __name__ == "__main__":
    main(*sys.argv[1:])
