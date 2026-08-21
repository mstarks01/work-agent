"""Tests for model-tier config loading, env overrides, and pin validation."""

from pathlib import Path
from typing import ClassVar

import pytest
from pydantic import ValidationError

from stride_service.model_tiers import (
    FRAMEWORK_NODES,
    LLM_NODES,
    SUPPORTED_VERSION,
    TIER_NAMES,
    ModelConfigError,
    ModelTierConfig,
    TierSelection,
    env_vars_for,
    load_model_tiers,
    validate_model_string,
)
from stride_service.report import FRAMEWORK_NAMES
from stride_service.vendors import VENDOR_NAMES

REPO_CONFIG = Path(__file__).parents[1] / "config" / "model_tiers.toml"

BASE = "gemini-2.5-flash"
STRONG = "gemini-2.5-pro"
VENDOR = "vertex"


def config_toml(
    base=BASE,
    strong=STRONG,
    base_vendor=VENDOR,
    strong_vendor=VENDOR,
    nodes=None,
    version=SUPPORTED_VERSION,
):
    if nodes is None:
        nodes = {
            node: "base" if node in ("extract", "repair") else "strong"
            for node in LLM_NODES
        }
    node_lines = "\n".join(f'"{node}" = "{tier}"' for node, tier in nodes.items())
    return (
        f"version = {version}\n\n"
        f'[tiers.base]\nvendor = "{base_vendor}"\nmodel = "{base}"\n\n'
        f'[tiers.strong]\nvendor = "{strong_vendor}"\nmodel = "{strong}"\n\n'
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
    def test_llm_nodes_are_the_bookends_plus_three_keys_per_framework(self):
        """The two shared extraction nodes, then each package's own three.

        Six ``analyze/<category>`` keys collapsed to one ``analyze/<framework>``
        in v5: a lane is a framework's internal fact, and every lane runs the
        same judgement on the same tier. What is per framework is the triple,
        because an operator may point one package's critic at a different vendor
        from another's.
        """
        assert LLM_NODES[:2] == ("extract", "repair")
        assert FRAMEWORK_NODES == (
            "analyze/asvs",
            "critic/asvs",
            "recritic/asvs",
            "analyze/stride",
            "critic/stride",
            "recritic/stride",
        )
        assert LLM_NODES[2:] == FRAMEWORK_NODES

    def test_every_carried_framework_needs_its_three_keys_of_every_install(self):
        """The triple is required in every file, whatever that install carries.

        ``LLM_NODES`` derives from ``FrameworkName``, which names what this build
        can spell rather than what an install runs. So a deployment carrying
        STRIDE alone still has to name ASVS's three keys, and a file that omits
        them fails the completeness check by name. That message is the fix, and
        it is the stated cost of adding a framework without moving the schema
        version.
        """
        assert {name.split("/", 1)[1] for name in FRAMEWORK_NODES} == set(
            FRAMEWORK_NAMES
        )

    def test_exactly_two_tiers_named_on_a_capability_axis(self):
        # Not flash/pro: those were one vendor's product names and would be an
        # active lie under a Claude or GPT model string.
        assert TIER_NAMES == ("base", "strong")


class TestRepoConfig:
    """What ships, and what it deliberately does not ship.

    The selection is empty on purpose: "no privileged default" is meant to be
    true of the shipped values, not only of the mechanism. These are the tests
    that would fail if a vendor crept back into the file.
    """

    SELECTED: ClassVar[dict[str, str]] = {
        "STRIDE_MODEL_BASE_VENDOR": "openai",
        "STRIDE_MODEL_BASE_MODEL": "gpt-4.1-mini",
        "STRIDE_MODEL_STRONG_VENDOR": "anthropic",
        "STRIDE_MODEL_STRONG_MODEL": "claude-opus-5",
    }

    def test_shipped_config_selects_no_vendor(self):
        with pytest.raises(ModelConfigError, match="no vendor selected") as excinfo:
            load_model_tiers(REPO_CONFIG, env={})

        message = str(excinfo.value)
        assert "base" in message and "strong" in message
        # The error is the onboarding instruction, so it has to name every
        # vendor available and both places a selection can be made.
        for vendor in VENDOR_NAMES:
            assert vendor in message
        assert "STRIDE_MODEL_BASE_VENDOR" in message
        assert "docs/First-Run.md" in message

    def test_shipped_config_names_no_vendor_anywhere_uncommented(self):
        """A commented example is guidance; an uncommented one is a default."""
        live = [
            line
            for line in REPO_CONFIG.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        assert not [line for line in live if "vendor" in line or "model =" in line]

    def test_shipped_config_covers_all_llm_nodes(self):
        config = load_model_tiers(REPO_CONFIG, env=self.SELECTED)
        assert set(config.nodes) == set(LLM_NODES)

    def test_shipped_config_tier_assignment(self):
        config = load_model_tiers(REPO_CONFIG, env=self.SELECTED)
        assert config.nodes["extract"] == "base"
        assert config.nodes["repair"] == "base"
        assert config.nodes["critic/stride"] == "strong"
        for node in FRAMEWORK_NODES:
            assert config.nodes[node] == "strong"


class TestResolution:
    def test_resolve_model_returns_a_vendor_model_pair(self, config_path):
        config = load_model_tiers(config_path(config_toml()), env={})
        assert config.resolve_model("extract") == TierSelection(
            vendor=VENDOR, model=BASE
        )
        assert config.resolve_model("critic/stride").model == STRONG

    def test_the_route_joins_the_vendor_prefix_to_the_model(self, config_path):
        config = load_model_tiers(config_path(config_toml()), env={})
        assert config.resolve_model("extract").route == "vertex_ai/gemini-2.5-flash"

    def test_the_two_tiers_may_run_different_vendors_at_once(self, config_path):
        text = config_toml(strong_vendor="anthropic", strong="claude-opus-5")
        config = load_model_tiers(config_path(text), env={})
        assert config.resolve_model("extract").vendor == "vertex"
        assert config.resolve_model("critic/stride").vendor == "anthropic"

    def test_resolve_model_unknown_node_raises(self, config_path):
        config = load_model_tiers(config_path(config_toml()), env={})
        with pytest.raises(ModelConfigError, match="unknown LLM node"):
            config.resolve_model("assemble")


class TestEnvOverrides:
    def test_model_alone_overrides_one_tier_only(self, config_path):
        # The real ops case: retune a tier's model on a deployed revision.
        _, model_var = env_vars_for("strong")
        config = load_model_tiers(
            config_path(config_toml()), env={model_var: "gemini-3.0-pro"}
        )
        assert config.resolve_model("critic/stride").model == "gemini-3.0-pro"
        assert config.resolve_model("extract").model == BASE

    def test_vendor_and_model_together_switch_vendor(self, config_path):
        vendor_var, model_var = env_vars_for("base")
        env = {vendor_var: "anthropic", model_var: "claude-opus-5"}
        config = load_model_tiers(config_path(config_toml()), env=env)
        assert config.resolve_model("extract").route == ("anthropic/claude-opus-5")

    def test_the_path_variable_can_actually_be_set(self, config_path):
        """``STRIDE_TIERS_FILE`` points the loader at a valid file and is read.

        Named against a *valid* file on purpose: a nonexistent path fails the
        read before the override check, so it would pass for the wrong reason.
        """
        path = config_path(config_toml())
        config = load_model_tiers(path, env={"STRIDE_TIERS_FILE": str(path)})

        assert config.resolve_model("extract").model == BASE

    def test_vendor_without_model_is_a_build_time_error(self, config_path):
        # The one half-set case nothing downstream catches: anthropic +
        # gemini-2.5-pro passes the denylist and passes the sampling gate, so it
        # would die on node one of a paid-for job instead.
        vendor_var, _ = env_vars_for("base")
        with pytest.raises(ModelConfigError, match="is set without"):
            load_model_tiers(config_path(config_toml()), env={vendor_var: "anthropic"})

    def test_an_unrecognised_override_is_rejected_not_ignored(self, config_path):
        # Any STRIDE_MODEL_* name outside the four the loader knows: silently
        # ignoring one would leave the tier quietly running the file's model.
        with pytest.raises(ModelConfigError, match="unrecognised model override"):
            load_model_tiers(
                config_path(config_toml()), env={"STRIDE_MODEL_FLASH": BASE}
            )

    def test_env_alias_rejected(self, config_path):
        _, model_var = env_vars_for("base")
        with pytest.raises(ModelConfigError, match="-latest"):
            load_model_tiers(
                config_path(config_toml()), env={model_var: "gemini-2.5-flash-latest"}
            )

    def test_env_preview_build_rejected(self, config_path):
        _, model_var = env_vars_for("strong")
        with pytest.raises(ModelConfigError, match="pre-GA"):
            load_model_tiers(
                config_path(config_toml()),
                env={model_var: "gemini-2.5-pro-preview-06-05"},
            )

    def test_env_set_but_empty_rejected(self, config_path):
        _, model_var = env_vars_for("base")
        with pytest.raises(ModelConfigError, match="set but empty"):
            load_model_tiers(config_path(config_toml()), env={model_var: "  "})

    def test_env_var_names(self):
        assert env_vars_for("base") == (
            "STRIDE_MODEL_BASE_VENDOR",
            "STRIDE_MODEL_BASE_MODEL",
        )
        assert env_vars_for("strong") == (
            "STRIDE_MODEL_STRONG_VENDOR",
            "STRIDE_MODEL_STRONG_MODEL",
        )


class TestPinValidation:
    """ "Pinned" is per-family: a denylist, plus a shape where one is published.

    The denylist half stays deliberately weak, and the guarantee lives on the
    served-build readback rather than here: an allowlist of numbered builds is
    what broke when Google retired them, and that risk runs against three
    catalogs. Claude's half is a *shape*, not a list of builds, so a model
    released tomorrow already satisfies it.
    """

    @pytest.mark.parametrize(
        "value",
        [
            "gemini-2.5-pro-latest",
            "gemini-2.5-pro-preview-06-05",
            "gemini-2.0-flash-exp",
            " gemini-2.5-pro",
            "",
        ],
    )
    def test_aliases_and_pre_ga_builds_rejected(self, value):
        with pytest.raises(ModelConfigError):
            validate_model_string(value, "vertex", source="tiers.strong.model")

    @pytest.mark.parametrize("value", ["gemini-2.5-pro", "gemini-2.5-flash"])
    def test_bare_stable_gemini_accepted(self, value):
        # Gemini 2.5+ ships no numbered stable builds, so the bare name is the
        # most specific identifier that exists.
        assert validate_model_string(value, "vertex", source="t") == value

    @pytest.mark.parametrize("vendor", ["anthropic", "vertex"])
    def test_the_dateless_claude_id_is_the_pinned_form(self, vendor):
        # Both vendors that serve Claude spell 4.6-and-later identically, and
        # the dateless ID is the snapshot rather than a pointer at one.
        assert (
            validate_model_string("claude-opus-5", vendor, source="t")
            == "claude-opus-5"
        )

    @pytest.mark.parametrize("vendor", ["anthropic", "vertex"])
    def test_the_pre_generation_dated_forms_are_rejected(self, vendor):
        for value in ("claude-sonnet-4-5-20250929", "claude-sonnet-4-5@20250929"):
            with pytest.raises(ModelConfigError, match="not pinned"):
                validate_model_string(value, vendor, source="t")

    def test_an_older_generation_in_the_pinned_form_is_accepted(self):
        # Well-formed and older than the generation this service once floored
        # at. The rule reads the identifier's shape, never its version, so
        # which model is worth running stays the deployment's call.
        assert (
            validate_model_string("claude-haiku-4-5", "anthropic", source="t")
            == "claude-haiku-4-5"
        )

    def test_the_vertex_rule_branches_on_model_family(self):
        # One vendor entry, two families: vertex_ai/ is not one provider. The
        # dated Claude is the case that proves the branch — under the
        # catch-all it would pass, since it is neither an alias nor pre-GA.
        assert validate_model_string("gemini-2.5-pro", "vertex", source="t")
        with pytest.raises(ModelConfigError, match="not pinned"):
            validate_model_string("claude-sonnet-4-5-20250929", "vertex", source="t")

    def test_openai_has_no_canonical_form_to_require(self):
        # The o-series ships none at all, so only the shared denylist applies.
        assert validate_model_string("o3", "openai", source="t") == "o3"

    def test_unknown_vendor_rejected(self):
        with pytest.raises(ModelConfigError, match="unknown vendor"):
            validate_model_string("whatever", "cohere", source="t")

    def test_alias_in_file_rejected(self, config_path):
        path = config_path(config_toml(strong="gemini-2.5-pro-latest"))
        with pytest.raises(ModelConfigError, match="-latest"):
            load_model_tiers(path, env={})


class TestFileValidation:
    def test_a_file_on_another_version_fails_closed(self, config_path):
        with pytest.raises(ModelConfigError, match="unsupported version"):
            load_model_tiers(config_path(config_toml(version=2)), env={})

    def test_unknown_node_rejected(self, config_path):
        nodes = {node: "strong" for node in LLM_NODES} | {"assemble": "base"}
        with pytest.raises(ModelConfigError, match="unknown node"):
            load_model_tiers(config_path(config_toml(nodes=nodes)), env={})

    def test_missing_node_rejected(self, config_path):
        nodes = {node: "strong" for node in LLM_NODES if node != "critic/stride"}
        with pytest.raises(ModelConfigError, match="missing entries"):
            load_model_tiers(config_path(config_toml(nodes=nodes)), env={})

    def test_unknown_tier_name_rejected(self, config_path):
        nodes = {node: "strong" for node in LLM_NODES} | {"extract": "turbo"}
        with pytest.raises(ModelConfigError):
            load_model_tiers(config_path(config_toml(nodes=nodes)), env={})

    def test_unknown_vendor_in_file_rejected(self, config_path):
        with pytest.raises(ModelConfigError):
            load_model_tiers(config_path(config_toml(base_vendor="cohere")), env={})

    def test_missing_tier_rejected(self, config_path):
        text = config_toml().replace(
            f'[tiers.strong]\nvendor = "{VENDOR}"\nmodel = "{STRONG}"\n', ""
        )
        with pytest.raises(ModelConfigError):
            load_model_tiers(config_path(text), env={})

    def test_missing_version_rejected(self, config_path):
        text = config_toml().replace(f"version = {SUPPORTED_VERSION}\n", "")
        with pytest.raises(ModelConfigError):
            load_model_tiers(config_path(text), env={})

    def test_extra_top_level_key_rejected(self, config_path):
        with pytest.raises(ModelConfigError):
            load_model_tiers(config_path(config_toml() + 'fallback = "auto"\n'), env={})

    def test_extra_tier_key_rejected(self, config_path):
        text = config_toml().replace(
            f'[tiers.base]\nvendor = "{VENDOR}"',
            f'[tiers.base]\napi_key = "sk-nope"\nvendor = "{VENDOR}"',
        )
        # Auth is derived from the vendor, never configured alongside it.
        with pytest.raises(ModelConfigError):
            load_model_tiers(config_path(text), env={})

    def test_invalid_toml_rejected(self, config_path):
        with pytest.raises(ModelConfigError, match="invalid TOML"):
            load_model_tiers(config_path("version = [unclosed"), env={})

    def test_config_is_frozen(self, config_path):
        config = load_model_tiers(config_path(config_toml()), env={})
        with pytest.raises(ValidationError):
            config.version = 2

    def test_os_environ_is_default_env(self, config_path, monkeypatch):
        monkeypatch.setenv("STRIDE_MODEL_STRONG_MODEL", "gemini-3.0-pro")
        config = load_model_tiers(config_path(config_toml()))
        assert config.resolve_model("critic/stride").model == "gemini-3.0-pro"


def test_direct_construction_validates_completeness():
    with pytest.raises(ValueError, match="nodes missing entries"):
        ModelTierConfig(
            version=SUPPORTED_VERSION,
            tiers={
                "base": TierSelection(vendor=VENDOR, model=BASE),
                "strong": TierSelection(vendor=VENDOR, model=STRONG),
            },
            nodes={},
        )
