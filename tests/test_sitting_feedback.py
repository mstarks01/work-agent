"""Regression coverage for the reviewer-facing case-sitting workflow."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from webapp import sitting

CASE = "02-iot-fleet-telemetry"
LOOPBACK = "http://127.0.0.1:8020"
OWN_LIST = ["a spoofed device"]
ROSTER = """version = 1

[voters.ada]
standing = "contributor"
"""


def tree_for(tmp_path: Path) -> Path:
    source_root = Path(__file__).resolve().parents[1]
    tree = tmp_path / "clone"
    case_dir = tree / "evals" / "corpus" / CASE
    case_dir.parent.mkdir(parents=True)
    shutil.copytree(source_root / "evals" / "corpus" / CASE, case_dir)
    (tree / "evals" / "review").mkdir(parents=True)
    (tree / "evals" / "review" / "voters.toml").write_text(ROSTER, encoding="utf-8")
    (tree / "tests").mkdir()
    (tree / "tests" / "test_case_review.py").write_text(
        f'UNREVIEWED: dict[str, str] = {{\n    "{CASE}": "unread for this test",\n}}\n',
        encoding="utf-8",
    )
    return tree


def client_for(tree: Path):
    drafts = tree.parent / "state" / "sittings"
    session = sitting.build_session(tree, "ada", drafts=drafts)
    app = TestClient(
        sitting.create_app(session),
        base_url=LOOPBACK,
        headers={
            "Sec-Fetch-Site": "same-origin",
            "X-Sitting-Token": session.token,
        },
    )
    return app, session, drafts


def test_reviewer_copy_is_product_neutral_and_explains_the_loop():
    page = sitting._PAGE
    assert "Work Agent" not in page
    assert "<summary>Review guide</summary>" in page
    assert "How to submit a case sitting" not in page
    assert "How to complete a case sitting" not in page
    assert ">Begin review</button>" in page
    assert ">Save and show model findings</button>" in page
    assert "previously produced by an analysis agent" in page
    assert "Nothing is sent anywhere automatically" in page
    assert "What happens after the review?" in page
    assert "run-to-run variation" in page
    assert "measure → change → re-measure" in page
    assert page.count("Thank you") == 1


def test_part_one_warns_that_revealing_findings_locks_it():
    page = sitting._PAGE
    assert "After you save and reveal the model findings" in page
    assert "this list is\n    locked" in page
    assert "does not\n    allow this independent first pass to be changed" in page
    assert "Reset review answers" in page


def test_framework_absence_is_not_presented_as_inapplicability():
    page = sitting._PAGE
    assert "Only reference sets carried by this corpus case are shown" in page
    assert "does not\n    by itself mean that framework is inapplicable" in page


def test_reset_keeps_the_blind_list_and_returns_status_to_not_reviewed(tmp_path):
    tree = tree_for(tmp_path)
    app, session, _ = client_for(tree)
    opened = app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
    assert opened.status_code == 200
    part_two = app.get(f"/api/part-two?case={CASE}").json()
    fingerprint = part_two["marks"][0]["fingerprint"]
    finished = app.post(
        "/api/finish",
        json={
            "case": CASE,
            "marks": {fingerprint: "agree"},
            "missing": ["a missed concern"],
            "notes": "context",
        },
    )
    assert finished.status_code == 200
    assert (tree / "evals" / "corpus" / CASE / "REVIEW-ada.md").is_file()

    reset = app.post("/api/reset", json={"case": CASE})
    assert reset.status_code == 200
    assert reset.json()["reset"] is True
    assert not (tree / "evals" / "corpus" / CASE / "REVIEW-ada.md").exists()

    part_one = app.get(f"/api/part-one?case={CASE}").json()
    assert part_one["own_list"] == OWN_LIST
    assert part_one["marks"] == {}
    assert part_one["missing"] == []
    assert part_one["notes"] == ""
    assert app.get(f"/api/part-two?case={CASE}").status_code == 200
    states = app.get("/api/review-states").json()["states"]
    assert states[CASE] == "Not reviewed"

    changed = app.post(
        "/api/own-list",
        json={"case": CASE, "items": ["a different answer after seeing findings"]},
    )
    assert changed.status_code == 409
    assert session.draft(CASE).own_list == OWN_LIST


def test_reviewer_identity_is_chosen_in_the_ui_and_locked_after_start(tmp_path):
    tree = tree_for(tmp_path)
    app, session, drafts = client_for(tree)
    changed = app.post("/api/reviewer", json={"mode": "anonymous"})
    assert changed.status_code == 200
    assert changed.json()["submitted_for"] == "anonymous"
    assert session.submitted_for == "anonymous"

    # The choice is state beside the drafts, so a restarted browser keeps the
    # same provenance rather than silently reverting to the submitter.
    again = sitting.build_session(tree, "ada", drafts=drafts)
    restarted = TestClient(
        sitting.create_app(again),
        base_url=LOOPBACK,
        headers={"Sec-Fetch-Site": "same-origin", "X-Sitting-Token": again.token},
    )
    assert restarted.get("/api/rail").json()["submitted_for"] == "anonymous"

    opened = app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
    assert opened.status_code == 200
    locked = app.post("/api/reviewer", json={"mode": "self"})
    assert locked.status_code == 409
    assert "locked" in locked.json()["detail"]


def test_reset_requires_the_same_write_controls(tmp_path):
    tree = tree_for(tmp_path)
    app, session, _ = client_for(tree)
    opened = app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
    assert opened.status_code == 200

    foreign = app.post(
        "/api/reset",
        json={"case": CASE},
        headers={
            "Sec-Fetch-Site": "cross-site",
            "X-Sitting-Token": session.token,
        },
    )
    assert foreign.status_code == 403

    no_token = TestClient(
        sitting.create_app(session),
        base_url=LOOPBACK,
        headers={"Sec-Fetch-Site": "same-origin"},
    )
    assert no_token.post("/api/reset", json={"case": CASE}).status_code == 403


def test_no_github_login_no_longer_blocks_launch(monkeypatch):
    seen: dict[str, list[str]] = {}

    def capture(argv: list[str]) -> int:
        seen["args"] = argv
        return 0

    monkeypatch.setattr(sitting.submit_spine, "gh_login", lambda root: "")
    monkeypatch.setattr(sitting.base, "main", capture)

    result = sitting.main([])
    assert result == 0
    args = seen["args"]
    assert args[args.index("--submitted-by") + 1] == sitting.LOCAL_SUBMITTER
    assert args[args.index("--submitted-for") + 1] == "anonymous"


def test_submit_stage_gives_an_actionable_no_gh_path():
    page = sitting._PAGE
    assert "Open a contribution issue in GitHub" in page
    assert "gh auth login" in page
    assert "keep the review local" in page.lower()
    assert (
        "Button unavailable because this session has no authenticated gh account"
        not in page
    )
