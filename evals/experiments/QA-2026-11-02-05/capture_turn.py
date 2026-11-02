"""Hold the capability reader's rebuilt request against a real graph run's.

For each case, the graph runs with scripted replies, so the run is offline and
costs nothing, and the request its ``capabilities`` node sends is captured. A
case needs a model that passes the validity gate against its own Sources:
case 15's reference model does, and the empty models of the contrast cases do
not.
``read_capabilities.request_for`` rebuilds the instruction and the user turn
the paid script sends. The output records the digest of each file that decides
them and, per case, whether the instruction and the user turn are equal, with
the captured user turn. It compares neither the response schema nor the
sampling: ``node_call`` sends those from the deployment.

Usage, from the repository root:

    capture_turn.py <case dir> [<case dir> ...]

It writes ``read_capabilities.REQUEST_CHECK``, the file the paid script reads.
"""

import asyncio
import json
import sys
from pathlib import Path

from pydantic import Field

from analysis_service import graph
from analysis_service.jobs import JobRecord
from analysis_service.pipeline import AdkPipelineRunner
from analysis_service.system_model import SystemModel
from evals.harness.artifact import repo_commit
from tests.factories import ScriptedLlm, emitted, sample_selection, scripted_pipeline

sys.path.insert(0, str(Path(__file__).parent))
from read_capabilities import REQUEST_CHECK, input_digests, request_for, sources_of


class Recording(ScriptedLlm):
    """A scripted model that keeps each request it receives."""

    requests: list = Field(default_factory=list)

    async def generate_content_async(self, llm_request, stream: bool):
        self.requests.append(llm_request)
        async for response in super().generate_content_async(llm_request, stream):
            yield response


def captured(case: Path):
    """The request the capabilities node sends in a real graph run of ``case``."""
    model = SystemModel.model_validate_json((case / "model.json").read_text())
    pipeline, llms = scripted_pipeline(
        {
            "extract": emitted(model),
            graph.CAPABILITIES_NODE: json.dumps({"capabilities": []}),
        },
        llm_class=Recording,
        entry=graph.ENTRY_HEAD_ONLY,
        frameworks=("asvs",),
        capability_reader=True,
    )
    record = JobRecord.create(
        owner_id="alice",
        sources=sources_of(case),
        frameworks=sample_selection(("asvs",)),
        ask_questions=True,
    )
    record.transition("running")

    async def on_node(node: str) -> None:
        pass

    asyncio.run(AdkPipelineRunner(pipeline).run(record, on_node))
    reader = llms[graph.CAPABILITIES_NODE]
    assert isinstance(reader, Recording)
    return reader.requests[0]


def main(case_dirs: list[str]) -> None:
    checks = {}
    for case_dir in map(Path, case_dirs):
        request = captured(case_dir)
        instruction, turn = asyncio.run(request_for(case_dir))
        contents = [
            c.model_dump(mode="json", exclude_none=True) for c in request.contents
        ]
        checks[case_dir.name] = {
            "instruction_equal": instruction == request.config.system_instruction,
            "turn_equal": [turn.model_dump(mode="json", exclude_none=True)] == contents,
            "captured_turn": contents,
        }
    REQUEST_CHECK.write_text(
        json.dumps(
            {
                "commit": repo_commit().model_dump(),
                "inputs": input_digests(),
                "cases": checks,
            },
            indent=1,
        )
        + "\n"
    )
    print(
        json.dumps(
            {k: [v["instruction_equal"], v["turn_equal"]] for k, v in checks.items()}
        )
    )


if __name__ == "__main__":
    main(sys.argv[1:])
