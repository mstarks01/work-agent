"""Regression coverage for the simplified reviewer-facing work-review flow."""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from evals.harness import submit as submit_spine
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


def client_for(tree: Path, reviewer: str = sitting.LOCAL_SUBMITTER):
    drafts = tree.parent / "state" / "work-reviews"
    session = sitting.build_session(
        tree, reviewer, "anonymous", drafts=drafts, can_submit=False
    )
    app = TestClient(
        sitting.create_app(session),
        base_url=LOOPBACK,
        headers={
            "Sec-Fetch-Site": "same-origin",
            "X-Sitting-Token": session.token,
        },
    )
    return app, session, drafts


def record_one(app: TestClient) -> None:
    opened = app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
    assert opened.status_code == 200
    assert app.get(f"/api/part-two?case={CASE}").status_code == 200
    recorded = app.post(
        "/api/finish",
        json={"case": CASE, "marks": {}, "missing": [], "notes": "reviewed"},
    )
    assert recorded.status_code == 200


def test_page_uses_one_simple_work_review_guide():
    page = sitting._PAGE
    assert "Work Agent" not in page
    assert "<title>Work review</title>" in page
    assert "<h1>Work review</h1>" in page
    assert page.count(">Review guide</button>") == 1
    assert "<summary>Review guide</summary>" not in page
    assert "How the work review works" in page
    assert page.count('class="example"') == 6
    assert "20–30 minutes" in page
    assert ">Begin review</button>" in page
    assert ">Save and show model findings</button>" in page
    assert "previously produced by an analysis agent" in page
    assert "measure → change → re-measure" in page
    assert page.count("Thank you") == 1


def test_page_removes_redundant_source_metadata_and_rail_status_text():
    page = sitting._PAGE
    assert "block.source_kind" not in page
    assert "press.append(icon, label)" in page
    assert 'return ["✓", "complete"]' in page
    assert 'return ["…", "progressing"]' in page
    assert 'return ["!", "error"]' in page
    assert "nav, #cases { overflow-x: hidden; }" in page
    assert 'status.className = "status"' not in page


def test_github_and_attribution_wait_until_contribution_stage():
    page = sitting._PAGE
    start = page.split('<div id="empty">', 1)[1].split("</div>", 1)[0]
    assert "GitHub" not in start
    assert "reviewerAttribution" not in start
    assert "Reviewer attribution" in page
    assert page.count('id="submit">Contribute</button>') == 1
    assert "contribution issue" not in page.lower()
    assert "gh auth login" not in page
    assert "browser-only pull request" in page


def test_reset_keeps_blind_part_one_but_returns_case_to_not_reviewed(tmp_path: Path):
    tree = tree_for(tmp_path)
    app, session, _ = client_for(tree)
    record_one(app)

    reset = app.post("/api/reset", json={"case": CASE})
    assert reset.status_code == 200
    state = app.get("/api/review-states").json()["states"][CASE]
    assert state == "Not reviewed"
    part_one = app.get(f"/api/part-one?case={CASE}").json()
    assert part_one["own_list"] == OWN_LIST
    assert part_one["marks"] == {}
    assert part_one["missing"] == []
    assert part_one["notes"] == ""

    changed = app.post(
        "/api/own-list",
        json={"case": CASE, "items": ["a different answer after seeing findings"]},
    )
    assert changed.status_code == 409
    assert session.draft(CASE).own_list == OWN_LIST


def test_no_gh_contribute_falls_back_to_browser_without_mutating_review(
    tmp_path: Path, monkeypatch
):
    tree = tree_for(tmp_path)
    app, _, _ = client_for(tree)
    record_one(app)
    case_path = tree / "evals" / "corpus" / CASE / "case.json"
    before = case_path.read_bytes()

    monkeypatch.setattr(sitting.submit_spine, "gh_login", lambda root: "")
    answer = app.post("/api/contribute", json={"reviewer": "anonymous"})
    assert answer.status_code == 200
    assert answer.json() == {"mode": "browser"}
    assert case_path.read_bytes() == before


def test_browser_bundle_contains_canonical_pr_files_and_is_non_mutating(tmp_path: Path):
    tree = tree_for(tmp_path)
    app, _, _ = client_for(tree)
    record_one(app)
    local_case = tree / "evals" / "corpus" / CASE / "case.json"
    local_doc = tree / "evals" / "corpus" / CASE / "REVIEW-local-review.md"
    local_before = local_case.read_bytes()
    assert local_doc.is_file()

    answer = app.post(
        "/api/contribution-bundle",
        json={"author": "web-reviewer", "reviewer": "anonymous"},
    )
    assert answer.status_code == 200
    assert answer.headers["content-type"].startswith("application/zip")

    with zipfile.ZipFile(io.BytesIO(answer.content)) as archive:
        names = set(archive.namelist())
        expected = {
            f"evals/corpus/{CASE}/case.json",
            f"evals/corpus/{CASE}/REVIEW-web-reviewer.md",
            "tests/test_case_review.py",
            "evals/review/voters.toml",
        }
        assert expected <= names
        assert f"evals/corpus/{CASE}/REVIEW-local-review.md" not in names
        case = json.loads(archive.read(f"evals/corpus/{CASE}/case.json"))
        review = case["reviews"][-1]
        assert review["submitted_by"] == "web-reviewer"
        assert review["submitted_for"] == "anonymous"
        assert review["document"] == "REVIEW-web-reviewer.md"
        assert CASE not in archive.read("tests/test_case_review.py").decode()
        roster = archive.read("evals/review/voters.toml").decode()
        assert "[voters.web-reviewer]" in roster
        assert 'standing = "contributor"' in roster

    assert local_case.read_bytes() == local_before
    assert local_doc.is_file(), "browser packaging changed the local work review"


def test_browser_bundle_can_attribute_review_to_pr_author(tmp_path: Path):
    tree = tree_for(tmp_path)
    app, _, _ = client_for(tree)
    record_one(app)
    answer = app.post(
        "/api/contribution-bundle",
        json={"author": "web-reviewer", "reviewer": "self"},
    )
    with zipfile.ZipFile(io.BytesIO(answer.content)) as archive:
        case = json.loads(archive.read(f"evals/corpus/{CASE}/case.json"))
    assert case["reviews"][-1]["submitted_for"] == "web-reviewer"


def test_direct_contribution_rebinds_local_review_to_authenticated_pr_author(
    tmp_path: Path, monkeypatch
):
    tree = tree_for(tmp_path)
    app, _, drafts = client_for(tree)
    record_one(app)
    monkeypatch.setattr(sitting.submit_spine, "gh_login", lambda root: "ada")
    monkeypatch.setattr(
        sitting.submit_spine,
        "submission",
        lambda root, kind: submit_spine.Outcome(
            author="ada",
            url="https://github.com/mstarks01/work-agent/pull/999",
            closing="ok",
        ),
    )

    answer = app.post("/api/contribute", json={"reviewer": "self"})
    assert answer.status_code == 200
    assert answer.json()["ok"] is True
    case_dir = tree / "evals" / "corpus" / CASE
    case = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    review = case["reviews"][-1]
    assert review["submitted_by"] == "ada"
    assert review["submitted_for"] == "ada"
    assert review["document"] == "REVIEW-ada.md"
    assert (case_dir / "REVIEW-ada.md").is_file()
    assert not (case_dir / "REVIEW-local-review.md").exists()
    assert not (drafts / sitting.LOCAL_SUBMITTER / f"{CASE}.json").exists()


def test_direct_contribution_failure_restores_local_review(tmp_path: Path, monkeypatch):
    tree = tree_for(tmp_path)
    app, _, drafts = client_for(tree)
    record_one(app)
    case_dir = tree / "evals" / "corpus" / CASE
    before = (case_dir / "case.json").read_bytes()
    monkeypatch.setattr(sitting.submit_spine, "gh_login", lambda root: "ada")
    monkeypatch.setattr(
        sitting.submit_spine,
        "submission",
        lambda root, kind: submit_spine.Outcome(author="ada", error="no push"),
    )

    answer = app.post("/api/contribute", json={"reviewer": "self"})
    assert answer.status_code == 409
    assert (case_dir / "case.json").read_bytes() == before
    assert (case_dir / "REVIEW-local-review.md").is_file()
    assert not (case_dir / "REVIEW-ada.md").exists()
    assert (drafts / sitting.LOCAL_SUBMITTER / f"{CASE}.json").is_file()
