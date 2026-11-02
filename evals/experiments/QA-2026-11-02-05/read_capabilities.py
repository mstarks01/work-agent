"""The capability reader's request, as a job sends it, once on each named case.

The request is rebuilt with :func:`~analysis_service.node_requests.node_request`
and sent with :func:`~evals.harness.node_call.node_call`, on the deployment's
route, sampling and structured output for the ``capabilities`` node. In a job
the node runs right after the validity gate, so its user turn is the gate's
valid-route payload, ``VALID_TURN``. ``capture_turn.py`` holds the rebuilt
request against a real graph run's on case 15 and writes
``runs/request-check.json``; this script refuses to call unless that check
passed. The only case input to the request is the Sources text, so the check
on one case covers the turn and the template filling of every case. The output names the tier, its selection, its
sampling, the pinned upstreams and the revision, and keeps what the provider
answered for each call.

Usage, under ``bash -l`` with ``ANALYSIS_TIERS_FILE`` set:

    read_capabilities.py <out.json> <case dir> [<case dir> ...]

Each case directory holds ``source.md`` and ``model.json``. Each case is one
paid call, and the charge is the one the provider reported.
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from analysis_service.capabilities import CAPABILITIES
from analysis_service.capability_reader import (
    CapabilityReading,
    with_stated_capabilities,
)
from analysis_service.deployment import Deployment
from analysis_service.execution import seed_state
from analysis_service.frameworks.asvs.applicability import applicability_for
from analysis_service.graph import CAPABILITIES_NODE, after_gate
from analysis_service.markdown_loader import MarkdownLoader
from analysis_service.node_requests import node_request
from analysis_service.prompts import compose_capabilities_prompt
from analysis_service.sources import Source
from analysis_service.system_model import SystemModel
from evals.harness.artifact import repo_commit
from evals.harness.node_call import node_call

#: What the validity gate hands the next node on its valid route.
VALID_TURN = {"issue_count": 0}
MFA = "multi-factor-authentication"
assert MFA in CAPABILITIES


#: The offline check ``capture_turn.py`` writes.
REQUEST_CHECK = Path(__file__).parent / "runs" / "request-check.json"


def _sources(case_dir: Path) -> list[Source]:
    return [Source.description(case_dir.joinpath("source.md").read_text())]


async def request_for(case_dir: Path) -> tuple[str, Any]:
    """The instruction and the user turn the capabilities node sends for a case."""
    return await node_request(
        compose_capabilities_prompt(MarkdownLoader(Path("prompts"))),
        seed_state(_sources(case_dir)),
        VALID_TURN,
        None,
    )


def refuse_unchecked() -> None:
    """Stop before any call unless the offline request check passed."""
    checks = json.loads(REQUEST_CHECK.read_text())
    passed = all(
        check["instruction_equal"] and check["turn_equal"] for check in checks.values()
    )
    if not checks or not passed:
        raise SystemExit("run capture_turn.py: the request is unchecked")


async def read_case(case_dir: Path, deployment: Deployment) -> dict:
    sources = _sources(case_dir)
    instruction, turn = await request_for(case_dir)
    spent: list[float | None] = []
    seen: list[dict[str, Any]] = []
    call = node_call(deployment, CAPABILITIES_NODE, CapabilityReading, spent, seen)
    reading = CapabilityReading.model_validate_json(await call(instruction, turn))
    model = after_gate(
        SystemModel.model_validate_json(case_dir.joinpath("model.json").read_text())
    )
    stated = with_stated_capabilities(
        model, reading, {source.label: source.text for source in sources}
    )
    decisions = applicability_for(3, stated.capability_facts())
    return {
        "case": case_dir.name,
        "rows": [row.model_dump() for row in reading.capabilities],
        "mfa_kept": [
            statement.model_dump()
            for statement in stated.capabilities
            if statement.capability == MFA
        ],
        "V6.4.4": decisions["V6.4.4"].state,
        "charge_usd": spent[0] if spent else None,
        "seen": seen,
    }


def main(out: str, case_dirs: list[str]) -> None:
    refuse_unchecked()
    deployment = Deployment.from_env()
    tier = deployment.tier_of(CAPABILITIES_NODE)
    record = {
        "node": CAPABILITIES_NODE,
        "tier": tier,
        "selection": deployment.tiers.tiers[tier].model_dump(mode="json"),
        "sampling": deployment.sampling.for_tier(tier).model_dump(mode="json"),
        "pinned_upstreams": list(deployment.tiers.pinned_upstreams()),
        "commit": repo_commit().model_dump(),
    }
    results = []
    for case_dir in case_dirs:
        results.append(asyncio.run(read_case(Path(case_dir), deployment)))
        Path(out).write_text(
            json.dumps({"record": record, "results": results}, indent=1) + "\n"
        )


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
