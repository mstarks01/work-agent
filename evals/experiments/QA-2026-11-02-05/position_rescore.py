"""Re-score each Baseline before and after the position rulings.

Usage, from the repository root: ``position_rescore.py``. Each Baseline's sweep
is scored by ``run.py score`` twice: on the corpus of :data:`BEFORE`, the
commit the rulings were recorded on, and on the corpus of this tree. A matched
must-find counts as on a ruled reference when this tree's ``ruled_positions``
holds its ``reference_index``. Writes ``runs/position-rescore.json``.
"""

import json
import subprocess
import sys
import tarfile
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any

from evals.harness.artifact import CORPUS_DIR, REPO_ROOT, corpus_digest
from evals.harness.reference import load_case

HERE = Path(__file__).parent
OUT = HERE / "runs" / "position-rescore.json"
BEFORE = "ac8a07b4922427fd8f1bf2e3545aab127b0d6db0"
BASELINES = REPO_ROOT / "evals" / "baselines"


def _corpus_at(commit: str, into: Path) -> Path:
    archive = subprocess.run(
        ["git", "archive", commit, "evals/corpus"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    ).stdout
    with tarfile.open(fileobj=BytesIO(archive)) as tar:
        tar.extractall(into, filter="data")
    return into / "evals" / "corpus"


def _counts(scored: Path, ruled: dict[str, Any], must: dict[str, set[int]]) -> dict:
    blocks = json.loads(scored.read_text(encoding="utf-8"))["series"]["blocks"]
    scores = blocks["all"]["scores"]
    hits = [
        (score["case"], pair["reference_index"])
        for score in scores
        for pair in score["matched"]
        if pair["reference_index"] in must[score["case"]]
    ]
    on_ruled = sum(index in ruled[case] for case, index in hits)
    return {
        "must_find_matched": sum(s["counts"]["must_find_matched"] for s in scores),
        "must_find_total": sum(s["counts"]["must_find_total"] for s in scores),
        "matched_on_a_ruled_reference": on_ruled,
        "matched_on_an_any_reference": len(hits) - on_ruled,
    }


def rescore(work: Path) -> dict[str, Any]:
    """The must-find matches of each Baseline on the two corpora."""
    cases = [load_case(path) for path in sorted(CORPUS_DIR.iterdir()) if path.is_dir()]
    ruled = {case.id: case.ruled_positions for case in cases}
    must = {
        case.id: {i for i, claim in enumerate(case.stride_claims()) if claim.must_find}
        for case in cases
    }
    corpora = {"before": _corpus_at(BEFORE, work / "before"), "after": CORPUS_DIR}
    baselines: dict[str, Any] = {}
    for baseline in sorted(path for path in BASELINES.iterdir() if path.is_dir()):
        (sweep,) = (p for p in baseline.glob("*.json") if p.name != "baseline.json")
        row = {}
        for side, corpus in corpora.items():
            scored = work / f"{baseline.name}-{side}.json"
            subprocess.run(
                [sys.executable, "-m", "evals.harness.run", "score", str(sweep)]
                + ["--corpus", str(corpus), "--out", str(scored)],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
            )
            row[side] = _counts(scored, ruled, must)
        baselines[baseline.name] = row
    return {
        "command": "position_rescore.py",
        "series": "all",
        "before_corpus": {
            "commit": BEFORE,
            "corpus_digest": corpus_digest(corpora["before"]),
        },
        "after_corpus": {"corpus_digest": corpus_digest(CORPUS_DIR)},
        "baselines": baselines,
    }


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as work:
        result = rescore(Path(work))
    OUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
