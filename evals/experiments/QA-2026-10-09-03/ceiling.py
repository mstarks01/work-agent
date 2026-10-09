"""E1: the must-finds a catalog rule leads to that no rule leads to without one.

Run from the repository root: ``PYTHONPATH=. uv run python
evals/experiments/QA-2026-10-09-03/ceiling.py stride`` (or ``asvs``). Each
case's signed facts are the catalog, which is what an assertion pass that
wrote exactly the signed rows would hold.
"""

import sys
from pathlib import Path

from analysis_service.candidates import generate_candidates
from analysis_service.frameworks import package_for
from analysis_service.open_facts import prepared_model
from evals.harness.reference import MUST_FIND, load_corpus, tuning_cases
from evals.reference_facts import FACTS_FILE, load_facts, reference_catalog

CORPUS = Path("evals/corpus")


def rules_hitting(model, catalog, reference, package):
    """The rules that fire in the reference's lane on an element it names."""
    lane = generate_candidates(model, package.lanes, package.rules, catalog).get(
        reference.lane
    )
    if lane is None:
        return set()
    targets = set(reference.affected_element_ids)
    return {c.rule_id for c in lane.candidates if targets & set(c.element_ids)}


def main(framework):
    package = package_for(framework)
    totals = {"must": 0, "unled": 0, "newly_led": 0, "new_rule_on_led": 0}
    for case in tuning_cases(load_corpus(CORPUS)):
        if not (CORPUS / case.id / FACTS_FILE).exists():
            continue
        sources = {source.label: source.text for source in case.sources}
        catalog = reference_catalog(load_facts(CORPUS / case.id), case.model, sources)
        projected = prepared_model(case.model, catalog)
        for index, reference in enumerate(case.references.get(framework) or ()):
            if reference.tier != MUST_FIND:
                continue
            totals["must"] += 1
            before = rules_hitting(case.model, None, reference, package)
            after = rules_hitting(projected, catalog, reference, package)
            if not before:
                totals["unled"] += 1
                if after:
                    totals["newly_led"] += 1
                    print(f"newly led {case.id}/{index}: {sorted(after)}")
            elif after - before:
                totals["new_rule_on_led"] += 1
                print(f"extra lead {case.id}/{index}: {sorted(after - before)}")
    print(totals)


if __name__ == "__main__":
    main(sys.argv[1])
