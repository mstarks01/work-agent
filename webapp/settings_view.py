"""What the Settings page shows: each Deployment Setting, its value and where
the value comes from (#1775).

A value comes from one of four sources. The process environment wins, then a
line of ``settings.env`` ("this page"), then the shipped TOML file that the
entry's ``file_key`` names, then the default. A value that ``settings.env``
sets carries the shipped value beside it. The page locates each TOML file with
the loader's reader, and where the loader refuses the path or the file, a row
of that file shows the refusal in place of a value.

The page shows a store row only where a loader reads it now: the store rows
follow the ``reads`` of the selected job store, object store and database.
A vendor row carries its vendor, and a credential row also the modes that
read it, from the ``VENDORS`` rows. The page's script shows a vendor row
where a tier names the vendor and the mode reads the row, with the unsaved
changes applied, so one save sets a tier and its key. An entry marked
``excluded`` never shows.

A row carries the closed set of its setting where the loader holds one, so
the page offers a choice. The data carries the revision of ``settings.env``,
which a save sends back (:mod:`webapp.settings_save`).

No row carries a secret. A secret row carries only whether a key file exists,
and this module never opens one. A value that holds a URL password is
withheld.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from analysis_service.claims import FRAMEWORK_NAMES
from analysis_service.config_files import (
    SETTINGS_FILE,
    SettingsFileError,
    SettingsLine,
    read_settings,
    read_toml,
    settings_dir,
)
from analysis_service.deployment import CONFIG_PATHS, PASS_FLAGS, config_path
from analysis_service.errors import ConfigError
from analysis_service.model_gate import tool_models
from analysis_service.model_tiers import (
    TIER_NAMES,
    charges_env_var_for,
    credentials_env_var_for,
    upstreams_env_var_for,
)
from analysis_service.objects import OBJECT_STORES, object_store_name
from analysis_service.settings_variables import deployment_variables, secret_vars
from analysis_service.store_backends import (
    JOB_STORE_VAR,
    JOB_STORES,
    RETENTION_DAYS_VAR,
    job_store_name,
)
from analysis_service.store_backends import (
    env_variables as store_variables,
)
from analysis_service.store_settings import (
    DATABASE_URL_VAR,
    DATABASE_VARIABLES,
    StoreConfigError,
)
from analysis_service.vendors import VENDOR_NAMES, VENDORS
from webapp.settings_page import IAM_SETUP, SETTINGS, Setting

#: The value of each store setting that this app gives where no source sets
#: it: a first run keeps its jobs in memory.
STORE_DEFAULTS = {JOB_STORE_VAR: "memory"}


def with_store_defaults(env: Mapping[str, str]) -> dict[str, str]:
    """``env`` over :data:`STORE_DEFAULTS`: the settings that this app's store
    is built from.

    **The one reader of the app's store defaults.**
    """
    return {**STORE_DEFAULTS, **env}


def settings_view(
    process_env: Mapping[str, str],
    error: ConfigError | None,
) -> dict[str, Any]:
    """The page's data: the rows, the unknown lines of ``settings.env``, the
    engine's error, the modes of each vendor, the ``iam`` notes and the model
    suggestions of each vendor. The page's script picks the suggestions of a
    tier from the vendor that the unsaved changes choose for it.
    """
    path = settings_dir(process_env) / SETTINGS_FILE
    try:
        revision, lines = read_settings(path)
    except SettingsFileError:
        # The startup refused the file for the same reason, and the banner
        # names it. A save needs a revision, so the page cannot save.
        revision, lines = None, {}
    known = set(deployment_variables())
    page = {var: line for var, line in lines.items() if var in known}
    merged = {
        **{var: line.value for var, line in page.items()},
        **{var: process_env[var] for var in known & process_env.keys()},
    }
    files = _Files(merged)
    values = {
        key: _value(setting, process_env, page, files)
        for key, setting in SETTINGS.items()
    }
    owners = _vendor_rows()
    shown = owners.keys() | _store_rows(merged)
    hidden = _vendor_variables() | set(store_variables())
    restart = set(store_variables())
    secrets = secret_vars()
    rows = []
    for key, setting in SETTINGS.items():
        var = setting.variable
        if setting.mark == "excluded" or (var in hidden and var not in shown):
            continue
        row = values[key] | {
            "key": key,
            "group": setting.group,
            "label": setting.label,
            "mark": setting.mark,
            "reason": setting.reason,
            "restart": var in restart,
            **_CONTROLS.get(key, {}),
            **(owners.get(var, {}) if var is not None else {}),
        }
        if var in secrets:
            row |= {"secret": True, "key_saved": _key_saved(row.pop("value"))}
            row.pop("shipped", None)
        rows.append(row)
    return {
        "error": None if error is None else str(error),
        "settings_file": str(path),
        "revision": revision,
        "unknown": [
            {"key": var, "line": line.number}
            for var, line in lines.items()
            if var not in known
        ],
        "rows": rows,
        "modes": {
            vendor: [mode.value for mode in VENDORS[vendor].credential_modes]
            for vendor in VENDOR_NAMES
        },
        "iam": [
            {"vendor": vendor, "note": setup.note, "command": setup.command}
            for vendor in VENDOR_NAMES
            if (setup := IAM_SETUP[vendor]) is not None
        ],
        "models": {vendor: list(tool_models(vendor)) for vendor in VENDOR_NAMES},
    }


def _controls() -> dict[str, dict[str, Any]]:
    """The closed set of each Deployment Setting whose loader holds one, from
    that loader's table, so the page offers a choice in place of free text.
    ``multiple`` marks a list of names separated by commas."""
    flag = ("true", "false")
    choices: dict[str, tuple[str, ...]] = {
        **{f"tiers.{tier}.vendor": VENDOR_NAMES for tier in TIER_NAMES},
        **{
            f"credentials.{vendor}.mode": tuple(VENDORS[vendor].credential_modes)
            for vendor in VENDOR_NAMES
        },
        **{
            f"charges.{vendor}": tuple(VENDORS[vendor].charge_modes)
            for vendor in VENDOR_NAMES
        },
        **{
            key: flag
            for key, setting in SETTINGS.items()
            if setting.variable in PASS_FLAGS
        },
        "certification.require_certified": flag,
        "store.job_store": tuple(JOB_STORES),
        "store.object_store": tuple(OBJECT_STORES),
    }
    return {
        **{
            key: {"options": list(options)}
            for key, options in choices.items()
            if options
        },
        "frameworks.carried": {"options": list(FRAMEWORK_NAMES), "multiple": True},
    }


_CONTROLS = _controls()


class _Files:
    """The shipped TOML files, read once each, at the paths ``merged`` names."""

    def __init__(self, merged: Mapping[str, str]) -> None:
        self._merged = merged
        self._read: dict[str, tuple[Path, dict[str, Any]] | ConfigError] = {}

    def lookup(self, field: str, keys: tuple[str, ...]) -> tuple[Path, object]:
        """The file of ``field`` and the value at ``keys`` in it, or ``None``
        where the file does not hold one. Raises the loader's
        :class:`ConfigError` where the loader refuses the path or the file."""
        if field not in self._read:
            var, default = CONFIG_PATHS[field]
            try:
                path = config_path(self._merged, var, default)
                self._read[field] = (path, read_toml(path, ConfigError))
            except ConfigError as exc:
                self._read[field] = exc
        read = self._read[field]
        if isinstance(read, ConfigError):
            raise read
        path, found = read
        value: object = found
        for key in keys:
            value = value.get(key) if isinstance(value, dict) else None
        return path, value


def _value(
    setting: Setting,
    process_env: Mapping[str, str],
    page: Mapping[str, SettingsLine],
    files: _Files,
) -> dict[str, Any]:
    """The value of one setting and its source, with the shipped value where
    ``settings.env`` sets it."""
    var = setting.variable
    if var is not None and var in process_env:
        return {
            "value": _withheld(process_env[var]),
            "source": "process",
            "variable": var,
        }
    base = _base(setting, files)
    if var is not None and var in page:
        return {
            "value": _withheld(page[var].value),
            "source": "page",
            "shipped": base["value"],
        }
    return base


def _base(setting: Setting, files: _Files) -> dict:
    """The value of a setting with no override: from its TOML file, else the
    default, else ``None``. Where the loader refuses the file, the value is the
    refusal."""
    if setting.file_key is not None:
        try:
            path, value = files.lookup(*setting.file_key)
        except ConfigError as exc:
            return {"value": None, "source": "refused", "refusal": str(exc)}
        if value is not None:
            return {"value": _text(value), "source": "file", "file": str(path)}
    var = setting.variable
    default: str | None = None
    if var in PASS_FLAGS:
        default = _text(PASS_FLAGS[var])
    elif var is not None:
        default = STORE_DEFAULTS.get(var)
    return {"value": default, "source": "default"}


def _text(value: object) -> str:
    """A TOML value in the form of its override."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    return str(value)


def _withheld(value: str) -> str | None:
    """``value``, or ``None`` where it holds a URL password, in the user part
    or as a query key, which the page never shows. A value that does not parse
    as a URL is withheld too."""
    try:
        parts = urlsplit(value)
        password = parts.password
    except ValueError:
        return None
    query = parse_qs(parts.query, keep_blank_values=True)
    in_query = any("password" in key.lower() for key in query)
    return None if password is not None or in_query else value


def _key_saved(path: str | None) -> bool:
    """Whether the secret variable names a file. The file is never opened."""
    return path is not None and Path(path).is_file()


def _vendor_variables() -> set[str]:
    """Every variable that belongs to one vendor."""
    return {
        var
        for vendor in VENDOR_NAMES
        for var in (
            credentials_env_var_for(vendor),
            charges_env_var_for(vendor),
            upstreams_env_var_for(vendor),
            *(
                var
                for mode in VENDORS[vendor].credential_modes
                for var in VENDORS[vendor].required_env_vars(mode)
            ),
        )
    }


def _vendor_rows() -> dict[str, dict[str, Any]]:
    """Each vendor variable that a vendor can read, with the vendor, and for a
    credential variable the modes that read it: a mode where the vendor allows
    more than one, a charge mode likewise, the upstreams where the vendor has
    one to pin, and the credentials of each mode."""
    rows: dict[str, dict[str, Any]] = {}
    for vendor in VENDOR_NAMES:
        row = VENDORS[vendor]
        if len(row.credential_modes) > 1:
            rows[credentials_env_var_for(vendor)] = {"vendor": vendor}
        if len(row.charge_modes) > 1:
            rows[charges_env_var_for(vendor)] = {"vendor": vendor}
        if row.upstream_pin is not None:
            rows[upstreams_env_var_for(vendor)] = {"vendor": vendor}
        for mode in row.credential_modes:
            for var in row.required_env_vars(mode):
                owner = rows.setdefault(var, {"vendor": vendor, "modes": []})
                owner["modes"].append(mode.value)
    return rows


def _store_rows(merged: Mapping[str, str]) -> set[str]:
    """The store variables that the selected job store, object store and
    database read."""
    env = with_store_defaults(merged)
    shown = {JOB_STORE_VAR, RETENTION_DAYS_VAR}
    try:
        job_store = JOB_STORES[job_store_name(env)]
    except StoreConfigError:
        return shown
    shown.update(job_store.reads)
    if job_store.objects:
        try:
            name = object_store_name(env)
        except StoreConfigError:
            pass
        else:
            shown.update(OBJECT_STORES[name].reads)
    if DATABASE_URL_VAR in job_store.reads:
        try:
            from analysis_service.sql_store import database_reads
        except ModuleNotFoundError:
            shown.update(DATABASE_VARIABLES)
        else:
            shown.update(database_reads(env))
    return shown
