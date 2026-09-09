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
    assert body["route"] == "editor"
    assert "/new/" in body["url"] and "value=" in body["url"]


def test_a_review_too_long_for_the_editor_link_takes_the_upload_door(
    tmp_path: Path, monkeypatch
):
    """GitHub refuses a request URL past its limit ("Whoa there!"), which a
    case 02 sitting hit on 2026-09-09. Past the limit the page opens GitHub's
    upload page at the submissions folder, with nothing in the URL, and the
    downloaded file is what the reader drops in."""
    tree = tree_for(tmp_path)
    client, _ = client_for(tree)
    record_one(client)
    monkeypatch.setattr(sitting.submit_spine, "gh_login", lambda root: "")
    monkeypatch.setattr(review_submissions, "EDITOR_URL_LIMIT", 100)

    body = client.post(
        "/api/contribute", json={"reviewer": "self", "author": "web-reviewer"}
    ).json()

    assert body["route"] == "upload"
    assert body["url"].endswith("/upload/main/evals/review/submissions")
    assert "?" not in body["url"]
    assert body["filename"].endswith(".json"), "the file still downloads"


def test_the_route_is_one_reader_for_both_doors():
    long_lines = [f"line {i:03d} " + "x" * 80 for i in range(120)]
    envelope = envelopes.Envelope.model_validate(
        {
            "envelope": envelopes.VERSION,
            "submitted_by": "ada",
            "submitted_for": "ada",
            "generated": "2026-09-09",
            "cases": {
                CASE: {
                    "own_list": long_lines,
                    "marks": {},
                    "missing": [],
                    "notes": "",
                    "opened_digests": {},
                }
            },
        }
    )
    route = review_submissions.contribution_route(envelope, "o/r")
    assert route.kind == "upload"
    assert route.url == review_submissions.upload_url("o/r")
    assert len(review_submissions.contribution_url(envelope, "o/r")) > (
        review_submissions.EDITOR_URL_LIMIT
    )


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
    assert CASE in review_submissions.unreviewed_cases(tree)


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


def test_the_read_only_document_rewrite_reads_what_document_writes(tmp_path: Path):
    """Two readers of one sentence, held together.

    ``_display_document`` rewrites the heading and the provenance line that
    :func:`evals.harness.sitting.document` writes. A change to that sentence
    would make the rewrite a silent no-op, and the read-only view would show
    the glossary's words where the page promises the reader's.
    """
    from webapp.sitting import _display_document

    case_dir = tree_for(tmp_path) / "evals" / "corpus" / CASE
    prepared = sittings.prepare(case_dir)
    text = sittings.document(prepared, ["a list"], {}, [], "", "ada", "ada", "x")

    shown = _display_document(text)

    assert shown.startswith("# Review — ")
    assert "Held through" not in shown
    assert "The independent list below was written before" in shown


def test_a_draft_that_will_not_delete_is_named_in_the_answer(
    tmp_path: Path, monkeypatch
):
    """The pull request is open either way, and the file is in a store only
    the reader can clear — so it is said, never dropped."""
    tree = tree_for(tmp_path)
    client, _ = client_for(tree)
    record_one(client)
    monkeypatch.setattr(sitting.submit_spine, "gh_login", lambda root: "ada")
    monkeypatch.setattr(
        sitting.review_submissions, "open_pull_request", lambda root, env: "url"
    )

    def stuck(drafts, login, case):
        raise sittings.DraftError(f"{case}: the store is read-only")

    monkeypatch.setattr(sitting.sittings, "discard_draft", stuck)
    response = client.post("/api/contribute", json={"reviewer": "anonymous"})

    assert response.status_code == 200
    assert response.json()["warnings"] == [f"{CASE}: {CASE}: the store is read-only"]
    assert "d.warnings" in client_script("sitting.js"), "the page says it"


def test_review_states_hand_the_page_a_state_and_a_label(tmp_path: Path):
    """The page keys on the state the rail speaks and spells no label.

    A draft holding an own list and nothing else reads as ``todo``, which is
    what the page's *Begin review* and its *remaining* count key on.
    """
    from webapp.sitting import REVIEW_LABELS

    tree = tree_for(tmp_path)
    client, _ = client_for(tree)
    client.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})

    states = client.get("/api/review-states").json()["states"]

    assert states[CASE] == {"state": "todo", "label": REVIEW_LABELS["todo"]}
    for entry in states.values():
        assert entry["label"] == REVIEW_LABELS[entry["state"]]


def test_contribution_status_needs_the_page_token(tmp_path: Path, monkeypatch):
    """Whether a `gh` login exists is a fact about the operator's machine.

    It reaches a request that read the page and no other, which is what the
    token proves. The page sends it on that one read.
    """
    tree = tree_for(tmp_path)
    client, _ = client_for(tree)
    monkeypatch.setattr(sitting.submit_spine, "gh_login", lambda root: "ada")

    bare = TestClient(client.app, base_url=str(client.base_url))
    assert bare.get("/api/contribution-status").status_code == 403
    told = client.get("/api/contribution-status")
    assert told.status_code == 200
    assert told.json() == {"mode": "direct", "author": "ada"}
    assert 'getJson("/api/contribution-status", true)' in client_script("sitting.js")


def test_a_record_whose_set_moved_is_sent_back_to_the_case(tmp_path: Path, monkeypatch):
    """The stage names the case and the remedy, not the digest.

    A reader records a case, a corpus edit lands, and they press Contribute
    from the stage without opening the case again. The digests are the ones
    the record was served at, so CI would refuse the file; the surface says
    which case to open instead, because opening it is what re-pins them.
    """
    tree = tree_for(tmp_path)
    client, session = client_for(tree)
    record_one(client)
    monkeypatch.setattr(sitting.submit_spine, "gh_login", lambda root: "ada")
    path = tree / "evals" / "corpus" / CASE / "claims" / "stride.json"
    claims = json.loads(path.read_text("utf-8"))
    claims[0]["verb"] = "replay"
    path.write_text(json.dumps(claims, indent=2) + "\n", encoding="utf-8")

    response = client.post("/api/contribute", json={"reviewer": "anonymous"})

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail.startswith(
        f"{CASE}: claims/stride.json changed since you recorded it"
    )
    assert "open the case" in detail
    assert "carries no digest" not in detail
    held = session.draft(CASE)
    assert held is not None and held.state == "finished", "nothing is lost"


def test_the_way_out_sits_above_the_file_preview():
    """The link that opens the pull request is what the reader came for.

    Show files renders the whole JSON, and a reader who then pressed
    Contribute found the link below it, off the screen. The steps and the
    result now sit above the preview, the preview scrolls inside its own
    box, and the page hides it on Contribute and scrolls the steps into view.
    """
    page = sitting._PAGE
    assert page.index('id="submit"') < page.index('id="browserSteps"')
    assert page.index('id="browserSteps"') < page.index('id="filePreview"')
    assert page.index('id="result"') < page.index('id="filePreview"')
    assert ".file-preview pre { max-height:40vh; overflow:auto; }" in page
    script = client_script("sitting.js")
    assert 'hide("filePreview");' in script.split('$("submit").addEventListener', 1)[1]
    assert "$(steps).scrollIntoView" in script
    assert page.index('id="uploadSteps"') < page.index('id="filePreview"')
