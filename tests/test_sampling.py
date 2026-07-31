"""Per-tier sampling config: the knob eval and production share.

These are unit tests of the config layer: loading, the ``STRIDE_SAMPLING_*``
override surface, the three-way split of a resolved tier's params at the point
of use, and the ``resolve_sampling`` sibling of ``resolve_model``. All
fail-closed, mirroring ``test_model_tiers``. Binding the resolver onto each
graph node is asserted in ``test_graph``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from stride_service.model_tiers import ModelTierConfig, TierSelection
from stride_service.sampling import (
    OFFERED_PARAMS,
    SUPPORTED_VERSION,
    SamplingConfig,
    SamplingConfigError,
    TierSampling,
    env_var_for,
    load_sampling,
    make_resolve_sampling,
    sampling_fingerprint,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLING_PATH = PROJECT_ROOT / "config" / "sampling.toml"


def tier_block(tier, body=""):
    return f"[tiers.{tier}]\ntemperature = 0.0\ncandidate_count = 1\n{body}"


def config_toml(version=SUPPORTED_VERSION, base_body="", strong_body="", extra=""):
    return (
        f"version = {version}\n\n"
        f"{tier_block('base', base_body)}\n"
        f"{tier_block('strong', strong_body)}\n"
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
        assert config.version == SUPPORTED_VERSION
        assert set(config.tiers) == {"base", "strong"}

    def test_defaults_to_greedy_decoding(self):
        config = load_sampling(SAMPLING_PATH, env={})
        for tier in ("base", "strong"):
            sampling = config.for_tier(tier)
            assert sampling.temperature == 0.0
            assert sampling.candidate_count == 1
            # Everything with no verified per-tier constant stays unset.
            assert sampling.top_p is None
            assert sampling.seed is None
            assert sampling.presence_penalty is None
            assert sampling.frequency_penalty is None
            assert sampling.thinking is None

    def test_max_output_tokens_is_pinned_not_silent(self):
        # Silence is not neutral: Anthropic derives a 5,120-8,192 cap only when
        # the caller says nothing, so an unset value means the vendor decides.
        config = load_sampling(SAMPLING_PATH, env={})
        for tier in ("base", "strong"):
            assert config.for_tier(tier).max_output_tokens is not None


class TestVersionCutover:
    def test_v1_flat_shape_rejected(self, config_path):
        path = config_path("version = 1\ntemperature = 0.0\n")
        with pytest.raises(SamplingConfigError):
            load_sampling(path, env={})

    def test_version_2_file_fails_closed_with_no_shim(self, config_path):
        path = config_path(config_toml(version=2))
        with pytest.raises(SamplingConfigError, match="unsupported version 2"):
            load_sampling(path, env={})

    def test_a_version_2_top_k_line_is_rejected_not_ignored(self, config_path):
        # top_k left the surface because the build-time gate provably cannot
        # cover it; a leftover line must fail loudly rather than be dropped.
        path = config_path(config_toml(base_body="top_k = 40\n"))
        with pytest.raises(SamplingConfigError):
            load_sampling(path, env={})

    def test_missing_version_rejected(self, config_path):
        text = config_toml().replace(f"version = {SUPPORTED_VERSION}\n", "")
        with pytest.raises(SamplingConfigError):
            load_sampling(config_path(text), env={})


class TestFileValidation:
    def test_unknown_key_in_tier_rejected(self, config_path):
        path = config_path(config_toml(base_body="thinking_budget = 1024\n"))
        with pytest.raises(SamplingConfigError):
            load_sampling(path, env={})

    def test_unknown_tier_name_rejected(self, config_path):
        path = config_path(config_toml() + tier_block("turbo"))
        with pytest.raises(SamplingConfigError):
            load_sampling(path, env={})

    def test_missing_tier_rejected(self, config_path):
        text = f"version = {SUPPORTED_VERSION}\n\n" + tier_block("base")
        with pytest.raises(SamplingConfigError, match="missing entries"):
            load_sampling(config_path(text), env={})

    def test_extra_top_level_key_rejected(self, config_path):
        path = config_path(config_toml(extra='fallback = "auto"\n'))
        with pytest.raises(SamplingConfigError):
            load_sampling(path, env={})

    def test_out_of_range_temperature_rejected(self, config_path):
        path = config_path(config_toml(base_body="temperature = 7\n"))
        with pytest.raises(SamplingConfigError):
            load_sampling(path, env={})

    def test_out_of_range_top_p_rejected(self, config_path):
        path = config_path(config_toml(strong_body="top_p = 1.5\n"))
        with pytest.raises(SamplingConfigError):
            load_sampling(path, env={})

    def test_zero_max_output_tokens_rejected(self, config_path):
        path = config_path(config_toml(base_body="max_output_tokens = 0\n"))
        with pytest.raises(SamplingConfigError):
            load_sampling(path, env={})

    def test_no_ceiling_is_mirrored_for_max_output_tokens(self, config_path):
        # The ceiling is a per-(vendor, model) fact; mirroring one here is the
        # mistake Vendor.supported was. A large value loads; the provider is
        # what rejects it.
        path = config_path(config_toml(base_body="max_output_tokens = 200000\n"))
        assert load_sampling(path, env={}).for_tier("base").max_output_tokens == 200000

    def test_missing_file_rejected(self, tmp_path):
        with pytest.raises(SamplingConfigError, match="cannot be read"):
            load_sampling(tmp_path / "absent.toml", env={})

    def test_invalid_toml_rejected(self, config_path):
        with pytest.raises(SamplingConfigError, match="invalid TOML"):
            load_sampling(config_path("version = = 2"), env={})

    def test_config_is_frozen(self, config_path):
        config = load_sampling(config_path(config_toml()), env={})
        with pytest.raises(Exception):
            config.version = 99


class TestCandidateCountReserved:
    def test_candidate_count_must_be_one(self, config_path):
        text = config_toml().replace("candidate_count = 1", "candidate_count = 3")
        with pytest.raises(SamplingConfigError, match="candidate_count must be 1"):
            load_sampling(config_path(text), env={})

    def test_candidate_count_not_overridable_by_env(self, config_path):
        env = {"STRIDE_SAMPLING_BASE_CANDIDATE_COUNT": "3"}
        with pytest.raises(SamplingConfigError, match="not overridable"):
            load_sampling(config_path(config_toml()), env=env)


class TestThinkingEnum:
    """The uniform reasoning surface, and why its value check lives here."""

    @pytest.mark.parametrize("effort", ["low", "medium", "high"])
    def test_the_three_efforts_load(self, config_path, effort):
        path = config_path(config_toml(base_body=f'thinking = "{effort}"\n'))
        assert load_sampling(path, env={}).for_tier("base").thinking == effort

    @pytest.mark.parametrize("value", ["off", "auto", "none", "banana", "1024"])
    def test_everything_else_is_rejected_here(self, config_path, value):
        # The provider gate cannot catch these: reasoning_effort="banana"
        # PASSES get_optional_params on o3, and gemini + "none" passes as
        # thinkingBudget: 0 and then 400s at request time. So a pydantic
        # Literal is the only thing standing between the config and a silent
        # wrong the fingerprint would attest to.
        path = config_path(config_toml(base_body=f'thinking = "{value}"\n'))
        with pytest.raises(SamplingConfigError):
            load_sampling(path, env={})

    def test_there_are_no_per_tier_legal_ranges_left(self, config_path):
        # Version 2 rejected "off" on pro and allowed it on flash. The enum is
        # uniform, so both tiers accept exactly the same three values.
        text = config_toml(base_body='thinking = "high"\n', strong_body='thinking = "high"\n')
        config = load_sampling(config_path(text), env={})
        assert config.for_tier("base").thinking == "high"
        assert config.for_tier("strong").thinking == "high"


class TestParamSplit:
    """A resolved tier is split three ways, because ADK carries only some of it."""

    def test_seed_and_reasoning_ride_the_constructor(self):
        sampling = TierSampling(temperature=0.0, seed=7, thinking="low")
        assert sampling.constructor_kwargs() == {"seed": 7, "reasoning_effort": "low"}

    def test_unset_constructor_params_are_omitted_entirely(self):
        assert TierSampling(temperature=0.0).constructor_kwargs() == {}

    def test_the_generate_content_config_carries_neither(self):
        # Put on the config instead, they would vanish silently while the
        # fingerprint went on attesting to a seed the request never carried.
        gcc = TierSampling(
            temperature=0.3, top_p=0.9, seed=7, thinking="high"
        ).to_generate_content_config()
        assert gcc.temperature == 0.3
        assert gcc.top_p == 0.9
        assert gcc.seed is None
        assert gcc.thinking_config is None

    def test_gate_params_name_things_as_litellm_names_them(self):
        params = TierSampling(
            temperature=0.0, max_output_tokens=8192, thinking="low"
        ).gate_params()
        assert params == {
            "temperature": 0.0,
            "max_tokens": 8192,
            "n": 1,
            "reasoning_effort": "low",
        }

    def test_gate_params_omit_unset_rather_than_sending_none(self):
        # The gate asks whether a request would be accepted; a param the
        # request will not carry is not part of it.
        assert "seed" not in TierSampling(temperature=0.0).gate_params()


class TestEnvOverrides:
    def test_offered_params_apply_per_tier(self, config_path):
        env = {
            env_var_for("strong", "temperature"): "0.7",
            env_var_for("base", "top_p"): "0.8",
            env_var_for("strong", "seed"): "42",
            env_var_for("base", "thinking"): "medium",
            env_var_for("strong", "max_output_tokens"): "4096",
        }
        config = load_sampling(config_path(config_toml()), env=env)
        assert config.for_tier("strong").temperature == 0.7
        assert config.for_tier("base").top_p == 0.8
        assert config.for_tier("strong").seed == 42
        assert config.for_tier("base").thinking == "medium"
        assert config.for_tier("strong").max_output_tokens == 4096
        # An untouched tier keeps its file value.
        assert config.for_tier("base").temperature == 0.0

    def test_override_validated_like_file_value(self, config_path):
        env = {env_var_for("base", "temperature"): "7"}
        with pytest.raises(SamplingConfigError):
            load_sampling(config_path(config_toml()), env=env)

    def test_override_thinking_enum_checked(self, config_path):
        env = {env_var_for("strong", "thinking"): "off"}
        with pytest.raises(SamplingConfigError):
            load_sampling(config_path(config_toml()), env=env)

    def test_non_numeric_override_rejected(self, config_path):
        env = {env_var_for("base", "temperature"): "hot"}
        with pytest.raises(SamplingConfigError, match="not a number"):
            load_sampling(config_path(config_toml()), env=env)

    def test_set_but_empty_rejected(self, config_path):
        env = {env_var_for("base", "temperature"): "  "}
        with pytest.raises(SamplingConfigError, match="set but empty"):
            load_sampling(config_path(config_toml()), env=env)

    @pytest.mark.parametrize("param", ["top_k", "presence_penalty", "candidate_count"])
    def test_reserved_and_removed_params_not_overridable(self, config_path, param):
        env = {env_var_for("base", param): "1"}
        with pytest.raises(SamplingConfigError, match="not overridable"):
            load_sampling(config_path(config_toml()), env=env)

    def test_forbidden_param_not_overridable(self, config_path):
        env = {"STRIDE_SAMPLING_STRONG_RESPONSE_SCHEMA": "{}"}
        with pytest.raises(SamplingConfigError, match="not overridable"):
            load_sampling(config_path(config_toml()), env=env)

    def test_unknown_tier_in_var_rejected(self, config_path):
        env = {"STRIDE_SAMPLING_TURBO_TEMPERATURE": "0.5"}
        with pytest.raises(SamplingConfigError, match="unknown tier"):
            load_sampling(config_path(config_toml()), env=env)

    def test_unrelated_env_vars_ignored(self, config_path):
        env = {"PATH": "/usr/bin", "STRIDE_MODEL_STRONG_MODEL": "gemini-2.5-pro"}
        config = load_sampling(config_path(config_toml()), env=env)
        assert config.for_tier("strong").temperature == 0.0

    def test_env_var_naming(self):
        assert env_var_for("base", "top_p") == "STRIDE_SAMPLING_BASE_TOP_P"
        assert (
            env_var_for("strong", "temperature") == "STRIDE_SAMPLING_STRONG_TEMPERATURE"
        )

    def test_os_environ_is_default_env(self, config_path, monkeypatch):
        monkeypatch.setenv(env_var_for("strong", "temperature"), "0.9")
        config = load_sampling(config_path(config_toml()))
        assert config.for_tier("strong").temperature == 0.9

    def test_offered_surface_is_exactly_the_offered_params(self):
        assert OFFERED_PARAMS == (
            "temperature",
            "top_p",
            "seed",
            "thinking",
            "max_output_tokens",
        )


class TestResolveSampling:
    def test_resolves_via_tier_map(self):
        config = load_sampling(SAMPLING_PATH, env={})
        tiers = ModelTierConfig(
            version=3,
            tiers={
                "base": TierSelection(vendor="vertex", model="gemini-2.5-flash"),
                "strong": TierSelection(vendor="vertex", model="gemini-2.5-pro"),
            },
            nodes={
                node: "base" if node in ("extract", "repair") else "strong"
                for node in _all_llm_nodes()
            },
        )
        resolve = make_resolve_sampling(config, tiers.resolve_tier)
        assert resolve("extract") is config.for_tier("base")
        assert resolve("critic") is config.for_tier("strong")

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
        SamplingConfig(version=SUPPORTED_VERSION, tiers={"base": TierSampling()})


class TestSamplingFingerprint:
    """The generation-identity hash."""

    def test_is_a_sha256_hex_digest(self):
        fp = sampling_fingerprint(
            "vertex_ai/gemini-2.5-pro", TierSampling(temperature=0.0)
        )
        assert re.fullmatch(r"[0-9a-f]{64}", fp)

    def test_is_deterministic(self):
        sampling = TierSampling(temperature=0.0, seed=7)
        first = sampling_fingerprint("m", sampling)
        assert first == sampling_fingerprint("m", sampling)

    def test_same_sampling_different_served_build_diverges(self):
        # Two nodes on one tier served different builds must differ — the drift
        # the gate exists to catch.
        sampling = TierSampling(temperature=0.0)
        assert sampling_fingerprint(
            "vertex_ai/gemini-2.5-pro", sampling
        ) != sampling_fingerprint("vertex_ai/gemini-2.5-pro-002", sampling)

    def test_the_same_served_build_under_two_vendors_diverges(self):
        # The served identifier carries no vendor, and Vertex-hosted Claude and
        # Anthropic-direct return through an identical transformation — so
        # without the prefix a manifest blessed on one would certify the other.
        sampling = TierSampling(temperature=0.0)
        assert sampling_fingerprint(
            "vertex_ai/claude-sonnet-4-5", sampling
        ) != sampling_fingerprint("anthropic/claude-sonnet-4-5", sampling)

    def test_same_model_different_sampling_diverges(self):
        assert sampling_fingerprint(
            "m", TierSampling(temperature=0.0)
        ) != sampling_fingerprint("m", TierSampling(temperature=1.0))

    def test_recomputable_from_the_recorded_clear_values(self):
        # The report stores TierSampling.model_dump(); reconstructing from that
        # dict must reproduce the very hash stamped on the NodeRun.
        sampling = TierSampling(temperature=0.0, seed=3, thinking="low")
        recomputed = sampling_fingerprint("m", TierSampling(**sampling.model_dump()))
        assert recomputed == sampling_fingerprint("m", sampling)
