"""The capability reader's request as a job sends it, once on each named case.

A job sends the composed instruction, with ``{input_text}`` filled from state,
as the system message, the rendered Sources as the user message, and the
``CapabilityReading`` schema in ADK's own response format, at the base tier's
output cap. This script sends the same request straight to litellm, without
the ADK runner and without the deployment's structured-output fallback. It then
runs the node's own check and the ASVS level 3 decisions of the result.

Usage, under ``bash -l`` with ``ANALYSIS_TIERS_FILE`` naming the tiers file:

    read_capabilities.py <out.json> <route> <upstream> <case dir> [<case dir> ...]

Each case directory holds ``source.md`` and ``model.json``. Each case is one
paid call, and the charge is the one the provider reported.
"""

import json
import os
import sys
from pathlib import Path

from google.adk.models.lite_llm import _to_litellm_response_format

from analysis_service import model_gate
from analysis_service.capability_reader import (
    CapabilityReading,
    with_stated_capabilities,
)
from analysis_service.charges import reported_charge_of
from analysis_service.execution import seed_state
from analysis_service.frameworks.asvs.applicability import applicability_for
from analysis_service.graph import STATE_INPUT_TEXT, after_gate
from analysis_service.markdown_loader import MarkdownLoader
from analysis_service.offline import refuse_live_inference
from analysis_service.prompts import compose_capabilities_prompt
from analysis_service.sources import Source
from analysis_service.system_model import SystemModel
from analysis_service.vendors import CredentialMode, vendor_for_route

#: The base tier's output cap in ``config/sampling.toml``.
MAX_OUTPUT_TOKENS = 16384
MFA = "multi-factor-authentication"


def read_case(case_dir: Path, route: str, upstream: str) -> dict:
    sources = [Source.description(case_dir.joinpath("source.md").read_text())]
    rendered = seed_state(sources)[STATE_INPUT_TEXT]
    instruction = compose_capabilities_prompt(MarkdownLoader(Path("prompts")))
    vendor = vendor_for_route(route)
    key = vendor.credential_kwargs(os.environ, CredentialMode.API_KEY)["api_key"]
    refuse_live_inference(route)
    reply = model_gate._litellm.completion(
        model=route,
        api_key=key,
        max_tokens=MAX_OUTPUT_TOKENS,
        response_format=_to_litellm_response_format(CapabilityReading, route),
        messages=[
            {
                "role": "system",
                "content": instruction.replace("{input_text}", rendered),
            },
            {"role": "user", "content": rendered},
        ],
        **vendor.upstream_kwargs((upstream,)),
    )
    reading = CapabilityReading.model_validate_json(reply.choices[0].message.content)
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
        "charge_usd": reported_charge_of(reply),
        "usage": {
            "prompt_tokens": reply.usage.prompt_tokens,
            "completion_tokens": reply.usage.completion_tokens,
        },
    }


def main(out: str, route: str, upstream: str, case_dirs: list[str]) -> None:
    results = []
    for case_dir in case_dirs:
        results.append(read_case(Path(case_dir), route, upstream))
        Path(out).write_text(
            json.dumps({"route": route, "results": results}, indent=1) + "\n"
        )


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:])
