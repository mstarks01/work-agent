"""Measure the critic view of one application-wide Control against draft count.

Calls the production CriticRound.view() with one Control whose row sits on an
ASVS chapter and whose evidence is one file of max_source_bytes.
"""

import json
import sys

from analysis_service.claims import AnalysisMarks
from analysis_service.controls import Control, ControlDocument
from analysis_service.critic_round import CriticRound
from analysis_service.job_controls import CopiedControl
from analysis_service.resilience import load_resilience
from analysis_service.sources import text_digest
from tests.factories import valid_model
from tests.source_tree import REPO_ROOT
from tests.test_critic_controls import (
    APP_CONTROL,
    EVIDENCE_ID,
    _applied,
    _asvs_draft,
    _row,
)

#: The largest evidence file a Control can hold, as the deployment config sets it.
MAX_SOURCE_BYTES = load_resilience(
    REPO_ROOT / "config" / "resilience.toml", env={}
).max_source_bytes


def _app_control(text: str):
    evidence = {
        "id": EVIDENCE_ID,
        "name": "policy.md",
        "class": "config",
        "sha256": text_digest(text),
        "size_bytes": len(text),
    }
    document = ControlDocument.model_validate(
        {
            "id": APP_CONTROL,
            "name": "Encryption policy",
            "scope": {"type": "application", "name": ""},
            "facts": [],
            "rows": [_row("V11")],
            "evidence": [evidence],
        }
    )
    return CopiedControl.of(
        Control.create(owner_id="alice", document=document), {EVIDENCE_ID: text}
    )


def view_bytes(drafts: int, text: str) -> int:
    items = [_asvs_draft(f"V11.2.{n}", "store:orders-db") for n in range(1, drafts + 1)]
    critic_round = CriticRound.of(
        "asvs",
        valid_model(),
        items,
        AnalysisMarks(),
        None,
        _applied(_app_control(text)),
    )
    return len(json.dumps(critic_round.view()))


def main() -> None:
    text = ("Storage is encrypted with AES-256. " * 3000)[:MAX_SOURCE_BYTES]
    for drafts in map(int, sys.argv[1:]):
        size = view_bytes(drafts, text)
        print(
            f"{drafts:3d} drafts: view {size:>10,} bytes, about {size // 4:>9,} tokens"
        )


if __name__ == "__main__":
    main()
