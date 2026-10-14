"""The published page prints held-out figures apart from development ones (#1093).

A figure pooled over tuned and held-out cases lets a fix shaped by the tuned
cases borrow the held-out ones' credit. Each published reader therefore takes
a :class:`~evals.harness.instruments.Cases` and counts only what it selects,
and the page prints one table per selection. The tests drive each reader on a
sweep that holds one held-out case, then the page, then hold the per-row
readers against the sweep's own aggregates on the sealed Baselines, where no
case is held out and the two must agree.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.harness import applicability, scorer, writing
from evals.harness.comparison import build, held_out_cases
from evals.harness.instruments import Cases
from evals.harness.provenance import REPO_ROOT
from tests.test_evals_comparison import merged

HELD = "15-multitenant-invoicing"
DEVELOPMENT = Cases(frozenset({HELD}), holdout=False)
HELD_OUT = Cases(frozenset({HELD}), holdout=True)


def _score(case: str, coverage: float, holdout: bool) -> dict:
    return {
        "case": case,
        "holdout": holdout,
        "metrics": {
            "reference_coverage": coverage,
            "must_find_coverage": coverage,
            "rejected_rate": 0.0,
        },
        "counts": {"rejected": 0, "pooled": 0, "open": 0, "unvoted": 0},
    }


def _applicability(case: str, matched: int, expected: int, holdout: bool) -> dict:
    return {
        "case": case,
        "holdout": holdout,
        "reference_set": "exhaustive",
        "expected": expected,
        "matched": [f"V{n}" for n in range(matched)],
        "missed": [],
        "matched_by_deferral": [],
        "must_find": 0,
        "must_find_missed": [],
        "negatives": [],
        "negatives_applied": [],
        "over_applied": ["X1"],
        "off_catalog": [],
    }


def _disposition(case: str, correct: int, judged: int) -> dict:
    return {
        "case": case,
        "judged": judged,
        "correct": correct,
        "needs_other_evidence": 2,
        "false_prose_requests": [{}] * (judged - correct),
    }


BLOCKS = {
    "scores": [
        _score("01-payments-checkout", 0.4, holdout=False),
        _score("02-iot-fleet-telemetry", 0.6, holdout=False),
        _score(HELD, 0.9, holdout=True),
    ],
    "applicability": [
        _applicability("01-payments-checkout", 1, 4, holdout=False),
        _applicability(HELD, 3, 3, holdout=True),
    ],
    "disposition": [
        _disposition("01-payments-checkout", 1, 2),
        _disposition(HELD, 2, 2),
    ],
    "writing": [
        {"case": "01-payments-checkout", "framework": "stride", "objections": 2},
        {"case": HELD, "framework": "stride", "objections": 5},
        {"case": HELD, "framework": "asvs", "objections": 7},
    ],
}


class TestEachReaderCountsOnlyTheCasesItSelects:
    def test_the_rows_name_the_held_out_case(self):
        assert held_out_cases(BLOCKS, ledger=()) == {HELD}

    def test_stride_coverage(self):
        assert scorer.published(
            BLOCKS, "reference_coverage", DEVELOPMENT
        ) == pytest.approx(0.5)
        assert scorer.published(BLOCKS, "reference_coverage", HELD_OUT) == 0.9

    def test_asvs_recall_and_precision_are_pooled_per_selection(self):
        assert applicability.published(BLOCKS, "recall", DEVELOPMENT) == 0.25
        assert applicability.published(BLOCKS, "recall", HELD_OUT) == 1.0
        assert applicability.published(BLOCKS, "precision", HELD_OUT) == 0.75

    def test_asvs_routing(self):
        assert (
            applicability.published_disposition(BLOCKS, "accuracy", DEVELOPMENT) == 0.5
        )
        assert applicability.published_disposition(BLOCKS, "accuracy", HELD_OUT) == 1.0

    def test_writing_objections_per_framework(self):
        assert writing.published(BLOCKS, "stride", DEVELOPMENT) == 2.0
        assert writing.published(BLOCKS, "stride", HELD_OUT) == 5.0
        assert writing.published(BLOCKS, "asvs", HELD_OUT) == 7.0

    @pytest.mark.parametrize(
        "read",
        [
            lambda blocks, cases: scorer.published(blocks, "reference_coverage", cases),
            lambda blocks, cases: applicability.published(blocks, "recall", cases),
            lambda blocks, cases: applicability.published_disposition(
                blocks, "accuracy", cases
            ),
            lambda blocks, cases: writing.published(blocks, "stride", cases),
        ],
        ids=["scores", "applicability", "disposition", "writing"],
    )
    def test_no_selected_case_is_no_figure_rather_than_zero(self, read):
        """A selection with no case measured nothing, which a zero would hide."""
        nothing_held = Cases(frozenset(), holdout=True)

        assert read(BLOCKS, nothing_held) is None


class TestThePagePrintsTheTwoApart:
    def test_a_held_out_case_gets_its_own_table(self, tmp_path):
        merged(
            tmp_path,
            "one",
            scores=[
                _score("01-payments-checkout", 0.4, holdout=False),
                _score(HELD, 0.9, holdout=True),
            ],
        )
        text = build(tmp_path)

        assert f"Held-out cases, printed apart: `{HELD}`." in text
        development, held_out = (
            text.split("`stride`, development cases")[1],
            text.split("`stride`, held-out cases")[1],
        )
        assert (
            "| 0.400 (spread unmeasured) |"
            in development.split("`stride`, held-out")[0]
        )
        assert "| 0.900 (spread unmeasured) |" in held_out

    def test_a_baseline_with_no_held_out_case_says_so(self, tmp_path):
        merged(tmp_path, "one", scores=[_score("01", 0.4, holdout=False)])
        text = build(tmp_path)

        assert "every figure below is over development cases" in text
        assert "held-out cases\n" not in text


def _sealed() -> list[dict]:
    root = REPO_ROOT / "evals" / "baselines"
    return [
        json.loads(path.read_text("utf-8"))
        for path in sorted(root.glob("*/*.json"))
        if path.name != "baseline.json"
    ]


class TestTheRowReadersAgreeWithTheSweepsOwnAggregates:
    """Two readers of one figure, held against each other on real artifacts."""

    @pytest.mark.parametrize("raw", _sealed(), ids=lambda raw: raw.get("run_id", "?"))
    def test_writing_objections(self, raw):
        assert not held_out_cases(raw)
        everything = Cases(frozenset(), holdout=False)
        for framework, totals in raw["writing_aggregate"]["by_framework"].items():
            assert writing.published(raw, framework, everything) == float(
                totals["objections"]
            )

    def test_the_sealed_baselines_hold_no_held_out_case(self):
        """The page's sentence for these rows rests on this."""
        assert _sealed()
        assert all(not held_out_cases(raw) for raw in _sealed())

    def test_the_pooled_figures_have_one_rule(self):
        """``pooled`` is ``pooled_rows`` over the rows it would write."""
        rows = BLOCKS["applicability"]

        assert applicability.pooled_rows(rows)["recall"] == pytest.approx(
            4 / 7, abs=1e-4
        )


def test_the_page_has_no_unread_part(tmp_path: Path):
    """Every part the page can print is reached by some Baseline shape."""
    merged(tmp_path, "one", scores=[_score(HELD, 0.9, holdout=True)])

    assert "`stride`, held-out cases" in build(tmp_path)
