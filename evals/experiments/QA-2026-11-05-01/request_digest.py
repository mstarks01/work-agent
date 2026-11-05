"""Print the digest of the information-disclosure lane request for each case.

Usage: ``request_digest.py <out.json>``, with ``PYTHONPATH`` set to the tree
to read. It builds each request as ``lane-replay --prepare-today`` builds it,
through :func:`~evals.harness.lane_replay.prepare_today` and
:func:`~evals.harness.lane_replay.compose`, and sends nothing. Two trees that
give equal digests send equal requests.
"""

import asyncio
import hashlib
import json
import sys
from pathlib import Path

from analysis_service.markdown_loader import MarkdownLoader
from evals.harness.descendants import FRAMEWORK
from evals.harness.lane_replay import compose, prepare_today
from evals.harness.provenance import REPO_ROOT
from evals.harness.reference import DEFAULT_CORPUS, load_case

ARMS = {
    "01-payments-checkout": "signed",
    "02-iot-fleet-telemetry": "signed",
    "03-batch-data-pipeline": "signed",
    "04-ml-inference-service": "signed",
    "05-cookbook-queue-webapp": "signed",
    "06-cookbook-online-game": "signed",
    "09-cookbook-sokify-retail": "signed",
    "10-cookbook-generic-cms": "signed",
    "11-sparse-shift-scheduling": "signed",
    "12-overclaiming-supplier-portal": "signed",
    "13-dispatch-control-plane": "signed",
    "14-loyalty-oauth-platform": "none",
    "15-multitenant-invoicing": "none",
}
LANE = "information-disclosure"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def request_digests(case_id: str, arm: str) -> dict[str, str]:
    """The instruction and user-turn digests of one case's lane request."""
    prepared = prepare_today(
        load_case(REPO_ROOT / DEFAULT_CORPUS / case_id), FRAMEWORK, arm
    )
    instruction, turn = asyncio.run(
        compose(
            prepared.material,
            FRAMEWORK,
            LANE,
            MarkdownLoader(REPO_ROOT / "frameworks" / FRAMEWORK),
            MarkdownLoader(REPO_ROOT / "prompts"),
        )
    )
    return {
        "instruction": _sha(instruction),
        "turn": _sha(turn.model_dump_json()),
    }


if __name__ == "__main__":
    out = Path(sys.argv[1])
    digests = {case: request_digests(case, arm) for case, arm in ARMS.items()}
    out.write_text(json.dumps(digests, indent=2) + "\n", encoding="utf-8")
    print(f"{len(digests)} requests from {REPO_ROOT}")
