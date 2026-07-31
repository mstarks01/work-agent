"""The first-run web app's offline lane.

Everything here runs credential-free against a stub runner. That is deliberate
and it is the point #28 closes on: the app's whole failure surface — the
diagnostic page, the CSRF check, the run gate, and the escape at the injection
point — is decidable without a model, so it is exercised on every pull request
rather than only when someone has Vertex configured.

The escape test is the load-bearing one. #29 made the injection point the entire
trust boundary: the template renders the submitter's own prose, so anything that
can close the ``<script>`` block turns the route's payoff page into stored XSS.
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from stride_service import StrideEngine, StubPipelineRunner
from stride_service.deployment import Deployment
from stride_service.vendors import ProviderAuthError
from tests.factories import TEST_TIER_ENV
from webapp.main import Analyses, Startup, create_app, render_report

SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin"}

# The payload that breaks a naive injection: it closes the JSON block and the
# rest of the page parses as HTML.
BREAKOUT = '</script><img src=x onerror=alert(1)>'


@pytest.fixture
def tiers():
    """The shipped tier config under the test selection; needs no credentials."""
    return Deployment.from_env(env=TEST_TIER_ENV).tiers


@pytest.fixture
def client(tiers):
    """The app wired to a stub runner — no models, no credentials, no cost."""
    startup = Startup(
        engine=StrideEngine(StubPipelineRunner()), tiers=tiers, error=None
    )
    return TestClient(create_app(startup))


@pytest.fixture
def broken_client(tiers):
    """The app as it comes up when the vendor's credentials are missing."""
    startup = Startup(
        engine=None,
        tiers=tiers,
        error=ProviderAuthError(
            "vendor 'vertex' needs STRIDE_VERTEX_PROJECT; it is unset or empty"
        ),
    )
    return TestClient(create_app(startup))


def run_to_completion(client, description: str = "A web app talks to a database.") -> str:
    """Submit, drain the event stream, and return the finished run's id."""
    started = client.post(
        "/analyze", json={"description": description}, headers=SAME_ORIGIN
    )
    assert started.status_code == 200, started.text
    run_id = started.json()["run"]
    # Draining the stream is what lets the background task make progress.
    events = client.get(f"/events/{run_id}").text
    assert "event: done" in events, events
    return run_id


def test_the_form_page_shows_the_resolved_tiers_read_only(client, tiers):
    page = client.get("/").text
    for tier, selection in tiers.tiers.items():
        assert f"<b>{tier}</b> → {selection.vendor} / {selection.model}" in page
    # Selection is config-only: the page reflects it, and offers no input that
    # could influence it. The only form control is the description.
    assert "<select" not in page
    assert page.count("<input") == 0
    assert page.count("<textarea") == 1


def test_load_example_serves_the_same_file_the_examples_run(client):
    from webapp.main import SAMPLE

    response = client.get("/example")
    assert response.status_code == 200
    assert response.text == SAMPLE.read_text(encoding="utf-8")


def test_analyze_refuses_a_request_that_is_not_same_origin(client):
    """CSRF: any page you visit could otherwise spend your vendor budget."""
    for headers in ({}, {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "none"}):
        response = client.post("/analyze", json={"description": "x"}, headers=headers)
        assert response.status_code == 403, headers


def test_a_run_streams_one_tick_per_node_then_redirects(client):
    started = client.post(
        "/analyze", json={"description": "A web app."}, headers=SAME_ORIGIN
    )
    run_id = started.json()["run"]
    events = client.get(f"/events/{run_id}").text

    ticked = [
        json.loads(line.removeprefix("data: "))["node"]
        for line in events.splitlines()
        if line.startswith("data: ") and '"node"' in line
    ]
    # Verbatim as the graph emits them — no display-name mapping.
    assert ticked == list(StubPipelineRunner.nodes)
    assert f'"url": "/report/{run_id}"' in events


def test_the_run_gate_admits_one_at_a_time():
    """The gate itself: claim, refuse, release, claim again."""
    analyses = Analyses()
    first = analyses.claim()
    assert first is not None
    assert analyses.claim() is None, "a second claim must be refused, not queued"
    analyses.release()
    second = analyses.claim()
    assert second is not None
    assert second.id != first.id


def test_the_registry_evicts_oldest_first_and_stays_bounded():
    """Untrusted prose and its report never accumulate without bound."""
    analyses = Analyses(max_runs=3)
    claimed = []
    for _ in range(5):
        run = analyses.claim()
        claimed.append(run)
        analyses.release()

    assert [analyses.get(run.id) for run in claimed[:2]] == [None, None]
    assert all(analyses.get(run.id) is not None for run in claimed[2:])


def test_a_second_submission_is_refused_while_one_is_running(tiers):
    """LLM10, at the HTTP surface: refused with a message, not held open."""
    analyses = Analyses()
    startup = Startup(
        engine=StrideEngine(StubPipelineRunner()), tiers=tiers, error=None
    )
    client = TestClient(create_app(startup, analyses))

    analyses.claim()  # stand in for a run already in flight
    response = client.post("/analyze", json={"description": "B"}, headers=SAME_ORIGIN)
    assert response.status_code == 409
    assert "already running" in response.json()["message"]


def test_the_gate_reopens_once_a_run_finishes(client):
    run_to_completion(client)
    again = client.post("/analyze", json={"description": "B"}, headers=SAME_ORIGIN)
    assert again.status_code == 200


def test_the_report_page_is_the_viewer_with_this_run_injected(client):
    from webapp.main import VIEWER

    run_id = run_to_completion(client)
    page = client.get(f"/report/{run_id}").text
    viewer = VIEWER.read_text(encoding="utf-8")

    # Only the payload block differs; the template's chrome is served as-is.
    assert page.count("<script") == viewer.count("<script")
    assert "Stub System" in page


def test_an_unknown_run_has_no_report(client):
    assert client.get("/report/nope").status_code == 404
    assert client.get("/events/nope").status_code == 404


def test_the_injection_point_escapes_every_angle_bracket():
    """The whole trust boundary, tested directly.

    A description containing ``</script>`` must not be able to close the JSON
    block. With the escape the payload lands inert, and the page still holds
    exactly the script tags the viewer shipped with.
    """
    import asyncio

    from webapp.main import VIEWER

    engine = StrideEngine(StubPipelineRunner())
    outcome = asyncio.run(engine.analyze("A web app.", system_name=BREAKOUT))
    page = render_report(outcome.report)

    payload = re.search(
        r'<script type="application/json" id="report">(.*?)</script>',
        page,
        re.DOTALL,
    )
    assert payload is not None, "the injected block must still be one script block"

    # The dangerous substring never appears literally anywhere in the page...
    assert "</script><img" not in page
    # ...but it survives as data: the JSON still decodes to the original text.
    assert json.loads(payload.group(1))["input"]["system_name"] == BREAKOUT
    # ...and the page's script-tag count is unchanged from the viewer's own.
    viewer = VIEWER.read_text(encoding="utf-8")
    assert page.count("</script>") == viewer.count("</script>")


def test_a_config_failure_renders_the_diagnostic_instead_of_the_form(broken_client):
    response = broken_client.get("/")
    assert response.status_code == 503
    page = response.text

    # Fail closed: no textarea, no Analyze button, nothing that could run a model.
    assert "<textarea" not in page
    assert "Analyze" not in page
    # The raised message, and the vendor's *whole* required set.
    assert "STRIDE_VERTEX_PROJECT" in page
    assert "STRIDE_VERTEX_LOCATION" in page
    assert "GOOGLE_APPLICATION_CREDENTIALS" in page
    # Recovery is always fix-then-restart; there is no retry affordance.
    assert "restart" in page.lower()


def test_the_diagnostic_never_prints_a_credential_value(broken_client, monkeypatch):
    monkeypatch.setenv("STRIDE_VERTEX_PROJECT", "super-secret-project-id")
    page = broken_client.get("/").text
    assert "super-secret-project-id" not in page
    assert "set" in page


def test_analyze_is_closed_while_the_config_is_broken(broken_client):
    response = broken_client.post(
        "/analyze", json={"description": "x"}, headers=SAME_ORIGIN
    )
    assert response.status_code == 503
