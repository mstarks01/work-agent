"""What ADR 0041's guard holds back, read over archived proposals and graphs.

The guard (:func:`~analysis_service.assertions.apply_projection`'s
``hold_unchecked``) stops an unchecked assertion from turning a lead into a
stated value. This instrument prices that offline, before anyone pays for a
run. It pairs every archived assertion proposal with every archived extracted
graph of the same case, the pairing ``run.py bind`` makes, and projects each
catalog twice: once as every job does, and once with the guard off. It then
reads what the two models give the lanes: the values that differ, the decided
**Boundary Crossings**, and every framework package's **Candidates**.

**A graph today's gate refuses is skipped and counted.** A job never projects
onto a model that fails validation, and :func:`apply_projection` keeps the
extracted model whole when the projected one fails. So a pairing over such a
graph changes nothing either way, and counting its firings prices a cost no
job pays.

**A lost candidate is priced against the must-finds.** A candidate is
attention, not a finding, so the ceiling of a lost candidate is a must-find it
led to and no remaining candidate leads to. The match is by lane and by a
shared element, so it is an upper bound: a candidate about an API's write to a
store shares the API with a must-find about what a customer sends it. A
package whose must-finds name no element cannot be matched by place, so those
must-finds are counted apart rather than read as unled.

It runs no model and reads no credential.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from analysis_service.assertions import (
    AssertionRecord,
    CatalogProposal,
    apply_projection,
)
from analysis_service.candidates import generate_candidates
from analysis_service.frameworks import PACKAGES, FrameworkName
from analysis_service.system_model import SystemModel
from analysis_service.validation import validate
from evals.harness.artifact import load_artifact
from evals.harness.bundle import assertions_from_reports, extractions_from_reports
from evals.harness.modes import EvalRunError
from evals.harness.provenance import ProvenanceError
from evals.harness.reference import (
    GoldenCase,
    ReferenceClaim,
    load_corpus,
    tuning_cases,
)

CORPUS = Path(__file__).resolve().parents[1] / "corpus"

#: One candidate, keyed by what makes it a separate lead: its package, rule,
#: lane and elements. The facts are left out, because a rule that fires on the
#: same elements with a differently worded value is the same lead.
CandidateKey = tuple[FrameworkName, str, str, tuple[str, ...]]


@dataclass(frozen=True)
class GuardCost:
    """What the guard changed in one proposal/graph pairing."""

    #: ``(element ID, attribute)`` for each value the guard held back.
    held: tuple[tuple[str, str], ...]
    #: Whether the projection passes the gate only with the guard on. With it
    #: off, the projected model fails and every projection is dropped.
    kept_alive: bool
    lost: tuple[CandidateKey, ...]
    gained: tuple[CandidateKey, ...]
    decided_on: int
    decided_off: int
    #: The must-finds a lost candidate led to and no remaining one leads to.
    unled: tuple[str, ...]


def candidates(model: SystemModel, record: AssertionRecord) -> Counter[CandidateKey]:
    """Every package's candidates for ``model``, counted by :data:`CandidateKey`."""
    found: Counter[CandidateKey] = Counter()
    for name, package in PACKAGES.items():
        sets = generate_candidates(model, package.lanes, package.rules, record.catalog)
        for held in sets.values():
            for candidate in held.candidates:
                found[
                    (name, candidate.rule_id, candidate.lane, candidate.element_ids)
                ] += 1
    return found


def _must_finds(case: GoldenCase, name: FrameworkName) -> tuple[ReferenceClaim, ...]:
    """The case's must-finds for one package, or none where it references none."""
    return case.must_find_for(name) if name in case.references else ()


def _led(key: CandidateKey, case: GoldenCase) -> frozenset[str]:
    """The must-finds of the candidate's package, in its lane, on its elements."""
    name, _, lane, elements = key
    return frozenset(
        reference.claim
        for reference in _must_finds(case, name)
        if reference.lane == lane
        and set(reference.affected_element_ids) & set(elements)
    )


def guard_cost(
    case: GoldenCase, record: AssertionRecord, graph: SystemModel
) -> GuardCost:
    """Project ``record`` onto ``graph`` with the guard on and off, and compare.

    ``graph`` must pass the gate: see the module docstring for why a refused
    graph is skipped rather than read.
    """
    on_model, on = apply_projection(graph, record.catalog)
    off_model, off = apply_projection(graph, record.catalog, hold_unchecked=False)
    on_elements = {element.id: element for element in on_model.elements()}
    off_elements = {element.id: element for element in off_model.elements()}
    held = tuple(
        (projection.element_id, projection.attribute)
        for projection in off
        if projection.element_id in on_elements
        and getattr(on_elements[projection.element_id], projection.attribute)
        != getattr(off_elements[projection.element_id], projection.attribute)
    )
    with_guard, without = candidates(on_model, record), candidates(off_model, record)
    lost = tuple(sorted((without - with_guard).elements()))
    still = frozenset().union(*(_led(key, case) for key in with_guard))
    unled = frozenset().union(*(_led(key, case) for key in lost)) - still
    return GuardCost(
        held=held,
        kept_alive=bool(on) and not off,
        lost=lost,
        gained=tuple(sorted((with_guard - without).elements())),
        decided_on=sum(crossing.decided for crossing in on_model.boundary_crossings()),
        decided_off=sum(
            crossing.decided for crossing in off_model.boundary_crossings()
        ),
        unled=tuple(sorted(unled)),
    )


@dataclass(frozen=True)
class Priced:
    """The pooled reading, and what it skipped."""

    pairings: int
    refused_graphs: int
    costs: tuple[tuple[str, GuardCost], ...]


def price(
    cases: Sequence[GoldenCase],
    proposals: Sequence[Path],
    graphs: Sequence[Path],
) -> Priced:
    """Every archived proposal against every archived graph of its case."""
    by_id = {case.id: case for case in cases}
    extracted = [extractions_from_reports(path, cases) for path in graphs]
    pairings = refused = 0
    costs = []
    for path in proposals:
        held = [case for case in cases if case.id in load_artifact(path).cases]
        for case_id, result in assertions_from_reports(path, held).items():
            case = by_id[case_id]
            sources = {source.label: source.text for source in case.sources}
            proposal = CatalogProposal.model_validate(result.proposal)
            for sweep in extracted:
                reading = sweep.get(case_id)
                if reading is None or reading.extracted is None:
                    continue
                pairings += 1
                if validate(reading.extracted):
                    refused += 1
                    continue
                record = AssertionRecord.of(proposal, reading.extracted, sources)
                costs.append((case_id, guard_cost(case, record, reading.extracted)))
    return Priced(pairings, refused, tuple(costs))


def render(priced: Priced, cases: Sequence[GoldenCase]) -> str:
    """The pooled reading, as text."""
    costs = [cost for _, cost in priced.costs]
    held = Counter(attribute for cost in costs for _, attribute in cost.held)
    lost = Counter(key[:2] for cost in costs for key in cost.lost)
    gained = Counter(key[:2] for cost in costs for key in cost.gained)
    unled = Counter(
        (case_id, claim) for case_id, cost in priced.costs for claim in cost.unled
    )
    nameless = Counter(
        name
        for name in PACKAGES
        for case in cases
        for reference in _must_finds(case, name)
        if not reference.affected_element_ids
    )
    unplaceable = ", ".join(f"{name} {nameless[name]}" for name in PACKAGES)
    by_attribute = ", ".join(f"{name} {count}" for name, count in held.most_common())
    skipped = (
        f"{priced.pairings} proposal/graph pairings; {priced.refused_graphs} are"
        " over a graph today's gate refuses, which no job projects onto, and are"
        " skipped."
    )
    values = (
        f"- Values held back: {sum(held.values())} in"
        f" {sum(1 for cost in costs if cost.held)} pairings"
        f" ({by_attribute or 'none'})."
    )
    alive = (
        "- Pairings whose projection passes the gate only with the guard on:"
        f" {sum(cost.kept_alive for cost in costs)}."
    )
    crossings = (
        f"- Decided crossings: {sum(cost.decided_on for cost in costs)} with the"
        f" guard, {sum(cost.decided_off for cost in costs)} without."
    )
    counted = (
        f"- Candidates only without the guard: {sum(lost.values())};"
        f" only with it: {sum(gained.values())}."
    )
    heading = (
        "**Must-finds a lost candidate led to and no remaining candidate"
        " leads to** (pairings):"
    )
    blind = (
        f"Must-finds that name no element, which this match cannot see: {unplaceable}."
    )
    out = [
        "## What the ADR 0041 guard holds back",
        "",
        skipped,
        "",
        values,
        alive,
        crossings,
        counted,
        "",
        "| package | rule | lost | gained |",
        "| --- | --- | ---: | ---: |",
        *(
            f"| {name} | `{rule}` | {lost[(name, rule)]} | {gained[(name, rule)]} |"
            for name, rule in sorted(set(lost) | set(gained))
        ),
        "",
        heading,
        "",
        *(
            f"- {case_id}: {claim} ({count})"
            for (case_id, claim), count in sorted(unled.items())
        ),
        *([] if unled else ["- none"]),
        "",
        blind,
    ]
    return "\n".join(out) + "\n"


def arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "proposals",
        nargs="+",
        type=Path,
        help="archived assertion sweep artifacts, each with its .reports/ dir",
    )
    parser.add_argument(
        "--graphs",
        nargs="+",
        type=Path,
        required=True,
        help="archived extraction sweep artifacts whose emissions are the graphs",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=CORPUS,
        help="corpus root: the cases and their must-finds",
    )


def command_guard_cost(args: argparse.Namespace) -> int:
    """Price the guard. It runs no model and reads no credential."""
    try:
        cases = tuning_cases(load_corpus(args.corpus))
        priced = price(cases, args.proposals, args.graphs)
    except (OSError, ValueError, EvalRunError, ProvenanceError) as error:
        print(f"cannot price the guard: {error}", file=sys.stderr)
        return 1
    print(render(priced, cases), end="")
    return 0
