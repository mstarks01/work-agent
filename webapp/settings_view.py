"""What the Settings page shows: each Deployment Setting, its value and where
the value comes from (#1775).

A value comes from one of four sources. The process environment wins, then a
line of ``settings.env`` ("this page"), then the shipped TOML file that the
entry's ``file_key`` names, then the default. A value that ``settings.env``
sets carries the shipped value beside it. The page locates each TOML file with
the loader's reader, and where the loader refuses the path or the file, a row
of that file shows the refusal in place of a value.

The page shows a store row only where a loader reads it: the store rows
follow the ``reads`` of the selected job store, object store and database.
The data carries every store row and the keys of the shown ones
(``store_shown``). After a store edit, the page's script sends its unsaved
changes, and :func:`store_shown` gives the keys again, so one save sets a job
store and the settings that it reads. A vendor row carries its vendor, and a credential row also the modes that
read it, from the ``VENDORS`` rows. The page's script shows a vendor row
where a tier names the vendor and the mode reads the row, with the unsaved
changes applied, so one save sets a tier and its key. An entry marked
``excluded`` never shows.

A row carries the closed set of its setting where the loader holds one, so
the page offers a choice, and the example of a free-text setting from
:data:`~webapp.settings_page.EXAMPLES`. The data carries the revision of
``settings.env``, which a save sends back (:mod:`webapp.settings_save`).

No row carries a secret. A secret row carries only whether a key file exists,
and this module never opens one. A value that holds a URL password is
withheld.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal, get_args

from analysis_service import resilience
from analysis_service.claims import FRAMEWORK_NAMES
from analysis_service.config_files import (
    SETTINGS_FILE,
    SettingsFileError,
    SettingsLine,
    data_dir,
    read_settings,
    read_toml,
    settings_dir,
)
from analysis_service.deployment import (
    CONFIG_PATHS,
    PASS_FLAGS,
    ConfigPathName,
    config_path,
)
from analysis_service.errors import ConfigError
from analysis_service.model_gate import no_thinking_models, tool_models
from analysis_service.model_tiers import (
    TIER_NAMES,
    charges_env_var_for,
    credentials_env_var_for,
    upstreams_env_var_for,
)
from analysis_service.objects import OBJECT_ROOT_VAR, OBJECT_STORE_VAR, object_reads
from analysis_service.sampling import ReasoningEffort
from analysis_service.settings_variables import deployment_variables, secret_vars
from analysis_service.store_backends import (
    CLOSED_SETTINGS,
    JOB_STORE_VAR,
    JOB_STORES,
    RETENTION_DAYS_VAR,
    JobStore,
    build_store,
    job_store_name,
)
from analysis_service.store_backends import (
    env_variables as store_variables,
)
from analysis_service.store_settings import (
    DATABASE_URL_VAR,
    DATABASE_VARIABLES,
    StoreConfigError,
    holds_password,
)
from analysis_service.vendors import VENDOR_NAMES, VENDORS
from webapp.settings_page import (
    CHANGED_SAMPLING,
    EXAMPLES,
    IAM_SETUP,
    SETTINGS,
    Setting,
    sampling_key,
    tier_setting_key,
)

#: Where a row's value comes from. ``refused`` is a file the loader refuses,
#: which a row shows in place of a value. ``settings.js`` shows each one.
ValueOrigin = Literal["process", "page", "file", "refused", "default"]

#: The numbers a numeric row takes. ``settings.js`` checks each one.
Numeric = Literal["integer", "decimal"]


def store_defaults(env: Mapping[str, str]) -> dict[str, str]:
    """The value of each store setting that this app gives where ``env`` sets
    none (ADR 0111).

    A first run keeps its jobs in a ``sql`` store on SQLite, with a
    ``filesystem`` object root, both in the :func:`data_dir`. A default
    applies only where ``env`` selects the row that reads it, so the app never
    gives a setting that the selected rows refuse.

    **The one reader of the app's store defaults.**
    """
    directory = data_dir(env)
    chosen = dict(env)
    chosen.setdefault(JOB_STORE_VAR, "sql")
    if chosen[JOB_STORE_VAR] == "sql":
        chosen.setdefault(DATABASE_URL_VAR, _default_database(directory))
        chosen.setdefault(OBJECT_STORE_VAR, "filesystem")
        if chosen[OBJECT_STORE_VAR] == "filesystem":
            chosen.setdefault(OBJECT_ROOT_VAR, str(_default_root(directory)))
    return {var: value for var, value in chosen.items() if var not in env}


def _default_database(directory: Path) -> str:
    return f"sqlite+aiosqlite:///{directory / 'jobs.db'}"


def _default_root(directory: Path) -> Path:
    return directory / "objects"


def with_store_defaults(env: Mapping[str, str]) -> dict[str, str]:
    """``env`` over :func:`store_defaults`: the settings that this app's store
    is built from."""
    return {**store_defaults(env), **env}


def build_app_store(env: Mapping[str, str]) -> JobStore:
    """The job store of this app, from ``env`` over its defaults.

    **The one builder of this app's store**, at its startup and in the check
    of a Settings save. Where the store uses the default data directory or
    object root, it makes each one that does not exist with mode ``0700``,
    and a directory that it cannot make stops the startup with a
    :class:`StoreConfigError`. Then it builds the store with its first-run
    step (ADR 0110, ADR 0111).
    """
    store_env = with_store_defaults(env)
    directory = data_dir(env)
    root = _default_root(directory)
    in_root = store_env.get(OBJECT_ROOT_VAR) == str(root)
    try:
        if in_root or store_env.get(DATABASE_URL_VAR) == _default_database(directory):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if in_root:
            root.mkdir(mode=0o700, exist_ok=True)
    except OSError as exc:
        raise StoreConfigError(
            f"the app cannot make its data directory {directory}: {exc.strerror}"
        ) from None
    return build_store(store_env, first_run=True)


def settings_view(
    process_env: Mapping[str, str],
    error: ConfigError | None,
) -> dict[str, Any]:
    """The page's data: the rows, the keys of the shown store rows
    (``store_shown``), the unknown lines of ``settings.env``, the engine's error, the modes of each vendor, the keys of the rows that choose
    a tier's vendor (``vendor_rows``), the ``iam`` notes and the model
    suggestions of each vendor. The page's script picks the suggestions of a
    tier from the vendor that the unsaved changes choose for it.
    """
    path = settings_dir(process_env) / SETTINGS_FILE
    revision, lines = _read_page(path)
    page = _known(lines)
    merged = _merged(process_env, page, {})
    files = _Files(merged)
    values = {
        key: _value(setting, process_env, page, files, store_defaults(merged))
        for key, setting in SETTINGS.items()
    }
    owners = _vendor_rows()
    unowned = _vendor_variables() - owners.keys()
    stores = set(store_variables())
    secrets = secret_vars()
    rows = []
    for key, setting in SETTINGS.items():
        var = setting.variable
        if setting.mark == "excluded" or var in unowned:
            continue
        row = values[key] | {
            "key": key,
            "group": setting.group,
            "label": setting.label,
            "mark": setting.mark,
            "reason": setting.reason,
            "note": setting.note,
            # A store setting decides which store rows show, and it takes
            # effect after a restart.
            "store": var in stores,
            "example": EXAMPLES.get(key),
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
            if var not in page
        ],
        "rows": rows,
        "store_shown": _store_keys(merged),
        "modes": {
            vendor: [mode.value for mode in VENDORS[vendor].credential_modes]
            for vendor in VENDOR_NAMES
        },
        "iam": [
            {"vendor": vendor, "note": setup.note, "command": setup.command}
            for vendor in VENDOR_NAMES
            if (setup := IAM_SETUP[vendor]) is not None
        ],
        "vendor_rows": [tier_setting_key(tier, "vendor") for tier in TIER_NAMES],
        "models": {vendor: list(tool_models(vendor)) for vendor in VENDOR_NAMES},
        "no_thinking": {
            vendor: list(no_thinking_models(vendor)) for vendor in VENDOR_NAMES
        },
    }


def store_shown(
    process_env: Mapping[str, str], changes: Mapping[str, str | None]
) -> list[str]:
    """The keys of the store rows that the page shows with ``changes``, the
    unsaved changes of the page, applied over ``settings.env``.

    Only a change of a store setting that holds no secret counts, and the
    process environment still wins over it. A ``null`` change unsets the
    setting.
    """
    path = settings_dir(process_env) / SETTINGS_FILE
    _, lines = _read_page(path)
    return _store_keys(_merged(process_env, _known(lines), changes))


def _read_page(path: Path) -> tuple[str | None, dict[str, SettingsLine]]:
    """The revision and the lines of ``settings.env``, or no revision and no
    lines where the file is refused."""
    try:
        return read_settings(path)
    except SettingsFileError:
        # The startup refused the file for the same reason, and the banner
        # names it. A save needs a revision, so the page cannot save.
        return None, {}


def _known(lines: Mapping[str, SettingsLine]) -> dict[str, SettingsLine]:
    """The lines of a variable that a Deployment Setting loader reads."""
    known = set(deployment_variables())
    return {var: line for var, line in lines.items() if var in known}


def _merged(
    process_env: Mapping[str, str],
    page: Mapping[str, SettingsLine],
    changes: Mapping[str, str | None],
) -> dict[str, str]:
    """The settings that the loaders read: the lines of ``page``, then the
    store ``changes`` that hold no secret, then the process environment."""
    merged = {var: line.value for var, line in page.items()}
    stores = set(store_variables()) - secret_vars()
    for key, value in changes.items():
        setting = SETTINGS.get(key)
        if setting is None or setting.variable not in stores:
            continue
        if value is None:
            merged.pop(setting.variable, None)
        else:
            merged[setting.variable] = value
    known = set(deployment_variables())
    return merged | {var: process_env[var] for var in known & process_env.keys()}


def _store_keys(merged: Mapping[str, str]) -> list[str]:
    """The keys of the store rows that the settings in ``merged`` read."""
    shown = _store_rows(merged)
    return [key for key, setting in SETTINGS.items() if setting.variable in shown]


def _numeric(parser: Callable[[str], float]) -> Numeric:
    return "integer" if parser is int else "decimal"


def _controls() -> dict[str, dict[str, Any]]:
    """The closed set of each Deployment Setting whose loader holds one, from
    that loader's table, so the page offers a choice in place of free text.
    ``multiple`` marks a list of names separated by commas. A tier's model row
    carries the key of its vendor row, and a tier's thinking row its tier and
    the keys of both, so the page parses no key."""
    flag = ("true", "false")
    choices: dict[str, tuple[str, ...]] = {
        **{tier_setting_key(tier, "vendor"): VENDOR_NAMES for tier in TIER_NAMES},
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
        **{
            key: CLOSED_SETTINGS[setting.variable]
            for key, setting in SETTINGS.items()
            if setting.variable in CLOSED_SETTINGS
        },
    }
    numeric: dict[str, dict[str, Numeric]] = {
        f"resilience.{field}": {"numeric": _numeric(parser)}
        for field, (_, parser) in resilience.ENV_OVERRIDES.items()
    }
    numeric["store.retention_days"] = {"numeric": "integer"}
    return {
        **numeric,
        **{
            key: {"options": list(options)}
            for key, options in choices.items()
            if options
        },
        "frameworks.carried": {"options": list(FRAMEWORK_NAMES), "multiple": True},
        **{
            tier_setting_key(tier, "model"): {
                "vendor_key": tier_setting_key(tier, "vendor")
            }
            for tier in TIER_NAMES
        },
        **{
            sampling_key(tier, CHANGED_SAMPLING): {
                "options": list(get_args(ReasoningEffort)),
                "unset": "The model's own level",
                "tier": tier,
                "vendor_key": tier_setting_key(tier, "vendor"),
                "model_key": tier_setting_key(tier, "model"),
            }
            for tier in TIER_NAMES
        },
    }


_CONTROLS = _controls()


class _Files:
    """The shipped TOML files, read once each, at the paths ``merged`` names."""

    def __init__(self, merged: Mapping[str, str]) -> None:
        self._merged = merged
        self._read: dict[ConfigPathName, tuple[Path, dict[str, Any]] | ConfigError] = {}

    def lookup(
        self, field: ConfigPathName, keys: tuple[str, ...]
    ) -> tuple[Path, object]:
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
    defaults: Mapping[str, str],
) -> dict[str, Any]:
    """The value of one setting and its source, with the shipped value where
    ``settings.env`` sets it. ``defaults`` are the store defaults of the
    settings as merged."""
    var = setting.variable
    if var is not None and var in process_env:
        return _sourced("process", _withheld(process_env[var]), variable=var)
    base = _base(setting, files, defaults)
    if var is not None and var in page:
        return _sourced("page", _withheld(page[var].value), shipped=base["value"])
    return base


def _base(setting: Setting, files: _Files, defaults: Mapping[str, str]) -> dict:
    """The value of a setting with no override: from its TOML file, else the
    default, else ``None``. Where the loader refuses the file, the value is the
    refusal."""
    if setting.file_key is not None:
        try:
            path, value = files.lookup(*setting.file_key)
        except ConfigError as exc:
            return _sourced("refused", None, refusal=str(exc))
        if value is not None:
            return _sourced("file", _override_form(value), file=str(path))
    var = setting.variable
    default: str | None = None
    if var in PASS_FLAGS:
        default = _override_form(PASS_FLAGS[var])
    elif var is not None:
        default = defaults.get(var)
    return _sourced("default", default)


def _sourced(
    source: ValueOrigin, value: str | None, **detail: str | None
) -> dict[str, Any]:
    """A row's value, where it comes from, and the detail that source shows."""
    return {"value": value, "source": source, **detail}


def _override_form(value: object) -> str:
    """A TOML value in the form of its override."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    return str(value)


def _withheld(value: str) -> str | None:
    """``value``, or ``None`` where :func:`holds_password` finds a password,
    which the page never shows."""
    return None if holds_password(value) else value


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
            *VENDORS[vendor].all_env_vars(),
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
        with contextlib.suppress(StoreConfigError):
            shown.update(object_reads(env))
    if DATABASE_URL_VAR in job_store.reads:
        try:
            from analysis_service.sql_store import database_reads
        except ModuleNotFoundError:
            shown.update(DATABASE_VARIABLES)
        else:
            shown.update(database_reads(env))
    return shown
