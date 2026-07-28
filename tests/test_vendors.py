"""The provider registry: routing, credential mode, and the pinned-form rule.

The registry holds only what nothing else can supply. Two things it deliberately
does *not* hold are asserted here too, because their absence is a decision
rather than an omission: per-``(vendor, model)`` sampling support (#12 moved it
to a call) and a per-vendor reasoning kwarg (#15 made the surface uniform).
"""

from __future__ import annotations

import pytest

from stride_service.vendors import (
    REASONING_KWARG,
    VENDOR_NAMES,
    CredentialMode,
    ProviderAuthError,
    Vendor,
    vendor_for,
)

API_KEY = "sk-test-not-a-real-key"


class TestRouting:
    def test_the_route_joins_prefix_to_model(self):
        assert vendor_for("vertex").route("gemini-2.5-pro") == (
            "vertex_ai/gemini-2.5-pro"
        )
        assert vendor_for("anthropic").route("claude-sonnet-4-5-20250929") == (
            "anthropic/claude-sonnet-4-5-20250929"
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


class TestCredentialMode:
    """The vendor implies the mode; config never picks it (#9)."""

    def test_vertex_is_adc_because_it_admits_no_api_key_path(self):
        assert vendor_for("vertex").credential is CredentialMode.ADC

    @pytest.mark.parametrize("name", ["anthropic", "openai"])
    def test_the_api_key_vendors_are_api_key(self, name):
        assert vendor_for(name).credential is CredentialMode.API_KEY

    def test_the_key_var_is_vendor_scoped(self):
        assert vendor_for("anthropic").api_key_var == "STRIDE_ANTHROPIC_API_KEY"
        assert vendor_for("openai").api_key_var == "STRIDE_OPENAI_API_KEY"

    def test_the_key_is_read_from_the_vendor_scoped_var(self):
        kwargs = vendor_for("anthropic").credential_kwargs(
            {"STRIDE_ANTHROPIC_API_KEY": API_KEY}
        )
        assert kwargs == {"api_key": API_KEY}

    def test_litellms_ambient_key_is_deliberately_not_used(self):
        # An undeclared credential in the process environment is the ASI03
        # inherited-credential path; only what this deployment declared may
        # authenticate a run.
        with pytest.raises(ProviderAuthError):
            vendor_for("anthropic").credential_kwargs({"ANTHROPIC_API_KEY": API_KEY})

    def test_a_missing_key_fails_closed_naming_the_var_not_the_value(self):
        with pytest.raises(ProviderAuthError) as excinfo:
            vendor_for("openai").credential_kwargs({})
        assert "STRIDE_OPENAI_API_KEY" in str(excinfo.value)

    def test_an_empty_key_is_a_deploy_mistake_not_an_absence(self):
        with pytest.raises(ProviderAuthError):
            vendor_for("openai").credential_kwargs({"STRIDE_OPENAI_API_KEY": "   "})

    def test_a_key_value_never_appears_in_the_error(self):
        # OWASP A09: a key echoed into a log or a problem+json body has leaked.
        with pytest.raises(ProviderAuthError) as excinfo:
            vendor_for("openai").credential_kwargs({"STRIDE_OPENAI_API_KEY": ""})
        assert API_KEY not in str(excinfo.value)

    def test_vertex_needs_project_location_and_adc(self):
        kwargs = vendor_for("vertex").credential_kwargs(
            {
                "STRIDE_VERTEX_PROJECT": "p",
                "STRIDE_VERTEX_LOCATION": "us-central1",
                "GOOGLE_APPLICATION_CREDENTIALS": "/adc.json",
            }
        )
        assert kwargs["vertex_project"] == "p"
        assert kwargs["vertex_location"] == "us-central1"

    @pytest.mark.parametrize(
        "missing",
        [
            "STRIDE_VERTEX_PROJECT",
            "STRIDE_VERTEX_LOCATION",
            "GOOGLE_APPLICATION_CREDENTIALS",
        ],
    )
    def test_each_vertex_variable_is_required(self, missing):
        env = {
            "STRIDE_VERTEX_PROJECT": "p",
            "STRIDE_VERTEX_LOCATION": "us-central1",
            "GOOGLE_APPLICATION_CREDENTIALS": "/adc.json",
        }
        del env[missing]
        with pytest.raises(ProviderAuthError, match=missing):
            vendor_for("vertex").credential_kwargs(env)


class TestPinnedFormRule:
    """Deliberately an open-world denylist, and deliberately weak."""

    @pytest.mark.parametrize("name", VENDOR_NAMES)
    def test_aliases_and_pre_ga_builds_are_rejected_everywhere(self, name):
        vendor = vendor_for(name)
        for bad in ("model-latest", "model-preview-06-05", "model-exp"):
            with pytest.raises(ValueError):
                vendor.validate_model(bad, source="t")

    @pytest.mark.parametrize("bad", ["", "  ", " gemini-2.5-pro"])
    def test_non_identifiers_are_rejected(self, bad):
        with pytest.raises(ValueError, match="not a model identifier"):
            vendor_for("vertex").validate_model(bad, source="t")

    def test_the_vertex_rule_branches_on_model_family(self):
        vertex = vendor_for("vertex")
        # Gemini 2.5+ ships no numbered stable builds, so bare is most specific.
        assert vertex.validate_model("gemini-2.5-pro", source="t")
        # Vertex Claude does publish a dated build, so bare is a floating alias.
        assert vertex.validate_model("claude-sonnet-4-5@20250929", source="t")
        with pytest.raises(ValueError, match="not pinned"):
            vertex.validate_model("claude-sonnet-4-5", source="t")

    def test_anthropic_direct_uses_its_own_dated_suffix(self):
        anthropic = vendor_for("anthropic")
        assert anthropic.validate_model("claude-sonnet-4-5-20250929", source="t")
        with pytest.raises(ValueError, match="not pinned"):
            anthropic.validate_model("claude-sonnet-4-5", source="t")

    def test_openai_has_no_dated_form_to_require(self):
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
        # #15 decision 3 made the surface uniform, so a per-vendor field would
        # mirror the same value three times.
        assert REASONING_KWARG == "reasoning_effort"
        assert not hasattr(vendor_for("vertex"), "reasoning_kwarg")

    def test_entries_are_frozen(self):
        with pytest.raises(Exception):
            vendor_for("vertex").prefix = "nope/"

    def test_every_named_vendor_has_an_entry(self):
        assert {vendor_for(name).name for name in VENDOR_NAMES} == set(VENDOR_NAMES)
        assert all(isinstance(vendor_for(name), Vendor) for name in VENDOR_NAMES)
