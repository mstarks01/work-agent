"""What the source review pass did in a heads sweep, read off its archived stages.

Run from the repository root: ``PYTHONPATH=. uv run python
evals/experiments/QA-2026-10-10-01/review_outcomes.py <artifact.json> ...``.
Each ``<artifact>.reports/<case>.assertions.json`` carries ``patch_outcomes``
under ``stages``: one outcome per operation the ``reread`` node proposed, and
whether ``apply`` discarded the batch whole. The ``reread`` charge comes from the
artifact's ``node_charges``.
"""

import json
import sys
from collections import Counter
from pathlib import Path


def outcomes_of(artifact):
    """Per case: the operation states, and whether the batch rolled back."""
    reports = artifact.with_suffix(".reports")
    for path in sorted(reports.glob("*.assertions.json")):
        stages = json.loads(path.read_text()).get("stages", {})
        patch = stages.get("patch_outcomes") or {}
        states = Counter(row["state"] for row in patch.get("outcomes", []))
        codes = Counter(
            row["code"]
            for row in patch.get("outcomes", [])
            if row["state"] == "refused"
        )
        yield path.name.split(".")[0], patch.get("rolled_back"), states, codes


def main(paths):
    total = Counter()
    rolled_back = 0
    refused_codes = Counter()
    for artifact in paths:
        data = json.loads(artifact.read_text())
        print(f"== {artifact}: reread ${data['node_charges'].get('reread', 0.0):.4f}")
        for case, back, states, codes in outcomes_of(artifact):
            print(f"  {case:34} rolled_back={back!s:5} {dict(states)}")
            total.update(states)
            refused_codes.update(codes)
            rolled_back += bool(back)
    print(f"operations: {dict(total)}; batches rolled back: {rolled_back}")
    if refused_codes:
        print(f"refusal codes: {dict(refused_codes)}")


if __name__ == "__main__":
    main([Path(arg) for arg in sys.argv[1:]])
