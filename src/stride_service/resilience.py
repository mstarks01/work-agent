"""Operational bounds that cannot change an answer.

Seven knobs. Three — attempts, timeout and the retry budget — are applied to
every LLM call: on library defaults the nodes never retry and never time out, so
a single 429 on any node would kill a paid-for job on first contact, and a
stalled call would park a job in ``running`` forever. Two more bound the job's
*input*: how many sources one job may carry, and how many UTF-8 bytes they may
total. The sixth bounds the job's *duration*, and the seventh bounds how many
jobs one caller may have running at once.

``job_deadline_ms`` is the only one of the seven that bounds a *single* job as a
whole, and it exists because the per-call knobs provably cannot. ``timeout_ms``
bounds one HTTP request; ``attempts`` multiplies it; the retry arithmetic below
multiplies it again; and the graph runs five LLM stages in series on its
longest path. Those compose to a worst case in the *hours* while every
individual bound is respected — so before this knob, a job's tail was set by a
product of four numbers nobody chose, and a wedged run held a ``running`` job
until the process died. A deadline is the one bound whose worst case is the
number written down.

``max_active_jobs`` is the only knob that bounds a *caller* rather than a job,
and the other six are why it has to exist. Every one of them is per-job, so a
caller who respects all six and simply submits a thousand submissions is inside
the contract while spending the service's whole provider quota: each accepted
job fans six category agents out on the ``strong`` tier, so ten concurrent
submissions is sixty concurrent ``strong``-tier requests against one shared
per-minute quota. Per-job budgets are not a per-caller budget, and the
unbounded-consumption half of OWASP LLM10 is the latter.

It bounds jobs *in flight*, not jobs per interval. A ceiling on concurrency is
self-clearing — finishing a job is what buys the next one — so it needs no
window, no timer and no state beyond the records the store already holds, and it
bounds the thing that actually costs money: simultaneous provider calls. A
submission rate says nothing about that on its own.

The input bounds are in **bytes, not tokens**. A token budget would make the
public contract depend on which vendor a deployment's tiers happen to select,
so what a caller may submit would change under them without the contract
changing. Bytes are something the caller can measure themselves. There is
deliberately no *per-source* cap: it would forbid only shapes the total already
permits, and it would let the service blame one source for a budget the whole
submission overspent.

Unlike :mod:`stride_service.sampling`, every value here **is** env-overridable.
The split that settles it: sampling decides *what* the model produces, so an
eval-only value there is how a suite goes green while production drifts. Nothing in this file can move an eval score — attempts and
timeout change only *how hard we try*, and the input bounds decide only whether
a submission is accepted at all, never which answer it gets — so these are
exactly the knobs to turn down mid-incident without a redeploy.

``attempts`` is a *total* count, and with the library's retry layer switched off
it is now literally the request count per node. It did not used to be. LiteLLM's
``num_retries`` counts retries *after* the first try, and worse, sets the
provider SDK's own ``max_retries`` **from** that same value on the way to the
client — so the first attempt carried its own SDK-level retries underneath, and
the worst case per node was ``2 * attempts - 1`` requests: five at the shipped
three, and up to thirty in the seconds the six category agents fan out. Against a
per-minute quota that burst is what turns one 429 into a run spending its budget
on retried 429s (OWASP LLM10). Passing ``max_retries`` did not close it —
``num_retries`` overwrites it on the way to the client — which is what
``tests/test_model_gate.py`` probes.

So the layer is off (``num_retries=0``, one request per call) and the loop lives
in :mod:`stride_service.retry`, above the adapter where it can be bounded.
``retry_budget_ratio`` is the knob that bounds it: retries draw from one
process-wide token bucket credited by successful requests, so they are capped at
a *share of working traffic* instead of at a count per node. Correlated failure
— the only kind that storms — empties the bucket once for everyone and the
service stops retrying, while an isolated failure sees a full bucket and is
retried as before. Turning ``attempts`` down mid-incident still works and is
still the blunt instrument; the budget is what makes it rarely necessary.

The backoff knobs version 2 removed stay removed, but the reasoning has changed
and is worth keeping straight. They went because LiteLLM picked its own curve
internally and the config surface connected to nothing. A curve now exists to
describe — full jitter, with a provider's ``Retry-After`` overriding it — and it
is pinned in :mod:`stride_service.retry` rather than re-opened here, because it
does not vary by deployment. What varies is how much retrying a deployment will
tolerate, and that is the one number this file carries.

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

from stride_service.retry import RetryBudget, RetryPolicy
from stride_service.sources import SourceLimits

# The only schema version this loader accepts. The version check fires before
# shape validation, so a file on another schema is named as such rather than
# reported as a set of stray keys under ``extra="forbid"``.
SUPPORTED_VERSION = 5

_ENV_PREFIX = "STRIDE_"

ATTEMPTS_VAR = f"{_ENV_PREFIX}RETRY_ATTEMPTS"
TIMEOUT_MS_VAR = f"{_ENV_PREFIX}TIMEOUT_MS"
MAX_SOURCE_BYTES_VAR = f"{_ENV_PREFIX}MAX_SOURCE_BYTES"
MAX_SOURCES_VAR = f"{_ENV_PREFIX}MAX_SOURCES"
JOB_DEADLINE_MS_VAR = f"{_ENV_PREFIX}JOB_DEADLINE_MS"
RETRY_BUDGET_RATIO_VAR = f"{_ENV_PREFIX}RETRY_BUDGET_RATIO"
MAX_ACTIVE_JOBS_VAR = f"{_ENV_PREFIX}MAX_ACTIVE_JOBS"

_T = TypeVar("_T", int, float)


class ResilienceConfigError(ValueError):
    """The resilience configuration is invalid or unusable."""


class ResilienceConfig(BaseModel):
    """Validated retry, timeout and input bounds for one deployment.

    ``max_source_bytes`` is the total across *all* of a job's sources, not a
    bound on any one of them — an over-budget submission overspent as a whole,
    and no single source is at fault.

    ``max_active_jobs`` is the one bound here that is per *caller* rather than
    per job: how many jobs one token subject may have in flight at once.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    attempts: int = Field(ge=1)
    timeout_ms: int = Field(gt=0)
    max_source_bytes: int = Field(gt=0)
    max_sources: int = Field(ge=1)
    job_deadline_ms: int = Field(gt=0)
    retry_budget_ratio: float = Field(gt=0, le=1)
    max_active_jobs: int = Field(ge=1)

    def retry_policy(self, budget_capacity: float) -> RetryPolicy:
        """The retry loop this deployment runs, with its shared budget.

        ``budget_capacity`` is what one job may spend from a cold bucket, and
        the caller supplies it because it is a fact about the *graph* — how many
        LLM nodes there are to retry — not about this file. See
        :class:`~stride_service.retry.RetryBudget`.

        There is deliberately no ``to_num_retries``. The library's retry layer
        is off, and a helper that still computed its parameter would read as
        though something down there were still counting.
        """
        return RetryPolicy(
            attempts=self.attempts,
            budget=RetryBudget(capacity=budget_capacity, ratio=self.retry_budget_ratio),
        )

    def deadline_seconds(self) -> float:
        """The job deadline as ``asyncio`` wants it: seconds, not milliseconds.

        The file is in milliseconds to match ``timeout_ms`` beside it — one unit
        for every duration an operator edits — and the conversion lives here so
        no caller has to remember which of the two it is holding.
        """
        return self.job_deadline_ms / 1000

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
            " 'timeout_ms', 'max_source_bytes', 'max_sources',"
            " 'job_deadline_ms', 'retry_budget_ratio' and 'max_active_jobs'"
        )

    overrides = {
        "attempts": _override(env, ATTEMPTS_VAR, int),
        "timeout_ms": _override(env, TIMEOUT_MS_VAR, int),
        "max_source_bytes": _override(env, MAX_SOURCE_BYTES_VAR, int),
        "max_sources": _override(env, MAX_SOURCES_VAR, int),
        "job_deadline_ms": _override(env, JOB_DEADLINE_MS_VAR, int),
        "retry_budget_ratio": _override(env, RETRY_BUDGET_RATIO_VAR, float),
        "max_active_jobs": _override(env, MAX_ACTIVE_JOBS_VAR, int),
    }
    raw.update({key: value for key, value in overrides.items() if value is not None})

    try:
        return ResilienceConfig(**raw)
    except ValidationError as exc:
        raise ResilienceConfigError(f"{path}: {exc}") from exc
