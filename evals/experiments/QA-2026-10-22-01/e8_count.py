"""Count the end-to-end matches the sibling rule refuses only because the extraction renamed a flow."""

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
for path in sys.argv[1:]:
    artifact = load_artifact(Path(path))
    for case_id in sorted(artifact.cases):
        case = corpus[case_id]
        file = finished_file(artifact, case_id, "report")
        if file is None:
            continue
        report = Report.model_validate_json(file.read_text())
        blessed = ModelIndex.of(case.model).flow_endpoints
        extracted = ModelIndex.of(report.system_model).flow_endpoints
        by_ends = Counter(blessed.values())
        alias = {
            flow: next(b for b, e in blessed.items() if e == ends)
            for flow, ends in extracted.items()
            if flow not in blessed and by_ends[ends] == 1
        }
        threats: list[Threat] = stride_threats(report)
        shared = sum(n > 1 for n in Counter(alias.values()).values())
        aliased = [
            t.model_copy(
                update={
                    "affected_element_ids": [
                        alias.get(e, e) for e in t.affected_element_ids
                    ]
                }
            )
            for t in threats
        ]
        identity = SubsetVerbIdentity(
            flows_by_case([case], {case_id: report.system_model})
        )
        must = {i for i, ref in enumerate(case.stride_claims()) if ref.must_find}
        before = {
            p.reference_index
            for p in score_case(case, threats, identity, Ledger()).matched
        }
        after = {
            p.reference_index
            for p in score_case(case, aliased, identity, Ledger()).matched
        }
        cited = sum(any(e in alias for e in t.affected_element_ids) for t in threats)
        print(
            f"{path} {case_id}: renamed flows {len(alias)} of {len(extracted)} ({shared} corpus flows take two), drafts citing one {cited} of {len(threats)};"
            f" matches {len(before)} -> {len(after)} (gained {sorted(after - before)}, lost {sorted(before - after)});"
            f" must-finds {len(before & must)} -> {len(after & must)} of {len(must)} (gained {sorted((after - before) & must)})"
        )
