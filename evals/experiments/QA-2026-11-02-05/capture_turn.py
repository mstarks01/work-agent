"""Record the instruction and the user turn of the capabilities node in a real graph run.

The graph runs with scripted replies, so the run is offline and costs nothing.
Usage, from the repository root: ``capture_turn.py <case dir> <out.json>``.
"""

import asyncio
import json
import sys
from pathlib import Path

from pydantic import Field

from analysis_service import graph
from analysis_service.jobs import JobRecord
from analysis_service.pipeline import AdkPipelineRunner
from analysis_service.sources import Source
from analysis_service.system_model import SystemModel
from tests.factories import ScriptedLlm, emitted, sample_selection, scripted_pipeline


class Recording(ScriptedLlm):
    requests: list = Field(default_factory=list)

    async def generate_content_async(self, llm_request, stream=False):
        self.requests.append(llm_request)
        async for response in super().generate_content_async(llm_request, stream):
            yield response


case = Path(sys.argv[1])
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
    sources=[Source.description((case / "source.md").read_text())],
    frameworks=sample_selection(("asvs",)),
    ask_questions=True,
)
record.transition("running")


async def on_node(node):
    pass


asyncio.run(AdkPipelineRunner(pipeline).run(record, on_node))
reader = llms[graph.CAPABILITIES_NODE]
assert isinstance(reader, Recording)
request = reader.requests[0]
out = {
    "system": request.config.system_instruction,
    "contents": [
        c.model_dump(mode="json", exclude_none=True) for c in request.contents
    ],
}
Path(sys.argv[2]).write_text(json.dumps(out, indent=1))
print("contents:", json.dumps(out["contents"])[:600])
