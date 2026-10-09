"""E2: the must-finds that neither flag-off run's drafts match, by case and lane.

Run from the repository root: ``PYTHONPATH=. uv run python
evals/experiments/QA-2026-10-09-03/targets.py``. It reads the drafts of
Baseline ``6bff717`` and of sweep B, both on ``openrouter/openai/gpt-5.6-terra``
with the assertion pass off, and scores them on today's corpus. It prints the
counts, then the targets as JSON (``targets.json``).
"""

import glob
import json
from collections import Counter, defaultdict
from pathlib import Path

from analysis_service.frameworks.stride.record import DraftThreat
from analysis_service.system_model import ModelIndex
from evals.harness.identity import SubsetVerbIdentity
from evals.harness.ledger import Ledger
from evals.harness.reference import load_corpus, tuning_cases
from evals.harness.scorer import score_case

RUNS = (
    "evals/baselines/6bff717-gpt-5.6-terra-24dda4db/mstarks01-e32ad9c5.reports",
    "evals/runs/20260926T-sweep-b/*.reports",
)


def matched(case, directory):
    """The reference indexes one run's drafts match on ``case``."""
    (path,) = glob.glob(f"{directory}/{case.id}.drafts.json")
    with open(path) as f:
        drafts = [DraftThreat.model_validate(d) for d in json.load(f)["stride"]]
    matcher = SubsetVerbIdentity({case.id: ModelIndex.of(case.model).flow_endpoints})
    score = score_case(case, drafts, matcher, Ledger())
    return {pair.reference_index for pair in score.matched}


def main():
    targets = defaultdict(list)
    counts = Counter()
    for case in tuning_cases(load_corpus(Path("evals/corpus"))):
        found = [matched(case, directory) for directory in RUNS]
        for index, reference in enumerate(case.stride_claims()):
            if not reference.must_find:
                continue
            hits = sum(index in each for each in found)
            counts[hits] += 1
            if hits == 0:
                targets[f"{case.id}:{reference.lane}"].append(index)
    print("must-finds by runs whose drafts match them:", dict(sorted(counts.items())))
    print("case-lanes", len(targets))
    print(json.dumps(targets, indent=1))


if __name__ == "__main__":
    main()
