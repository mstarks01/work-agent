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
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from analysis_service.config_files import SettingsLine, value_refusal
from analysis_service.settings_variables import deployment_variables, secret_vars
from analysis_service.store_backends import env_variables as store_variables
from webapp.settings_page import SETTINGS

#: The largest save body that the server reads.
MAX_SAVE_BYTES = 64 * 1024


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


def parse_save(body: object) -> Save:
    """The save that ``body``, a parsed JSON value, holds, or raise
    :class:`SaveShapeError`."""
    if not isinstance(body, dict):
        raise SaveShapeError("the body is not an object")
    revision = body.get("revision")
    changes = body.get("changes", {})
    remove = body.get("remove", [])
    if not isinstance(revision, str):
        raise SaveShapeError("revision is not text")
    if not isinstance(changes, dict) or not all(
        isinstance(value, str | None) for value in changes.values()
    ):
        raise SaveShapeError("changes is not an object of text or null values")
    if not isinstance(remove, list) or not all(isinstance(var, str) for var in remove):
        raise SaveShapeError("remove is not a list of text")
    return Save(revision, changes, tuple(dict.fromkeys(remove)))


def refusals(
    save: Save, process_env: Mapping[str, str], lines: Mapping[str, SettingsLine]
) -> list[Refusal]:
    """Each refusal of the keys of ``save``, in the order of the body.

    A change must name an entry of
    :data:`~webapp.settings_page.SETTINGS` marked ``changes``, whose variable
    names no secret file and is not set in the process environment, and its
    value must pass :func:`~analysis_service.config_files.value_refusal`. A
    removal must name a line of ``lines`` that this version does not read.
    """
    found = []
    secrets = secret_vars()
    for key, value in save.changes.items():
        setting = SETTINGS.get(key)
        if setting is None or setting.mark != "changes" or setting.variable is None:
            found.append(Refusal(key, f"{key} is not a setting that this page changes"))
            continue
        var = setting.variable
        if var in secrets:
            message = f"{setting.label} is a key, and this page saves no key"
        elif var in process_env:
            message = (
                f"{setting.label}: the process environment sets {var}; unset it"
                " to change this here"
            )
        elif value is not None and (refusal := value_refusal(value)):
            message = f"{setting.label} {refusal}"
        else:
            continue
        found.append(Refusal(key, message))
    known = set(deployment_variables())
    found += [
        Refusal(
            var, f"{var} is not a line of settings.env that this version does not read"
        )
        for var in save.remove
        if var in known or var not in lines
    ]
    return found


def saved_lines(
    lines: Mapping[str, SettingsLine], save: Save
) -> dict[str, SettingsLine]:
    """The lines of ``settings.env`` after ``save``, numbered as the new file
    numbers them. A line keeps its place, and a new line goes at the end."""
    values = {var: line.value for var, line in lines.items() if var not in save.remove}
    for key, value in save.changes.items():
        var = SETTINGS[key].variable
        assert var is not None, key
        if value is None:
            values.pop(var, None)
        else:
            values[var] = value
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
