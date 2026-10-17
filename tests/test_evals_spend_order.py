"""Every paid command refuses a bad input or output before its first model call.

A refusal after the spend costs the money and writes no artifact (#886). So
each paid command reads every input it needs, and checks every path it will
write, before the consent gate or the first provider call.
"""

from __future__ import annotations

import argparse
import json
from typing import get_args

import pytest

from evals.critic_review import __main__ as critic_review
from evals.harness import critic_replay, lane_replay, run
from evals.harness.archive import CaseKind, kind_of
from evals.harness.bundle import case_file
from tests.test_evals_lane_replay import recorded  # noqa: F401  (fixture)
from tests.test_evals_run_grounds import case  # noqa: F401  (fixture)


def _spend_point(*_args, **_kwargs):
    raise AssertionError("the command reached its first paid step")


def _swept(recorded):  # noqa: F811
    """The scripted sweep's artifact, with the commit a replay prints."""
    artifact, _ = recorded
    artifact.write_text(json.dumps({"repo_commit": {"commit": "c" * 40}}), "utf-8")
    return artifact


def _bad_ledger(tmp_path):
    votes = tmp_path / "votes"
    votes.mkdir()
    (votes / "someone.jsonl").write_text("not json\n", encoding="utf-8")
    return ["--ledger", str(votes)]


def _reports_is_a_file(tmp_path):
    (tmp_path / "artifact.reports").write_text("", encoding="utf-8")
    return ["--out", str(tmp_path / "artifact.json")]


def _reports_is_a_dangling_link(tmp_path):
    (tmp_path / "artifact.reports").symlink_to(tmp_path / "gone")
    return ["--out", str(tmp_path / "artifact.json")]


def _case_file_is_a_folder(tmp_path):
    (tmp_path / "artifact.reports" / "01-payments-checkout.drafts.json").mkdir(
        parents=True
    )
    return ["--out", str(tmp_path / "artifact.json")]


def _reports_holds_an_earlier_report(tmp_path):
    reports = tmp_path / "artifact.reports"
    reports.mkdir()
    (reports / "01-payments-checkout.report.json").write_text("{}", encoding="utf-8")
    return ["--out", str(tmp_path / "artifact.json")]


@pytest.mark.parametrize(
    "given",
    [
        lambda tmp_path: ["--roster", str(tmp_path / "missing.toml")],
        _bad_ledger,
        lambda tmp_path: ["--out", str(tmp_path / "missing" / "artifact.json")],
        lambda tmp_path: ["--out", str(tmp_path)],
        _reports_is_a_file,
        _reports_is_a_dangling_link,
        _case_file_is_a_folder,
        _reports_holds_an_earlier_report,
    ],
    ids=[
        "roster",
        "ledger",
        "out-folder",
        "out-is-a-folder",
        "reports-is-a-file",
        "reports-is-a-dangling-link",
        "case-file-is-a-folder",
        "reports-holds-an-earlier-report",
    ],
)
def test_a_sweep_refuses_a_bad_path_before_the_consent_gate(
    given, tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(run.consent, "gate", _spend_point)
    monkeypatch.setattr(run, "_run_mode", _spend_point)

    code = run.main(
        [
            "run",
            "--mode",
            "analysis",
            "--case",
            "01-payments-checkout",
            "--accept-cost",
            "unknown",
            *given(tmp_path),
        ]
    )

    assert code == 1
    assert str(tmp_path) in capsys.readouterr().err


def test_a_sweep_names_the_reports_folder_that_is_not_empty(
    tmp_path, monkeypatch, capsys
):
    """A report an earlier sweep left would read as this sweep's result."""
    monkeypatch.setattr(run.consent, "gate", _spend_point)
    monkeypatch.setattr(run, "_run_mode", _spend_point)

    code = run.main(
        [
            "run",
            "--mode",
            "analysis",
            "--case",
            "01-payments-checkout",
            "--accept-cost",
            "unknown",
            *_reports_holds_an_earlier_report(tmp_path),
        ]
    )

    assert code == 1
    assert f"{tmp_path / 'artifact.reports'}: is not empty" in capsys.readouterr().err


def test_a_critic_replay_refuses_a_bad_out_before_the_call(
    recorded,  # noqa: F811
    case,  # noqa: F811
    tmp_path,
    monkeypatch,
    capsys,
):
    artifact = _swept(recorded)
    monkeypatch.setattr(critic_replay, "node_call", _spend_point)
    missing = tmp_path / "missing"
    args = argparse.Namespace(
        artifact=artifact,
        case=case.id,
        framework="stride",
        accept_cost="unknown",
        out=missing / "rulings.json",
        raw=missing / "raw.json",
    )

    assert critic_replay.command_critic_replay(args) == 1
    assert str(missing) in capsys.readouterr().err


def test_a_lane_replay_refuses_a_bad_out_before_the_call(
    recorded,  # noqa: F811
    case,  # noqa: F811
    tmp_path,
    monkeypatch,
    capsys,
):
    artifact = _swept(recorded)
    monkeypatch.setattr(lane_replay, "node_call", _spend_point)
    missing = tmp_path / "missing"
    args = argparse.Namespace(
        artifact=artifact,
        case=case.id,
        lane="spoofing",
        framework="stride",
        accept_cost="unknown",
        append=None,
        fresh_leads=False,
        prepare_today=None,
        out=missing / "proposals.json",
    )

    assert lane_replay.command_lane_replay(args) == 1
    assert str(missing) in capsys.readouterr().err


def test_a_critic_review_refuses_a_bad_out_before_the_call(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(critic_review, "node_call", _spend_point)
    missing = tmp_path / "missing"

    code = critic_review.main(
        ["--accept-cost", "unknown", "--out", str(missing / "record.json")]
    )

    assert code == 1
    assert str(missing) in capsys.readouterr().err


@pytest.mark.parametrize("kind", get_args(CaseKind))
def test_each_case_file_the_sweep_checks_reads_back_as_its_kind(kind):
    """The pre-spend check and the archive reader name each case file alike."""
    assert kind_of(case_file("artifact.json", "a-case", kind).name) == kind
