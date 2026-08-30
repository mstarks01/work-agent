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
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from typing import get_args

import pytest
from fastapi.testclient import TestClient

from analysis_service import (
    Engine,
    FrameworkName,
    Source,
    SourceLimits,
    StubPipelineRunner,
)
from analysis_service.deployment import Deployment
from analysis_service.vendors import ProviderAuthError
from tests.factories import TEST_TIER_ENV, sample_selection
from webapp.main import Analyses, Startup, create_app, render_report

SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin"}

#: The app refuses any other Host, so the client has to wear one it accepts.
#: ``testserver``, the TestClient default, is the shape a DNS-rebound request
#: arrives in — see ``webapp.page.LOOPBACK_HOSTS``.
LOOPBACK = "http://127.0.0.1:8000"

# The payload that breaks a naive injection: it closes the JSON block and the
# rest of the page parses as HTML.
BREAKOUT = "</script><img src=x onerror=alert(1)>"


@pytest.fixture
def tiers():
    """The shipped tier config under the test selection; needs no credentials."""
    return Deployment.from_env(env=TEST_TIER_ENV).tiers


# What the offline lane's install "carries", and so what its picker offers and
# its allow-list admits. Both packages, because one of them declares a required
# option and the other declares none — which is the whole of what the picker
# has to tell apart.
CARRIED: tuple[FrameworkName, ...] = ("stride", "asvs")

# The complete option set for each carried package, for a test that needs a
# submission to be accepted rather than to be about the options. Keyed by
# framework and not branched on: a package added to CARRIED without an entry
# here raises rather than quietly submitting without its options.
COMPLETE_OPTIONS: dict[FrameworkName, dict] = {"stride": {}, "asvs": {"level": 2}}


def stub_engine_for(selection):
    """Build a stub engine for one selection, as ``build_startup``'s factory does.

    The real factory is ``partial(Engine.from_deployment, deployment)``,
    which composes a graph and binds credentials. This is the same signature
    over a stub runner, so the whole selection path — allow-list, options
    validation, one report block per picked framework — runs with no model and
    no cost. The engine's own constructor is what refuses a missing option, so
    that refusal is exercised here rather than mocked out.
    """
    return Engine(
        StubPipelineRunner(),
        limits=WEBAPP_LIMITS,
        deadline_seconds=TEST_DEADLINE,
        frameworks=selection,
    )


@pytest.fixture
def client(tiers):
    """The app wired to a stub runner — no models, no credentials, no cost."""
    startup = Startup(
        engine_for=stub_engine_for,
        frameworks=CARRIED,
        tiers=tiers,
        error=None,
    )
    return TestClient(create_app(startup), base_url=LOOPBACK)


@pytest.fixture
def broken_client(tiers):
    """The app as it comes up when the vendor's credentials are missing."""
    startup = Startup(
        engine_for=None,
        frameworks=(),
        tiers=tiers,
        error=ProviderAuthError(
            "vendor 'vertex' needs ANALYSIS_VERTEX_PROJECT; it is unset or empty"
        ),
    )
    return TestClient(create_app(startup), base_url=LOOPBACK)


# The demo app has one textarea, so it posts one description-kind source. Its
# label is the app's, not the user's: the form asks for text, not a citation key.
WEBAPP_LIMITS = SourceLimits(max_total_bytes=100 * 1024, max_sources=10)

# Ample: no test here is exercising the deadline, and a tight one would make
# an unrelated slow run flake. The bound itself is covered in test_engine.py.
TEST_DEADLINE = 30.0


def posted(text: str, frameworks: Sequence[FrameworkName] = CARRIED) -> dict:
    """The body the app's own page sends: the textarea, and the ticked boxes.

    Every carried framework by default, which is the state the form ships in.
    Each one carries the complete options for its package, so a body from here
    is one the allow-list and the options models both accept.
    """
    return {
        "sources": [
            {"kind": "description", "label": "Pasted description", "text": text}
        ],
        "frameworks": [
            {"name": name, "options": COMPLETE_OPTIONS[name]} for name in frameworks
        ],
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


def test_no_form_control_can_influence_which_model_runs(client, tiers):
    """The picker chooses what is analysed; nothing chooses what analyses it.

    Model identity stays config-only (A01, LLM10). The page prints the resolved
    tier, vendor and model, so this cannot assert those strings are absent —
    it asserts that no *control* carries one, which is the property that would
    actually be lost if a model field were ever added.
    """
    controls = re.findall(r"<(?:input|select|textarea)\b[^>]*>", client.get("/").text)
    named = {tier for tier in tiers.tiers}
    named |= {selection.vendor for selection in tiers.tiers.values()}
    named |= {selection.model for selection in tiers.tiers.values()}

    assert controls, "the form page should carry controls"
    for control in controls:
        assert not any(name in control for name in named), control


def test_the_only_controls_are_the_picker_and_the_description(client):
    """One textarea, one checkbox per carried framework, one select per option."""
    page = client.get("/").text

    assert page.count("<textarea") == 1
    assert page.count('type="checkbox"') == len(CARRIED)
    # ASVS declares a level and STRIDE declares nothing, so exactly one select.
    assert page.count("<select") == 1
    assert 'data-option="level"' in page


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


def report_of(client, run_id: str) -> dict:
    """The report the viewer was handed, read back out of its JSON block."""
    page = client.get(f"/report/{run_id}").text
    block = re.search(
        r'<script type="application/json" id="report"[^>]*>(.*?)</script>',
        page,
        re.DOTALL,
    )
    assert block is not None, "the report page carried no injected JSON block"
    return json.loads(block.group(1))


def frameworks_of(client, body: dict) -> list[str]:
    """Submit ``body``, run it out, and return the report's blocks in order."""
    started = client.post("/analyze", json=body, headers=SAME_ORIGIN)
    assert started.status_code == 200, started.text
    run_id = started.json()["run"]
    assert "event: done" in client.get(f"/events/{run_id}").text
    return [block["framework"] for block in report_of(client, run_id)["analyses"]]


def test_the_offline_lanes_tables_cover_the_whole_registry():
    """``CARRIED`` and ``COMPLETE_OPTIONS`` are checked against ``PACKAGES``.

    Both are tables keyed by framework, and a table nobody compares to the
    registry fails as quietly as the branch it replaced: a package registered
    later would leave this lane testing the picker against a set that no longer
    matches what an install can carry. Comparing here is what makes that a
    failure at the next run rather than a gap nobody sees.
    """
    from analysis_service.frameworks import PACKAGES

    assert set(CARRIED) == set(PACKAGES), (
        "PACKAGES has changed. Widen CARRIED so this lane still exercises the"
        " picker against everything an install can carry."
    )
    assert set(COMPLETE_OPTIONS) == set(PACKAGES), (
        "PACKAGES has changed. Give the new package a complete option set here,"
        " read off its own options model."
    )


def test_every_complete_option_set_really_is_complete():
    """Each entry satisfies its own package's options model, not this lane's idea of it.

    Validated against the model rather than eyeballed, so an entry that stops
    being complete — a package that adds a required field — fails here instead
    of turning every submission in this file into a 400.
    """
    from analysis_service.frameworks import package_for

    for name, options in COMPLETE_OPTIONS.items():
        package_for(name).options.model_validate(options)


def test_the_picker_offers_every_framework_this_install_carries(client):
    """The rows come from the carried list, so a package added gets a row."""
    page = client.get("/").text
    for name in CARRIED:
        assert f'name="framework" value="{name}"' in page


def test_a_package_that_declares_an_option_gets_a_control_for_it(client):
    """The choices offered are the choices its own options model declares.

    Read off the package rather than spelled here, so this asks the question of
    whichever package declares a closed-set option rather than of one by name.
    """
    from analysis_service.frameworks import package_for
    from webapp.main import _option_fields

    page = client.get("/").text
    for name in CARRIED:
        for field, annotation in _option_fields(name):
            assert f'data-framework="{name}" data-option="{field}"' in page
            for choice in get_args(annotation):
                assert f'<option value="{json.dumps(choice)}">' in page
        assert package_for(name).options is not None


def test_picking_both_frameworks_returns_one_report_with_both_blocks(client):
    """The whole point: two frameworks, one submission, one shared model."""
    assert frameworks_of(client, posted("A web app talks to a database.")) == [
        "stride",
        "asvs",
    ]


@pytest.mark.parametrize("name", CARRIED)
def test_picking_one_framework_runs_only_that_one(client, name):
    """Unticking a box costs its nodes, so it must actually leave it out."""
    body = posted("A web app talks to a database.", frameworks=[name])
    assert frameworks_of(client, body) == [name]


def test_the_block_order_is_the_installs_order_not_the_pages(client):
    """A reordered post is normalised, not honoured: block order is config's."""
    body = posted("A web app talks to a database.", frameworks=reversed(CARRIED))
    assert frameworks_of(client, body) == list(CARRIED)


def test_a_framework_named_twice_runs_once(client):
    """Otherwise a page could double its own cost and break the envelope."""
    body = posted("A web app talks to a database.", frameworks=["asvs", "asvs"])
    assert frameworks_of(client, body) == ["asvs"]


def test_a_framework_this_install_does_not_carry_is_refused(tiers):
    """The allow-list's real job, which the closed type does not do for it.

    A name this repo cannot spell is refused by ``FrameworkName`` before this
    app sees it. A name it *can* spell, on an install that does not carry it,
    reaches the allow-list — and only the allow-list refuses it. So this drives
    an install narrowed to one package and posts the other.
    """
    narrow, withheld = CARRIED[0], CARRIED[1]
    client = TestClient(
        base_url=LOOPBACK,
        app=create_app(
            Startup(
                engine_for=stub_engine_for,
                frameworks=(narrow,),
                tiers=tiers,
                error=None,
            )
        ),
    )

    def submit(*names):
        return client.post(
            "/analyze",
            json=posted("A web app talks to a database.", frameworks=names),
            headers=SAME_ORIGIN,
        )

    assert f'value="{withheld}"' not in client.get("/").text, (
        "the picker offered a framework this install cannot run"
    )
    assert submit(withheld).status_code == 400

    # The decisive one. Dropping the check rather than refusing on it leaves
    # this a 200 that quietly runs `narrow` alone — a submission answered by a
    # selection nobody made, which is the failure the allow-list exists to stop.
    assert submit(narrow, withheld).status_code == 400


@pytest.mark.parametrize(
    "frameworks",
    [
        pytest.param([{"name": "stride"}, {"name": "nope"}], id="not-a-framework"),
        pytest.param([], id="nothing-ticked"),
        pytest.param("stride", id="not-a-list-of-selections"),
        pytest.param([{"level": 2}], id="no-name"),
    ],
)
def test_a_submission_this_install_cannot_serve_is_refused(client, frameworks):
    """Deny by default (A01): the allow-list runs before an engine exists."""
    body = posted("A web app talks to a database.")
    body["frameworks"] = frameworks

    refused = client.post("/analyze", json=body, headers=SAME_ORIGIN)

    assert refused.status_code == 400, refused.text


def test_a_submission_missing_a_required_option_is_refused_by_name(client):
    """The package's own options model refuses it, and says which field.

    Not the app's check and not a name spelled here: the engine's constructor
    validates against the package's model, so the message names the field that
    package declares.
    """
    body = posted("A web app talks to a database.")
    body["frameworks"] = [{"name": "asvs", "options": {}}]

    refused = client.post("/analyze", json=body, headers=SAME_ORIGIN)

    assert refused.status_code == 400
    assert "asvs" in refused.json()["message"]
    assert "level" in refused.json()["message"]


def test_an_option_outside_its_declared_set_is_refused(client):
    """A page can post any number; the closed set is what decides."""
    body = posted("A web app talks to a database.")
    body["frameworks"] = [{"name": "asvs", "options": {"level": 9}}]

    refused = client.post("/analyze", json=body, headers=SAME_ORIGIN)

    assert refused.status_code == 400


def test_a_refused_selection_leaves_the_gate_open_for_the_next_submitter(client):
    """The engine is built before the gate is claimed, so a 400 locks nobody out."""
    body = posted("A web app talks to a database.")
    body["frameworks"] = [{"name": "asvs", "options": {}}]
    assert client.post("/analyze", json=body, headers=SAME_ORIGIN).status_code == 400

    run_to_completion(client)


def test_an_option_that_is_not_a_closed_set_fails_loudly(client):
    """A control that cannot be filled in is a defect, not a blank field."""
    from webapp.main import _option_control

    with pytest.raises(RuntimeError, match="closed set"):
        _option_control("stride", "budget", int)


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
        engine_for=stub_engine_for,
        frameworks=CARRIED,
        tiers=tiers,
        error=None,
    )
    client = TestClient(create_app(startup, analyses), base_url=LOOPBACK)

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

    engine = Engine(
        StubPipelineRunner(),
        limits=WEBAPP_LIMITS,
        deadline_seconds=TEST_DEADLINE,
        frameworks=sample_selection(),
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
    assert "ANALYSIS_VERTEX_PROJECT" in page
    assert "ANALYSIS_VERTEX_LOCATION" in page
    assert "GOOGLE_APPLICATION_CREDENTIALS" in page
    # Recovery is always fix-then-restart; there is no retry affordance.
    assert "restart" in page.lower()


def test_the_diagnostic_never_prints_a_credential_value(broken_client, monkeypatch):
    monkeypatch.setenv("ANALYSIS_VERTEX_PROJECT", "super-secret-project-id")
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
    caught by existing: a service mark is a list on an analysis block whose
    element type carries a ``claim_id``, which is what makes it a note about a
    finding rather than part of one.

    **Derived from the block, and from each package's own block type.** The
    marks moved off the envelope in the cutover — a mark annotates one
    framework's claims — and a package may add its own beside the neutral three,
    as STRIDE does with ``missing_mitigations``. Walking every registered
    block type is what keeps a second framework's mark covered by this test
    existing rather than by somebody remembering to extend it.
    """
    from typing import get_args, get_origin

    from analysis_service.frameworks import SCHEMAS
    from analysis_service.report import FrameworkAnalysis

    block_types = {FrameworkAnalysis, *(schemas.block for schemas in SCHEMAS.values())}
    marks = set()
    for block_type in block_types:
        for name, field in block_type.model_fields.items():
            annotation = field.annotation
            if get_origin(annotation) is not list:
                continue
            (item,) = get_args(annotation)
            if hasattr(item, "model_fields") and "claim_id" in item.model_fields:
                marks.add(name)

    assert marks, "no service marks found on the analysis block — shape changed?"
    javascript = viewer_javascript()
    unread = sorted(name for name in marks if f"block.{name}" not in javascript)
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


# Enough DOM to run the viewer's helper block under ``node``: a node carries a
# tag and a list of children, ``textContent`` reads back the text a person sees,
# and ``codes`` reads back the identifiers that reached a `code` element.
VIEWER_DOM_SHIM = """
class Node {
  constructor(tag) { this.tag = tag; this.children = []; }
  append(...kids) { kids.forEach(k => this.children.push(k)); }
  set textContent(t) { this.children = [t]; }
  get textContent() {
    return this.children.map(k => (k instanceof Node ? k.textContent : k)).join("");
  }
  get codes() {
    return this.children.flatMap(k => k instanceof Node
      ? (k.tag === "code" ? [k.textContent] : k.codes) : []);
  }
}
globalThis.document = {
  getElementById: () => Object.assign(new Node("script"), { children: ["{}"] }),
  createElement: (tag) => new Node(tag),
  createDocumentFragment: () => new Node("#fragment"),
  createTextNode: (t) => Object.assign(new Node("#text"), { children: [t] }),
};
"""

# The viewer's helpers run before this constant, and nothing before it touches
# the page. Splitting here is what lets the test drive the real source rather
# than a copy of it.
FIRST_VIEWER_CONSTANT = "const GROUND_KIND"


def run_viewer_helpers(script: str):
    """The viewer's helper block plus ``script``, run under ``node``.

    Skipped where ``node`` is absent, like the form page's parse check: the
    suite is worth keeping free of a JavaScript runtime.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node on PATH to run the viewer's helpers")

    javascript = viewer_javascript()
    assert FIRST_VIEWER_CONSTANT in javascript, (
        f"{FIRST_VIEWER_CONSTANT} is gone, so this test no longer knows where"
        " the viewer's helper block ends."
    )
    helpers = javascript.split(FIRST_VIEWER_CONSTANT)[0]
    with tempfile.NamedTemporaryFile("w", suffix=".mjs") as handle:
        handle.write(VIEWER_DOM_SHIM + helpers + script)
        handle.flush()
        run = subprocess.run(
            [node, handle.name], capture_output=True, text=True, check=False
        )
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_the_viewer_renders_a_backticked_identifier_as_code():
    """A model writes `process:web-api`, and a reader must not see the ticks.

    The prompt hands identifiers to the lane agent in backticks and the agent
    writes them back the same way, so every description, rationale and
    mitigation carries them. The page showed them as characters until `prose`
    turned each span into a `code` element.
    """
    written = "The `encryption_in_transit` state of `flow:a-to-b:sync` is unknown."
    rendered = run_viewer_helpers(
        f'const n = proseEl("div", null, {json.dumps(written)});'
        "console.log(JSON.stringify({ text: n.textContent, codes: n.codes }));"
    )

    assert "`" not in rendered["text"]
    assert rendered["text"] == (
        "The encryption_in_transit state of flow:a-to-b:sync is unknown."
    )
    assert rendered["codes"] == ["encryption_in_transit", "flow:a-to-b:sync"]


def test_the_viewer_leaves_an_unpaired_backtick_alone():
    """The half-written case renders as itself rather than as a swallowed line.

    A model that opens a span and never closes it is writing a stray character,
    not markup, and a reader is better served by the character than by the rest
    of the sentence disappearing into a `code` element.
    """
    written = "The cost is 3 ` per unit"
    rendered = run_viewer_helpers(
        f'const n = proseEl("div", null, {json.dumps(written)});'
        "console.log(JSON.stringify({ text: n.textContent, codes: n.codes }));"
    )

    assert rendered["text"] == written
    assert rendered["codes"] == []


def test_the_viewer_renders_a_span_as_text_not_as_markup():
    """`prose` is new text handling, so it gets #86's payload too.

    A code span is still built as a `code` element with its content set as
    text. The payload must come back as the characters a reader sees, which is
    what says it never became markup.
    """
    rendered = run_viewer_helpers(
        f'const n = proseEl("div", null, {json.dumps(f"a `{MARKUP_PAYLOAD}` span")});'
        "console.log(JSON.stringify({ text: n.textContent, codes: n.codes }));"
    )

    assert rendered["codes"] == [MARKUP_PAYLOAD]


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
    from analysis_service.report import Ground, Mitigation, Severity, Verdict
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
        verdict=Verdict(
            status="rejected",
            reason=MARKUP_PAYLOAD,
            rejected_because="evidence",
        ),
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
    assert decoded["analyses"][0]["rejected_claims"][0]["title"] == MARKUP_PAYLOAD
    assert (
        decoded["analyses"][0]["rejected_claims"][0]["grounds"][0]["text"]
        == MARKUP_PAYLOAD
    )
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


def test_no_page_can_be_framed(client, broken_client):
    """``frame-ancestors`` is why the CSRF check on ``/analyze`` means anything.

    A press inside somebody else's frame reaches this app as same-origin,
    because it genuinely comes from this app's own page — so framing walks past
    the header check, and refusing to be framed is what closes it. The
    directive is asserted per page rather than once, because it does **not**
    fall back to ``default-src``: a policy can read as total, pass every other
    check in this file, and still leave the page framable.
    """
    run_id = run_to_completion(client)
    served = {
        "form": client.get("/"),
        "report": client.get(f"/report/{run_id}"),
        "diagnostic": broken_client.get("/"),
    }
    for page, response in served.items():
        csp = response.headers["Content-Security-Policy"]
        assert "frame-ancestors 'none'" in csp, f"the {page} page can be framed"


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
    assert (
        json.loads(payload.group(1))["analyses"][0]["claims"][0]["title"]
        == "__CSP_NONCE__"
    )
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


# --- the other two pages (#114) ---------------------------------------------
#
# The report page had both controls and the form and diagnostic pages had
# neither, which read as an oversight rather than a decision. These hold the
# same two rules over all three: untrusted text reaches the DOM as text, and
# every page carries a policy that authorises its own blocks and nothing else.


def form_javascript() -> str:
    """The form page's behaviour block, with its comments stripped.

    The same treatment :func:`viewer_javascript` gives the viewer, and for the
    same reason: the prose explains why there is no ``innerHTML``, so a
    substring check over the raw template would fail on its own documentation.
    """
    from webapp.main import _FORM_PAGE

    body = _FORM_PAGE.split('<script nonce="__CSP_NONCE__">')[-1].split("</script>")[0]
    return "\n".join(
        line for line in body.splitlines() if not line.lstrip().startswith("//")
    )


@pytest.mark.parametrize("sink", HTML_STRING_SINKS)
def test_the_form_page_has_no_html_string_sink(sink):
    """``fail()`` used to write ``innerHTML``, and submitter bytes reach it.

    A source label travels label → ``LimitBreach.message`` → the ``failed``
    event → the page, and a validator message travels the ``rejected`` event
    the same way. Neither is escaped by ``sources.py``, which excludes line
    breaks and control characters and says nothing about ``<``.
    """
    assert sink not in form_javascript(), (
        f"the form page uses {sink}. Source labels and validator messages carry"
        f" submitter bytes onto this page — build DOM nodes or set textContent."
    )


def test_the_form_page_javascript_parses():
    """A syntax error here serves a page whose Analyze button silently does nothing.

    The block is served as text and never compiled by anything in this lane, so
    without this the first thing to run it is a browser. Skipped rather than
    failed where ``node`` is absent: the check is worth having in CI and on a
    developer's machine, and it is not worth making the suite depend on a
    JavaScript runtime.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node on PATH to parse the form's script block")

    from webapp.main import _FORM_PAGE

    body = _FORM_PAGE.split('<script nonce="__CSP_NONCE__">')[-1].split("</script>")[0]
    with tempfile.NamedTemporaryFile("w", suffix=".js") as handle:
        handle.write(body)
        handle.flush()
        checked = subprocess.run(
            [node, "--check", handle.name],
            capture_output=True,
            text=True,
            check=False,
        )

    assert checked.returncode == 0, checked.stderr


def test_the_page_posts_the_selection_it_built_from_the_checkboxes():
    """The picker has to reach the body, or every submission runs the default set.

    A source-level check, which is what this lane can decide about JavaScript
    it does not execute. What the *server* does with the posted selection is
    covered for real, above, through the app.
    """
    javascript = form_javascript()

    assert "frameworks: selection()" in javascript
    assert 'querySelectorAll("input[name=framework]")' in javascript


def test_an_options_value_is_parsed_as_json_not_read_as_text():
    """A level is ``Literal[1, 2, 3]``, so posting the string "1" is a 400.

    The control carries each choice's JSON and the page parses it back. This is
    the line that keeps a filled-in form from being refused, and it is one
    ``JSON.parse`` away from being wrong in a way no server-side test sees.
    """
    assert "JSON.parse(s.value)" in form_javascript()


def test_the_form_page_carries_no_escape_helper():
    """The corollary, the viewer's rule applied here.

    The page had one and every call site remembered it. That is the arrangement
    that had already failed once elsewhere, which is why the helper going away
    is the fix rather than a fourth call site being added to it.
    """
    assert "escape(" not in form_javascript()


@pytest.mark.parametrize("page", ["form", "diagnostic", "report"])
def test_every_page_ships_a_nonce_csp(client, broken_client, page):
    """No page is served bare, and each nonce covers its own page's blocks."""
    if page == "form":
        response = client.get("/")
    elif page == "diagnostic":
        response = broken_client.get("/")
    else:
        response = client.get(f"/report/{run_to_completion(client)}")

    csp = response.headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp
    assert "base-uri 'none'" in csp
    assert "form-action 'none'" in csp
    assert "unsafe-inline" not in csp
    assert "__CSP_NONCE__" not in response.text

    nonce = re.search(r"'nonce-([^']+)'", csp).group(1)
    blocks = re.findall(r"<(script|style)([^>]*)>", response.text)
    assert blocks, f"the {page} page carries no inline block for a nonce to cover"
    for tag, attributes in blocks:
        assert f'nonce="{nonce}"' in attributes, (
            f"a <{tag}> block on the {page} page carries no nonce, so the CSP"
            " would block it"
        )


def test_the_form_page_may_reach_its_own_origin_and_no_further(client):
    """It fetches ``/example``, posts ``/analyze`` and opens ``/events``.

    ``default-src 'none'`` alone would block all three, so this is the one
    grant the form page needs that the report page does not.
    """
    csp = client.get("/").headers["Content-Security-Policy"]
    assert "connect-src 'self'" in csp


def test_the_diagnostic_page_is_granted_no_script_at_all(broken_client):
    """It runs none, so ``script-src`` falls through to ``default-src 'none'``.

    A page with nothing to authorise should not carry the grant that would
    authorise something.
    """
    response = broken_client.get("/")
    csp = response.headers["Content-Security-Policy"]
    assert "script-src" not in csp
    assert "connect-src" not in csp
    assert "<script" not in response.text


def test_every_page_render_gets_a_fresh_nonce(client, broken_client):
    """A nonce reused across responses is not a nonce."""
    for page_client in (client, broken_client):
        first = page_client.get("/").headers["Content-Security-Policy"]
        second = page_client.get("/").headers["Content-Security-Policy"]
        assert first != second


def test_a_config_error_spelling_the_placeholder_is_not_substituted(tiers):
    """The nonce is stamped before the fields are filled, as on the report page.

    An error message is content. One that happens to spell the placeholder must
    come back as those characters rather than become a live nonce.
    """
    from analysis_service import ConfigError
    from webapp.main import diagnostic_page

    page = diagnostic_page(
        Startup(
            engine_for=None,
            frameworks=(),
            tiers=tiers,
            error=ConfigError("__CSP_NONCE__"),
        )
    )

    nonce = re.search(r"'nonce-([^']+)'", page.csp).group(1)
    assert "__CSP_NONCE__" in page.html
    assert page.html.count(nonce) == 1


@pytest.mark.parametrize(
    "path", ["/", "/example", "/analyze", "/events/nope", "/report/nope"]
)
def test_every_response_carries_the_sniffing_and_referrer_headers(client, path):
    """Per response rather than per page, which is why they are not the CSP.

    ``/example`` serves prose as ``text/plain`` and sniffing is what would let a
    browser decide otherwise; a run id in an outbound ``Referer`` is the other
    half. The 404s and the 405 are included on purpose — an error response is
    still a response.
    """
    response = client.get(path)
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_the_event_stream_still_streams_under_the_header_middleware(client):
    """The headers are added to the frame, never by buffering the body.

    A middleware that collected the response before editing it would turn the
    per-node progress into one delivery at the end, which is the whole point of
    the stream.
    """
    started = client.post("/analyze", json=posted("An app."), headers=SAME_ORIGIN)
    with client.stream("GET", f"/events/{started.json()['run']}") as response:
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["content-type"].startswith("text/event-stream")
        assert "event: done" in "".join(response.iter_text())
