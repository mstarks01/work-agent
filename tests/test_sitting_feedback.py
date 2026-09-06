"""Reviewer-facing regression coverage for the corpus review app."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from evals import review_submission as review_submissions
from evals.harness import envelope as envelopes
from evals.harness import sitting as sittings
from webapp import sitting
from webapp.page import client_script

CASE = "02-iot-fleet-telemetry"
OTHER = "03-batch-data-pipeline"
OWN_LIST = ["a spoofed device"]
LOOPBACK = "http://127.0.0.1:8020"
SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin"}
ROSTER = """version = 1

[voters.ada]
standing = "contributor"
"""


def tree_for(tmp_path: Path) -> Path:
    source_root = Path(__file__).resolve().parents[1]
    tree = tmp_path / "tree"
    for case in (CASE, OTHER):
        source = source_root / "evals" / "corpus" / case
        shutil.copytree(source, tree / "evals" / "corpus" / case)
    (tree / "evals" / "review").mkdir(parents=True)
    (tree / "evals" / "review" / "voters.toml").write_text(ROSTER, encoding="utf-8")
    (tree / "tests").mkdir()
    (tree / "tests" / "test_case_review.py").write_text(
        "UNREVIEWED: dict[str, str] = {\n"
        f'    "{CASE}": "unread for this test",\n'
        f'    "{OTHER}": "unread for this test",\n'
        "}\n",
        encoding="utf-8",
    )
    return tree


def client_for(tree: Path):
    drafts = tree.parent / "state" / "reviews"
    session = sitting.build_session(
        tree,
        sitting.LOCAL_SUBMITTER,
        "anonymous",
        drafts=drafts,
    )
    client = TestClient(
        sitting.create_app(session),
        base_url=LOOPBACK,
        headers={**SAME_ORIGIN, "X-Sitting-Token": session.token},
    )
    return client, session


def record_one(client: TestClient) -> None:
    assert (
        client.post("/api/own-list", json={"case": CASE, "items": OWN_LIST}).status_code
        == 200
    )
    part_two = client.get(f"/api/part-two?case={CASE}")
    assert part_two.status_code == 200
    assert (
        client.post(
            "/api/finish",
            json={
                "case": CASE,
                # Every finding: a record that leaves one unanswered is refused.
                "marks": {
                    target["fingerprint"]: "agree"
                    for target in part_two.json()["marks"]
                },
                "missing": ["a missed authorization edge"],
                "notes": "reviewer context",
            },
        ).status_code
        == 200
    )


def central_review(tree: Path, author: str = "ada") -> Path:
    prepared = sittings.prepare(tree / "evals" / "corpus" / CASE)
    envelope = envelopes.Envelope(
        envelope=envelopes.VERSION,
        submitted_by=author,
        submitted_for=author,
        generated="2026-09-05",
        cases={
            CASE: envelopes.CaseAnswers(
                own_list=OWN_LIST,
                marks={target.fingerprint: "agree" for target in prepared.mark_targets},
                missing=["a missed authorization edge"],
                notes="reviewer context",
                opened_digests=sittings.digests(
                    tree / "evals" / "corpus" / CASE, prepared.files
                ),
            )
        },
    )
    path = tree / review_submissions.relative_path(envelope)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(review_submissions.serialize(envelope))
    return path


def test_page_uses_plain_review_language_and_one_guide():
    page = sitting._PAGE
    assert "<title>Review</title>" in page
    assert "<h1>Review</h1>" in page
    assert "How the review works" in page
    assert "How the work review works" not in page
    assert "Start a work review" not in page
    assert "Record work review" not in page
    assert page.count('id="guide"') == 1
    assert page.count('class="example"') == 6
    assert "20–30 minute sessions" in page
    assert "one or several cases" in page
    assert "Resetting clears your Part 2 answers" in page
    assert "Resetting later" not in page
    assert "Not every framework applies to every case" in page


def test_thanks_only_appears_after_contribution_control():
    page = sitting._PAGE
    assert page.count("Thank you") == 1
    assert page.index('id="submit"') < page.index(
        "Thank you for contributing this review"
    )
    guide = page.split('id="guide"', 1)[1].split("</article>", 1)[0]
    assert "Thank you" not in guide


def test_reviewer_copy_hides_repository_transport_details():
    page = sitting._PAGE.lower()
    assert "working tree" not in page
    assert "clone" not in page
    assert "fork" not in page
    assert ".zip" not in page
    assert "show files" in page
    assert sitting._PAGE.index('id="showFiles"') < sitting._PAGE.index('id="submit"')


def test_source_header_has_no_redundant_source_kind():
    assert "block.source_kind" not in client_script("sitting.js")


def test_reset_keeps_the_independent_list_locked(tmp_path: Path):
    tree = tree_for(tmp_path)
    client, session = client_for(tree)
    record_one(client)

    reset = client.post("/api/reset", json={"case": CASE})
    assert reset.status_code == 200
    held = session.draft(CASE)
    assert held is not None
    assert held.own_list == OWN_LIST
    assert held.marks == {}
    assert held.missing == []
    assert held.notes == ""
    assert held.state == "open"

    refused = client.post(
        "/api/own-list", json={"case": CASE, "items": ["replacement"]}
    )
    assert refused.status_code == 409


def test_show_files_is_the_exact_single_json_contribution(tmp_path: Path, monkeypatch):
    tree = tree_for(tmp_path)
    client, _ = client_for(tree)
    record_one(client)
    monkeypatch.setattr(sitting.submit_spine, "gh_login", lambda root: "")

    preview = client.post(
        "/api/contribution-preview",
        json={"reviewer": "anonymous", "author": "web-reviewer"},
    )
    assert preview.status_code == 200
    body = preview.json()
    assert body["path"].startswith("evals/review/submissions/review-")
    assert body["path"].endswith(".json")
    parsed = json.loads(body["content"])
    assert parsed["submitted_by"] == "web-reviewer"
    assert parsed["submitted_for"] == "anonymous"
    assert list(parsed["cases"]) == [CASE]
    assert parsed["cases"][CASE]["own_list"] == OWN_LIST
    assert parsed["cases"][CASE]["missing"] == ["a missed authorization edge"]


def test_browser_contribution_returns_the_same_json_without_mutating_local_files(
    tmp_path: Path, monkeypatch
):
    tree = tree_for(tmp_path)
    client, _ = client_for(tree)
    record_one(client)
    monkeypatch.setattr(sitting.submit_spine, "gh_login", lambda root: "")
    case_before = (tree / "evals" / "corpus" / CASE / "case.json").read_bytes()
    list_before = (tree / "tests" / "test_case_review.py").read_bytes()

    response = client.post(
        "/api/contribute",
        json={"reviewer": "self", "author": "web-reviewer"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "browser"
    assert body["filename"].endswith(".json")
    parsed = json.loads(body["content"])
    assert parsed["submitted_for"] == "web-reviewer"
    assert (tree / "evals" / "corpus" / CASE / "case.json").read_bytes() == case_before
    assert (tree / "tests" / "test_case_review.py").read_bytes() == list_before


def test_direct_contribution_opens_one_json_pr_and_cleans_local_record(
    tmp_path: Path, monkeypatch
):
    tree = tree_for(tmp_path)
    client, session = client_for(tree)
    record_one(client)
    monkeypatch.setattr(sitting.submit_spine, "gh_login", lambda root: "ada")
    captured = {}

    def open_pr(root, envelope):
        captured["root"] = root
        captured["envelope"] = envelope
        return "https://github.com/mstarks01/work-agent/pull/999"

    monkeypatch.setattr(sitting.review_submissions, "open_pull_request", open_pr)
    response = client.post("/api/contribute", json={"reviewer": "anonymous"})
    assert response.status_code == 200
    assert response.json()["mode"] == "direct"
    assert captured["envelope"].submitted_by == "ada"
    assert captured["envelope"].submitted_for == "anonymous"
    assert list(captured["envelope"].cases) == [CASE]
    assert session.draft(CASE) is None
    assert not (tree / "evals" / "corpus" / CASE / "REVIEW-local-review.md").exists()
    assert CASE in sittings.unreviewed_cases(tree)


def test_direct_failure_preserves_local_review(tmp_path: Path, monkeypatch):
    tree = tree_for(tmp_path)
    client, session = client_for(tree)
    record_one(client)
    monkeypatch.setattr(sitting.submit_spine, "gh_login", lambda root: "ada")

    def fail(root, envelope):
        raise review_submissions.ReviewSubmissionError("push failed")

    monkeypatch.setattr(sitting.review_submissions, "open_pull_request", fail)
    response = client.post("/api/contribute", json={"reviewer": "anonymous"})
    assert response.status_code == 409
    assert "push failed" in response.json()["detail"]
    held = session.draft(CASE)
    assert held is not None and held.state == "finished"


def test_submitted_case_remains_clickable_as_read_only(tmp_path: Path):
    tree = tree_for(tmp_path)
    central_review(tree)
    client, session = client_for(tree)
    row = next(row for row in session.refresh() if row.case_id == CASE)
    assert row.state == "signed"
    assert row.pressable is False, "writes stay protected by the existing allow-list"

    read_only = client.get(f"/api/read-only?case={CASE}")
    assert read_only.status_code == 200
    body = read_only.json()
    assert body["case"] == CASE
    assert "# Review" in body["document"]
    assert "a spoofed device" in body["document"]
    assert client.get(f"/api/part-one?case={CASE}").status_code == 404
    assert "openReadOnly(row.case)" in client_script("sitting.js")


def test_every_rail_state_has_a_label():
    """The page reads the label off the server, so the table answers for
    every state the rail can carry."""
    from typing import get_args

    from webapp.sitting import REVIEW_LABELS

    assert set(REVIEW_LABELS) == set(get_args(sittings.RowState))
