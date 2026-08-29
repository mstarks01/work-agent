"""``config/frameworks.toml``: the set of frameworks one install carries.

The fifth config file, and the thinnest. It holds one list, it fails closed on a
wrong version like the other four, and it says nothing about which job runs
what: the **Deployment** carries the set and the job selects from it.

It holds **no default**. A default set would make one submission mean different
things on two installs, and the caller would read no sign of it — which is worse
than the vendor-dependence this repo already refused when it bounded a job in
UTF-8 bytes rather than tokens, because it changes what the answer *is* rather
than whether the request is accepted.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import get_args

from analysis_service.errors import ConfigError
from analysis_service.report import FRAMEWORK_NAMES, FrameworkName

__all__ = ["SUPPORTED_VERSION", "FrameworkConfigError", "load_frameworks"]

#: The only schema version this loader accepts. A file on any other version
#: fails its own check rather than being migrated in place.
SUPPORTED_VERSION = 1


class FrameworkConfigError(ConfigError):
    """The framework configuration is invalid or names something unavailable."""


def load_frameworks(
    path: Path, env: Mapping[str, str] | None = None
) -> tuple[FrameworkName, ...]:
    """The frameworks this install carries, in file order.

    Raises rather than defaulting on every failure: a missing file, a wrong
    version, an empty list, a repeated name, or a name outside the closed
    vocabulary. An install that has not chosen stops and says so.
    """
    del env  # no per-key overrides: the carried set is not an ops-time knob
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FrameworkConfigError(f"{path}: no such file") from exc
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise FrameworkConfigError(f"{path}: {exc}") from exc

    version = raw.get("version")
    if version != SUPPORTED_VERSION:
        raise FrameworkConfigError(
            f"{path}: unsupported version {version!r}; expected {SUPPORTED_VERSION}"
        )

    carried = raw.get("carried")
    if not isinstance(carried, list) or not carried:
        raise FrameworkConfigError(
            f"{path}: 'carried' must be a non-empty list of framework names"
        )
    if not all(isinstance(name, str) for name in carried):
        raise FrameworkConfigError(f"{path}: 'carried' must hold strings")

    repeated = sorted({name for name in carried if carried.count(name) > 1})
    if repeated:
        raise FrameworkConfigError(f"{path}: 'carried' repeats {', '.join(repeated)}")
    unknown = sorted(set(carried) - set(FRAMEWORK_NAMES))
    if unknown:
        raise FrameworkConfigError(
            f"{path}: 'carried' names frameworks this build cannot spell:"
            f" {', '.join(unknown)}; it knows {sorted(get_args(FrameworkName))}"
        )
    return tuple(carried)
