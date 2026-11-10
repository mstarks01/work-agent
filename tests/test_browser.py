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
from contextlib import contextmanager
from pathlib import Path

import pytest

from analysis_service.assertions import ABSENT, Assertion, AssertionCatalog, Subject
from analysis_service.jobs import Checkpoint, PipelineAwaiting
from analysis_service.owner_files import NOT_UTF_8
from analysis_service.system_model import (
    SystemModel,
    derive_element_id,
    make_element_id,
)
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
    # BROWSER_TIMEOUT_MS shortens each wait, for a run that expects tests to
    # fail, such as the run of new tests against the code before a change.
    if timeout := os.environ.get("BROWSER_TIMEOUT_MS"):
        context.set_default_timeout(int(timeout))
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


def open_frameworks(page) -> None:
    """Open the frameworks menu, so its rows take a tick."""
    if page.get_attribute("#frameworks-toggle", "aria-expanded") == "false":
        page.click("#frameworks-toggle")


def close_frameworks(page) -> None:
    """Close the frameworks menu, which lies over the column under it."""
    page.press("#frameworks-toggle", "Escape")


def pause(page, opened: bool = True) -> None:
    """Submit a description that asks questions, wait for the list, and open
    its groups unless ``opened`` is false."""
    if not page.locator("#analysis-name").input_value():
        page.fill("#analysis-name", "Test analysis")
    page.fill("#description", "A web app talks to a database.")
    page.check("#ask")
    if not page.locator('input[name="framework"]:checked').count():
        open_frameworks(page)
        for framework in page.locator('input[name="framework"]').all():
            framework.check()
        close_frameworks(page)
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


def test_the_theme_switch_sets_the_theme_on_every_page(page):
    """The sidebar's theme switch sets the theme, its pressed button names the
    theme the page shows, and each page the reader opens next draws in it."""
    page.emulate_media(color_scheme="light")
    light_button = page.get_by_role("button", name="Light theme")
    dark_button = page.get_by_role("button", name="Dark theme")
    assert light_button.get_attribute("aria-pressed") == "true"
    assert dark_button.get_attribute("aria-pressed") == "false"
    canvas = "getComputedStyle(document.body).backgroundColor"
    light = page.evaluate(canvas)
    dark_button.click()
    assert dark_button.get_attribute("aria-pressed") == "true"
    assert light_button.get_attribute("aria-pressed") == "false"
    dark = page.evaluate(canvas)
    assert dark != light
    for path in ("/settings", "/files", "/controls"):
        page.goto(path)
        assert page.evaluate("document.documentElement.dataset.theme") == "dark"
        assert page.evaluate(canvas) == dark
        assert (
            page.get_by_role("button", name="Dark theme").get_attribute("aria-pressed")
            == "true"
        )
    page.get_by_role("button", name="Light theme").click()
    page.goto("/")
    assert page.evaluate(canvas) == light


def test_settings_and_the_theme_switch_sit_at_the_foot_of_the_sidebar(page):
    """Settings leaves the menu for the foot of the sidebar, beside the theme
    switch, and the foot stays at the bottom of the window as the page
    scrolls."""
    page.set_viewport_size({"width": 1280, "height": 800})
    assert page.locator("#sidebar-navigation a[href='/settings']").count() == 0
    foot = page.locator(".sidebar-foot")
    assert foot.get_by_role("link", name="Settings").count() == 1
    assert foot.get_by_role("group", name="Theme").count() == 1
    menu = page.locator("#sidebar-navigation").bounding_box()
    box = foot.bounding_box()
    assert menu and box
    assert box["y"] > menu["y"] + menu["height"]
    assert 800 - (box["y"] + box["height"]) <= 40
    page.goto("/settings")
    page.wait_for_selector("#settings-rows tr[data-key]")
    height = page.evaluate("document.documentElement.scrollHeight")
    assert height > 1600, "a page that scrolls past the window"
    page.evaluate(f"window.scrollTo(0, {height // 2 - 400})")
    box = page.locator(".sidebar-foot").bounding_box()
    assert box and 800 - (box["y"] + box["height"]) <= 40
    assert page.get_attribute("#settings-nav", "aria-current") == "page"


def test_the_report_draws_in_the_chosen_theme(page):
    """The report's own palette obeys the sidebar's theme switch."""
    page.emulate_media(color_scheme="light")
    pause(page)
    page.click("#continue")
    page.wait_for_url("**/report/**")
    ink = "n => getComputedStyle(n).getPropertyValue('--ink').trim()"
    light = page.locator(".viz-root").evaluate(ink)
    page.get_by_role("button", name="Dark theme").click()
    page.reload()
    assert page.locator(".viz-root").evaluate(ink) != light


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


def test_each_question_control_names_its_question(page):
    """A screen reader names every input of the list by its question, from the
    shared editors and not from a patch after the list is drawn."""
    pause(page)
    unnamed = page.evaluate(
        """() => [...document.querySelectorAll("#list select, #list input")]
          .filter((c) => !c.labels?.length && !c.getAttribute("aria-label"))
          .map((c) => c.outerHTML.slice(0, 80))"""
    )
    assert unnamed == []


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
    help = page.locator(".ask-with-help .question-help summary").bounding_box()
    assert ask_row and reuse_row and help
    assert reuse_row["y"] >= ask_row["y"] + ask_row["height"]
    assert ask_row["y"] <= help["y"] < ask_row["y"] + ask_row["height"]
    earlier = page.locator("#answers-from option").nth(1).get_attribute("value")
    page.select_option("#answers-from", earlier)
    pause(page)
    page.click("#earlier summary")
    assert "yes" in page.inner_text("#earlier")
    assert page.locator(f'#list select[aria-label="{label}"]').count() == 0


def _renamed_store_model():
    """The paused model with its store named "Orders Database", and each flow
    ID built again from the renamed endpoint."""
    model = web.HELD.system_model.model_copy(deep=True)
    old = model.data_stores[0].id
    model.data_stores[0].name = "Orders Database"
    model.data_stores[0].id = make_element_id("store", "Orders Database")
    for flow in model.data_flows:
        if flow.source == old:
            flow.source = model.data_stores[0].id
        if flow.destination == old:
            flow.destination = model.data_stores[0].id
        flow.id = derive_element_id(flow)
    return SystemModel.model_validate(model.model_dump())


class RenamingRunner(web.PausingRunner):
    """Pauses the second job on a model whose store has another name."""

    def __init__(self) -> None:
        super().__init__(catalog=False)
        self.pauses = 0

    async def run(self, job, on_node):
        if job.pauses():
            self.pauses += 1
            if self.pauses == 2:
                renamed = Checkpoint(
                    system_model=_renamed_store_model(), assertions=None
                )
                return PipelineAwaiting(checkpoint=renamed)
        return await super().run(job, on_node)


def test_an_owner_maps_a_carried_answer_to_the_renamed_part(browser, tiers):
    """The earlier analysis answered a fact about "Orders DB". The new one
    names the store "Orders Database", so the answer waits until the owner
    says which part it is now (ADR 0116)."""
    errors: list[str] = []
    for base in serve(web.app_for(tiers, RenamingRunner(), catalog=False)):
        context = browser.new_context(base_url=base)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto("/")
        pause(page)
        page.goto("/")
        page.wait_for_selector("#answers-from-field", state="visible")
        earlier = page.locator("#answers-from option").nth(1).get_attribute("value")
        key = [
            web.HELD.system_model.data_stores[0].id,
            "encryption_at_rest",
            "",
            "",
            "",
            "",
        ]
        status = page.evaluate(
            """async ([run, key]) => (await fetch("/answer/" + run, {
                method: "POST", headers: {"Content-Type": "application/json"},
                body: JSON.stringify({links: [], facts: [{key, value: "AES-256"}],
                  save: true, skip: [], presented: [], revision: 0})})).status""",
            [earlier, key],
        )
        assert status < 300
        page.select_option("#answers-from", earlier)
        pause(page)
        box = page.locator("div.rename")
        box.wait_for(state="visible")
        assert "AES-256" in box.inner_text()
        box.locator("select").select_option("store:orders-database")
        with page.expect_response(_post_to_answer) as done:
            box.get_by_role("button", name="Keep these answers").click()
        assert done.value.ok, done.value.text()
        page.wait_for_selector("div.rename", state="detached")
        assert "now belong to Orders Database" in page.inner_text("#questions")
        row = page.locator("#list .question", has_text="data stored in Orders Database")
        assert "AES-256" in row.inner_text(), "the row shows the carried answer"
        context.close()
        assert errors == [], f"the page raised: {errors}"


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
        open_frameworks(opened)
        for framework in opened.locator('input[name="framework"]').all():
            framework.check()
        close_frameworks(opened)
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
        assert opened.locator("#upload-file").is_disabled()
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


def test_one_shared_answer_saves_the_answer_for_every_element_ticked(page):
    """A box asks its question once (ADR 0081): one choice in the shared
    control saves that answer for each element a person ticks (ADR 0113)."""
    pause(page)
    shared = page.locator("#list .shared-controls select").first
    shared.wait_for()
    box = shared.locator("xpath=ancestor::details[contains(@class, 'question-group')]")
    for tick in box.locator("input.share-answer").all():
        tick.check()
    keys = box.locator(".each-one select[data-facet]")
    facet = shared.get_attribute("data-facet")
    with page.expect_response(_post_to_answer) as response:
        shared.select_option("yes")
    assert response.value.ok
    body = response.value.request.post_data_json
    elements = keys.evaluate_all(
        "(selects, facet) => [...new Set(selects.filter(s => s.dataset.facet === facet)"
        ".map(s => JSON.parse(s.dataset.key)[0]))]",
        facet,
    )
    assert len(elements) >= 2
    # One share for the box (ADR 0114), and no answer of each part's own.
    [share] = body["shares"]
    assert body["facts"] == []
    assert sorted(share["covers"]) == sorted(elements)
    assert share["answer"]["facets"] == {facet: "yes"}
    assert share["answer"]["key"][0] == ""


def test_cover_every_part_waits_for_a_shared_answer(page):
    """The button "Cover every part" is disabled while the shared answer is
    empty, so a click copies no empty value. A choice enables it (ADR 0113)."""
    pause(page)
    shared = page.locator("#list .shared-controls select").first
    shared.wait_for()
    box = shared.locator("xpath=ancestor::details[contains(@class, 'question-group')]")
    cover = box.get_by_role("button", name="Cover every part")
    assert cover.is_disabled()
    shared.select_option("yes")
    assert cover.is_enabled()
    shared.select_option("")
    assert cover.is_disabled()
    ticks = box.locator("input.share-answer").all()
    assert not any(tick.is_checked() for tick in ticks)


def test_a_ticked_choice_row_keeps_its_title_beside_the_tick(page):
    """On a wide page a choice row is "split", and a row of a shared box has a
    tick. The title sits in the column after the tick, at its full width."""
    page.set_viewport_size({"width": 1280, "height": 900})
    pause(page)
    page.locator("#list .each-one").first.wait_for()
    title, tick = page.evaluate(
        """() => {
          const row = document.createElement("p");
          row.className = "question";
          row.dataset.layout = "split";
          const tick = document.createElement("input");
          tick.type = "checkbox";
          tick.className = "share-answer";
          const title = document.createElement("div");
          title.className = "question-title";
          title.append(document.createElement("b"));
          title.firstChild.textContent = "What is the trust zone for this part?";
          const fields = document.createElement("div");
          fields.className = "answer-fields";
          fields.append(document.createElement("select"));
          row.append(tick, title, fields);
          document.querySelector("#list .each-one").append(row);
          const box = (node) => node.getBoundingClientRect().toJSON();
          return [box(title), box(tick)];
        }"""
    )
    assert title["left"] >= tick["right"]
    assert title["height"] < 40, "the title wraps in the tick's column"


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


@pytest.fixture
def hanging(tiers):
    """The first-run app over a run that never ends, served on a free port."""
    from tests.test_jobs import HangingRunner

    yield from serve(web.app_for(tiers, HangingRunner()))


def test_the_stop_buttons_stop_a_running_analysis(browser, hanging):
    """The row's Stop and the progress panel's Stop analysis each end a run
    that would otherwise never end (ADR 0109)."""
    context = browser.new_context(base_url=hanging)
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto("/")
    submitted(page, "Hangs")
    page.goto("/?view=analyses")
    page.click('[aria-label="Stop Hangs"]')
    page.wait_for_selector("#analysis-rows .badge:text-is('Stopped')")
    assert page.inner_text("#workspace-message") == "Stopped “Hangs”."
    assert page.locator('[aria-label="Stop Hangs"]').count() == 0

    submitted(page, "Hangs again")
    page.goto("/?view=analyses")
    page.click("#analysis-rows a:text-is('Hangs again')")
    page.wait_for_selector("#stop-run", state="visible")
    page.click("#stop-run")
    page.wait_for_selector("#analysis-state:text-is('Stopped')")
    assert page.inner_text("#workspace-message").startswith("You stopped the analysis.")
    context.close()
    assert errors == [], f"the page raised: {errors}"


def test_a_redraw_during_a_stop_keeps_its_button_off(browser, hanging):
    context = browser.new_context(base_url=hanging)
    page = context.new_page()
    page.goto("/")
    submitted(page, "Hangs")
    page.goto("/?view=analyses")
    held: list = []
    page.route("**/stop", lambda route: held.append(route))
    page.click('[aria-label="Stop Hangs"]')
    for _ in range(100):
        if held:
            break
        page.wait_for_timeout(20)
    page.fill("#analysis-search", "Hang")
    assert page.is_disabled('[aria-label="Stop Hangs"]')
    held[0].continue_()
    page.wait_for_selector("#analysis-rows .badge:text-is('Stopped')")
    context.close()


def _count_reads(page, part: str) -> None:
    """Count in ``window.readsDone`` each answer to a URL that holds
    ``part`` once the page's own handler has read it and run on."""
    page.evaluate(
        """(part) => {
          window.readsDone = 0;
          const send = window.fetch;
          window.fetch = async (...args) => {
            const response = await send(...args);
            if (String(args[0]).includes(part)) {
              const read = response.json.bind(response);
              response.json = async () => {
                const body = await read();
                setTimeout(() => { window.readsDone += 1; }, 0);
                return body;
              };
            }
            return response;
          };
        }""",
        part,
    )


def test_an_older_list_answer_leaves_the_newer_list(page):
    page.goto("/")
    _count_reads(page, "/workspace/runs")
    held: list = []
    page.route(
        "**/workspace/runs",
        lambda route: held.append(route) if not held else route.fallback(),
    )
    page.click("#library-nav")
    for _ in range(100):
        if held:
            break
        page.wait_for_timeout(20)
    page.click("#library-nav")
    page.wait_for_selector("#analysis-empty", state="visible")
    old = {
        "run": "old",
        "name": "An older list",
        "status": "completed",
        "created_at": "2026-11-01T00:00:00Z",
        "frameworks": [],
    }
    held[0].fulfill(json=[old])
    page.wait_for_function("() => window.readsDone >= 2")
    assert page.locator("text=An older list").count() == 0


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
        current = page.locator('#settings-nav[aria-current="page"]')
        assert current.inner_text() == "Settings"
        review = page.locator('tr[data-key="tiers.review.model"]').inner_text()
        sources = page.locator('tr[data-key="resilience.max_sources"]').inner_text()
        location, key = (
            page.locator(f'tr[data-key="{row}"] input').get_attribute("placeholder")
            for row in ("credentials.vertex.location", "credentials.openai.api_key")
        )
        attempts = page.locator('tr[data-key="resilience.attempts"] input')
        bounds = (attempts.get_attribute("min"), attempts.get_attribute("max"))
        attempts.fill("11")
        page.locator("#settings-search").click()
        over = page.locator('tr[data-key="resilience.attempts"] .field-error')
        over_text = over.inner_text()
        attempts.fill("3")
        ratio = page.locator('tr[data-key="resilience.retry_budget_ratio"] input')
        ratio.fill("0")
        page.locator("#settings-search").click()
        floor_text = page.locator(
            'tr[data-key="resilience.retry_budget_ratio"] .field-error'
        ).inner_text()
        ratio.fill("0.1")
        text_bound = page.locator(
            'tr[data-key="credentials.vertex.location"] input'
        ).get_attribute("maxlength")
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
    assert bounds == ("1", "10")
    assert over_text == "Enter a number from 1 to 10."
    assert floor_text == "Enter a number over 0 and up to 1."
    assert text_bound == "4096"


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


def test_the_store_rows_follow_the_unsaved_store_choices(browser, tmp_path):
    """A store choice shows the rows it reads before any save, so one save
    sets the job store and the settings it needs."""
    from tests.test_settings_env import env_in, hand_written, write_settings
    from webapp.main import create_app

    write_settings(tmp_path, hand_written())
    shown = "#settings-rows tr[data-key^='store.']"
    for url in serve(create_app(env=env_in(tmp_path))):
        page, errors = opened(browser, url + "/settings")
        page.wait_for_selector(shown)
        on_default = page.eval_on_selector_all(
            shown, "rows => rows.map(r => r.dataset.key)"
        )
        page.select_option('tr[data-key="store.job_store"] select', "sql")
        page.wait_for_selector('tr[data-key="store.object_store"]')
        page.select_option('tr[data-key="store.object_store"] select', "gcs")
        page.wait_for_selector('tr[data-key="store.object_bucket"]')
        page.locator('tr[data-key="store.database_url"] input').fill(
            "postgresql+asyncpg://analysis@db.example.com/analysis"
        )
        page.locator("#settings-search").click()
        page.wait_for_selector('tr[data-key="store.database_credentials"]')
        on_gcs = page.eval_on_selector_all(
            shown, "rows => rows.map(r => r.dataset.key)"
        )
        page.select_option('tr[data-key="store.job_store"] select', "memory")
        page.wait_for_selector('tr[data-key="store.object_store"]', state="detached")
        listed = page.locator("#changes-list").inner_text()
        page.close()
    assert errors == [], f"the settings page raised: {errors}"
    # With no store setting, the page shows the default SQLite store (ADR 0111).
    assert set(on_default) == {
        "store.job_store",
        "store.retention_days",
        "store.database_url",
        "store.object_store",
        "store.object_root",
    }
    assert {
        "store.database_url",
        "store.database_credentials",
        "store.object_store",
        "store.object_bucket",
        "store.object_credentials",
    } <= set(on_gcs)
    assert "store.object_root" not in on_gcs
    assert "This setting is inactive" in listed


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
    """The menu's upload opens a file chooser, a binary file is refused on the
    page, and a text file goes to ``/analyze`` as the one source, as text."""
    open_frameworks(page)
    page.locator('input[name="framework"]').first.check()
    close_frameworks(page)

    def upload(name: str, data: bytes) -> None:
        page.click("#upload")
        with page.expect_file_chooser() as chosen:
            page.click("#upload-computer")
        chosen.value.set_files({"name": name, "mimeType": "text/plain", "buffer": data})

    upload("call.txt", b"\x89PNG\r\n\x1a\n\x00\x00")
    page.wait_for_selector("#problem", state="visible")
    assert page.inner_text("#problem") == NOT_UTF_8
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


def test_an_uploaded_mermaid_file_starts_an_analysis_as_a_diagram(page):
    """A ``.mmd`` file goes to ``/analyze`` as a ``diagram`` source, and the
    server accepts it."""
    open_frameworks(page)
    page.locator('input[name="framework"]').first.check()
    close_frameworks(page)
    diagram = "flowchart LR\n    user[Customer] -->|HTTPS| app(Web App)\n"
    page.click("#upload")
    with page.expect_file_chooser() as chosen:
        page.click("#upload-computer")
    chosen.value.set_files(
        {"name": "model.mmd", "mimeType": "text/plain", "buffer": diagram.encode()}
    )
    page.wait_for_selector("#diagram-note", state="visible")
    assert page.inner_text("#diagram-name") == "model.mmd"
    assert page.is_hidden("#transcript-note")
    page.fill("#analysis-name", "Diagram analysis")
    with page.expect_request("**/analyze") as posted:
        page.click("#go")
    assert posted.value.post_data_json["sources"] == [
        {"kind": "diagram", "label": "Uploaded diagram", "text": diagram}
    ]
    page.wait_for_selector("#asked", state="visible")


def test_the_settings_column_sits_beside_the_description_and_stacks_on_a_phone(page):
    """The settings column holds the frameworks, the questions box and the
    button. A wide screen shows it beside the description box, and a phone
    shows it under the box."""
    page.set_viewport_size({"width": 1280, "height": 900})
    settings = page.locator("aside.entry-settings")
    for held in ("#frameworks", "#ask", "#go"):
        assert settings.locator(held).count() == 1, held
    box = page.locator(".composer").bounding_box()
    column = settings.bounding_box()
    assert box and column
    assert column["x"] >= box["x"] + box["width"]
    page.set_viewport_size({"width": 390, "height": 844})
    box = page.locator(".composer").bounding_box()
    column = settings.bounding_box()
    assert box and column
    assert column["y"] >= box["y"] + box["height"]
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_the_description_guidance_opens_from_its_help(page):
    """The box holds a short prompt, and its help holds the full guidance."""
    help = page.locator(".label-with-help .question-help")
    assert not help.locator(".question-help-content").is_visible()
    help.locator("summary").click()
    assert "how each side proves who it is" in help.inner_text()
    assert len(page.get_attribute("#description", "placeholder")) < 100


def test_workspace_mobile_has_no_page_overflow(page):
    page.set_viewport_size({"width": 390, "height": 844})
    pause(page)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.click("#description-nav")
    assert page.is_visible("#description-panel")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_workspace_reopens_selected_framework_options(page):
    open_frameworks(page)
    page.check('input[name="framework"][value="asvs"]')
    page.select_option('select[data-option="level"]', "2")
    close_frameworks(page)
    pause(page)
    page.reload()
    page.wait_for_selector("#asked", state="visible")
    page.click("#description-nav")
    assert not page.is_checked('input[name="framework"][value="stride"]')
    assert page.is_checked('input[name="framework"][value="asvs"]')
    assert page.input_value('select[data-option="level"]') == "2"


def test_an_unticked_framework_shows_its_options_disabled(page):
    """A submitter sees a framework's options before ticking it (#1541)."""
    open_frameworks(page)
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


def test_the_frameworks_menu_names_the_choice_and_closes(page):
    """The menu's button names the ticked frameworks with their options, and
    Escape or a click outside the menu closes it."""
    toggle = page.locator("#frameworks-toggle")
    assert toggle.inner_text() == "Choose frameworks"
    assert page.is_hidden("#frameworks-panel")
    open_frameworks(page)
    page.check('input[name="framework"][value="stride"]')
    page.check('input[name="framework"][value="asvs"]')
    page.select_option('select[data-option="level"]', "2")
    assert toggle.inner_text() == "STRIDE, ASVS level 2"
    page.keyboard.press("Escape")
    assert page.is_hidden("#frameworks-panel")
    assert toggle.get_attribute("aria-expanded") == "false"
    assert page.evaluate("document.activeElement.id") == "frameworks-toggle"
    open_frameworks(page)
    page.click("#analysis-name")
    assert page.is_hidden("#frameworks-panel")


def test_a_click_on_a_framework_name_ticks_it_and_keeps_the_menu_open(page):
    """A click on a row's text moves focus to no element, and the menu stays
    open so the tick lands."""
    open_frameworks(page)
    page.click('.pick:has(input[value="stride"]) b')
    assert page.is_checked('input[name="framework"][value="stride"]')
    assert page.is_visible("#frameworks-panel")
    assert page.inner_text("#frameworks-toggle") == "STRIDE"


def test_the_new_analysis_title_is_on_the_line_of_frameworks(page):
    """The page title sits level with "Frameworks" in the settings column, and
    takes the 20px title size."""
    page.set_viewport_size({"width": 1280, "height": 900})
    title = page.locator("#new-analysis-title")
    legend = page.locator("#frameworks legend")
    assert title.evaluate("(node) => getComputedStyle(node).fontSize") == "20px"
    one, two = title.bounding_box(), legend.bounding_box()
    assert one and two
    title_middle = one["y"] + one["height"] / 2
    legend_middle = two["y"] + two["height"] / 2
    assert abs(title_middle - legend_middle) <= 4


def test_every_page_title_and_heading_takes_one_scale(page):
    """A page title is 20px, a section heading 17px and a sub-heading 15px,
    on the Controls editor that shows all three."""
    page.goto("/controls")
    page.click("#new-control")
    size = "(node) => getComputedStyle(node).fontSize"
    assert page.locator("h1").first.evaluate(size) == "20px"
    assert page.locator(".editor h2").first.evaluate(size) == "17px"
    assert page.locator(".editor .card-head h3").first.evaluate(size) == "15px"
    page.goto("/")
    assert page.locator("#controls-offer > h2").evaluate(size) == "17px"


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
    page.get_by_role("button", name="Dark theme").click()
    group = page.locator("details.finding-group").first
    assert group.get_attribute("open") is None
    page.emulate_media(media="print")
    for hidden in (
        ".sidebar",
        "#analysis-tabs",
        "#report-tabs",
        "#invite",
        "#preview-bar",
        "#report-tools",
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
    """Enter closes and opens a group, Tab reaches each control, an arrow key
    answers, and Enter skips and starts (#1632). The first group starts open
    (#2029 package 8)."""
    pause(page, opened=False)
    _tab_to(page, "active.matches('.question-group > summary')")
    assert page.evaluate("document.activeElement.parentElement.open")
    page.keyboard.press("Enter")
    assert not page.evaluate("document.activeElement.parentElement.open")
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
    html = render_report(
        report, report_state(report), previous=None, parent=None, controls=()
    ).html
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


def test_the_diagram_link_downloads_the_model_as_mermaid(browser):
    """The link under the diagram saves the service's Mermaid text unchanged."""
    from analysis_service.mermaid import styled_mermaid
    from tests.factories import sample_report

    report = sample_report()
    page, errors = _drawn(browser, report)
    with page.expect_download() as download:
        page.click("#model-diagram .mermaid-link")
    saved = download.value
    text = Path(saved.path()).read_text(encoding="utf-8")
    page.close()

    assert errors == []
    assert saved.suggested_filename == "system-model.mmd"
    assert text == styled_mermaid(report.system_model)


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


def upload_to_files(page, name: str, text: str) -> None:
    """Choose a file on the Files page; the upload dialog opens on a pass."""
    with page.expect_file_chooser() as chosen:
        page.click("#upload-file-button")
    chosen.value.set_files(
        {"name": name, "mimeType": "text/plain", "buffer": text.encode()}
    )


def test_text_typed_while_the_example_loads_stays(page):
    page.goto("/")
    held: list = []
    page.route("**/example", lambda route: held.append(route))
    page.click("#load")
    for _ in range(100):
        if held:
            break
        page.wait_for_timeout(20)
    page.fill("#description", "My own system.")
    with page.expect_response("**/example"):
        held[0].continue_()
    page.wait_for_timeout(200)
    assert page.input_value("#description") == "My own system."


def test_an_older_file_read_leaves_the_newer_file_in_the_dialog(page):
    page.goto("/files")
    page.evaluate(
        """() => {
          const read = textFile.read;
          let first = true;
          let open;
          window.releaseFirst = () => open();
          textFile.read = async (...args) => {
            if (!first) return read(...args);
            first = false;
            await new Promise((resolve) => { open = resolve; });
            const out = await read(...args);
            setTimeout(() => { window.firstDone = true; }, 0);
            return out;
          };
        }"""
    )
    upload_to_files(page, "older.txt", "old\n")
    page.wait_for_function("() => typeof window.releaseFirst === 'function'")
    upload_to_files(page, "newer.txt", "new\n")
    page.wait_for_selector("#upload-dialog[open]")
    page.evaluate("window.releaseFirst()")
    page.wait_for_function("() => window.firstDone")
    assert page.input_value("#upload-name") == "newer.txt"


def test_a_save_that_answers_after_a_new_pick_leaves_its_dialog(page):
    page.goto("/files")
    held: list = []
    page.route(
        "**/workspace/files",
        lambda route: (
            held.append(route)
            if route.request.method == "POST" and not held
            else route.fallback()
        ),
    )
    upload_to_files(page, "first.txt", "one\n")
    page.wait_for_selector("#upload-dialog[open]")
    page.click("#upload-save")
    for _ in range(100):
        if held:
            break
        page.wait_for_timeout(20)
    page.keyboard.press("Escape")
    page.wait_for_selector("#upload-dialog:not([open])", state="attached")
    upload_to_files(page, "second.txt", "two\n")
    page.wait_for_selector("#upload-dialog[open]")
    with page.expect_response("**/workspace/files"):
        held[0].continue_()
    page.wait_for_selector("#files-notice:has-text('first.txt')")
    assert page.is_visible("#upload-dialog")
    assert page.input_value("#upload-name") == "second.txt"


def test_the_files_page_keeps_lists_and_deletes_a_file_as_text(page):
    """A name and a text that spell markup stay text (OWASP A03)."""
    page.goto("/files")
    assert page.get_attribute("#files-nav", "aria-current") == "page"
    assert page.is_visible("#files-empty")
    hostile = '<img src=x onerror="window.ran=1">.txt'
    upload_to_files(page, hostile, "<script>window.ran=1</script> Ana: hi")
    page.wait_for_selector("#upload-dialog[open]")
    assert page.input_value("#upload-name") == hostile
    assert page.is_checked("input[name=upload-kind][value=transcript]")
    page.check("input[name=upload-kind][value=description]")
    page.click("#upload-save")
    page.wait_for_selector("#files-list tr")
    assert page.inner_text("#files-list td.name") == hostile
    assert page.inner_text("#files-list tr td:nth-child(2)") == "Description"
    assert page.locator("#files-list img").count() == 0
    assert page.evaluate("window.ran") is None
    assert "is in Files" in page.inner_text("#files-notice")

    upload_to_files(page, hostile, "again")
    page.wait_for_selector("#upload-dialog[open]")
    page.click("#upload-save")
    page.wait_for_selector("#upload-problem:not([hidden])")
    assert "You already have a file named" in page.inner_text("#upload-problem")
    page.click("#upload-cancel")

    upload_to_files(page, "notes.md", "text")
    assert "Files keeps only" in page.inner_text("#files-problem")

    page.click("#files-list button")
    page.wait_for_selector("#delete-dialog[open]")
    assert "keeps its own copy" in page.inner_text("#delete-dialog")
    page.click("#delete-go")
    page.wait_for_selector("#files-empty:not([hidden])")
    assert "keep their copy" in page.inner_text("#files-notice")


def test_a_files_answer_that_is_not_json_says_what_failed(page):
    page.goto("/files")
    page.route(
        "**/workspace/files",
        lambda route: (
            route.fulfill(status=502, body="Bad Gateway")
            if route.request.method == "POST"
            else route.fallback()
        ),
    )
    upload_to_files(page, "notes.txt", "Ana: hi")
    page.wait_for_selector("#upload-dialog[open]")
    page.click("#upload-save")
    page.wait_for_selector(
        "#upload-problem:has-text('The service could not keep the file.')"
    )


def test_the_files_page_filters_by_name_kind_and_format(page):
    page.goto("/files")
    for name, text in (
        ("network.mmd", "flowchart LR\n  a --> b\n"),
        ("call.vtt", "Ana: hi"),
    ):
        upload_to_files(page, name, text)
        page.wait_for_selector("#upload-dialog[open]")
        page.click("#upload-save")
        page.wait_for_selector(f"#files-list td.name >> text={name}")

    def names() -> list[str]:
        return page.locator("#files-list td.name").all_inner_texts()

    page.fill("#files-search", "NET")
    assert names() == ["network.mmd"]
    page.fill("#files-search", "")
    page.select_option("#files-kind", "transcript")
    assert names() == ["call.vtt"]
    page.select_option("#files-format", "text/vnd.mermaid")
    assert page.is_hidden("#files-table")
    assert page.inner_text("#files-empty") == "No files match your filters."
    page.select_option("#files-kind", "")
    assert names() == ["network.mmd"]


def choose_from_files(page):
    """Tick the first file in the "Select from Files" dialog and select it."""
    page.click("#upload")
    page.click("#upload-files")
    page.wait_for_selector("#files-dialog[open] #files-rows label")
    page.check("#files-rows input[type=checkbox]")
    page.click("#files-select")
    page.wait_for_selector("#chosen-files li")


def test_the_plus_button_offers_an_upload_and_a_choice_from_files(page):
    page.goto("/")
    assert page.get_attribute("#upload", "aria-expanded") == "false"
    page.click("#upload")
    assert page.get_attribute("#upload", "aria-expanded") == "true"
    assert page.inner_text("#upload-menu") == "Upload from computer\nSelect from Files"
    page.keyboard.press("Escape")
    assert page.is_hidden("#upload-menu")
    page.click("#upload")
    with page.expect_file_chooser() as chooser:
        page.click("#upload-computer")
    chooser.value.set_files(
        {"name": "call.vtt", "mimeType": "text/vtt", "buffer": b"Ana: hi"}
    )
    page.wait_for_selector("#transcript-note:not([hidden])")
    assert page.inner_text("#transcript-name") == "call.vtt"
    page.click("#upload")
    page.click("#upload-files")
    page.wait_for_selector("#files-dialog[open]")
    assert "Files is empty." in page.inner_text("#files-rows")
    page.click("#files-cancel")
    assert page.is_hidden("#chosen-files")
    page.route("**/workspace/files", lambda route: route.abort())
    page.click("#upload")
    page.click("#upload-files")
    page.wait_for_selector("#files-dialog[open]")
    assert page.inner_text("#files-rows") == (
        "Your files did not load. Close this and open it again to retry."
    )


def test_a_chosen_file_goes_into_the_analysis_and_shows_when_it_opens_again(page):
    diagram = "flowchart LR\n    user[Customer] -->|HTTPS| app(Web App)\n"
    page.goto("/files")
    upload_to_files(page, "network.mmd", diagram)
    page.wait_for_selector("#upload-dialog[open]")
    assert page.is_hidden("#upload-kinds")
    assert "always a diagram" in page.inner_text("#upload-fixed-kind")
    page.click("#upload-save")
    page.wait_for_selector("#files-list tr")
    file_id = page.evaluate("fetch('/workspace/files').then((r) => r.json())")[0]["id"]

    page.goto("/")
    choose_from_files(page)
    assert page.inner_text("#chosen-files").startswith("File: network.mmd · Diagram")
    assert "Up to 102,3" in page.inner_text("#description-remaining")
    open_frameworks(page)
    page.locator('input[name="framework"]').first.check()
    close_frameworks(page)
    page.fill("#analysis-name", "Files analysis")
    with page.expect_response("**/analyze") as answered:
        page.click("#go")
    request = answered.value.request
    assert request.post_data_json["owner_files"] == [file_id]
    assert request.post_data_json["sources"] == []
    run = answered.value.json()["run"]
    page.wait_for_selector("#asked", state="visible")

    page.goto(f"/?run={run}")
    page.wait_for_selector("#asked", state="visible")
    page.click("#description-nav")
    page.wait_for_selector("#files-used", state="visible")
    assert page.inner_text("#files-used-list") == "network.mmd (diagram)"
    assert page.is_hidden("#chosen-files")


def test_a_file_deleted_before_the_submission_shows_as_missing(page):
    page.goto("/files")
    upload_to_files(page, "call.vtt", "Ana: hi")
    page.wait_for_selector("#upload-dialog[open]")
    page.click("#upload-save")
    page.wait_for_selector("#files-list tr")
    page.goto("/")
    choose_from_files(page)
    file_id = page.evaluate("fetch('/workspace/files').then((r) => r.json())")[0]["id"]
    page.evaluate(f"fetch('/workspace/files/{file_id}', {{method: 'DELETE'}})")
    open_frameworks(page)
    page.locator('input[name="framework"]').first.check()
    close_frameworks(page)
    page.fill("#analysis-name", "Gone file")
    page.click("#go")
    page.wait_for_selector("#problem:not([hidden])")
    assert "no longer in Files" in page.inner_text("#problem")
    page.wait_for_selector("#chosen-files li >> text=Missing")


# ---- Controls (#2157) ------------------------------------------------------

#: A Control name that spells markup and script. The page shows it as text.
HOSTILE_CONTROL = '<img src=x onerror="window.ran=1">SSO <b>(Okta)</b>'


def new_control(page, name: str, *, scope=("store", "Orders DB")) -> None:
    """Open the editor and fill the name and what the Control protects."""
    page.click("#new-control")
    page.fill("#control-name", name)
    kind, scope_name = scope
    page.select_option("#scope-type", kind)
    if scope_name:
        page.fill("#scope-name", scope_name)


def tick(page, statement: str, how: str = "") -> None:
    """Tick a statement of "What it does", and write how where it asks."""
    page.get_by_label(statement, exact=True).check()
    if how:
        page.fill(f"#statements input[aria-label='How: {statement}']", how)


def add_row(page, framework: str, unit: str) -> None:
    """Find ``unit`` in the unit tree of ``framework``, and add it."""
    page.select_option("#row-framework", framework)
    page.fill("#unit-find", unit)
    page.click(f"[id='unit-add-{framework}-{unit}']")


def add_evidence(page, name: str, text: str, evidence_class: str = "config") -> None:
    page.select_option("#evidence-class", evidence_class)
    with page.expect_file_chooser() as chosen:
        page.click("#evidence-file")
    chosen.value.set_files(
        {"name": name, "mimeType": "text/plain", "buffer": text.encode()}
    )


def save_control(page) -> None:
    """Save the editor, and wait until the list shows the saved name. A
    notice from an earlier save is already shown, so the notice alone does
    not say that the list has reloaded."""
    with page.expect_response("**/workspace/controls**") as answered:
        page.click("#save-control")
    assert answered.value.ok, answered.value.text()
    name = answered.value.json()["name"]
    page.wait_for_function(
        """name => [...document.querySelectorAll('#controls-list td.name-cell a')]
          .some((link) => link.textContent === name)""",
        arg=name,
    )


def test_the_controls_page_refuses_an_alias_or_a_row_over_its_count(page):
    from analysis_service.controls import (
        ALIASES_FULL,
        MAX_CONTROL_ALIASES,
        MAX_CONTROL_ROWS,
        ROWS_FULL,
    )

    page.goto("/controls")
    new_control(page, "SSO")
    for number in range(MAX_CONTROL_ALIASES + 1):
        page.fill("#alias-input", f"alias {number}")
        page.press("#alias-input", "Enter")
    aliases = page.locator(".aliases .tag").count()
    alias_refusal = page.inner_text("#editor-problem")
    from analysis_service.frameworks import PACKAGES

    for unit in PACKAGES["asvs"].units[: MAX_CONTROL_ROWS + 1]:
        add_row(page, "asvs", unit.id)
    rows = page.locator("#rows-list li").count()
    row_refusal = page.inner_text("#editor-problem")
    assert (aliases, alias_refusal) == (MAX_CONTROL_ALIASES, ALIASES_FULL)
    assert (rows, row_refusal) == (MAX_CONTROL_ROWS, ROWS_FULL)


def _two_kept_controls(page) -> None:
    """Keep the Controls A and B, and show them on the Controls page."""
    page.goto("/controls")
    for name in "AB":
        keep_control(page, name, {"type": "application"})
    page.reload()
    page.wait_for_selector("#controls-list tr")


def _hold_file_reads(page) -> None:
    """Hold each evidence file read until ``window.releaseReads()``."""
    page.evaluate(
        """() => {
          const decode = textFile.decode;
          let open;
          const gate = new Promise((resolve) => { open = resolve; });
          window.releaseReads = open;
          textFile.decode = async (...args) => { await gate; return decode(...args); };
        }"""
    )


def test_a_save_locks_the_editor_and_lands_on_its_own_draft(page):
    _two_kept_controls(page)
    held: list = []
    page.route(
        "**/workspace/controls/*",
        lambda route: (
            held.append(route) if route.request.method == "PUT" else route.fallback()
        ),
    )
    page.click('button[aria-label="Edit A"]')
    page.fill("#control-name", "A2")
    page.click("#save-control")
    page.wait_for_function("document.querySelector('fieldset.editor').disabled")
    assert page.inner_text("#editor-status") == "Saving “A2”…"
    assert page.is_disabled('button[aria-label="Edit B"]')
    assert page.is_disabled("#new-control")
    page.locator("#controls-list td.name-cell a", has_text="B").click(force=True)
    assert page.input_value("#control-name") == "A2"
    with page.expect_response("**/workspace/controls/*"):
        held[0].fallback()
    page.wait_for_selector("#controls-notice:has-text('“A2” is saved.')")
    assert page.is_enabled('button[aria-label="Edit B"]')


def test_a_refused_save_unlocks_the_editor_and_keeps_the_draft(page):
    _two_kept_controls(page)
    page.route(
        "**/workspace/controls/*",
        lambda route: (
            route.fulfill(
                status=409, json={"message": "Another tab changed this Control."}
            )
            if route.request.method == "PUT"
            else route.fallback()
        ),
    )
    page.click('button[aria-label="Edit A"]')
    page.fill("#control-name", "A2")
    page.click("#save-control")
    page.wait_for_selector("#editor-problem:has-text('Another tab changed')")
    assert page.input_value("#control-name") == "A2"
    assert page.is_enabled("#save-control")
    assert page.is_enabled('button[aria-label="Edit B"]')


def test_a_delete_during_a_save_leaves_the_table_usable(page):
    _two_kept_controls(page)
    held: list = []
    page.route("**/workspace/controls/*", lambda route: held.append(route))
    page.click('button[aria-label="Edit A"]')
    page.click('button[aria-label="Delete A"]')
    page.click("#delete-go")
    page.click("#save-control")
    page.wait_for_function("document.querySelector('fieldset.editor').disabled")
    deleting, saving = sorted(held, key=lambda route: route.request.method)
    deleting.continue_()
    page.wait_for_selector("#controls-notice:has-text('is deleted')")
    with page.expect_response("**/workspace/controls/*"):
        saving.continue_()
    page.wait_for_selector('button[aria-label="Edit B"]:enabled')
    page.wait_for_selector("#new-control:enabled")


def test_a_new_editor_shows_no_old_refusal(page):
    _two_kept_controls(page)
    page.route(
        "**/workspace/controls/*",
        lambda route: (
            route.fulfill(status=409, json={"message": "Another tab changed it."})
            if route.request.method == "PUT"
            else route.fallback()
        ),
    )
    page.click('button[aria-label="Edit A"]')
    page.click("#save-control")
    page.wait_for_selector("#editor-problem:has-text('Another tab changed it.')")
    page.click('button[aria-label="Edit B"]')
    assert page.is_hidden("#editor-problem")


def test_a_file_read_waits_for_save_and_stays_with_its_draft(page):
    _two_kept_controls(page)
    _hold_file_reads(page)
    page.click('button[aria-label="Edit A"]')
    add_evidence(page, "a.txt", "for A\n")
    page.wait_for_selector("#evidence-list li:has-text('Reading…')")
    assert page.is_disabled("#save-control")
    assert page.inner_text("#editor-status") == "Save waits for the file read to end."
    page.click('button[aria-label="Edit B"]')
    page.click("#leave-discard")
    page.evaluate("window.releaseReads()")
    page.wait_for_timeout(200)
    assert page.input_value("#control-name") == "B"
    assert page.locator("#evidence-list li").count() == 0


def test_a_file_read_after_cancel_changes_nothing(page):
    _two_kept_controls(page)
    _hold_file_reads(page)
    page.click('button[aria-label="Edit A"]')
    add_evidence(page, "a.txt", "for A\n")
    page.wait_for_selector("#evidence-list li:has-text('Reading…')")
    page.click("#cancel-control")
    page.evaluate("window.releaseReads()")
    page.wait_for_timeout(200)
    assert page.locator("#control-name").count() == 0


def test_a_finished_file_read_opens_save_again(page):
    _two_kept_controls(page)
    _hold_file_reads(page)
    page.click('button[aria-label="Edit A"]')
    add_evidence(page, "a.txt", "for A\n")
    page.wait_for_selector("#evidence-list li:has-text('Reading…')")
    page.evaluate("window.releaseReads()")
    page.wait_for_selector("#evidence-list li:not(:has-text('Reading…'))")
    assert page.is_enabled("#save-control")
    with page.expect_request("**/workspace/controls/*") as sent:
        page.click("#save-control")
    assert sent.value.post_data_json["evidence"] == [
        {"name": "a.txt", "class": "config", "text": "for A\n"}
    ]


def replace_evidence(page, index: int, name: str, text: str) -> None:
    with page.expect_file_chooser() as chosen:
        page.click(f"#evidence-replace-{index}")
    chosen.value.set_files(
        {"name": name, "mimeType": "text/plain", "buffer": text.encode()}
    )


def _kept_with_a_file(page, text: str = "storage_encrypted = true\n") -> None:
    """Save a Control with one evidence file of the class Code, and open it."""
    page.goto("/controls")
    new_control(page, "Orders DB encryption")
    tick(page, "Encrypts its data at rest.", "AES-256")
    add_evidence(page, "rds.tf", text, "code")
    page.wait_for_selector("#evidence-remove-0")
    save_control(page)
    page.click('button[aria-label="Edit Orders DB encryption"]')


def changed(page) -> bool:
    """Whether the page asks before the owner leaves: the draft has changes."""
    return page.evaluate(
        """() => { const event = new Event('beforeunload', { cancelable: true });
          window.dispatchEvent(event); return event.defaultPrevented; }"""
    )


def evidence_names(page) -> list[str]:
    return [
        line.split(" · ")[0]
        for line in page.locator(
            "#evidence-list li > span:first-child"
        ).all_inner_texts()
    ]


def test_view_shows_a_new_file_and_a_held_file_as_text(page):
    text = "<b>bold</b> = true\n"
    page.goto("/controls")
    new_control(page, "Orders DB encryption")
    add_evidence(page, "rds.tf", text)
    page.wait_for_selector("#evidence-view-0")
    page.click("#evidence-view-0")
    assert page.inner_text("#evidence-text-0") == text
    assert page.locator("#evidence-text-0 b").count() == 0
    page.click("#evidence-view-0")
    assert page.locator("#evidence-text-0").count() == 0

    _kept_with_a_file(page, text)
    with page.expect_response("**/evidence/*") as answered:
        page.click("#evidence-view-0")
    assert answered.value.ok
    page.wait_for_selector("#evidence-text-0:not(:text-is('Loading…'))")
    assert page.inner_text("#evidence-text-0") == text
    assert page.get_attribute("#evidence-view-0", "aria-expanded") == "true"
    assert not changed(page)


def test_a_refused_view_shows_why_and_closes_the_line(page):
    _kept_with_a_file(page)
    page.route(
        "**/evidence/*",
        lambda route: route.fulfill(
            status=404,
            json={"message": "This evidence file is no longer in the Control."},
        ),
    )
    page.click("#evidence-view-0")
    page.wait_for_selector(
        "#editor-problem:has-text('This evidence file is no longer in the Control.')"
    )
    assert page.locator("#evidence-text-0").count() == 0


def test_a_view_answer_that_is_not_json_says_the_service_could_not_read(page):
    _kept_with_a_file(page)
    page.route(
        "**/evidence/*",
        lambda route: route.fulfill(status=500, body="Internal Server Error"),
    )
    page.click("#evidence-view-0")
    page.wait_for_selector(
        "#editor-problem:has-text('The service could not read this file.')"
    )


def test_a_save_answer_that_is_not_json_says_the_service_could_not_save(page):
    _kept_with_a_file(page)
    page.route(
        "**/workspace/controls/*",
        lambda route: (
            route.fulfill(status=502, body="Bad Gateway")
            if route.request.method == "PUT"
            else route.fallback()
        ),
    )
    page.fill("#control-name", "Orders DB at rest")
    page.click("#save-control")
    page.wait_for_selector(
        "#editor-problem:has-text('The service could not save the Control.')"
    )


def test_a_delete_answer_that_is_not_json_says_the_service_could_not_delete(page):
    _two_kept_controls(page)
    page.route(
        "**/workspace/controls/*",
        lambda route: route.fulfill(status=502, body="Bad Gateway"),
    )
    page.click('button[aria-label="Delete A"]')
    page.click("#delete-go")
    page.wait_for_selector(
        "#controls-problem:has-text('The service could not delete the Control.')"
    )


def test_focus_goes_back_to_replace_after_the_read(page):
    _kept_with_a_file(page)
    _hold_file_reads(page)
    replace_evidence(page, 0, "kms.tf", "kms = true\n")
    page.wait_for_selector("#evidence-list li:has-text('Reading…')")
    assert page.evaluate("document.activeElement.id") == "evidence-remove-0"
    page.evaluate("window.releaseReads()")
    page.wait_for_selector("#evidence-list li:not(:has-text('Reading…'))")
    assert page.evaluate("document.activeElement.id") == "evidence-replace-0"


def test_download_saves_a_held_file_and_a_new_file_has_none(page):
    page.goto("/controls")
    new_control(page, "Orders DB encryption")
    add_evidence(page, "rds.tf", "a = 1\n")
    page.wait_for_selector("#evidence-remove-0")
    assert page.locator("#evidence-download-0").count() == 0
    page.click("#cancel-control")

    _kept_with_a_file(page)
    with page.expect_download() as saved:
        page.click("#evidence-download-0")
    assert saved.value.suggested_filename == "rds.tf"
    assert Path(saved.value.path()).read_text() == "storage_encrypted = true\n"


def test_a_kind_change_of_a_held_file_reaches_the_save(page):
    _kept_with_a_file(page)
    assert page.input_value("#evidence-class-0") == "code"
    page.select_option("#evidence-class-0", "people")
    assert changed(page)
    with page.expect_request("**/workspace/controls/*") as sent:
        save_control(page)
    [held] = sent.value.post_data_json["evidence"]
    assert held["class"] == "people" and set(held) == {"id", "class"}
    [control] = page.evaluate("fetch('/workspace/controls').then((r) => r.json())")
    assert control["evidence"][0]["class"] == "people"


def test_replace_reads_a_new_file_into_the_line_and_keeps_its_kind(page):
    _kept_with_a_file(page)
    add_evidence(page, "waf.yaml", "waf = on\n")
    page.wait_for_selector("#evidence-remove-1")
    replace_evidence(page, 0, "kms.tf", "kms = true\n")
    page.wait_for_selector("#evidence-list li:not(:has-text('Reading…')) >> nth=1")
    assert evidence_names(page) == ["kms.tf", "waf.yaml"]
    assert page.input_value("#evidence-class-0") == "code"
    with page.expect_request("**/workspace/controls/*") as sent:
        save_control(page)
    assert sent.value.post_data_json["evidence"][0] == {
        "name": "kms.tf",
        "class": "code",
        "text": "kms = true\n",
    }


def test_a_failed_replace_keeps_the_file_it_read_over(page):
    _kept_with_a_file(page)
    replace_evidence(page, 0, "big.txt", "x" * 10_001)
    page.wait_for_selector("#editor-problem:not([hidden])")
    assert evidence_names(page) == ["rds.tf"]
    assert not changed(page)


def _save_elsewhere(page, control: str, **changed) -> None:
    """Save the Control named ``control`` as another tab would, with
    ``changed``."""
    answered = page.evaluate(
        """async ([name, changed]) => {
          const [control] = (await (await fetch('/workspace/controls')).json())
            .filter((one) => one.name === name);
          const scope = control.scope.type === 'application'
            ? { type: 'application' } : control.scope;
          const body = { name: control.name, scope, facts: control.facts,
            rows: control.rows, updated_at: control.updated_at,
            evidence: control.evidence.map((file) => ({ id: file.id, class: file.class })),
            ...changed };
          const sent = await fetch('/workspace/controls/' + control.id, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body) });
          return sent.status;
        }""",
        [control, changed],
    )
    assert answered == 200


def test_a_stale_save_shows_what_changed_and_keeps_the_draft(page):
    _two_kept_controls(page)
    page.click('button[aria-label="Edit A"]')
    page.fill("#control-name", "A mine")
    _save_elsewhere(page, "A", name="A theirs")
    with page.expect_response("**/workspace/controls/*"):
        page.click("#save-control")
    page.wait_for_selector("#stale-note")
    assert page.locator("#stale-fields li").all_inner_texts() == ["Name: A theirs"]
    assert page.input_value("#control-name") == "A mine"
    assert page.inner_text("#editor-problem") == (
        "This Control changed in another tab or window after you opened it."
    )
    assert page.get_attribute("#stale-note", "role") is None
    page.wait_for_selector("#controls-list td.name-cell a:text-is('A theirs')")


def test_keep_my_changes_replaces_the_saved_version(page):
    _two_kept_controls(page)
    page.click('button[aria-label="Edit A"]')
    page.fill("#control-name", "A mine")
    _save_elsewhere(page, "A", name="A theirs")
    page.click("#save-control")
    page.click("#stale-keep")
    assert page.locator("#stale-note").count() == 0
    assert page.evaluate("document.activeElement.id") == "save-control"
    save_control(page)
    names = page.locator("#controls-list td.name-cell a").all_inner_texts()
    assert sorted(names) == ["A mine", "B"]


def test_open_the_saved_version_drops_the_draft(page):
    _two_kept_controls(page)
    page.click('button[aria-label="Edit A"]')
    page.fill("#control-name", "A mine")
    _save_elsewhere(page, "A", name="A theirs")
    page.click("#save-control")
    page.click("#stale-open")
    assert page.input_value("#control-name") == "A theirs"
    assert page.locator("#stale-note").count() == 0
    assert not changed(page)


def test_keep_drops_a_file_the_saved_version_no_longer_holds(page):
    _kept_with_a_file(page)
    page.fill("#control-name", "Orders DB mine")
    _save_elsewhere(page, "Orders DB encryption", evidence=[])
    page.click("#save-control")
    page.wait_for_selector("#stale-fields li:has-text('Evidence: None')")
    page.click("#stale-keep")
    assert evidence_names(page) == []
    assert "no longer holds “rds.tf”" in page.inner_text("#controls-notice")
    save_control(page)


def test_keep_keeps_a_file_that_the_other_save_added(page):
    _kept_with_a_file(page)
    page.fill("#control-name", "Orders DB mine")
    added = page.evaluate(
        """async () => {
          const [control] = await (await fetch('/workspace/controls')).json();
          const body = { name: control.name, scope: control.scope,
            facts: control.facts, rows: control.rows,
            updated_at: control.updated_at,
            evidence: [...control.evidence.map((file) => ({ id: file.id, class: file.class })),
              { name: 'waf.tf', class: 'config', text: 'waf = on\\n' }] };
          const sent = await fetch('/workspace/controls/' + control.id, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body) });
          return sent.status;
        }"""
    )
    assert added == 200
    page.click("#save-control")
    page.click("#stale-keep")
    assert evidence_names(page) == ["rds.tf", "waf.tf"]
    assert "also holds “waf.tf”, so your changes keep it" in page.inner_text(
        "#controls-notice"
    )
    save_control(page)
    [control] = page.evaluate("fetch('/workspace/controls').then((r) => r.json())")
    assert [file["name"] for file in control["evidence"]] == ["rds.tf", "waf.tf"]


def test_a_second_stale_save_lists_only_what_changed_since_keep(page):
    _two_kept_controls(page)
    page.click('button[aria-label="Edit A"]')
    page.fill("#control-name", "A mine")
    _save_elsewhere(page, "A", name="A theirs")
    page.click("#save-control")
    page.click("#stale-keep")
    tampering = {"framework": "stride", "unit": "tampering", "version": "1.0"}
    _save_elsewhere(page, "A theirs", rows=[tampering])
    page.click("#save-control")
    page.wait_for_selector("#stale-note")
    assert page.locator("#stale-fields li").all_inner_texts() == [
        "Rows: stride tampering"
    ]


def _fits_the_screen(page) -> bool:
    return page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )


def test_a_narrow_screen_shows_each_row_without_a_side_scroll(page):
    page.set_viewport_size({"width": 390, "height": 844})
    _two_kept_controls(page)
    assert _fits_the_screen(page)
    wrap = page.evaluate(
        """() => { const box = document.querySelector('.controls-table-wrap');
          return box.scrollWidth <= box.clientWidth; }"""
    )
    assert wrap
    for name in ("A", "B"):
        for action in ("Edit", "Delete"):
            box = page.locator(f'button[aria-label="{action} {name}"]').bounding_box()
            assert box["x"] + box["width"] <= 390
    row = page.locator("#controls-list tr", has=page.locator("a:text-is('A')"))
    assert "The whole application" in row.inner_text()
    assert row.locator("td.wide-only").first.is_hidden()
    assert page.is_visible("th[data-sort='updated'] button")
    page.click("summary[aria-label='Details of A']")
    assert "stride spoofing" in row.locator(".row-more dl").inner_text()
    row.locator("input[type=checkbox]").check()
    assert row.locator(".row-more").get_attribute("open") == ""
    page.click('button[aria-label="Edit A"]')
    assert _fits_the_screen(page)


def test_a_narrow_screen_names_each_row_as_the_wide_chip_does(page):
    """The details of a Control name each row with its title, and say "None"
    for a Control with no row."""
    page.set_viewport_size({"width": 390, "height": 844})
    _two_kept_controls(page)
    key = ["", "encryption_at_rest", "", "", "", ""]
    store = {"type": "store", "name": "Orders DB"}
    keep_control(page, "F", store, facts=[{"key": key, "value": "AES-256"}], rows=[])
    encoding = {"framework": "asvs", "unit": "V1", "version": "5.0.0"}
    keep_control(page, "V", {"type": "application"}, rows=[encoding])
    page.reload()
    page.wait_for_selector("#controls-list tr")

    def covers(name: str) -> str:
        row = page.locator(
            "#controls-list tr", has=page.locator(f"a:text-is('{name}')")
        )
        page.click(f"summary[aria-label='Details of {name}']")
        return row.locator(".row-more dd").first.inner_text()

    assert covers("F") == "None"
    assert covers("V") == "asvs V1 · Encoding and Sanitization"


def test_a_stale_save_names_the_scope_in_the_words_of_the_service(page):
    page.goto("/controls")
    store = {"type": "store", "name": "Orders DB"}
    keep_control(page, "S", store)
    page.reload()
    page.wait_for_selector("#controls-list tr")
    page.click('button[aria-label="Edit S"]')
    page.fill("#control-name", "S mine")
    _save_elsewhere(page, "S", scope=store | {"aliases": ["orders"]})
    page.click("#save-control")
    page.wait_for_selector("#stale-note")
    assert page.locator("#stale-fields li").all_inner_texts() == [
        "What it protects: The data store “Orders DB” (aliases: orders)"
    ]


def test_a_wide_screen_shows_the_columns_and_no_details(page):
    page.set_viewport_size({"width": 1280, "height": 800})
    _two_kept_controls(page)
    assert page.locator("#controls-list .row-more").first.is_hidden()
    assert page.locator("#controls-list td.wide-only").first.is_visible()


def test_the_editor_keeps_its_picks_and_focus_across_a_redraw(page):
    page.goto("/controls")
    new_control(page, "Rows")
    page.select_option("#row-framework", "asvs")
    page.fill("#unit-find", "V11.2")
    page.focus("[id='unit-add-asvs-V11.2']")
    page.keyboard.press("Enter")
    assert page.input_value("#row-framework") == "asvs"
    assert page.input_value("#unit-find") == "V11.2"
    # Focus moves to the next unit that shows.
    assert page.evaluate("document.activeElement.id") == "unit-add-asvs-V11.2.1"
    page.keyboard.press("Enter")
    rows = page.locator("#rows-list li").all_inner_texts()
    assert [row.split()[:2] for row in rows] == [["asvs", "V11.2"], ["asvs", "V11.2.1"]]
    page.select_option("#evidence-class", "code")
    page.fill("#alias-input", "half typed")
    tick(page, "Encrypts its data at rest.", "AES-256")
    assert page.input_value("#evidence-class") == "code"
    assert page.input_value("#alias-input") == "half typed"


def test_the_unit_tree_opens_by_chapter_and_finds_by_words(page):
    from analysis_service.frameworks import PACKAGES

    page.goto("/controls")
    new_control(page, "Rows")
    page.select_option("#row-framework", "asvs")
    chapters = [unit for unit in PACKAGES["asvs"].units if unit.grain == "chapter"]
    tree = page.locator("#unit-tree > li")
    assert tree.count() == len(chapters)
    assert page.locator("[id='unit-add-asvs-V11.2']").count() == 0
    page.click("[id='unit-open-asvs-V11']")
    assert page.get_attribute("[id='unit-open-asvs-V11']", "aria-expanded") == "true"
    assert page.locator("[id='unit-add-asvs-V11.2']").count() == 1
    label = PACKAGES["asvs"].record.text_of_unit("V11.2.1")
    page.fill("#unit-find", label.split()[0] + " " + label.split()[-1])
    assert "match" in page.inner_text("#unit-count")
    shown = page.locator("#unit-tree").inner_text()
    assert f"V11.2.1 · {label}" in shown
    page.fill("#unit-find", "no unit has this zz")
    assert page.inner_text("#unit-count") == "No unit matches."


def test_a_search_opens_its_lines_and_keeps_one_count_line(page):
    page.goto("/controls")
    new_control(page, "Rows")
    page.select_option("#row-framework", "asvs")
    toggle = page.locator("[id='unit-open-asvs-V11']")
    assert toggle.locator("span[aria-hidden='true']").inner_text().strip() == "▸"
    page.evaluate("document.querySelector('#unit-count').dataset.mark = 'kept'")
    page.fill("#unit-find", "V11")
    assert page.locator("[id^='unit-open-asvs-']").count() == 0
    page.fill("#unit-find", "V11.2")
    assert page.get_attribute("#unit-count", "data-mark") == "kept"
    assert page.get_attribute("#unit-count", "role") == "status"
    page.fill("#unit-find", "")
    assert page.inner_text("#unit-count") == ""
    assert toggle.get_attribute("aria-expanded") == "false"


def test_a_unit_already_a_row_cannot_be_added_again(page):
    page.goto("/controls")
    new_control(page, "Rows")
    add_row(page, "stride", "spoofing")
    assert page.locator("[id='unit-add-stride-spoofing']").count() == 0
    line = page.locator("#unit-tree li", has_text="spoofing")
    assert line.inner_text().endswith("Added")
    page.fill("#unit-find", "")
    assert page.locator("#unit-tree > li").count() == 6


def test_removing_a_row_moves_focus_to_its_neighbour(page):
    page.goto("/controls")
    new_control(page, "Rows")
    add_row(page, "stride", "spoofing")
    add_row(page, "stride", "tampering")
    page.click("#row-remove-0")
    assert page.evaluate("document.activeElement.id") == "row-remove-0"
    page.click("#row-remove-0")
    assert page.evaluate("document.activeElement.id") == "row-framework"


def test_a_table_filter_leaves_the_editor_as_it_is(page):
    page.goto("/controls")
    new_control(page, "Rows")
    page.evaluate("document.querySelector('fieldset.editor').dataset.mark = 'kept'")
    page.fill("#controls-search", "nothing")
    page.fill("#controls-search", "")
    assert page.get_attribute("fieldset.editor", "data-mark") == "kept"


def test_a_row_tick_keeps_its_focus(page):
    _two_kept_controls(page)
    page.focus('input[aria-label="Select A"]')
    page.keyboard.press("Space")
    assert page.is_checked('input[aria-label="Select A"]')
    assert page.evaluate("document.activeElement.getAttribute('aria-label')") == (
        "Select A"
    )


def test_cancel_moves_focus_to_the_edit_button(page):
    _two_kept_controls(page)
    page.click('button[aria-label="Edit A"]')
    page.focus("#cancel-control")
    page.keyboard.press("Enter")
    assert page.evaluate("document.activeElement.getAttribute('aria-label')") == (
        "Edit A"
    )


def test_a_delete_moves_focus_to_the_next_row(page):
    _two_kept_controls(page)
    first = page.locator("#controls-list td.name-cell a").first.inner_text()
    second = page.locator("#controls-list td.name-cell a").nth(1).inner_text()
    page.focus(f'button[aria-label="Delete {first}"]')
    page.keyboard.press("Enter")
    page.click("#delete-go")
    page.wait_for_selector("#controls-notice:has-text('is deleted')")
    page.wait_for_function(
        "name => document.activeElement?.getAttribute('aria-label') === 'Select ' + name",
        arg=second,
    )


def test_a_saved_control_takes_focus_on_its_edit_button(page):
    _two_kept_controls(page)
    page.click('button[aria-label="Edit A"]')
    page.fill("#control-name", "A2")
    page.focus("#save-control")
    page.keyboard.press("Enter")
    page.wait_for_selector("#controls-notice:has-text('“A2” is saved.')")
    page.wait_for_function(
        "() => document.activeElement?.getAttribute('aria-label') === 'Edit A2'"
    )


def test_a_delete_with_new_control_off_moves_focus_to_search(page):
    page.goto("/controls")
    keep_control(page, "Only", {"type": "application"})
    page.reload()
    page.wait_for_selector("#controls-list tr")
    page.click("#new-control")
    assert page.is_disabled("#new-control")
    page.focus('button[aria-label="Delete Only"]')
    page.keyboard.press("Enter")
    page.click("#delete-go")
    page.wait_for_selector("#controls-notice:has-text('is deleted')")
    page.wait_for_function("() => document.activeElement?.id === 'controls-search'")


def test_focus_moved_during_a_save_stays(page):
    _two_kept_controls(page)
    held = _hold(page, "**/workspace/controls/*")
    page.click('button[aria-label="Edit A"]')
    page.focus("#save-control")
    page.keyboard.press("Enter")
    _wait_for(page, held)
    page.focus("#controls-search")
    with page.expect_response("**/workspace/controls/*"):
        held[0].continue_()
    page.wait_for_selector("#controls-notice:has-text('is saved')")
    page.wait_for_selector('button[aria-label="Edit A"]:enabled')
    assert page.evaluate("document.activeElement.id") == "controls-search"


def test_focus_moved_during_a_delete_stays(page):
    _two_kept_controls(page)
    held = _hold(page, "**/workspace/controls/*")
    page.focus('button[aria-label="Delete A"]')
    page.keyboard.press("Enter")
    page.click("#delete-go")
    _wait_for(page, held)
    page.focus("#controls-search")
    with page.expect_response("**/workspace/controls/*"):
        held[0].continue_()
    page.wait_for_selector("#controls-notice:has-text('is deleted')")
    page.wait_for_function(
        "() => document.querySelectorAll('#controls-list tr').length === 1"
    )
    assert page.evaluate("document.activeElement.id") == "controls-search"


def test_each_focusable_control_on_the_controls_page_has_an_id(page):
    """The page keeps focus across a redraw by element ID, so a focusable
    element with no ID loses focus when its box draws again."""
    _two_kept_controls(page)
    # Every part of the editor that holds a focusable element: a named scope
    # with an alias, a fact with its "how" field, a row and an evidence file.
    new_control(page, "Every part")
    page.fill("#alias-input", "orders")
    page.press("#alias-input", "Enter")
    tick(page, "Encrypts its data at rest.", "AES-256")
    add_row(page, "stride", "spoofing")
    add_evidence(page, "rds.tf", "storage_encrypted = true\n")
    page.wait_for_selector("#evidence-remove-0")
    ids = page.evaluate("[...document.querySelectorAll('[id]')].map((node) => node.id)")
    assert len(ids) == len(set(ids)), "an ID occurs twice"
    missing = page.evaluate(
        """() => [...document.querySelectorAll('#editor, #controls-list')]
          .flatMap((box) => [...box.querySelectorAll(
            'a[href], button, input, select, textarea, summary')])
          .filter((node) => !node.id)
          .map((node) => node.outerHTML.slice(0, 80))"""
    )
    assert missing == []


def test_another_control_opens_at_once_when_the_draft_has_no_change(page):
    _two_kept_controls(page)
    page.click('button[aria-label="Edit A"]')
    page.click('button[aria-label="Edit B"]')
    assert page.is_hidden("#leave-dialog")
    assert page.input_value("#control-name") == "B"


def test_stay_keeps_the_changed_draft(page):
    _two_kept_controls(page)
    page.click('button[aria-label="Edit A"]')
    page.fill("#control-name", "A2")
    page.click('button[aria-label="Edit B"]')
    assert page.inner_text("#leave-question") == (
        "“A2” has changes you have not saved."
    )
    assert page.inner_text("#leave-save") == "Save and open “B”"
    page.click("#leave-stay")
    assert page.is_hidden("#leave-dialog")
    assert page.input_value("#control-name") == "A2"


def test_discard_opens_the_other_control(page):
    _two_kept_controls(page)
    page.click('button[aria-label="Edit A"]')
    page.fill("#control-name", "A2")
    page.click("#new-control")
    assert page.inner_text("#leave-save") == "Save and open a new Control"
    page.click("#leave-discard")
    assert page.input_value("#control-name") == ""
    assert page.locator("#controls-list td.name-cell a").all_inner_texts() == [
        "B",
        "A",
    ]


def test_save_and_open_saves_then_opens_the_other_control(page):
    _two_kept_controls(page)
    page.click('button[aria-label="Edit A"]')
    page.fill("#control-name", "A2")
    page.click('button[aria-label="Edit B"]')
    with page.expect_response("**/workspace/controls/*"):
        page.click("#leave-save")
    page.wait_for_function(
        "() => document.querySelector('#control-name')?.value === 'B'"
    )
    assert page.inner_text("#controls-notice") == "“A2” is saved."


def test_a_refused_save_and_open_keeps_the_draft(page):
    _two_kept_controls(page)
    page.route(
        "**/workspace/controls/*",
        lambda route: (
            route.fulfill(status=409, json={"message": "Changed elsewhere."})
            if route.request.method == "PUT"
            else route.fallback()
        ),
    )
    page.click('button[aria-label="Edit A"]')
    page.fill("#control-name", "A2")
    page.click('button[aria-label="Edit B"]')
    page.click("#leave-save")
    page.wait_for_selector("#editor-problem:has-text('Changed elsewhere.')")
    assert page.input_value("#control-name") == "A2"


def test_a_scope_change_lists_what_it_removed_and_undo_restores_it(page):
    page.goto("/controls")
    new_control(page, "Orders DB encryption")
    page.fill("#alias-input", "orders")
    page.press("#alias-input", "Enter")
    tick(page, "Encrypts its data at rest.", "AES-256")
    page.select_option("#scope-type", "application")
    assert page.inner_text("#scope-removed").splitlines()[:3] == [
        "This change removed what does not fit the new choice:",
        "its name “Orders DB” and 1 alias",
        "1 fact: “Encrypts its data at rest: AES-256”",
    ]
    page.click("#scope-undo")
    assert page.input_value("#scope-type") == "store"
    assert page.input_value("#scope-name") == "Orders DB"
    assert page.locator(".aliases .tag").all_inner_texts() == ["orders\n×"]
    assert (
        page.input_value(
            "#statements input[aria-label='How: Encrypts its data at rest.']"
        )
        == "AES-256"
    )
    assert page.locator("#scope-removed").count() == 0
    assert page.evaluate("document.activeElement.id") == "scope-type"


def test_a_scope_change_that_removes_nothing_shows_no_notice(page):
    page.goto("/controls")
    new_control(page, "Empty", scope=("store", ""))
    page.select_option("#scope-type", "application")
    assert page.locator("#scope-removed").count() == 0


def test_an_edit_of_the_scope_after_a_scope_change_ends_its_undo(page):
    page.goto("/controls")
    new_control(page, "Orders DB encryption")
    page.select_option("#scope-type", "process")
    assert page.locator("#scope-removed").count() == 1
    page.fill("#scope-name", "Worker")
    assert page.locator("#scope-removed").count() == 0


def test_an_alias_after_a_scope_change_ends_its_undo(page):
    page.goto("/controls")
    new_control(page, "Orders DB encryption")
    page.select_option("#scope-type", "process")
    page.fill("#alias-input", "worker")
    page.press("#alias-input", "Enter")
    assert page.locator("#scope-removed").count() == 0


def test_other_edits_and_a_file_read_keep_the_undo(page):
    page.goto("/controls")
    _hold_file_reads(page)
    new_control(page, "Orders DB encryption")
    add_evidence(page, "rds.tf", "storage_encrypted = true\n")
    page.wait_for_selector("#evidence-list li:has-text('Reading…')")
    page.select_option("#scope-type", "application")
    page.fill("#control-name", "Edge TLS")
    add_row(page, "stride", "spoofing")
    page.evaluate("window.releaseReads()")
    page.wait_for_selector("#evidence-list li:not(:has-text('Reading…'))")
    page.click("#scope-undo")
    assert page.input_value("#scope-name") == "Orders DB"
    assert page.input_value("#control-name") == "Edge TLS"


def test_a_scope_change_that_removes_nothing_keeps_the_undo(page):
    page.goto("/controls")
    new_control(page, "Orders DB encryption")
    page.select_option("#scope-type", "boundary")
    page.select_option("#scope-type", "store")
    page.click("#scope-undo")
    assert page.input_value("#scope-name") == "Orders DB"


def test_the_leave_question_names_a_control_with_no_name(page):
    _two_kept_controls(page)
    page.click('button[aria-label="Edit A"]')
    page.fill("#control-name", "")
    page.click('button[aria-label="Edit B"]')
    assert page.inner_text("#leave-question") == (
        "This Control has changes you have not saved."
    )


def test_save_and_open_waits_for_a_file_read(page):
    _two_kept_controls(page)
    _hold_file_reads(page)
    page.click('button[aria-label="Edit A"]')
    add_evidence(page, "a.txt", "for A\n")
    page.wait_for_selector("#evidence-list li:has-text('Reading…')")
    page.click('button[aria-label="Edit B"]')
    assert page.is_disabled("#leave-save")
    assert page.inner_text("#leave-wait") == "Save waits for the file read to end."
    page.evaluate("window.releaseReads()")
    page.wait_for_selector("#leave-save:enabled")
    assert page.is_hidden("#leave-wait")


def test_save_and_open_keeps_a_draft_opened_during_the_reload(page):
    _two_kept_controls(page)
    page.click('button[aria-label="Edit A"]')
    page.fill("#control-name", "A2")
    page.click('button[aria-label="Edit B"]')
    held = _hold(page, "**/workspace/controls")
    with page.expect_response("**/workspace/controls/*"):
        page.click("#leave-save")
    _wait_for(page, held)
    page.click("#new-control")
    page.fill("#control-name", "Mine")
    with page.expect_response("**/workspace/controls"):
        held[0].continue_()
    page.wait_for_selector("#controls-list td.name-cell a:text-is('A2')")
    assert page.input_value("#control-name") == "Mine"


def test_a_new_control_starts_with_what_it_protects(page):
    page.goto("/controls")
    page.click("#new-control")
    assert page.input_value("#scope-type") == ""
    steps = page.locator(".editor .card-step").all_inner_texts()
    assert steps == ["1 of 4", "2 of 4", "3 of 4 · optional", "4 of 4 · optional"]
    does = page.locator(".q-card:has(h3:text-is('What it does'))")
    assert does.inner_text().endswith(
        "Choose what it protects to see the protections it can state."
    )
    assert page.locator("#scope-name").count() == 0
    page.fill("#control-name", "Edge")
    page.click("#save-control")
    assert page.inner_text("#editor-problem") == "Choose what the Control protects."
    assert page.evaluate("document.activeElement.id") == "scope-type"
    page.select_option("#scope-type", "application")
    assert "3 of 4 · required" in page.locator(".editor .card-step").all_inner_texts()
    assert "Add at least one unit." in page.inner_text(
        ".q-card:has(h3:text-is('What it covers'))"
    )
    assert does.inner_text().endswith("It works through the units it covers.")
    page.select_option("#scope-type", "store")
    covers = page.locator(".q-card:has(h3:text-is('What it covers'))")
    assert "3 of 4 · required" in page.locator(".editor .card-step").all_inner_texts()
    assert "Tick a protection above, or add at least one unit." in covers.inner_text()
    tick(page, "Encrypts its data at rest.", "AES-256")
    assert "3 of 4 · optional" in page.locator(".editor .card-step").all_inner_texts()
    assert "Tick a protection above" not in covers.inner_text()
    assert page.locator("#scope-name").count() == 1


def test_an_existing_control_lists_no_placeholder(page):
    _two_kept_controls(page)
    page.click('button[aria-label="Edit A"]')
    assert page.input_value("#scope-type") == "application"
    assert page.locator("#scope-type option[value='']").count() == 0


def test_the_evidence_card_asks_the_owner_to_remove_each_credential(page):
    page.goto("/controls")
    new_control(page, "Notes")
    note = page.inner_text("#evidence-note")
    assert note.startswith("Remove each password, key and token from a file")
    assert "it does not find every form" in note


def test_the_reach_note_says_what_the_whole_application_means(page):
    page.goto("/controls")
    new_control(page, "TLS at the edge")
    add_row(page, "asvs", "V2.4.1")
    note = page.inner_text("#rows-reach")
    assert "only when the protection covers every part of the application" in note
    assert "removes the name and the facts of this part" in note


def test_leaving_with_a_changed_draft_asks_first(page):
    _two_kept_controls(page)
    asked = page.evaluate(
        """() => { const event = new Event('beforeunload', { cancelable: true });
          window.dispatchEvent(event); return event.defaultPrevented; }"""
    )
    assert not asked
    page.click('button[aria-label="Edit A"]')
    page.fill("#control-name", "A2")
    asked = page.evaluate(
        """() => { const event = new Event('beforeunload', { cancelable: true });
          window.dispatchEvent(event); return event.defaultPrevented; }"""
    )
    assert asked


def test_a_failed_save_gives_focus_back(page):
    _two_kept_controls(page)
    page.route(
        "**/workspace/controls/*",
        lambda route: (
            route.fulfill(status=409, json={"message": "Changed elsewhere."})
            if route.request.method == "PUT"
            else route.fallback()
        ),
    )
    page.click('button[aria-label="Edit A"]')
    page.focus("#save-control")
    page.keyboard.press("Enter")
    page.wait_for_selector("#editor-problem:has-text('Changed elsewhere.')")
    assert page.evaluate("document.activeElement.id") == "save-control"


def test_the_controls_page_saves_each_card_and_shows_values_as_text(page):
    page.goto("/controls")
    assert page.get_attribute("#controls-nav", "aria-current") == "page"
    assert page.inner_text("#controls-empty") == "No Controls yet."
    assert page.inner_text("#controls-count") == "0 of 50 Controls"
    new_control(page, HOSTILE_CONTROL)
    page.fill("#alias-input", "orders <i>database</i>")
    page.press("#alias-input", "Enter")
    tick(page, "Encrypts its data at rest.", "AES-256 <script>window.ran=1</script>")
    tick(page, "Encrypts its backups and snapshots.")
    tick(page, "The internet cannot reach it.")
    add_row(page, "asvs", "V11.2")
    add_row(page, "stride", "information-disclosure")
    add_evidence(page, "<b>rds</b>.tf", "storage_encrypted = true\n", "code")
    page.wait_for_selector("#evidence-list li")
    save_control(page)
    assert page.inner_text("#controls-notice") == f"“{HOSTILE_CONTROL}” is saved."
    assert page.inner_text("#controls-count") == "1 of 50 Controls"
    cells = page.locator("#controls-list tr td")
    assert page.inner_text("#controls-list td.name-cell a") == HOSTILE_CONTROL
    assert (
        "Encrypts its data at rest: AES-256 <script>window.ran=1</script>;"
        " Encrypts its backups and snapshots" in cells.nth(1).inner_text()
    )
    assert cells.nth(2).inner_text() == "The data store “Orders DB”"
    assert cells.nth(3).inner_text().split("\n") == [
        "asvs V11.2",
        "stride information-disclosure",
    ]
    assert cells.nth(4).inner_text() == "<b>rds</b>.tf (Code)"
    assert page.locator("#controls-list img, #controls-list b").count() == 0
    assert page.evaluate("window.ran") is None
    [control] = page.evaluate("fetch('/workspace/controls').then((r) => r.json())")
    assert control["scope"]["aliases"] == ["orders <i>database</i>"]
    assert control["facts"][0]["value"] == "AES-256 <script>window.ran=1</script>"
    assert [fact.get("facets") for fact in control["facts"][1:]] == [
        {"encrypted": "yes"},
        {"internet": "no"},
    ]

    # An edit opens the same editor with the Control's values, and replaces it.
    page.click("#controls-list button:text('Edit')")
    assert page.input_value("#control-name") == HOSTILE_CONTROL
    assert page.input_value("#scope-name") == "Orders DB"
    assert "orders <i>database</i>" in page.inner_text(".aliases")
    page.fill("#control-name", "Orders DB encrypted")
    assert page.get_by_label("The internet cannot reach it.", exact=True).is_checked()
    page.get_by_label("The internet cannot reach it.", exact=True).uncheck()
    page.fill("#statements input[aria-label='How: Encrypts its data at rest.']", "KMS")
    save_control(page)
    assert page.inner_text("#controls-list td.name-cell a") == "Orders DB encrypted"
    [edited] = page.evaluate("fetch('/workspace/controls').then((r) => r.json())")
    assert edited["id"] == control["id"]
    assert [fact["value"] for fact in edited["facts"]] == [
        "KMS",
        "Are its backups and snapshots encrypted? yes",
    ]
    assert edited["evidence"] == control["evidence"]


def test_the_editor_warns_of_a_row_that_a_part_cannot_reach(page):
    page.goto("/controls")
    new_control(page, "TLS at the edge")
    cards = page.locator(".editor .q-card h3")
    assert cards.all_inner_texts() == [
        "What it protects",
        "What it does",
        "What it covers",
        "Evidence",
    ]
    # An empty list takes no space, so no second line shows under the title.
    assert not page.locator("#rows-list").is_visible()
    add_row(page, "stride", "spoofing")
    assert page.locator("#rows-reach").count() == 0
    add_row(page, "asvs", "V2.4.1")
    assert page.inner_text("#rows-reach").startswith("Many findings of asvs")

    # The description of a card opens from its "?".
    about = page.locator(".q-card:has(h3:text-is('What it does')) .question-help")
    assert not about.locator(".question-help-content").is_visible()
    about.locator("summary").click()
    assert "Tick each protection the part has." in about.inner_text()
    assert "fills a fact that the description leaves open" in about.inner_text()
    evidence = page.locator(".q-card:has(h3:text-is('Evidence')) .question-help")
    evidence.locator("summary").click()
    assert "With no row, a file has no effect." in evidence.inner_text()

    page.select_option("#scope-type", "application")
    assert page.locator("#rows-reach").count() == 0


def test_a_statement_that_asks_how_needs_the_how(page):
    page.goto("/controls")
    new_control(page, "Orders DB encrypted")
    tick(page, "Encrypts its data at rest.")
    page.click("#save-control")
    assert page.inner_text("#editor-problem") == (
        "Write how the part does this: “Encrypts its data at rest.”"
    )


@pytest.mark.parametrize(
    ("fill", "message"),
    [
        (lambda page: new_control(page, " "), "Give the Control a name."),
        (
            lambda page: new_control(page, "WAF", scope=("process", "")),
            "Name what the Control protects, or choose the whole application.",
        ),
        (
            lambda page: new_control(page, "WAF"),
            "Add at least one fact or one row. A Control with neither changes nothing.",
        ),
    ],
)
def test_each_save_refusal_shows_its_message(page, fill, message):
    page.goto("/controls")
    fill(page)
    if "fact or one row" not in message:
        add_row(page, "stride", "spoofing")
    page.click("#save-control")
    page.wait_for_selector("#editor-problem:not([hidden])")
    assert page.inner_text("#editor-problem") == message


def test_a_repeated_row_and_name_and_a_large_file_are_refused(page):
    page.goto("/controls")
    new_control(page, "WAF")
    add_row(page, "stride", "spoofing")
    # The page offers no second Add for a unit that is a row; the service
    # refuses a repeated row too (tests/test_controls.py).
    assert page.locator("[id='unit-add-stride-spoofing']").count() == 0
    save_control(page)
    new_control(page, "WAF")
    add_row(page, "stride", "tampering")
    page.click("#save-control")
    page.wait_for_selector("#editor-problem:not([hidden])")
    assert page.inner_text("#editor-problem") == (
        "You already have a Control named “WAF”. Give this Control a different name."
    )
    add_evidence(page, "big.txt", "x" * 10_001)
    page.wait_for_function(
        "() => document.querySelector('#editor-problem').textContent.includes('larger')"
    )
    assert page.inner_text("#editor-problem") == (
        "This file is larger than the 10,000 bytes one evidence file can hold."
    )


def test_a_delete_and_a_multi_delete_ask_first(page):
    page.goto("/controls")
    for name in ("One", "Two", "Three"):
        new_control(page, name, scope=("application", ""))
        add_row(page, "stride", "spoofing")
        save_control(page)
    page.click("#controls-list tr:has-text('Three') button:text('Delete')")
    page.wait_for_selector("#delete-dialog[open]")
    assert page.inner_text("#delete-question") == (
        "Delete “Three”? Each analysis that already applied it keeps its own copy,"
        " and its reports do not change. You cannot undo this."
    )
    page.click("#delete-go")
    page.wait_for_selector("#controls-list tr:has-text('Three')", state="detached")
    assert page.inner_text("#controls-notice") == (
        "“Three” is deleted. The analyses that applied it keep their copy."
    )
    page.check("#select-shown")
    assert page.inner_text("#delete-selected") == "Delete selected (2)"
    page.click("#delete-selected")
    page.wait_for_selector("#delete-dialog[open]")
    assert page.inner_text("#delete-question").startswith("Delete 2 Controls?")
    page.click("#delete-go")
    page.wait_for_selector("#controls-empty:not([hidden])")
    assert page.inner_text("#controls-notice") == (
        "2 Controls are deleted. The analyses that applied them keep their copy."
    )


@pytest.fixture
def zoned_page(browser, tiers):
    """A page on the app over the zoned model of the Control tests."""
    from tests.test_webapp_controls import ZonedRunner

    app = web.app_for(tiers, ZonedRunner())
    with contextmanager(serve)(app) as base:
        context = browser.new_context(base_url=base)
        opened = context.new_page()
        errors: list[str] = []
        opened.on("pageerror", lambda error: errors.append(str(error)))
        opened.on("dialog", lambda dialog: errors.append(f"dialog: {dialog.message}"))
        opened.goto("/controls")
        yield opened
        context.close()
        assert errors == [], f"the page raised: {errors}"


def keep_control(page, name: str, scope: dict, *, facts=(), rows=None) -> str:
    """Keep a Control through the page's own route, and return its ID."""
    body = {
        "name": name,
        "scope": {"name": "", "aliases": []} | scope,
        "facts": list(facts),
        "rows": rows
        if rows is not None
        else [{"framework": "stride", "unit": "spoofing", "version": "1.0"}],
        "evidence": [],
    }
    saved = page.evaluate(
        """(body) => fetch('/workspace/controls', {method: 'POST',
             headers: {'Content-Type': 'application/json'},
             body: JSON.stringify(body)}).then((r) => r.json())""",
        body,
    )
    return saved["id"]


def submit_with_controls(page) -> dict:
    """Submit the form with its ticked Controls, and wait for the pause."""
    page.goto("/")
    page.wait_for_selector("#controls-offer:not([hidden])")
    page.fill("#description", "A web app talks to a database.")
    page.fill("#analysis-name", "Controls analysis")
    open_frameworks(page)
    page.locator('input[name="framework"]').first.check()
    close_frameworks(page)
    with page.expect_response("**/analyze") as answered:
        page.click("#go")
    assert answered.value.ok, answered.value.text()
    page.wait_for_selector("#control-offer .offer-line")
    return answered.value.request.post_data_json


def save_offer(page, action) -> dict:
    """Do ``action`` on the offer, and return the save it sends."""
    with page.expect_response(_post_to_answer) as answered:
        action()
    assert answered.value.ok, answered.value.text()
    page.wait_for_timeout(100)
    return answered.value.request.post_data_json


def _hold_answers(page) -> list:
    """Hold each POST to /answer/, and return the held routes."""
    held: list = []
    page.route(
        "**/answer/*",
        lambda route: (
            held.append(route) if route.request.method == "POST" else route.fallback()
        ),
    )
    return held


def _release(page, held: list, count: int) -> None:
    """Wait for ``count`` held answers in turn, and send each one on."""
    for at in range(count):
        for _ in range(250):
            if len(held) > at:
                break
            page.wait_for_timeout(20)
        with page.expect_response(_post_to_answer):
            held[at].continue_()


def test_rapid_control_changes_save_the_latest_choice(zoned_page):
    page = zoned_page
    first = keep_control(page, "First", {"type": "application"})
    second = keep_control(page, "Second", {"type": "application"})
    submit_with_controls(page)
    held = _hold_answers(page)
    page.click(f"#offer-{first}")
    page.click(f"#offer-{second}")
    assert page.is_visible("#scope-status")
    assert not page.is_checked(f"#offer-{second}")
    _release(page, held, 2)
    page.wait_for_selector("#scope-status", state="hidden")
    sent = [route.request.post_data_json["control_scopes"] for route in held]
    assert sent[-1] == []
    assert not page.is_checked(f"#offer-{first}")
    assert not page.is_checked(f"#offer-{second}")
    page.reload()
    page.wait_for_selector(f"#offer-{first}")
    assert not page.is_checked(f"#offer-{first}")
    assert not page.is_checked(f"#offer-{second}")


def test_a_question_save_during_a_control_save_takes_the_next_revision(zoned_page):
    page = zoned_page
    first = keep_control(page, "First", {"type": "application"})
    submit_with_controls(page)
    held = _hold_answers(page)
    page.click(f"#offer-{first}")
    page.locator("#list select").first.select_option(index=1)
    # Longer than the autosave delay: a question save that does not wait for
    # the Control save goes out now.
    page.wait_for_timeout(600)
    _release(page, held, 2)
    scope, question = (route.request.post_data_json for route in held[:2])
    assert "control_scopes" in scope
    assert question["revision"] == scope["revision"] + 1


def test_a_double_toggle_saves_the_control_ticked(zoned_page):
    page = zoned_page
    first = keep_control(page, "First", {"type": "application"})
    submit_with_controls(page)
    held = _hold_answers(page)
    page.click(f"#offer-{first}")
    page.click(f"#offer-{first}")
    _release(page, held, 2)
    page.wait_for_selector("#scope-status", state="hidden")
    assert held[-1].request.post_data_json["control_scopes"] == [{"control": first}]
    assert page.is_checked(f"#offer-{first}")


def test_a_failed_control_save_shows_what_the_service_kept(zoned_page):
    page = zoned_page
    first = keep_control(page, "First", {"type": "application"})
    submit_with_controls(page)
    page.route(
        "**/answer/*",
        lambda route: (
            route.abort() if route.request.method == "POST" else route.fallback()
        ),
    )
    page.click(f"#offer-{first}")
    page.wait_for_selector("#answer-problem:has-text('was not saved')")
    page.wait_for_selector("#scope-status", state="hidden")
    assert page.is_checked(f"#offer-{first}")


def test_start_stays_off_while_the_control_choices_save(zoned_page):
    page = zoned_page
    first = keep_control(page, "First", {"type": "application"})
    submit_with_controls(page)
    held = _hold_answers(page)
    page.click(f"#offer-{first}")
    assert page.is_disabled("#continue")
    _release(page, held, 1)
    page.wait_for_selector("#continue:enabled")


def test_a_zone_control_fans_out_and_the_owner_ticks_a_part(zoned_page):
    page = zoned_page
    zone = keep_control(
        page, "Internal <b>WAF</b>", {"type": "boundary", "name": "Internal Network"}
    )
    sent = submit_with_controls(page)
    assert sent["controls"] == [zone]
    offer = page.locator("#control-offer")
    assert "matches Internal Network" in offer.inner_text()
    assert page.locator("#control-offer b").first.inner_text() == "Internal <b>WAF</b>"
    fanout = page.locator("#control-offer .fanout li")
    assert [text.strip() for text in fanout.all_inner_texts()] == [
        "Web App — a source places this part in the zone",
        "Orders DB — the service inferred this placement",
        "Audit Log — no source places this part",
    ]
    assert page.is_checked(f"#cover-{zone}-process\\:web-app")
    assert not page.is_checked(f"#cover-{zone}-store\\:orders-db")
    body = save_offer(page, lambda: page.check(f"#cover-{zone}-store\\:orders-db"))
    assert body["control_scopes"] == [
        {
            "control": zone,
            "element": "boundary:internal-network",
            "ticked": ["process:web-app", "store:orders-db"],
        }
    ]
    assert page.is_checked(f"#cover-{zone}-store\\:orders-db")
    assert "1 Control of 1 will apply." in offer.inner_text()


def test_an_unmatched_control_takes_a_choice_and_keeps_the_name(zoned_page):
    page = zoned_page
    waf = keep_control(page, "WAF", {"type": "process", "name": "Edge"})
    submit_with_controls(page)
    offer = page.locator("#control-offer")
    assert "left out: no part of this description has the name" in offer.inner_text()
    assert page.is_disabled(f"#offer-{waf}")
    choices = page.locator(f"#apply-{waf} option").all_inner_texts()
    assert choices == ["Not this time", "Web App"]
    body = save_offer(
        page, lambda: page.select_option(f"#apply-{waf}", "process:web-app")
    )
    assert body["control_scopes"] == [{"control": waf, "element": "process:web-app"}]
    page.wait_for_selector("#control-offer >> text=matches Web App")
    assert page.is_checked(f"#offer-{waf}")
    save_offer(page, lambda: page.click("#control-offer button:text('Keep this name')"))
    assert "“Web App” is now an alias of “WAF”." in offer.inner_text()
    [control] = page.evaluate("fetch('/workspace/controls').then((r) => r.json())")
    assert control["scope"]["aliases"] == ["Web App"]


def test_a_zone_chosen_with_no_tick_keeps_its_parts_for_a_tick(zoned_page):
    page = zoned_page
    edge = keep_control(page, "Edge", {"type": "boundary", "name": "Perimeter"})
    submit_with_controls(page)
    body = save_offer(
        page, lambda: page.select_option(f"#apply-{edge}", "boundary:dmz")
    )
    assert body["control_scopes"] == [
        {"control": edge, "element": "boundary:dmz", "ticked": []}
    ]
    assert page.input_value(f"#apply-{edge}") == "boundary:dmz"
    offer = page.locator("#control-offer")
    assert "Tick each part of the DMZ that this Control protects." in offer.inner_text()
    assert not page.is_checked(f"#cover-{edge}-store\\:audit-log")
    body = save_offer(page, lambda: page.check(f"#cover-{edge}-store\\:audit-log"))
    assert body["control_scopes"] == [
        {"control": edge, "element": "boundary:dmz", "ticked": ["store:audit-log"]}
    ]
    page.wait_for_selector("#control-offer >> text=matches DMZ")
    assert page.is_checked(f"#offer-{edge}")


def test_unticking_the_last_part_keeps_the_zone_with_no_tick(zoned_page):
    page = zoned_page
    zone = keep_control(page, "WAF", {"type": "boundary", "name": "Internal Network"})
    submit_with_controls(page)
    body = save_offer(page, lambda: page.uncheck(f"#cover-{zone}-process\\:web-app"))
    assert body["control_scopes"] == [
        {"control": zone, "element": "boundary:internal-network", "ticked": []}
    ]
    offer = page.locator("#control-offer")
    assert "left out: it is not ticked" in offer.inner_text()
    assert not page.is_checked(f"#cover-{zone}-process\\:web-app")
    assert "Tick each part of the Internal Network" in offer.inner_text()


def test_a_change_to_a_second_control_keeps_a_zone_chosen_with_no_tick(zoned_page):
    page = zoned_page
    edge = keep_control(page, "Edge", {"type": "boundary", "name": "Perimeter"})
    other = keep_control(page, "Other", {"type": "application"})
    submit_with_controls(page)
    save_offer(page, lambda: page.select_option(f"#apply-{edge}", "boundary:dmz"))
    body = save_offer(page, lambda: page.uncheck(f"#offer-{other}"))
    assert page.input_value(f"#apply-{edge}") == "boundary:dmz"
    assert "left out: it is not ticked" in page.inner_text("#control-offer")
    assert body["control_scopes"] == [
        {"control": edge, "element": "boundary:dmz", "ticked": []}
    ]
    body = save_offer(page, lambda: page.check(f"#cover-{edge}-store\\:audit-log"))
    assert body["control_scopes"] == [
        {"control": edge, "element": "boundary:dmz", "ticked": ["store:audit-log"]}
    ]


def test_a_change_to_a_second_control_keeps_a_matched_zone_with_no_tick(zoned_page):
    page = zoned_page
    zone = keep_control(page, "WAF", {"type": "boundary", "name": "Internal Network"})
    other = keep_control(page, "Other", {"type": "application"})
    submit_with_controls(page)
    save_offer(page, lambda: page.uncheck(f"#cover-{zone}-process\\:web-app"))
    body = save_offer(page, lambda: page.uncheck(f"#offer-{other}"))
    assert body["control_scopes"] == [
        {"control": zone, "element": "boundary:internal-network", "ticked": []}
    ]
    assert not page.is_checked(f"#cover-{zone}-process\\:web-app")
    assert not page.is_checked(f"#offer-{zone}")


def test_ticking_a_zone_control_on_again_restores_its_default_ticks(zoned_page):
    page = zoned_page
    zone = keep_control(page, "WAF", {"type": "boundary", "name": "Internal Network"})
    submit_with_controls(page)
    save_offer(page, lambda: page.uncheck(f"#offer-{zone}"))
    body = save_offer(page, lambda: page.check(f"#offer-{zone}"))
    assert body["control_scopes"] == [
        {
            "control": zone,
            "element": "boundary:internal-network",
            "ticked": ["process:web-app"],
        }
    ]
    page.wait_for_selector("#control-offer >> text=matches Internal Network")
    assert page.is_checked(f"#cover-{zone}-process\\:web-app")


def test_a_zone_chosen_in_an_earlier_run_leaves_a_later_match(zoned_page):
    page = zoned_page
    edge = keep_control(page, "Edge", {"type": "boundary", "name": "Perimeter"})
    submit_with_controls(page)
    with page.expect_response(_post_to_answer) as answered:
        page.select_option(f"#apply-{edge}", "boundary:dmz")
    data = answered.value.json()
    # The next run of an amended description matches the Control's own zone.
    data["run"] = "job-amended"
    [entry] = data["controls"]
    entry |= {
        "match": "name",
        "element": {"id": "boundary:internal-network", "name": "Internal Network"},
        "choices": [],
        "covers": [
            {
                "element": "process:web-app",
                "name": "Web App",
                "reason": "stated-zone",
                "ticked": True,
            }
        ],
        "resolution": {"state": "left-out", "control": edge, "reason": "unticked"},
    }
    page.evaluate(
        """(data) => formControls.drawOffer(document.getElementById("control-offer"),
          data, {save() {}, refuse() {}})""",
        data,
    )
    assert page.locator(f"#apply-{edge}").count() == 0
    assert page.locator(f"#cover-{edge}-process\\:web-app").count() == 1
    assert not page.is_checked(f"#cover-{edge}-process\\:web-app")


def test_a_zone_chosen_in_an_earlier_run_is_not_chosen_again(zoned_page):
    page = zoned_page
    edge = keep_control(page, "Edge", {"type": "boundary", "name": "Perimeter"})
    submit_with_controls(page)
    with page.expect_response(_post_to_answer) as answered:
        page.select_option(f"#apply-{edge}", "boundary:dmz")
    data = answered.value.json()
    # The next run of an amended description leaves the Control unmatched
    # again, with the same choices.
    data["run"] = "job-amended"
    [entry] = data["controls"]
    entry["resolution"] = {
        "state": "left-out",
        "control": edge,
        "reason": "missing-element",
    }
    page.evaluate(
        """(data) => formControls.drawOffer(document.getElementById("control-offer"),
          data, {save() {}, refuse() {}})""",
        data,
    )
    assert page.input_value(f"#apply-{edge}") == ""


def test_a_refused_zone_choice_is_not_kept(zoned_page):
    page = zoned_page
    edge = keep_control(page, "Edge", {"type": "boundary", "name": "Perimeter"})
    submit_with_controls(page)
    page.route(
        "**/answer/*",
        lambda route: (
            route.abort() if route.request.method == "POST" else route.fallback()
        ),
    )
    page.select_option(f"#apply-{edge}", "boundary:dmz")
    page.wait_for_selector("#answer-problem:has-text('was not saved')")
    page.wait_for_selector("#scope-status", state="hidden")
    assert page.input_value(f"#apply-{edge}") == ""


def test_a_refused_change_of_zone_shows_the_saved_zone(zoned_page):
    page = zoned_page
    edge = keep_control(page, "Edge", {"type": "boundary", "name": "Perimeter"})
    submit_with_controls(page)
    save_offer(page, lambda: page.select_option(f"#apply-{edge}", "boundary:dmz"))
    assert page.input_value(f"#apply-{edge}") == "boundary:dmz"
    page.route(
        "**/answer/*",
        lambda route: (
            route.abort() if route.request.method == "POST" else route.fallback()
        ),
    )
    page.select_option(f"#apply-{edge}", "boundary:internal-network")
    page.wait_for_selector("#answer-problem:has-text('was not saved')")
    page.wait_for_selector("#scope-status", state="hidden")
    assert page.input_value(f"#apply-{edge}") == "boundary:dmz"


def test_turning_off_a_chosen_zone_shows_no_tick(zoned_page):
    page = zoned_page
    edge = keep_control(page, "Edge", {"type": "boundary", "name": "Perimeter"})
    submit_with_controls(page)
    save_offer(
        page,
        lambda: page.select_option(f"#apply-{edge}", "boundary:internal-network"),
    )
    page.wait_for_selector("#control-offer >> text=matches Internal Network")
    save_offer(page, lambda: page.uncheck(f"#offer-{edge}"))
    assert page.locator("#control-offer .fanout input:checked").count() == 0


def test_two_controls_with_two_values_are_refused_until_one_is_unticked(zoned_page):
    page = zoned_page
    db = {"type": "store", "name": "Orders DB"}
    key = ["", "encryption_at_rest", "", "", "", ""]
    aes = keep_control(page, "AES", db, facts=[{"key": key, "value": "AES-256"}])
    kms = keep_control(page, "KMS", db, facts=[{"key": key, "value": "KMS"}])
    submit_with_controls(page)
    assert "Is data stored in Orders DB encrypted?: AES-256" in page.inner_text(
        "#control-offer"
    )
    message = (
        "The Controls “KMS” and “AES” write “encryption_at_rest” on Orders DB"
        " with two values: “KMS” and “AES-256”. Untick one of them, then"
        " continue."
    )
    page.click("#continue")
    page.wait_for_selector("#answer-problem:not([hidden])")
    assert page.inner_text("#answer-problem") == message
    body = save_offer(page, lambda: page.uncheck(f"#offer-{aes}"))
    assert body["control_scopes"] == [{"control": kms, "element": "store:orders-db"}]
    assert page.is_hidden("#answer-problem")
    assert not page.is_checked(f"#offer-{aes}")
    assert "left out: it is not ticked" in page.inner_text("#control-offer")


def test_a_job_the_app_did_not_continue_waits_for_the_owner(zoned_page, monkeypatch):
    """A job that waits to continue, with no task and no stored refusal,
    shows why, and the page does not continue it by itself (ADR 0125)."""
    from webapp.main import _NOT_CONTINUED, Workspace

    async def ended_first(*_):
        """The process ends after the pause and before the continue."""

    monkeypatch.setattr(Workspace, "_continue", ended_first)
    page = zoned_page
    key = ["", "encryption_at_rest", "", "", "", ""]
    db = {"type": "store", "name": "Orders DB"}
    keep_control(page, "AES", db, facts=[{"key": key, "value": "AES-256"}])
    page.goto("/")
    page.wait_for_selector("#controls-offer:not([hidden])")
    page.fill("#description", "A web app talks to a database.")
    page.fill("#analysis-name", "Controls analysis")
    open_frameworks(page)
    page.locator('input[name="framework"]').first.check()
    close_frameworks(page)
    page.uncheck("#ask")
    continues: list[str] = []
    page.on(
        "request",
        lambda request: (
            continues.append(request.url) if "/answer/" in request.url else None
        ),
    )
    page.click("#go")
    page.wait_for_selector("#answer-problem:not([hidden])")
    assert page.inner_text("#answer-problem") == _NOT_CONTINUED
    assert page.is_visible("#continue")
    assert continues == []


def test_a_control_that_fits_no_selected_framework_takes_no_tick(zoned_page):
    page = zoned_page
    spoofing = keep_control(page, "Spoof guard", {"type": "application"})
    page.goto("/")
    page.wait_for_selector(f"#control-{spoofing}", state="attached")
    open_frameworks(page)
    stride = page.locator('#frameworks input[name="framework"][value="stride"]')
    asvs = page.locator('#frameworks input[name="framework"][value="asvs"]')
    asvs.check()
    stride.uncheck()
    # Its one row is STRIDE's and it states no fact, so ASVS alone takes none.
    assert page.is_disabled(f"#control-{spoofing}")
    assert not page.is_checked(f"#control-{spoofing}")
    assert "Not for this analysis" in page.text_content("#controls-unfit-rows")
    stride.check()
    assert page.is_enabled(f"#control-{spoofing}")
    assert page.is_checked(f"#control-{spoofing}")


def test_a_control_whose_rows_are_of_an_older_version_takes_no_tick(zoned_page):
    page = zoned_page
    old_rows = [{"framework": "stride", "unit": "spoofing", "version": "0.9"}]
    old = keep_control(page, "Old guard", {"type": "application"}, rows=old_rows)
    page.goto("/")
    page.wait_for_selector(f"#control-{old}", state="attached")
    open_frameworks(page)
    page.locator('#frameworks input[name="framework"][value="stride"]').check()
    assert page.is_disabled(f"#control-{old}")
    assert (
        "its rows are not at the framework version this service carries"
        in page.text_content("#controls-unfit-rows")
    )


def test_the_form_and_the_service_agree_on_a_control_that_changes_nothing(
    zoned_page,
):
    """The form's check (form_controls.js ``fits``) and the service's
    (``job_controls.changes_nothing``), held against each other over each
    kind of row, in a STRIDE-only analysis."""
    from analysis_service.frameworks import PACKAGES
    from tests.test_webapp import posted

    page = zoned_page
    stride = PACKAGES["stride"].version
    db = {"type": "store", "name": "Orders DB"}
    fact = {"key": ["", "encryption_at_rest", "", "", "", ""], "value": "AES-256"}
    current = {"framework": "stride", "unit": "spoofing", "version": stride}
    old = current | {"version": f"{stride}-old"}
    asvs = {"framework": "asvs", "unit": "V11.2", "version": PACKAGES["asvs"].version}
    cases = {
        "Current row": ({"type": "application"}, [], [current], True),
        "Old row": ({"type": "application"}, [], [old], False),
        "Other framework": ({"type": "application"}, [], [asvs], False),
        "Fact and old row": (db, [fact], [old], True),
    }
    ids = {
        name: keep_control(page, name, scope, facts=facts, rows=rows)
        for name, (scope, facts, rows, _) in cases.items()
    }
    page.goto("/")
    open_frameworks(page)
    page.locator('#frameworks input[name="framework"][value="stride"]').check()
    page.locator('#frameworks input[name="framework"][value="asvs"]').uncheck()
    body = posted("A web app talks to a database.", ("stride",))
    for name, (*_, fits) in cases.items():
        control = ids[name]
        page.wait_for_selector(f"#control-{control}", state="attached")
        status = page.evaluate(
            """(body) => fetch('/analyze', {method: 'POST',
                 headers: {'Content-Type': 'application/json'},
                 body: JSON.stringify(body)}).then((r) => r.status)""",
            body | {"controls": [control]},
        )
        assert page.is_enabled(f"#control-{control}") is fits, name
        assert (status == 200) is fits, (name, status)


def test_a_control_shows_each_element_as_its_tag(zoned_page):
    page = zoned_page
    key = ["", "encryption_at_rest", "", "", "", ""]
    keep_control(
        page,
        "Encrypted",
        {"type": "store", "name": "Orders DB"},
        facts=[{"key": key, "value": "AES-256"}],
    )
    submit_with_controls(page)
    tags = page.locator("#questions .ref:not(.neutral)")
    assert tags.count() > 0
    assert page.evaluate(
        "() => [...document.querySelectorAll('#questions .ref:not(.neutral)')]"
        ".every((tag) => tag.style.getPropertyValue('--h') !== '')"
    )
    with page.expect_response("**/answer/**"):
        page.click("#continue")
    page.wait_for_url("**/report/**", timeout=20_000)
    reached = page.locator('#controls-section .ref[title="store:orders-db"]')
    assert reached.inner_text() == "Orders DB"
    assert reached.evaluate("(tag) => tag.style.getPropertyValue('--h')") != ""


def test_the_tag_colours_follow_the_theme_on_every_page(page):
    """The tag tokens live in the shared style, so a page other than the
    report takes the dark ones from the sidebar's theme switch."""
    page.emulate_media(color_scheme="light")
    page.goto("/")
    fill = (
        "() => getComputedStyle(document.documentElement)"
        ".getPropertyValue('--tag-fill-l').trim()"
    )
    assert page.evaluate(fill) == "96%"
    page.get_by_role("button", name="Dark theme").click()
    assert page.evaluate(fill) == "18%"


def test_the_controls_line_names_the_ticked_controls_and_the_dialog_chooses(
    zoned_page,
):
    """The settings column names up to three ticked Controls, then a count;
    the dialog finds a Control by name, ticks and unticks what it shows, and
    Enter in its find box submits nothing."""
    page = zoned_page
    ids = [keep_control(page, name, {"type": "application"}) for name in "ABCDE"]
    page.goto("/")
    page.wait_for_selector(f"#control-{ids[-1]}", state="attached")
    # A form that would submit, so Enter in the find box would send it.
    page.fill("#analysis-name", "Controls analysis")
    page.fill("#description", "A web app talks to a database.")
    open_frameworks(page)
    page.check('input[name="framework"][value="stride"]')
    close_frameworks(page)
    line = page.locator("#controls-summary")
    assert line.inner_text() == "5 ticked: E, D, C and 2 more."
    assert page.is_hidden("#controls-rows")
    page.click("#controls-choose")
    page.fill("#controls-find", "d")
    assert page.is_hidden(f"#control-{ids[0]}")
    assert page.is_visible(f"#control-{ids[3]}")
    page.click("#controls-none")
    assert line.inner_text() == "4 ticked: E, C, B and 1 more."
    assert page.inner_text("#controls-count") == "4 of 5 ticked"
    sent: list[str] = []
    page.on("request", lambda request: sent.append(request.url))
    page.press("#controls-find", "Enter")
    page.wait_for_timeout(300)
    assert not [url for url in sent if url.endswith("/analyze")]
    page.fill("#controls-find", "")
    page.uncheck(f"#control-{ids[0]}")
    assert page.evaluate("document.activeElement.id") == f"control-{ids[0]}"
    page.click("#controls-all")
    assert line.inner_text() == "5 ticked: E, D, C and 2 more."
    page.click("#controls-done")
    assert page.is_hidden("#controls-dialog")


def test_a_deleted_control_shows_missing_and_a_reopened_analysis_lists_its_copy(
    zoned_page,
):
    page = zoned_page
    gone = keep_control(page, "Gone", {"type": "application"})
    page.goto("/")
    page.wait_for_selector(f"#control-{gone}", state="attached")
    assert page.is_checked(f"#control-{gone}")
    page.evaluate(f"fetch('/workspace/controls/{gone}', {{method: 'DELETE'}})")
    page.fill("#description", "A web app talks to a database.")
    page.fill("#analysis-name", "Gone control")
    open_frameworks(page)
    page.locator('input[name="framework"]').first.check()
    close_frameworks(page)
    page.click("#go")
    page.wait_for_selector("#problem:not([hidden])")
    assert "no longer in Controls" in page.inner_text("#problem")
    assert "1 ticked Control is missing." in page.inner_text("#controls-summary")
    page.click("#controls-choose")
    page.wait_for_selector("#controls-rows li >> text=Missing")
    page.click("#controls-rows button:text('Remove')")
    # The list is empty now, so the line and its dialog close.
    assert page.is_hidden("#controls-dialog")

    kept = keep_control(page, "SSO", {"type": "application"})
    page.reload()
    page.wait_for_selector(f"#control-{kept}", state="attached")
    page.uncheck("#ask")
    assert page.is_enabled(f"#control-{kept}")
    page.check("#ask")
    sent = submit_with_controls(page)
    assert sent["controls"] == [kept]
    run = (
        page.evaluate("new URLSearchParams(location.search).get('run')")
        or page.evaluate("fetch('/workspace/runs').then((r) => r.json())")[0]["run"]
    )
    page.goto(f"/?run={run}")
    page.wait_for_selector("#asked", state="visible")
    page.click("#description-nav")
    page.wait_for_selector("#controls-used", state="visible")
    assert page.inner_text("#controls-used-list") == "SSO (applied)"
    assert page.is_hidden("#controls-offer")


def _fail_the_first_list(page, failure: str) -> None:
    """Fail the page's first read of the Controls list in ``failure``'s way."""
    failed: list[str] = []

    def answer(route):
        if route.request.method != "GET" or failed:
            route.fallback()
            return
        failed.append(route.request.url)
        if failure == "network":
            route.abort()
        elif failure == "http":
            route.fulfill(status=500, body="{}", content_type="application/json")
        else:
            route.fulfill(body="not json", content_type="application/json")

    page.route("**/workspace/controls", answer)


@pytest.mark.parametrize("failure", ["http", "malformed", "network"])
def test_a_failed_controls_list_blocks_the_start_until_a_retry(zoned_page, failure):
    """A failed first read of the list says so and holds the start; Retry
    ticks every Control, as a first successful read does."""
    page = zoned_page
    kept = keep_control(page, "Guard", {"type": "application"})
    _fail_the_first_list(page, failure)
    page.goto("/")
    page.wait_for_selector("#controls-retry:visible")
    assert "did not load" in page.inner_text("#controls-status")
    assert page.is_disabled("#go")
    page.click("#controls-retry")
    page.wait_for_selector(f"#control-{kept}", state="attached")
    assert page.is_checked(f"#control-{kept}")
    assert page.inner_text("#controls-summary") == "1 ticked: Guard."
    assert page.is_enabled("#go")


def test_the_controls_line_says_how_the_controls_apply(zoned_page):
    page = zoned_page
    keep_control(page, "Guard", {"type": "application"})
    page.goto("/")
    page.wait_for_selector("#controls-summary:visible")
    assert page.is_checked("#ask")
    assert page.inner_text("#controls-apply") == (
        "You confirm what each Control protects when the questions open."
    )
    page.uncheck("#ask")
    assert page.inner_text("#controls-apply").startswith(
        "Questions are off, so your Controls apply with no check by you. A"
        " Control of the whole application always applies."
    )
    page.click("#controls-choose")
    page.click("#controls-none")
    page.click("#controls-done")
    assert page.is_hidden("#controls-apply")


def test_a_controls_list_in_flight_holds_the_start(zoned_page):
    """The form sends no analysis while it does not know the Controls."""
    page = zoned_page
    keep_control(page, "Guard", {"type": "application"})
    held: list = []
    page.route("**/workspace/controls", lambda route: held.append(route))
    page.goto("/")
    page.wait_for_selector("#controls-status:visible")
    assert page.is_disabled("#go")
    assert page.is_visible("#go-wait")
    page.unroute("**/workspace/controls")
    held[0].fallback()
    page.wait_for_selector("#controls-summary:visible")
    assert page.is_enabled("#go")
    assert page.is_hidden("#go-wait")


def test_continue_without_controls_opens_the_start_and_keeps_retry(zoned_page):
    page = zoned_page
    kept = keep_control(page, "Guard", {"type": "application"})
    _fail_the_first_list(page, "http")
    page.goto("/")
    page.click("#controls-skip")
    assert page.is_enabled("#go")
    assert page.inner_text("#controls-status") == (
        "No Controls: you chose to continue without them."
    )
    assert page.evaluate("formControls.chosen()") is None
    page.click("#controls-retry")
    page.wait_for_selector(f"#control-{kept}", state="attached")
    assert page.is_checked(f"#control-{kept}")


def test_overlapping_failed_reads_keep_the_choice_to_continue(zoned_page):
    page = zoned_page
    keep_control(page, "Guard", {"type": "application"})
    _fail_the_first_list(page, "http")
    page.goto("/")
    page.click("#controls-skip")
    page.route("**/workspace/controls", lambda route: route.abort())
    page.evaluate("Promise.all([formControls.load(), formControls.load()])")
    assert page.inner_text("#controls-status") == (
        "No Controls: you chose to continue without them."
    )
    assert page.is_enabled("#go")


def test_a_read_in_flight_keeps_the_choice_to_continue(zoned_page):
    page = zoned_page
    keep_control(page, "Guard", {"type": "application"})
    _fail_the_first_list(page, "http")
    page.goto("/")
    page.click("#controls-skip")
    held: list = []
    page.route("**/workspace/controls", lambda route: held.append(route))
    page.evaluate("void formControls.load()")
    page.wait_for_timeout(200)
    assert page.is_enabled("#go")
    assert page.is_visible("#controls-retry")
    held[0].abort()


def test_a_controls_read_during_a_run_leaves_the_start_off(zoned_page):
    page = zoned_page
    kept = keep_control(page, "Guard", {"type": "application"})
    _fail_the_first_list(page, "http")
    page.goto("/")
    page.click("#controls-skip")
    page.fill("#description", "A web app talks to a database.")
    page.fill("#analysis-name", "Declined Controls")
    open_frameworks(page)
    page.locator('input[name="framework"]').first.check()
    close_frameworks(page)
    with page.expect_response("**/analyze"):
        page.click("#go")
    assert page.is_disabled("#go")
    page.evaluate("formControls.load()")
    page.wait_for_selector(f"#control-{kept}", state="attached")
    assert page.is_disabled("#go")


def test_a_failed_reload_keeps_the_last_list_and_its_ticks(zoned_page):
    page = zoned_page
    first = keep_control(page, "First", {"type": "application"})
    second = keep_control(page, "Second", {"type": "application"})
    page.goto("/")
    page.wait_for_selector(f"#control-{first}", state="attached")
    page.click("#controls-choose")
    page.uncheck(f"#control-{first}")
    page.click("#controls-done")
    page.route("**/workspace/controls", lambda route: route.abort())
    page.evaluate("formControls.load()")
    page.wait_for_selector("#controls-retry:visible")
    assert "did not refresh" in page.inner_text("#controls-status")
    assert page.evaluate("formControls.chosen()") == [second]
    assert page.is_enabled("#go")


def test_the_report_shows_the_controls_section_and_its_downloads(browser, tiers):
    from tests.test_webapp_controls_page import RulingRunner

    runner = RulingRunner()
    with contextmanager(serve)(web.app_for(tiers, runner)) as base:
        context = browser.new_context(base_url=base)
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("dialog", lambda dialog: errors.append(f"dialog: {dialog.message}"))
        page.goto("/controls")
        rows = [
            {"framework": "stride", "unit": unit, "version": "1.0"}
            for unit in ("spoofing", "tampering")
        ]
        runner.control = keep_control(
            page, HOSTILE_CONTROL, {"type": "application"}, rows=rows
        )
        submit_with_controls(page)
        with page.expect_response("**/answer/**"):
            page.click("#continue")
        page.wait_for_url("**/report/**", timeout=20_000)
        page.wait_for_selector("#controls-section")
        section = page.locator("#controls-section")
        assert page.locator("#controls-section td.name").inner_text() == HOSTILE_CONTROL
        assert page.locator(".controls-summary dt").all_inner_texts() == [
            "Match",
            "Facts",
            "Rows",
        ]
        assert section.locator("img, b").count() == 0
        page.click("#controls-section summary:has-text('tampering')")
        assert "It shows no <b>signing</b> at https://evil.example." in (
            section.inner_text()
        )
        assert "applied" in section.inner_text() and "refused" in section.inner_text()
        assert page.get_attribute("#export-md", "href").endswith("/export.md")
        # The download icon opens its three formats, and Escape closes them.
        assert page.is_hidden("#export-menu")
        page.click("#export-download")
        assert page.is_visible("#export-menu")
        assert page.evaluate("document.activeElement.id") == "export-md"
        page.keyboard.press("ArrowDown")
        assert page.evaluate("document.activeElement.id") == "export-json"
        page.keyboard.press("ArrowDown")
        assert page.evaluate("document.activeElement.id") == "export-csv"
        assert page.get_attribute("#export-csv", "href").endswith("/export.csv")
        page.keyboard.press("Escape")
        assert page.is_hidden("#export-menu")
        page.click("#export-preview")
        assert page.is_visible("#controls-section")
        assert page.evaluate("window.ran") is None
        context.close()
        assert errors == [], f"the page raised: {errors}"


def test_a_row_applied_to_some_findings_shows_partly_applied(browser, tiers):
    from tests.test_webapp_controls_page import RulingRunner

    runner = RulingRunner()
    runner.mixed = True
    with contextmanager(serve)(web.app_for(tiers, runner)) as base:
        context = browser.new_context(base_url=base)
        page = context.new_page()
        page.goto("/controls")
        rows = [
            {"framework": "stride", "unit": "spoofing", "version": "1.0"},
            {"framework": "stride", "unit": "denial-of-service", "version": "0.9"},
        ]
        runner.control = keep_control(page, "WAF", {"type": "application"}, rows=rows)
        submit_with_controls(page)
        with page.expect_response("**/answer/**"):
            page.click("#continue")
        page.wait_for_url("**/report/**", timeout=20_000)
        row = page.locator("#controls-section details.control-row", has_text="spoofing")
        summary = row.locator("summary").inner_text()
        assert "partly applied" in summary
        assert "stride spoofing (version 1.0)" in summary
        assert "Ruled out: 2 · Refused: 1" in summary
        row.locator("summary").click()
        assert row.locator(".ruling-heading").all_inner_texts() == [
            "Ruled out",
            "Refused",
        ]
        [refused] = row.locator(".ruling-list").nth(1).locator("li").all_inner_texts()
        assert refused == "stride-returned: It shows no check of the token's issuer."
        assert "refused this row for the findings listed under Refused" in (
            row.inner_text()
        )
        old = page.locator(
            "#controls-section details.control-row", has_text="denial-of-service"
        )
        assert old.locator("summary .tag").inner_text() == "left out"
        old.locator("summary").click()
        assert old.locator("p.meta").all_inner_texts() == [
            (
                "Left out: this row names version 0.9 of stride, and the analysis"
                " ran version 1.0, so the critic did not judge it."
            )
        ]
        context.close()


@pytest.fixture(params=["recorded"])
def sitting_page(browser, tmp_path, request, monkeypatch):
    """A page on a review sitting with two cases: each recorded with a mark
    on every finding, blank, or signed by a central review. The page's
    confirm dialogs are accepted."""
    from tests.test_sitting_feedback import (
        CASE,
        OTHER,
        OWN_LIST,
        central_review,
        tree_for,
    )
    from webapp import sitting

    # No GitHub login, on every machine: a contribution takes the browser
    # route, which asks for an author and opens no pull request.
    monkeypatch.setattr(sitting, "_gh_login", lambda root: "")
    tree = tree_for(tmp_path)
    if request.param == "signed":
        central_review(tree, cases=(CASE, OTHER))
    session = sitting.build_session(
        tree,
        sitting.LOCAL_SUBMITTER,
        "anonymous",
        drafts=tree.parent / "state" / "reviews",
    )
    with contextmanager(serve)(sitting.create_app(session)) as base:
        context = browser.new_context(base_url=base)
        opened = context.new_page()
        errors: list[str] = []
        opened.on("pageerror", lambda error: errors.append(str(error)))
        opened.on("dialog", lambda dialog: dialog.accept())
        opened.goto("/")
        opened.wait_for_selector("#cases li button")
        for case in (CASE, OTHER) if request.param == "recorded" else ():
            opened.evaluate(
                """async ([id, own]) => {
                  await post("/api/own-list", {case: id, items: own});
                  const two = await fetch("/api/part-two?case=" + id, {headers})
                    .then((r) => r.json());
                  const marks = Object.fromEntries(
                    two.marks.map((t) => [t.fingerprint, "agree"]));
                  await post("/api/finish",
                    {case: id, marks, missing: [], notes: ""});
                }""",
                [case, OWN_LIST],
            )
        opened.reload()
        opened.wait_for_selector("#cases li button")
        yield opened, session, (CASE, OTHER)
        context.close()
        assert errors == [], f"the page raised: {errors}"


def _open_case(page, case: str) -> None:
    page.click(f'#cases li[data-case="{case}"] button')
    page.wait_for_function(
        "id => current === id"
        " && document.querySelectorAll('select[data-finding]').length > 0"
        " && [...document.querySelectorAll('select[data-finding]')]"
        ".every((s) => s.value)",
        arg=case,
    )


def test_a_reset_that_answers_after_another_case_opens_clears_only_its_case(
    sitting_page,
):
    page, session, (case, other) = sitting_page
    held: list = []
    page.route("**/api/reset", lambda route: held.append(route))
    _open_case(page, case)
    page.click("#resetReview")
    for _ in range(100):
        if held:
            break
        page.wait_for_timeout(20)
    _open_case(page, other)
    with (
        page.expect_response("**/api/rail"),
        page.expect_response("**/api/reset") as answered,
    ):
        held[0].continue_()
    assert answered.value.ok, answered.value.text()
    assert all(
        page.eval_on_selector_all("select[data-finding]", "s => s.map((x) => x.value)")
    )
    assert session.draft(other).marks


def _open_submit(page) -> None:
    """Open the contribution stage, with an author for the browser route."""
    page.evaluate("openSubmit()")
    page.wait_for_selector("#submit:enabled")
    page.fill("#githubAuthor", "web-reviewer")


def _hold(page, path: str) -> list:
    """Hold each request to ``path``, and return the held routes."""
    held: list = []
    page.route(path, lambda route: held.append(route))
    return held


def _wait_for(page, held: list) -> None:
    for _ in range(250):
        if held:
            return
        page.wait_for_timeout(20)
    raise AssertionError("no request was held")


def test_a_record_that_answers_after_another_case_opens_leaves_it(sitting_page):
    page, _, (case, other) = sitting_page
    held = _hold(page, "**/api/finish")
    _open_case(page, case)
    page.click("#finish")
    _wait_for(page, held)
    _open_case(page, other)
    with page.expect_response("**/api/rail"):
        held[0].continue_()
    assert page.is_hidden("#done")
    page.wait_for_selector("#finish:enabled")


def test_a_mark_change_during_a_record_keeps_its_button_off(sitting_page):
    page, _, (case, _) = sitting_page
    held = _hold(page, "**/api/finish")
    _open_case(page, case)
    page.click("#finish")
    _wait_for(page, held)
    page.select_option("select[data-finding] >> nth=0", "unsure")
    assert page.is_disabled("#finish")
    held[0].continue_()
    page.wait_for_selector("#finish:enabled")


@pytest.mark.parametrize("sitting_page", ["blank"], indirect=True)
def test_a_lock_that_answers_after_another_case_opens_leaves_it(sitting_page):
    page, session, (case, other) = sitting_page
    held = _hold(page, "**/api/own-list")
    page.click(f'#cases li[data-case="{case}"] button')
    page.wait_for_function("id => current === id", arg=case)
    page.fill("#own", "a spoofed device\nanother line of my own list")
    page.click("#lock")
    _wait_for(page, held)
    page.click(f'#cases li[data-case="{other}"] button')
    page.wait_for_function("id => current === id", arg=other)
    with page.expect_response("**/api/rail"):
        held[0].continue_()
    assert not page.evaluate("document.getElementById('own').readOnly")
    assert session.draft(case).own_list


@pytest.mark.parametrize("sitting_page", ["signed"], indirect=True)
def test_a_late_read_only_case_leaves_the_case_opened_after_it(sitting_page):
    page, _, (case, other) = sitting_page
    _count_reads(page, "/api/read-only")
    held = _hold(page, f"**/api/read-only?case={case}")
    page.click(f'#cases li[data-case="{case}"] button')
    _wait_for(page, held)
    page.click(f'#cases li[data-case="{other}"] button')
    page.wait_for_function(
        "() => document.getElementById('readOnlyDocument').textContent"
    )
    shown = page.inner_text("#readOnlyTitle")
    held[0].continue_()
    page.wait_for_function("() => window.readsDone >= 2")
    assert page.inner_text("#readOnlyTitle") == shown


def test_a_stage_redraw_during_a_contribution_keeps_submit_off(sitting_page):
    page, _, _ = sitting_page
    held = _hold(page, "**/api/contribute")
    _open_submit(page)
    page.click("#submit")
    _wait_for(page, held)
    page.evaluate("loadStage()")
    assert page.is_disabled("#submit")
    held[0].fulfill(status=409, json={"detail": "Contribution needs attention"})
    page.wait_for_selector("#submit:enabled")


def test_a_record_that_fails_to_send_gives_its_button_back(sitting_page):
    page, _, (case, _) = sitting_page
    page.route("**/api/finish", lambda route: route.abort())
    _open_case(page, case)
    page.click("#finish")
    page.wait_for_selector("#saveStatus:has-text('connection was interrupted')")
    assert page.is_enabled("#finish")


def test_a_cancelled_pick_drops_an_earlier_read(page):
    page.goto("/files")
    page.evaluate(
        """() => {
          const read = textFile.read;
          let open;
          window.releaseFirst = () => open();
          textFile.read = async (...args) => {
            await new Promise((resolve) => { open = resolve; });
            const out = await read(...args);
            setTimeout(() => { window.firstDone = true; }, 0);
            return out;
          };
        }"""
    )
    upload_to_files(page, "slow.txt", "slow\n")
    page.wait_for_function("() => typeof window.releaseFirst === 'function'")
    page.evaluate(
        """() => {
          const input = document.getElementById("file-input");
          Object.defineProperty(input, "files", {value: [], configurable: true});
          input.dispatchEvent(new Event("change"));
        }"""
    )
    page.evaluate("window.releaseFirst()")
    page.wait_for_function("() => window.firstDone")
    assert page.is_hidden("#upload-dialog")


def test_a_contribution_that_fails_to_send_gives_submit_back(sitting_page):
    page, _, _ = sitting_page
    page.route("**/api/contribute", lambda route: route.abort())
    _open_submit(page)
    page.click("#submit")
    page.wait_for_selector("#contributeStatus:has-text('connection was interrupted')")
    assert page.is_enabled("#submit")


def test_a_stop_that_fails_to_send_says_so(browser, hanging):
    context = browser.new_context(base_url=hanging)
    page = context.new_page()
    page.goto("/")
    submitted(page, "Hangs")
    page.goto("/?view=analyses")
    page.route("**/stop", lambda route: route.abort())
    page.click('[aria-label="Stop Hangs"]')
    page.wait_for_selector("#workspace-message:has-text('Not stopped')")
    page.wait_for_selector('[aria-label="Stop Hangs"]:enabled')
    context.close()


def test_a_stop_from_the_analysis_that_fails_to_send_says_so(browser, hanging):
    context = browser.new_context(base_url=hanging)
    page = context.new_page()
    page.goto("/")
    submitted(page, "Hangs")
    page.goto("/?view=analyses")
    page.click("#analysis-rows a:text-is('Hangs')")
    page.wait_for_selector("#stop-run", state="visible")
    page.route("**/stop", lambda route: route.abort())
    page.click("#stop-run")
    page.wait_for_selector("#workspace-message:has-text('Not stopped')")
    assert page.is_enabled("#stop-run")
    context.close()


def test_submit_follows_the_stage_after_a_contribution(sitting_page):
    page, _, _ = sitting_page
    held = _hold(page, "**/api/contribute")
    _open_submit(page)
    page.click("#submit")
    _wait_for(page, held)
    while rows := page.locator("#carrying button").count():
        page.locator("#carrying button").first.click()
        page.wait_for_function(
            "n => document.querySelectorAll('#carrying button').length < n",
            arg=rows,
        )
    held[0].fulfill(status=409, json={"detail": "Contribution needs attention"})
    page.wait_for_selector(
        "#contributeStatus:has-text('needs attention')", state="attached"
    )
    assert page.is_disabled("#submit")


@pytest.mark.parametrize(
    ("path", "button", "status"),
    [
        ("finish", "#finish", "#saveStatus"),
        ("contribute", "#submit", "#contributeStatus"),
    ],
)
def test_a_good_answer_that_is_not_json_says_so(sitting_page, path, button, status):
    page, _, (case, _) = sitting_page
    page.route(f"**/api/{path}", lambda route: route.fulfill(body="<html>oops</html>"))
    if path == "finish":
        _open_case(page, case)
    else:
        _open_submit(page)
    page.click(button)
    page.wait_for_selector(
        f"{status}:has-text('connection was interrupted')", state="attached"
    )
    assert page.is_enabled(button)


def test_the_list_keeps_stop_off_while_the_header_stops_the_run(browser, hanging):
    context = browser.new_context(base_url=hanging)
    page = context.new_page()
    page.goto("/")
    submitted(page, "Hangs")
    page.goto("/?view=analyses")
    page.click("#analysis-rows a:text-is('Hangs')")
    page.wait_for_selector("#stop-run", state="visible")
    held = _hold(page, "**/stop")
    page.click("#stop-run")
    _wait_for(page, held)
    page.click("#library-nav")
    page.wait_for_selector('[aria-label="Stop Hangs"]')
    assert page.is_disabled('[aria-label="Stop Hangs"]')
    held[0].fulfill(status=409, json={"message": "Not now."})
    page.wait_for_selector('[aria-label="Stop Hangs"]:enabled')
    context.close()


# --- an answer that is not JSON, and an answer that comes late ---------------

#: A refusal as a proxy words it: a plain error page, not the app's JSON.
ERROR_PAGE = {"status": 502, "body": "<html>Bad gateway</html>"}


def _answer_with_an_error_page(page, path: str) -> None:
    page.route(path, lambda route: route.fulfill(**ERROR_PAGE))


def _reload(page, case: str) -> None:
    page.reload()


def _press_case(page, case: str) -> None:
    page.click(f'#cases li[data-case="{case}"] button')


def _reset(page, case: str) -> None:
    page.click("#resetReview")


def _drop(page, case: str) -> None:
    page.locator("#carrying button").first.click()


def _preview(page, case: str) -> None:
    page.click("#showFiles")


@pytest.mark.parametrize(
    ("path", "stage", "act", "said"),
    [
        ("**/api/rail", None, _reload, "#left:has-text('did not load')"),
        ("**/api/part-one*", None, _press_case, "#partOne:has-text('did not load')"),
        ("**/api/part-two*", None, _press_case, "#ownHint:has-text('did not load')"),
        ("**/api/reset", "case", _reset, "#saveStatus:has-text('Not reset')"),
        ("**/api/drop", "submit", _drop, "#ready:has-text('Not changed')"),
        (
            "**/api/contribution-preview",
            "submit",
            _preview,
            "#contributeStatus:has-text('did not load')",
        ),
    ],
)
def test_a_sitting_refusal_that_is_not_json_says_so(
    sitting_page, path, stage, act, said
):
    page, _, (case, _) = sitting_page
    if stage == "case":
        _open_case(page, case)
    elif stage == "submit":
        _open_submit(page)
    _answer_with_an_error_page(page, path)
    act(page, case)
    page.wait_for_selector(said, state="attached")


def test_a_late_preview_stays_closed_after_the_author_changes(sitting_page):
    page, _, _ = sitting_page
    _count_reads(page, "/api/contribution-preview")
    held = _hold(page, "**/api/contribution-preview")
    _open_submit(page)
    page.click("#showFiles")
    _wait_for(page, held)
    page.fill("#githubAuthor", "another-reviewer")
    held[0].continue_()
    page.wait_for_function("() => window.readsDone >= 1")
    assert page.is_hidden("#filePreview")


def test_an_older_rail_answer_leaves_the_newer_rail(sitting_page):
    page, _, _ = sitting_page
    _count_reads(page, "/api/rail")
    held: list = []
    page.route(
        "**/api/rail",
        lambda route: held.append(route) if not held else route.fallback(),
    )
    page.evaluate("() => { loadRail(); }")
    _wait_for(page, held)
    page.evaluate("loadRail()")
    held[0].fulfill(json={"cases": [], "ready": 0})
    page.wait_for_function("() => window.readsDone >= 2")
    assert page.locator("#cases li").count() == 2


def test_the_last_case_pressed_owns_the_stage(sitting_page):
    """Each press saves the draft first. A press whose save answers late
    leaves the stage to the press after it."""
    page, _, (case, other) = sitting_page
    _open_case(page, case)
    held: list = []
    page.route(
        "**/api/draft",
        lambda route: held.append(route) if not held else route.fallback(),
    )
    _press_case(page, other)
    _wait_for(page, held)
    _press_case(page, case)
    with page.expect_response("**/api/draft"):
        held[0].continue_()
    page.wait_for_timeout(300)
    assert page.evaluate("current") == case
    _open_case(page, case)


def test_a_record_pressed_while_the_next_case_opens_leaves_it(sitting_page):
    """A press of a case saves the draft before the stage changes. A record
    pressed in that time belongs to the case that the stage shows then."""
    page, _, (case, other) = sitting_page
    _open_case(page, case)
    drafts: list = []
    page.route(
        "**/api/draft",
        lambda route: drafts.append(route) if not drafts else route.fallback(),
    )
    finishes = _hold(page, "**/api/finish")
    _press_case(page, other)
    _wait_for(page, drafts)
    page.click("#finish")
    _wait_for(page, finishes)
    drafts[0].continue_()
    page.wait_for_function("id => current === id", arg=other)
    with page.expect_response("**/api/rail"):
        finishes[0].continue_()
    page.wait_for_timeout(300)
    assert page.is_hidden("#done")


def test_a_record_keeps_its_stage_when_the_draft_saves_of_two_presses_fail(
    sitting_page,
):
    """A press of a case whose draft save does not arrive leaves the stage on
    its case, so a record that answers then draws there."""
    page, _, (case, other) = sitting_page
    _open_case(page, case)
    finishes = _hold(page, "**/api/finish")
    page.click("#finish")
    _wait_for(page, finishes)
    drafts = _hold(page, "**/api/draft")
    _press_case(page, other)
    _press_case(page, other)
    for _ in range(250):
        if len(drafts) == 2:
            break
        page.wait_for_timeout(20)
    assert len(drafts) == 2
    for draft in drafts:
        with page.expect_event("requestfailed"):
            draft.abort()
    with page.expect_response("**/api/rail"):
        finishes[0].continue_()
    page.wait_for_selector("#done", state="visible", timeout=3000)
    assert page.evaluate("current") == case


def _lose(route) -> None:
    route.abort()


def _refuse(route) -> None:
    route.fulfill(**ERROR_PAGE)


_NOT_SAVED = "#saveStatus:has-text('Your draft did not save.')"


@pytest.mark.parametrize("fail", [_lose, _refuse])
def test_a_press_whose_draft_save_fails_stays_on_the_case_and_says_so(
    sitting_page, fail
):
    """A press of another case saves the draft first. Where that save fails,
    the stage keeps its case and says that the draft did not save. The next
    press that saves moves on."""
    page, _, (case, other) = sitting_page
    _open_case(page, case)
    page.route("**/api/draft", fail)
    _press_case(page, other)
    page.wait_for_selector(_NOT_SAVED, timeout=3000)
    assert "Try again." in page.inner_text("#saveStatus")
    assert page.evaluate("current") == case
    assert page.is_visible("#two")
    page.unroute("**/api/draft")
    _open_case(page, other)
    assert page.inner_text("#saveStatus") == ""


@pytest.mark.parametrize("fail", [_lose, _refuse])
def test_a_queued_draft_save_that_fails_says_so_until_a_save_lands(sitting_page, fail):
    page, _, (case, _) = sitting_page
    _open_case(page, case)
    page.route("**/api/draft", fail)
    page.fill("#notes", "a note")
    page.wait_for_selector(_NOT_SAVED, timeout=3000)
    page.unroute("**/api/draft")
    with page.expect_response("**/api/draft") as saved:
        page.evaluate("saveDraft()")
    assert saved.value.ok
    page.wait_for_function(
        "() => document.getElementById('saveStatus').textContent === ''"
    )


@pytest.mark.parametrize("fail", [_lose, _refuse])
def test_the_guide_opens_and_says_that_the_draft_did_not_save(sitting_page, fail):
    page, _, (case, _) = sitting_page
    _open_case(page, case)
    page.route("**/api/draft", fail)
    page.click("#helpToggle")
    page.wait_for_selector(_NOT_SAVED, timeout=3000)
    page.wait_for_selector("#guide", state="visible", timeout=3000)
    assert page.evaluate("current") == case


def test_a_drop_keeps_the_author_the_reader_typed(sitting_page):
    page, _, _ = sitting_page
    _open_submit(page)
    page.fill("#githubAuthor", "typed-author")
    rows = page.locator("#carrying button").count()
    with page.expect_response("**/api/contribution-status"):
        _drop(page, "")
    page.wait_for_function(
        "n => document.querySelectorAll('#carrying button').length < n", arg=rows
    )
    assert page.input_value("#githubAuthor") == "typed-author"


def _upload(page, name: str) -> None:
    upload_to_files(page, name, "Ana: hi")
    page.wait_for_selector("#upload-dialog[open]")
    page.click("#upload-save")


def test_an_older_files_answer_leaves_the_newer_list(page):
    """A delete and an upload each read the list again. The read of the
    delete answers last here, with a list that the upload made old."""
    page.goto("/files")
    _upload(page, "first.txt")
    page.wait_for_selector("#files-list td.name >> text=first.txt")
    _upload(page, "second.txt")
    page.wait_for_selector("#files-list td.name >> text=second.txt")
    _count_reads(page, "/workspace/files")
    held: list = []
    page.route(
        "**/workspace/files",
        lambda route: (
            held.append(route)
            if route.request.method == "GET" and not held
            else route.fallback()
        ),
    )
    page.locator("#files-list button").first.click()
    page.click("#delete-go")
    _wait_for(page, held)
    _upload(page, "third.txt")
    page.wait_for_selector("#files-list td.name >> text=third.txt")
    held[0].fulfill(json=[])
    page.wait_for_function("() => window.readsDone >= 2")
    assert page.locator("#files-list td.name").count() == 2


def test_a_kept_file_whose_answer_is_not_json_closes_the_dialog(page):
    page.goto("/files")
    upload_to_files(page, "kept.txt", "Ana: hi")
    page.wait_for_selector("#upload-dialog[open]")
    page.route(
        "**/workspace/files",
        lambda route: (
            route.fulfill(body="<html>ok</html>")
            if route.request.method == "POST"
            else route.fallback()
        ),
    )
    page.click("#upload-save")
    page.wait_for_selector("#files-notice:has-text('“kept.txt” is in Files.')")
    assert page.is_hidden("#upload-dialog")


def test_a_list_answer_that_is_not_json_says_so_in_plain_words(page):
    page.goto("/")
    page.route("**/workspace/runs", lambda route: route.fulfill(body="<html></html>"))
    page.click("#library-nav")
    page.wait_for_selector(
        "#workspace-message:has-text('Could not load analyses. Try again.')"
    )


def test_an_analysis_answer_that_is_not_json_says_so_in_plain_words(page):
    page.goto("/")
    submitted(page, "Read me")
    page.goto("/?view=analyses")
    page.route("**/workspace/runs/*", lambda route: route.fulfill(body="<html></html>"))
    page.click("#analysis-rows a:text-is('Read me')")
    page.wait_for_selector(
        "#workspace-message:has-text('Could not load this analysis. Try again.')"
    )


def _hanging_list(browser, hanging):
    """A page on the list of a run that hangs."""
    context = browser.new_context(base_url=hanging)
    if timeout := os.environ.get("BROWSER_TIMEOUT_MS"):
        context.set_default_timeout(int(timeout))
    page = context.new_page()
    page.goto("/")
    submitted(page, "Hangs")
    page.goto("/?view=analyses")
    page.wait_for_selector('[aria-label="Stop Hangs"]')
    return context, page


def test_a_late_stop_answer_keeps_the_ticks_the_sort_and_a_rename(browser, hanging):
    context, page = _hanging_list(browser, hanging)
    page.check('[aria-label="Select Hangs"]')
    page.click('[data-sort="name"]')
    held = _hold(page, "**/stop")
    page.click('[aria-label="Stop Hangs"]')
    _wait_for(page, held)
    page.click('[aria-label="Rename Hangs"]')
    page.fill('[aria-label="New name for Hangs"]', "Hangs no")
    _count_reads(page, "/workspace/runs")
    held[0].continue_()
    page.wait_for_function("() => window.readsDone >= 1")
    assert page.input_value('[aria-label="New name for Hangs"]') == "Hangs no"
    page.press('[aria-label="New name for Hangs"]', "Escape")
    page.wait_for_selector("#analysis-rows .badge:text-is('Stopped')")
    assert page.is_checked('[aria-label="Select Hangs"]')
    assert page.get_attribute("#sort-name", "aria-sort") == "ascending"
    context.close()


def test_a_rename_that_loses_its_connection_says_so_and_the_list_draws(
    browser, hanging
):
    context, page = _hanging_list(browser, hanging)
    page.route("**/title", lambda route: route.abort())
    page.click('[aria-label="Rename Hangs"]')
    page.fill('[aria-label="New name for Hangs"]', "Hangs no")
    page.press('[aria-label="New name for Hangs"]', "Enter")
    page.wait_for_selector("#workspace-message:has-text('Not renamed')")
    assert page.locator('[aria-label="New name for Hangs"]').count() == 0
    assert page.locator("#analysis-rows a:text-is('Hangs')").count() == 1
    context.close()


def test_a_rename_that_ends_leaves_a_later_rename_open(browser, hanging):
    context, page = _hanging_list(browser, hanging)
    page.click('[aria-label="Stop Hangs"]')
    page.wait_for_selector("#analysis-rows .badge:text-is('Stopped')")
    page.goto("/")
    submitted(page, "Other")
    page.goto("/?view=analyses")
    page.wait_for_selector('[aria-label="Rename Other"]')
    held = _hold(page, "**/title")
    page.click('[aria-label="Rename Hangs"]')
    page.fill('[aria-label="New name for Hangs"]', "Hangs no")
    page.press('[aria-label="New name for Hangs"]', "Enter")
    _wait_for(page, held)
    page.click('[aria-label="Rename Other"]')
    page.fill('[aria-label="New name for Other"]', "Other no")
    with page.expect_response("**/title"):
        held[0].continue_()
    page.wait_for_timeout(300)
    assert page.input_value('[aria-label="New name for Other"]') == "Other no"
    page.unroute("**/title")
    page.press('[aria-label="New name for Other"]', "Enter")
    page.wait_for_selector("#analysis-rows a:text-is('Other no')")
    assert page.locator("#analysis-rows a:text-is('Hangs no')").count() == 1
    context.close()


@pytest.mark.parametrize("lost", ["no answer", "an answer that is not JSON"])
def test_a_start_with_no_readable_answer_says_to_check_the_run(page, lost):
    pause(page)

    def lose(route):
        if lost == "no answer":
            route.abort()
        else:
            route.fulfill(body="<html></html>")

    page.route(
        "**/answer/*",
        lambda route: (
            lose(route) if route.request.method == "POST" else route.fallback()
        ),
    )
    if lost == "no answer":
        page.route("**/workspace/runs/*", lambda route: route.abort())
    page.click("#continue")
    page.wait_for_selector("#answer-problem:has-text('check whether it started')")
    assert page.is_enabled("#continue")


def _takes_no_press(page, selector: str) -> bool:
    """Whether a press on ``selector`` reaches nothing, as on an inert part."""
    from playwright.sync_api import TimeoutError as NoPress

    try:
        page.click(selector, timeout=500)
    except NoPress:
        return True
    return False


_REFUSED = {
    "status": 503,
    "content_type": "application/json",
    "body": '{"message": "The service is busy. Try again."}',
}


def test_the_controls_take_no_input_while_a_submission_starts(zoned_page):
    """From the press of Start to the answer, the Controls of the form take no
    press. A refusal gives them back."""
    page = zoned_page
    keep_control(page, "First", {"type": "application"})
    page.goto("/")
    page.wait_for_selector("#controls-offer:not([hidden])")
    page.fill("#description", "A web app talks to a database.")
    page.fill("#analysis-name", "Controls analysis")
    open_frameworks(page)
    page.locator('input[name="framework"]').first.check()
    close_frameworks(page)
    held = _hold(page, "**/analyze")
    page.click("#go")
    _wait_for(page, held)
    assert _takes_no_press(page, "#controls-choose")
    assert page.is_hidden("#controls-dialog")
    held[0].fulfill(**_REFUSED)
    page.wait_for_selector("#problem:has-text('The service is busy')")
    page.click("#controls-choose", timeout=3000)
    page.wait_for_selector("#controls-dialog[open]")


def _start(page) -> None:
    page.click("#continue")


def _amend(page) -> None:
    page.click(".amend summary")
    page.fill(".amend textarea", "The database is encrypted.")
    page.click(".amend button")


@pytest.mark.parametrize(
    ("path", "press"), [("**/answer/*", _start), ("**/amend/*", _amend)]
)
def test_the_controls_take_no_input_while_a_pause_ends(zoned_page, path, press):
    """From the press that can end the pause to its answer, the Controls of
    the pause take no press, so no change goes to a run that ended. A refusal
    gives them back."""
    page = zoned_page
    first = keep_control(page, "First", {"type": "application"})
    submit_with_controls(page)
    held = _hold(page, path)
    press(page)
    _wait_for(page, held)
    assert _takes_no_press(page, f"#offer-{first}")
    held[0].fulfill(**_REFUSED)
    page.wait_for_selector("#answer-problem:has-text('The service is busy')")
    page.unroute(path)
    assert "control_scopes" in save_offer(
        page, lambda: page.click(f"#offer-{first}", timeout=3000)
    )


def test_a_settings_save_whose_answer_is_not_json_says_to_reload(browser, tmp_path):
    from tests.test_settings_env import env_in, hand_written, write_settings
    from webapp.main import create_app

    write_settings(tmp_path, hand_written())
    timeout = 'tr[data-key="resilience.timeout_ms"]'
    for url in serve(create_app(env=env_in(tmp_path))):
        page, errors = opened(browser, url + "/settings")
        page.route(
            "**/settings",
            lambda route: (
                route.fulfill(body="<html></html>")
                if route.request.method == "POST"
                else route.fallback()
            ),
        )
        page.locator(f"{timeout} input").fill("5000")
        page.click("#save-changes")
        page.wait_for_selector("#save-errors", state="visible", timeout=3000)
        said = page.locator("#save-errors").inner_text()
        page.close()
    assert errors == [], f"the settings page raised: {errors}"
    assert "Reload the page" in said


def test_the_review_pages_say_that_a_read_failed(browser, tmp_path):
    from tests import test_review_app as review
    from webapp.review import build_session, create_app

    session = build_session(
        [[review.STEADY]],
        voter="ada",
        ledger_path=tmp_path / "votes",
        configs={review.STEADY.case: "engine-1.2.3"},
    )
    said = {"/": "#counts", "/review": "#left"}
    for url in serve(create_app(session)):
        for path, line in said.items():
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
            page.route("**/api/*", lambda route: route.fulfill(**ERROR_PAGE))
            page.goto(url + path)
            page.wait_for_selector(f"{line}:has-text('did not load')", timeout=3000)
            page.close()
            assert errors == [], f"{path} raised: {errors}"
