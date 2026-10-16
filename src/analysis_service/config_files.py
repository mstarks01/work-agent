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

:func:`private_fd` is the one permission rule, in the manner of OpenSSH. Both
drivers open the settings directory, ``settings.env`` and each secret file
through it. :func:`secret_refusal` is the one rule for the content of a secret
file.
"""

from __future__ import annotations

import os
import re
import stat
import tomllib
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
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


@contextmanager
def private_fd(
    path: Path | str, error: type[Exception], *, secret: bool = False
) -> Iterator[int]:
    """A read-only descriptor of ``path``, a file or a directory, that passes
    the permission rule.

    **The one permission rule** (ADR 0092). The check reads the open
    descriptor and not the path, so a file that replaces ``path`` after the
    open does not pass. It raises ``error``, which names the path and its
    mode, when:

    - the group or other users can write ``path``;
    - ``secret`` is set and other users can read ``path``. Group read stays
      allowed, for a Kubernetes ``fsGroup`` mount;
    - the owner is not the process user or root.

    An ``OSError`` from the open goes to the caller.
    """
    fd = os.open(path, os.O_RDONLY)
    try:
        status = os.fstat(fd)
        mode = stat.S_IMODE(status.st_mode)
        if mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise error(
                f"{path}: mode {mode:04o} lets the group or other users write it"
            )
        if secret and mode & stat.S_IROTH:
            raise error(f"{path}: mode {mode:04o} lets other users read it")
        if status.st_uid not in (os.geteuid(), 0):
            raise error(
                f"{path}: the owner, uid {status.st_uid}, is not the process"
                f" user (uid {os.geteuid()}) or root"
            )
        yield fd
    finally:
        os.close(fd)


def read_private(
    path: Path | str, error: type[Exception], *, secret: bool = False
) -> bytes:
    """The content of the file ``path``, read through :func:`private_fd`."""
    with (
        private_fd(path, error, secret=secret) as fd,
        open(fd, "rb", closefd=False) as file,
    ):
        return file.read()


def read_settings_env(path: Path) -> dict[str, SettingsLine]:
    """Each key of ``settings.env`` with its value and line number.

    A missing file is no lines. Each line is ``KEY=value`` or empty, ``KEY``
    matches ``ANALYSIS_[A-Z0-9_]+``, and the value is all of the text after
    the first ``=``. The file has no quotes, no ``export``, no ``$``
    expansion and no comments. The split is on ``\\n`` only, so a ``\\r`` or
    a Unicode line separator stays in a line, where the key pattern or the
    value rule refuses it. The caller applies :func:`value_refusal` and
    decides which keys it knows.

    The directory and the file each pass :func:`private_fd` where they exist.
    """
    try:
        with private_fd(path.parent, SettingsFileError):
            pass
        text = read_private(path, SettingsFileError).decode("utf-8")
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


def secret_refusal(value: str) -> str | None:
    """Why the secret rule refuses ``value``, or ``None`` where it passes.

    **The one secret rule.** A secret is not empty, is printable, has no
    whitespace at either end and has 4,096 characters or fewer. A space, a
    ``\\`` or a ``$`` inside it passes, because a database password can hold
    them. The reason names the rule and never repeats the value.
    """
    if not value:
        return "is empty"
    if len(value) > MAX_VALUE_CHARS:
        return f"is longer than {MAX_VALUE_CHARS:,} characters"
    if not value.isprintable():
        return "holds a character that is not printable"
    if value != value.strip():
        return "has whitespace at an end"
    return None
