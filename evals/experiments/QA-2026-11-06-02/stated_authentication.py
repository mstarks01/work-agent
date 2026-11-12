"""The authentication of case 12's category-manager flow, on the signed model and each extracted one."""

import sys
from pathlib import Path

from evals.harness.alignment import align
from evals.harness.reference import load_corpus
from evals.harness.run import extractions_from_reports, load_artifact

FLOW = "flow:entity:category-manager>process:supplier-portal>review-documents"


def authentication(model, flow_id):
    return next(flow.authentication for flow in model.data_flows if flow.id == flow_id)


case = next(c for c in load_corpus(Path("evals/corpus")) if c.id.startswith("12"))
print("signed:", repr(authentication(case.model, FLOW)))
for artifact in sys.argv[1:]:
    extracted = extractions_from_reports(load_artifact(Path(artifact)), [case])[
        case.id
    ].extracted
    produced = align(case, extracted).produced_of.get(FLOW)
    print(
        Path(artifact).name,
        produced,
        repr(authentication(extracted, produced)) if produced else None,
    )
