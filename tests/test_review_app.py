"""The review app writes the only human record this repository keeps.

So the tests are about what it refuses as much as what it serves: a vote on a
finding the queue never offered, a verdict outside the closed set, a second vote
from a client that has gone stale. And one property the whole design rests on —
the payload the reviewer's browser receives carries nothing about which
configuration produced the finding.

Deterministic and free of provider calls, so it gates on every PR.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from evals.harness import queue as review_queue
from evals.harness.ledger import load
from stride_service.frameworks import PACKAGES
from webapp.review import (
    QUESTIONS,
    build_session,
    create_app,
    findings_from_artifacts,
)

#: What a browser puts on every request this page makes. The vote endpoint
#: refuses without it.
SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin"}

#: Found by every run of the sweep.
STEADY = review_queue.Finding(
    case="01-payments-checkout",
    framework="stride",
    lane="spoofing",
    title="Session replay against the storefront",
    description="An attacker replays a stolen cookie.",
    element_ids=("entity:shopper",),
    verb="replay",
    quotes=("Shoppers sign in with email and password",),
)

#: An ASVS record, which rules applicability rather than an attack. Here so a
#: test can read the question the page asks about a claim that is not a threat.
REQUIREMENT = review_queue.Finding(
    case="01-payments-checkout",
    framework="asvs",
    lane="V6 Authentication",
    title="Password strength is unstated",
    description="The description never says what a password must be.",
    element_ids=("process:storefront",),
    identifier="6.2.1",
)

#: Found by two runs of the five, which is what makes it the first question.
SOMETIMES = review_queue.Finding(
    case="01-payments-checkout",
    framework="stride",
    lane="tampering",
    title="Order price rewritten in the database",
    description="An attacker edits stored prices.",
    element_ids=("store:orders-db",),
    verb="alter",
)


@pytest.fixture
def runs():
    """Five sweeps of one configuration, disagreeing about one finding.

    The run counts are computed from this rather than declared on a finding:
    that is the whole reason the app takes an artifact per sweep.
    """
    return [[STEADY, SOMETIMES], [STEADY, SOMETIMES], [STEADY], [STEADY], [STEADY]]


@pytest.fixture
def client(runs, tmp_path):
    session = build_session(
        runs,
        voter="ada",
        ledger_path=tmp_path / "votes",
        configs={"01-payments-checkout": "engine-1.2.3"},
    )
    # A loopback base URL, because the app now refuses any other Host: the
    # default ``testserver`` is exactly the shape a DNS-rebound request wears.
    # The same-origin header rides on the client for the same reason — a real
    # browser sets it on every request the page makes, and the tests that care
    # about its absence set their own.
    return (
        TestClient(
            create_app(session),
            base_url="http://127.0.0.1:8010",
            headers=SAME_ORIGIN,
        ),
        session,
    )


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


class TestReadingSeveralSweeps:
    """What the app is handed: one artifact per sweep, kept apart until merged."""

    @staticmethod
    def _sweep(tmp_path, name, claims, engine="engine-1.2.3"):
        artifact = tmp_path / name
        artifact.write_text("{}", encoding="utf-8")
        reports = tmp_path / f"{name}.reports"
        reports.mkdir()
        (reports / "01-payments-checkout.report.json").write_text(
            json.dumps({"engine_version": engine, "analyses": claims}),
            encoding="utf-8",
        )
        return artifact

    @staticmethod
    def _stride(title="A finding", category="spoofing"):
        return {
            "framework": "stride",
            "claims": [
                {
                    "id": "S-01",
                    "category": category,
                    "title": title,
                    "description": "d",
                    "affected_element_ids": ["entity:shopper"],
                    "verb": "impersonate",
                    "grounds": [{"kind": "quote", "text": "t"}],
                }
            ],
        }

    def test_each_artifact_becomes_its_own_run(self, tmp_path):
        first = self._sweep(tmp_path, "one.json", [self._stride()])
        second = self._sweep(
            tmp_path, "two.json", [self._stride("Another", "tampering")]
        )

        runs, _ = findings_from_artifacts([first, second])

        assert [len(run) for run in runs] == [1, 1]
        assert [run[0].lane for run in runs] == ["spoofing", "tampering"]

    def test_an_asvs_claim_takes_its_lane_from_its_own_field(self, tmp_path):
        """A fallback to the framework name keyed every chapter alike."""
        block = {
            "framework": "asvs",
            "claims": [
                {
                    "id": "v5.0.0-6.2.1",
                    "chapter": "authentication",
                    "title": "No password length policy is stated",
                    "description": "d",
                    "affected_element_ids": [],
                    "grounds": [{"kind": "quote", "text": "t"}],
                }
            ],
        }
        artifact = self._sweep(tmp_path, "asvs.json", [block])

        runs, _ = findings_from_artifacts([artifact])

        assert runs[0][0].lane == "authentication"

    def test_two_configurations_are_both_recorded_on_the_vote(self, tmp_path):
        """A vote must not name one sweep as though it were the whole input."""
        first = self._sweep(tmp_path, "one.json", [self._stride()], engine="engine-1")
        second = self._sweep(tmp_path, "two.json", [self._stride()], engine="engine-2")

        _, configs = findings_from_artifacts([first, second])

        assert configs["01-payments-checkout"] == "engine-1, engine-2"


def test_a_rebound_host_is_refused_before_it_can_forge_a_vote(runs, tmp_path):
    """Binding to 127.0.0.1 does not stop a page in the operator's own browser.

    DNS rebinding makes an attacker's domain resolve to 127.0.0.1, which makes
    their page same-origin with this app — no CORS preflight, and a write
    endpoint reachable from any page the reviewer happens to be visiting. The
    rebound request still carries the attacker's name in ``Host``, and that is
    what this refuses. The ledger is the supply chain of every published
    quality number, so a forged row here would be laundered into a real PR
    under the reviewer's own name.
    """
    session = build_session(runs, voter="ada", ledger_path=tmp_path / "votes")
    client = TestClient(create_app(session), base_url="http://attacker.example")
    assert client.get("/").status_code == 400
    assert (
        client.post("/api/vote", json={"fingerprint": "x", "verdict": "up"}).status_code
        == 400
    )
    assert not (tmp_path / "votes").exists(), "a refused request wrote nothing"


def test_a_cross_site_vote_is_refused(runs, tmp_path):
    """A foreign page can name a real finding, because a fingerprint is public.

    The ledger is committed, so knowing a fingerprint proves nothing about who
    is asking. Only the browser-set header does, and this is the endpoint where
    it decides whether a row enters under the reviewer's name.
    """
    session = build_session(runs, voter="ada", ledger_path=tmp_path / "votes")
    client = TestClient(create_app(session), base_url="http://127.0.0.1:8010")
    real = client.get("/api/next").json()["fingerprint"]

    for headers in ({}, {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "none"}):
        refused = client.post(
            "/api/vote", json={"fingerprint": real, "verdict": "up"}, headers=headers
        )
        assert refused.status_code == 403

    assert not (tmp_path / "votes").exists(), "a refused vote wrote nothing"


def test_neither_page_can_be_framed(client):
    """A vote pressed inside somebody else's frame arrives same-origin.

    So the header check above cannot see it, and refusing the frame is the
    control that does. ``frame-ancestors`` does not fall back to
    ``default-src``, which is why it is asserted rather than assumed.
    """
    app, _ = client
    for path in ("/", "/review"):
        csp = app.get(path).headers["Content-Security-Policy"]
        assert "frame-ancestors 'none'" in csp, f"{path} can be framed"


def test_localhost_is_allowed_beside_the_numeric_loopback(runs, tmp_path):
    """Both spellings a person types, and nothing else."""
    session = build_session(runs, voter="ada", ledger_path=tmp_path / "votes")
    client = TestClient(create_app(session), base_url="http://localhost:8010")
    assert client.get("/").status_code == 200


def test_every_package_has_its_own_reviewer_question():
    """A package missing here would be asked about in another's words.

    ``webapp/review.py`` raises at import over the same set. This states the
    property in a place a reader looks for it.
    """
    assert set(PACKAGES) <= set(QUESTIONS)
    for framework, question in QUESTIONS.items():
        assert set(question) == {"heading", "ask", "yes", "no"}, framework


def test_the_question_is_the_finding_s_own_framework(tmp_path):
    """One heading asked STRIDE's question over an ASVS record. It does not now."""
    questions = {}
    for finding in (STEADY, REQUIREMENT):
        session = build_session(
            [[finding]],
            voter="ada",
            ledger_path=tmp_path / f"votes-{finding.framework}",
        )
        app = TestClient(
            create_app(session),
            base_url="http://127.0.0.1:8010",
            headers=SAME_ORIGIN,
        )
        questions[finding.framework] = app.get("/api/next").json()["question"]

    assert questions["stride"] == QUESTIONS["stride"]
    assert questions["asvs"] == QUESTIONS["asvs"]
    assert "attack" in questions["stride"]["ask"]
    assert "requirement" in questions["asvs"]["ask"]
