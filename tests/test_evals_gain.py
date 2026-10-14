"""The gain gates: a coverage gain bought by saying more is caught (#1093).

The negative control is the point of the module. A copy of a sealed Baseline is
flooded: every case gains a must-find and also files six more unmatched
findings that nobody voted on. Coverage rises, and the gates must not let that
rise through. The positive control gains the same coverage with no added
volume and every finding answered, and the gates must let it through, or the
gates refuse everything and the negative control proves nothing.
"""

from __future__ import annotations

import copy
import json

import pytest

from analysis_service.frameworks import PACKAGES
from evals.harness import gain
from evals.harness.provenance import REPO_ROOT
from evals.harness.run import COMMANDS

SEALED = (
    REPO_ROOT / "evals/baselines/6bff717-gpt-5.6-terra-24dda4db/mstarks01-e32ad9c5.json"
)
SPREAD = {"volume": 1.5, "rejected-rate": 0.05}
#: The parts two artifacts share, for a fixture that is not a sealed sweep.
SAME = {
    "corpus_digest": "d" * 64,
    "artifact_version": 7,
    "flow_identity_version": 2,
    "mode": "end-to-end",
}


def _baseline() -> dict:
    raw = json.loads(SEALED.read_text("utf-8"))
    for row in raw["scores"]:
        # Every unmatched finding answered, as after a full sitting, so the
        # baseline itself passes the reviewed gate.
        row["counts"]["unvoted"] = 0
    return raw


def _gained(raw: dict, *, added: int, voted: bool) -> dict:
    """The same sweep, one must-find better per case, with ``added`` new findings."""
    candidate = copy.deepcopy(raw)
    for row in candidate["scores"]:
        counts = row["counts"]
        counts["must_find_matched"] = min(
            counts["must_find_total"], counts["must_find_matched"] + 1
        )
        counts["eligible"] += added
        counts["produced"] += added
        counts["unvoted"] = 0 if voted else added
    return candidate


def _verdicts(readings) -> dict[str, str]:
    return {reading.gate: reading.verdict for reading in readings}


class TestTheNegativeControl:
    def test_a_flooded_gain_fails_on_volume_and_on_review(self):
        readings = gain.read(
            _baseline(),
            _gained(_baseline(), added=6, voted=False),
            "stride",
            spread=SPREAD,
        )

        verdicts = _verdicts(readings)
        assert verdicts["volume"] == "fail"
        assert verdicts["reviewed"] == "fail"

    def test_a_flood_somebody_voted_on_still_fails_on_volume(self):
        """Votes do not launder volume: the two halves are separate gates."""
        readings = gain.read(
            _baseline(),
            _gained(_baseline(), added=6, voted=True),
            "stride",
            spread=SPREAD,
        )

        assert _verdicts(readings)["volume"] == "fail"

    def test_more_rejections_fail(self):
        candidate = _baseline()
        for row in candidate["scores"]:
            row["metrics"]["rejected_rate"] += 0.2

        verdicts = _verdicts(gain.read(_baseline(), candidate, "stride", spread=SPREAD))
        assert verdicts["rejected-rate"] == "fail"


class TestThePositiveControl:
    def test_a_gain_with_no_added_volume_and_every_finding_answered_passes(self):
        readings = gain.read(
            _baseline(),
            _gained(_baseline(), added=0, voted=True),
            "stride",
            spread=SPREAD,
        )

        assert set(_verdicts(readings).values()) == {"pass"}

    def test_a_small_rise_inside_the_spread_passes(self):
        readings = gain.read(
            _baseline(),
            _gained(_baseline(), added=1, voted=True),
            "stride",
            spread=SPREAD,
        )

        assert _verdicts(readings)["volume"] == "pass"


class TestAnUnreadGateIsNotAPass:
    def test_without_a_spread_the_sd_gates_are_unread(self):
        verdicts = _verdicts(gain.read(_baseline(), _baseline(), "stride"))

        assert verdicts["volume"] == "inconclusive"
        assert verdicts["rejected-rate"] == "inconclusive"

    def test_the_sealed_baseline_as_merged_waits_for_votes(self):
        """The sealed artifact counts its findings unvoted, so the gain waits."""
        raw = json.loads(SEALED.read_text("utf-8"))

        assert _verdicts(gain.read(raw, raw, "stride"))["reviewed"] == "fail"

    def test_a_package_with_no_vote_reading_says_so(self):
        readings = {
            one.gate: one for one in gain.read(SAME, SAME, "asvs", spread=SPREAD)
        }

        assert readings["reviewed"].verdict == "inconclusive"
        assert "vote" in readings["reviewed"].why

    def test_held_out_cases_never_count(self):
        """A flood on a held-out case alone moves no gate: it is not read here."""
        baseline = _baseline()
        candidate = copy.deepcopy(baseline)
        flooded = candidate["scores"][0]
        flooded["holdout"] = True
        flooded["counts"]["eligible"] += 50
        flooded["counts"]["unvoted"] += 50
        trimmed = copy.deepcopy(baseline)
        trimmed["scores"] = baseline["scores"][1:]

        verdicts = _verdicts(gain.read(trimmed, candidate, "stride", spread=SPREAD))
        assert verdicts["volume"] == "pass"
        assert verdicts["reviewed"] == "pass"


def test_every_package_has_its_readers():
    assert set(gain.READERS) == set(PACKAGES)


def test_the_asvs_volume_reads_what_the_run_applied_unlisted():
    blocks = {
        **SAME,
        "applicability": [
            {"case": "01", "over_applied": ["V1", "V2"], "off_catalog": ["X"]},
            {"case": "02", "over_applied": [], "off_catalog": []},
        ],
    }

    readings = gain.read(blocks, blocks, "asvs", spread=SPREAD)
    assert {one.gate: one.measured for one in readings}["volume"] == 1.5


def test_the_command_prints_the_table(tmp_path, capsys):
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(_gained(_baseline(), added=6, voted=False)), "utf-8")
    spread = tmp_path / "spread.json"
    spread.write_text(json.dumps(SPREAD), "utf-8")
    parser = __import__("argparse").ArgumentParser()
    COMMANDS["gain"].arguments(parser)

    args = parser.parse_args(
        [str(SEALED), str(path), "--framework", "stride", "--spread", str(spread)]
    )
    assert COMMANDS["gain"].run(args) == 0
    printed = capsys.readouterr().out
    assert "| `volume` | fail |" in printed
    assert "is not a promotion" in printed


class TestAnIncomparablePairIsRefused:
    """A gain read across two corpora measures the corpus change, not the fix."""

    @pytest.mark.parametrize(
        ("part", "value"),
        [
            ("corpus_digest", "e" * 64),
            ("artifact_version", 6),
            ("flow_identity_version", 1),
            ("mode", "analysis"),
        ],
    )
    def test_a_part_that_differs_is_refused_by_name(self, part, value):
        part_name = "corpus" if part == "corpus_digest" else part
        with pytest.raises(gain.IncomparableError, match=part_name):
            gain.read(SAME, {**SAME, part: value}, "stride", spread=SPREAD)

    def test_a_pair_nobody_can_show_comparable_is_refused(self):
        with pytest.raises(gain.IncomparableError, match="corpus None"):
            gain.read({}, {}, "stride", spread=SPREAD)

    def test_a_re_scored_artifact_compares_on_the_corpus_it_was_scored_on(self):
        """The figures follow ``scored``, so a re-score on one corpus compares."""
        moved = "e" * 64
        rescored = {**SAME, "scored": {"corpus_digest": moved}}

        gain.read(rescored, {**SAME, "corpus_digest": moved}, "stride", spread=SPREAD)
        with pytest.raises(gain.IncomparableError, match="corpus"):
            gain.read(rescored, SAME, "stride", spread=SPREAD)

    def test_the_sealed_baselines_on_two_corpora_are_refused(self):
        """The real case: two merged Baselines that sit in different groups."""
        older = json.loads(
            (
                REPO_ROOT
                / "evals/baselines/d3f1898-gpt-5.6-terra-29929443"
                / "mstarks01-92560eb2.json"
            ).read_text("utf-8")
        )
        with pytest.raises(gain.IncomparableError, match="corpus"):
            gain.read(older, _baseline(), "stride", spread=SPREAD)

    def test_the_command_refuses_and_exits_non_zero(self, tmp_path, capsys):
        path = tmp_path / "candidate.json"
        path.write_text(json.dumps({**_baseline(), "mode": "end-to-end"}), "utf-8")
        parser = __import__("argparse").ArgumentParser()
        COMMANDS["gain"].arguments(parser)

        args = parser.parse_args([str(SEALED), str(path), "--framework", "stride"])
        assert COMMANDS["gain"].run(args) == 1
        assert "refused: the two artifacts do not compare" in capsys.readouterr().err


def test_the_command_names_no_default_framework():
    parser = __import__("argparse").ArgumentParser()
    COMMANDS["gain"].arguments(parser)
    with pytest.raises(SystemExit):
        parser.parse_args([str(SEALED), str(SEALED)])
