"""Reading the fixture set and the corpus model its drafts are written against.

One home for both, because the CLI, the lints and the replay tests all need
them and three copies of "where the fixtures live" is three places to miss when
one moves.
"""

from __future__ import annotations

import json
from pathlib import Path

from analysis_service.system_model import SystemModel
from evals.critic_review.model import CriticFixture

REPO_ROOT = Path(__file__).resolve().parents[2]
CASES = REPO_ROOT / "evals" / "critic_review" / "cases.json"
CORPUS = REPO_ROOT / "evals" / "corpus"


def load_fixtures(path: Path = CASES) -> list[CriticFixture]:
    """Every fixture in declared order, validated."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [CriticFixture.model_validate(entry) for entry in raw]


def corpus_model(case_id: str) -> SystemModel:
    """The blessed model a fixture's ``case`` names.

    Read from the corpus rather than copied beside the fixtures, so a draft
    cannot quietly go on describing a system the corpus no longer holds.
    """
    path = CORPUS / case_id / "model.json"
    return SystemModel.model_validate(json.loads(path.read_text(encoding="utf-8")))


def source_text(case_id: str) -> str:
    """The submitted text a fixture's quotes must be found in."""
    return (CORPUS / case_id / "source.md").read_text(encoding="utf-8")
