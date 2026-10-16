"""One reader each for the TOML files, the boolean flags and ``settings.env``.

Four loaders refuse the same two failures the same way: a file that cannot
be read, and a file that is not TOML. Each one names its own error class, so
the caller that handles that loader's failures keeps catching what it caught.
The loaders whose refusals differ by design — the framework list, which names
a missing file on its own, and the keyring, which does not echo the reason —
keep their own read.

The first-run app reads ``settings.env`` from the settings directory, in the
strict format of ADR 0092. :func:`value_refusal` is the one value rule: the
reader's caller and the writer of the file call it.
"""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NamedTuple

from analysis_service.errors import ConfigError

#: The variable that moves the settings directory. The app reads it from the
#: process environment only, because ``settings.env`` cannot name its own
#: location.
SETTINGS_DIR_VAR = "ANALYSIS_SETTINGS_DIR"
SETTINGS_FILE = "settings.env"
MAX_VALUE_CHARS = 4096
_KEY = re.compile(r"ANALYSIS_[A-Z0-9_]+")


class SettingsFileError(ConfigError):
    """A ``settings.env`` that the first-run app cannot run on.

    A message names the file, the line number and the key, and never the
    value, because a line can hold a secret by mistake.
    """


class SettingsLine(NamedTuple):
    """The value of one ``settings.env`` line, and its line number."""

    number: int
    value: str


def read_toml(path: Path | str, error: type[Exception]) -> dict[str, Any]:
    """Parse one TOML file, or raise ``error`` naming the file and the cause."""
    try:
        return tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise error(f"{path}: invalid TOML: {exc}") from exc
    except OSError as exc:
        raise error(f"{path}: cannot be read: {exc}") from exc


def env_flag(env: Mapping[str, str], var: str, *, default: bool = False) -> bool:
    """A boolean env flag: ``default`` when unset or empty, else on only for an affirmative."""
    value = env.get(var, "").strip().lower()
    if not value:
        return default
    return value in ("1", "true", "yes", "on")


def settings_dir(env: Mapping[str, str]) -> Path:
    """The settings directory of the first-run app (ADR 0092).

    ``ANALYSIS_SETTINGS_DIR`` where set, else ``$XDG_CONFIG_HOME/work-agent``,
    else ``~/.config/work-agent`` where ``XDG_CONFIG_HOME`` is unset or not
    an absolute path.
    """
    if chosen := env.get(SETTINGS_DIR_VAR):
        return Path(chosen)
    xdg = env.get("XDG_CONFIG_HOME", "")
    base = Path(xdg) if os.path.isabs(xdg) else Path.home() / ".config"
    return base / "work-agent"


def read_settings_env(path: Path) -> dict[str, SettingsLine]:
    """Each key of ``settings.env`` with its value and line number.

    A missing file is no lines. Each line is ``KEY=value`` or empty, ``KEY``
    matches ``ANALYSIS_[A-Z0-9_]+``, and the value is all of the text after
    the first ``=``. The file has no quotes, no ``export``, no ``$``
    expansion and no comments. The split is on ``\\n`` only, so a ``\\r`` or
    a Unicode line separator stays in a line, where the key pattern or the
    value rule refuses it. The caller applies :func:`value_refusal` and
    decides which keys it knows.
    """
    try:
        text = path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        return {}
    except UnicodeDecodeError as exc:
        raise SettingsFileError(f"{path}: is not UTF-8") from exc
    except OSError as exc:
        raise SettingsFileError(f"{path}: cannot be read: {exc.strerror}") from exc
    lines: dict[str, SettingsLine] = {}
    for number, line in enumerate(text.split("\n"), start=1):
        if not line:
            continue
        key, sep, value = line.partition("=")
        if not sep or not _KEY.fullmatch(key):
            raise SettingsFileError(
                f"{path} line {number}: is not KEY=value with a KEY that matches"
                f" {_KEY.pattern}"
            )
        if key in lines:
            raise SettingsFileError(
                f"{path} line {number}: sets {key} again, after line"
                f" {lines[key].number}"
            )
        lines[key] = SettingsLine(number, value)
    return lines


def value_refusal(value: str) -> str | None:
    """Why the value rule refuses ``value``, or ``None`` where it passes.

    **The one value rule of ``settings.env``** (ADR 0092). Each refused form
    gives another reader of the file a different value or a second entry.
    systemd ``EnvironmentFile=`` removes whitespace at the ends, reads ``\\``
    as an escape and a line continuation, and reads a value that starts with
    a quote as quoted text that can span lines. A shell that sources the file
    expands ``$``. The reason names the rule and never repeats the value.
    """
    if len(value) > MAX_VALUE_CHARS:
        return f"is longer than {MAX_VALUE_CHARS:,} characters"
    if not value.isprintable():
        return "holds a character that is not printable"
    if value != value.strip():
        return "has whitespace at an end"
    if "\\" in value or "$" in value:
        return "holds a \\ or a $"
    if value.startswith(("'", '"')):
        return "starts with a quote"
    return None
