"""Every page script in a real browser: headless Chromium through Playwright.

The first-run pages are driven through each step that sends answers. The review
and sitting pages are loaded once each, and fail on a script error or on a page
that draws nothing from its data.

The other page tests run the scripts under ``node`` with a stand-in DOM. These
serve the app over HTTP and drive its pages as a person would, so they also
catch what a stand-in cannot: a script error in a real engine, a request the
browser shapes differently, a page that never reaches its next state.

They need the ``browser`` dependency group and Chromium (``uv sync --group
browser`` and ``uv run playwright install chromium``). Without them they skip,
except where ``REQUIRE_BROWSER=1``, as in CI, where a missing browser fails
rather than lets the check pass without running.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import threading
import time
from pathlib import Path

import pytest

from analysis_service.assertions import ABSENT, Assertion, AssertionCatalog, Subject
from tests import test_webapp_questions as web

tiers = web.tiers

REQUIRED = os.environ.get("REQUIRE_BROWSER") == "1"


def _unavailable(reason: str) -> None:
    if REQUIRED:
        pytest.fail(f"REQUIRE_BROWSER=1, and {reason}")
    pytest.skip(reason)


@pytest.fixture(scope="module")
def browser():
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        _unavailable("Playwright is not installed: uv sync --group browser")
    with sync_playwright() as playwright:
        try:
            chromium = playwright.chromium.launch()
        except Error as error:
            _unavailable(f"Chromium did not launch: {error}")
        yield chromium
        chromium.close()


@pytest.fixture
def served(tiers):
    """The first-run app over a pausing stub run, served on a free local port."""
    app = web.app_for(tiers, web.PausingRunner(catalog=False), catalog=False)
    yield from serve(app)


def serve(app):
    """Serve ``app`` on a free local port; yields its base URL."""
    try:
        import uvicorn
    except ImportError:
        _unavailable("uvicorn is not installed: uv sync --group browser")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "the app did not start"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(5)


@pytest.fixture
def page(browser, served):
    """A fresh page on the app's form; every script error fails the test."""
    context = browser.new_context(base_url=served)
    opened = context.new_page()
    errors: list[str] = []
    opened.on("pageerror", lambda error: errors.append(str(error)))
    opened.goto("/")
    yield opened
    context.close()
    assert errors == [], f"the page raised: {errors}"


def _post_to_answer(response) -> bool:
    return "/answer/" in response.url and response.request.method == "POST"


def saved(page) -> None:
    """Wait until the page says every answer is saved."""
    page.wait_for_function(
        "() => document.querySelector('#save-status').dataset.state === 'saved'"
    )


def choose(page, locator, value: str) -> None:
    """Choose ``value`` in ``locator``, which the page saves at once, and wait
    for the save to land."""
    with page.expect_response(_post_to_answer) as done:
        locator.select_option(value)
    assert done.value.ok, done.value.text()
    saved(page)


def open_groups(page) -> None:
    """Open each shown question group, as a submitter clicks its heading."""
    for summary in page.query_selector_all(".question-group:not([open]) > summary"):
        if summary.is_visible():
            summary.click()


def pause(page, opened: bool = True) -> None:
    """Submit a description that asks questions, wait for the list, and open
    its groups unless ``opened`` is false."""
    if not page.locator("#analysis-name").input_value():
        page.fill("#analysis-name", "Test analysis")
    page.fill("#description", "A web app talks to a database.")
    page.check("#ask")
    if not page.locator('input[name="framework"]:checked').count():
        for framework in page.locator('input[name="framework"]').all():
            framework.check()
    page.click("#go")
    page.wait_for_selector("#asked", state="visible")
    if opened:
        open_groups(page)


def test_entry_validates_its_name_framework_and_description_limit(page):
    assert page.locator("#analysis-name").get_attribute("required") is not None
    before = int(page.locator("#description").get_attribute("data-max-bytes"))
    assert f"{before:,} bytes remaining" in page.inner_text("#description-remaining")
    page.fill("#analysis-name", "Test analysis")
    page.fill("#description", "A")
    assert f"{before - 1:,} bytes remaining" in page.inner_text(
        "#description-remaining"
    )
    page.click("#go")
    assert page.inner_text("#problem") == "Please select a framework."


def test_sidebar_collapses_and_stays_collapsed_on_settings(page):
    page.click("#sidebar-toggle")
    assert "sidebar-collapsed" in page.locator(".workspace").get_attribute("class")
    assert (
        page.locator("#sidebar-toggle").get_attribute("aria-label") == "Expand sidebar"
    )
    page.goto("/settings")
    assert "sidebar-collapsed" in page.locator(".workspace").get_attribute("class")
    # A collapsed link shows only its icon, and keeps its name and a title.
    settings = page.get_by_role("link", name="Settings")
    assert settings.locator(".nav-icon").is_visible()
    assert settings.locator(".nav-label").bounding_box()["width"] <= 1
    assert settings.get_attribute("title") == "Settings"
    page.click("#sidebar-toggle")
    assert settings.locator(".nav-label").bounding_box()["width"] > 1
    assert settings.get_attribute("title") is None
    assert (
        page.locator("#sidebar-toggle").get_attribute("aria-label")
        == "Collapse sidebar"
    )


def test_clearing_a_control_sends_no_answer_for_it(page):
    """Blank after "There is none" still sent ``none`` (#1289, B1)."""
    pause(page)
    control = page.locator('#questions select:has(option[value="mechanism"])').first
    choose(page, control, "none")
    control.select_option("")
    with page.expect_request("**/answer/*") as sent:
        page.click("#continue")
    facts = sent.value.post_data_json["facts"]
    assert all(fact.get("value") != "none" for fact in facts)


def test_a_submitter_who_skips_every_question_reaches_a_report_that_says_what_is_open(
    page,
):
    """Skip, the ready state, the start button and the report, end to end."""
    pause(page)
    for _ in range(20):
        if page.is_disabled("#skip"):
            break
        page.click("#skip")
        # The skip hides its rows at once, before its save answers. The
        # start button stays disabled until the whole skip has ended.
        page.wait_for_function("() => !document.querySelector('#continue').disabled")
    else:
        pytest.fail("the list never ended")
    assert "Nothing runs until you choose Start the analysis" in page.inner_text(
        "#questions"
    )
    assert page.is_disabled("#skip")
    assert "primary" in page.get_attribute("#continue", "class")
    page.click("#continue")
    page.wait_for_url("**/report/**")
    report = page.inner_text("#report-view")
    assert re.search(r"\d+ additional findings? (is|are) conditional", report)
    assert "You can use this report without answering any questions." in report
    assert "Conditional findings ·" in report


def test_a_report_moves_between_its_three_sections(page):
    """The report opens on its findings. A conditional finding's question link
    opens Questions on its row, and About the analysis holds the System Model
    and the pipeline (#1884)."""
    pause(page)
    page.click("#continue")
    page.wait_for_url("**/report/**")
    assert page.locator("#analyses").is_visible()
    assert not page.locator("#elements").is_visible()
    assert not page.locator("#question-actions").is_visible()
    page.locator(
        "details.finding-group > summary", has_text="Conditional findings"
    ).click()
    finding = page.locator("details.finding-group details.finding").first
    finding.locator("> summary").click()
    finding.locator(".field ul a").first.click()
    page.wait_for_url("**?view=questions")
    assert page.locator("#question-actions").is_visible()
    assert page.evaluate(
        "document.getElementById('list').contains(document.activeElement)"
    )
    page.get_by_role("link", name="About the analysis").click()
    page.wait_for_url("**?view=about")
    assert page.locator("#elements").is_visible()
    assert not page.locator("#nodes").is_visible()
    page.get_by_text("Pipeline", exact=True).click()
    assert page.locator("#nodes").is_visible()
    assert not page.locator("#analyses").is_visible()
    page.get_by_role("link", name="Report", exact=True).click()
    page.locator("#analyses").wait_for()


def test_a_phone_sized_submitter_answers_skips_corrects_and_starts(browser, served):
    """The #1289 review asked for a small-screen walk through every step."""
    context = browser.new_context(
        base_url=served, viewport={"width": 375, "height": 667}
    )
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto("/")
    posted: list[str] = []
    page.on(
        "request",
        lambda request: (
            posted.append(request.post_data or "")
            if "/answer/" in request.url and request.method == "POST"
            else None
        ),
    )
    pause(page)
    choose(page, page.locator('#questions select:has(option[value="no"])').first, "no")
    with page.expect_response(_post_to_answer):
        page.click("#skip")
    saved(page)
    # The answered row stays in the list, compact, and "Change" reopens it.
    # A handle keeps the row while the list adds rows above it.
    row = page.locator("#list p", has=page.locator("button", has_text="Change"))
    held = row.first.element_handle()
    held.query_selector("button").click()
    with page.expect_response(_post_to_answer) as corrected:
        held.query_selector("select").select_option("yes")
    facts = corrected.value.request.post_data_json["facts"]
    saved(page)
    wide = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    page.click("#continue")
    page.wait_for_url("**/report/**")
    context.close()
    assert errors == [], f"the page raised: {errors}"
    assert any(fact.get("value") == "yes" for fact in facts), posted
    assert wide <= 0, f"the page scrolls sideways by {wide}px on a phone"


def test_the_page_shows_no_round_control(page):
    """No round counter, no next-round and no save-and-next step (#1632)."""
    pause(page)
    text = page.inner_text("#asked").lower()
    assert "round" not in text and "next" not in text
    assert not page.is_visible("#save"), "nothing is unsaved, so no save button"
    assert page.locator("#list button", has_text="Skip for now").count() > 0


def test_a_focused_editor_survives_another_answer_s_save(page):
    """Typing in one row while another row's save brings the list back keeps
    the node, the text and the focus (#1632)."""
    pause(page)
    typed = page.locator("#list input[list]").first
    typed.click()
    typed.type("the platform tea")
    choose(page, page.locator('#list select:has(option[value="yes"])').first, "yes")
    assert page.evaluate(
        "document.activeElement === document.querySelector('#list input[list]')"
    )
    assert typed.input_value() == "the platform tea"


def test_a_reload_shows_the_saved_answers(page):
    pause(page)
    control = page.locator('#list select:has(option[value="yes"])').first
    choose(page, control, "yes")
    page.reload()
    page.wait_for_selector("#asked", state="visible")
    open_groups(page)
    page.click("#earlier summary")
    assert "yes" in page.inner_text("#earlier")


def test_a_new_analysis_reuses_the_answers_of_an_earlier_one(page):
    """The form lists the earlier analysis, and the new one asks again none of
    the questions it answered (ADR 0079)."""
    pause(page)
    control = page.locator('#list select:has(option[value="yes"])').first
    label = control.get_attribute("aria-label")
    assert label, "the row's control names its question"
    choose(page, control, "yes")
    page.goto("/")
    page.wait_for_selector("#answers-from-field", state="visible")
    ask_row = page.locator(".ask-with-help").bounding_box()
    reuse_row = page.locator("#answers-from-field").bounding_box()
    help = page.locator(".question-help summary").bounding_box()
    assert ask_row and reuse_row and help
    assert reuse_row["y"] >= ask_row["y"] + ask_row["height"]
    assert ask_row["y"] <= help["y"] < ask_row["y"] + ask_row["height"]
    earlier = page.locator("#answers-from option").nth(1).get_attribute("value")
    page.select_option("#answers-from", earlier)
    pause(page)
    page.click("#earlier summary")
    assert "yes" in page.inner_text("#earlier")
    assert page.locator(f'#list select[aria-label="{label}"]').count() == 0


class HeldRunner(web.PausingRunner):
    """Holds each run in progress until the test releases it."""

    def __init__(self) -> None:
        super().__init__(catalog=False)
        self.step = threading.Event()
        self.release = threading.Event()

    async def run(self, job, on_node):
        while not self.step.is_set():
            await asyncio.sleep(0.05)
        await on_node("extract")
        while not self.release.is_set():
            await asyncio.sleep(0.05)
        return await super().run(job, on_node)


def test_a_running_analysis_keeps_a_tab_that_leads_back_to_its_progress(browser, tiers):
    """A run that will ask shows its progress under Questions, and the tab
    stays enabled, so a submitter who opens Description can come back."""
    runner = HeldRunner()
    for base in serve(web.app_for(tiers, runner, catalog=False)):
        context = browser.new_context(base_url=base)
        opened = context.new_page()
        opened.goto("/")
        opened.fill("#analysis-name", "Test analysis")
        opened.fill("#description", "A web app talks to a database.")
        opened.check("#ask")
        for framework in opened.locator('input[name="framework"]').all():
            framework.check()
        opened.click("#go")
        opened.wait_for_function(
            "() => document.querySelector('#questions-nav')"
            ".getAttribute('aria-current') === 'page'"
        )
        tab = opened.locator("#questions-nav")
        assert opened.is_visible("#progress-panel")
        assert opened.locator("#analysis-progress").count() == 1
        assert opened.locator("#analysis-progress").get_attribute("value") == "0"
        assert opened.locator("#analysis-progress").get_attribute("max") == "4"
        assert (
            opened.locator("#status-text").inner_text()
            == "0 of 4 stages complete · Reading your description and identifying the parts of your system…"
        )
        elapsed = opened.locator("#status-elapsed")
        assert elapsed.inner_text().startswith("Still working on this step · 0:0")
        assert elapsed.get_attribute("role") is None
        opened.wait_for_function(
            "() => !document.querySelector('#status-elapsed').textContent.includes('· 0:00.')"
        )
        assert opened.is_hidden("#upload")
        assert opened.locator("#transcript-file").is_disabled()
        assert opened.locator(".configuration").count() == 0
        assert (
            opened.locator("#progress-panel")
            .locator("h2, details, .spinner, #ticks")
            .count()
            == 0
        )
        assert tab.is_enabled()

        runner.step.set()
        opened.wait_for_function(
            "() => document.querySelector('#status-text').textContent === '1 of 4 stages complete · Checking the system map for gaps or conflicting details…'"
        )
        assert opened.locator("#analysis-progress").get_attribute("value") == "1"
        assert elapsed.inner_text().startswith("Still working on this step · 0:0")
        opened.click("#description-nav")
        assert not opened.is_visible("#progress-panel")
        tab.click()
        assert opened.is_visible("#progress-panel")
        assert tab.get_attribute("aria-current") == "page"

        runner.release.set()
        opened.wait_for_selector("#asked", state="visible")
        context.close()


def test_one_shared_answer_saves_the_part_for_every_element_listed(page):
    """A box asks its question once (ADR 0081): one choice in the shared
    control saves that part for each element under it."""
    pause(page)
    shared = page.locator("#list .shared-controls select").first
    shared.wait_for()
    keys = page.locator("#list .each-one select[data-facet]")
    facet = shared.get_attribute("data-facet")
    with page.expect_response(_post_to_answer) as response:
        shared.select_option("yes")
    facts = response.value.request.post_data_json["facts"]
    elements = keys.evaluate_all(
        "(selects, facet) => [...new Set(selects.filter(s => s.dataset.facet === facet)"
        ".map(s => s.dataset.key))]",
        facet,
    )
    assert len(elements) >= 2
    sent = sorted(json.dumps(fact["key"], separators=(",", ":")) for fact in facts)
    assert sent == sorted(elements)
    assert all(fact["facets"] == {facet: "yes"} for fact in facts)


def test_no_to_the_rest_answers_each_blank_capability_question(page):
    """The button answers "no" to every capability question left blank, each
    as its own answer, and leaves a capability answered "yes" as it is."""
    pause(page)
    rows = page.locator('#list p[data-key^=\'["","","","","",\']')
    rows.first.wait_for()
    choose(page, rows.first.locator("select").first, "yes")
    blank = rows.evaluate_all(
        "(rows) => rows.filter((row) => !row.hidden && row.querySelector('select')"
        " && !row.querySelector('select').value).map((row) => row.dataset.key)"
    )
    assert len(blank) >= 2
    with page.expect_response(_post_to_answer) as response:
        page.click("#no-rest")
    facts = response.value.request.post_data_json["facts"]
    sent = sorted(json.dumps(fact["key"], separators=(",", ":")) for fact in facts)
    assert sent == sorted(blank)
    assert {fact["value"] for fact in facts} == {"no"}
    saved(page)
    assert page.locator("#no-rest").is_hidden()


def test_a_double_click_on_start_starts_one_run(page):
    pause(page)
    starts: list[str] = []
    page.on(
        "request",
        lambda request: (
            starts.append(request.url)
            if "/answer/" in request.url
            and request.method == "POST"
            and '"save"' not in (request.post_data or "")
            else None
        ),
    )
    page.dblclick("#continue")
    page.wait_for_url("**/report/**")
    assert len(starts) == 1


def test_two_tabs_do_not_write_over_each_other_in_silence(browser, served):
    """The second tab's stale save is refused; it reads the answers again and
    shows the answer the first tab saved, for the submitter to settle (#1632)."""
    context = browser.new_context(base_url=served)
    first = context.new_page()
    errors: list[str] = []
    first.on("pageerror", lambda error: errors.append(str(error)))
    first.goto("/")
    pause(first)
    second = context.new_page()
    second.on("pageerror", lambda error: errors.append(str(error)))
    second.goto(first.url)
    second.wait_for_selector("#asked", state="visible")
    open_groups(second)
    # A plain row's select: the first yes/no select in a STRIDE list can be a
    # facet cell, whose row is a table row.
    control = '#list p select:has(option[value="yes"])'
    choose(first, first.locator(control).first, "yes")
    with second.expect_response(lambda r: "/workspace/runs/" in r.url):
        second.locator(control).first.select_option("no")
    second.wait_for_selector("#list .conflict")
    assert "Changed elsewhere" in second.inner_text("#list .conflict")
    assert "changed elsewhere" in second.inner_text("#save-status")
    # The row with the conflict keeps its place; the list has moved on above
    # it, and "Take theirs" removes the note, so the row is held first.
    row = second.locator("#list p:has(.conflict)").first.element_handle()
    row.query_selector("button:has-text('Take theirs')").click()
    assert row.query_selector("select").input_value() == "yes"
    saved(second)
    context.close()
    assert errors == [], f"a page raised: {errors}"


def test_a_first_report_offers_no_way_to_reopen_a_known_answer(page):
    """The follow-up refuses "I don't know" for a known earlier answer, and
    the page offered it (#1289, F2)."""
    first_report(page)
    earlier = earlier_answers(page)
    for index in range(earlier.count()):
        earlier.nth(index).locator("button", has_text="Change").click()
    offered = earlier.locator("option").evaluate_all("o => o.map(n => n.value)")
    assert "no" in offered
    assert "unknown" not in offered
    assert earlier.locator("label", has_text="I don't know").count() == 0


def earlier_answers(page):
    """The rows of the answers the report read, in the Questions section."""
    page.get_by_role("link", name=re.compile("^Questions")).click()
    page.locator("#question-index a", has_text="Answers this report read").click()
    group = page.locator(".question-group", has_text="Answers this report read")
    return group.locator("p.question")


def first_report(page) -> None:
    """Answer "no" to the first question, start, and wait for the report."""
    pause(page)
    choose(page, page.locator('#questions select:has(option[value="no"])').first, "no")
    page.click("#continue")
    page.wait_for_url("**/report/**")


def final_report(page) -> str:
    """Change the earlier answer to "yes" on a first report, and send the
    follow-up. Returns the final report's path."""
    first_report(page)
    first = page.url
    row = earlier_answers(page).first
    row.locator("button", has_text="Change").click()
    row.locator("select").select_option("yes")
    snapshots = []
    page.on(
        "request",
        lambda request: (
            snapshots.append(request.url) if "/workspace/runs/" in request.url else None
        ),
    )
    with page.expect_request("**/answer/*") as sent:
        page.get_by_role("button", name="Update report").click()
    assert any(
        fact.get("value") == "yes" for fact in sent.value.post_data_json["facts"]
    )
    page.wait_for_url(
        lambda url: "/report/" in url and url.split("?")[0] != first.split("?")[0]
    )
    assert len(snapshots) == 1, "Live progress must not also start reopening the run"
    return page.url


def test_a_first_report_sends_a_changed_answer_and_reaches_the_final_report(page):
    final_report(page)
    page.get_by_role("link", name=re.compile("^Questions")).click()
    assert page.locator("#questions-title").inner_text() == "Correct an answer"
    assert page.get_by_role("button", name="Update report").count() == 0


def test_a_final_report_saves_a_correction(page):
    final_report(page)
    row = earlier_answers(page).first
    row.locator("button", has_text="Change").click()
    row.locator("select").select_option("unknown")
    with page.expect_request("**/correct/*") as sent:
        page.click("text=Save corrections")
    assert sent.value.post_data_json["facts"][0]["value"] == "unknown"
    page.wait_for_selector("text=Corrected after this report", state="attached")


def opened(browser, url: str):
    """A page at ``url``, and the script errors it raises."""
    page = browser.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url)
    return page, errors


def case_title(corpus) -> str:
    """The first sitting case's title, which each sitting page's rail draws."""
    from tests.test_sitting_app import CASE

    return json.loads((corpus / CASE / "case.json").read_text())["title"]


def test_the_review_pages_run_their_scripts(browser, tmp_path):
    from tests import test_review_app as review
    from webapp.review import build_session, create_app

    session = build_session(
        [[review.STEADY, review.SOMETIMES], [review.STEADY]],
        voter="ada",
        ledger_path=tmp_path / "votes",
        configs={review.STEADY.case: "engine-1.2.3"},
    )
    # Text each page's script draws from the API, so a script that never ran
    # fails here as well as one that raised.
    drawn = {"/": "findings in the reference pool", "/review": review.SOMETIMES.title}
    for url in serve(create_app(session)):
        for path, expected in drawn.items():
            page, errors = opened(browser, url + path)
            page.wait_for_load_state("networkidle")
            text = page.inner_text("body")
            page.close()
            assert errors == [], f"{path} raised: {errors}"
            assert expected in text, f"{path} drew nothing from the API"


def test_the_sitting_page_runs_its_script(browser, tmp_path):
    from tests import test_sitting_app as sitting
    from webapp.sitting import create_app

    tree = sitting.build_tree(tmp_path)
    title = case_title(tree / "evals" / "corpus")
    for url in serve(create_app(sitting.session_for(tree, "ada", sitting.CASE))):
        page, errors = opened(browser, url)
        page.wait_for_load_state("networkidle")
        text = page.inner_text("body")
        page.close()
    assert errors == [], f"the sitting page raised: {errors}"
    assert title in text


def test_the_offline_sitting_page_runs_its_script(browser, tmp_path):
    from evals.harness.reference import ANONYMOUS
    from tests import test_sitting_app as sitting
    from webapp.offline_sitting import build

    corpus = sitting.build_tree(tmp_path) / "evals" / "corpus"
    written = tmp_path / "sitting.html"
    written.write_text(build(corpus, "ada", ANONYMOUS), encoding="utf-8")
    page, errors = opened(browser, written.as_uri())
    text = page.inner_text("body")
    page.close()
    assert errors == [], f"the offline sitting page raised: {errors}"
    assert case_title(corpus) in text


def test_the_offline_sitting_page_shows_a_restored_mark_keyed_by_an_alias(
    browser, tmp_path
):
    """A draft keyed before a version moved still marks its finding."""
    from evals.harness import sitting as sittings
    from evals.harness.envelope import VERSION
    from evals.harness.reference import ANONYMOUS
    from tests import test_sitting_app as sitting
    from webapp.offline_sitting import build

    corpus = sitting.build_tree(tmp_path) / "evals" / "corpus"
    case_dir = next(
        path
        for path in sorted(corpus.iterdir())
        if path.is_dir() and any(t.aliases for t in sittings.prepare(path).mark_targets)
    )
    prepared = sittings.prepare(case_dir)
    target = next(t for t in prepared.mark_targets if t.aliases)
    mark = sittings.MARKS[0]
    draft = {
        "envelope": VERSION,
        "cases": {
            prepared.case_id: {
                "own_list": ["x" * sittings.MIN_OWN_LIST],
                "marks": {target.aliases[0]: mark},
            }
        },
    }
    written = tmp_path / "sitting.html"
    written.write_text(build(corpus, "ada", ANONYMOUS), encoding="utf-8")
    page, errors = opened(browser, written.as_uri())
    page.set_input_files(
        "#file",
        files=[
            {
                "name": "draft.json",
                "mimeType": "application/json",
                "buffer": json.dumps(draft).encode("utf-8"),
            }
        ],
    )
    page.get_by_role("button", name=prepared.title).click()
    radios = page.locator(f'input[name="m-{target.fingerprint}"]')
    checked = [radios.nth(i).is_checked() for i in range(radios.count())]
    page.close()

    assert errors == [], f"the offline sitting page raised: {errors}"
    assert checked == [option == mark for option in sittings.MARKS]


def test_workspace_reopens_grouped_questions_and_renders_names_as_text(page):
    page.fill("#analysis-name", '<img src=x onerror="alert(1)">')
    pause(page)
    page.wait_for_selector("#question-index a")
    original_url = page.url
    assert page.locator("#analysis-title img").count() == 0
    page.reload()
    page.wait_for_selector("#asked", state="visible")
    assert page.url == original_url
    assert page.locator("#question-index a").count() > 0
    page.locator("#question-index a").first.click()
    assert page.is_visible("#asked")
    page.click("#reports-nav")
    assert page.is_visible("#reports-panel")
    page.click("#library-nav")
    page.wait_for_selector("#analysis-list a")
    assert page.locator("#analysis-list th").all_inner_texts() == [
        "",
        "Name",
        "Date",
        "Status",
        "Actions",
    ]
    assert page.locator("#analysis-list time").first.get_attribute("datetime")
    page.locator('[data-sort="name"]').click()
    assert page.locator("#sort-name").get_attribute("aria-sort") == "ascending"
    page.locator('[data-sort="name"]').click()
    assert page.locator("#sort-name").get_attribute("aria-sort") == "descending"
    assert page.locator("#analysis-list img").count() == 0
    page.locator("#analysis-list a").click()
    page.wait_for_selector("#asked", state="visible")


def submitted(page, name: str) -> None:
    """Start a run that completes, through the page's own fetch, so the
    request carries the browser's ``Sec-Fetch-Site``."""
    from tests.test_webapp import posted

    body = json.dumps(posted("A web app talks to a database.") | {"name": name})
    status = page.evaluate(
        """async (body) => (await fetch("/analyze", {method: "POST",
        headers: {"Content-Type": "application/json"}, body})).status""",
        body,
    )
    assert status == 200


def test_the_analyses_list_renames_filters_and_deletes(page):
    submitted(page, '<img src=x onerror="alert(1)">')
    submitted(page, "Beta")
    page.goto("/?view=analyses")
    page.wait_for_selector("#analysis-rows tr >> nth=1")
    page.fill("#analysis-search", "beta")
    assert page.locator("#analysis-rows tr").count() == 1
    page.fill("#analysis-search", "")
    page.select_option("#analysis-status", "running")
    assert page.is_visible("#analysis-empty")
    page.select_option("#analysis-status", "")
    page.click('[aria-label="Rename Beta"]')
    page.fill("#analysis-rows input:not([type=checkbox])", "<script>x</script> board")
    page.keyboard.press("Enter")
    page.wait_for_selector("text=<script>x</script> board")
    assert page.locator("#analysis-list img, #analysis-list script").count() == 0
    page.check("#select-shown")
    assert page.inner_text("#delete-selected") == "Delete selected (2)"
    page.click("#delete-selected")
    assert "2 analyses" in page.inner_text("#confirm-delete-text")
    page.click("#confirm-delete-cancel")
    assert page.locator("#analysis-rows tr").count() == 2
    page.click("#delete-selected")
    page.click("#confirm-delete-go")
    page.wait_for_selector("#analysis-empty", state="visible")
    assert page.inner_text("#workspace-message") == "Deleted 2 analyses."


def test_the_settings_page_runs_its_script(browser, tmp_path):
    from tests.test_settings_env import env_in, hand_written, write_settings
    from webapp.main import create_app
    from webapp.settings_page import EXAMPLES

    write_settings(
        tmp_path, hand_written(ANALYSIS_MODEL_REVIEW_MODEL="claude-sonnet-5-5")
    )
    for url in serve(create_app(env=env_in(tmp_path, ANALYSIS_MAX_SOURCES="5"))):
        page, errors = opened(browser, url + "/settings")
        page.wait_for_selector("#settings-rows tr[data-key]")
        assert page.is_hidden("#changes-panel")
        current = page.locator('#sidebar-navigation a[aria-current="page"]')
        assert current.inner_text() == "Settings"
        review = page.locator('tr[data-key="tiers.review.model"]').inner_text()
        sources = page.locator('tr[data-key="resilience.max_sources"]').inner_text()
        location, key = (
            page.locator(f'tr[data-key="{row}"] input').get_attribute("placeholder")
            for row in ("credentials.vertex.location", "credentials.openai.api_key")
        )
        page.fill("#settings-search", "timeout")
        found = page.locator("#settings-rows tr[data-key]").all_inner_texts()
        page.set_viewport_size({"width": 390, "height": 844})
        fits = page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        )
        page.close()
    assert errors == [], f"the settings page raised: {errors}"
    assert "Not in the model list" in review
    assert "Shipped: none shipped" in review
    assert "ANALYSIS_MAX_SOURCES" in sources
    assert location == f"e.g. {EXAMPLES['credentials.vertex.location']}"
    assert key is not None and "e.g." not in key
    assert found and all("imeout" in row for row in found)
    assert fits


def test_the_settings_page_saves_resets_and_refuses_a_stale_tab(browser, tmp_path):
    from tests.test_settings_env import env_in, hand_written, write_settings
    from webapp.main import create_app

    write_settings(tmp_path, hand_written())
    timeout = 'tr[data-key="resilience.timeout_ms"]'
    for url in serve(create_app(env=env_in(tmp_path))):
        page, errors = opened(browser, url + "/settings")
        other, other_errors = opened(browser, url + "/settings")
        page.locator(f"{timeout} input").fill("")
        page.locator("#settings-search").click()
        assert page.locator(f"{timeout} input").get_attribute("aria-invalid") == "true"
        assert (
            "Enter a whole number"
            in page.locator(f"{timeout} .field-error").inner_text()
        )
        page.click("#save-changes")
        page.wait_for_selector("#save-errors", state="visible")
        refused = page.locator("#save-errors").inner_text()

        page.locator(f"{timeout} input").fill("5000")
        listed = page.locator("#changes-list").inner_text()
        page.click("#save-changes")
        page.wait_for_selector("#save-status", state="visible")
        status = page.locator("#save-status").inner_text()
        changed = page.locator(timeout).inner_text()
        written = (tmp_path / "settings.env").read_text()

        other.locator('tr[data-key="resilience.attempts"] input').fill("2")
        other.click("#save-changes")
        other.wait_for_selector("#save-errors", state="visible")
        stale = other.locator("#save-errors").inner_text()

        page.locator(f"{timeout} button.reset").click()
        page.click("#save-changes")
        page.wait_for_selector(f"{timeout} >> text=resilience.toml")
        reset = (tmp_path / "settings.env").read_text()
        page.close()
        other.close()
    assert errors == other_errors == [], f"the settings page raised: {errors}"
    assert "Timeout ms" in listed and "5000" in listed
    assert "Timeout ms: Enter a whole number." in refused
    assert "next analysis uses the new settings" in status
    assert "this page" in changed and "Shipped:" in changed
    assert "ANALYSIS_TIMEOUT_MS=5000" in written
    assert "ANALYSIS_TIMEOUT_MS" not in reset
    assert "Reload the page" in stale


def test_a_thinking_row_follows_its_tier_model_and_resets_to_the_default(
    browser, tmp_path
):
    """The base tier's model takes no level, the strong tier's model takes one,
    "The model's own level" removes a saved level, and a model typed on the
    page that takes none hides the levels before any save."""
    from tests.test_settings_env import env_in, hand_written, write_settings
    from webapp.main import create_app

    write_settings(tmp_path, hand_written())
    base = 'tr[data-key="sampling.base.thinking"]'
    strong = 'tr[data-key="sampling.strong.thinking"]'
    for url in serve(create_app(env=env_in(tmp_path))):
        page, errors = opened(browser, url + "/settings")
        page.wait_for_selector(strong)
        base_text = page.locator(base).inner_text()
        base_selects = page.locator(f"{base} select").count()
        offered = page.locator(f"{strong} select option").all_inner_texts()

        page.select_option(f"{strong} select", "high")
        page.click("#save-changes")
        page.wait_for_selector("#save-status", state="visible")
        written = (tmp_path / "settings.env").read_text()

        page.select_option(f"{strong} select", "")
        page.click("#save-changes")
        page.wait_for_selector(f"{strong} >> text=default")
        removed = (tmp_path / "settings.env").read_text()

        page.select_option('tr[data-key="tiers.strong.vendor"] select', "openai")
        model = page.locator('tr[data-key="tiers.strong.model"] input')
        model.fill("gpt-4o-2024-08-06")
        model.press("Tab")
        typed = page.locator(strong).inner_text()
        typed_selects = page.locator(f"{strong} select").count()
        page.close()
    assert errors == [], f"the settings page raised: {errors}"
    assert "This model takes no thinking level." in base_text
    assert base_selects == 0
    assert offered == ["The model's own level", "low", "medium", "high"]
    assert "ANALYSIS_SAMPLING_STRONG_THINKING=high" in written
    assert "THINKING" not in removed
    assert "This model takes no thinking level." in typed
    assert typed_selects == 0


def test_the_settings_page_saves_a_key_with_the_vendor_that_needs_it(browser, tmp_path):
    from tests.test_settings_env import env_in, hand_written, write_settings
    from webapp.main import create_app

    key = "sk-typed-in-the-browser-never-shown"
    write_settings(tmp_path, hand_written())
    vendor = 'tr[data-key="tiers.review.vendor"] select'
    key_row = 'tr[data-key="credentials.anthropic.api_key"]'
    for url in serve(create_app(env=env_in(tmp_path))):
        page, errors = opened(browser, url + "/settings")
        page.wait_for_selector(key_row)
        page.select_option(vendor, "openai")
        hidden = page.locator(key_row).count()
        warned = page.locator("#changes-list").inner_text()

        page.select_option(vendor, "anthropic")
        page.locator(f"{key_row} input").fill(key)
        listed = page.locator("#changes-list").inner_text()
        page.click("#save-changes")
        page.wait_for_selector("#save-status", state="visible")
        shown = page.locator(key_row).inner_text()
        html = page.content()
        page.close()
    assert errors == [], f"the settings page raised: {errors}"
    assert hidden == 0
    assert "deletes the saved anthropic api key" in warned
    assert "a new key" in listed and key not in listed
    assert "A key is saved" in shown
    assert key not in html
    [line] = [
        line
        for line in (tmp_path / "settings.env").read_text(encoding="utf-8").split("\n")
        if line.startswith("ANALYSIS_ANTHROPIC_API_KEY_FILE=")
    ]
    assert Path(line.partition("=")[2]).read_text(encoding="utf-8") == key


@pytest.mark.parametrize(
    "vendors",
    [
        {"base": "openai", "strong": "vertex", "review": "openai"},
        {"base": "anthropic", "strong": "anthropic", "review": "anthropic"},
        {"base": "anthropic", "strong": "vertex", "review": "openai"},
    ],
)
def test_the_page_warns_of_each_key_line_that_the_save_removes(
    browser, tmp_path, vendors
):
    """The page and the server each read which saved key a save removes:
    the warnings before the save name the key lines that it removes."""
    from analysis_service.settings_variables import secret_vars
    from tests.test_settings_env import env_in, hand_written, write_settings
    from webapp.main import create_app
    from webapp.settings_page import SETTINGS

    models = {
        "openai": "gpt-4.1",
        "anthropic": "claude-opus-5",
        "vertex": "gemini-2.5-pro",
    }
    path = write_settings(tmp_path, hand_written())
    before = path.read_text(encoding="utf-8")
    for url in serve(create_app(env=env_in(tmp_path))):
        page, errors = opened(browser, url + "/settings")
        page.wait_for_selector('tr[data-key="tiers.base.model"]')
        for tier, vendor in vendors.items():
            page.select_option(f'tr[data-key="tiers.{tier}.vendor"] select', vendor)
            page.fill(f'tr[data-key="tiers.{tier}.model"] input', models[vendor])
        warned = {
            item.get_attribute("data-key")
            for item in page.locator("#changes-list li.warning").all()
        }
        page.click("#save-changes")
        page.wait_for_selector("#save-status", state="visible")
        page.close()
    after = path.read_text(encoding="utf-8")
    removed = {
        key
        for key, setting in SETTINGS.items()
        if setting.variable in secret_vars()
        and f"{setting.variable}=" in before
        and f"{setting.variable}=" not in after
    }
    assert errors == [], f"the settings page raised: {errors}"
    assert removed == warned


def test_a_tier_model_suggests_the_models_of_the_vendor_just_chosen(browser, tmp_path):
    from tests.test_settings_env import env_in, hand_written, write_settings
    from webapp.main import create_app

    write_settings(tmp_path, hand_written())
    model = 'tr[data-key="tiers.review.model"]'
    for url in serve(create_app(env=env_in(tmp_path))):
        page, errors = opened(browser, url + "/settings")
        page.wait_for_selector(model)
        before = page.locator(f"{model} input").get_attribute("list")
        page.select_option('tr[data-key="tiers.review.vendor"] select', "gemini")
        after = page.locator(f"{model} input").get_attribute("list")
        suggested = page.locator(f"#{after} option").count()
        note = page.locator(model).inner_text()
        page.close()
    assert errors == [], f"the settings page raised: {errors}"
    assert (before, after) == ("models-anthropic", "models-gemini")
    assert suggested > 0
    assert "Not in the model list" in note


def test_a_saved_vendor_that_names_an_object_key_still_draws_the_table(
    browser, tmp_path
):
    from tests.test_settings_env import env_in, hand_written, write_settings
    from webapp.main import create_app

    write_settings(tmp_path, hand_written(ANALYSIS_MODEL_REVIEW_VENDOR="__proto__"))
    for url in serve(create_app(env=env_in(tmp_path))):
        page, errors = opened(browser, url + "/settings")
        page.wait_for_selector('tr[data-key="tiers.review.model"]')
        note = page.locator('tr[data-key="tiers.review.model"]').inner_text()
        page.close()
    assert errors == [], f"the settings page raised: {errors}"
    assert "Not in the model list" in note


def test_an_uploaded_transcript_alone_starts_an_analysis(page):
    """The button opens a file chooser, a binary file is refused on the page,
    and a text file goes to ``/analyze`` as the one source, as text."""
    page.locator('input[name="framework"]').first.check()

    def upload(name: str, data: bytes) -> None:
        with page.expect_file_chooser() as chosen:
            page.click("#upload")
        chosen.value.set_files({"name": name, "mimeType": "text/plain", "buffer": data})

    upload("call.txt", b"\x89PNG\r\n\x1a\n\x00\x00")
    page.wait_for_selector("#problem", state="visible")
    assert "UTF-8" in page.inner_text("#problem")
    assert page.is_hidden("#transcript-note")

    upload("call.txt", "Ana: the web app talks to a database. — Ben: yes.".encode())
    page.wait_for_selector("#transcript-note", state="visible")
    assert page.inner_text("#transcript-name") == "call.txt"
    page.fill("#analysis-name", "Transcript analysis")
    with page.expect_request("**/analyze") as posted:
        page.click("#go")
    assert posted.value.post_data_json["sources"] == [
        {
            "kind": "transcript",
            "label": "Uploaded transcript",
            "text": "Ana: the web app talks to a database. — Ben: yes.",
        }
    ]
    page.wait_for_selector("#asked", state="visible")


def test_workspace_mobile_has_no_page_overflow(page):
    page.set_viewport_size({"width": 390, "height": 844})
    pause(page)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.click("#description-nav")
    assert page.is_visible("#description-panel")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_workspace_reopens_selected_framework_options(page):
    page.check('input[name="framework"][value="asvs"]')
    page.select_option('select[data-option="level"]', "2")
    pause(page)
    page.reload()
    page.wait_for_selector("#asked", state="visible")
    page.click("#description-nav")
    assert not page.is_checked('input[name="framework"][value="stride"]')
    assert page.is_checked('input[name="framework"][value="asvs"]')
    assert page.input_value('select[data-option="level"]') == "2"


def test_an_unticked_framework_shows_its_options_disabled(page):
    """A submitter sees a framework's options before ticking it (#1541)."""
    level = page.locator('select[data-option="level"]')
    assert not page.is_checked('input[name="framework"][value="asvs"]'), "a control"
    assert level.is_visible()
    assert level.is_disabled()
    page.check('input[name="framework"][value="asvs"]')
    assert level.is_enabled()
    page.select_option('select[data-option="level"]', "3")
    page.uncheck('input[name="framework"][value="asvs"]')
    assert level.is_visible() and level.is_disabled()
    page.check('input[name="framework"][value="asvs"]')
    assert page.input_value('select[data-option="level"]') == "3", "kept on re-tick"


def test_question_guidance_without_a_misleading_progress_count(page):
    assert page.locator('input[name="framework"]:checked').count() == 0
    help = page.locator(".ask-with-help .question-help")
    assert help.locator("summary").text_content().strip() == "How questions work"
    assert not help.locator(".question-help-content").is_visible()
    help.locator("summary").click()
    assert help.locator(".question-help-content").is_visible()
    help.locator("summary").click()
    assert not help.locator(".question-help-content").is_visible()
    assert page.get_by_label("Ask me questions to make the report better.").is_checked()
    pause(page)
    note = "Questions are dynamically generated. Your answers may generate more or answer several at once."
    assert page.locator(".question-note > p:first-child").inner_text() == note
    assert page.locator("#answer-progress-bar").count() == 0
    control = page.locator('#questions select:has(option[value="yes"])').first
    control.select_option("yes")
    assert page.locator(".question-note > p:first-child").inner_text() == note
    assert (
        "Is the connection initiated by Web App to Orders DB encrypted?"
        in page.inner_text("#questions")
    )


def test_reopening_a_completed_analysis_displays_its_report(page):
    pause(page)
    analysis_url = page.url
    page.click("#continue")
    page.wait_for_url("**/report/**")
    report_url = page.url
    page.get_by_role("link", name="About the analysis").click()
    page.get_by_text("How this report was made").click()
    assert "Generated" in page.inner_text("#report-facts")
    page.goto(analysis_url)
    page.wait_for_url(report_url)
    assert page.locator("#sysname").is_visible()
    page.get_by_role("link", name="Reports", exact=True).click()
    page.wait_for_selector("#report-list a")
    assert page.locator("#report-list a").inner_text().startswith("Test analysis")
    assert "Generated " in page.locator("#report-list a").inner_text()


def test_a_question_s_finding_link_opens_its_report_row(page):
    """A Used by link on a question opens the report on that finding, inside
    its closed section, and focuses it (#1884)."""
    pause(page)
    page.click("#continue")
    page.wait_for_url("**/report/**")
    page.get_by_role("link", name=re.compile("^Questions")).click()
    link = page.locator(".used-by a").first
    target = link.get_attribute("href")
    link.click()
    page.locator(target).wait_for(state="visible")
    assert page.locator(target).get_attribute("open") is not None
    assert page.evaluate(
        f"document.querySelector({json.dumps(target)}).contains(document.activeElement)"
    )


def test_a_printed_report_is_the_complete_export(page):
    """A print holds every section, every finding open and every question,
    with no navigation and no editor, in the light palette. After the print,
    the page is as the reader left it (#1884)."""
    pause(page)
    page.click("#continue")
    page.wait_for_url("**/report/**")
    page.click("#themebtn")
    group = page.locator("details.finding-group").first
    assert group.get_attribute("open") is None
    page.emulate_media(media="print")
    for hidden in (
        ".sidebar",
        "#themebtn",
        "#analysis-tabs",
        "#report-tabs",
        "#invite",
        "#preview-bar",
        ".export-bar",
    ):
        assert not page.locator(hidden).is_visible(), hidden
    assert (
        page.locator(".viz-root").evaluate(
            "n => getComputedStyle(n).getPropertyValue('--ink').trim()"
        )
        == "#0b0b0b"
    )
    page.evaluate("dispatchEvent(new Event('beforeprint'))")
    assert group.get_attribute("open") is not None
    for shown in ("#export-questions", "#about-view", "#analyses"):
        assert page.locator(shown).is_visible(), shown
    assert not page.locator("#question-actions").is_visible()
    assert "open this report online" in page.inner_text("#export-questions")
    page.evaluate("dispatchEvent(new Event('afterprint'))")
    assert group.get_attribute("open") is None
    assert not page.locator("#about-view").is_visible()
    assert not page.locator("#export-questions").is_visible()


def test_a_finding_s_question_saves_reloads_and_updates_the_report(page):
    """Finding, question, Save draft, reload, Update report: the draft runs
    nothing, a reload shows it, and the update starts the follow-up (#1884)."""
    pause(page)
    page.click("#continue")
    page.wait_for_url("**/report/**")
    page.locator(
        "details.finding-group > summary", has_text="Conditional findings"
    ).click()
    finding = page.locator("details.finding-group details.finding").first
    finding.locator("> summary").click()
    finding.locator(".field ul a").first.click()
    page.wait_for_url("**?view=questions")
    focused = page.evaluate_handle("document.activeElement")
    key = focused.evaluate("n => n.dataset.key")
    tag = focused.evaluate("n => n.tagName")
    if tag == "SELECT":
        value = focused.evaluate(
            "n => [...n.options].map(o => o.value).find(v => v && v !== 'unknown')"
        )
        focused.as_element().select_option(value)
    else:
        value = "mutual TLS"
        focused.as_element().fill(value)
        focused.as_element().dispatch_event("input")
    assert "Unsaved changes" in page.inner_text("#question-actions")
    with page.expect_request("**/answer/*") as saved:
        page.get_by_role("button", name="Save draft").click()
    assert saved.value.post_data_json["save"] is True
    page.wait_for_selector("text=Saved draft: 1 answer", state="attached")
    page.reload()
    escaped = key.replace(chr(34), chr(92) + chr(34))
    restored = page.locator(f':is(input, select)[data-key="{escaped}"]').first
    assert restored.input_value() == value
    first = page.url.split("?")[0]
    with page.expect_request("**/answer/*") as sent:
        page.get_by_role("button", name="Update report").click()
    assert "save" not in sent.value.post_data_json
    page.wait_for_url(lambda url: "/report/" in url and url.split("?")[0] != first)


def test_the_export_preview_restores_what_the_reader_had_open(page):
    """Preview complete export opens every section and record, and Close
    preview returns to the section and the disclosures the reader had."""
    pause(page)
    page.click("#continue")
    page.wait_for_url("**/report/**")
    page.locator(
        "details.finding-group > summary", has_text="Conditional findings"
    ).click()
    opened = page.evaluate(
        "[...document.querySelectorAll('details[open]')].map(d => d.id || d.className)"
    )
    page.click("#export-preview")
    assert page.locator("#preview-bar").is_visible()
    assert (
        page.evaluate(
            "document.querySelectorAll('#report-view details:not([open])').length"
        )
        == 0
    )
    assert page.locator("#about-view").is_visible()
    page.click("#export-close")
    assert (
        page.evaluate(
            "[...document.querySelectorAll('details[open]')].map(d => d.id || d.className)"
        )
        == opened
    )
    assert page.locator("#analyses").is_visible()
    assert not page.locator("#about-view").is_visible()
    assert page.evaluate("document.activeElement.id") == "export-preview"


@pytest.mark.parametrize("width", [375, 320])
def test_each_report_section_fits_a_phone_without_sideways_scroll(
    browser, served, width
):
    """No section of the report is wider than a phone's screen (#1884)."""
    page = browser.new_page(base_url=served, viewport={"width": width, "height": 800})
    page.goto("/")
    pause(page)
    page.click("#continue")
    page.wait_for_url("**/report/**")
    report = page.url.split("?")[0]
    for view in ("", "?view=questions", "?view=about"):
        page.goto(report + view)
        assert page.evaluate("document.documentElement.scrollWidth") <= width, view
    page.close()


def test_the_report_follows_a_dark_appearance(browser, served):
    """In a dark appearance the report's ink is light, and a print stays light."""
    page = browser.new_page(base_url=served, color_scheme="dark")
    page.goto("/")
    pause(page)
    page.click("#continue")
    page.wait_for_url("**/report/**")
    ink = "n => getComputedStyle(n).getPropertyValue('--ink').trim()"
    assert page.locator(".viz-root").evaluate(ink) == "#ffffff"
    page.emulate_media(media="print")
    assert page.locator(".viz-root").evaluate(ink) == "#0b0b0b"
    page.close()


def test_a_gap_s_answer_link_opens_its_question(page):
    """An Answer link in the model's gaps opens Questions on the question's
    group, with the focus on its input."""
    pause(page)
    page.click("#continue")
    page.wait_for_url("**/report/**")
    page.goto(page.url + "?view=about")
    page.locator("#model-gaps a", has_text="Answer").first.click()
    page.wait_for_url("**?view=questions")
    assert page.locator("#questions-view").is_visible()
    assert not page.locator("#about-view").is_visible()
    assert page.evaluate(
        "document.getElementById('list').contains(document.activeElement)"
    )


def test_a_report_sits_in_the_workspace_with_its_analysis_tabs(page):
    """The report shows the workspace sidebar, and its Description tab opens
    the analysis at its description without a return to the report."""
    pause(page)
    page.click("#continue")
    page.wait_for_url("**/report/**")
    assert page.locator(".sidebar").is_visible()
    assert page.locator("#reports-tab").get_attribute("aria-current") == "page"
    page.click("#sidebar-toggle")
    assert page.locator(".workspace.sidebar-collapsed").count() == 1
    page.click("#description-tab")
    page.wait_for_url("**view=description")
    page.locator("#description-panel").wait_for(state="visible")
    assert page.input_value("#description")
    assert "/report/" not in page.url


def test_reopening_an_analysis_whose_report_is_withheld_stays_on_it(page, monkeypatch):
    """The page goes to a report only where the snapshot lists it, so a
    withheld report never leads to a 404."""
    from analysis_service.job_answers import JobAnswers

    pause(page)
    analysis_url = page.url
    page.click("#continue")
    page.wait_for_url("**/report/**")
    monkeypatch.setattr(JobAnswers, "withheld", lambda self, record: object())

    page.goto(analysis_url)

    page.wait_for_selector("#reports-panel", state="visible", timeout=5000)
    assert "/report/" not in page.url
    assert "withheld" in page.inner_text("#workspace-message")
    assert page.locator("#report-list a").count() == 0


def test_a_320px_submitter_answers_and_skips_without_sideways_scroll(browser, served):
    """The narrowest layout the issue names: every control reachable, no
    horizontal scroll, an answer saved and a question skipped (#1632)."""
    context = browser.new_context(
        base_url=served, viewport={"width": 320, "height": 568}
    )
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto("/")
    pause(page)
    wide = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    choose(page, page.locator('#list p select:has(option[value="yes"])').first, "yes")
    with page.expect_response(_post_to_answer):
        page.locator("#list button", has_text="Skip for now").first.click()
    saved(page)
    context.close()
    assert errors == [], f"the page raised: {errors}"
    assert wide <= 0, f"the page scrolls sideways by {wide}px at 320px"


def _tab_to(page, reached: str, limit: int = 400) -> None:
    """Press Tab until ``reached``, a script over the focused element, holds."""
    for _ in range(limit):
        page.keyboard.press("Tab")
        if page.evaluate(
            f"(active) => {reached}", page.evaluate_handle("document.activeElement")
        ):
            return
    pytest.fail(f"Tab never reached: {reached}")


def test_a_keyboard_answers_skips_and_starts_with_no_pointer(page):
    """Enter opens a group, Tab reaches each control, an arrow key answers,
    and Enter skips and starts (#1632)."""
    pause(page, opened=False)
    _tab_to(page, "active.matches('.question-group > summary')")
    page.keyboard.press("Enter")
    assert page.evaluate("document.activeElement.parentElement.open")
    _tab_to(
        page,
        "active.tagName === 'SELECT' && [...active.options].some((o) => o.value === 'yes')",
    )
    with page.expect_response(_post_to_answer) as answered:
        page.keyboard.press("ArrowDown")
    assert answered.value.ok, answered.value.text()
    saved(page)
    # The answered group can hold no other question; Enter opens the next one.
    _tab_to(
        page,
        "active.textContent === 'Skip for now'"
        " || active.matches('.question-group:not([open]) > summary')",
    )
    if page.evaluate("document.activeElement.tagName === 'SUMMARY'"):
        page.keyboard.press("Enter")
        _tab_to(page, "active.textContent === 'Skip for now'")
    with page.expect_response(_post_to_answer) as skipped:
        page.keyboard.press("Enter")
    assert skipped.value.request.post_data_json["skip"], "the skip names a question"
    saved(page)
    _tab_to(page, "active.id === 'continue'")
    page.keyboard.press("Enter")
    page.wait_for_url("**/report/**")


#: A capability and a question kind: the open facts no "<attribute> on
#: <element>" wording can name.
WAITS_ON = (
    {"capability": "browser-frontend"},
    {"element_id": "process:web-app", "question": "audit-evidence"},
)


class _WaitingAsvsRunner(web.PausingRunner):
    """Completes with an ASVS ruling that waits on :data:`WAITS_ON`."""

    def _stub_block(self, selection):
        from analysis_service.claims import UnknownRef, Verdict
        from tests.test_asvs import _block, sample_asvs_claim

        if selection.name != "asvs":
            return super()._stub_block(selection)
        waiting = sample_asvs_claim().model_copy(
            update={
                "grounds": [],
                "verdict": Verdict(
                    status="needs-info",
                    reason="The sources do not state this.",
                    related_unknowns=[UnknownRef(**ref) for ref in WAITS_ON],
                ),
            }
        )
        return _block(selection.options["level"], [waiting])


@pytest.fixture
def waiting_asvs(tiers):
    """The first-run app over :class:`_WaitingAsvsRunner`."""
    app = web.app_for(tiers, _WaitingAsvsRunner(catalog=False), catalog=False)
    yield from serve(app)


def test_a_report_names_each_open_fact_by_its_label(browser, waiting_asvs):
    """The card and the requirement row name each open fact by the label the
    server computes, never as "<attribute> on <element>"."""
    from analysis_service.claims import UnknownRef
    from analysis_service.open_facts import label_of

    context = browser.new_context(base_url=waiting_asvs)
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto("/")
    pause(page)
    page.click("#continue")
    page.wait_for_url("**/report/**")

    labels = [
        label_of(UnknownRef(**ref), {"process:web-app": "Web App"}) for ref in WAITS_ON
    ]
    unit = page.locator("details.unit", has_text="Still to answer")
    unit.locator("summary").click()
    row = unit.locator("div.lbl:text-is('Still to answer') + p").all_inner_texts()
    card = page.locator(".finding .field", has_text=labels[0]).first.locator("li")
    listed = [text.split(" — ")[0] for text in card.all_text_contents()]
    context.close()

    assert errors == [], f"the page raised: {errors}"
    assert row == labels
    assert listed == labels


#: Principals named as the held model's elements are, so each link row offers
#: its element with the arrow.
_MATCHED = ("customer", "orders db", "web app")


@pytest.fixture
def matched(tiers, monkeypatch):
    """The first-run app over a paused job that asks one link question for
    each of :data:`_MATCHED`, served on a free local port."""
    subjects = [
        Subject(id=f"principal:{name.replace(' ', '-')}", type="principal", label=name)
        for name in _MATCHED
    ]
    entries = [
        Assertion(
            subject=subject.id,
            predicate="mfa-requirement",
            value=ABSENT,
            basis="inferred",
            origin="assertion-pass",
            explanation="the description names one factor",
        )
        for subject in subjects
    ]
    record = web.HELD.assertions.model_copy(
        update={"catalog": AssertionCatalog(subjects=subjects, entries=entries)}
    )
    monkeypatch.setattr(web, "HELD", web.HELD.model_copy(update={"assertions": record}))
    yield from serve(web.app_for(tiers, web.PausingRunner(catalog=True)))


def test_each_arrow_answers_and_closes_its_own_row(browser, matched):
    """A click on a row's arrow chooses its element, and the row closes to its
    answer, the last row too, with no click on another row."""
    context = browser.new_context(base_url=matched)
    page = context.new_page()
    page.goto("/")
    pause(page)
    for name in sorted(_MATCHED):
        row = page.locator(
            "p.question", has=page.locator(f'select[data-principal="{name}"]')
        )
        with page.expect_response(_post_to_answer):
            row.locator(".use-match").click()
        closed = page.locator("p.question", has_text=f"{name} — ")
        closed.locator("button", has_text="Change").wait_for()
    assert page.locator(".use-match").count() == 0
    context.close()


def _drawn(browser, report):
    """A rendered report's About section in a browser, and its script errors."""
    from tests.factories import report_state
    from webapp.main import render_report

    page = browser.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    html = render_report(report, report_state(report), previous=None, parent=None).html
    page.route(
        "http://report.test/report/r1",
        lambda route: route.fulfill(body=html, content_type="text/html"),
    )
    page.goto("http://report.test/report/r1")
    page.click("#about-tab")
    return page, errors


#: Each drawn element's box and the box of the zone it is drawn in.
_PLACED = """() => [...document.querySelectorAll('#model-diagram g.element')].map(g => {
  const zone = g.dataset.zone;
  const band = [...document.querySelectorAll('#model-diagram g.zone')]
    .find(z => z.dataset.zone === zone).querySelector('rect').getBBox();
  const box = g.querySelector('rect').getBBox();
  return { id: g.dataset.element, zone, inside: box.x >= band.x
    && box.x + box.width <= band.x + band.width && box.y >= band.y
    && box.y + box.height <= band.y + band.height, text: g.textContent };
})"""


def test_the_diagram_draws_each_element_in_its_zone_and_each_flow_once(browser):
    """Every element once, inside the box of the zone the model states, and
    every flow once, marked where it crosses a boundary (#1884)."""
    from tests.factories import sample_report

    report = sample_report()
    model = report.system_model
    page, errors = _drawn(browser, report)
    placed = page.evaluate(_PLACED)
    flows = page.evaluate(
        "[...document.querySelectorAll('#model-diagram path.flow')]"
        ".map(p => [p.dataset.flow, p.classList.contains('crosses')])"
    )
    page.close()

    assert errors == []
    zoned = {e.id: e.trust_zone for e in model.zoned_elements()}
    assert sorted(p["id"] for p in placed) == sorted(zoned)
    assert all(p["inside"] and p["zone"] == zoned[p["id"]] for p in placed)
    crossing = {c.flow_id for c in report.boundary_crossings if c.decided}
    assert sorted(flows) == sorted([f.id, f.id in crossing] for f in model.data_flows)


def test_the_diagram_places_no_element_in_a_zone_the_model_does_not_state(browser):
    """Placement is stated, never inferred: an element with no stated zone is
    drawn outside every zone, and an inferred zone says so (#1884)."""
    from analysis_service.system_model import Assumption
    from tests.factories import sample_report

    report = sample_report()
    model = report.system_model
    store, process = model.data_stores[0], model.processes[0]
    model = model.model_copy(
        update={
            "data_stores": [store.model_copy(update={"trust_zone": "unknown"})],
            "assumptions": [
                *model.assumptions,
                Assumption(
                    assumption="placed by its role",
                    element_id=process.id,
                    attribute="trust_zone",
                    basis="inferred from the role",
                ),
            ],
        }
    )
    report = report.model_copy(
        update={"system_model": model, "boundary_crossings": model.boundary_crossings()}
    )
    page, errors = _drawn(browser, report)
    placed = {p["id"]: p for p in page.evaluate(_PLACED)}
    unplaced = page.locator("#model-diagram g.zone.unplaced").text_content()
    dotted = page.evaluate(
        "[...document.querySelectorAll('#model-diagram path.flow.undecided')]"
        ".map(p => p.dataset.flow)"
    )
    page.close()

    assert errors == []
    assert placed[store.id]["zone"] == "" and placed[store.id]["inside"]
    assert unplaced == "Zone not stated"
    assert "zone assumed" in placed[process.id]["text"]
    assert sorted(dotted) == sorted(
        c.flow_id for c in report.boundary_crossings if not c.decided
    )


def test_the_diagram_s_flows_and_labels_stay_clear_on_every_archived_model(browser):
    """On each distinct model the archive holds, no flow label covers an
    element or another label, and no flow passes over an element it does not
    join."""
    import glob

    from analysis_service.report import Report

    reports = {}
    for path in sorted(
        glob.glob(
            str(Path(__file__).parents[1] / "evals/baselines/*/*.reports/*.report.json")
        )
    ):
        report = Report.model_validate_json(Path(path).read_text())
        reports.setdefault(report.system_model.model_dump_json(), (path, report))
    found = {}
    for model, (path, report) in reports.items():
        page, errors = _drawn(browser, report)
        faults = page.evaluate(
            """(model) => {
          const boxes = s => [...document.querySelectorAll(s)].map(n => n.getBBox());
          const meet = (a, b) => a.x < b.x + b.width && b.x < a.x + a.width
            && a.y < b.y + b.height && b.y < a.y + a.height;
          const labels = boxes('#model-diagram .flow-label-box');
          const elements = [...document.querySelectorAll('#model-diagram g.element')]
            .map(g => ({ id: g.dataset.element, box: g.querySelector('rect').getBBox() }));
          const flows = Object.fromEntries(model.data_flows.map(f => [f.id, f]));
          const over = [...document.querySelectorAll('#model-diagram path.flow')].flatMap(path => {
            const f = flows[path.dataset.flow];
            const length = path.getTotalLength();
            return elements.filter(e => e.id !== f.source && e.id !== f.destination)
              .filter(e => Array.from({ length: 50 }, (_, k) => path.getPointAtLength(length * k / 49))
                .some(p => p.x > e.box.x && p.x < e.box.x + e.box.width
                  && p.y > e.box.y && p.y < e.box.y + e.box.height))
              .map(e => `${f.id} over ${e.id}`);
          });
          return {
            covered: labels.filter(l => elements.some(e => meet(l, e.box))).length,
            stacked: labels.filter((l, at) => labels.some((m, other) => other !== at && meet(l, m))).length,
            over,
          };
        }""",
            json.loads(model),
        )
        page.close()
        assert errors == [], path
        if faults != {"covered": 0, "stacked": 0, "over": []}:
            found[Path(path).name] = faults

    assert found == {}
