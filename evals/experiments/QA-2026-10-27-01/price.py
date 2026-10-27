"""QA-2026-10-27-01-E1: which must-finds four insider lead rules newly lead.

Run from the repository root with the directory of a sweep's re-scored
artifacts::

    ANALYSIS_OFFLINE=1 python evals/experiments/QA-2026-10-27-01/price.py <dir>

A must-find is newly led when no shipped rule in its lane names one of its
elements and a trial rule does, which is the overlap
:mod:`evals.harness.triggers` reads. Each row also says whether the sweep
missed that must-find, because a lead adds nothing where the lane already
found it.
"""

import json
import sys
from collections.abc import Iterator
from pathlib import Path

from analysis_service.assertions import AssertionCatalog
from analysis_service.candidates import Match, Rule, generate_candidates
from analysis_service.frameworks import package_for
from analysis_service.system_model import SystemModel
from evals.harness.reference import MUST_FIND, development_cases, load_corpus


def _writes(model: SystemModel) -> Iterator[tuple[str, str]]:
    for flow in model.data_flows:
        if flow.operations != "read":
            yield flow.source, flow.destination


def writer_of_store(model: SystemModel, catalog: AssertionCatalog) -> Iterator[Match]:
    """Every element that writes a store: the legitimate writer turned hostile."""
    stores = {store.id for store in model.data_stores}
    for flow in model.data_flows:
        if flow.destination in stores and flow.operations != "read":
            yield (flow.id, flow.destination, flow.source), {}


def wide_writer(model: SystemModel, catalog: AssertionCatalog) -> Iterator[Match]:
    """A process that writes two or more targets: one compromise, every target."""
    for process in model.processes:
        targets = sorted({dst for src, dst in _writes(model) if src == process.id})
        if len(targets) >= 2:
            yield (process.id, *targets), {}


def shared_entry(model: SystemModel, catalog: AssertionCatalog) -> Iterator[Match]:
    """A process two callers reach: can one caller send what the other sends?"""
    for process in model.processes:
        callers = sorted(
            {flow.source for flow in model.data_flows if flow.destination == process.id}
        )
        if len(callers) >= 2:
            yield (process.id, *callers), {}


def principal_through_process(
    model: SystemModel, catalog: AssertionCatalog
) -> Iterator[Match]:
    """A non-process caller of a process that writes a store."""
    processes = {process.id for process in model.processes}
    stores = {store.id for store in model.data_stores}
    for inbound in model.data_flows:
        if inbound.destination not in processes or inbound.source in processes:
            continue
        for src, dst in _writes(model):
            if src == inbound.destination and dst in stores:
                yield (inbound.source, inbound.destination, dst), {}


TRIAL_RULES = (
    Rule("trial-writer-of-store", "tampering", "", writer_of_store),
    Rule("trial-wide-writer", "elevation-of-privilege", "", wide_writer),
    Rule("trial-shared-entry", "elevation-of-privilege", "", shared_entry),
    Rule(
        "trial-through-process", "elevation-of-privilege", "", principal_through_process
    ),
)


def missed_must_finds(scores: Path) -> dict[tuple[str, int], str]:
    """(case, reference index) to loss cause, for every must-find the sweep missed."""
    missed = {}
    for path in scores.glob("*.json"):
        for case in json.loads(path.read_text())["losses"]:
            for loss in case["losses"]:
                if loss["must_find"]:
                    missed[case["case"], loss["reference_index"]] = loss["cause"]
    return missed


def main(scores: Path) -> None:
    missed = missed_must_finds(scores)
    package = package_for("stride")
    for rule in TRIAL_RULES:
        fired_total = 0
        rows = []
        for case in development_cases(load_corpus("evals/corpus")):
            shipped = generate_candidates(case.model, package.lanes, package.rules)
            fired = rule.fire(case.model, AssertionCatalog())
            fired_total += len(fired)
            for index, reference in enumerate(case.references.get("stride") or ()):
                if reference.tier != MUST_FIND or reference.lane != rule.lane:
                    continue
                targets = set(reference.affected_element_ids)
                led = any(
                    targets & set(candidate.element_ids)
                    for candidate in shipped[rule.lane].candidates
                )
                hits = [c for c in fired if targets & set(c.element_ids)]
                if led or not hits:
                    continue
                covers = any(targets <= set(c.element_ids) for c in hits)
                sweep = missed.get((case.id, index), "found or not run")
                rows.append(
                    f"  {case.id} ref {index} {reference.verb}"
                    f" {'covers' if covers else 'overlaps'} | sweep: {sweep}"
                )
        print(f"{rule.rule_id}: {fired_total} candidates, {len(rows)} newly led")
        print("\n".join(rows))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
