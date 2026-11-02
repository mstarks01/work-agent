"""What today's code gives the information-disclosure lane on case 05's reference model."""

from pathlib import Path

from analysis_service.evidence import evidence_catalog, render_catalog
from analysis_service.graph import FrameworkNodes, after_gate, framework_lane_inputs
from evals.harness.reference import load_case
from evals.harness.replay import signed_reference
from tests.test_prompt_lints import PACKAGE_LOADERS

case = load_case("evals/corpus/05-cookbook-queue-webapp")
reference = signed_reference(Path("evals/corpus"), case)
assert reference is not None, "case 05 has a signed facts file"
held = reference.catalog
model = after_gate(case.model)
inputs = framework_lane_inputs(
    FrameworkNodes("stride"), model, {}, held, PACKAGE_LOADERS["stride"]
)
lane = inputs.artifacts["information-disclosure"]
for line in lane["candidates"].splitlines():
    if "config" in line:
        print("LEAD", line.strip()[:300])
print("NOTES", [d for d in inputs.retrieved])
catalog = evidence_catalog(model, held)
text = render_catalog(catalog, held)
for line in text.splitlines():
    if "custody" in line or "config" in line and "credential" in line:
        print("EVIDENCE", line[:300])
