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
import socket
import threading
import time

import pytest

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


def pause(page) -> None:
    """Submit a description that asks questions, and wait for the list."""
    page.fill("#description", "A web app talks to a database.")
    page.check("#ask")
    if not page.locator('input[name="framework"]:checked').count():
        for framework in page.locator('input[name="framework"]').all():
            framework.check()
    page.click("#go")
    page.wait_for_selector("#asked", state="visible")


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
    report = page.inner_text("body")
    assert "What remains open" in report
    assert "neither confirmed nor cleared" in report


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
        self.release = threading.Event()

    async def run(self, job, on_node):
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
        assert tab.is_enabled()

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
    followup = page.locator("details.followup")
    followup.locator("summary").click()
    earlier = followup.locator("p", has=page.locator("button", has_text="Change"))
    for index in range(earlier.count()):
        earlier.nth(index).locator("button").click()
    offered = earlier.locator("option").evaluate_all("o => o.map(n => n.value)")
    assert "no" in offered
    assert "unknown" not in offered
    assert earlier.locator("label", has_text="I don't know").count() == 0


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
    followup = page.locator("details.followup")
    followup.locator("summary").click()
    row = followup.locator("p", has=page.locator("button", has_text="Change")).first
    row.locator("button").click()
    row.locator("select").select_option("yes")
    snapshots = []
    page.on(
        "request",
        lambda request: (
            snapshots.append(request.url) if "/workspace/runs/" in request.url else None
        ),
    )
    with page.expect_request("**/answer/*") as sent:
        page.click("text=Run the follow-up with these answers")
    assert any(
        fact.get("value") == "yes" for fact in sent.value.post_data_json["facts"]
    )
    page.wait_for_url(lambda url: "/report/" in url and url != first)
    assert len(snapshots) == 1, "Live progress must not also start reopening the run"
    return page.url


def test_a_first_report_sends_a_changed_answer_and_reaches_the_final_report(page):
    final_report(page)
    assert page.locator("summary", has_text="Correct an answer").count() == 1
    assert page.locator("text=Run the follow-up with these answers").count() == 0


def test_a_final_report_saves_a_correction(page):
    final_report(page)
    corrections = page.locator("details.followup", has_text="Correct an answer")
    corrections.locator("summary").click()
    row = corrections.locator("p", has=page.locator("button", has_text="Change")).first
    row.locator("button").click()
    row.locator("select").select_option("unknown")
    with page.expect_request("**/correct/*") as sent:
        page.click("text=Save the corrections")
    assert sent.value.post_data_json["facts"][0]["value"] == "unknown"
    page.wait_for_selector("text=(corrected after this report)", state="attached")


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
    assert page.locator("#analysis-list img").count() == 0
    page.locator("#analysis-list a").click()
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


def test_question_progress_and_entry_guidance(page):
    assert page.locator('input[name="framework"]:checked').count() == 0
    assert (
        page.locator("#description-panel summary")
        .filter(has_text="How questions work")
        .count()
        == 1
    )
    assert page.get_by_label("Ask me questions to make the report better.").is_checked()
    pause(page)
    assert page.locator("#round-progress-bar").get_attribute("value") == "0"
    control = page.locator('#questions select:has(option[value="yes"])').first
    control.select_option("yes")
    assert float(page.locator("#round-progress-bar").get_attribute("value")) >= 1
    assert "Is the connection Web App → Orders DB encrypted?" in page.inner_text(
        "#questions"
    )


def test_reopening_a_completed_analysis_displays_its_report(page):
    pause(page)
    analysis_url = page.url
    page.click("#continue")
    page.wait_for_url("**/report/**")
    report_url = page.url
    assert "Generated " in page.inner_text("#jobmeta")
    page.goto(analysis_url)
    page.wait_for_url(report_url)
    assert page.locator("#sysname").is_visible()
    page.get_by_role("link", name="All reports", exact=True).click()
    page.wait_for_selector("#report-list a")
    assert page.locator("#report-list a").inner_text().startswith("Your system")
    assert "Generated " in page.locator("#report-list a").inner_text()


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
    """Tab reaches each control, an arrow key answers, and Enter skips and
    starts (#1632)."""
    pause(page)
    _tab_to(
        page,
        "active.tagName === 'SELECT' && [...active.options].some((o) => o.value === 'yes')",
    )
    with page.expect_response(_post_to_answer) as answered:
        page.keyboard.press("ArrowDown")
    assert answered.value.ok, answered.value.text()
    saved(page)
    _tab_to(page, "active.textContent === 'Skip for now'")
    with page.expect_response(_post_to_answer) as skipped:
        page.keyboard.press("Enter")
    assert skipped.value.request.post_data_json["skip"], "the skip names a question"
    saved(page)
    _tab_to(page, "active.id === 'continue'")
    page.keyboard.press("Enter")
    page.wait_for_url("**/report/**")
