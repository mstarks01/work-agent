"""The promotion gate of ADR 0086: the capability reader as it ships, once on each holdout case.

The node's own instruction (``compose_capabilities_prompt``), the job's
sources rendered as the service renders them (``render_sources``), and the
node's own check (``with_stated_capabilities``) over the case's reference
model. The one difference from a job: the call asks for a JSON object, where
the service asks through the node's schema.

Usage: gate.py <out.json> <model route> <case> [<case> ...]
"""

import json
import os
import sys
from pathlib import Path

from analysis_service.capability_reader import (
    CapabilityReading,
    with_stated_capabilities,
)
from analysis_service.markdown_loader import MarkdownLoader
from analysis_service.model_gate import completion
from analysis_service.prompts import compose_capabilities_prompt
from analysis_service.sources import Source, render_sources
from analysis_service.system_model import SystemModel
from analysis_service.vendors import CredentialMode, vendor_for_route


def read_case(case: str, model: str, key: str) -> dict:
    sources = [Source.description(Path("evals/corpus", case, "source.md").read_text())]
    instruction = compose_capabilities_prompt(MarkdownLoader(Path("prompts")))
    prompt = instruction.replace("{input_text}", render_sources(sources))
    reply = completion(
        model=model,
        api_key=key,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}],
    )
    reading = CapabilityReading.model_validate_json(reply.choices[0].message.content)
    reference = SystemModel.model_validate_json(
        Path("evals/corpus", case, "model.json").read_text()
    )
    texts = {source.label: source.text for source in sources}
    stated = with_stated_capabilities(reference, reading, texts)
    before = {s.capability for s in reference.capabilities}
    return {
        "case": case,
        "rows": [row.model_dump() for row in reading.capabilities],
        "kept": [
            s.model_dump() for s in stated.capabilities if s.capability not in before
        ],
        "usage": {
            "prompt_tokens": reply.usage.prompt_tokens,
            "completion_tokens": reply.usage.completion_tokens,
        },
    }


def main(out: str, model: str, cases: list[str]) -> None:
    vendor = vendor_for_route(model)
    key = vendor.credential_kwargs(os.environ, CredentialMode.API_KEY)["api_key"]
    results = [read_case(case, model, key) for case in cases]
    Path(out).write_text(
        json.dumps({"model": model, "results": results}, indent=1) + "\n"
    )


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3:])
