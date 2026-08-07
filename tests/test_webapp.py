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

from stride_service import Source, SourceLimits, StrideEngine, StubPipelineRunner
from stride_service.deployment import Deployment
from stride_service.vendors import ProviderAuthError
from tests.factories import TEST_TIER_ENV
from webapp.main import Analyses, Startup, create_app, render_report

SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin"}

# The payload that breaks a naive injection: it closes the JSON block and the
# rest of the page parses as HTML.
BREAKOUT = "</script><img src=x onerror=alert(1)>"


@pytest.fixture
def tiers():
    """The shipped tier config under the test selection; needs no credentials."""
    return Deployment.from_env(env=TEST_TIER_ENV).tiers


@pytest.fixture
def client(tiers):
    """The app wired to a stub runner — no models, no credentials, no cost."""
    startup = Startup(
        engine=StrideEngine(
            StubPipelineRunner(), limits=WEBAPP_LIMITS, deadline_seconds=TEST_DEADLINE
        ),
        tiers=tiers,
        error=None,
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


# The demo app has one textarea, so it posts one description-kind source. Its
# label is the app's, not the user's: the form asks for text, not a citation key.
WEBAPP_LIMITS = SourceLimits(max_total_bytes=100 * 1024, max_sources=10)

# Ample: no test here is exercising the deadline, and a tight one would make
# an unrelated slow run flake. The bound itself is covered in test_engine.py.
TEST_DEADLINE = 30.0


def posted(text: str) -> dict:
    """The body the app's own page sends."""
    return {
        "sources": [
            {"kind": "description", "label": "Pasted description", "text": text}
        ]
    }


def run_to_completion(client, text: str = "A web app talks to a database.") -> str:
    """Submit, drain the event stream, and return the finished run's id."""
    started = client.post("/analyze", json=posted(text), headers=SAME_ORIGIN)
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
        response = client.post("/analyze", json=posted("x"), headers=headers)
        assert response.status_code == 403, headers


def test_a_run_streams_one_tick_per_node_then_redirects(client):
    started = client.post("/analyze", json=posted("A web app."), headers=SAME_ORIGIN)
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
        engine=StrideEngine(
            StubPipelineRunner(), limits=WEBAPP_LIMITS, deadline_seconds=TEST_DEADLINE
        ),
        tiers=tiers,
        error=None,
    )
    client = TestClient(create_app(startup, analyses))

    analyses.claim()  # stand in for a run already in flight
    response = client.post("/analyze", json=posted("B"), headers=SAME_ORIGIN)
    assert response.status_code == 409
    assert "already running" in response.json()["message"]


def test_the_gate_reopens_once_a_run_finishes(client):
    run_to_completion(client)
    again = client.post("/analyze", json=posted("B"), headers=SAME_ORIGIN)
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

    A submitted system name containing ``</script>`` must not close the JSON
    block. With the escape the payload lands inert, and the page still holds
    exactly the script tags the viewer shipped with.
    """
    import asyncio

    from webapp.main import VIEWER

    engine = StrideEngine(
        StubPipelineRunner(), limits=WEBAPP_LIMITS, deadline_seconds=TEST_DEADLINE
    )
    outcome = asyncio.run(
        engine.analyze([Source.description("A web app.")], system_name=BREAKOUT)
    )
    page = render_report(outcome.report).html

    payload = re.search(
        r'<script type="application/json" id="report"[^>]*>(.*?)</script>',
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
    response = broken_client.post("/analyze", json=posted("x"), headers=SAME_ORIGIN)
    assert response.status_code == 503


# --- render safety (#78) ----------------------------------------------------
#
# Two independent controls, and the tests below hold them apart because they
# fail differently. The *primary* control is that the viewer never interpolates
# a value into an HTML string — that is decision 1, and no offline test can
# execute the DOM, so it is guarded by a lint over the template. The *secondary*
# control is the server-side escape at the injection point, which is testable
# directly. The strict nonce CSP is defence in depth behind both.

# What a model-authored field looks like when the submitter engineered it.
MARKUP_PAYLOAD = "<img src=x onerror=alert(1)>"

# The sinks that take a string and parse it as markup. None may appear in the
# viewer's own code: decision 1 is that untrusted text reaches the DOM as
# textContent or as constructed nodes, so that forgetting shows junk on screen
# instead of executing script.
HTML_STRING_SINKS = ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write")


def viewer_javascript() -> str:
    """The viewer's behaviour block, with its comments stripped.

    Comments are removed so the lint reads what runs. The prose in this file
    names ``innerHTML`` precisely because it is explaining why there isn't one,
    and a substring check over the raw template would fail on its own docs.
    """
    from webapp.main import VIEWER

    source = VIEWER.read_text(encoding="utf-8")
    body = source.split('<script nonce="__CSP_NONCE__">')[-1].split("</script>")[0]
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)
    return "\n".join(
        line for line in body.splitlines() if not line.lstrip().startswith("//")
    )


@pytest.mark.parametrize("sink", HTML_STRING_SINKS)
def test_the_viewer_has_no_html_string_sink(sink):
    """Decision 1, as a lint: there is no sink left to forget to escape."""
    assert sink not in viewer_javascript(), (
        f"webapp/report_view.html uses {sink}. Every value in the report is "
        f"untrusted — build DOM nodes or set textContent instead."
    )


def test_the_viewer_reads_every_service_mark_the_report_carries():
    """A mark computed and never rendered is half a check.

    Written after exactly that happened: ``unresolved_mentions`` and
    ``missing_mitigations`` shipped on the report — validated, threaded through
    graph state, in the payload — while the viewer named only
    ``unverified_grounds``, the mark they were both modelled on. The service
    had done the work and the reader was never told.

    The field list is *derived* rather than written down, so the next mark is
    caught by existing: a service mark is a top-level list whose element type
    carries a ``threat_id``, which is what makes it a note about a finding
    rather than part of one.
    """
    from typing import get_args, get_origin

    from stride_service.report import StrideReport

    marks = []
    for name, field in StrideReport.model_fields.items():
        annotation = field.annotation
        if get_origin(annotation) is not list:
            continue
        (item,) = get_args(annotation)
        if hasattr(item, "model_fields") and "threat_id" in item.model_fields:
            marks.append(name)

    assert marks, "no service marks found on StrideReport — has the shape changed?"
    javascript = viewer_javascript()
    unread = [name for name in marks if f"R.{name}" not in javascript]
    assert not unread, (
        f"the report carries {unread} and the viewer never reads them. A mark"
        " the service computes and nothing shows is a check that stops one step"
        " short of the person it was for."
    )


def test_the_viewer_carries_no_escape_helper():
    """The corollary. An escape helper would mean a sink somewhere to use it on.

    #78's evidence for decision 1 was that "remember to call esc()" had already
    failed once, unnoticed, in the element table's attribute column. The helper
    going away is what makes that class of bug unwritable rather than merely
    fixed.
    """
    assert "esc(" not in viewer_javascript()


def model_with_markup_everywhere():
    """A System Model whose every free-text field carries the payload.

    These are exactly the ``str(max_length=200)`` fields extraction copies out
    of the submitter's prose — the ones the element table renders, and the ones
    the live bug (#86) put into ``innerHTML`` unescaped.
    """
    from tests.factories import valid_model

    model = valid_model()
    return model.model_copy(
        update={
            "processes": [
                model.processes[0].model_copy(
                    update={"technology": MARKUP_PAYLOAD, "name": MARKUP_PAYLOAD}
                )
            ],
            "data_stores": [
                model.data_stores[0].model_copy(
                    update={
                        "technology": MARKUP_PAYLOAD,
                        "data_classification": MARKUP_PAYLOAD,
                        "encryption_at_rest": MARKUP_PAYLOAD,
                        "assets": [MARKUP_PAYLOAD],
                    }
                )
            ],
            "data_flows": [
                flow.model_copy(
                    update={
                        "protocol": MARKUP_PAYLOAD,
                        "authentication": MARKUP_PAYLOAD,
                        "encryption_in_transit": MARKUP_PAYLOAD,
                    }
                )
                for flow in model.data_flows
            ],
        }
    )


def report_with_markup_everywhere():
    """A complete report carrying the payload in every free-text field.

    Grounds included: a quote's ``text`` is submitter prose copied verbatim by
    rule, and its ``source_label`` is a caller-chosen string, so the grounds
    rail is now the newest place on the page where untrusted text lands.
    """
    from stride_service.report import Ground, Mitigation, Severity, Verdict
    from tests.factories import sample_report, sample_threat

    threat = sample_threat(
        title=MARKUP_PAYLOAD,
        description=MARKUP_PAYLOAD,
        grounds=[
            Ground(kind="quote", text=MARKUP_PAYLOAD, source_label=MARKUP_PAYLOAD),
            Ground(
                kind="unknown-attribute",
                element_id=MARKUP_PAYLOAD,
                attribute=MARKUP_PAYLOAD,
            ),
        ],
        severity=Severity(
            likelihood="medium", impact="high", justification=MARKUP_PAYLOAD
        ),
        mitigations=[Mitigation(summary=MARKUP_PAYLOAD)],
        verdict=Verdict(status="rejected", reason=MARKUP_PAYLOAD),
    )
    report = sample_report(threats=[], rejected_threats=[threat])
    return report.model_copy(update={"system_model": model_with_markup_everywhere()})


def test_no_free_text_field_reaches_the_page_as_markup():
    """#86's regression test, widened to every field that can carry prose.

    The payload must not appear as markup anywhere in the served bytes, and it
    must still survive as data — a report that renders the threat away is not a
    fix. This covers the server-side half; the client-side half is the sink
    lint above, since no offline test here can run the DOM.
    """
    page = render_report(report_with_markup_everywhere()).html

    assert MARKUP_PAYLOAD not in page
    assert "<img" not in page

    payload = re.search(
        r'<script type="application/json" id="report"[^>]*>(.*?)</script>',
        page,
        re.DOTALL,
    )
    decoded = json.loads(payload.group(1))
    assert decoded["rejected_threats"][0]["title"] == MARKUP_PAYLOAD
    assert decoded["rejected_threats"][0]["grounds"][0]["text"] == MARKUP_PAYLOAD
    assert decoded["system_model"]["processes"][0]["technology"] == MARKUP_PAYLOAD


def test_the_report_page_ships_a_strict_nonce_csp(client):
    """Decision 2, including the part that is easy to get subtly wrong.

    A nonce that authorises the policy but is missing from one of the page's
    own blocks would silently stop that block running, so the check walks every
    inline block rather than trusting the header alone.
    """
    run_id = run_to_completion(client)
    response = client.get(f"/report/{run_id}")
    csp = response.headers["Content-Security-Policy"]

    assert "default-src 'none'" in csp
    assert "base-uri 'none'" in csp
    assert "form-action 'none'" in csp
    # Styles are nonced too — no 'unsafe-inline' anywhere, for either directive.
    assert "unsafe-inline" not in csp

    nonce = re.search(r"script-src 'nonce-([^']+)'", csp).group(1)
    assert f"style-src 'nonce-{nonce}'" in csp

    blocks = re.findall(r"<(script|style)([^>]*)>", response.text)
    assert blocks, "the viewer should carry inline blocks for the nonce to cover"
    for tag, attributes in blocks:
        assert f'nonce="{nonce}"' in attributes, (
            f"a <{tag}> block carries no nonce, so the CSP would block it"
        )
    # The placeholder is fully substituted; none of it survives into the page.
    assert "__CSP_NONCE__" not in response.text


def test_every_render_gets_a_fresh_nonce(client):
    """A nonce reused across responses is not a nonce."""
    run_id = run_to_completion(client)
    first = client.get(f"/report/{run_id}").headers["Content-Security-Policy"]
    second = client.get(f"/report/{run_id}").headers["Content-Security-Policy"]
    assert first != second


def test_a_submitted_nonce_placeholder_is_not_substituted():
    """The nonce is stamped before the payload lands, so this stays inert data."""
    from tests.factories import sample_report, sample_threat

    report = sample_report(threats=[sample_threat(title="__CSP_NONCE__")])
    rendered = render_report(report)

    nonce = re.search(r"script-src 'nonce-([^']+)'", rendered.csp).group(1)
    payload = re.search(
        r'<script type="application/json" id="report"[^>]*>(.*?)</script>',
        rendered.html,
        re.DOTALL,
    )
    assert json.loads(payload.group(1))["threats"][0]["title"] == "__CSP_NONCE__"
    assert nonce not in payload.group(1)


def test_the_viewer_has_no_inline_style_attribute():
    """The CSP grants no 'unsafe-inline' for styles, so a style="" would break.

    Generated colours go through ``element.style.*``, a CSSOM write the policy
    does not govern; static ones are classes.
    """
    from webapp.main import VIEWER

    markup = VIEWER.read_text(encoding="utf-8")
    markup = re.sub(r"/\*.*?\*/", "", markup, flags=re.DOTALL)
    markup = re.sub(r"<!--.*?-->", "", markup, flags=re.DOTALL)
    assert "style=" not in markup
