"""The zones and the crossing of each reference flow, on the signed model and on each extracted one."""

import sys
from pathlib import Path

from analysis_service.frameworks.stride.rules import zone_kinds
from evals.harness.alignment import align
from evals.harness.reference import load_corpus
from evals.harness.run import extractions_from_reports, load_artifact


def crossing_of(model, flow_id):
    kinds = zone_kinds(model)
    for crossing in model.boundary_crossings():
        if crossing.flow_id == flow_id:
            return (
                crossing.source_zone,
                kinds.get(crossing.source_zone),
                "->",
                crossing.destination_zone,
                kinds.get(crossing.destination_zone),
                "decided" if crossing.decided else "undecided",
            )
    return "no crossing"


case_id, index = sys.argv[1], int(sys.argv[2])
case = next(c for c in load_corpus(Path("evals/corpus")) if c.id.startswith(case_id))
reference = case.references["stride"][index]
flows = [e for e in reference.affected_element_ids if e.startswith("flow:")]
print(case.id, index, reference.lane)
print("  signed zones:", {b.id: b.kind for b in case.model.trust_boundaries})
for flow in flows:
    print("  signed", flow, crossing_of(case.model, flow))
for artifact in sys.argv[3:]:
    extracted = extractions_from_reports(load_artifact(Path(artifact)), [case])[
        case.id
    ].extracted
    if extracted is None:
        print("  ", Path(artifact).name, "no model")
        continue
    produced = align(case, extracted).produced_of
    print(
        "  ",
        Path(artifact).name,
        "zones:",
        {b.id: b.kind for b in extracted.trust_boundaries},
    )
    for flow in flows:
        print("     ", produced.get(flow), crossing_of(extracted, produced.get(flow)))
