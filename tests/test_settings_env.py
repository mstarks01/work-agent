"""The settings directory and ``settings.env`` of the first-run app (ADR 0092).

The loader tests drive the reader, the value rule and the merge. The app
tests drive :func:`webapp.main.create_app` with an environment mapping and a
settings directory, and read only the HTTP response.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from analysis_service.config_files import (
    MAX_VALUE_CHARS,
    SETTINGS_DIR_VAR,
    SettingsFileError,
    create_private,
    read_settings,
    read_settings_env,
    settings_dir,
    value_refusal,
    write_settings_env,
)
from analysis_service.settings_variables import deployment_env
from tests.factories import TEST_CREDENTIAL_ENV, TEST_TIER_ENV
from tests.test_webapp import LOOPBACK
from webapp.main import create_app

#: A model that no fixture sets, so a page that names it read the file.
HAND_MODEL = "gpt-4.1"

#: Each value form that the value rule refuses, with a marker that a message
#: must never repeat.
REFUSED_VALUES = {
    "backslash": "SECRET\\x",
    "dollar": "SECRET$HOME",
    "leading space": " SECRET",
    "trailing space": "SECRET ",
    "tab": "SE\tCRET",
    "single quote": "'SECRET'",
    "double quote": '"SECRET"',
    "too long": "SECRET" + "x" * MAX_VALUE_CHARS,
}

#: Each character that ends a line for some reader but not for this one.
LINE_BREAKS = ["\r", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", " ", " "]


def write_settings(directory: Path, text: str) -> Path:
    path = directory / "settings.env"
    path.write_text(text, encoding="utf-8", newline="")
    path.chmod(0o600)
    return path


def env_in(directory: Path, **process: str) -> dict[str, str]:
    """A process environment whose settings directory is ``directory``."""
    return {SETTINGS_DIR_VAR: str(directory), **process}


class TestTheWriter:
    def test_the_reader_reads_what_it_writes_with_the_same_revision(self, tmp_path):
        path = tmp_path / "settings.env"

        write_settings_env(path, {"ANALYSIS_A": "1", "ANALYSIS_B": "x/y"})

        assert path.read_text() == "ANALYSIS_A=1\nANALYSIS_B=x/y\n"
        assert read_settings(path).revision == sha256(path.read_bytes()).hexdigest()
        assert read_settings_env(path)["ANALYSIS_B"].value == "x/y"

    def test_a_missing_file_has_the_revision_of_no_bytes(self, tmp_path):
        assert read_settings(tmp_path / "settings.env").revision == sha256().hexdigest()

    def test_the_first_write_makes_a_private_directory(self, tmp_path):
        path = tmp_path / "a" / "work-agent" / "settings.env"

        write_settings_env(path, {"ANALYSIS_A": "1"})

        assert path.parent.stat().st_mode & 0o777 == 0o700
        assert path.stat().st_mode & 0o777 == 0o600
        assert [entry.name for entry in path.parent.iterdir()] == ["settings.env"]

    def test_it_keeps_the_mode_of_a_directory_that_exists(self, tmp_path):
        tmp_path.chmod(0o750)

        write_settings_env(tmp_path / "settings.env", {"ANALYSIS_A": "1"})

        assert tmp_path.stat().st_mode & 0o777 == 0o750

    def test_it_refuses_a_directory_that_others_can_write(self, tmp_path):
        tmp_path.chmod(0o777)

        with pytest.raises(SettingsFileError, match="mode 0777"):
            write_settings_env(tmp_path / "settings.env", {"ANALYSIS_A": "1"})
        tmp_path.chmod(0o700)
        assert not list(tmp_path.iterdir())

    def test_a_new_file_is_private_and_never_replaces_one(self, tmp_path):
        path = tmp_path / "work-agent" / "key"

        create_private(path, b"first")
        with pytest.raises(FileExistsError):
            create_private(path, b"second")

        assert path.read_bytes() == b"first"
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700

    @pytest.mark.parametrize("form", sorted(REFUSED_VALUES))
    def test_it_writes_no_value_that_the_rule_refuses(self, tmp_path, form):
        path = tmp_path / "settings.env"

        with pytest.raises(SettingsFileError) as refused:
            write_settings_env(path, {"ANALYSIS_A": REFUSED_VALUES[form]})

        assert "SECRET" not in str(refused.value)
        assert not path.exists()

    @pytest.mark.parametrize("key", ["analysis_a", "ANALYSIS_A=B", "OTHER"])
    def test_it_writes_no_key_outside_the_pattern(self, tmp_path, key):
        with pytest.raises(SettingsFileError):
            write_settings_env(tmp_path / "settings.env", {key: "1"})


class TestTheSettingsDirectory:
    def test_the_variable_moves_it(self, tmp_path):
        assert settings_dir({SETTINGS_DIR_VAR: str(tmp_path)}) == tmp_path

    def test_it_sits_under_an_absolute_xdg_config_home(self, tmp_path):
        assert (
            settings_dir({"XDG_CONFIG_HOME": str(tmp_path)}) == tmp_path / "work-agent"
        )

    @pytest.mark.parametrize("xdg", [None, "", "relative/config"])
    def test_it_falls_back_to_dot_config(self, xdg):
        env = {} if xdg is None else {"XDG_CONFIG_HOME": xdg}
        assert settings_dir(env) == Path.home() / ".config" / "work-agent"


class TestTheReader:
    def test_a_missing_file_is_no_lines(self, tmp_path):
        assert read_settings_env(tmp_path / "absent" / "settings.env") == {}

    def test_each_value_is_the_text_after_the_first_equals(self, tmp_path):
        path = write_settings(
            tmp_path, "ANALYSIS_A=x=y\n\nANALYSIS_B=\nANALYSIS_C=a b\n"
        )
        lines = read_settings_env(path)
        assert {key: line.value for key, line in lines.items()} == {
            "ANALYSIS_A": "x=y",
            "ANALYSIS_B": "",
            "ANALYSIS_C": "a b",
        }
        assert lines["ANALYSIS_C"].number == 4

    @pytest.mark.parametrize(
        "line",
        [
            "export ANALYSIS_A=1",
            "# a comment",
            "analysis_a=1",
            "ANALYSIS_A",
            " ANALYSIS_A=1",
            "ANALYSIS_A =1",
            "OTHER_A=1",
            "﻿ANALYSIS_A=1",
        ],
    )
    def test_each_other_line_is_refused_with_its_number(self, tmp_path, line):
        path = write_settings(tmp_path, f"ANALYSIS_OK=1\n{line}\n")
        with pytest.raises(
            SettingsFileError, match=r"settings\.env line 2: "
        ) as caught:
            read_settings_env(path)
        assert "=1" not in str(caught.value)

    def test_a_key_that_occurs_two_times_is_refused(self, tmp_path):
        path = write_settings(tmp_path, "ANALYSIS_A=1\nANALYSIS_B=2\nANALYSIS_A=3\n")
        with pytest.raises(
            SettingsFileError, match=r"line 3: sets ANALYSIS_A again, after line 1"
        ):
            read_settings_env(path)

    def test_a_file_that_is_not_utf8_is_refused(self, tmp_path):
        (tmp_path / "settings.env").write_bytes(b"ANALYSIS_A=\xff\n")
        (tmp_path / "settings.env").chmod(0o600)
        with pytest.raises(SettingsFileError, match="is not UTF-8"):
            read_settings_env(tmp_path / "settings.env")

    @pytest.mark.parametrize("char", LINE_BREAKS, ids=repr)
    def test_it_does_not_split_on_another_line_break(self, tmp_path, char):
        path = write_settings(tmp_path, f"ANALYSIS_A=1{char}ANALYSIS_B=2\n")
        lines = read_settings_env(path)
        assert list(lines) == ["ANALYSIS_A"]
        assert value_refusal(lines["ANALYSIS_A"].value) is not None


class TestTheValueRule:
    @pytest.mark.parametrize("value", ["", "gpt-4.1", "a b", "/tmp/key", "x" * 4096])
    def test_it_passes_a_plain_value(self, value):
        assert value_refusal(value) is None

    @pytest.mark.parametrize("value", REFUSED_VALUES.values(), ids=REFUSED_VALUES)
    def test_it_refuses_each_form_and_never_repeats_the_value(self, value):
        refusal = value_refusal(value)
        assert refusal is not None
        assert "SECRET" not in refusal


class TestTheMerge:
    def test_settings_env_adds_its_lines(self, tmp_path):
        write_settings(tmp_path, f"ANALYSIS_MODEL_BASE_MODEL={HAND_MODEL}\n")
        env = deployment_env(env_in(tmp_path, PATH="/usr/bin"))
        assert env["ANALYSIS_MODEL_BASE_MODEL"] == HAND_MODEL
        assert env["PATH"] == "/usr/bin"

    def test_a_missing_directory_adds_nothing(self, tmp_path):
        process = env_in(tmp_path / "absent")
        assert deployment_env(process) == process

    def test_a_variable_that_both_sources_set_is_refused(self, tmp_path):
        write_settings(tmp_path, f"ANALYSIS_MODEL_BASE_MODEL={HAND_MODEL}\n")
        process = env_in(tmp_path, ANALYSIS_MODEL_BASE_MODEL="gpt-4.1-mini")
        with pytest.raises(
            SettingsFileError,
            match="line 1: ANALYSIS_MODEL_BASE_MODEL is also set in the process",
        ):
            deployment_env(process)

    def test_an_unknown_key_is_refused_with_its_line_number(self, tmp_path):
        write_settings(tmp_path, "ANALYSIS_TIMEOUT_MS=1000\nANALYSIS_RENAMED=1\n")
        with pytest.raises(
            SettingsFileError,
            match="line 2: ANALYSIS_RENAMED is not a setting that this version reads",
        ):
            deployment_env(env_in(tmp_path))

    def test_settings_env_cannot_move_its_own_directory(self, tmp_path):
        write_settings(tmp_path, f"{SETTINGS_DIR_VAR}=/elsewhere\n")
        with pytest.raises(
            SettingsFileError, match=f"line 1: {SETTINGS_DIR_VAR} is not"
        ):
            deployment_env(env_in(tmp_path))

    @pytest.mark.parametrize("value", REFUSED_VALUES.values(), ids=REFUSED_VALUES)
    def test_the_value_rule_refuses_a_line(self, tmp_path, value):
        write_settings(tmp_path, f"ANALYSIS_MODEL_BASE_MODEL={value}\n")
        with pytest.raises(
            SettingsFileError, match="line 1: ANALYSIS_MODEL_BASE_MODEL "
        ) as caught:
            deployment_env(env_in(tmp_path))
        assert "SECRET" not in str(caught.value)

    @pytest.mark.parametrize("value", REFUSED_VALUES.values(), ids=REFUSED_VALUES)
    def test_the_value_rule_refuses_a_process_variable(self, tmp_path, value):
        process = env_in(tmp_path, ANALYSIS_MODEL_BASE_MODEL=value)
        with pytest.raises(
            SettingsFileError,
            match="ANALYSIS_MODEL_BASE_MODEL in the process environment ",
        ) as caught:
            deployment_env(process)
        assert "SECRET" not in str(caught.value)

    def test_the_value_rule_reads_only_the_deployment_variables(self, tmp_path):
        process = env_in(tmp_path, PS1="\\u@\\h$ ")
        assert deployment_env(process) == process


def hand_written(**overrides: str) -> str:
    """A ``settings.env`` that selects every tier and its credentials."""
    lines = TEST_TIER_ENV | TEST_CREDENTIAL_ENV | overrides
    return "".join(f"{key}={value}\n" for key, value in lines.items())


class TestTheFirstRunApp:
    def test_it_runs_on_a_hand_written_settings_env(self, tmp_path):
        write_settings(tmp_path, hand_written(ANALYSIS_MODEL_BASE_MODEL=HAND_MODEL))
        client = TestClient(create_app(env=env_in(tmp_path)), base_url=LOOPBACK)

        page = client.get("/")

        assert page.status_code == 200
        assert HAND_MODEL in page.text

    def test_it_carries_what_settings_env_names(self, tmp_path):
        write_settings(tmp_path, hand_written(ANALYSIS_FRAMEWORKS_CARRIED="stride"))
        client = TestClient(create_app(env=env_in(tmp_path)), base_url=LOOPBACK)

        page = client.get("/")

        assert page.status_code == 200
        assert 'value="stride"' in page.text
        assert 'value="asvs"' not in page.text

    def test_a_conflict_stops_it_and_names_the_variable(self, tmp_path):
        write_settings(tmp_path, hand_written())
        process = env_in(tmp_path, ANALYSIS_MODEL_BASE_MODEL=HAND_MODEL)
        client = TestClient(create_app(env=process), base_url=LOOPBACK)

        page = client.get("/")

        assert page.status_code == 503
        assert "ANALYSIS_MODEL_BASE_MODEL is also set in the process" in page.text

    def test_an_unknown_key_stops_it_and_names_the_key_and_line(self, tmp_path):
        write_settings(tmp_path, hand_written() + "ANALYSIS_RENAMED=1\n")
        client = TestClient(create_app(env=env_in(tmp_path)), base_url=LOOPBACK)

        page = client.get("/")

        assert page.status_code == 503
        line = len(TEST_TIER_ENV | TEST_CREDENTIAL_ENV) + 1
        assert f"line {line}: ANALYSIS_RENAMED is not a setting" in page.text

    def test_a_refused_value_stops_it_without_the_value(self, tmp_path):
        write_settings(tmp_path, hand_written(ANALYSIS_MODEL_BASE_MODEL="SECRET\\x"))
        client = TestClient(create_app(env=env_in(tmp_path)), base_url=LOOPBACK)

        page = client.get("/")

        assert page.status_code == 503
        assert "ANALYSIS_MODEL_BASE_MODEL holds a" in page.text
        assert "SECRET" not in page.text
