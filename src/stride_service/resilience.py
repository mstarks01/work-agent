"""Operational bounds that cannot change an answer.

Four knobs. Two — attempts and timeout — are applied to every LLM call: on
library defaults the nodes never retry and never time out, so a single 429 on
any node would kill a paid-for job on first contact, and a stalled call would
park a job in ``running`` forever. Two more bound the job's *input*: how many
sources one job may carry, and how many UTF-8 bytes they may total.

The input bounds are in **bytes, not tokens**. A token budget would make the
public contract depend on which vendor a deployment's tiers happen to select,
so what a caller may submit would change under them without the contract
changing. Bytes are something the caller can measure themselves. There is
deliberately no *per-source* cap: it would forbid only shapes the total already
permits, and it would let the service blame one source for a budget the whole
submission overspent.

Unlike :mod:`stride_service.sampling`, every value here **is** env-overridable.
The split that settles it: sampling is pinned because temperature changes *what*
the model produces, so an eval-only value is how a suite goes green while
production drifts. Nothing in this file can move an eval score — attempts and
timeout change only *how hard we try*, and the input bounds decide only whether
a submission is accepted at all, never which answer it gets — so these are
exactly the knobs to turn down mid-incident without a redeploy.

There are no backoff knobs. ``litellm`` picks its backoff curve internally from
the exception type, so an ``initial_delay`` / ``max_delay`` / ``exp_base`` /
``jitter`` surface would read as a knob and connect to nothing.

Retry rides the adapter's constructor, and the arithmetic is explicit.
``attempts`` is a *total* count while LiteLLM's ``num_retries`` counts retries
*after* the first try, so passing ``attempts`` through verbatim would over-shoot
to four tries where three are configured. Only ``attempts - 1`` reproduces the
configured number.

Loading fails closed: a malformed file, an out-of-range value, an unknown key or
a stale version raises :class:`ResilienceConfigError` rather than silently
reverting a node to never-retry, no-timeout behaviour.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TypeVar

from google.genai import types
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from stride_service.sources import SourceLimits

# The only schema version this loader accepts. The version check fires before
# shape validation, so a file on another schema is named as such rather than
# reported as a set of stray keys under ``extra="forbid"``.
SUPPORTED_VERSION = 3

_ENV_PREFIX = "STRIDE_"

ATTEMPTS_VAR = f"{_ENV_PREFIX}RETRY_ATTEMPTS"
TIMEOUT_MS_VAR = f"{_ENV_PREFIX}TIMEOUT_MS"
MAX_SOURCE_BYTES_VAR = f"{_ENV_PREFIX}MAX_SOURCE_BYTES"
MAX_SOURCES_VAR = f"{_ENV_PREFIX}MAX_SOURCES"

_T = TypeVar("_T", int, float)


class ResilienceConfigError(ValueError):
    """The resilience configuration is invalid or unusable."""


class ResilienceConfig(BaseModel):
    """Validated retry, timeout and input bounds for one deployment.

    ``max_source_bytes`` is the total across *all* of a job's sources, not a
    bound on any one of them — an over-budget submission overspent as a whole,
    and no single source is at fault.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    attempts: int = Field(ge=1)
    timeout_ms: int = Field(gt=0)
    max_source_bytes: int = Field(gt=0)
    max_sources: int = Field(ge=1)

    def to_num_retries(self) -> int:
        """LiteLLM's ``num_retries``: retries *after* the first try.

        See the module docstring — this arithmetic is the whole reason the value
        is not passed through verbatim.
        """
        return self.attempts - 1

    def source_limits(self) -> SourceLimits:
        """The input bounds, as the value every entry point checks against."""
        return SourceLimits(
            max_total_bytes=self.max_source_bytes, max_sources=self.max_sources
        )

    def to_http_options(self) -> types.HttpOptions:
        """Per-request HTTP options carrying the deadline.

        ADK merges its tracking headers and api version into a caller-supplied
        ``http_options``, so setting the timeout here leaves those untouched.
        """
        return types.HttpOptions(timeout=self.timeout_ms)


def _override(
    env: Mapping[str, str], var: str, parse: Callable[[str], _T]
) -> _T | None:
    """Parse one env override, treating a set-but-empty value as a mistake."""
    if var not in env:
        return None
    value = env[var]
    if not value.strip():
        raise ResilienceConfigError(f"{var} is set but empty")
    try:
        return parse(value)
    except ValueError as exc:
        raise ResilienceConfigError(f"{var}: {value!r} is not valid: {exc}") from exc


def load_resilience(
    path: Path | str,
    env: Mapping[str, str] | None = None,
) -> ResilienceConfig:
    """Load and validate the resilience config, applying env-var overrides.

    Overrides replace the corresponding file value before validation, so a
    negative ``attempts`` arriving via the environment is rejected exactly like
    one in the file.
    """
    if env is None:
        env = os.environ
    try:
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ResilienceConfigError(f"{path}: invalid TOML: {exc}") from exc
    except OSError as exc:
        raise ResilienceConfigError(f"{path}: cannot be read: {exc}") from exc

    version = raw.get("version")
    if version != SUPPORTED_VERSION:
        raise ResilienceConfigError(
            f"{path}: unsupported version {version!r};"
            f" expected {SUPPORTED_VERSION}, which carries 'attempts',"
            " 'timeout_ms', 'max_source_bytes' and 'max_sources'"
        )

    overrides = {
        "attempts": _override(env, ATTEMPTS_VAR, int),
        "timeout_ms": _override(env, TIMEOUT_MS_VAR, int),
        "max_source_bytes": _override(env, MAX_SOURCE_BYTES_VAR, int),
        "max_sources": _override(env, MAX_SOURCES_VAR, int),
    }
    raw.update({key: value for key, value in overrides.items() if value is not None})

    try:
        return ResilienceConfig(**raw)
    except ValidationError as exc:
        raise ResilienceConfigError(f"{path}: {exc}") from exc
