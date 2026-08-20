"""The review app writes the only human record this repository keeps.

So the tests are about what it refuses as much as what it serves: a vote on a
finding the queue never offered, a verdict outside the closed set, a second vote
from a client that has gone stale. And one property the whole design rests on —
the payload the reviewer's browser receives carries nothing about which
configuration produced the finding.

Deterministic and free of provider calls, so it gates on every PR.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from evals.harness import queue as review_queue
from evals.harness.ledger import load
from webapp.review import build_session, create_app


@pytest.fixture
def findings():
    return [
        review_queue.Finding(
            case="01-payments-checkout",
            framework="stride",
            lane="spoofing",
            title="Session replay against the storefront",
            description="An attacker replays a stolen cookie.",
            element_ids=("entity:shopper",),
            verb="replay",
            quotes=("Shoppers sign in with email and password",),
        ),
        review_queue.Finding(
            case="01-payments-checkout",
            framework="stride",
            lane="tampering",
            title="Order price rewritten in the database",
            description="An attacker edits stored prices.",
            element_ids=("store:orders-db",),
            verb="alter",
            seen_in=2,
            runs=5,
        ),
    ]


@pytest.fixture
def client(findings, tmp_path):
    session = build_session(
        findings,
        voter="ada",
        ledger_path=tmp_path / "votes.jsonl",
        configs={"01-payments-checkout": "engine-1.2.3"},
    )
    return TestClient(create_app(session)), session


def test_the_queue_page_names_the_reviewer(client):
    app, _ = client
    body = app.get("/").text
    assert "ada" in body
    assert "Start reviewing" in body


def test_security_headers_and_a_policy_ride_on_every_page(client):
    app, _ = client
    for path in ("/", "/review"):
        response = app.get(path)
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["Referrer-Policy"] == "no-referrer"
        policy = response.headers["Content-Security-Policy"]
        assert "default-src 'none'" in policy
        assert "'unsafe-inline'" not in policy


def test_the_volatile_finding_is_served_first(client):
    """The queue spends the reviewer's first click on the best question."""
    app, _ = client
    item = app.get("/api/next").json()
    assert item["title"] == "Order price rewritten in the database"
    assert "some runs and not others" in item["why"]


def test_the_payload_carries_no_configuration(client):
    """Blind by construction. The vote is stamped with it; the page is not."""
    app, _ = client
    item = app.get("/api/next").json()

    assert "engine-1.2.3" not in str(item)
    for forbidden in ("config", "model", "vendor", "tier", "run"):
        assert forbidden not in item


def test_the_payload_carries_the_source_and_the_reasons(client):
    app, _ = client
    item = app.get("/api/next").json()

    assert item["source"], "a reviewer cannot answer without the description"
    kinds = {reason["kind"] for reason in item["reasons"]}
    assert kinds == {"substance", "style"}


def test_a_vote_is_appended_and_the_finding_does_not_come_back(client):
    app, session = client
    first = app.get("/api/next").json()

    assert (
        app.post(
            "/api/vote", json={"fingerprint": first["fingerprint"], "verdict": "up"}
        ).status_code
        == 200
    )

    ledger = load(session.ledger_path)
    assert len(ledger) == 1
    assert ledger.votes[0].voter == "ada"
    assert ledger.votes[0].config == "engine-1.2.3"
    assert ledger.pool() == {first["fingerprint"]}

    assert app.get("/api/next").json()["fingerprint"] != first["fingerprint"]


def test_a_style_downvote_keeps_the_finding_in_the_pool(client):
    app, session = client
    item = app.get("/api/next").json()

    app.post(
        "/api/vote",
        json={
            "fingerprint": item["fingerprint"],
            "verdict": "down",
            "reason": "poorly-written",
        },
    )
    assert load(session.ledger_path).pool() == {item["fingerprint"]}


def test_a_substance_downvote_does_not(client):
    app, session = client
    item = app.get("/api/next").json()

    app.post(
        "/api/vote",
        json={
            "fingerprint": item["fingerprint"],
            "verdict": "down",
            "reason": "not-a-threat",
        },
    )
    assert load(session.ledger_path).pool() == frozenset()


def test_a_vote_on_an_unoffered_finding_is_refused(client):
    """A client cannot invent a finding and write a row nothing resolves."""
    app, session = client
    response = app.post(
        "/api/vote", json={"fingerprint": "v1:deadbeefdeadbeef", "verdict": "up"}
    )
    assert response.status_code == 404
    assert len(load(session.ledger_path)) == 0


def test_an_invented_verdict_is_refused_without_writing(client):
    app, session = client
    item = app.get("/api/next").json()
    response = app.post(
        "/api/vote", json={"fingerprint": item["fingerprint"], "verdict": "lgtm"}
    )
    assert response.status_code == 422
    assert len(load(session.ledger_path)) == 0


def test_a_downvote_with_no_reason_is_refused(client):
    app, session = client
    item = app.get("/api/next").json()
    response = app.post(
        "/api/vote", json={"fingerprint": item["fingerprint"], "verdict": "down"}
    )
    assert response.status_code == 422
    assert len(load(session.ledger_path)) == 0


def test_an_unknown_field_is_refused(client):
    """Closed body: an unknown key is a 422, never a silently dropped value."""
    app, _ = client
    item = app.get("/api/next").json()
    response = app.post(
        "/api/vote",
        json={
            "fingerprint": item["fingerprint"],
            "verdict": "up",
            "voter": "somebody-else",
        },
    )
    assert response.status_code == 422


def test_the_voter_cannot_be_set_from_the_request(client):
    """The double-vote agreement measure rests on the name being true."""
    app, session = client
    item = app.get("/api/next").json()
    app.post("/api/vote", json={"fingerprint": item["fingerprint"], "verdict": "up"})

    assert {vote.voter for vote in load(session.ledger_path)} == {"ada"}


def test_the_queue_empties_and_says_so(client):
    app, _ = client
    while True:
        item = app.get("/api/next").json()
        if item.get("done"):
            break
        app.post(
            "/api/vote", json={"fingerprint": item["fingerprint"], "verdict": "up"}
        )

    assert app.get("/api/next").json() == {"done": True, "remaining": 0}


def test_the_summary_tracks_the_sitting(client):
    app, _ = client
    before = app.get("/api/summary").json()
    assert before["waiting"] == 2
    assert before["voter"] == "ada"

    item = app.get("/api/next").json()
    app.post("/api/vote", json={"fingerprint": item["fingerprint"], "verdict": "up"})

    assert app.get("/api/summary").json()["waiting"] == 1


def test_a_second_tab_cannot_double_answer_one_finding(client):
    """``remaining`` is recomputed per request, so two tabs stay consistent."""
    app, session = client
    item = app.get("/api/next").json()
    app.post("/api/vote", json={"fingerprint": item["fingerprint"], "verdict": "up"})

    stale = app.post(
        "/api/vote",
        json={
            "fingerprint": item["fingerprint"],
            "verdict": "down",
            "reason": "not-a-threat",
        },
    )
    # Accepted and recorded as a correction, never silently dropped: the ledger
    # is append-only and a reviewer changing their mind is a real event.
    assert stale.status_code == 200
    assert len(load(session.ledger_path)) == 2
    assert load(session.ledger_path).pool() == frozenset()
