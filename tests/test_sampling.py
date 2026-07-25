"""Per-tier sampling config: the knob eval and production share (decision 15).

These are unit tests of the config layer (ticket 05): loading, per-class
thinking resolution, the ``STRIDE_SAMPLING_*`` override surface, and the
``resolve_sampling`` sibling of ``resolve_model``. All fail-closed, mirroring
``test_model_tiers``. Binding the resolver onto each graph node is ticket 06,
and its per-node assertions live with that ticket, not here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from stride_service.model_tiers import ModelTierConfig
from stride_service.sampling import (
    MODEL_OUTPUT_CEILING,
    OFFERED_PARAMS,
    SamplingConfig,
    SamplingConfigError,
    TierSampling,
    env_var_for,
    load_sampling,
    make_resolve_sampling,
    resolve_thinking,
    sampling_fingerprint,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLING_PATH = PROJECT_ROOT / "config" / "sampling.toml"


def tier_block(tier, body=""):
    return f"[tiers.{tier}]\ntemperature = 0.0\ncandidate_count = 1\n{body}"


def config_toml(version=2, flash_body="", pro_body="", extra=""):
    return (
        f"version = {version}\n\n"
        f"{tier_block('flash', flash_body)}\n"
        f"{tier_block('pro', pro_body)}\n"
        f"{extra}"
    )


@pytest.fixture
def config_path(tmp_path):
    def write(text):
        path = tmp_path / "sampling.toml"
        path.write_text(text)
        return path

    return write


class TestShippedConfig:
    def test_loads_and_covers_both_tiers(self):
        config = load_sampling(SAMPLING_PATH, env={})
        assert config.version == 2
        assert set(config.tiers) == {"flash", "pro"}

    def test_defaults_to_greedy_decoding_behaviour_unchanged(self):
        config = load_sampling(SAMPLING_PATH, env={})
        for tier in ("flash", "pro"):
            sampling = config.for_tier(tier)
            assert sampling.temperature == 0.0
            assert sampling.candidate_count == 1
            # Everything ticket 04 could not verify stays unset, not invented.
            assert sampling.top_p is None
            assert sampling.top_k is None
            assert sampling.seed is None
            assert sampling.max_output_tokens is None
            assert sampling.presence_penalty is None
            assert sampling.frequency_penalty is None
            # Unset thinking leaves the model's preset budget, not dynamic.
            assert sampling.thinking_budget is None


class TestVersionCutover:
    def test_v1_flat_shape_rejected(self, config_path):
        path = config_path("version = 1\ntemperature = 0.0\n")
        with pytest.raises(SamplingConfigError):
            load_sampling(path, env={})

    def test_wrong_version_number_rejected(self, config_path):
        path = config_path(config_toml(version=3))
        with pytest.raises(SamplingConfigError, match="unsupported version 3"):
            load_sampling(path, env={})

    def test_missing_version_rejected(self, config_path):
        text = config_toml().replace("version = 2\n", "")
        with pytest.raises(SamplingConfigError):
            load_sampling(config_path(text), env={})


class TestFileValidation:
    def test_unknown_key_in_tier_rejected(self, config_path):
        path = config_path(config_toml(flash_body="thinking_budget = 1024\n"))
        with pytest.raises(SamplingConfigError):
            load_sampling(path, env={})

    def test_unknown_tier_name_rejected(self, config_path):
        path = config_path(config_toml() + tier_block("turbo"))
        with pytest.raises(SamplingConfigError):
            load_sampling(path, env={})

    def test_missing_tier_rejected(self, config_path):
        text = "version = 2\n\n" + tier_block("flash")
        with pytest.raises(SamplingConfigError, match="missing entries"):
            load_sampling(config_path(text), env={})

    def test_extra_top_level_key_rejected(self, config_path):
        path = config_path(config_toml(extra='fallback = "auto"\n'))
        with pytest.raises(SamplingConfigError):
            load_sampling(path, env={})

    def test_out_of_range_temperature_rejected(self, config_path):
        path = config_path(config_toml(flash_body="temperature = 7\n"))
        with pytest.raises(SamplingConfigError):
            load_sampling(path, env={})

    def test_out_of_range_top_p_rejected(self, config_path):
        path = config_path(config_toml(pro_body="top_p = 1.5\n"))
        with pytest.raises(SamplingConfigError):
            load_sampling(path, env={})

    def test_max_output_tokens_over_ceiling_rejected(self, config_path):
        over = MODEL_OUTPUT_CEILING + 1
        path = config_path(config_toml(flash_body=f"max_output_tokens = {over}\n"))
        with pytest.raises(SamplingConfigError):
            load_sampling(path, env={})

    def test_missing_file_rejected(self, tmp_path):
        with pytest.raises(SamplingConfigError, match="cannot be read"):
            load_sampling(tmp_path / "absent.toml", env={})

    def test_invalid_toml_rejected(self, config_path):
        with pytest.raises(SamplingConfigError, match="invalid TOML"):
            load_sampling(config_path("version = = 2"), env={})

    def test_config_is_frozen(self, config_path):
        config = load_sampling(config_path(config_toml()), env={})
        with pytest.raises(Exception):
            config.version = 3


class TestCandidateCountReserved:
    def test_candidate_count_must_be_one(self, config_path):
        text = config_toml().replace("candidate_count = 1", "candidate_count = 3")
        with pytest.raises(SamplingConfigError, match="candidate_count must be 1"):
            load_sampling(config_path(text), env={})

    def test_candidate_count_not_overridable_by_env(self, config_path):
        env = {"STRIDE_SAMPLING_FLASH_CANDIDATE_COUNT": "3"}
        with pytest.raises(SamplingConfigError, match="not overridable"):
            load_sampling(config_path(config_toml()), env=env)


class TestThinkingResolution:
    def test_unset_is_model_preset(self):
        assert resolve_thinking(None, "flash") is None
        assert resolve_thinking(None, "pro") is None

    def test_auto_is_dynamic(self):
        assert resolve_thinking("auto", "flash") == -1
        assert resolve_thinking("auto", "pro") == -1

    def test_flash_off_disables(self):
        assert resolve_thinking("off", "flash") == 0

    def test_pro_off_rejected(self):
        with pytest.raises(SamplingConfigError, match="cannot be 'off'"):
            resolve_thinking("off", "pro")

    def test_in_range_int_kept(self):
        assert resolve_thinking(1024, "flash") == 1024
        assert resolve_thinking(1024, "pro") == 1024

    @pytest.mark.parametrize("tier,budget", [("flash", 24_577), ("pro", 127)])
    def test_out_of_class_range_rejected(self, tier, budget):
        with pytest.raises(SamplingConfigError, match="out of range"):
            resolve_thinking(budget, tier)

    def test_file_thinking_resolves_to_budget(self, config_path):
        path = config_path(config_toml(flash_body='thinking = "off"\n'))
        config = load_sampling(path, env={})
        assert config.for_tier("flash").thinking_budget == 0

    def test_pro_off_in_file_fails_closed(self, config_path):
        path = config_path(config_toml(pro_body='thinking = "off"\n'))
        with pytest.raises(SamplingConfigError, match="cannot be 'off'"):
            load_sampling(path, env={})


class TestGenerateContentConfig:
    def test_unset_thinking_sends_no_thinking_config(self):
        config = TierSampling(temperature=0.0)
        gcc = config.to_generate_content_config()
        assert gcc.temperature == 0.0
        assert gcc.candidate_count == 1
        assert gcc.thinking_config is None

    def test_set_budget_sends_thinking_config(self):
        gcc = TierSampling(thinking_budget=1024).to_generate_content_config()
        assert gcc.thinking_config is not None
        assert gcc.thinking_config.thinking_budget == 1024

    def test_all_offered_params_map_through(self):
        gcc = TierSampling(
            temperature=0.3, top_p=0.9, seed=7, thinking_budget=-1
        ).to_generate_content_config()
        assert gcc.temperature == 0.3
        assert gcc.top_p == 0.9
        assert gcc.seed == 7
        assert gcc.thinking_config.thinking_budget == -1


class TestEnvOverrides:
    def test_offered_params_apply_per_tier(self, config_path):
        env = {
            env_var_for("pro", "temperature"): "0.7",
            env_var_for("flash", "top_p"): "0.8",
            env_var_for("pro", "seed"): "42",
            env_var_for("flash", "thinking"): "off",
        }
        config = load_sampling(config_path(config_toml()), env=env)
        assert config.for_tier("pro").temperature == 0.7
        assert config.for_tier("flash").top_p == 0.8
        assert config.for_tier("pro").seed == 42
        assert config.for_tier("flash").thinking_budget == 0
        # An untouched tier keeps its file value.
        assert config.for_tier("flash").temperature == 0.0

    def test_override_validated_like_file_value(self, config_path):
        env = {env_var_for("flash", "temperature"): "7"}
        with pytest.raises(SamplingConfigError):
            load_sampling(config_path(config_toml()), env=env)

    def test_override_thinking_class_checked(self, config_path):
        env = {env_var_for("pro", "thinking"): "off"}
        with pytest.raises(SamplingConfigError, match="cannot be 'off'"):
            load_sampling(config_path(config_toml()), env=env)

    def test_non_numeric_override_rejected(self, config_path):
        env = {env_var_for("flash", "temperature"): "hot"}
        with pytest.raises(SamplingConfigError, match="not a number"):
            load_sampling(config_path(config_toml()), env=env)

    def test_set_but_empty_rejected(self, config_path):
        env = {env_var_for("flash", "temperature"): "  "}
        with pytest.raises(SamplingConfigError, match="set but empty"):
            load_sampling(config_path(config_toml()), env=env)

    @pytest.mark.parametrize(
        "param", ["top_k", "max_output_tokens", "presence_penalty"]
    )
    def test_reserved_param_not_overridable(self, config_path, param):
        env = {env_var_for("flash", param): "1"}
        with pytest.raises(SamplingConfigError, match="not overridable"):
            load_sampling(config_path(config_toml()), env=env)

    def test_forbidden_param_not_overridable(self, config_path):
        env = {"STRIDE_SAMPLING_PRO_RESPONSE_SCHEMA": "{}"}
        with pytest.raises(SamplingConfigError, match="not overridable"):
            load_sampling(config_path(config_toml()), env=env)

    def test_unknown_tier_in_var_rejected(self, config_path):
        env = {"STRIDE_SAMPLING_TURBO_TEMPERATURE": "0.5"}
        with pytest.raises(SamplingConfigError, match="unknown tier"):
            load_sampling(config_path(config_toml()), env=env)

    def test_unrelated_env_vars_ignored(self, config_path):
        env = {"PATH": "/usr/bin", "STRIDE_MODEL_PRO": "gemini-2.5-pro"}
        config = load_sampling(config_path(config_toml()), env=env)
        assert config.for_tier("pro").temperature == 0.0

    def test_env_var_naming(self):
        assert env_var_for("flash", "top_p") == "STRIDE_SAMPLING_FLASH_TOP_P"
        assert env_var_for("pro", "temperature") == "STRIDE_SAMPLING_PRO_TEMPERATURE"

    def test_os_environ_is_default_env(self, config_path, monkeypatch):
        monkeypatch.setenv(env_var_for("pro", "temperature"), "0.9")
        config = load_sampling(config_path(config_toml()))
        assert config.for_tier("pro").temperature == 0.9

    def test_offered_surface_is_exactly_the_offered_params(self):
        assert OFFERED_PARAMS == ("temperature", "top_p", "seed", "thinking")


class TestResolveSampling:
    def test_resolves_via_tier_map(self):
        config = load_sampling(SAMPLING_PATH, env={})
        tiers = ModelTierConfig(
            version=2,
            tiers={"flash": "gemini-2.5-flash", "pro": "gemini-2.5-pro"},
            nodes={node: "flash" if node in ("extract", "repair") else "pro"
                   for node in _all_llm_nodes()},
        )
        resolve = make_resolve_sampling(config, tiers.resolve_tier)
        assert resolve("extract") is config.for_tier("flash")
        assert resolve("critic") is config.for_tier("pro")

    def test_unknown_node_propagates(self):
        config = load_sampling(SAMPLING_PATH, env={})

        def resolve_tier(node):
            raise KeyError(node)

        resolve = make_resolve_sampling(config, resolve_tier)
        with pytest.raises(KeyError):
            resolve("nope")


def _all_llm_nodes():
    from stride_service.model_tiers import LLM_NODES

    return LLM_NODES


def test_direct_construction_requires_both_tiers():
    with pytest.raises(ValueError, match="missing entries"):
        SamplingConfig(version=2, tiers={"flash": TierSampling()})


class TestSamplingFingerprint:
    """The generation-identity hash (ticket 07 / ticket 03 §1)."""

    def test_is_a_sha256_hex_digest(self):
        fp = sampling_fingerprint("gemini-2.5-pro", TierSampling(temperature=0.0))
        assert re.fullmatch(r"[0-9a-f]{64}", fp)

    def test_is_deterministic(self):
        sampling = TierSampling(temperature=0.0, seed=7)
        first = sampling_fingerprint("m", sampling)
        assert first == sampling_fingerprint("m", sampling)

    def test_same_sampling_different_served_model_diverges(self):
        # Two nodes on one tier served different builds must differ — the drift
        # the gate exists to catch (ticket 026 keys on the served model).
        sampling = TierSampling(temperature=0.0)
        assert sampling_fingerprint("gemini-2.5-pro", sampling) != sampling_fingerprint(
            "gemini-2.5-pro-002", sampling
        )

    def test_same_model_different_sampling_diverges(self):
        assert sampling_fingerprint(
            "m", TierSampling(temperature=0.0)
        ) != sampling_fingerprint("m", TierSampling(temperature=1.0))

    def test_recomputable_from_the_recorded_clear_values(self):
        # The report stores TierSampling.model_dump(); reconstructing from that
        # dict must reproduce the very hash stamped on the NodeRun.
        sampling = TierSampling(temperature=0.0, seed=3, thinking_budget=-1)
        recomputed = sampling_fingerprint("m", TierSampling(**sampling.model_dump()))
        assert recomputed == sampling_fingerprint("m", sampling)
