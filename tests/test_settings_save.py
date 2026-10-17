"""A save on the Settings page of the first-run app, and the live swap
(#1776, ADR 0092, ADR 0093), at the HTTP surface.

Each test builds the app on a settings directory in ``tmp_path`` with a
process environment of its own. The build checks each mapping with the real
loaders and the real runner, which makes no provider call, and then runs each
job on a stub runner, so a test reads which Deployment ran a job.
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from analysis_service.certification import CertifyResult
from analysis_service.model_gate import tool_models
from tests.factories import TEST_CREDENTIAL_ENV, TEST_TIER_ENV
from tests.test_runtime_certification import FP_A, CertifyingRunner
from tests.test_settings_env import (
    OTHER_MODEL,
    env_in,
    hand_written,
    settings_client,
    write_settings,
)
from tests.test_webapp import LOOPBACK, SAME_ORIGIN, posted, run_to_completion
from tests.test_webapp_cell import Recording
from tests.test_webapp_questions import PausingRunner, shown_list, start
from tests.test_webapp_settings import page_data, rows_of
from webapp import main, settings_save
from webapp.main import Startup, build_startup, create_app
from webapp.settings_save import MAX_SAVE_BYTES

BASE_MODEL = TEST_TIER_ENV["ANALYSIS_MODEL_BASE_MODEL"]


def test_the_other_model_is_one_the_list_suggests():
    assert OTHER_MODEL != BASE_MODEL
    assert OTHER_MODEL in tool_models("openai")


class Builds:
    """The build of each Deployment: the real one, then a stub runner from
    ``runner()`` in place of the real runner.

    The real runner of each selection is still built, so the check of the
    selection holds. ``models`` holds the base model of each Deployment that
    built, and ``runners`` its stub runner.
    """

    def __init__(self, runner=Recording) -> None:
        self.runner = runner
        self.models: list[str] = []
        self.runners: list = []

    def __call__(self, env) -> Startup:
        startup = build_startup(env)
        if startup.settings is None:
            return startup
        assert startup.tiers is not None
        real, stub = startup.settings.runner_for, self.runner()
        self.models.append(startup.tiers.tiers["base"].model)
        self.runners.append(stub)

        def runner_for(selection, entry):
            real(selection, entry)
            return stub

        return replace(
            startup, settings=replace(startup.settings, runner_for=runner_for)
        )


def app_on(tmp_path, settings: str | None = None, builds=None, **process: str):
    if settings is not None:
        write_settings(tmp_path, settings)
    builds = Builds() if builds is None else builds
    return settings_client(tmp_path, process, build=builds), builds


def revision(client) -> str:
    return page_data(client.get("/settings"))["revision"]


def save(client, changes=None, remove=(), rev=None):
    body = {
        "revision": revision(client) if rev is None else rev,
        "changes": changes or {},
        "remove": list(remove),
    }
    return client.post("/settings", json=body, headers=SAME_ORIGIN)


def settings_text(tmp_path) -> str:
    return (tmp_path / "settings.env").read_text(encoding="utf-8")


class TestASaveThatPasses:
    def test_a_tier_model_writes_one_line_and_the_next_job_runs_on_it(self, tmp_path):
        client, builds = app_on(tmp_path, hand_written())

        saved = save(client, {"tiers.base.model": OTHER_MODEL})
        run_to_completion(client)

        assert saved.status_code == 200, saved.text
        lines = settings_text(tmp_path).splitlines()
        assert [line for line in lines if "BASE_MODEL" in line] == [
            f"ANALYSIS_MODEL_BASE_MODEL={OTHER_MODEL}"
        ]
        assert builds.models == [BASE_MODEL, OTHER_MODEL]
        assert builds.runners[1].jobs and not builds.runners[0].jobs

    def test_the_response_holds_the_new_page_data(self, tmp_path):
        client, _ = app_on(tmp_path, hand_written())

        data = save(client, {"tiers.base.model": OTHER_MODEL}).json()["settings"]

        row = next(row for row in data["rows"] if row["key"] == "tiers.base.model")
        assert (row["value"], row["source"]) == (OTHER_MODEL, "page")
        assert data["revision"] == revision(client)

    def test_a_job_in_flight_finishes_on_the_deployment_that_admitted_it(
        self, tmp_path
    ):
        client, builds = app_on(
            tmp_path, hand_written(), Builds(lambda: Recording(delay=0.3))
        )
        with client:
            started = client.post("/analyze", json=posted("A"), headers=SAME_ORIGIN)
            assert started.status_code == 200, started.text
            assert save(client, {"tiers.base.model": OTHER_MODEL}).status_code == 200
            run = started.json()["run"]
            events = client.get(f"/events/{run}").text
            after = run_to_completion(client)

        assert '"status":"completed"' in events, events
        assert [runner.jobs for runner in builds.runners] == [[run], [after]]

    def test_a_removed_framework_refuses_a_later_resume(self, tmp_path):
        client, _ = app_on(tmp_path, hand_written(), Builds(PausingRunner))
        paused = start(client, questions=True)
        shown_list(client, paused)

        saved = save(client, {"frameworks.carried": "asvs"})
        refused = client.post(
            f"/answer/{paused}",
            json={"links": [], "facts": [], "revision": 0},
            headers=SAME_ORIGIN,
        )

        assert saved.status_code == 200, saved.text
        assert refused.status_code == 409, refused.text
        assert "does not carry stride" in refused.json()["message"]

    def test_require_certified_changes_whether_an_older_report_serves(self, tmp_path):
        uncertified = CertifyResult(
            certified=False, uncertified=({"node": "critic", "fingerprint": FP_A},)
        )
        client, _ = app_on(
            tmp_path, hand_written(), Builds(lambda: CertifyingRunner(uncertified))
        )
        run = run_to_completion(client)
        served = client.get(f"/report/{run}")

        saved = save(client, {"certification.require_certified": "true"})
        withheld = client.get(f"/report/{run}")

        assert saved.status_code == 200, saved.text
        assert (served.status_code, withheld.status_code) == (200, 404)

    def test_the_first_save_makes_the_directory_and_the_file_private(self, tmp_path):
        directory = tmp_path / "new" / "work-agent"
        client = settings_client(
            directory, TEST_TIER_ENV | TEST_CREDENTIAL_ENV, build=Builds()
        )

        saved = save(client, {"resilience.timeout_ms": "5000"})

        assert saved.status_code == 200, saved.text
        assert directory.stat().st_mode & 0o777 == 0o700
        assert (directory / "settings.env").stat().st_mode & 0o777 == 0o600
        assert [path.name for path in directory.iterdir()] == ["settings.env"]

    def test_it_writes_one_log_line_with_the_keys_and_no_value(self, tmp_path, caplog):
        client, _ = app_on(tmp_path, hand_written())
        caplog.set_level(logging.INFO, logger="webapp")

        save(client, {"tiers.base.model": OTHER_MODEL, "resilience.timeout_ms": "5000"})

        lines = [
            r.getMessage() for r in caplog.records if "settings saved" in r.message
        ]
        assert lines == ["settings saved: tiers.base.model, resilience.timeout_ms"]
        assert OTHER_MODEL not in caplog.text


class TestAReset:
    def test_it_removes_the_line_and_the_page_shows_the_shipped_value(self, tmp_path):
        client, _ = app_on(tmp_path, hand_written(ANALYSIS_TIMEOUT_MS="5000"))

        saved = save(client, {"resilience.timeout_ms": None})

        assert saved.status_code == 200, saved.text
        assert "ANALYSIS_TIMEOUT_MS" not in settings_text(tmp_path)
        assert rows_of(client)["resilience.timeout_ms"]["source"] == "file"

    def test_the_removal_of_an_unknown_line_lets_the_next_startup_pass(self, tmp_path):
        client, _ = app_on(tmp_path, hand_written(ANALYSIS_NOT_A_SETTING="1"))
        assert client.get("/").status_code == 503

        saved = save(client, remove=["ANALYSIS_NOT_A_SETTING"])
        restarted, _ = app_on(tmp_path)

        assert saved.status_code == 200, saved.text
        assert client.get("/").status_code == 200
        assert restarted.get("/").status_code == 200

    def test_a_known_line_is_not_an_unknown_one(self, tmp_path):
        client, _ = app_on(tmp_path, hand_written())

        refused = save(client, remove=["ANALYSIS_MODEL_BASE_MODEL", "ANALYSIS_ABSENT"])

        assert refused.status_code == 422
        keys = [error["key"] for error in refused.json()["errors"]]
        assert keys == ["ANALYSIS_MODEL_BASE_MODEL", "ANALYSIS_ABSENT"]


class TestARefusedSave:
    def test_each_value_rule_refusal_is_listed_once_with_no_value(self, tmp_path):
        client, _ = app_on(tmp_path, hand_written())
        before = settings_text(tmp_path)

        refused = save(
            client,
            {"resilience.timeout_ms": " 5000", "resilience.attempts": "3$HOME"},
        )

        assert refused.status_code == 422
        errors = refused.json()["errors"]
        assert [error["key"] for error in errors] == [
            "resilience.timeout_ms",
            "resilience.attempts",
        ]
        assert "5000" not in refused.text and "HOME" not in refused.text
        assert settings_text(tmp_path) == before

    @pytest.mark.parametrize(
        ("changes", "names"),
        [
            ({"tiers.base.model": "not-a-model!"}, "tiers.base.model"),
            ({"store.job_store": "nowhere"}, "ANALYSIS_JOB_STORE"),
            ({"store.retention_days": "0"}, "ANALYSIS_RETENTION_DAYS"),
            ({"resilience.timeout_ms": "-1"}, "timeout_ms"),
        ],
    )
    def test_a_loader_refusal_writes_nothing_and_keeps_the_cell(
        self, tmp_path, changes, names
    ):
        client, builds = app_on(tmp_path, hand_written())
        before = settings_text(tmp_path)

        refused = save(client, changes)
        run_to_completion(client)

        assert refused.status_code == 422, refused.text
        (error,) = refused.json()["errors"]
        assert names in error["message"]
        value = next(iter(changes.values()))
        assert len(value) < 3 or value not in refused.text
        assert settings_text(tmp_path) == before
        assert builds.runners[0].jobs

    @pytest.mark.parametrize(
        "key",
        [
            "tiers.review_independence",
            "sampling.base.temperature",
            "paths.model_tiers",
            "no.such.setting",
        ],
    )
    def test_a_key_that_the_page_does_not_change_is_refused(self, tmp_path, key):
        client, _ = app_on(tmp_path, hand_written())

        refused = save(client, {key: "x"})

        assert refused.status_code == 422, refused.text
        assert [error["key"] for error in refused.json()["errors"]] == [key]

    def test_a_key_that_the_process_environment_sets_is_refused(self, tmp_path):
        client, _ = app_on(tmp_path, hand_written(), ANALYSIS_TIMEOUT_MS="5000")

        refused = save(client, {"resilience.timeout_ms": "6000"})

        assert refused.status_code == 422
        assert "ANALYSIS_TIMEOUT_MS" in refused.json()["errors"][0]["message"]

    def test_a_body_over_the_limit_is_refused(self, tmp_path):
        client, _ = app_on(tmp_path, hand_written())
        body = b'{"revision": "' + b"a" * MAX_SAVE_BYTES + b'"}'

        refused = client.post(
            "/settings",
            content=body,
            headers=SAME_ORIGIN | {"Content-Type": "application/json"},
        )

        assert refused.status_code == 413

    @pytest.mark.parametrize(
        ("body", "media_type", "status"),
        [
            (b"not json", "application/json", 400),
            (b"[]", "application/json", 400),
            (b'{"revision": 1}', "application/json", 400),
            (b'{"revision": "r", "changes": {"k": 1}}', "application/json", 400),
            (b"[" * 10_000, "application/json", 400),
            (b'{"revision": "r"}', "text/plain", 415),
        ],
    )
    def test_a_body_that_is_not_a_save_is_refused(
        self, tmp_path, body, media_type, status
    ):
        client, _ = app_on(tmp_path, hand_written())

        refused = client.post(
            "/settings",
            content=body,
            headers=SAME_ORIGIN | {"Content-Type": media_type},
        )

        assert refused.status_code == status, refused.text

    def test_a_cross_origin_save_is_refused(self, tmp_path):
        client, _ = app_on(tmp_path, hand_written())

        refused = client.post(
            "/settings",
            json={"revision": revision(client), "changes": {}},
            headers={"Sec-Fetch-Site": "cross-site"},
        )

        assert refused.status_code == 403


class TestAStaleSave:
    def test_a_stale_revision_is_a_409_that_says_to_reload(self, tmp_path):
        client, _ = app_on(tmp_path, hand_written())
        old = revision(client)
        assert save(client, {"resilience.timeout_ms": "5000"}).status_code == 200

        stale = save(client, {"resilience.attempts": "2"}, rev=old)

        assert stale.status_code == 409
        assert "Reload the page" in stale.json()["message"]

    def test_a_hand_edit_after_the_load_is_a_409(self, tmp_path):
        client, _ = app_on(tmp_path, hand_written())
        loaded = revision(client)
        write_settings(tmp_path, hand_written(ANALYSIS_TIMEOUT_MS="7000"))

        stale = save(client, {"resilience.attempts": "2"}, rev=loaded)

        assert stale.status_code == 409
        assert "ANALYSIS_TIMEOUT_MS=7000" in settings_text(tmp_path)

    def test_a_hand_edit_while_the_check_runs_is_a_409(self, tmp_path):
        builds = Builds()

        def edits(env) -> Startup:
            built = builds(env)
            if len(builds.models) > 1:
                write_settings(tmp_path, hand_written(ANALYSIS_TIMEOUT_MS="7000"))
            return built

        write_settings(tmp_path, hand_written())
        client = settings_client(tmp_path, build=edits)

        stale = save(client, {"resilience.attempts": "2"})

        assert stale.status_code == 409
        assert "ANALYSIS_RETRY_ATTEMPTS" not in settings_text(tmp_path)

    def test_two_saves_at_the_same_time_do_not_interleave(self, tmp_path):
        """The second save waits for the first, then reads its revision as
        stale."""
        inside = threading.Lock()
        overlaps: list[bool] = []
        builds = Builds()

        def slow(env) -> Startup:
            overlaps.append(not inside.acquire(blocking=False))
            try:
                time.sleep(0.2)
                return builds(env)
            finally:
                inside.release()

        write_settings(tmp_path, hand_written())
        app = create_app(env=env_in(tmp_path), build=slow)
        loaded = revision(TestClient(app, base_url=LOOPBACK))

        async def both() -> list[int]:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url=LOOPBACK) as c:
                responses = await asyncio.gather(
                    *(
                        c.post(
                            "/settings",
                            json={"revision": loaded, "changes": change},
                            headers=SAME_ORIGIN,
                        )
                        for change in (
                            {"resilience.timeout_ms": "5000"},
                            {"resilience.attempts": "2"},
                        )
                    )
                )
            return sorted(response.status_code for response in responses)

        assert asyncio.run(both()) == [200, 409]
        assert not any(overlaps)


class TestAStoreChange:
    def test_it_passes_keeps_the_running_store_and_says_to_restart(self, tmp_path):
        client, _ = app_on(tmp_path, hand_written())
        run = run_to_completion(client)

        saved = save(client, {"store.retention_days": "30"})

        assert saved.status_code == 200, saved.text
        assert saved.json()["restart"] == ["store.retention_days"]
        assert client.get(f"/workspace/runs/{run}").status_code == 200

    def test_another_change_names_no_store_key(self, tmp_path):
        client, _ = app_on(tmp_path, hand_written())

        saved = save(client, {"resilience.timeout_ms": "5000"})

        assert saved.json()["restart"] == []


#: A key that no fixture holds, so a text that carries it leaked the key.
KEY = "sk-saved-from-the-page-never-shown"
ANTHROPIC_KEY = "credentials.anthropic.api_key"
OLD_KEY = "sk-ant-an-older-key"
#: The name form of a key file that a save writes.
KEY_FILE = re.compile(r"(?P<stem>[a-z0-9_]+)\.[0-9a-f]{16}")


def key_on_disk(tmp_path, name: str = "anthropic_api_key.0123456789abcdef") -> str:
    """A key file of the settings directory, as a save names it, and its line."""
    path = tmp_path / name
    path.write_text(OLD_KEY, encoding="utf-8")
    path.chmod(0o600)
    return str(path)


def named_key(tmp_path, var: str = "ANALYSIS_ANTHROPIC_API_KEY_FILE") -> Path:
    """The key file that the ``var`` line of ``settings.env`` names."""
    [value] = [
        line.partition("=")[2]
        for line in settings_text(tmp_path).splitlines()
        if line.startswith(f"{var}=")
    ]
    return Path(value)


def stems(tmp_path) -> list[str]:
    """The name of each file in ``tmp_path``, with the token of a key file
    replaced by ``*``."""
    return sorted(
        f"{match['stem']}.*" if (match := KEY_FILE.fullmatch(p.name)) else p.name
        for p in tmp_path.iterdir()
    )


def failing(path, values):
    raise OSError(28, "No space left on device")


class TestAKeySave:
    def test_it_writes_a_private_file_and_its_line(self, tmp_path):
        client, _ = app_on(tmp_path, hand_written())

        saved = save(client, {ANTHROPIC_KEY: KEY})

        assert saved.status_code == 200, saved.text
        path = named_key(tmp_path)
        assert path.parent == tmp_path
        assert KEY_FILE.fullmatch(path.name)["stem"] == "anthropic_api_key"
        assert path.read_text(encoding="utf-8") == KEY
        assert path.stat().st_mode & 0o777 == 0o600
        assert stems(tmp_path) == ["anthropic_api_key.*", "settings.env"]
        assert rows_of(client)[ANTHROPIC_KEY]["key_saved"] is True

    def test_each_save_writes_a_new_file_and_deletes_the_old_one(self, tmp_path):
        old_key = key_on_disk(tmp_path)
        client, _ = app_on(
            tmp_path, hand_written(ANALYSIS_ANTHROPIC_API_KEY_FILE=old_key)
        )

        saved = save(client, {ANTHROPIC_KEY: KEY})

        assert saved.status_code == 200, saved.text
        assert str(named_key(tmp_path)) != old_key
        assert named_key(tmp_path).read_text(encoding="utf-8") == KEY
        assert stems(tmp_path) == ["anthropic_api_key.*", "settings.env"]

    def test_a_key_save_builds_the_deployment_once(self, tmp_path):
        client, builds = app_on(tmp_path, hand_written())

        saved = save(client, {ANTHROPIC_KEY: KEY})
        run_to_completion(client)

        assert saved.status_code == 200, saved.text
        assert len(builds.runners) == 2
        assert builds.runners[1].jobs

    def test_the_built_deployment_reads_the_saved_key_file(self, tmp_path):
        # A selection that the check did not build reads the key file again.
        client, builds = app_on(tmp_path, hand_written())

        saved = save(client, {ANTHROPIC_KEY: KEY})
        started = client.post(
            "/analyze",
            json=posted("A web app talks to a database.", ["stride"]),
            headers=SAME_ORIGIN,
        )

        assert saved.status_code == 200, saved.text
        assert started.status_code == 200, started.text
        assert len(builds.runners) == 2

    def test_the_key_reaches_no_response_page_file_or_log(self, tmp_path, caplog):
        client, _ = app_on(tmp_path, hand_written())
        caplog.set_level(logging.DEBUG)

        refused = save(client, {ANTHROPIC_KEY: KEY, "tiers.base.model": "x y"})
        saved = save(client, {ANTHROPIC_KEY: KEY})
        page = client.get("/settings")

        assert (refused.status_code, saved.status_code) == (422, 200)
        for text in (refused.text, saved.text, page.text, settings_text(tmp_path)):
            assert KEY not in text
        assert KEY not in caplog.text
        assert all(KEY not in p.name for p in tmp_path.iterdir())

    @pytest.mark.parametrize("key", [" sk-padded", "", "sk-\x07bell"])
    def test_a_key_that_the_secret_rule_refuses_names_the_setting(self, tmp_path, key):
        client, _ = app_on(tmp_path, hand_written())
        before = settings_text(tmp_path)

        refused = save(client, {ANTHROPIC_KEY: key})

        assert refused.status_code == 422, refused.text
        [error] = refused.json()["errors"]
        assert error["key"] == ANTHROPIC_KEY
        assert error["message"].startswith("anthropic api key ")
        assert len(key) < 3 or key not in refused.text
        assert settings_text(tmp_path) == before
        assert [p.name for p in tmp_path.iterdir()] == ["settings.env"]

    def test_a_loader_refusal_leaves_no_key_file(self, tmp_path):
        client, _ = app_on(tmp_path, hand_written())

        refused = save(client, {ANTHROPIC_KEY: KEY, "resilience.timeout_ms": "0"})

        assert refused.status_code == 422, refused.text
        assert [p.name for p in tmp_path.iterdir()] == ["settings.env"]

    def test_a_hand_edit_while_the_check_runs_leaves_no_key_file(
        self, tmp_path, monkeypatch
    ):
        client, _ = app_on(tmp_path, hand_written())
        check = main.check_saved

        def editing(env, build):
            with (tmp_path / "settings.env").open("a", encoding="utf-8") as file:
                file.write("ANALYSIS_TIMEOUT_MS=5000\n")
            return check(env, build)

        monkeypatch.setattr(main, "check_saved", editing)

        stale = save(client, {ANTHROPIC_KEY: KEY})

        assert stale.status_code == 409, stale.text
        assert [p.name for p in tmp_path.iterdir()] == ["settings.env"]

    def test_the_key_files_come_first_and_the_deletion_last(
        self, tmp_path, monkeypatch
    ):
        old_key = key_on_disk(tmp_path)
        client, _ = app_on(
            tmp_path, hand_written(ANALYSIS_ANTHROPIC_API_KEY_FILE=old_key)
        )
        seen = []
        write = main.write_settings_env

        def recording(path, values):
            seen.append(stems(tmp_path))
            write(path, values)

        monkeypatch.setattr(main, "write_settings_env", recording)

        saved = save(
            client,
            {
                "tiers.review.vendor": "openai",
                "tiers.review.model": OTHER_MODEL,
                "credentials.openai.api_key": KEY,
            },
        )

        assert saved.status_code == 200, saved.text
        assert seen == [["anthropic_api_key.*", "openai_api_key.*", "settings.env"]]
        assert stems(tmp_path) == ["openai_api_key.*", "settings.env"]
        assert "ANALYSIS_ANTHROPIC_API_KEY_FILE" not in settings_text(tmp_path)

    def test_a_failed_write_of_settings_env_keeps_the_old_file_and_key(
        self, tmp_path, monkeypatch
    ):
        old_key = key_on_disk(tmp_path)
        client, builds = app_on(
            tmp_path, hand_written(ANALYSIS_ANTHROPIC_API_KEY_FILE=old_key)
        )
        before = settings_text(tmp_path)
        monkeypatch.setattr(main, "write_settings_env", failing)

        failed = save(
            client,
            {
                ANTHROPIC_KEY: KEY,
                "tiers.review.vendor": "openai",
                "tiers.review.model": OTHER_MODEL,
            },
        )
        run_to_completion(client)

        assert failed.status_code == 500, failed.text
        assert settings_text(tmp_path) == before
        assert Path(old_key).read_text(encoding="utf-8") == OLD_KEY
        assert stems(tmp_path) == ["anthropic_api_key.*", "settings.env"]
        assert builds.runners[0].jobs and not builds.runners[1].jobs

    def test_a_failed_key_write_leaves_no_key_file(self, tmp_path, monkeypatch):
        client, _ = app_on(tmp_path, hand_written())
        create = settings_save.create_private
        calls = []

        def second_fails(path, data):
            calls.append(path)
            if len(calls) == 2:
                raise OSError(28, "No space left on device")
            create(path, data)

        monkeypatch.setattr(settings_save, "create_private", second_fails)

        failed = save(client, {ANTHROPIC_KEY: KEY, "credentials.openai.api_key": KEY})

        assert failed.status_code == 500, failed.text
        assert [p.name for p in tmp_path.iterdir()] == ["settings.env"]

    def test_a_save_deletes_only_unnamed_files_of_the_key_name_form(self, tmp_path):
        # A save that stops before the swap, such as in a crash, can leave a
        # key file that no variable names.
        named = key_on_disk(tmp_path)
        orphan = key_on_disk(tmp_path, "openai_api_key.fedcba9876543210")
        process = key_on_disk(tmp_path, "gemini_api_key.00112233445566ff")
        kept = ["anthropic_api_key", "notes.0123456789abcdef", "openai_api_key.1"]
        for name in kept:
            key_on_disk(tmp_path, name)
        client, _ = app_on(
            tmp_path,
            hand_written(ANALYSIS_ANTHROPIC_API_KEY_FILE=named),
            ANALYSIS_GEMINI_API_KEY_FILE=process,
        )

        saved = save(client, {"resilience.timeout_ms": "5000"})

        assert saved.status_code == 200, saved.text
        assert not Path(orphan).exists()
        assert Path(named).is_file() and Path(process).is_file()
        assert all((tmp_path / name).is_file() for name in kept)

    def test_a_save_that_leaves_a_tier_on_the_vendor_keeps_its_key(self, tmp_path):
        old_key = key_on_disk(tmp_path)
        client, _ = app_on(
            tmp_path, hand_written(ANALYSIS_ANTHROPIC_API_KEY_FILE=old_key)
        )

        saved = save(client, {"resilience.timeout_ms": "5000"})

        assert saved.status_code == 200, saved.text
        assert Path(old_key).is_file()
        assert f"ANALYSIS_ANTHROPIC_API_KEY_FILE={old_key}" in settings_text(tmp_path)


class TestAFirstRun:
    def test_one_save_of_models_and_keys_fills_the_cell(self, tmp_path):
        vertex = {
            var: TEST_CREDENTIAL_ENV[var]
            for var in ("ANALYSIS_VERTEX_PROJECT", "ANALYSIS_VERTEX_LOCATION")
        }
        flags = {
            var: TEST_TIER_ENV[var]
            for var in ("ANALYSIS_ASSERTIONS", "ANALYSIS_CAPABILITY_READER")
        }
        client, builds = app_on(tmp_path, **vertex, **flags)
        assert client.get("/").status_code == 503

        saved = save(
            client,
            {
                **{
                    f"tiers.{tier}.{field}": TEST_TIER_ENV[
                        f"ANALYSIS_MODEL_{tier.upper()}_{field.upper()}"
                    ]
                    for tier in ("base", "strong", "review")
                    for field in ("vendor", "model")
                },
                ANTHROPIC_KEY: KEY,
                "credentials.openai.api_key": "sk-also-not-a-real-key",
            },
        )
        run = run_to_completion(client)

        assert saved.status_code == 200, saved.text
        assert client.get("/").status_code == 200
        assert builds.runners[-1].jobs == [run]
        assert named_key(tmp_path).read_text(encoding="utf-8") == KEY


def test_new_lines_refuse_a_setting_with_no_variable_under_optimize():
    """``python -O`` removes an ``assert``, so the refusal must be a raise."""
    script = (
        "from webapp.settings_save import Save, saved_lines\n"
        "saved_lines({}, Save('r', {'tiers.review_independence': 'x'}, ()), {})\n"
    )
    result = subprocess.run(
        [sys.executable, "-O", "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode != 0
    assert "ValueError: tiers.review_independence" in result.stderr
