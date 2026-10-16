"""The readers of a closed store setting and of a stray one, and the error
of an unusable one.

The job store, the object store and the database each select a row of a
table by one setting. This module imports nothing from the stores, so each
store module can read its settings here.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import TypeVar

#: The settings of the database of the ``sql`` job store. They live here, and
#: not in :mod:`~analysis_service.sql_store`, so that a reader without the
#: ``sql`` extra can name them. The rows of
#: :data:`~analysis_service.sql_store.DATABASES` and
#: :data:`~analysis_service.sql_store.DATABASE_CREDENTIALS` read them.
DATABASE_URL_VAR = "ANALYSIS_DATABASE_URL"
DATABASE_CREDENTIALS_VAR = "ANALYSIS_DATABASE_CREDENTIALS"
DATABASE_PASSWORD_FILE_VAR = "ANALYSIS_DATABASE_PASSWORD_FILE"
DATABASE_CA_FILE_VAR = "ANALYSIS_DATABASE_CA_FILE"
DATABASE_IAM_VAR = "ANALYSIS_DATABASE_IAM"
DATABASE_REGION_VAR = "ANALYSIS_DATABASE_REGION"
DATABASE_VARIABLES = (
    DATABASE_URL_VAR,
    DATABASE_CREDENTIALS_VAR,
    DATABASE_PASSWORD_FILE_VAR,
    DATABASE_CA_FILE_VAR,
    DATABASE_IAM_VAR,
    DATABASE_REGION_VAR,
)


class StoreConfigError(ValueError):
    """A setting of the job store, the object store or the database is missing
    or unusable."""


Name = TypeVar("Name", bound=str)


def closed_setting(value: str, setting: str, table: Mapping[Name, object]) -> Name:
    """The row of ``table`` that ``value`` names. An empty or unknown value raises.

    **The one reader of a closed setting**: the job store, the object store,
    the database and their credential modes all read theirs here. ``setting``
    names where the value comes from, for the message. The value is never read
    from a request.
    """
    value = value.strip().lower()
    if not value:
        raise StoreConfigError(f"set {setting}")
    for name in table:
        if name == value:
            return name
    known = ", ".join(sorted(table))
    raise StoreConfigError(
        f"unknown value {value!r} for {setting}; known values: {known}"
    )


def refuse_unread(
    env: Mapping[str, str],
    prefixes: tuple[str, ...],
    read: Collection[str],
    backend: str,
) -> None:
    """Raise for each set setting under ``prefixes`` that ``backend`` does not read.

    **The one reader of a stray store setting.** A setting that the selected
    backend does not read would otherwise do nothing, and the operator would
    think it applies. ``backend`` names the backend, for the message.
    """
    stray = sorted(
        name
        for name, value in env.items()
        if name.startswith(prefixes) and name not in read and value.strip()
    )
    if stray:
        pronoun = "it" if len(stray) == 1 else "them"
        raise StoreConfigError(
            f"{backend} takes no {', '.join(stray)}; unset {pronoun}"
        )
