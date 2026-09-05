"""Tests for model-tier config loading, env overrides, and pin validation."""

import tempfile
from pathlib import Path
from typing import ClassVar

import pytest
from pydantic import ValidationError

from analysis_service.model_tiers import (
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
from analysis_service.report import FRAMEWORK_NAMES
from analysis_service.vendors import VENDOR_NAMES, CredentialMode, vendor_for

PROJECT_ROOT = Path(__file__).parents[1]
REPO_CONFIG = PROJECT_ROOT / "config" / "model_tiers.toml"

BASE = "gemini-2.5-flash"
STRONG = "gemini-2.5-pro"
VENDOR = "vertex"
# A second vendor on `review`, so an independence test has something to be
# independent *of* without inventing a config the rest of the module does not use.
REVIEW = "claude-opus-5"
REVIEW_VENDOR = "anthropic"


def config_toml(
    base=BASE,
    strong=STRONG,
    review=REVIEW,
    base_vendor=VENDOR,
    strong_vendor=VENDOR,
    review_vendor=REVIEW_VENDOR,
    nodes=None,
    version=SUPPORTED_VERSION,
    independence="shared",
):
    if nodes is None:
        nodes = {
            node: "base" if node in ("extract", "repair") else "strong"
            for node in LLM_NODES
        }
    node_lines = "\n".join(f'"{node}" = "{tier}"' for node, tier in nodes.items())
    return (
        f"version = {version}\n"
        f'review_independence = "{independence}"\n\n'
        f'[tiers.base]\nvendor = "{base_vendor}"\nmodel = "{base}"\n\n'
        f'[tiers.strong]\nvendor = "{strong_vendor}"\nmodel = "{strong}"\n\n'
        f'[tiers.review]\nvendor = "{review_vendor}"\nmodel = "{review}"\n\n'
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

    def test_the_tiers_are_named_on_a_capability_axis(self):
        # Not flash/pro: those were one vendor's product names and would be an
        # active lie under a Claude or GPT model string. `review` names a place
        # criticism can be bound to rather than a capability, which is the
        # exception the third tier is: it exists so a critic can be moved off
        # the model it checks, not because it is stronger or cheaper.
        assert TIER_NAMES == ("base", "strong", "review")


class TestRepoConfig:
    """What ships, and what it deliberately does not ship.

    The selection is empty on purpose: "no privileged default" is meant to be
    true of the shipped values, not only of the mechanism. These are the tests
    that would fail if a vendor crept back into the file.
    """

    SELECTED: ClassVar[dict[str, str]] = {
        "ANALYSIS_MODEL_BASE_VENDOR": "openai",
        "ANALYSIS_MODEL_BASE_MODEL": "gpt-4.1-mini",
        "ANALYSIS_MODEL_STRONG_VENDOR": "anthropic",
        "ANALYSIS_MODEL_STRONG_MODEL": "claude-opus-5",
        "ANALYSIS_MODEL_REVIEW_VENDOR": "vertex",
        "ANALYSIS_MODEL_REVIEW_MODEL": "gemini-2.5-pro",
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
        assert "ANALYSIS_MODEL_BASE_VENDOR" in message
        assert "docs/First-Run.md" in message

    def test_a_tier_the_node_map_does_not_use_needs_no_selection(self):
        """The shipped map runs criticism on ``strong``, so nothing sits on
        ``review`` -- and ``build_adapters`` binds no adapter for a tier nothing
        is bound to.

        Requiring a pair for it anyway made a first run choose a vendor and a
        model for a tier no request reaches, and the answer the config file
        suggested was to repeat the ``strong`` pair, which chooses nothing.
        """
        selected = {
            key: value for key, value in self.SELECTED.items() if "REVIEW" not in key
        }

        config = load_model_tiers(REPO_CONFIG, env=selected)

        assert set(config.tiers) == {"base", "strong"}
        assert "review" not in set(config.nodes.values())

    def test_a_tier_the_node_map_does_use_is_still_required(self):
        """The reason the requirement existed, kept: the day somebody moves
        criticism onto ``review`` is the wrong day to find no model was chosen
        for it. That day is a node-map edit, and this is read on that edit."""
        moved = REPO_CONFIG.read_text(encoding="utf-8").replace(
            '"critic/stride" = "strong"', '"critic/stride" = "review"'
        )
        path = self.written(moved)
        selected = {
            key: value for key, value in self.SELECTED.items() if "REVIEW" not in key
        }

        with pytest.raises(ModelConfigError, match="no vendor selected") as excinfo:
            load_model_tiers(path, env=selected)

        assert "review" in str(excinfo.value)

    def written(self, text: str) -> Path:
        directory = Path(tempfile.mkdtemp())
        path = directory / "model_tiers.toml"
        path.write_text(text, encoding="utf-8")
        return path

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

    def test_the_shipped_review_is_shared_and_says_so(self):
        # The `review` tier exists and nothing is pointed at it. That is the
        # cheaper default and the honest one: the policy states it rather than
        # leaving a reader to work it out from the node map.
        config = load_model_tiers(REPO_CONFIG, env=self.SELECTED)
        assert config.review_independence == "shared"
        assert "review" not in set(config.nodes.values())
        assert config.independence_breaches() == []


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
        """``ANALYSIS_TIERS_FILE`` points the loader at a valid file and is read.

        Named against a *valid* file on purpose: a nonexistent path fails the
        read before the override check, so it would pass for the wrong reason.
        """
        path = config_path(config_toml())
        config = load_model_tiers(path, env={"ANALYSIS_TIERS_FILE": str(path)})

        assert config.resolve_model("extract").model == BASE

    def test_vendor_without_model_is_a_build_time_error(self, config_path):
        # The one half-set case nothing downstream catches: anthropic +
        # gemini-2.5-pro passes the denylist and passes the sampling gate, so it
        # would die on node one of a paid-for job instead.
        vendor_var, _ = env_vars_for("base")
        with pytest.raises(ModelConfigError, match="is set without"):
            load_model_tiers(config_path(config_toml()), env={vendor_var: "anthropic"})

    def test_an_unrecognised_override_is_rejected_not_ignored(self, config_path):
        # Any ANALYSIS_MODEL_* name outside the four the loader knows: silently
        # ignoring one would leave the tier quietly running the file's model.
        with pytest.raises(ModelConfigError, match="unrecognised model override"):
            load_model_tiers(
                config_path(config_toml()), env={"ANALYSIS_MODEL_FLASH": BASE}
            )

    def test_env_alias_rejected(self, config_path):
        _, model_var = env_vars_for("base")
        with pytest.raises(ModelConfigError, match="latest"):
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
            "ANALYSIS_MODEL_BASE_VENDOR",
            "ANALYSIS_MODEL_BASE_MODEL",
        )
        assert env_vars_for("strong") == (
            "ANALYSIS_MODEL_STRONG_VENDOR",
            "ANALYSIS_MODEL_STRONG_MODEL",
        )


class TestPinValidation:
    """ "Pinned" is per-family: a denylist, plus a shape where one is published.

    The denylist half stays deliberately weak, and the guarantee lives on the
    served-build readback rather than here: an allowlist of numbered builds is
    what broke when Google retired them, and that risk runs against three
    catalogs. Claude's half is a *shape*, not a list of builds, so a model
    released tomorrow already satisfies it.
    """

    @pytest.mark.parametrize("value", [" gemini-2.5-pro", ""])
    def test_a_non_identifier_is_rejected(self, value):
        with pytest.raises(ModelConfigError):
            validate_model_string(value, "vertex", source="tiers.strong.model")

    def test_the_registrys_refusal_arrives_as_a_config_error(self):
        """What this seam adds over ``Vendor.validate_model`` is the type.

        Which identifiers float is asserted once, in
        ``test_vendors.py::TestPinnedFormRule``. Restating that table here
        would give one rule a second reader that agrees with it by copying.
        """
        with pytest.raises(ModelConfigError, match="latest"):
            validate_model_string(
                "gemini-2.5-pro-latest", "vertex", source="tiers.strong.model"
            )

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
        with pytest.raises(ModelConfigError, match="latest"):
            load_model_tiers(path, env={})


class TestDeclaredCredentialMode:
    """A deployment declares a mode only where the vendor gives it a choice.

    Both rules read ``CREDENTIAL_MODES``, so they follow the registry rather
    than a second copy of it. Every vendor allows exactly one mode today, so the
    shipped table is empty — what version 7 carries is the rule, which is what
    makes a vendor row that gains a second mode unable to ship silently.
    """

    def test_a_single_mode_vendor_needs_no_declaration(self, config_path):
        tiers = load_model_tiers(config_path(config_toml()), env={})
        assert tiers.credentials == {}
        assert tiers.credential_mode("vertex") is CredentialMode.IAM

    def test_the_mode_comes_from_the_registry_not_from_a_default(self):
        # Not "whatever the first vendor uses": each one answers for itself.
        assert vendor_for("anthropic").sole_credential_mode is CredentialMode.API_KEY
        assert vendor_for("vertex").sole_credential_mode is CredentialMode.IAM

    def test_declaring_a_mode_for_a_single_mode_vendor_is_an_error(self, config_path):
        """It is not a choice, so stating it can only go stale.

        A file that names a mode the registry has since replaced would otherwise
        keep asserting it, and the loader would keep accepting it.
        """
        path = config_path(config_toml() + '\n[credentials]\nvertex = "iam"\n')
        with pytest.raises(ModelConfigError, match="nothing to choose"):
            load_model_tiers(path, env={})

    def test_a_mode_the_vendor_does_not_allow_is_an_error(self, config_path):
        path = config_path(config_toml() + '\n[credentials]\nvertex = "api_key"\n')
        with pytest.raises(ModelConfigError):
            load_model_tiers(path, env={})

    def test_an_unknown_mode_is_an_error(self, config_path):
        path = config_path(config_toml() + '\n[credentials]\nvertex = "sudo"\n')
        with pytest.raises(ModelConfigError):
            load_model_tiers(path, env={})

    def test_an_unknown_vendor_is_an_error(self, config_path):
        path = config_path(config_toml() + '\n[credentials]\ncohere = "api_key"\n')
        with pytest.raises(ModelConfigError):
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
        monkeypatch.setenv("ANALYSIS_MODEL_STRONG_MODEL", "gemini-3.0-pro")
        config = load_model_tiers(config_path(config_toml()))
        assert config.resolve_model("critic/stride").model == "gemini-3.0-pro"


def test_direct_construction_validates_completeness():
    with pytest.raises(ValueError, match="nodes missing entries"):
        ModelTierConfig(
            version=SUPPORTED_VERSION,
            tiers={
                "base": TierSelection(vendor=VENDOR, model=BASE),
                "strong": TierSelection(vendor=VENDOR, model=STRONG),
                "review": TierSelection(vendor=REVIEW_VENDOR, model=REVIEW),
            },
            nodes={},
            review_independence="shared",
        )


class TestReviewIndependence:
    """How far a framework's criticism has to sit from its own analysis (#506).

    The point is bounded *correlated* failure, never accuracy: an analysis and a
    critic on one model share that model's blind spots, so the critic improves
    consistency and cannot notice what the model does not know. Nothing here
    claims a second provider finds more.
    """

    def critic_on(self, tier: str, independence: str, **kwargs):
        """A config with every framework's critic and recritic on ``tier``."""
        nodes = {
            node: "base"
            if node in ("extract", "repair")
            else tier
            if node.startswith(("critic/", "recritic/"))
            else "strong"
            for node in LLM_NODES
        }
        return config_toml(nodes=nodes, independence=independence, **kwargs)

    def test_shared_admits_a_critic_on_the_analysis_tier(self, config_path):
        config = load_model_tiers(
            config_path(self.critic_on("strong", "shared")), env={}
        )
        assert config.review_independence == "shared"

    @pytest.mark.parametrize("independence", ["distinct_model", "distinct_provider"])
    def test_a_policy_beyond_shared_refuses_a_critic_on_the_analysis_tier(
        self, config_path, independence
    ):
        # Fails closed at load, and names the framework. A deployment that asked
        # for an independent reviewer and did not get one has a configuration to
        # fix, not a run to annotate — annotating it would put the finding in an
        # artifact somebody already paid for.
        with pytest.raises(ModelConfigError, match="critic/stride are not independent"):
            load_model_tiers(
                config_path(self.critic_on("strong", independence)), env={}
            )

    @pytest.mark.parametrize("independence", ["distinct_model", "distinct_provider"])
    def test_the_review_tier_satisfies_both_policies(self, config_path, independence):
        # `review` selects anthropic/claude-opus-5 against strong's
        # vertex/gemini-2.5-pro, so it differs in model and in vendor.
        config = load_model_tiers(
            config_path(self.critic_on("review", independence)), env={}
        )
        assert config.independence_breaches() == []
        assert config.resolve_model("critic/stride").vendor == REVIEW_VENDOR

    def test_a_distinct_model_on_one_vendor_fails_the_provider_policy(
        self, config_path
    ):
        # The two policies are not the same test. A second model from one
        # provider removes a build's blind spots and keeps the provider's, so a
        # deployment that asked for a distinct provider must not read as
        # satisfied by it.
        text = self.critic_on(
            "review",
            "distinct_provider",
            review_vendor=VENDOR,
            review="gemini-2.5-flash",
        )
        with pytest.raises(ModelConfigError, match="both run vendor 'vertex'"):
            load_model_tiers(config_path(text), env={})

        relaxed = self.critic_on(
            "review", "distinct_model", review_vendor=VENDOR, review="gemini-2.5-flash"
        )
        assert (
            load_model_tiers(config_path(relaxed), env={}).independence_breaches() == []
        )

    def test_the_same_pair_under_two_tier_names_is_not_independence(self, config_path):
        # Independence is about the selection, not the tier name. A `review`
        # tier configured with strong's own pair is the shared case wearing a
        # second label.
        text = self.critic_on(
            "review", "distinct_model", review_vendor=VENDOR, review=STRONG
        )
        with pytest.raises(ModelConfigError, match="both run vertex/gemini-2.5-pro"):
            load_model_tiers(config_path(text), env={})

    def test_an_unknown_policy_is_refused(self, config_path):
        with pytest.raises(ModelConfigError):
            load_model_tiers(
                config_path(config_toml(independence="mostly_distinct")), env={}
            )

    def test_the_policy_is_required(self, config_path):
        # No default, because inheriting "shared" is how an install that meant
        # to review itself independently ends up not doing so.
        text = config_toml().replace('review_independence = "shared"\n', "")
        with pytest.raises(ModelConfigError, match="review_independence"):
            load_model_tiers(config_path(text), env={})

    def test_the_recritic_pairing_still_holds_on_the_review_tier(self, config_path):
        # Moving criticism to `review` must move the re-ask with it: a re-ask on
        # a different tier from the pass it corrects is the drift
        # critic_pairing_issues exists to refuse, whichever tier that is.
        nodes = {
            node: "base"
            if node in ("extract", "repair")
            else "review"
            if node.startswith("critic/")
            else "strong"
            for node in LLM_NODES
        }
        with pytest.raises(ModelConfigError, match="recritic/stride"):
            load_model_tiers(
                config_path(config_toml(nodes=nodes, independence="shared")), env={}
            )


def test_the_review_tier_needs_no_credentials_until_something_runs_on_it():
    """Selecting a tier is not the same as calling its provider (#506).

    `review` is required in every config so that moving criticism onto it is a
    one-line edit rather than a discovery. Building an adapter for it anyway
    would demand a second vendor's credentials from every deployment that never
    asked for an independent reviewer, and refuse to start without them.
    """
    from analysis_service.binding import build_tier_adapters
    from analysis_service.resilience import load_resilience
    from analysis_service.sampling import load_sampling

    tiers = load_model_tiers(
        REPO_CONFIG,
        env={
            "ANALYSIS_MODEL_BASE_VENDOR": "openai",
            "ANALYSIS_MODEL_BASE_MODEL": "gpt-4.1-mini",
            "ANALYSIS_MODEL_STRONG_VENDOR": "openai",
            "ANALYSIS_MODEL_STRONG_MODEL": "gpt-5.6",
            # A vendor whose key is deliberately absent from the env below.
            "ANALYSIS_MODEL_REVIEW_VENDOR": "anthropic",
            "ANALYSIS_MODEL_REVIEW_MODEL": "claude-opus-5",
        },
    )
    adapters = build_tier_adapters(
        tiers,
        load_sampling(PROJECT_ROOT / "config" / "sampling.toml", env={}),
        load_resilience(PROJECT_ROOT / "config" / "resilience.toml", env={}),
        env={"ANALYSIS_OPENAI_API_KEY": "sk-not-a-real-key"},
    )
    assert set(adapters) == {"base", "strong"}
