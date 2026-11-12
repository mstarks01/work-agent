"""The capability reader's request, as a job sends it, once on each named case.

The request is rebuilt with :func:`~analysis_service.node_requests.node_request`
and sent with :func:`~evals.harness.node_call.node_call`, on the deployment's
route, sampling and structured output for the ``capabilities`` node. In a job
the node runs right after the validity gate, so its user turn is the gate's
valid-route payload, ``VALID_TURN``; ``capture_turn.py`` records that turn from a
real graph run. The output names the tier, its selection, its sampling, the
pinned upstreams and the revision.

Usage, under ``bash -l`` with ``ANALYSIS_TIERS_FILE`` set:

    read_capabilities.py <out.json> <case dir> [<case dir> ...]

Each case directory holds ``source.md`` and ``model.json``. Each case is one
paid call, and the charge is the one the provider reported.
"""

import asyncio
import json
import sys
from pathlib import Path

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


async def read_case(case_dir: Path, deployment: Deployment) -> dict:
    sources = [Source.description(case_dir.joinpath("source.md").read_text())]
    instruction, turn = await node_request(
        compose_capabilities_prompt(MarkdownLoader(Path("prompts"))),
        seed_state(sources),
        VALID_TURN,
        None,
    )
    spent: list[float | None] = []
    call = node_call(deployment, CAPABILITIES_NODE, CapabilityReading, spent)
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
    }


def main(out: str, case_dirs: list[str]) -> None:
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
