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


def pause(page) -> None:
    """Submit a description that asks questions, and wait for the first round."""
    page.fill("#description", "A web app talks to a database.")
    page.check("#ask")
    page.click("#go")
    page.wait_for_selector("#asked", state="visible")


def test_clearing_a_control_sends_no_answer_for_it(page):
    """Blank after "There is none" still sent ``none`` (#1289, B1)."""
    pause(page)
    control = page.locator('#questions select:has(option[value="mechanism"])').first
    control.select_option("none")
    control.select_option("")
    with page.expect_request("**/answer/*") as sent:
        page.click("#save")
    facts = sent.value.post_data_json["facts"]
    assert all(fact.get("value") != "none" for fact in facts)


def test_an_owner_who_skips_every_round_reaches_a_report_that_says_what_is_open(
    page,
):
    """Skip, the ready state, the start button and the report, end to end."""
    pause(page)
    for _ in range(20):
        if page.is_hidden("#save"):
            break
        before = page.inner_text("#questions")
        page.click("#skip")
        # The response lands before the page draws the next round from it.
        page.wait_for_function(
            "before => document.querySelector('#questions').innerText !== before",
            arg=before,
        )
    else:
        pytest.fail("the rounds never ended")
    assert "Nothing runs until you choose Start the analysis" in page.inner_text(
        "#questions"
    )
    page.click("#continue")
    page.wait_for_url("**/report/**")
    report = page.inner_text("body")
    assert "What remains open" in report
    assert "neither confirmed nor cleared" in report


def test_a_phone_sized_owner_answers_skips_corrects_and_starts(browser, served):
    """The #1289 review asked for a small-screen walk through every step."""
    context = browser.new_context(
        base_url=served, viewport={"width": 375, "height": 667}
    )
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto("/")
    pause(page)
    page.locator('#questions select:has(option[value="no"])').first.select_option("no")
    page.click("#save")
    page.wait_for_selector("#earlier", state="visible")
    before = page.inner_text("#questions")
    page.click("#skip")
    page.wait_for_function(
        "before => document.querySelector('#questions').innerText !== before",
        arg=before,
    )
    page.click("#earlier summary")
    page.locator("#earlier button", has_text="Change").first.click()
    page.locator('#earlier select:has(option[value="yes"])').first.select_option("yes")
    wide = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    with page.expect_request("**/answer/*") as sent:
        page.click("#continue")
    facts = sent.value.post_data_json["facts"]
    page.wait_for_url("**/report/**")
    context.close()
    assert errors == [], f"the page raised: {errors}"
    assert any("yes" in str(fact) for fact in facts)
    assert wide <= 0, f"the page scrolls sideways by {wide}px on a phone"


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
    page.locator('#questions select:has(option[value="no"])').first.select_option("no")
    page.click("#save")
    page.wait_for_selector("#earlier", state="visible")
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
    with page.expect_request("**/answer/*") as sent:
        page.click("text=Run the follow-up with these answers")
    assert any(
        fact.get("value") == "yes" for fact in sent.value.post_data_json["facts"]
    )
    page.wait_for_url(lambda url: "/report/" in url and url != first)
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
    page.uncheck('input[name="framework"][value="stride"]')
    page.select_option('select[data-option="level"]', "2")
    pause(page)
    page.reload()
    page.wait_for_selector("#asked", state="visible")
    page.click("#description-nav")
    assert not page.is_checked('input[name="framework"][value="stride"]')
    assert page.is_checked('input[name="framework"][value="asvs"]')
    assert page.input_value('select[data-option="level"]') == "2"
