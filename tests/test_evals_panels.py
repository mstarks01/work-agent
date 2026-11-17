"""The pre-generation panels, read from tracked archives.

Each panel reads one source in its own unit. The tests hold each panel to
the instrument it wraps, so a panel cannot count a fact again its own way.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.harness import panels
from evals.harness.reference import load_corpus
from evals.harness.replay import pooled_assertions, pooled_extraction
from evals.harness.run import main, panels_of, replay_artifact
from evals.harness.scorecard import Rate
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
    assert panel.unknown == pool["produced"]["unreviewed"]


def test_the_capability_panel_counts_the_ballot_and_states_no_recall():
    panel = panels.capability_panel(BALLOT)

    assert (panel.counts["right"], panel.counts["wrong"]) == (63, 1)
    assert panel.rates == {"precision": Rate(63, 64)}
    assert panel.unknown == 0
    assert panel.path is not None and panel.path.fidelity == "known-different"


def test_a_ballot_verdict_outside_the_set_is_unknown(tmp_path: Path):
    ballot = tmp_path / "ballot.csv"
    ballot.write_text("verdict\nright\nwrong\nunsure\n", encoding="utf-8")

    panel = panels.capability_panel(ballot)

    assert panel.rates["precision"] == Rate(1, 2)
    assert panel.unknown == 1


@pytest.mark.parametrize(
    ("changed", "fidelity"),
    [
        (None, "unverified"),
        (("prompts/extract.md", "README.md"), "known-different"),
        (("prompts/critic.md",), "unverified"),
        ((), "unverified"),
    ],
)
def test_a_route_is_known_different_only_where_its_own_prompt_changed(
    changed, fidelity
):
    path = panels.route_path("abcdef1234", ("extract",), changed)

    assert path.fidelity == fidelity
    assert path.evidence


def test_an_analysis_sweep_feeds_only_the_preparation_panel():
    found = _by_node(panels_of(BASELINE, CORPUS, None))

    assert set(found) == {"extract", "assert", "capabilities", "prepare"}
    for node in ("extract", "assert", "capabilities"):
        assert found[node].uncaptured and not found[node].counts
    prepare = found["prepare"]
    assert prepare.phase == "preparation"
    traced = sum(
        count for name, count in prepare.counts.items() if name.startswith("rebuilt ")
    )
    assert traced == sum(
        count for name, count in prepare.counts.items() if name.startswith("capture ")
    )


def test_uncaptured_lane_material_is_unknown_and_never_equivalent():
    prepare = _by_node(panels_of(BASELINE, CORPUS, None))["prepare"]

    assert prepare.unknown == prepare.counts["delivered uncaptured"] > 0
    assert prepare.path is not None and prepare.path.fidelity == "unverified"


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
    assert {panel["node"] for panel in written["panels"]} == {
        "extract",
        "assert",
        "capabilities",
        "prepare",
    }
    assert len({panel["source"] for panel in written["panels"]}) >= 2
