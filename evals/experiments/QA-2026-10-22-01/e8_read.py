"""Print each gained must-find beside the draft that the aliased map matches to it."""

import sys
from collections import Counter
from pathlib import Path

from analysis_service.frameworks.stride.record import Threat
from analysis_service.report import Report
from analysis_service.system_model import ModelIndex
from evals.harness.bundle import finished_file, load_artifact, stride_threats
from evals.harness.identity import SubsetVerbIdentity
from evals.harness.ledger import Ledger
from evals.harness.reference import flows_by_case, load_corpus
from evals.harness.scorer import score_case

corpus = {case.id: case for case in load_corpus(Path("evals/corpus"))}
artifact = load_artifact(Path(sys.argv[1]))
wanted = {int(x) for x in sys.argv[2].split(",")}
for case_id in artifact.cases:
    case = corpus[case_id]
    file = finished_file(artifact, case_id, "report")
    if file is None:
        continue
    report = Report.model_validate_json(file.read_text())
    blessed = ModelIndex.of(case.model).flow_endpoints
    by_ends = Counter(blessed.values())
    alias = {
        f: next(b for b, e in blessed.items() if e == ends)
        for f, ends in ModelIndex.of(report.system_model).flow_endpoints.items()
        if f not in blessed and by_ends[ends] == 1
    }
    threats = {
        t.id: t.model_copy(
            update={
                "affected_element_ids": [
                    alias.get(e, e) for e in t.affected_element_ids
                ]
            }
        )
        for t in stride_threats(report)
    }
    original: dict[str, Threat] = {t.id: t for t in stride_threats(report)}
    identity = SubsetVerbIdentity(flows_by_case([case], {case_id: report.system_model}))
    refs = case.stride_claims()
    for pair in score_case(case, list(threats.values()), identity, Ledger()).matched:
        if pair.reference_index in wanted:
            t = original[pair.threat_id]
            print(
                f"ref {pair.reference_index} [{refs[pair.reference_index].verb}] {refs[pair.reference_index].claim}"
            )
            print(f"  {t.id} [{t.verb}] {t.title}\n    {t.affected_element_ids}\n")
