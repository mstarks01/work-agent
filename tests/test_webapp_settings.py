"""The Settings page of the first-run app (#1775), at its HTTP surface.

Each test builds the app on a settings directory in ``tmp_path`` with a
process environment of its own, and reads the page and its script data as the
browser gets them.
"""

from __future__ import annotations

import json
import logging
import re

import pytest
from fastapi.testclient import TestClient

from analysis_service.deployment import DEFAULT_RESILIENCE_PATH
from analysis_service.jobs import InMemoryJobStore
from analysis_service.model_gate import tool_models
from analysis_service.settings_variables import secret_vars
from analysis_service.store_backends import env_variables as store_variables
from analysis_service.vendors import VENDOR_NAMES
from tests.factories import TEST_CREDENTIAL_ENV, TEST_TIER_ENV, secret_env
from tests.test_settings_env import env_in, hand_written, write_settings
from tests.test_webapp import LOOPBACK
from webapp.main import Workspace, create_app
from webapp.settings_page import SETTINGS

#: A key that no fixture holds, so a page that carries it leaked the file.
KEY = "sk-ant-page-must-never-show-this"


def client_for(tmp_path, settings: str = "", **process: str) -> TestClient:
    if settings:
        write_settings(tmp_path, settings)
    return TestClient(create_app(env=env_in(tmp_path, **process)), base_url=LOOPBACK)


def memory_client(tmp_path, **process: str) -> TestClient:
    """The app on a memory store of its own, so a store setting that the
    startup would refuse still reaches the page."""
    write_settings(tmp_path, hand_written())
    app = create_app(
        workspace=Workspace(store=InMemoryJobStore()), env=env_in(tmp_path, **process)
    )
    return TestClient(app, base_url=LOOPBACK)


def page_data(response) -> dict:
    """The data the page's script draws from."""
    block = re.search(
        r'<script type="application/json" id="settings"[^>]*>(.*?)</script>',
        response.text,
        re.DOTALL,
    )
    assert block is not None, "the page carries no settings data"
    return json.loads(block.group(1))


def rows_of(client) -> dict[str, dict]:
    return {row["key"]: row for row in page_data(client.get("/settings"))["rows"]}


class TestTheRows:
    def test_each_shown_entry_is_in_its_group_and_no_excluded_one_is(self, tmp_path):
        rows = rows_of(client_for(tmp_path, hand_written()))

        excluded = {key for key, s in SETTINGS.items() if s.mark == "excluded"}
        assert excluded
        assert not excluded & rows.keys()
        for key, row in rows.items():
            assert (row["group"], row["label"], row["mark"]) == (
                SETTINGS[key].group,
                SETTINGS[key].label,
                SETTINGS[key].mark,
            )

    def test_each_entry_that_no_selection_hides_shows(self, tmp_path):
        rows = rows_of(client_for(tmp_path, hand_written()))

        follows = {
            key
            for key, s in SETTINGS.items()
            if s.variable in store_variables()
            or key.startswith(("credentials.", "charges.", "upstreams."))
        }
        always = {key for key, s in SETTINGS.items() if s.mark != "excluded"} - follows
        assert always <= rows.keys()

    def test_a_shown_only_entry_carries_its_reason(self, tmp_path):
        row = rows_of(client_for(tmp_path, hand_written()))["tiers.review_independence"]

        assert row["mark"] == "shows"
        assert row["reason"] == SETTINGS["tiers.review_independence"].reason


class TestTheSource:
    def test_each_source_shows(self, tmp_path):
        settings = hand_written(ANALYSIS_TIMEOUT_MS="1000")
        rows = rows_of(client_for(tmp_path, settings, ANALYSIS_MAX_SOURCES="5"))

        assert (
            rows["resilience.max_sources"].items()
            >= {
                "value": "5",
                "source": "process",
                "variable": "ANALYSIS_MAX_SOURCES",
            }.items()
        )
        assert (
            rows["resilience.timeout_ms"].items()
            >= {
                "value": "1000",
                "source": "page",
                "shipped": "300000",
            }.items()
        )
        assert (
            rows["resilience.attempts"].items()
            >= {
                "value": "3",
                "source": "file",
                "file": str(DEFAULT_RESILIENCE_PATH),
            }.items()
        )
        assert (
            rows["passes.compact_extraction"].items()
            >= {
                "value": "false",
                "source": "default",
            }.items()
        )

    def test_only_a_process_value_names_its_variable(self, tmp_path):
        rows = rows_of(client_for(tmp_path, hand_written(), ANALYSIS_MAX_SOURCES="5"))

        named = {key for key, row in rows.items() if "variable" in row}
        assert named == {"resilience.max_sources"}

    def test_a_tier_shows_none_shipped(self, tmp_path):
        row = rows_of(client_for(tmp_path, hand_written()))["tiers.base.model"]

        assert row["source"] == "page"
        assert row["shipped"] is None

    def test_a_config_path_moves_the_file_a_value_comes_from(self, tmp_path):
        moved = tmp_path / "resilience.toml"
        moved.write_text(
            DEFAULT_RESILIENCE_PATH.read_text(encoding="utf-8").replace(
                "attempts = 3", "attempts = 4"
            ),
            encoding="utf-8",
        )
        rows = rows_of(
            client_for(tmp_path, hand_written(), ANALYSIS_RESILIENCE=str(moved))
        )

        assert rows["resilience.attempts"]["value"] == "4"
        assert rows["resilience.attempts"]["file"] == str(moved)


class TestSecrets:
    def test_no_key_and_no_key_path_reaches_the_page_or_a_log(self, tmp_path, caplog):
        key_env = secret_env("ANALYSIS_ANTHROPIC_API_KEY_FILE", KEY)
        path = key_env["ANALYSIS_ANTHROPIC_API_KEY_FILE"]
        caplog.set_level(logging.DEBUG)
        client = client_for(tmp_path, hand_written(**key_env))

        response = client.get("/settings")

        assert KEY not in response.text
        assert path not in response.text
        assert KEY not in caplog.text
        row = {r["key"]: r for r in page_data(response)["rows"]}[
            "credentials.anthropic.api_key"
        ]
        assert row["secret"] is True
        assert row["key_saved"] is True
        assert "value" not in row

    def test_each_secret_row_holds_only_its_state(self, tmp_path):
        rows = rows_of(client_for(tmp_path, hand_written()))

        secret = [
            row for key, row in rows.items() if SETTINGS[key].variable in secret_vars()
        ]
        assert secret
        for row in secret:
            assert {"value", "shipped", "file"}.isdisjoint(row)

    def test_a_missing_key_file_reads_as_no_key(self, tmp_path):
        settings = hand_written(
            ANALYSIS_ANTHROPIC_API_KEY_FILE=str(tmp_path / "absent")
        )
        rows = rows_of(client_for(tmp_path, settings))

        assert rows["credentials.anthropic.api_key"]["key_saved"] is False

    @pytest.mark.parametrize(
        "url",
        [
            "postgresql+asyncpg://user:PASSWORD@db/jobs",
            "postgresql+asyncpg://user@db/jobs?password=PASSWORD",
            "postgresql+asyncpg://user:PASSWORD@[::1/jobs",
        ],
    )
    def test_a_url_password_is_withheld(self, tmp_path, url):
        client = memory_client(
            tmp_path, ANALYSIS_JOB_STORE="sql", ANALYSIS_DATABASE_URL=url
        )

        response = client.get("/settings")

        assert "PASSWORD" not in response.text
        row = {r["key"]: r for r in page_data(response)["rows"]}["store.database_url"]
        assert row["value"] is None


class TestWhatTheSelectionShows:
    def test_each_vendor_row_carries_its_vendor_and_the_modes_that_read_it(
        self, tmp_path
    ):
        rows = rows_of(client_for(tmp_path, hand_written()))

        marks = {
            key: (row["vendor"], row.get("modes"))
            for key, row in rows.items()
            if "vendor" in row
        }
        assert marks["credentials.bedrock.mode"] == ("bedrock", None)
        assert marks["credentials.bedrock.api_key"] == ("bedrock", ["api_key"])
        assert marks["credentials.bedrock.region"] == ("bedrock", ["api_key", "iam"])
        assert marks["credentials.vertex.project"] == ("vertex", ["iam"])
        assert marks["charges.openrouter"] == ("openrouter", None)
        assert "credentials.vertex.mode" not in rows
        assert {key for key in rows if key.startswith("credentials.")} <= marks.keys()

    def test_the_data_carries_the_modes_of_each_vendor(self, tmp_path):
        data = page_data(client_for(tmp_path, hand_written()).get("/settings"))

        assert data["modes"]["bedrock"] == ["api_key", "iam"]
        assert data["modes"]["vertex"] == ["iam"]

    def test_each_iam_vendor_carries_its_setup_command(self, tmp_path):
        data = page_data(client_for(tmp_path, hand_written()).get("/settings"))

        setups = {setup["vendor"]: setup["command"] for setup in data["iam"]}
        assert setups.keys() == {"bedrock", "vertex"}
        assert setups["vertex"] == "gcloud auth application-default login"

    def test_the_memory_store_shows_no_database_or_object_setting(self, tmp_path):
        rows = rows_of(client_for(tmp_path, hand_written()))

        store = {key for key in rows if key.startswith("store.")}
        assert store == {"store.job_store", "store.retention_days"}
        assert rows["store.job_store"]["value"] == "memory"
        assert all(rows[key]["restart"] for key in store)

    def test_the_sql_store_shows_the_reads_of_its_object_store(self, tmp_path):
        rows = rows_of(
            memory_client(
                tmp_path,
                ANALYSIS_JOB_STORE="sql",
                ANALYSIS_OBJECT_STORE="filesystem",
            )
        )

        assert {"store.database_url", "store.object_store", "store.object_root"} <= (
            rows.keys()
        )
        assert "store.object_bucket" not in rows


class TestModelSuggestions:
    def test_the_suggestions_come_from_the_model_gate(self, tmp_path):
        data = page_data(client_for(tmp_path, hand_written()).get("/settings"))

        assert data["models"] == {
            vendor: list(tool_models(vendor)) for vendor in VENDOR_NAMES
        }

    def test_each_suggestion_is_a_chat_model_that_calls_tools(self):
        import litellm

        names = tool_models("anthropic")

        assert names
        for name in names:
            info = (
                litellm.model_cost.get(name) or litellm.model_cost[f"anthropic/{name}"]
            )
            assert info["mode"] == "chat"
            assert info["supports_function_calling"]

    def test_a_model_outside_the_map_serves_without_a_refusal(self, tmp_path):
        settings = hand_written(ANALYSIS_MODEL_REVIEW_MODEL="claude-sonnet-5-5")
        client = client_for(tmp_path, settings)

        assert "claude-sonnet-5-5" not in tool_models("anthropic")
        assert client.get("/").status_code == 200
        assert rows_of(client)["tiers.review.model"]["value"] == "claude-sonnet-5-5"


class TestAnEngineThatDoesNotBuild:
    def test_the_page_serves_with_the_loader_message(self, tmp_path):
        client = client_for(tmp_path)

        response = client.get("/settings")

        assert response.status_code == 200
        assert "no vendor selected" in page_data(response)["error"]

    def test_a_built_engine_shows_no_banner(self, tmp_path):
        client = client_for(tmp_path, hand_written())

        assert page_data(client.get("/settings"))["error"] is None

    def test_the_diagnostic_page_links_to_it(self, tmp_path):
        response = client_for(tmp_path).get("/")

        assert response.status_code == 503
        assert 'href="/settings"' in response.text

    def test_an_unknown_line_shows_its_key_and_line_number(self, tmp_path):
        client = client_for(tmp_path, hand_written() + "ANALYSIS_RENAMED=1\n")

        data = page_data(client.get("/settings"))

        line = len(TEST_TIER_ENV | TEST_CREDENTIAL_ENV) + 1
        assert data["unknown"] == [{"key": "ANALYSIS_RENAMED", "line": line}]
        assert "ANALYSIS_RENAMED" in data["error"]
        assert rows_of(client)["tiers.base.model"]["source"] == "page"


class TestLoopbackControls:
    def test_a_rebound_host_is_refused(self, tmp_path):
        client = TestClient(create_app(env=env_in(tmp_path)))

        assert client.get("/settings").status_code == 400

    def test_the_policy_grants_script_style_and_its_own_origin(self, tmp_path):
        csp = client_for(tmp_path).get("/settings").headers["Content-Security-Policy"]

        assert "script-src 'nonce-" in csp
        assert "connect-src 'self'" in csp

    @pytest.mark.parametrize("path", ["/", "/settings"])
    def test_the_first_run_nav_links_to_it(self, tmp_path, path):
        client = client_for(tmp_path, hand_written())

        assert '<a href="/settings"' in client.get(path).text
