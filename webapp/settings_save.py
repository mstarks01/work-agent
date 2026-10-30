"""The save of the Settings page: the body, the refusals and the new lines
(ADR 0092, ADR 0093).

A save is a JSON object of at most :data:`MAX_SAVE_BYTES`::

    {"revision": "<SHA-256 of settings.env>",
     "changes": {"tiers.strong.model": "<value>", "store.retention_days": null},
     "remove": ["ANALYSIS_OLD_NAME"]}

``changes`` names each value by its **Deployment Setting** key. A ``null``
value is a reset: the save removes the line, and the shipped value applies.
``remove`` names the lines of ``settings.env`` that this version does not
read. A refusal names the setting and the rule, and never the value.

The value of a key setting is the key text. The save writes it to a new
key file of its variable (:func:`_write_keys`) and sets the variable to that
path. A save removes the key line of each vendor that no tier uses
(:func:`_unused_keys`), and then deletes each key file that no variable names
(:func:`_unused_key_files`).

:func:`apply_save` is the one save operation. It runs each step of a save in
the order that makes the save safe, and it gives one :data:`SaveOutcome`.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from secrets import token_hex
from typing import Protocol, TypeVar

from analysis_service.config_files import (
    SettingsFileError,
    SettingsLine,
    create_private,
    read_settings,
    refused_var,
    secret_refusal,
    value_refusal,
    write_settings_env,
)
from analysis_service.errors import ConfigError
from analysis_service.model_gate import NoThinkingLevel
from analysis_service.model_tiers import ModelTierConfig
from analysis_service.settings_variables import (
    deployment_variables,
    merged_env,
    secret_vars,
)
from analysis_service.store_backends import build_store, retention_days
from analysis_service.store_backends import env_variables as store_variables
from analysis_service.store_settings import StoreConfigError
from analysis_service.vendors import VENDORS
from webapp.settings_page import CHANGED_SAMPLING, SETTINGS, sampling_key
from webapp.settings_view import with_store_defaults

logger = logging.getLogger("webapp")

#: The largest save body that the server reads.
MAX_SAVE_BYTES = 64 * 1024

#: The name of a :func:`_new_key_file`: a stem, a dot and 16 hexadecimal
#: characters from ``token_hex(8)``.
_KEY_FILE_NAME = re.compile(r"(?P<stem>[a-z0-9_]+)\.[0-9a-f]{16}")


class SaveShapeError(ValueError):
    """A save body that is not the object this module reads."""


@dataclass(frozen=True)
class Save:
    """One save: the revision the page loaded, the changes and the removals."""

    revision: str
    changes: dict[str, str | None]
    remove: tuple[str, ...]


@dataclass(frozen=True)
class Refusal:
    """Why the save refuses one key. ``key`` is ``None`` for a refusal of the
    whole save, such as a loader's message."""

    key: str | None
    message: str


class Built(Protocol):
    """What a build of the test Deployment gives: the tiers that it read, and
    the error that stopped it, or ``None``."""

    @property
    def tiers(self) -> ModelTierConfig | None: ...

    @property
    def error(self) -> ConfigError | None: ...


BuiltT = TypeVar("BuiltT", bound=Built)


@dataclass(frozen=True)
class Saved:
    """``settings.env`` holds the save, and the swap ran. ``env`` is the
    mapping that the saved file gives."""

    env: Mapping[str, str]


@dataclass(frozen=True)
class SaveConflict:
    """``settings.env`` changed after the page loaded it: before the save or
    while the check ran. The save changed nothing."""


@dataclass(frozen=True)
class SaveRefused:
    """A rule, a loader or the store check refused the save. The save changed
    nothing."""

    refusals: tuple[Refusal, ...]


#: Each outcome of :func:`apply_save`. An ``OSError`` before the commit is no
#: outcome: it raises, and the save changed nothing.
SaveOutcome = Saved | SaveConflict | SaveRefused


def parse_save(body: object) -> Save:
    """The save that ``body``, a parsed JSON value, holds, or raise
    :class:`SaveShapeError`."""
    if not isinstance(body, dict):
        raise SaveShapeError("the body is not an object")
    changes = parse_changes(body)
    revision = body.get("revision")
    remove = body.get("remove", [])
    if not isinstance(revision, str):
        raise SaveShapeError("revision is not text")
    if not isinstance(remove, list) or not all(isinstance(var, str) for var in remove):
        raise SaveShapeError("remove is not a list of text")
    return Save(revision, changes, tuple(dict.fromkeys(remove)))


def parse_changes(body: object) -> dict[str, str | None]:
    """The ``changes`` object of ``body``, a parsed JSON value, or raise
    :class:`SaveShapeError`. A save and the store row request of the page
    both send it."""
    if not isinstance(body, dict):
        raise SaveShapeError("the body is not an object")
    changes = body.get("changes", {})
    if not isinstance(changes, dict) or not all(
        isinstance(value, str | None) for value in changes.values()
    ):
        raise SaveShapeError("changes is not an object of text or null values")
    return changes


def _refusals(
    save: Save, process_env: Mapping[str, str], lines: Mapping[str, SettingsLine]
) -> list[Refusal]:
    """Each refusal of the keys of ``save``, in the order of the body.

    A change must name an entry of
    :data:`~webapp.settings_page.SETTINGS` marked ``changes``, whose variable
    is not set in the process environment, and its value must pass
    :func:`~analysis_service.config_files.value_refusal`, or
    :func:`~analysis_service.config_files.secret_refusal` for a key. A
    removal must name a line of ``lines`` that this version does not read.
    A refusal names a key the caller made up by its position, never by the
    key.
    """
    found = []
    secrets = secret_vars()
    for at, (key, value) in enumerate(save.changes.items()):
        setting = SETTINGS.get(key)
        if setting is None:
            found.append(
                Refusal(
                    None, f"changes key {at} is not a setting that this page changes"
                )
            )
            continue
        if setting.mark != "changes" or setting.variable is None:
            found.append(Refusal(key, f"{key} is not a setting that this page changes"))
            continue
        var = setting.variable
        rule = secret_refusal if var in secrets else value_refusal
        if var in process_env:
            message = (
                f"{setting.label}: the process environment sets {var}; unset it"
                " to change this here"
            )
        elif value is not None and (refusal := rule(value)):
            message = f"{setting.label} {refusal}"
        else:
            continue
        found.append(Refusal(key, message))
    known = set(deployment_variables())
    found += [
        Refusal(
            var, f"{var} is not a line of settings.env that this version does not read"
        )
        if var in known
        else Refusal(
            None,
            f"remove[{at}] is not a line of settings.env that this version does not"
            " read",
        )
        for at, var in enumerate(save.remove)
        if var in known or var not in lines
    ]
    return found


def _saved_keys(save: Save) -> dict[str, str]:
    """The key text of each key setting that ``save`` sets, by its variable."""
    secrets = secret_vars()
    return {
        var: value
        for key, value in save.changes.items()
        if (var := SETTINGS[key].variable) in secrets and value is not None
    }


def _key_stem(var: str) -> str:
    return refused_var(var).removeprefix("ANALYSIS_").lower()


def _new_key_file(directory: Path, var: str) -> Path:
    """A new absolute path for a key file of the key variable ``var`` in
    ``directory``: its name without ``ANALYSIS_`` and ``_FILE``, in lower
    case, a dot and a random token, such as ``anthropic_api_key.3f9c0a1b2d4e5f60``.
    The name holds no part of the key."""
    return directory.absolute() / f"{_key_stem(var)}.{token_hex(8)}"


def _write_keys(directory: Path, keys: Mapping[str, str]) -> dict[str, Path]:
    """Write each key text of ``keys`` to a new :func:`_new_key_file` by
    :func:`~analysis_service.config_files.create_private`, and give each path
    by its variable. Where a write fails, it deletes each file it wrote and
    raises."""
    written: dict[str, Path] = {}
    try:
        for var, text in keys.items():
            path = _new_key_file(directory, var)
            create_private(path, text.encode())
            written[var] = path
    except BaseException:
        _delete_files(written.values())
        raise
    return written


def _delete_files(paths: Iterable[Path]) -> None:
    """Delete each file of ``paths`` that exists."""
    for path in paths:
        path.unlink(missing_ok=True)


def _saved_lines(
    lines: Mapping[str, SettingsLine],
    save: Save,
    written: Mapping[str, Path],
    drop: Collection[str] = (),
) -> dict[str, SettingsLine]:
    """The lines of ``settings.env`` after ``save``, without the variables of
    ``drop``, numbered as the new file numbers them. A line keeps its place,
    and a new line goes at the end. A key setting's line holds the path that
    ``written``, from :func:`_write_keys`, gives its variable."""
    secrets = secret_vars()
    values = {var: line.value for var, line in lines.items() if var not in save.remove}
    for key, value in save.changes.items():
        var = SETTINGS[key].variable
        if var is None:
            raise ValueError(f"{key} has no variable; _refusals() refuses it")
        if value is None:
            values.pop(var, None)
        else:
            values[var] = str(written[var]) if var in secrets else value
    for var in drop:
        values.pop(var, None)
    return {
        var: SettingsLine(number, value)
        for number, (var, value) in enumerate(values.items(), start=1)
    }


def restart_keys(running: Mapping[str, str], saved: Mapping[str, str]) -> list[str]:
    """The Deployment Setting key of each store variable whose value in
    ``saved`` differs from the one that the running store was built from."""
    by_variable = {setting.variable: key for key, setting in SETTINGS.items()}
    return [
        by_variable[var]
        for var in store_variables()
        if running.get(var) != saved.get(var)
    ]


def _unused_keys(tiers: ModelTierConfig | None) -> frozenset[str]:
    """The key variables of each vendor that no tier of ``tiers`` uses.

    The page's script reads the same rule in ``deletedKeys`` to warn before
    a save, and a test in ``tests/test_browser.py`` checks the two against
    each other.
    """
    used = set() if tiers is None else {tier.vendor for tier in tiers.tiers.values()}
    return frozenset(
        var
        for name, vendor in VENDORS.items()
        if name not in used
        for var in vendor.all_secret_env_vars()
    )


def _unused_key_files(directory: Path, env: Mapping[str, str]) -> list[Path]:
    """Each file in ``directory`` whose name has the form of a
    :func:`_new_key_file` and that no key variable of ``env`` names."""
    secrets = secret_vars()
    stems = {_key_stem(var) for var in secrets}
    named = {Path(env[var]).resolve() for var in secrets if env.get(var)}
    return sorted(
        path
        for path in directory.iterdir()
        if (match := _KEY_FILE_NAME.fullmatch(path.name))
        and match["stem"] in stems
        and path.is_file()
        and path.resolve() not in named
    )


def _check(
    env: Mapping[str, str], build: Callable[[Mapping[str, str]], BuiltT]
) -> tuple[BuiltT, list[Refusal]]:
    """The check of the mapping that a save gives: the value that ``build``
    makes from it, and a refusal for each loader that refuses it. A thinking
    level on a model that takes none is refused on its own row.

    ``build`` builds the test Deployment and its runner, and the store check
    runs :func:`~analysis_service.store_backends.build_store` with its
    first-run step and the retention rule, as the startup does. So a save can
    name a SQLite file that does not exist yet (ADR 0110). No step calls a
    provider.
    """
    built = build(env)
    found = [] if built.error is None else [_refusal_of(built.error)]
    try:
        build_store(with_store_defaults(env), first_run=True)
        retention_days(env)
    except (StoreConfigError, ImportError) as exc:
        found.append(Refusal(None, str(exc)))
    return built, found


def _refusal_of(error: ConfigError) -> Refusal:
    """The refusal of a build error, on the row that it names where one does."""
    key = (
        sampling_key(error.tier, CHANGED_SAMPLING)
        if isinstance(error, NoThinkingLevel)
        else None
    )
    return Refusal(key, str(error))


async def apply_save(
    lock: asyncio.Lock,
    path: Path,
    save: Save,
    process_env: Mapping[str, str],
    build: Callable[[Mapping[str, str]], BuiltT],
    swap: Callable[[BuiltT], None],
) -> SaveOutcome:
    """Check ``save`` against the ``settings.env`` at ``path``, write it, and
    give the value that the check built to ``swap`` (ADR 0092, ADR 0093).

    One save at a time holds ``lock`` from the revision check to the swap.
    The revision must be the one that the page loaded, and the value rule,
    the secret rule, the test Deployment and the store check must pass. The
    save writes each new key to a new key file first, and ``build`` builds
    the test Deployment on those paths, in a thread. The replace of
    ``settings.env`` is the one step that commits the save: before it, each
    outcome and each exception deletes the new key files, so the old
    ``settings.env`` and its key files stay in force. After it, ``swap``
    takes the value that the check built, and the save then deletes each key
    file that no variable names. A failure of that deletion only logs.

    A :class:`~analysis_service.config_files.SettingsFileError` refuses the
    whole save.
    """
    async with lock:
        try:
            committed = await _commit(path, save, process_env, build)
        except SettingsFileError as exc:
            return SaveRefused((Refusal(None, str(exc)),))
        if isinstance(committed, SaveConflict | SaveRefused):
            return committed
        built, env = committed
        swap(built)
        try:
            _delete_files(_unused_key_files(path.parent, env))
        except OSError as exc:
            # The next save deletes each key file that this one left.
            logger.warning("a key file that no variable names stays: %s", exc)
    return Saved(env)


async def _commit(
    path: Path,
    save: Save,
    process_env: Mapping[str, str],
    build: Callable[[Mapping[str, str]], BuiltT],
) -> tuple[BuiltT, Mapping[str, str]] | SaveConflict | SaveRefused:
    """The steps of :func:`apply_save` up to the replace of ``settings.env``:
    the value that the check built and the saved mapping, or the outcome
    that stopped the save."""
    revision, lines = read_settings(path)
    if save.revision != revision:
        return SaveConflict()
    if found := _refusals(save, process_env, lines):
        return SaveRefused(tuple(found))
    written: dict[str, Path] = {}
    committed = False
    try:
        written = _write_keys(path.parent, _saved_keys(save))
        env = merged_env(process_env, _saved_lines(lines, save, written), path)
        built, found = await asyncio.to_thread(_check, env, build)
        if found:
            return SaveRefused(tuple(found))
        # A hand edit while the check ran is not written over.
        if read_settings(path).revision != revision:
            return SaveConflict()
        # The tiers that the check built name the vendors whose key lines the
        # save drops, so the lines and the mapping are made again after it.
        saved = _saved_lines(lines, save, written, _unused_keys(built.tiers))
        env = merged_env(process_env, saved, path)
        write_settings_env(path, {var: line.value for var, line in saved.items()})
        committed = True
    finally:
        if not committed:
            _delete_files(written.values())
    return built, env
