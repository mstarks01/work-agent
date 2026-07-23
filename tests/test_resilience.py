"""The resilience config: the retry and timeout knobs of ticket 038.

Mirrors ``test_sampling.py`` and ``test_model_tiers.py``: a versioned TOML
file loaded fail-closed, with the one difference that *these* values are
env-overridable, because attempts and timeout change how hard we try, never
which answer we get.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stride_service.resilience import (
    ATTEMPTS_VAR,
    JITTER_VAR,
    TIMEOUT_MS_VAR,
    ResilienceConfig,
    ResilienceConfigError,
    load_resilience,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "resilience.toml"


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "resilience.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_the_shipped_config_loads_with_the_decided_values():
    config = load_resilience(CONFIG_PATH, env={})
    assert config.attempts == 3
    assert config.timeout_ms == 300000


def test_retry_options_carry_attempts_and_leave_the_status_set_to_the_sdk():
    config = ResilienceConfig(version=1, attempts=3, timeout_ms=300000)
    options = config.to_retry_options()
    assert options.attempts == 3
    # Reusing the SDK's own retryable set rather than second-guessing it.
    assert options.http_status_codes is None


def test_http_options_carry_the_timeout():
    config = ResilienceConfig(version=1, attempts=3, timeout_ms=300000)
    assert config.to_http_options().timeout == 300000


def test_optional_backoff_knobs_stay_unset_unless_given():
    config = ResilienceConfig(version=1, attempts=3, timeout_ms=300000)
    options = config.to_retry_options()
    assert options.initial_delay is None
    assert options.max_delay is None


def test_env_overrides_attempts_and_timeout(tmp_path):
    path = write(tmp_path, "version = 1\nattempts = 3\ntimeout_ms = 300000\n")
    config = load_resilience(
        path, env={ATTEMPTS_VAR: "5", TIMEOUT_MS_VAR: "120000"}
    )
    assert config.attempts == 5
    assert config.timeout_ms == 120000


def test_an_env_override_is_validated_like_a_file_value(tmp_path):
    path = write(tmp_path, "version = 1\nattempts = 3\ntimeout_ms = 300000\n")
    with pytest.raises(ResilienceConfigError):
        load_resilience(path, env={ATTEMPTS_VAR: "0"})


def test_a_non_numeric_override_is_rejected(tmp_path):
    path = write(tmp_path, "version = 1\nattempts = 3\ntimeout_ms = 300000\n")
    with pytest.raises(ResilienceConfigError, match=ATTEMPTS_VAR):
        load_resilience(path, env={ATTEMPTS_VAR: "lots"})


def test_a_set_but_empty_override_is_a_mistake_not_a_no_op(tmp_path):
    path = write(tmp_path, "version = 1\nattempts = 3\ntimeout_ms = 300000\n")
    with pytest.raises(ResilienceConfigError, match="set but empty"):
        load_resilience(path, env={JITTER_VAR: ""})


def test_a_missing_file_fails_closed(tmp_path):
    with pytest.raises(ResilienceConfigError):
        load_resilience(tmp_path / "gone.toml", env={})


def test_an_unknown_key_fails_closed(tmp_path):
    path = write(
        tmp_path, "version = 1\nattempts = 3\ntimeout_ms = 300000\nbudget = 9\n"
    )
    with pytest.raises(ResilienceConfigError):
        load_resilience(path, env={})


def test_a_zero_timeout_is_rejected():
    with pytest.raises(ValueError):
        ResilienceConfig(version=1, attempts=3, timeout_ms=0)
