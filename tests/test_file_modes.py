"""The permission rule of the settings files and the secret files, and the
secret rule (ADR 0092).

The tests make real files with real modes in a temporary directory. The loader
tests drive :func:`~analysis_service.config_files.private_fd` and
:func:`~analysis_service.vendors.read_secret`. The app tests drive the
first-run app and ``/v1``, and read only the HTTP response and the log.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from analysis_service import config_files
from analysis_service.api import create_app as create_v1_app
from analysis_service.config_files import (
    MAX_VALUE_CHARS,
    SettingsFileError,
    read_private,
    read_settings_env,
)
from analysis_service.deployment import Deployment
from analysis_service.jobs import InMemoryJobStore
from analysis_service.vendors import ProviderAuthError, read_secret
from tests.factories import TEST_CREDENTIAL_ENV, TEST_TIER_ENV
from tests.test_api import FakeVerifier, auth, submission
from tests.test_settings_env import (
    hand_written,
    private_file,
    settings_client,
    write_settings,
)

#: The key file of the base tier, which the runner build reads.
KEY_VAR = "ANALYSIS_OPENAI_API_KEY_FILE"

#: Each secret that the secret rule refuses. Each one holds a marker that a
#: message must never repeat.
REFUSED_SECRETS = {
    "leading space": " SECRET",
    "trailing space": "SECRET ",
    "two final newlines": "SECRET\n\n",
    "tab": "SE\tCRET",
    "nul": "SE\x00CRET",
    "too long": "SECRET" + "x" * MAX_VALUE_CHARS,
}


def key_env(path: Path) -> dict[str, str]:
    return {KEY_VAR: str(path)}


class TestThePermissionRule:
    @pytest.mark.parametrize("mode", [0o620, 0o602, 0o666])
    def test_a_file_that_others_can_write_is_refused(self, tmp_path, mode):
        path = private_file(tmp_path / "settings.env", "", mode)
        with pytest.raises(SettingsFileError) as refused:
            read_private(path, SettingsFileError)
        assert str(refused.value) == (
            f"{path}: mode {mode:04o} lets the group or other users write it"
        )

    @pytest.mark.parametrize("mode", [0o600, 0o640, 0o644, 0o400])
    def test_a_file_that_others_cannot_write_passes(self, tmp_path, mode):
        path = private_file(tmp_path / "settings.env", "A", mode)
        assert read_private(path, SettingsFileError) == b"A"

    @pytest.mark.parametrize("mode", [0o604, 0o644])
    def test_a_secret_that_others_can_read_is_refused(self, tmp_path, mode):
        path = private_file(tmp_path / "key", "SECRET", mode)
        with pytest.raises(ProviderAuthError) as refused:
            read_secret(key_env(path), KEY_VAR)
        assert str(refused.value) == f"{path}: mode {mode:04o} lets other users read it"

    @pytest.mark.parametrize("mode", [0o640, 0o440])
    def test_a_secret_that_the_group_can_read_passes(self, tmp_path, mode):
        path = private_file(tmp_path / "key", "SECRET", mode)
        assert read_secret(key_env(path), KEY_VAR) == "SECRET"

    def test_a_file_of_another_owner_is_refused(self, tmp_path, monkeypatch):
        path = private_file(tmp_path / "settings.env", "")
        other = os.geteuid() + 1
        monkeypatch.setattr(config_files.os, "geteuid", lambda: other)
        with pytest.raises(SettingsFileError, match="is not the process user"):
            read_private(path, SettingsFileError)

    def test_it_reads_the_open_file_and_not_the_path(self, tmp_path, monkeypatch):
        """A file that replaces the path after the open does not pass."""
        path = private_file(tmp_path / "key", "SECRET", 0o604)
        good = private_file(tmp_path / "good", "GOOD")
        real_open = os.open

        def open_then_swap(*args, **kwargs):
            fd = real_open(*args, **kwargs)
            os.replace(good, path)
            return fd

        monkeypatch.setattr(config_files.os, "open", open_then_swap)
        with pytest.raises(ProviderAuthError, match="mode 0604"):
            read_secret(key_env(path), KEY_VAR)
        assert path.stat().st_mode & 0o777 == 0o600

    def test_a_settings_directory_that_others_can_write_is_refused(self, tmp_path):
        directory = tmp_path / "settings"
        directory.mkdir(mode=0o700)
        directory.chmod(0o770)
        with pytest.raises(SettingsFileError, match=f"{directory}: mode 0770"):
            read_settings_env(directory / "settings.env")


class TestTheSecretRule:
    def test_one_final_newline_is_not_part_of_the_secret(self, tmp_path):
        path = private_file(tmp_path / "key", "SECRET\n")
        assert read_secret(key_env(path), KEY_VAR) == "SECRET"

    def test_a_password_with_spaces_and_a_backslash_passes(self, tmp_path):
        password = "pass word \\ with $ and 'quotes'"
        path = private_file(tmp_path / "password", password)
        assert read_secret(key_env(path), KEY_VAR) == password

    @pytest.mark.parametrize("text", ["", "\n"])
    def test_an_empty_secret_is_refused(self, tmp_path, text):
        path = private_file(tmp_path / "key", text)
        with pytest.raises(ProviderAuthError, match=f"{KEY_VAR} names is empty"):
            read_secret(key_env(path), KEY_VAR)

    @pytest.mark.parametrize("secret", REFUSED_SECRETS.values(), ids=REFUSED_SECRETS)
    def test_it_refuses_each_form_and_never_repeats_the_secret(self, tmp_path, secret):
        path = private_file(tmp_path / "key", secret)
        with pytest.raises(ProviderAuthError, match=KEY_VAR) as refused:
            read_secret(key_env(path), KEY_VAR)
        assert "SECRET" not in str(refused.value)

    def test_an_unset_variable_is_no_secret(self):
        assert read_secret({}, KEY_VAR) == ""


class TestTheFirstRunApp:
    def page(self, directory: Path, **overrides: str):
        write_settings(directory, hand_written(**overrides))
        client = settings_client(directory)
        return client.get("/")

    def test_a_settings_env_that_others_can_write_stops_it(self, tmp_path):
        write_settings(tmp_path, hand_written()).chmod(0o620)
        client = settings_client(tmp_path)

        page = client.get("/")

        assert page.status_code == 503
        assert f"{tmp_path / 'settings.env'}: mode 0620" in page.text

    def test_a_settings_directory_that_others_can_write_stops_it(self, tmp_path):
        tmp_path.chmod(0o777)
        page = self.page(tmp_path)

        assert page.status_code == 503
        assert f"{tmp_path}: mode 0777" in page.text

    def test_a_secret_that_others_can_read_stops_it(self, tmp_path):
        key = private_file(tmp_path / "openai_api_key", "SECRET", 0o604)
        page = self.page(tmp_path, **key_env(key))

        assert page.status_code == 503
        assert f"{key}: mode 0604 lets other users read it" in page.text
        assert "SECRET" not in page.text

    def test_a_secret_that_the_group_can_read_runs(self, tmp_path):
        key = private_file(tmp_path / "openai_api_key", "SECRET", 0o640)
        assert self.page(tmp_path, **key_env(key)).status_code == 200


class TestTheV1Service:
    def test_a_secret_that_others_can_read_refuses_a_job(self, tmp_path, caplog):
        key = private_file(tmp_path / "openai_api_key", "SECRET", 0o604)
        env = TEST_TIER_ENV | TEST_CREDENTIAL_ENV | key_env(key)
        app = create_v1_app(
            deployment=Deployment.from_env(env),
            store=InMemoryJobStore(),
            verifier=FakeVerifier(),
        )

        with TestClient(app) as client, caplog.at_level(logging.ERROR):
            response = client.post("/v1/jobs", json=submission(), headers=auth())

        assert response.status_code == 503
        assert f"{key}: mode 0604 lets other users read it" in caplog.text
        assert "SECRET" not in response.text + caplog.text
