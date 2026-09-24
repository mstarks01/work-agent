"""``run.py guard-cost``: what ADR 0041's guard holds back, read offline.

Two groups. The first rebuilds the one must-find the archive shows the guard
costing — case 09's web API, whose exposure an unchecked row states — on the
blessed model, and reads each figure the instrument reports. The second holds
the pooled reading to its own accounting over one archived pair of sweeps.
"""

from __future__ import annotations

from pathlib import Path

from analysis_service.assertions import (
    Assertion,
    AssertionCatalog,
    AssertionRecord,
    Subject,
)
from evals.harness import guard_cost
from evals.harness.reference import load_corpus
from evals.harness.run import COMMANDS

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS = REPO_ROOT / "evals" / "corpus"
CASES = {case.id: case for case in load_corpus(CORPUS)}
RETAIL = CASES["09-cookbook-sokify-retail"]
API = "process:web-api"


def exposed_api() -> tuple:
    """Case 09 with the web API's exposure open, and a row stating it.

    The user database moves to another zone, so the API's write to it crosses
    a boundary: the shape the elevation-of-privilege rule reads.
    """
    graph = RETAIL.model.model_copy(deep=True)
    (api,) = (process for process in graph.processes if process.id == API)
    api.exposure = "unknown"
    (database,) = (
        store for store in graph.data_stores if store.id == "store:user-database"
    )
    database.trust_zone = "boundary:marketing-office"
    row = Assertion(
        subject=API,
        predicate="internet-exposure",
        value="internet-facing",
        basis="inferred",
        explanation="fixture",
    )
    catalog = AssertionCatalog(
        subjects=[Subject(id=API, type="component", label="web API")], entries=[row]
    )
    return graph, AssertionRecord(proposed=1, catalog=catalog)


class TestOneHeldValue:
    def test_the_guard_holds_the_exposure_back(self) -> None:
        graph, record = exposed_api()
        cost = guard_cost.guard_cost(RETAIL, record, graph)

        assert cost.held == ((API, "exposure"),)
        assert not cost.kept_alive

    def test_the_rules_that_need_a_stated_exposure_lose_their_candidate(self) -> None:
        graph, record = exposed_api()
        cost = guard_cost.guard_cost(RETAIL, record, graph)

        assert {key[1] for key in cost.lost} >= {
            "elevation-of-privilege-inbound-from-exposed-process",
            "denial-of-service-internet-exposed-process",
        }

    def test_the_must_find_only_that_candidate_led_to_is_named(self) -> None:
        graph, record = exposed_api()
        cost = guard_cost.guard_cost(RETAIL, record, graph)

        (claim,) = cost.unled
        assert claim.startswith("A customer sends the web API")

    def test_a_row_that_changes_nothing_costs_nothing(self) -> None:
        """Over the blessed model the exposure is already stated."""
        _, record = exposed_api()
        cost = guard_cost.guard_cost(RETAIL, record, RETAIL.model)

        assert cost.held == () and cost.lost == () and cost.unled == ()


class TestThePooledReading:
    def test_every_pairing_is_priced_or_skipped(self) -> None:
        proposals = [
            REPO_ROOT
            / "evals/emissions/20260914T201030Z-assert-model-benchmark/gem-r1.json"
        ]
        graphs = [REPO_ROOT / "evals/emissions/20260914T2205Z-extract-sweep.json"]
        priced = guard_cost.price(list(CASES.values()), proposals, graphs)

        assert priced.pairings
        assert priced.pairings == priced.refused_graphs + len(priced.costs)
        assert "## What the ADR 0041 guard holds back" in guard_cost.render(
            priced, list(CASES.values())
        )

    def test_the_command_is_registered(self) -> None:
        assert COMMANDS["guard-cost"].run is guard_cost.command_guard_cost
