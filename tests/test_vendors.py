"""The provider registry: routing, credential mode, and the pinned-form rule.

The registry holds only what nothing else can supply. Two things it deliberately
does *not* hold are asserted here too, because their absence is a decision
rather than an omission: per-``(vendor, model)`` sampling support (#12 moved it
to a call) and a per-vendor reasoning kwarg (#15 made the surface uniform).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import ClassVar, get_args

import pytest

from analysis_service.vendors import (
    _CREDENTIAL_VARS,
    CREDENTIAL_MODE_NOTES,
    CREDENTIAL_MODES,
    REASONING_KWARG,
    VENDOR_NAMES,
    CredentialMode,
    ProviderAuthError,
    ServedTrust,
    Vendor,
    claude_generation,
    join_served,
    openai_reasoning_model,
    vendor_for,
    vendor_for_route,
)

API_KEY = "sk-test-not-a-real-key"


class TestRouting:
    def test_the_route_joins_prefix_to_model(self):
        assert vendor_for("vertex").route("gemini-2.5-pro") == (
            "vertex_ai/gemini-2.5-pro"
        )
        assert vendor_for("anthropic").route("claude-sonnet-5") == (
            "anthropic/claude-sonnet-5"
        )

    def test_the_litellm_provider_is_derived_from_the_prefix(self):
        # Derived, not stored, so the router string and the gate's
        # custom_llm_provider can never disagree.
        for name in VENDOR_NAMES:
            vendor = vendor_for(name)
            assert vendor.prefix == f"{vendor.litellm_provider}/"

    def test_unknown_vendor_raises(self):
        with pytest.raises(ValueError, match="unknown vendor"):
            vendor_for("cohere")


class TestCredentialModes:
    """The mechanism is declared; the material may be discovered."""

    def test_every_vendor_declares_its_allowed_modes(self):
        # A missing key raises, which is the point: no vendor can be silent
        # about how it authenticates.
        assert set(CREDENTIAL_MODES) == set(VENDOR_NAMES)

    def test_the_var_table_answers_for_every_allowed_pair(self):
        """The table is checked against its registry, in both directions.

        A table nobody compares to ``CREDENTIAL_MODES`` fails as quietly as the
        branch it replaced: an allowed mode with no entry raises at build time
        on a deployment nobody tested, and an entry for a mode the vendor does
        not allow is a shape that can never run.
        """
        declared = {
            (vendor, mode)
            for vendor, modes in CREDENTIAL_MODES.items()
            for mode in modes
        }
        assert set(_CREDENTIAL_VARS) == declared

    def test_every_mode_tells_an_operator_what_to_arrange(self):
        assert set(CREDENTIAL_MODE_NOTES) == set(CredentialMode)

    def test_vertex_is_platform_identity_because_it_admits_no_api_key_path(self):
        assert vendor_for("vertex").credential_modes == (CredentialMode.IAM,)

    @pytest.mark.parametrize("name", ["anthropic", "openai"])
    def test_the_api_key_vendors_are_api_key(self, name):
        assert vendor_for(name).credential_modes == (CredentialMode.API_KEY,)

    def test_the_key_var_is_vendor_scoped(self):
        assert vendor_for("anthropic").api_key_var == "ANALYSIS_ANTHROPIC_API_KEY"
        assert vendor_for("openai").api_key_var == "ANALYSIS_OPENAI_API_KEY"

    def test_a_mode_a_vendor_does_not_allow_raises(self):
        with pytest.raises(ValueError, match="no 'api_key' credential mode"):
            vendor_for("vertex").credential_kwargs({}, CredentialMode.API_KEY)

    def test_the_key_is_read_from_the_vendor_scoped_var(self):
        kwargs = vendor_for("anthropic").credential_kwargs(
            {"ANALYSIS_ANTHROPIC_API_KEY": API_KEY}, CredentialMode.API_KEY
        )
        assert kwargs == {"api_key": API_KEY}

    @pytest.mark.parametrize(
        "name",
        [
            name
            for name in VENDOR_NAMES
            if CredentialMode.API_KEY in CREDENTIAL_MODES[name]
        ],
    )
    def test_an_ambient_key_authenticates_nothing(self, name):
        """Every vendor that takes a key, not just the one somebody tested.

        An undeclared credential in the process environment is the ASI03
        inherited-credential path. LiteLLM picks up ``ANTHROPIC_API_KEY`` and
        ``OPENAI_API_KEY`` on its own and cannot be told not to, so the refusal
        has to happen in the registry — which is the same reason the *declared*
        half of the rule is enforced here rather than in the adapter.
        """
        vendor = vendor_for(name)
        ambient = f"{name.upper()}_API_KEY"
        with pytest.raises(ProviderAuthError, match=vendor.api_key_var):
            vendor.credential_kwargs({ambient: API_KEY}, CredentialMode.API_KEY)

    def test_a_missing_key_fails_closed_naming_the_var_not_the_value(self):
        with pytest.raises(ProviderAuthError) as excinfo:
            vendor_for("openai").credential_kwargs({}, CredentialMode.API_KEY)
        assert "ANALYSIS_OPENAI_API_KEY" in str(excinfo.value)

    def test_an_empty_key_is_a_deploy_mistake_not_an_absence(self):
        with pytest.raises(ProviderAuthError):
            vendor_for("openai").credential_kwargs(
                {"ANALYSIS_OPENAI_API_KEY": "   "}, CredentialMode.API_KEY
            )

    def test_a_key_value_never_appears_in_the_error(self):
        # OWASP A09: a key echoed into a log or a problem+json body has leaked.
        with pytest.raises(ProviderAuthError) as excinfo:
            vendor_for("openai").credential_kwargs(
                {"ANALYSIS_OPENAI_API_KEY": ""}, CredentialMode.API_KEY
            )
        assert API_KEY not in str(excinfo.value)


class TestPlatformIdentity:
    """``IAM`` passes no credential material, so the platform's own chain runs."""

    def test_vertex_passes_a_project_and_a_location_and_no_credential(self):
        kwargs = vendor_for("vertex").credential_kwargs(
            {
                "ANALYSIS_VERTEX_PROJECT": "p",
                "ANALYSIS_VERTEX_LOCATION": "us-east5",
            },
            CredentialMode.IAM,
        )
        assert kwargs == {"vertex_project": "p", "vertex_location": "us-east5"}

    def test_no_google_credentials_file_is_required(self):
        """The defect this mode fixes: corporate Google IAM could not run.

        ``Vendor._require`` demanded ``GOOGLE_APPLICATION_CREDENTIALS``, so a
        Workload Identity deployment failed closed before LiteLLM ran — even
        though ``vertex_llm_base.load_auth`` takes ``credentials=None`` and
        calls ``google.auth.default()``, which resolves exactly that identity.
        """
        env = {"ANALYSIS_VERTEX_PROJECT": "p", "ANALYSIS_VERTEX_LOCATION": "us-east5"}
        assert "GOOGLE_APPLICATION_CREDENTIALS" not in env
        assert vendor_for("vertex").credential_kwargs(env, CredentialMode.IAM)

    def test_the_registry_names_no_credentials_file_at_all(self):
        # An operator who sets it still gets it, through ADC's own chain.
        # Nothing here names it or requires it.
        for entries in _CREDENTIAL_VARS.values():
            for entry in entries:
                assert entry.var != "GOOGLE_APPLICATION_CREDENTIALS"

    @pytest.mark.parametrize(
        "missing", ["ANALYSIS_VERTEX_PROJECT", "ANALYSIS_VERTEX_LOCATION"]
    )
    def test_each_addressing_variable_is_still_required(self, missing):
        env = {"ANALYSIS_VERTEX_PROJECT": "p", "ANALYSIS_VERTEX_LOCATION": "us-east5"}
        del env[missing]
        with pytest.raises(ProviderAuthError, match=missing):
            vendor_for("vertex").credential_kwargs(env, CredentialMode.IAM)


class TestWhichVariablesAreSecret:
    """One table, two questions. Reporting is wider than redacting."""

    def test_every_required_variable_is_reported(self):
        vertex = vendor_for("vertex")
        assert vertex.required_env_vars(CredentialMode.IAM) == (
            "ANALYSIS_VERTEX_PROJECT",
            "ANALYSIS_VERTEX_LOCATION",
        )

    def test_a_region_is_required_and_is_not_a_secret(self):
        """The #601 defect. A redactor reading the wider list took the region
        out of provider error text, and the region is the one fact that
        diagnoses a wrong-region request."""
        vertex = vendor_for("vertex")
        assert "ANALYSIS_VERTEX_LOCATION" in vertex.required_env_vars(
            CredentialMode.IAM
        )
        assert vertex.secret_env_vars(CredentialMode.IAM) == ()

    def test_an_api_key_is_secret(self):
        anthropic = vendor_for("anthropic")
        assert anthropic.secret_env_vars(CredentialMode.API_KEY) == (
            "ANALYSIS_ANTHROPIC_API_KEY",
        )

    # Sorted so the parameter ids are stable; ``CredentialMode`` is a
    # ``StrEnum``, so the pairs order as the strings they spell.
    @pytest.mark.parametrize(("vendor", "mode"), sorted(_CREDENTIAL_VARS))
    def test_the_secret_set_is_a_subset_of_the_required_set(self, vendor, mode):
        entry = vendor_for(vendor)
        assert set(entry.secret_env_vars(mode)) <= set(entry.required_env_vars(mode))


class TestPinnedFormRule:
    """An open-world denylist, plus a closed shape for the one family with one."""

    @pytest.mark.parametrize("name", VENDOR_NAMES)
    @pytest.mark.parametrize(
        "model",
        [
            "model-latest",
            "model-preview-06-05",
            "model-exp",
            # A word the identifier delimits with something other than a
            # hyphen. ``@`` is Vertex Model Garden's alias spelling, and it is
            # what a delimiter list missed.
            "codestral@latest",
            "mistral-large@latest",
            # An alias in the middle rather than at the end.
            "kimi-latest-128k",
            # Both spellings of the same word, which a whole-word test has to
            # list separately.
            "gemini-exp-1206",
            "gemini-flash-experimental",
            "gpt-4o-realtime-preview",
            "gemini-2.5-pro-latest",
        ],
    )
    def test_a_floating_word_is_refused_on_every_vendor(self, name, model):
        with pytest.raises(ValueError):
            vendor_for(name).validate_model(model, source="t")

    @pytest.mark.parametrize("name", VENDOR_NAMES)
    @pytest.mark.parametrize(
        "model",
        [
            # ``express`` begins with ``exp``, and Titan Text Express has been
            # generally available for years. A fragment test refused it, and an
            # operator could not fix that from config.
            "amazon.titan-text-express-v1",
            "black_forest_labs/flux-pro-1.0-expand",
        ],
    )
    def test_a_word_that_merely_begins_with_a_marker_passes(self, name, model):
        assert vendor_for(name).validate_model(model, source="t") == model

    def test_an_alias_and_a_pre_ga_build_read_differently(self):
        """Two messages, because they name two different next actions."""
        vendor = vendor_for("vertex")
        with pytest.raises(ValueError, match="'latest' alias"):
            vendor.validate_model("codestral@latest", source="t")
        with pytest.raises(ValueError, match="pre-GA 'exp' build"):
            vendor.validate_model("gemini-exp-1206", source="t")

    @pytest.mark.parametrize(
        ("model", "word"),
        [
            ("kimi-latest-128k", "latest"),
            ("gemini-flash-experimental", "experimental"),
            ("gpt-4o-realtime-preview", "preview"),
        ],
    )
    def test_the_message_names_the_word_that_matched(self, model, word):
        with pytest.raises(ValueError, match=f"'{word}'"):
            vendor_for("vertex").validate_model(model, source="t")

    @pytest.mark.parametrize("bad", ["", "  ", " gemini-2.5-pro"])
    def test_non_identifiers_are_rejected(self, bad):
        with pytest.raises(ValueError, match="not a model identifier"):
            vendor_for("vertex").validate_model(bad, source="t")

    def test_the_rule_branches_on_model_family(self):
        vertex = vendor_for("vertex")
        # Gemini 2.5+ ships no numbered stable builds, so bare is most specific.
        assert vertex.validate_model("gemini-2.5-pro", source="t")
        # Claude's canonical form is the dateless ID, on Vertex as on the direct
        # API — Google Cloud spells 4.6-and-later identically.
        assert vertex.validate_model("claude-opus-5", source="t")

    #: One identifier, one verdict, whatever vendor names it. A **family** rule
    #: follows the family, so moving a tier between vendors must not change
    #: which identifiers are legal. The dated and pre-4.6 forms are the cases
    #: that decide it: they were refused on ``vertex`` and ``anthropic`` and
    #: accepted on ``openai``, because that row listed the catch-all alone.
    CLAUDE_VERDICTS: ClassVar[dict[str, bool]] = {
        "claude-opus-5": True,
        "claude-sonnet-4-6": True,
        "claude-haiku-4-5": True,
        "claude-opus-4-1": True,
        # A floating alias from the era when the bare name was one.
        "claude-3-opus": False,
        "claude-3-sonnet": False,
        # The dated forms, direct and Vertex-spelled.
        "claude-opus-4-20250514": False,
        "claude-sonnet-4-5-20250929": False,
        "claude-haiku-4-5@20251001": False,
        # The old name-after-version order.
        "claude-3-5-sonnet-20241022": False,
        "claude-3-7-sonnet": False,
    }

    @pytest.mark.parametrize("name", VENDOR_NAMES)
    @pytest.mark.parametrize(("model", "accepted"), sorted(CLAUDE_VERDICTS.items()))
    def test_a_claude_gets_one_verdict_whatever_vendor_names_it(
        self, name, model, accepted
    ):
        """A gateway serves a family its vendor did not train.

        ``openai/`` reaches any OpenAI-compatible endpoint, so a Claude
        identifier arrives under a vendor that never published it. The shape
        rule has to answer the same way there, for the reason
        ``check_temperature`` and ``openai_reasoning_model`` both key on the
        model and refuse to key on the vendor: the number of routes to one
        family only ever grows.
        """
        vendor = vendor_for(name)
        if accepted:
            assert vendor.validate_model(model, source="t") == model
        else:
            with pytest.raises(ValueError):
                vendor.validate_model(model, source="t")

    @pytest.mark.parametrize("name", ["anthropic", "vertex"])
    @pytest.mark.parametrize(
        "model",
        ["claude-opus-5", "claude-sonnet-5", "claude-opus-4-8", "claude-sonnet-4-6"],
    )
    def test_the_dateless_claude_id_is_the_pinned_form(self, name, model):
        # Not an alias: Anthropic ships a new ID rather than moving weights
        # under an existing one, so the dateless ID *is* the snapshot.
        assert vendor_for(name).validate_model(model, source="t") == model

    @pytest.mark.parametrize("name", ["anthropic", "vertex"])
    @pytest.mark.parametrize(
        "model",
        [
            # The pre-4.6 dated forms, direct and on Vertex.
            "claude-sonnet-4-5-20250929",
            "claude-haiku-4-5@20251001",
            # The old name-after-version scheme, and the alias that fronted it.
            "claude-3-7-sonnet-20250219",
            "claude-3-opus",
        ],
    )
    def test_pre_generation_forms_are_not_pinned_forms(self, name, model):
        with pytest.raises(ValueError, match="not pinned"):
            vendor_for(name).validate_model(model, source="t")

    @pytest.mark.parametrize("name", ["anthropic", "vertex"])
    @pytest.mark.parametrize(
        "model", ["claude-opus-4-20250514", "claude-sonnet-4-20250514"]
    )
    def test_a_date_is_not_a_minor_version(self, name, model):
        """The minor group was ``\\d+``, so it read a date as a version.

        Both halves of the rule were wrong at once. ``validate_model`` accepted
        a dated form the module's own comment says it rejects, and
        ``claude_generation`` returned ``(4, 20250514)`` — so the build-time
        sampling rule read a generation far above its floor and refused
        ``temperature`` on a Claude 4.0, which accepts it.
        """
        with pytest.raises(ValueError, match="not pinned"):
            vendor_for(name).validate_model(model, source="t")
        assert claude_generation(model) is None

    @pytest.mark.parametrize(
        ("model", "generation"),
        [
            ("claude-opus-5", (5, 0)),
            ("claude-sonnet-4-6", (4, 6)),
            ("claude-opus-4-1", (4, 1)),
            # Two digits is a minor version and stays one; the bound is on the
            # minor alone, so no generation count is too large to name.
            ("claude-opus-4-12", (4, 12)),
            ("claude-opus-123", (123, 0)),
        ],
    )
    def test_a_minor_version_still_reads_as_one(self, model, generation):
        assert claude_generation(model) == generation

    @pytest.mark.parametrize("name", ["anthropic", "vertex"])
    @pytest.mark.parametrize("model", ["claude-haiku-4-5", "claude-opus-4-1"])
    def test_an_older_generation_in_the_pinned_form_is_accepted(self, name, model):
        """No generation is too old to name: the rule reads shape, not version.

        These two are the case that decides it. Both are well-formed dateless
        IDs and both are older than the generation this service once floored
        at, so a rule that read the version would reject them and a rule that
        reads the shape takes them. Which model a deployment can afford to run
        is its own call, and the vendor serving the build is the authority on
        whether it still exists.
        """
        assert vendor_for(name).validate_model(model, source="t") == model

    def test_openai_has_no_canonical_form_to_require(self):
        assert vendor_for("openai").validate_model("o3", source="t") == "o3"
        assert vendor_for("openai").validate_model("gpt-4o", source="t") == "gpt-4o"

    def test_the_error_names_the_source_so_ops_finds_the_knob(self):
        with pytest.raises(ValueError, match="tiers.strong.model"):
            vendor_for("vertex").validate_model(
                "gemini-2.5-pro-latest", source="tiers.strong.model"
            )


class TestWhatTheRegistryDeliberatelyOmits:
    def test_there_is_no_per_vendor_sampling_support_set(self):
        # #12: supportedness is a function of (vendor, model) and lives in
        # LiteLLM's config classes, so mirroring it here forks a subsystem that
        # drifts silently against the gate that actually fires.
        assert not hasattr(vendor_for("vertex"), "supported")

    def test_the_reasoning_kwarg_is_a_constant_not_a_field(self):
        # The reasoning surface is uniform, so a per-vendor field would mirror
        # the same value three times.
        assert REASONING_KWARG == "reasoning_effort"
        assert not hasattr(vendor_for("vertex"), "reasoning_kwarg")

    def test_entries_are_frozen(self):
        with pytest.raises(FrozenInstanceError):
            vendor_for("vertex").prefix = "nope/"

    def test_every_named_vendor_has_an_entry(self):
        assert {vendor_for(name).name for name in VENDOR_NAMES} == set(VENDOR_NAMES)
        assert all(isinstance(vendor_for(name), Vendor) for name in VENDOR_NAMES)


class TestOpenAIReasoningFamilies:
    """Which identifiers pin ``temperature`` at their own default.

    A family rule rather than a support table, and open at the top: an
    unrecognised ``gpt-6`` reads as reasoning, because a false positive costs
    one clear error at startup and a false negative costs node one of a paid
    job.
    """

    @pytest.mark.parametrize(
        "model",
        ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6", "gpt-5", "o3", "o4-mini"],
    )
    def test_reasoning_families_are_recognised(self, model):
        assert openai_reasoning_model(model)

    @pytest.mark.parametrize(
        "model",
        ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "claude-opus-5", "gemini-2.5-pro"],
    )
    def test_everything_else_is_left_to_the_supported_param_gate(self, model):
        assert not openai_reasoning_model(model)

    def test_gpt_4o_is_parsed_rather_than_special_cased(self):
        """The one identifier whose trailing letter could read as a suffix."""
        assert not openai_reasoning_model("gpt-4o")
        assert openai_reasoning_model("gpt-5o")


class TestTheRouteToVendorRule:
    """One reader, built by inverting the registry on its own prefixes."""

    @pytest.mark.parametrize("name", VENDOR_NAMES)
    def test_a_route_resolves_to_the_vendor_that_built_it(self, name):
        vendor = vendor_for(name)
        assert vendor_for_route(vendor.route("some-model")) is vendor

    def test_a_bare_name_raises_rather_than_inventing_a_vendor(self):
        # A fingerprint that names a provider which never ran is worse than an
        # error, and it is the failure a pass-through would have produced.
        with pytest.raises(ValueError, match="no vendor prefix"):
            vendor_for_route("gemini-2.5-pro")

    def test_a_prefix_no_vendor_claims_raises(self):
        with pytest.raises(ValueError, match="no vendor serves"):
            vendor_for_route("cohere/command-r")

    @pytest.mark.parametrize("name", VENDOR_NAMES)
    def test_the_join_reattaches_the_requesting_vendors_prefix(self, name):
        # Providers return a bare build; the vendor comes from what was asked
        # for. Two vendors serving one build must not join to one route.
        vendor = vendor_for(name)
        assert join_served(vendor.route("requested"), "served-002") == (
            f"{vendor.prefix}served-002"
        )

    @pytest.mark.parametrize("name", VENDOR_NAMES)
    def test_every_vendor_states_what_its_served_build_is_worth(self, name):
        assert vendor_for(name).served_trust in get_args(ServedTrust)
