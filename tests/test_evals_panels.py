"""The pre-generation panels, read from tracked archives.

Each panel reads one source in its own unit. The tests hold each panel to
the instrument it wraps, so a panel cannot count a fact again its own way.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from evals.harness import audit, delivery, panels
from evals.harness.artifact import load_artifact
from evals.harness.delivery import (
    Agreement,
    Delivery,
    DeliveryRow,
    LaneComparison,
    LaneTrace,
    PlaceFacts,
)
from evals.harness.ledger import Ledger
from evals.harness.reference import development_cases, load_corpus
from evals.harness.replay import pooled_assertions, pooled_extraction
from evals.harness.run import main, panels_of, replay_artifact, scorecard_of
from evals.harness.scorecard import Rate
from katra.graph import LANE_SHARED_KEYS
from katra.prompts import EXTRACTION_DELTAS, PROMPT_BODY_NAMES
from tests.source_tree import REPO_ROOT

CORPUS = REPO_ROOT / "evals/corpus"
BASELINE = (
    REPO_ROOT / "evals/baselines/2198870-gpt-5.6-terra-6cd39a95/mstarks01-e5d71171.json"
)
EXTRACTION = REPO_ROOT / "evals/emissions/20261017T-1813-name-rule/after-r1.json"
COMBINED_HEAD = REPO_ROOT / "evals/emissions/20260917T-arms-luna-pro/arm-A.json"
ASSERTION_ONLY = REPO_ROOT / "evals/emissions/20260916T-assert-step6/luna-after-r1.json"
BALLOT = REPO_ROOT / "evals/experiments/QA-2026-10-14-02/ballot-returned.csv"


def _by_node(found):
    return {panel.node: panel for panel in found}


def _sweep(path: Path):
    return replay_artifact(path, load_corpus(CORPUS), CORPUS, None)


def test_the_extraction_panel_is_the_replay_of_its_sweep():
    sweep = _sweep(EXTRACTION)
    pool = pooled_extraction([sweep])

    panel = panels.extraction_panel(sweep)

    assert panel.phase == "extraction"
    assert {fate: panel.counts[fate] for fate in pool["fates"]} == pool["fates"]
    assert panel.counts["unreviewed extra elements"] == pool["extra"]
    for key, row in pool["attribute_aggregate"]["by_attribute"].items():
        assert panel.rates[f"wrong {key}"] == Rate(
            row["compared"] - row["agreed"], row["compared"]
        )


def test_an_assertion_panel_names_the_node_that_wrote_its_catalog():
    head = panels.assertion_panel(_sweep(COMBINED_HEAD))
    alone = panels.assertion_panel(_sweep(ASSERTION_ONLY))

    assert head.source.endswith("catalog written by extract")
    assert alone.source.endswith("catalog written by assert")
    assert head.phase == alone.phase == "extraction"


def test_pooled_recall_and_the_mean_of_each_run_are_two_figures():
    sweep = _sweep(COMBINED_HEAD)
    pool = pooled_assertions([sweep])

    panel = panels.assertion_panel(sweep)
    mean, over = panel.means["mean of each emission's required-fact recall"]

    assert panel.rates["pooled required-fact recall"] == Rate(
        pool["recovered"], pool["required"]
    )
    assert over == len(sweep.assertions)
    assert mean == pytest.approx(
        sum(replay.recall for replay in sweep.assertions) / over
    )
    assert panel.unread == pool["produced"]["unreviewed"]


def test_a_sweep_with_no_assertion_emission_is_uncaptured():
    sweep = replace(_sweep(COMBINED_HEAD), assertions=())

    panel = panels.assertion_panel(sweep)

    assert panel.uncaptured and not panel.counts and panel.path is None


def test_the_capability_panel_counts_the_ballot_and_states_no_recall():
    row = panels.ballot_row(BALLOT, audit.load())

    panel = panels.capability_panel(BALLOT, row)

    assert row is not None and row.experiment_id == "QA-2026-10-14-02-E1"
    assert (panel.counts["right"], panel.counts["wrong"]) == (63, 1)
    assert panel.rates == {"precision": Rate(63, 64)}
    assert panel.unread == 0
    assert panel.path is not None
    assert panel.path.evidence[0] == "ledger row QA-2026-10-14-02-E1"


def test_a_ballot_no_ledger_row_lists_is_unverified(tmp_path: Path):
    ballot = tmp_path / "ballot.csv"
    ballot.write_text("verdict\nright\nwrong\nunsure\n", encoding="utf-8")

    panel = panels.capability_panel(ballot, panels.ballot_row(ballot, audit.load()))

    assert panel.rates["precision"] == Rate(1, 2)
    assert panel.unread == 1
    assert panel.path == panels.ProductionPath(
        "unverified", ("no ledger row lists the ballot",)
    )


@pytest.mark.parametrize(
    ("changed", "fidelity"),
    [
        (None, "unverified"),
        (("prompts/extract.md", "README.md"), "known-different"),
        (("prompts/critic.md",), "unverified"),
        (("prompts/extract-compact.md",), "unverified"),
        ((), "unverified"),
    ],
)
def test_a_route_is_known_different_only_where_its_prompt_body_changed(
    changed, fidelity
):
    path = panels.route_path("abcdef1234", ("extract",), changed)

    assert path.fidelity == fidelity
    assert path.evidence
    for changed_path in changed or ():
        if changed_path.startswith("prompts/extract"):
            assert any(line.startswith(changed_path) for line in path.evidence)


@pytest.mark.parametrize("name", PROMPT_BODY_NAMES)
def test_each_prompt_body_is_a_file_the_route_rule_names(name):
    for path in panels.prompt_files(name):
        assert (REPO_ROOT / path).is_file()


def test_the_extract_route_names_every_extraction_delta():
    deltas = {delta for names in EXTRACTION_DELTAS.values() for delta in names}

    assert {f"prompts/{name}.md" for name in deltas} < panels.prompt_files("extract")


def test_an_analysis_sweep_feeds_only_the_preparation_panel():
    found = _by_node(panels_of(BASELINE, CORPUS, None))

    assert tuple(sorted(found)) == tuple(sorted(panels.PANEL_NODES))
    for node in ("extract", "assert", "capabilities"):
        assert found[node].uncaptured and not found[node].counts
    prepare = found["prepare"]
    assert prepare.phase == "preparation"
    traced = sum(
        count for name, count in prepare.counts.items() if name.startswith("rebuilt ")
    )
    assert traced == prepare.counts["lane material untraced"] + sum(
        count
        for name, count in prepare.counts.items()
        if name.startswith("same leads ")
    )


def test_the_preparation_panel_counts_the_delivery_trace():
    result = delivery.trace(
        scorecard_of(BASELINE, CORPUS, Ledger()),
        load_artifact(BASELINE),
        development_cases(load_corpus(CORPUS)),
        CORPUS,
    )
    lanes = [row.lane for row in result.rows]
    facts = [fact for row in result.rows for fact in row.place_facts.traces]

    panel = panels.preparation_panel(result)

    assert lanes
    assert sum(
        count for name, count in panel.counts.items() if name.startswith("rebuilt ")
    ) == len(lanes)
    assert panel.unread == sum(lane.comparison is None for lane in lanes)
    assert panel.counts["with an archived lead rule"] == sum(
        1 for lane in lanes if lane.archived_lead_rules
    )
    assert panel.counts["re-keyed must-finds outside the population"] == (
        result.re_keyed
    )
    assert sum(
        panel.counts[f"place facts {state}"] for state in delivery.FACT_DELIVERIES
    ) == len(facts)
    assert panel.counts["with no peer"] == sum(row.peer is None for row in result.rows)


def _delivery(*readings: tuple[Agreement, Agreement] | None) -> Delivery:
    """A delivery trace of one row for each captured pair, or ``None`` untraced.

    A pair is the lane's ``same_leads`` and ``same_lane_artifacts``.
    """
    reference = scorecard_of(BASELINE, CORPUS, Ledger()).rows[0]
    rows = tuple(
        DeliveryRow(
            reference=reference,
            lane=LaneTrace(
                comparison=None
                if reading is None
                else LaneComparison(
                    delivered="equal",
                    same_leads=reading[0],
                    same_lane_artifacts=reading[1],
                ),
                rebuilt="equal",
                archived_lead_rules=(),
            ),
            place_facts=PlaceFacts((), ""),
            peer=None,
        )
        for reading in readings
    )
    return Delivery(run=reference.run, rows=rows, re_keyed=0, untraced={})


AGREES = ("agrees", "agrees")


@pytest.mark.parametrize(
    ("readings", "fidelity"),
    [
        ((AGREES, AGREES), "unverified"),
        ((AGREES, ("agrees", "differs")), "known-different"),
        ((AGREES, ("differs", "differs")), "known-different"),
        ((AGREES, None), "unverified"),
        ((), "unverified"),
    ],
)
def test_the_preparation_path_reads_each_compared_dimension(readings, fidelity):
    panel = panels.preparation_panel(_delivery(*readings))

    assert panel.path is not None and panel.path.fidelity == fidelity


def test_an_agreeing_preparation_path_names_each_dimension_it_does_not_compare():
    panel = panels.preparation_panel(_delivery(AGREES))

    assert panel.path is not None
    assert {f"not compared: the shared key {key}" for key in LANE_SHARED_KEYS} <= set(
        panel.path.evidence
    )
    assert "not compared: the prepared user turn" in panel.path.evidence
    assert (
        "not compared: the case options, which the archive does not record"
        in panel.path.evidence
    )


def test_the_command_writes_each_panel_apart(tmp_path: Path):
    out = tmp_path / "panels.json"

    code = main(
        [
            "panels",
            str(COMBINED_HEAD),
            "--capability-ballot",
            str(BALLOT),
            "--out",
            str(out),
        ]
    )
    written = json.loads(out.read_text())

    assert code == 0
    assert set(written) == {"record", "panels"}
    assert {panel["node"] for panel in written["panels"]} == set(panels.PANEL_NODES)
    assert len({panel["source"] for panel in written["panels"]}) >= 2
