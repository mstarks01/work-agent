"""One reader each for the TOML files and the boolean flags a deployment reads.

Four loaders refuse the same two failures the same way: a file that cannot
be read, and a file that is not TOML. Each one names its own error class, so
the caller that handles that loader's failures keeps catching what it caught.
The loaders whose refusals differ by design — the framework list, which names
a missing file on its own, and the keyring, which does not echo the reason —
keep their own read.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def read_toml(path: Path | str, error: type[Exception]) -> dict[str, Any]:
    """Parse one TOML file, or raise ``error`` naming the file and the cause."""
    try:
        return tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise error(f"{path}: invalid TOML: {exc}") from exc
    except OSError as exc:
        raise error(f"{path}: cannot be read: {exc}") from exc


def env_flag(env: Mapping[str, str], var: str) -> bool:
    """A boolean env flag, on only for an explicit affirmative."""
    return env.get(var, "").strip().lower() in ("1", "true", "yes", "on")
