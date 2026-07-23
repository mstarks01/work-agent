"""Resilience configuration for the graph's LLM calls.

Implements wayfinder ticket 038: on their own defaults the nine LLM nodes
never retry and never time out. ``Gemini.retry_options`` defaults to ``None``,
which genai turns into "attempt once", so a single 429 on any node kills a
paid-for job on first contact; ``http_options.timeout`` defaults to ``None``,
which reaches httpx as no deadline, so a stalled call parks a job in
``running`` forever. This module is the two knobs that fix both.

Unlike :mod:`stride_service.sampling`, these values **are** env-overridable.
The split that settles it: sampling is pinned because temperature changes
*what* the model produces, so an eval-only value is how a suite goes green
while production drifts; attempts and timeout change only *how hard we try*,
never which answer we get, so they cannot move a score and are exactly the
knobs to turn down mid-incident without a redeploy.

``attempts`` and ``timeout_ms`` compose two SDK mechanisms into one
resilience story (ticket 038 decisions 2 and 4): the timeout rides on the
per-request ``GenerateContentConfig.http_options`` and turns a hang into an
``httpx.TimeoutException``, which is already in the SDK's retry predicate, so
the client-level retry then re-issues it. The backoff knobs are optional —
left unset, the SDK's own jittered defaults and retryable status set apply.

Loading fails closed: a malformed file, an out-of-range value or an unknown
key raises :class:`ResilienceConfigError` rather than silently reverting a
node to never-retry, no-timeout behaviour.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TypeVar

from google.genai import types
from pydantic import BaseModel, ConfigDict, Field, ValidationError

_ENV_PREFIX = "STRIDE_"

# The env vars that retune resilience at deploy time or mid-incident. Named
# alongside STRIDE_MODEL_FLASH / STRIDE_MODEL_PRO (ticket 007).
ATTEMPTS_VAR = f"{_ENV_PREFIX}RETRY_ATTEMPTS"
TIMEOUT_MS_VAR = f"{_ENV_PREFIX}TIMEOUT_MS"
INITIAL_DELAY_VAR = f"{_ENV_PREFIX}RETRY_INITIAL_DELAY"
MAX_DELAY_VAR = f"{_ENV_PREFIX}RETRY_MAX_DELAY"
EXP_BASE_VAR = f"{_ENV_PREFIX}RETRY_EXP_BASE"
JITTER_VAR = f"{_ENV_PREFIX}RETRY_JITTER"

_T = TypeVar("_T", int, float)


class ResilienceConfigError(ValueError):
    """The resilience configuration is invalid or unusable."""


class ResilienceConfig(BaseModel):
    """Validated retry and timeout parameters applied to every LLM call.

    Only the parameters the design has an opinion about are required.
    ``attempts`` and ``timeout_ms`` are the two decided in ticket 038; the
    backoff knobs are optional and default to the SDK's own jittered values
    when left unset, so pinning a number this project has never measured is
    never forced.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    attempts: int = Field(ge=1)
    timeout_ms: int = Field(gt=0)
    initial_delay: float | None = Field(default=None, gt=0.0)
    max_delay: float | None = Field(default=None, gt=0.0)
    exp_base: float | None = Field(default=None, gt=0.0)
    jitter: float | None = Field(default=None, ge=0.0)

    def to_retry_options(self) -> types.HttpRetryOptions:
        """The client-level retry policy the GenAI SDK applies (decision 2).

        ``http_status_codes`` is left unset on purpose — reusing the SDK's own
        retryable set rather than second-guessing which 5xx/429 codes deserve
        a retry.
        """
        return types.HttpRetryOptions(
            attempts=self.attempts,
            initial_delay=self.initial_delay,
            max_delay=self.max_delay,
            exp_base=self.exp_base,
            jitter=self.jitter,
        )

    def to_http_options(self) -> types.HttpOptions:
        """Per-request HTTP options carrying the deadline (decision 4).

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
    negative ``attempts`` arriving via the environment is rejected exactly
    like one in the file.
    """
    if env is None:
        env = os.environ
    try:
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ResilienceConfigError(f"{path}: invalid TOML: {exc}") from exc
    except OSError as exc:
        raise ResilienceConfigError(f"{path}: cannot be read: {exc}") from exc

    overrides = {
        "attempts": _override(env, ATTEMPTS_VAR, int),
        "timeout_ms": _override(env, TIMEOUT_MS_VAR, int),
        "initial_delay": _override(env, INITIAL_DELAY_VAR, float),
        "max_delay": _override(env, MAX_DELAY_VAR, float),
        "exp_base": _override(env, EXP_BASE_VAR, float),
        "jitter": _override(env, JITTER_VAR, float),
    }
    raw.update({key: value for key, value in overrides.items() if value is not None})

    try:
        return ResilienceConfig(**raw)
    except ValidationError as exc:
        raise ResilienceConfigError(f"{path}: {exc}") from exc
