"""The readers of a closed store setting and of a stray one, and the error
of an unusable one.

The job store, the object store and the database each select a row of a
table by one setting. This module imports nothing from the stores, so each
store module can read its settings here.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import TypeVar


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
    owner: str,
) -> None:
    """Raise for each set setting under ``prefixes`` that ``owner`` does not read.

    **The one reader of a stray store setting.** A setting that the selected
    backend does not read would otherwise do nothing, and the operator would
    think it applies. ``owner`` names the backend, for the message.
    """
    stray = sorted(
        name
        for name, value in env.items()
        if name.startswith(prefixes) and name not in read and value.strip()
    )
    if stray:
        pronoun = "it" if len(stray) == 1 else "them"
        raise StoreConfigError(f"{owner} takes no {', '.join(stray)}; unset {pronoun}")
