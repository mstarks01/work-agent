"""Tests for model-tier config loading, env overrides, and pin validation."""

from pathlib import Path

import pytest

from stride_service.model_tiers import (
    ANALYST_NODES,
    LLM_NODES,
    TIER_NAMES,
    ModelConfigError,
    ModelTierConfig,
    env_var_for,
    load_model_tiers,
    validate_model_string,
)

REPO_CONFIG = Path(__file__).parents[1] / "config" / "model_tiers.toml"

FLASH = "gemini-2.5-flash-002"
PRO = "gemini-2.5-pro-002"


def config_toml(flash=FLASH, pro=PRO, nodes=None, version=1):
    if nodes is None:
        nodes = {node: "flash" if node in ("extract", "repair") else "pro"
                 for node in LLM_NODES}
    node_lines = "\n".join(f'"{node}" = "{tier}"' for node, tier in nodes.items())
    return (
        f"version = {version}\n\n"
        f'[tiers]\nflash = "{flash}"\npro = "{pro}"\n\n'
        f"[nodes]\n{node_lines}\n"
    )


@pytest.fixture
def config_path(tmp_path):
    def write(text):
        path = tmp_path / "model_tiers.toml"
        path.write_text(text)
        return path

    return write


class TestNodeInventory:
    def test_llm_nodes_are_bookends_plus_analysts_plus_critic(self):
        assert LLM_NODES[:2] == ("extract", "repair")
        assert LLM_NODES[-1] == "critic"
        assert len(ANALYST_NODES) == 6
        assert all(node.startswith("analyst/") for node in ANALYST_NODES)

    def test_exactly_two_tiers(self):
        assert TIER_NAMES == ("flash", "pro")


class TestRepoConfig:
    def test_shipped_config_loads_and_covers_all_llm_nodes(self):
        config = load_model_tiers(REPO_CONFIG, env={})
        assert set(config.nodes) == set(LLM_NODES)

    def test_shipped_config_tier_assignment(self):
        config = load_model_tiers(REPO_CONFIG, env={})
        assert config.nodes["extract"] == "flash"
        assert config.nodes["repair"] == "flash"
        assert config.nodes["critic"] == "pro"
        for node in ANALYST_NODES:
            assert config.nodes[node] == "pro"


class TestResolution:
    def test_resolve_model_returns_tier_string(self, config_path):
        config = load_model_tiers(config_path(config_toml()), env={})
        assert config.resolve_model("extract") == FLASH
        assert config.resolve_model("analyst/spoofing") == PRO
        assert config.resolve_model("critic") == PRO

    def test_resolve_model_unknown_node_raises(self, config_path):
        config = load_model_tiers(config_path(config_toml()), env={})
        with pytest.raises(ModelConfigError, match="unknown LLM node"):
            config.resolve_model("assemble")


class TestEnvOverrides:
    def test_env_overrides_one_tier_only(self, config_path):
        env = {env_var_for("pro"): "gemini-3.0-pro-001"}
        config = load_model_tiers(config_path(config_toml()), env=env)
        assert config.resolve_model("critic") == "gemini-3.0-pro-001"
        assert config.resolve_model("extract") == FLASH

    def test_env_alias_rejected(self, config_path):
        env = {env_var_for("flash"): "gemini-2.5-flash-latest"}
        with pytest.raises(ModelConfigError, match="STRIDE_MODEL_FLASH"):
            load_model_tiers(config_path(config_toml()), env=env)

    def test_env_unpinned_rejected(self, config_path):
        env = {env_var_for("pro"): "gemini-2.5-pro"}
        with pytest.raises(ModelConfigError, match="not a pinned"):
            load_model_tiers(config_path(config_toml()), env=env)

    def test_env_set_but_empty_rejected(self, config_path):
        env = {env_var_for("flash"): "  "}
        with pytest.raises(ModelConfigError, match="set but empty"):
            load_model_tiers(config_path(config_toml()), env=env)

    def test_env_var_names(self):
        assert env_var_for("flash") == "STRIDE_MODEL_FLASH"
        assert env_var_for("pro") == "STRIDE_MODEL_PRO"


class TestPinValidation:
    @pytest.mark.parametrize(
        "value",
        ["gemini-2.5-pro-latest", "gemini-2.5-pro", "gemini-flash-preview-05"],
    )
    def test_aliases_and_unpinned_rejected(self, value):
        with pytest.raises(ModelConfigError):
            validate_model_string(value, source="tiers.pro")

    @pytest.mark.parametrize("value", ["gemini-2.5-pro-002", "gemini-3.0-flash-1001"])
    def test_pinned_accepted(self, value):
        assert validate_model_string(value, source="tiers.pro") == value

    def test_alias_in_file_rejected(self, config_path):
        path = config_path(config_toml(pro="gemini-2.5-pro-latest"))
        with pytest.raises(ModelConfigError, match="-latest"):
            load_model_tiers(path, env={})


class TestFileValidation:
    def test_unknown_node_rejected(self, config_path):
        nodes = {node: "pro" for node in LLM_NODES} | {"assemble": "flash"}
        with pytest.raises(ModelConfigError, match="unknown node"):
            load_model_tiers(config_path(config_toml(nodes=nodes)), env={})

    def test_missing_node_rejected(self, config_path):
        nodes = {node: "pro" for node in LLM_NODES if node != "critic"}
        with pytest.raises(ModelConfigError, match="missing entries"):
            load_model_tiers(config_path(config_toml(nodes=nodes)), env={})

    def test_unknown_tier_name_rejected(self, config_path):
        nodes = {node: "pro" for node in LLM_NODES} | {"extract": "turbo"}
        with pytest.raises(ModelConfigError):
            load_model_tiers(config_path(config_toml(nodes=nodes)), env={})

    def test_missing_tier_rejected(self, config_path):
        text = config_toml().replace(f'pro = "{PRO}"\n', "")
        with pytest.raises(ModelConfigError):
            load_model_tiers(config_path(text), env={})

    def test_missing_version_rejected(self, config_path):
        text = config_toml().replace("version = 1\n", "")
        with pytest.raises(ModelConfigError):
            load_model_tiers(config_path(text), env={})

    def test_extra_top_level_key_rejected(self, config_path):
        with pytest.raises(ModelConfigError):
            load_model_tiers(config_path(config_toml() + 'fallback = "auto"\n'), env={})

    def test_invalid_toml_rejected(self, config_path):
        with pytest.raises(ModelConfigError, match="invalid TOML"):
            load_model_tiers(config_path("version = [unclosed"), env={})

    def test_config_is_frozen(self, config_path):
        config = load_model_tiers(config_path(config_toml()), env={})
        with pytest.raises(Exception):
            config.version = 2

    def test_os_environ_is_default_env(self, config_path, monkeypatch):
        monkeypatch.setenv("STRIDE_MODEL_PRO", "gemini-3.0-pro-001")
        config = load_model_tiers(config_path(config_toml()))
        assert config.resolve_model("critic") == "gemini-3.0-pro-001"


def test_direct_construction_validates_completeness():
    with pytest.raises(ValueError, match="nodes missing entries"):
        ModelTierConfig(version=1, tiers={"flash": FLASH, "pro": PRO}, nodes={})
