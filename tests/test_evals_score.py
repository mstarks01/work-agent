"""Re-scoring a finished sweep, so a vote reaches a number without a provider.

A vote is cast *after* the sweep that produced the finding. Before ``score``
existed the answer reached the numbers only on the next sweep, and a sweep costs
a provider — so the cheapest half of the loop waited on the most expensive one.

Driven offline against the scripted sweep in
:mod:`tests.test_evals_run_grounds`, which produces real reports through the
shipped graph with no provider call.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.harness.artifact import ARTIFACT_VERSION
from evals.harness.fingerprint import components_for
from evals.harness.ledger import append, cast
from evals.harness.reference import load_case
from evals.harness.run import _write_reports, main, reports_dir, stride_threats
from stride_service.report import Report
from tests.test_evals_run_grounds import CASE_DIR, sweep


@pytest.fixture(scope="module")
def case():
    return load_case(CASE_DIR)


@pytest.fixture
def swept(monkeypatch, case, tmp_path):
    """A finished sweep on disk: its artifact, its reports and its drafts."""
    run = sweep(monkeypatch, case, None)
    out = tmp_path / "artifact.json"
    _write_reports(str(out), "analysis", run.runs)
    out.write_text(
        json.dumps(
            {
                "artifact_version": ARTIFACT_VERSION,
                "mode": "analysis",
                "cases": [case.id, "case-second"],
                "trusted": True,
                "structural_failures": [],
                "repo_commit": {"commit": "0" * 40, "clean": True},
                "corpus_digest": "0" * 64,
                # A run-level block, to prove a re-score leaves it alone.
                "grounds_aggregate": {"grounds_per_threat": 1.5},
                "provenance": run.provenance.to_json(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return out


@pytest.fixture
def roster_path(tmp_path):
    """A roster the score tests own: ada is a maintainer.

    Every series narrows the ledger by standing (#326), and the artifact's
    top-level scored keys are the primary series — maintainer votes only. So a
    test about what a vote does to the published numbers needs its voter in
    that series; ``TestStandingSplitsTheSeries`` below covers the other side.
    """
    return write_roster(tmp_path, "maintainer")


def write_roster(tmp_path, standing):
    path = tmp_path / f"voters-{standing}.toml"
    path.write_text(
        f'version = 1\n[voters.ada]\nstanding = "{standing}"\n', encoding="utf-8"
    )
    return path


def scored(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def unlisted_of(payload: dict) -> list[dict]:
    return [entry for score in payload["scores"] for entry in score["unlisted"]]


def make_one_finding_unlisted(artifact: Path, case) -> None:
    """Re-file one produced claim under an action no reference claims.

    The scripted sweep's threats all match a reference, and standings are a
    reading over the ones that do not. Changing the action is the smallest
    honest way to produce one: the identity rule reads the verb, so a claim
    filed under a different attacker action is a different finding.
    """
    path = reports_dir(artifact) / f"{case.id}.report.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    for block in raw["analyses"]:
        for claim in block["claims"]:
            claim["verb"] = "abuse-grant"
            break
        break
    path.write_text(json.dumps(raw, indent=2), encoding="utf-8")


def down_vote(entry: dict, artifact: Path, case, path: Path) -> None:
    """One person rejects one unlisted finding, for substance.

    The vote is keyed from the produced claim rather than from the artifact's
    recorded fingerprint, and the two are asserted equal — a reviewer votes on
    what the queue offers, and the score has to look the finding up under the
    same key.
    """
    report = Report.model_validate_json(
        (reports_dir(artifact) / f"{case.id}.report.json").read_text("utf-8")
    )
    claim = next(
        claim for claim in stride_threats(report) if claim.id == entry["threat_id"]
    )
    flows = {flow.id: (flow.source, flow.destination) for flow in case.model.data_flows}
    components = components_for(
        "stride",
        claim.category,
        tuple(claim.affected_element_ids),
        flows,
        verb=claim.verb,
    )
    recorded = cast(
        components,
        case=case.id,
        verdict="down",
        voter="ada",
        reason="not-a-threat",
    )
    assert recorded.fingerprint == entry["fingerprint"], (
        "the scorer and the queue must key one finding alike"
    )
    append(recorded, path)


class TestAVoteReachesTheNumbersWithoutASweep:
    """The gap this command closes, in one before-and-after."""

    def test_a_cold_ledger_leaves_every_unlisted_finding_unvoted(
        self, swept, tmp_path, case, roster_path
    ):
        make_one_finding_unlisted(swept, case)

        assert (
            main(
                [
                    "score",
                    str(swept),
                    "--ledger",
                    str(tmp_path / "none"),
                    "--roster",
                    str(roster_path),
                ]
            )
            == 0
        )

        payload = scored(swept)
        standings = {entry["standing"] for entry in unlisted_of(payload)}
        assert standings == {"unvoted"}
        assert all(
            score["metrics"]["rejected_rate"] == 0.0 for score in payload["scores"]
        )

    def test_a_rejection_moves_the_rate_on_the_next_score_not_the_next_sweep(
        self, swept, tmp_path, case, roster_path
    ):
        make_one_finding_unlisted(swept, case)
        ledger_path = tmp_path / "votes"
        main(
            [
                "score",
                str(swept),
                "--ledger",
                str(ledger_path),
                "--roster",
                str(roster_path),
            ]
        )
        first = unlisted_of(scored(swept))
        assert first, "the re-filed claim matches no reference, so it is unlisted"

        down_vote(first[0], swept, case, ledger_path)
        main(
            [
                "score",
                str(swept),
                "--ledger",
                str(ledger_path),
                "--roster",
                str(roster_path),
            ]
        )

        after = {entry["fingerprint"]: entry for entry in unlisted_of(scored(swept))}
        assert after[first[0]["fingerprint"]]["standing"] == "rejected"
        assert any(
            score["metrics"]["rejected_rate"] > 0 for score in scored(swept)["scores"]
        )

    def test_a_style_objection_reaches_the_writing_numbers(
        self, swept, tmp_path, case, roster_path
    ):
        """The other instrument a vote moves, and it needs no unmatched finding."""
        ledger_path = tmp_path / "votes"
        main(
            [
                "score",
                str(swept),
                "--ledger",
                str(ledger_path),
                "--roster",
                str(roster_path),
            ]
        )
        assert scored(swept)["writing_aggregate"]["objections"] == 0

        report = Report.model_validate_json(
            (reports_dir(swept) / f"{case.id}.report.json").read_text("utf-8")
        )
        claim = stride_threats(report)[0]
        flows = {
            flow.id: (flow.source, flow.destination) for flow in case.model.data_flows
        }
        append(
            cast(
                components_for(
                    "stride",
                    claim.category,
                    tuple(claim.affected_element_ids),
                    flows,
                    verb=claim.verb,
                ),
                case=case.id,
                verdict="down",
                voter="ada",
                reason="poorly-written",
            ),
            ledger_path,
        )
        main(
            [
                "score",
                str(swept),
                "--ledger",
                str(ledger_path),
                "--roster",
                str(roster_path),
            ]
        )

        totals = scored(swept)["writing_aggregate"]
        assert (totals["answered"], totals["objections"]) == (1, 1)
        assert totals["by_reason"] == {"poorly-written": 1}


class TestStandingSplitsTheSeries:
    """Standing selects which votes a series reads, and nothing else (#326)."""

    def test_a_contributor_moves_the_second_series_and_not_the_headline(
        self, swept, tmp_path, case
    ):
        """The whole point of a second series: it is visible and not published."""
        contributor = write_roster(tmp_path, "contributor")
        ledger_path = tmp_path / "votes"
        make_one_finding_unlisted(swept, case)
        main(
            [
                "score",
                str(swept),
                "--ledger",
                str(ledger_path),
                "--roster",
                str(contributor),
            ]
        )
        first = unlisted_of(scored(swept))

        down_vote(first[0], swept, case, ledger_path)
        main(
            [
                "score",
                str(swept),
                "--ledger",
                str(ledger_path),
                "--roster",
                str(contributor),
            ]
        )

        payload = scored(swept)
        headline = {entry["fingerprint"]: entry for entry in unlisted_of(payload)}
        assert headline[first[0]["fingerprint"]]["standing"] == "unvoted", (
            "a contributor's vote must not move the maintainer series"
        )
        second = payload["series"]["blocks"]["all"]
        moved = {
            entry["fingerprint"]: entry
            for score in second["scores"]
            for entry in score["unlisted"]
        }
        assert moved[first[0]["fingerprint"]]["standing"] == "rejected"

    def test_the_artifact_names_the_standings_each_series_reads(
        self, swept, tmp_path, roster_path
    ):
        main(
            [
                "score",
                str(swept),
                "--ledger",
                str(tmp_path / "votes"),
                "--roster",
                str(roster_path),
            ]
        )
        series = scored(swept)["series"]
        assert series["primary"] == "maintainer"
        assert series["standings"] == {
            "maintainer": ["maintainer"],
            "all": ["maintainer", "contributor"],
        }
        assert "maintainer" not in series["blocks"], (
            "the primary series' numbers are the top-level keys, never a copy"
        )

    def test_an_unrostered_voter_is_named_rather_than_dropped_in_silence(
        self, swept, tmp_path, case, capsys
    ):
        empty = tmp_path / "nobody.toml"
        empty.write_text(
            'version = 1\n[voters.someone-else]\nstanding = "maintainer"\n',
            encoding="utf-8",
        )
        ledger_path = tmp_path / "votes"
        make_one_finding_unlisted(swept, case)
        main(
            ["score", str(swept), "--ledger", str(ledger_path), "--roster", str(empty)]
        )
        down_vote(unlisted_of(scored(swept))[0], swept, case, ledger_path)
        main(
            ["score", str(swept), "--ledger", str(ledger_path), "--roster", str(empty)]
        )
        assert "no roster line and no series reads them: ada" in capsys.readouterr().out


class TestWhatARescoreTouches:
    """The readings that read the ledger, and nothing else."""

    def test_a_run_level_block_survives_untouched(self, swept, tmp_path, roster_path):
        """Grounds, coverage and provenance are facts no later vote changes."""
        main(
            [
                "score",
                str(swept),
                "--ledger",
                str(tmp_path / "none"),
                "--roster",
                str(roster_path),
            ]
        )

        payload = scored(swept)
        assert payload["grounds_aggregate"] == {"grounds_per_threat": 1.5}
        assert payload["provenance"]
        assert payload["cases"] == ["01-payments-checkout", "case-second"]

    def test_every_scored_instrument_writes_its_keys(
        self, swept, tmp_path, roster_path
    ):
        main(
            [
                "score",
                str(swept),
                "--ledger",
                str(tmp_path / "none"),
                "--roster",
                str(roster_path),
            ]
        )

        payload = scored(swept)
        for key in ("scores", "critic_yield", "writing", "writing_aggregate"):
            assert key in payload, f"{key} is a scored instrument's key"

    def test_it_can_write_beside_the_input_instead(self, swept, tmp_path):
        out = tmp_path / "rescored.json"

        main(
            [
                "score",
                str(swept),
                "--ledger",
                str(tmp_path / "n.jsonl"),
                "--out",
                str(out),
            ]
        )

        assert out.exists()
        assert "scores" not in scored(swept), "the input is left as the sweep wrote it"


class TestItRefusesRatherThanScoringHalf:
    def test_a_sweep_with_no_drafts_is_refused(
        self, swept, tmp_path, case, roster_path
    ):
        """Critic yield is the difference between the drafts and the report."""
        (reports_dir(swept) / f"{case.id}.drafts.json").unlink()

        with pytest.raises(Exception, match="Re-run the sweep"):
            main(
                [
                    "score",
                    str(swept),
                    "--ledger",
                    str(tmp_path / "none"),
                    "--roster",
                    str(roster_path),
                ]
            )

    def test_an_artifact_with_no_reports_directory_is_refused(
        self, swept, tmp_path, roster_path
    ):
        for path in reports_dir(swept).iterdir():
            path.unlink()
        reports_dir(swept).rmdir()

        with pytest.raises(Exception, match="does not exist"):
            main(
                [
                    "score",
                    str(swept),
                    "--ledger",
                    str(tmp_path / "none"),
                    "--roster",
                    str(roster_path),
                ]
            )
