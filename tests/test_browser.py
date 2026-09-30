"""The first-run pages in a real browser: headless Chromium through Playwright.

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
    try:
        import uvicorn
    except ImportError:
        _unavailable("uvicorn is not installed: uv sync --group browser")
    app = web.app_for(tiers, web.PausingRunner(catalog=False), catalog=False)
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
