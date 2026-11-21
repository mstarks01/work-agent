"""Count where candidate STRIDE lead rules fire, for QA-2026-11-19-01-E5.

Usage: ``PYTHONPATH=src:. python unled_leads.py <corpus-dir> <run-dir>``.
``<run-dir>`` holds the ``<case>.reports`` folders of the paid run.

The candidate rules are defined here and nowhere else: no rule ships. Each
candidate is priced on two sets of models: the blessed corpus models of the
development cases, and the System Models of the paid run. Each heading prints
the number of models it read. Cases 14 and 15 are holdouts that the ledger
has exhausted, so ``development_cases`` keeps them. For each candidate the
script prints:

- ``places``: the element sets the candidate fires on;
- ``false leads``: places that share no element with a reference of the
  candidate's lane;
- ``newly led``: the references of the lane that no shipped rule leads and
  that the candidate leads (``*`` marks a must-find). The shipped side is
  ``evals.harness.triggers._hit``; the candidate side is its
  ``lead_rules``, the lane plus element overlap. On a run model this reads
  today's rules, where the loss attribution of -E2 reads the run's own
  ``fired_rules``. ``rules.py`` is the same at the run commit and here;
- ``unled targets``: the newly led references among the 11 ``unled`` losses
  of QA-2026-11-19-01-E2.

The shipped rules run with no assertion catalog, as in the paid run. The
script then lists each corpus place of each candidate with the references of
the lane it overlaps.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from evals.harness.bundle import read_report
from evals.harness.reference import (
    GoldenCase,
    ReferenceClaim,
    development_cases,
    load_corpus,
)
from evals.harness.triggers import _hit, lead_rules
from katra.analysis import (
    crossings_by_flow,
    inbound_flows,
    internet_exposed_elements,
    is_unverified,
    outbound_flows,
)
from katra.candidates import Candidate, CandidateSet
from katra.claims import FrameworkName
from katra.system_model import SystemModel

FRAMEWORK: FrameworkName = "stride"

Place = tuple[str, ...]
Find = Callable[[SystemModel], Iterator[Place]]
References = list[tuple[int, ReferenceClaim]]
Exposure = Literal["destination", "either", "any"]

UNLED_TARGETS = frozenset(
    {
        ("01", 2),
        ("01", 6),
        ("01", 9),
        ("06", 15),
        ("08", 19),
        ("09", 6),
        ("09", 16),
        ("09", 18),
        ("09", 19),
        ("10", 11),
        ("10", 13),
    }
)


def _processes(model: SystemModel) -> set[str]:
    return {process.id for process in model.processes}


def _exposed(model: SystemModel) -> set[str]:
    return {element.id for element in internet_exposed_elements(model)}


def _spoofing_unverified_internal(exposure: Exposure) -> Find:
    """Spoofing: an unverified flow into a process that crosses no boundary.

    ``exposure`` is ``destination`` (the destination is internet-facing),
    ``either`` (one end is) or ``any`` (no condition).
    """

    def find(model: SystemModel) -> Iterator[Place]:
        crossings = crossings_by_flow(model)
        processes, exposed = _processes(model), _exposed(model)
        for flow in model.data_flows:
            internal_into_process = (
                flow.id not in crossings and flow.destination in processes
            )
            if not internal_into_process or not is_unverified(flow.authentication):
                continue
            if exposure == "destination" and flow.destination not in exposed:
                continue
            if exposure == "either" and not {flow.source, flow.destination} & exposed:
                continue
            yield (flow.id, flow.source, flow.destination)

    return find


def _tampering_write_to_process(model: SystemModel) -> Iterator[Place]:
    """Tampering: an unverified flow that may write into a process."""
    processes = _processes(model)
    for flow in model.data_flows:
        writes = flow.destination in processes and flow.operations != "read"
        if writes and is_unverified(flow.authentication):
            yield (flow.id, flow.destination)


def _dos_exposed_callee(model: SystemModel) -> Iterator[Place]:
    """Denial of service: each element an internet-facing process calls."""
    for element in internet_exposed_elements(model):
        for flow in outbound_flows(model, element.id):
            yield (flow.destination, element.id)


def _dos_unverified_write(model: SystemModel) -> Iterator[Place]:
    """Denial of service: an unverified flow that may write to a store or process."""
    targets = _processes(model) | {store.id for store in model.data_stores}
    for flow in model.data_flows:
        writes = flow.destination in targets and flow.operations != "read"
        if writes and is_unverified(flow.authentication):
            yield (flow.id, flow.destination)


def _disclosure_unprotected_crossing(model: SystemModel) -> Iterator[Place]:
    """Information disclosure: an unprotected crossing, whatever its assets.

    The condition is the shipped tampering rule ``_unprotected_transit_crossing``
    in the information-disclosure lane.
    """
    crossings = crossings_by_flow(model)
    for flow in model.data_flows:
        if flow.id in crossings and is_unverified(flow.encryption_in_transit):
            yield (flow.id, flow.source, flow.destination)


def _elevation_exposed_outbound(model: SystemModel) -> Iterator[Place]:
    """Elevation: every flow out of an internet-facing process."""
    for element in internet_exposed_elements(model):
        for flow in outbound_flows(model, element.id):
            yield (flow.id, flow.source, flow.destination)


def _elevation_shared_entry(model: SystemModel) -> Iterator[Place]:
    """Elevation: an internet-facing process two callers reach, one unverified."""
    for element in internet_exposed_elements(model):
        flows = inbound_flows(model, element.id)
        callers = sorted({flow.source for flow in flows})
        unverified = any(is_unverified(flow.authentication) for flow in flows)
        if len(callers) >= 2 and unverified:
            yield (element.id, *callers)


def _tampering_any_store_write(model: SystemModel) -> Iterator[Place]:
    """Tampering: every flow that may write into a store, whatever its auth."""
    stores = {store.id for store in model.data_stores}
    for flow in model.data_flows:
        if flow.destination in stores and flow.operations != "read":
            yield (flow.id, flow.destination)


CANDIDATES: dict[str, tuple[str, Find]] = {
    "C1a": ("spoofing", _spoofing_unverified_internal("destination")),
    "C1b": ("spoofing", _spoofing_unverified_internal("either")),
    "C1c": ("spoofing", _spoofing_unverified_internal("any")),
    "C2": ("tampering", _tampering_write_to_process),
    "C3": ("denial-of-service", _dos_exposed_callee),
    "C3b": ("denial-of-service", _dos_unverified_write),
    "C4": ("information-disclosure", _disclosure_unprotected_crossing),
    "C5a": ("elevation-of-privilege", _elevation_exposed_outbound),
    "C5b": ("elevation-of-privilege", _elevation_shared_entry),
    "C7": ("tampering", _tampering_any_store_write),
}


def _leads(name: str, lane: str, places: Iterable[Place]) -> CandidateSet:
    """``places`` as the candidates of one rule, so ``lead_rules`` can read them."""
    return CandidateSet(
        lane=lane,
        candidates=tuple(
            Candidate(rule_id=name, lane=lane, element_ids=place) for place in places
        ),
    )


def _led(leads: CandidateSet, references: References) -> References:
    """The ``references`` that ``leads`` leads, by the overlap of ``lead_rules``."""
    return [
        (index, reference)
        for index, reference in references
        if lead_rules(leads, reference.affected_element_ids)
    ]


def _star(reference: ReferenceClaim) -> str:
    return "*" if reference.must_find else ""


@dataclass
class Tally:
    """One candidate's counts, each entry a case number or a reference label."""

    places: list[str] = field(default_factory=list)
    false_leads: list[str] = field(default_factory=list)
    newly_led: list[str] = field(default_factory=list)
    unled_targets: list[str] = field(default_factory=list)


def _in_lane(case: GoldenCase, lane: str) -> References:
    return [
        (index, reference)
        for index, reference in enumerate(case.references[FRAMEWORK])
        if reference.lane == lane
    ]


def _tally(case: GoldenCase, model: SystemModel, totals: dict[str, Tally]) -> None:
    number = case.id[:2]
    for name, (lane, find) in CANDIDATES.items():
        references = _in_lane(case, lane)
        places = list(dict.fromkeys(find(model)))
        tally = totals.setdefault(name, Tally())
        for place in places:
            tally.places.append(number)
            if not _led(_leads(name, lane, [place]), references):
                tally.false_leads.append(number)
        for index, reference in _led(_leads(name, lane, places), references):
            if _hit(FRAMEWORK, reference, model).rule_ids:
                continue
            tally.newly_led.append(f"{number}/{index}{_star(reference)}")
            if (number, index) in UNLED_TARGETS:
                tally.unled_targets.append(f"{number}/{index}")


def _print_totals(title: str, totals: dict[str, Tally]) -> None:
    print(f"== {title}")
    for name, tally in totals.items():
        print(
            f"{name} {CANDIDATES[name][0]}: places {len(tally.places)},"
            f" false leads {len(tally.false_leads)},"
            f" newly led {len(tally.newly_led)} [{', '.join(tally.newly_led)}],"
            f" unled targets {len(tally.unled_targets)}"
            f" [{', '.join(tally.unled_targets)}]"
        )


def main(corpus: Path, run: Path) -> None:
    cases = development_cases(load_corpus(corpus))
    corpus_totals: dict[str, Tally] = {}
    run_totals: dict[str, Tally] = {}
    run_models = 0
    for case in cases:
        _tally(case, case.model, corpus_totals)
        report = run / f"{case.id[:2]}.reports" / f"{case.id}.report.json"
        if report.exists():
            _tally(case, read_report(report).system_model, run_totals)
            run_models += 1
    _print_totals(f"corpus models, {len(cases)} cases", corpus_totals)
    _print_totals(f"run models, {run_models} cases", run_totals)
    print("== corpus places (references of the lane each place overlaps)")
    for case in cases:
        for name, (lane, find) in CANDIDATES.items():
            references = _in_lane(case, lane)
            for place in dict.fromkeys(find(case.model)):
                hits = [
                    f"{index}{_star(reference)}"
                    for index, reference in _led(
                        _leads(name, lane, [place]), references
                    )
                ]
                print(case.id[:2], name, place, "refs:", ",".join(hits) or "-")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
