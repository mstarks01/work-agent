"""The resilience config: retry, timeout, and the job's input bounds.

Mirrors ``test_sampling.py`` and ``test_model_tiers.py``: a versioned TOML file
loaded fail-closed, with the one difference that *these* values are
env-overridable, because none of them can move an eval score — attempts and
timeout change how hard we try, and the input bounds decide only whether a
submission is accepted at all, never which answer it gets.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from analysis_service.frameworks.stride.record import STRIDE_CATEGORIES
from analysis_service.resilience import (
    ATTEMPTS_VAR,
    JOB_DEADLINE_MS_VAR,
    MAX_ACTIVE_JOBS_VAR,
    MAX_SOURCE_BYTES_VAR,
    MAX_SOURCES_VAR,
    SUPPORTED_VERSION,
    TIMEOUT_MS_VAR,
    ResilienceConfig,
    ResilienceConfigError,
    load_resilience,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "resilience.toml"

VALID = (
    f"version = {SUPPORTED_VERSION}\n"
    "attempts = 3\n"
    "timeout_ms = 300000\n"
    "max_source_bytes = 102400\n"
    "max_sources = 10\n"
    "job_deadline_ms = 900000\n"
    "retry_budget_ratio = 0.1\n"
    "max_active_jobs = 3\n"
)


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "resilience.toml"
    path.write_text(body, encoding="utf-8")
    return path


def config(**kwargs) -> ResilienceConfig:
    fields = {
        "version": SUPPORTED_VERSION,
        "attempts": 3,
        "timeout_ms": 300000,
        "max_source_bytes": 102400,
        "max_sources": 10,
        "job_deadline_ms": 900000,
        "retry_budget_ratio": 0.1,
        "max_active_jobs": 3,
    }
    return ResilienceConfig(**(fields | kwargs))


def test_the_shipped_config_loads_with_the_decided_values():
    loaded = load_resilience(CONFIG_PATH, env={})
    assert loaded.attempts == 3
    assert loaded.timeout_ms == 300000
    assert loaded.max_source_bytes == 100 * 1024
    assert loaded.max_sources == 10
    assert loaded.job_deadline_ms == 900000
    assert loaded.max_active_jobs == 3


def test_the_deadline_converts_to_the_seconds_asyncio_wants():
    """The file is in milliseconds beside timeout_ms; asyncio takes seconds."""
    assert config(job_deadline_ms=900000).deadline_seconds() == 900.0
    assert config(job_deadline_ms=1500).deadline_seconds() == 1.5


def test_a_job_deadline_is_required_rather_than_defaulted():
    """No value means "no deadline" — that was version 3, and it is the defect."""
    fields = {
        "version": SUPPORTED_VERSION,
        "attempts": 3,
        "timeout_ms": 300000,
        "max_source_bytes": 102400,
        "max_sources": 10,
    }
    with pytest.raises(Exception, match="job_deadline_ms"):
        ResilienceConfig(**fields)


def test_the_deadline_bounds_what_the_per_call_knobs_cannot():
    """The arithmetic the deadline exists for, pinned so it stays visible.

    ``timeout_ms`` bounds one request; ``attempts`` of them may be made per node
    now that the library's retry layer is off; five LLM stages run in series on
    the graph's longest path. The deadline has to be well under that product or
    it is not a bound at all — and the product moves whenever any factor does,
    which is why the deadline states the answer directly instead.
    """
    loaded = load_resilience(CONFIG_PATH, env={})
    per_node = loaded.timeout_ms * loaded.attempts
    unbounded_worst_case = per_node * 5
    assert loaded.job_deadline_ms < unbounded_worst_case


def test_the_policy_carries_attempts_as_a_total():
    # attempts is a *total* count, and with LiteLLM's layer off it is now
    # literally the request count per node rather than half of a product.
    policy = config().retry_policy(budget_capacity=10)
    assert policy.attempts == 3
    assert config(attempts=1).retry_policy(budget_capacity=10).attempts == 1


def test_the_policy_budget_starts_full_at_the_capacity_it_was_given():
    # Full at the start, so an isolated failure in a healthy process is still
    # retried immediately: the budget bounds sustained retrying, not a burst.
    policy = config().retry_policy(budget_capacity=10)
    assert policy.budget.tokens == 10
    assert policy.budget.ratio == 0.1


def test_a_zero_retry_budget_is_refused(tmp_path):
    # A deployment that never retries says attempts = 1 and means it, rather
    # than expressing it as a budget that can never be drawn on.
    with pytest.raises(ValueError):
        config(retry_budget_ratio=0)


def test_http_options_carry_the_timeout():
    assert config().to_http_options().timeout == 300000


def test_the_backoff_knobs_stay_out_of_the_schema():
    # They were removed for connecting to nothing, and a curve now exists to
    # describe — but it is pinned in analysis_service.retry, because it does not
    # vary by deployment. What varies is retry_budget_ratio.
    for knob in ("initial_delay", "max_delay", "exp_base", "jitter"):
        with pytest.raises(ValueError):
            config(**{knob: 1.0})


@pytest.mark.parametrize("stale", [1, 2, 4])
def test_a_stale_version_fails_closed_with_no_shim(tmp_path, stale):
    # Version 4 is the case a live deployment actually hits: it is a well-formed
    # file for the previous schema, carrying no per-caller ceiling. Accepting it
    # would mean serving unbounded concurrent jobs per token — the state this
    # version exists to end — under a file that looked valid.
    path = write(tmp_path, f"version = {stale}\nattempts = 3\ntimeout_ms = 300000\n")
    with pytest.raises(ResilienceConfigError, match="unsupported version"):
        load_resilience(path, env={})


def test_the_version_error_names_the_keys_the_new_schema_wants(tmp_path):
    path = write(tmp_path, "version = 2\nattempts = 3\ntimeout_ms = 300000\n")
    with pytest.raises(ResilienceConfigError, match="max_source_bytes"):
        load_resilience(path, env={})


def test_input_bounds_are_required_not_defaulted(tmp_path):
    # No code default: a deployment either states its bounds or does not load.
    path = write(
        tmp_path, f"version = {SUPPORTED_VERSION}\nattempts = 3\ntimeout_ms = 300000\n"
    )
    with pytest.raises(ResilienceConfigError):
        load_resilience(path, env={})


def test_the_ceiling_is_required_rather_than_defaulted(tmp_path):
    # No value means "unlimited" — that was version 4, and it is the defect. A
    # deployment that has not chosen a per-caller ceiling does not load.
    path = write(tmp_path, VALID.replace("max_active_jobs = 3\n", ""))
    with pytest.raises(ResilienceConfigError, match="max_active_jobs"):
        load_resilience(path, env={})


def test_a_ceiling_of_zero_is_refused(tmp_path):
    # A deployment that accepts no jobs at all should not be running, so zero is
    # a misconfiguration rather than a way to spell "closed".
    path = write(tmp_path, VALID.replace("max_active_jobs = 3", "max_active_jobs = 0"))
    with pytest.raises(ResilienceConfigError):
        load_resilience(path, env={})


def test_a_serial_ceiling_of_one_is_legal(tmp_path):
    # The first-run web app's gate expressed as a number: one job per caller.
    path = write(tmp_path, VALID.replace("max_active_jobs = 3", "max_active_jobs = 1"))
    assert load_resilience(path, env={}).max_active_jobs == 1


def test_env_overrides_the_ceiling(tmp_path):
    """Turning it down sheds load mid-incident without an image rebuild."""
    path = write(tmp_path, VALID)
    loaded = load_resilience(path, env={MAX_ACTIVE_JOBS_VAR: "1"})
    assert loaded.max_active_jobs == 1


def test_the_ceiling_is_sized_against_the_category_fan_out(tmp_path):
    """The arithmetic the number was chosen by, pinned so it stays visible.

    Each accepted job fans the six STRIDE category agents out in parallel on the
    ``strong`` tier, so the burst a provider's per-minute quota actually sees is
    six times this ceiling — the reason the knob is small, and the check anyone
    raising it has to redo.
    """
    loaded = load_resilience(CONFIG_PATH, env={})
    assert loaded.max_active_jobs * len(STRIDE_CATEGORIES) == 18


def test_a_version_1_backoff_knob_is_rejected_not_ignored(tmp_path):
    path = write(tmp_path, VALID + "jitter = 0.5\n")
    with pytest.raises(ResilienceConfigError):
        load_resilience(path, env={})


def test_env_overrides_attempts_and_timeout(tmp_path):
    path = write(tmp_path, VALID)
    loaded = load_resilience(path, env={ATTEMPTS_VAR: "5", TIMEOUT_MS_VAR: "120000"})
    assert loaded.attempts == 5
    assert loaded.timeout_ms == 120000


def test_env_overrides_the_job_deadline(tmp_path):
    """Turning it down sheds load mid-incident without an image rebuild."""
    path = write(tmp_path, VALID)
    loaded = load_resilience(path, env={JOB_DEADLINE_MS_VAR: "120000"})
    assert loaded.job_deadline_ms == 120000
    assert loaded.deadline_seconds() == 120.0


def test_an_env_override_is_validated_like_a_file_value(tmp_path):
    path = write(tmp_path, VALID)
    with pytest.raises(ResilienceConfigError):
        load_resilience(path, env={ATTEMPTS_VAR: "0"})


def test_a_non_numeric_override_is_rejected(tmp_path):
    path = write(tmp_path, VALID)
    with pytest.raises(ResilienceConfigError, match=ATTEMPTS_VAR):
        load_resilience(path, env={ATTEMPTS_VAR: "lots"})


def test_a_set_but_empty_override_is_a_mistake_not_a_no_op(tmp_path):
    path = write(tmp_path, VALID)
    with pytest.raises(ResilienceConfigError, match="set but empty"):
        load_resilience(path, env={ATTEMPTS_VAR: ""})


def test_a_missing_file_fails_closed(tmp_path):
    with pytest.raises(ResilienceConfigError):
        load_resilience(tmp_path / "gone.toml", env={})


def test_an_unknown_key_fails_closed(tmp_path):
    path = write(tmp_path, VALID + "budget = 9\n")
    with pytest.raises(ResilienceConfigError):
        load_resilience(path, env={})


def test_a_zero_timeout_is_rejected():
    with pytest.raises(ValueError):
        config(timeout_ms=0)


def test_env_overrides_the_input_bounds(tmp_path):
    # A constrained install lowers these without editing a file.
    path = write(tmp_path, VALID)
    loaded = load_resilience(
        path, env={MAX_SOURCE_BYTES_VAR: "4096", MAX_SOURCES_VAR: "2"}
    )
    assert loaded.max_source_bytes == 4096
    assert loaded.max_sources == 2


@pytest.mark.parametrize(
    ("var", "value"), [(MAX_SOURCE_BYTES_VAR, "0"), (MAX_SOURCES_VAR, "0")]
)
def test_an_out_of_range_bound_is_rejected_from_the_environment(tmp_path, var, value):
    path = write(tmp_path, VALID)
    with pytest.raises(ResilienceConfigError):
        load_resilience(path, env={var: value})


def test_a_job_must_be_allowed_at_least_one_source():
    with pytest.raises(ValueError):
        config(max_sources=0)
