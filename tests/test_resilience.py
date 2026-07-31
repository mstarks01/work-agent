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

from stride_service.resilience import (
    ATTEMPTS_VAR,
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
    }
    return ResilienceConfig(**(fields | kwargs))


def test_the_shipped_config_loads_with_the_decided_values():
    loaded = load_resilience(CONFIG_PATH, env={})
    assert loaded.attempts == 3
    assert loaded.timeout_ms == 300000
    assert loaded.max_source_bytes == 100 * 1024
    assert loaded.max_sources == 10


def test_num_retries_is_attempts_minus_one():
    # LiteLLM counts retries after the first try; attempts is a total. Passing
    # 3 through verbatim would give four tries, and the old timeout-only path
    # silently gave one — only the arithmetic reproduces the configured number.
    assert config().to_num_retries() == 2


def test_a_single_attempt_means_no_retries():
    assert config(attempts=1).to_num_retries() == 0


def test_http_options_carry_the_timeout():
    assert config().to_http_options().timeout == 300000


def test_the_backoff_knobs_are_gone_from_the_schema():
    # They appear nowhere in litellm, which picks its curve from the exception
    # type, so as config they read as a knob and connected to nothing.
    for knob in ("initial_delay", "max_delay", "exp_base", "jitter"):
        with pytest.raises(ValueError):
            config(**{knob: 1.0})


@pytest.mark.parametrize("stale", [1, 2])
def test_a_stale_version_fails_closed_with_no_shim(tmp_path, stale):
    # Version 2 is the case a live deployment actually hits on this upgrade: it
    # is a well-formed file for the *previous* schema, carrying no input bounds.
    # Accepting it would mean enforcing caps nobody configured.
    path = write(
        tmp_path, f"version = {stale}\nattempts = 3\ntimeout_ms = 300000\n"
    )
    with pytest.raises(ResilienceConfigError, match="unsupported version"):
        load_resilience(path, env={})


def test_the_version_error_names_the_keys_the_new_schema_wants(tmp_path):
    path = write(tmp_path, "version = 2\nattempts = 3\ntimeout_ms = 300000\n")
    with pytest.raises(ResilienceConfigError, match="max_source_bytes"):
        load_resilience(path, env={})


def test_input_bounds_are_required_not_defaulted(tmp_path):
    # No code default: a deployment either states its bounds or does not load.
    path = write(tmp_path, f"version = {SUPPORTED_VERSION}\nattempts = 3\n"
                 "timeout_ms = 300000\n")
    with pytest.raises(ResilienceConfigError):
        load_resilience(path, env={})


def test_a_version_1_backoff_knob_is_rejected_not_ignored(tmp_path):
    path = write(tmp_path, VALID + "jitter = 0.5\n")
    with pytest.raises(ResilienceConfigError):
        load_resilience(path, env={})


def test_env_overrides_attempts_and_timeout(tmp_path):
    path = write(tmp_path, VALID)
    loaded = load_resilience(path, env={ATTEMPTS_VAR: "5", TIMEOUT_MS_VAR: "120000"})
    assert loaded.attempts == 5
    assert loaded.timeout_ms == 120000


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
