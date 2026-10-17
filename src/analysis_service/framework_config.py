"""``config/frameworks.toml``: the set of frameworks one install carries.

This is the fifth config file, and the thinnest. It holds one list. It fails
closed on a wrong version, as the other four do. It says nothing about which job
runs what: the **Deployment** carries the set, and the job selects from it.

It holds no default. A default set would make one submission mean different
things on two installs, and the caller would read no sign of it. That is worse
than the vendor-dependence this repository already refused when it bounded a job
in UTF-8 bytes rather than tokens, because it changes what the answer is rather
than whether the request is accepted.

``ANALYSIS_FRAMEWORKS_CARRIED`` replaces the ``carried`` list of the file. Its
value is framework names separated by commas, with no spaces, such as
``asvs,stride`` (ADR 0092). This loader is its one reader.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast, get_args

from analysis_service.claims import (
    FRAMEWORK_NAMES,
    FrameworkName,
)
from analysis_service.errors import ConfigError
from analysis_service.parsing import is_version, strings

__all__ = [
    "CARRIED_VAR",
    "SUPPORTED_VERSION",
    "FrameworkConfigError",
    "env_variables",
    "load_frameworks",
]

#: The only schema version this loader accepts. A file on any other version
#: fails its own check rather than being migrated in place.
SUPPORTED_VERSION = 1

#: The override of the ``carried`` list of the file.
CARRIED_VAR = "ANALYSIS_FRAMEWORKS_CARRIED"


def env_variables() -> tuple[str, ...]:
    """Every env override this loader reads."""
    return (CARRIED_VAR,)


class FrameworkConfigError(ConfigError):
    """The framework configuration is invalid or names something unavailable."""


def load_frameworks(
    path: Path, env: Mapping[str, str] | None = None
) -> tuple[FrameworkName, ...]:
    """The frameworks this install carries, in file order or in the order of
    :data:`CARRIED_VAR`.

    Raises rather than defaulting on every failure: a missing file, a wrong
    version, an empty list, a repeated name, or a name outside the closed
    vocabulary. An install that has not chosen stops and says so.
    """
    if env is None:
        env = os.environ
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FrameworkConfigError(f"{path}: no such file") from exc
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise FrameworkConfigError(f"{path}: {exc}") from exc

    version = raw.get("version")
    if not is_version(version, SUPPORTED_VERSION):
        raise FrameworkConfigError(
            f"{path}: unsupported version {version!r}; expected {SUPPORTED_VERSION}"
        )

    override = env.get(CARRIED_VAR)
    if override is not None:
        return _carried(_split(override), CARRIED_VAR)

    carried = raw.get("carried")
    if not isinstance(carried, list) or not carried:
        raise FrameworkConfigError(
            f"{path}: 'carried' must be a non-empty list of framework names"
        )
    if not strings(carried):
        raise FrameworkConfigError(f"{path}: 'carried' must hold strings")
    return _carried(carried, f"{path}: 'carried'")


def _split(value: str) -> list[str]:
    """The names of :data:`CARRIED_VAR`, which holds no space and no empty name."""
    if not value:
        raise FrameworkConfigError(
            f"{CARRIED_VAR} is empty; it must name at least one framework"
        )
    if any(char.isspace() for char in value):
        raise FrameworkConfigError(
            f"{CARRIED_VAR} holds a space; separate the names with commas only"
        )
    names = value.split(",")
    if not all(names):
        raise FrameworkConfigError(f"{CARRIED_VAR} holds an empty name")
    return names


def _carried(names: Sequence[str], where: str) -> tuple[FrameworkName, ...]:
    """``names`` as the carried set, when no name repeats and each one is in
    the closed vocabulary. ``where`` opens each refusal."""
    repeated = sorted({name for name in names if names.count(name) > 1})
    if repeated:
        raise FrameworkConfigError(f"{where} repeats {', '.join(repeated)}")
    unknown = sorted(set(names) - set(FRAMEWORK_NAMES))
    if unknown:
        raise FrameworkConfigError(
            f"{where} names frameworks this build cannot spell:"
            f" {', '.join(unknown)}; it knows {sorted(get_args(FrameworkName))}"
        )
    return cast(tuple[FrameworkName, ...], tuple(names))
