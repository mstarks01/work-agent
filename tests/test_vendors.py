"""The provider registry: routing, credential mode, and the pinned-form rule.

The registry holds only what nothing else can supply. Two things it deliberately
does *not* hold are asserted here too, because their absence is a decision
rather than an omission: per-``(vendor, model)`` sampling support (#12 moved it
to a call) and a per-vendor reasoning kwarg (#15 made the surface uniform).
"""

from __future__ import annotations

import re
import sys
import tomllib
from collections.abc import Callable
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import ClassVar, get_args

import pytest

from analysis_service.vendors import (
    _CATCH_ALL,
    _CLAUDE_RULE,
    CREDENTIAL_MODE_NOTES,
    REASONING_KWARG,
    VENDOR_NAMES,
    VENDORS,
    CredentialMode,
    ProviderAuthError,
    ServedTrust,
    Vendor,
    VendorSdkError,
    claude_generation,
    join_served,
    missing_sdk,
    openai_reasoning_model,
    require_sdk,
    vendor_for,
    vendor_for_route,
)
from tests.factories import AMBIENT_KEY_VARS

PROJECT_ROOT = Path(__file__).resolve().parents[1]

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


class TestVendorSdks:
    """The client library a vendor's provider needs, a field no row may skip."""

    def test_every_extra_the_registry_names_exists_in_pyproject(self):
        """The registry checked against the file that would install from it.

        Two readers of one fact — the registry names an extra and packaging
        defines one — so they are checked against each other rather than each
        against its own expectation. An extra named here and absent there sends
        an operator to an install command that fails.
        """
        pyproject = tomllib.loads(
            (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        defined = set(pyproject["project"].get("optional-dependencies", {}))
        named = {vendor.sdk.extra for vendor in VENDORS.values() if vendor.sdk}
        assert named <= defined, (
            f"these extras are named by the registry and defined nowhere:"
            f" {sorted(named - defined)}"
        )

    def test_a_vendor_that_needs_nothing_reports_nothing_missing(self):
        for name in VENDOR_NAMES:
            if vendor_for(name).sdk is None:
                assert missing_sdk(name) is None
                require_sdk(name)

    def test_an_absent_library_fails_closed_naming_the_extra(self, monkeypatch):
        """The gate, driven with the module hidden rather than uninstalled.

        The dev environment installs the extra, because the offline conformance
        suite binds an adapter for every vendor. So the absent case is reached
        by making the probe answer the way it would on an image without it,
        which is the same call the gate makes.
        """
        monkeypatch.setattr(
            "analysis_service.vendors.importlib.util.find_spec", lambda name: None
        )
        with pytest.raises(VendorSdkError) as raised:
            require_sdk("bedrock")
        message = str(raised.value)
        assert "boto3" in message
        assert "analysis-service[bedrock]" in message

    def test_the_probe_never_imports_the_library(self, monkeypatch):
        """Asking whether a library is there must not pull it in.

        Two costs, and the first is why the rule exists.
        ``tests/test_identity.py`` asserts that every module ``src/`` imports is
        declared in ``project.dependencies``, so an ``import boto3`` here would
        turn an optional extra into a hard dependency of the wheel. The second
        is the diagnostic page, which calls this on every render and must not
        pay a vendor SDK's import for it.
        """
        monkeypatch.delitem(sys.modules, "boto3", raising=False)
        assert missing_sdk("bedrock") is None
        assert "boto3" not in sys.modules


class TestCredentialModes:
    """The mechanism is declared; the material may be discovered."""

    def test_every_mode_tells_an_operator_what_to_arrange(self):
        assert set(CREDENTIAL_MODE_NOTES) == set(CredentialMode)

    def test_vertex_is_platform_identity_because_it_admits_no_api_key_path(self):
        assert vendor_for("vertex").credential_modes == (CredentialMode.IAM,)

    @pytest.mark.parametrize("name", ["anthropic", "openai"])
    def test_the_api_key_vendors_are_api_key(self, name):
        assert vendor_for(name).credential_modes == (CredentialMode.API_KEY,)

    @pytest.mark.parametrize(
        ("name", "mode"),
        [
            (name, mode)
            for name, vendor in VENDORS.items()
            for mode in vendor.credential_modes
            if mode is CredentialMode.IAM
        ],
    )
    def test_a_platform_identity_mode_passes_no_ambient_credential(self, name, mode):
        """ "Pass no credential material" is not the same as "pass nothing".

        litellm reads ``AWS_BEARER_TOKEN_BEDROCK`` out of the process
        environment whenever ``api_key`` is ``None``, sets an ``Authorization``
        header from it and skips request signing entirely. So an *absent*
        ``api_key`` means "look in the environment", which is the ASI03
        inherited-credential path this registry exists to close.

        The rule is asserted for every platform-identity pair rather than for
        the one vendor that has the problem: a provider whose SDK reads an
        ambient credential is what makes the difference, and there is no reason
        the next one will not.
        """
        vendor = vendor_for(name)
        env = dict.fromkeys(vendor.required_env_vars(mode), "value")
        kwargs = vendor.credential_kwargs(env, mode)
        assert kwargs.get("api_key", "") == "", (
            f"{name} under {mode.value} passes a credential value; this mode"
            " passes none"
        )
        assert not vendor.secret_env_vars(mode)

    def test_the_key_var_is_vendor_scoped(self):
        assert vendor_for("anthropic").api_key_var == "ANALYSIS_ANTHROPIC_API_KEY"
        assert vendor_for("openai").api_key_var == "ANALYSIS_OPENAI_API_KEY"
        assert vendor_for("gemini").api_key_var == "ANALYSIS_GEMINI_API_KEY"

    def test_a_vendor_with_a_choice_refuses_to_answer_for_the_deployment(self):
        """A caller that never learned about the choice must not make it.

        Picking the first entry here would let a build authenticate under a
        mode nobody declared. The config is what holds the answer, and this
        raise is what sends the caller there.
        """
        with pytest.raises(ValueError, match="allows 2 credential modes"):
            _ = vendor_for("bedrock").sole_credential_mode

    def test_the_multi_mode_vendor_reads_its_own_variables_per_mode(self):
        """Each mode names what it needs and nothing else.

        The region is required under both, and is a credential under neither.
        The key mode adds the bearer; the platform-identity mode adds nothing,
        which is what lets the vendor's SDK resolve an identity of its own.
        """
        bedrock = vendor_for("bedrock")
        env = {
            "ANALYSIS_BEDROCK_API_KEY": API_KEY,
            "ANALYSIS_BEDROCK_REGION": "us-east-1",
        }
        assert bedrock.credential_kwargs(env, CredentialMode.API_KEY) == {
            "api_key": API_KEY,
            "aws_region_name": "us-east-1",
        }
        # ``api_key`` is stated and empty rather than absent: absent is what
        # sends litellm to the process environment for a bearer token.
        assert bedrock.credential_kwargs(env, CredentialMode.IAM) == {
            "api_key": "",
            "aws_region_name": "us-east-1",
        }
        assert bedrock.secret_env_vars(CredentialMode.API_KEY) == (
            "ANALYSIS_BEDROCK_API_KEY",
        )
        assert bedrock.secret_env_vars(CredentialMode.IAM) == ()

    def test_a_mode_a_vendor_does_not_allow_raises(self):
        with pytest.raises(ValueError, match="no 'api_key' credential mode"):
            vendor_for("vertex").credential_kwargs({}, CredentialMode.API_KEY)

    def test_the_key_is_read_from_the_vendor_scoped_var(self):
        kwargs = vendor_for("anthropic").credential_kwargs(
            {"ANALYSIS_ANTHROPIC_API_KEY": API_KEY}, CredentialMode.API_KEY
        )
        assert kwargs == {"api_key": API_KEY}

    # Read from ``VENDORS`` rather than from ``VENDOR_NAMES``: this check runs
    # at collection time, and a vendor added to ``VENDOR_NAMES`` before its
    # ``VENDORS`` row would raise here and stop the whole suite from collecting
    # — including ``test_vendor_neutrality``, whose message is what names the
    # missing row. A guard that cannot run when the tree is half-built helps
    # nobody.
    def test_every_key_bearing_vendor_names_its_ambient_variables(self):
        takes_a_key = {
            name
            for name, vendor in VENDORS.items()
            if CredentialMode.API_KEY in vendor.credential_modes
        }
        assert set(AMBIENT_KEY_VARS) == takes_a_key

    @pytest.mark.parametrize(
        ("name", "ambient"),
        [(name, var) for name, vars_ in AMBIENT_KEY_VARS.items() for var in vars_],
    )
    def test_an_ambient_key_authenticates_nothing(self, name, ambient):
        """Every vendor that takes a key, not just the one somebody tested.

        An undeclared credential in the process environment is the ASI03
        inherited-credential path. LiteLLM picks up ``ANTHROPIC_API_KEY`` and
        ``OPENAI_API_KEY`` on its own and cannot be told not to, so the refusal
        has to happen in the registry — which is the same reason the *declared*
        half of the rule is enforced here rather than in the adapter.
        """
        vendor = vendor_for(name)
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
        for vendor in VENDORS.values():
            for source in vendor.credentials.values():
                for entry in source.env:
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
    @pytest.mark.parametrize(
        ("vendor", "mode"),
        sorted((name, mode) for name, v in VENDORS.items() for mode in v.credentials),
    )
    def test_the_secret_set_is_a_subset_of_the_required_set(self, vendor, mode):
        entry = vendor_for(vendor)
        assert set(entry.secret_env_vars(mode)) <= set(entry.required_env_vars(mode))


#: The bare spelling's minor version, which OpenRouter writes with a dot. Used
#: only to restate a bare identifier in that vendor's spelling, so the
#: portability property can be asserted against the rule rather than against a
#: second list of identifiers.
_MODERN_MINOR = re.compile(r"^(claude-[a-z]+-\d+)-(\d{1,2})$")


def _dotted_minor(model: str) -> str:
    """``claude-sonnet-4-6`` in OpenRouter's spelling; anything else unchanged."""
    return _MODERN_MINOR.sub(r"\1.\2", model)


def _bare(model: str) -> str:
    """The spelling Anthropic publishes, which three other vendors serve too."""
    return model


#: How each vendor spells one Claude identifier. A **family** rule follows the
#: family, so which identifiers are legal must differ between two vendors by
#: exactly this transform and by nothing else — which is the property the tests
#: below assert in both directions, acceptance and refusal.
#:
#: Keyed by vendor and checked against the registry twice: for completeness,
#: and for agreement with the rule objects the registry actually lists. The
#: second check is what stops this table from being a second opinion — a vendor
#: whose row pins a shape this table does not spell would otherwise pass every
#: assertion below against the wrong string.
CLAUDE_SPELLING: dict[str, Callable[[str], str]] = {
    "anthropic": _bare,
    "gemini": _bare,
    "openai": _bare,
    "vertex": _bare,
    "bedrock": lambda model: f"anthropic.{model}",
    "openrouter": lambda model: f"anthropic/{_dotted_minor(model)}",
}


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

    #: The vendors that spell the Claude family the bare way, derived from the
    #: table above. The dated and pre-4.6 forms are the cases that decide it:
    #: they were refused on ``vertex`` and ``anthropic`` and accepted on
    #: ``openai``, because that row listed the catch-all alone.
    BARE_SPELLING: ClassVar[tuple[str, ...]] = tuple(
        name for name, spell in CLAUDE_SPELLING.items() if spell is _bare
    )

    def test_the_spelling_table_answers_for_every_vendor(self):
        """A vendor the table does not spell is a vendor nothing below reads."""
        assert set(CLAUDE_SPELLING) == set(VENDOR_NAMES)

    def test_the_bare_group_is_the_group_the_registry_says_it_is(self):
        """The table and the registry, checked against each other.

        A ``BARE_SPELLING`` that read "every vendor except bedrock" would let a
        third spelling join the bare group silently, and every portability
        assertion below would go on passing against an identifier that vendor
        does not serve. So the group is derived from the
        table, and the table is compared with the rule objects the rows list.
        """
        from_registry = {
            name for name in VENDOR_NAMES if _CLAUDE_RULE in VENDORS[name].form_rules
        }
        assert set(self.BARE_SPELLING) == from_registry

    #: One identifier, one verdict, on every vendor that spells the family this
    #: way.
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
        # The name-after-version order.
        "claude-3-5-sonnet-20241022": False,
        "claude-3-7-sonnet": False,
    }

    #: Bedrock spellings a bare-spelling vendor must refuse, beyond what the
    #: transform above produces: a region scope, and a dated build tail.
    EXTRA_BEDROCK_SPELLINGS: ClassVar[tuple[str, ...]] = (
        "us.anthropic.claude-opus-5",
        "anthropic.claude-3-5-sonnet-20240620-v1:0",
    )

    @pytest.mark.parametrize("name", sorted(CLAUDE_SPELLING))
    @pytest.mark.parametrize(
        "model", sorted(name for name, ok in CLAUDE_VERDICTS.items() if ok)
    )
    def test_a_route_pasted_into_the_model_field_is_refused(self, model, name):
        """The mistake an aggregator makes easy, and it reached no rule.

        ``route`` joins the vendor prefix onto the model, so an operator who
        copies the whole route into ``model`` gets it joined a second time.
        While the family read one optional gateway segment,
        ``openrouter/anthropic/claude-opus-4.7`` matched no Claude rule, passed
        unpinned through the catch-all, and the job died on node one against
        ``openrouter/openrouter/anthropic/claude-opus-4.7``.
        """
        vendor = vendor_for(name)
        doubled = f"{vendor.prefix}{CLAUDE_SPELLING[name](model)}"
        with pytest.raises(ValueError, match="not pinned"):
            vendor.validate_model(doubled, source="t")

    @pytest.mark.parametrize("name", BARE_SPELLING)
    @pytest.mark.parametrize("model", EXTRA_BEDROCK_SPELLINGS)
    def test_a_scoped_bedrock_spelling_is_refused_where_it_is_not_served(
        self, name, model
    ):
        """The other half of the copy-paste, and it was open until #603's review.

        One family pattern reads every spelling any vendor gives the family, so
        a row copied *out* of the bedrock table meets a rule and fails its shape
        with a hint. Reaching the catch-all instead would load the config, build
        the route `anthropic/anthropic.claude-opus-5`, and die on node one.
        """
        with pytest.raises(ValueError, match="not pinned"):
            vendor_for(name).validate_model(model, source="t")

    @pytest.mark.parametrize("name", BARE_SPELLING)
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

    @pytest.mark.parametrize("name", sorted(CLAUDE_SPELLING))
    @pytest.mark.parametrize(
        "model", sorted(name for name, ok in CLAUDE_VERDICTS.items() if ok)
    )
    def test_every_pinned_claude_is_pinned_under_every_spelling(self, model, name):
        """Moving a Claude tier changes the spelling and nothing else.

        The portability property, and the half that must hold both ways: every
        identifier the bare-spelling vendors pin is pinned on every vendor,
        once that vendor's own segments are in front of it and its own minor
        separator is inside it.

        The refusals are **not** mirrored, and that is a decision rather than a
        gap. A dated form is refused where the vendor also serves the bare name
        as a floating alias, because the two cannot be told apart; Bedrock
        serves no such alias, so the same date is a published build there. That
        is a property of the catalogue, and the cases sit below.
        """
        spelled = CLAUDE_SPELLING[name](model)
        assert vendor_for(name).validate_model(spelled, source="t") == spelled

    @pytest.mark.parametrize(
        ("name", "other"),
        sorted(
            (name, other)
            for name in CLAUDE_SPELLING
            for other in CLAUDE_SPELLING
            if name != other
        ),
    )
    @pytest.mark.parametrize(
        "model", sorted(name for name, ok in CLAUDE_VERDICTS.items() if ok)
    )
    def test_one_vendors_spelling_is_refused_by_every_vendor_that_spells_it_otherwise(
        self, model, name, other
    ):
        """The copy-paste caught in every direction, not only out of Bedrock.

        A tier row moved from one vendor to another keeps the first vendor's
        spelling. It has to meet the shared family and fail the second
        vendor's shape with a hint, rather than pass unpinned through the
        catch-all and die on node one.

        Two vendors that spell one identifier the same way have nothing to
        refuse, so those pairs assert acceptance instead — which is the
        portability property above, restated where it would otherwise be
        skipped.
        """
        spelled = CLAUDE_SPELLING[other](model)
        vendor = vendor_for(name)
        if spelled == CLAUDE_SPELLING[name](model):
            assert vendor.validate_model(spelled, source="t") == spelled
            return
        with pytest.raises(ValueError, match="not pinned"):
            vendor.validate_model(spelled, source="t")

    @pytest.mark.parametrize(
        "model",
        [
            # The bare spelling, which is what a config copying an
            # anthropic-direct tier row into the bedrock row produces. It meets
            # the broad family and fails the strict shape, rather than passing
            # unpinned through the catch-all.
            "claude-opus-5",
            "claude-sonnet-4-5-20250929-v1:0",
            # Vertex's ``@date`` spelling, on one key against 148.
            "anthropic.claude-haiku-4-5@20251001",
            # The 2023 names, which carry no family name and generation.
            "anthropic.claude-v1",
            "anthropic.claude-v2:1",
            "anthropic.claude-instant-v1",
        ],
    )
    def test_a_bedrock_claude_outside_the_shape_is_refused(self, model):
        with pytest.raises(ValueError):
            vendor_for("bedrock").validate_model(model, source="t")

    @pytest.mark.parametrize(
        "model",
        [
            # A scope is matched as a shape and never enumerated: the pinned
            # map carries seven, one of them hyphenated and one naming no
            # region at all.
            "us.anthropic.claude-opus-5",
            "us-gov.anthropic.claude-sonnet-4-6",
            "global.anthropic.claude-opus-5",
            # A date passes here and nowhere else, because Bedrock serves no
            # bare alias the dated form could be confused with.
            "anthropic.claude-sonnet-4-5-20250929-v1:0",
            "eu.anthropic.claude-3-5-sonnet-20241022-v2:0",
            # A build tail is part of the canonical AWS identifier.
            "anthropic.claude-opus-4-6-v1",
        ],
    )
    def test_a_bedrock_claude_in_the_shape_is_pinned(self, model):
        assert vendor_for("bedrock").validate_model(model, source="t") == model

    @pytest.mark.parametrize("name", VENDOR_NAMES)
    @pytest.mark.parametrize(
        "model",
        [
            # A provisioned-throughput ARN, and an inference profile naming a
            # family that has no shape rule at all.
            "arn:aws:bedrock:us-east-1:123456789012:provisioned-model/abc",
            "arn:aws:bedrock:us-east-1:123456789012:inference-profile/nova",
            # The three router forms litellm resolves an ARN behind. An ARN
            # does not have to start the identifier, and a rule that read
            # position zero refused only the shape nobody writes.
            "invoke/arn:aws:bedrock:us-east-1:123456789012:provisioned-model/a",
            "converse/arn:aws:bedrock:us-east-1:123456789012:inference-profile/x",
            "anthropic/arn:aws:bedrock:us-east-1:123456789012:imported-model/y",
            # Case is not part of the property either.
            "invoke/ARN:aws:bedrock:us-east-1:123456789012:imported-model/y",
            # Google's resource paths, which the rule admitted while refusing
            # the AWS one. `litellm.get_llm_provider` resolves each of these to
            # a provider exactly as it resolves an ARN.
            #
            # A Vertex endpoint is the repointable one: it names no weights at
            # all, so a blessing over it certifies whatever it is pointed at
            # next.
            "projects/acme-prod/locations/us-central1/endpoints/1234567890",
            # A full resource name for a public model carries the project even
            # though it does name the model. The project is the account half of
            # the property, and it would reach a fingerprint and a report.
            "projects/acme-prod/locations/us-central1/publishers/google/models/gemini-2.5-pro",
            # The Developer API's own resource, reached under `gemini/`. It
            # names no base build, which is the first property outright.
            "tunedModels/my-tuned-abc123",
            # Behind a router segment and in another case, for the same reason
            # the ARN forms above are listed three ways.
            "vertex_ai/projects/p/locations/l/endpoints/9",
            "TunedModels/my-tuned-abc123",
        ],
    )
    def test_an_identifier_naming_a_resource_is_refused_on_every_vendor(
        self, name, model
    ):
        """A resource is not a build, and the rule follows that rather than a cloud.

        Refused for every vendor, and outside the Claude shape, because both
        reasons are properties of a resource identifier: it can be repointed at
        other weights, so a blessed fingerprint would go on certifying it, and
        it carries the account that owns it into a fingerprint and a report.
        A rule that lived in the Claude shape would leave a Nova ARN accepted.

        **The Google forms are here because the rule was one cloud's syntax.**
        The docstring said "on two properties rather than on whose syntax it
        is" and the pattern was ``arn:``, so a Vertex endpoint and a Developer
        API tuned model both passed. Parametrizing over every vendor is what
        keeps the next spelling from being a `bedrock`-only thought.
        """
        with pytest.raises(ValueError, match="rather than a model build"):
            vendor_for(name).validate_model(model, source="t")

    @pytest.mark.parametrize("name", VENDOR_NAMES)
    @pytest.mark.parametrize(
        "model",
        [
            # The Developer API's resource form for a *public* model. It names
            # the build and carries no account, so neither property holds and
            # refusing it would refuse a model under a published name.
            "models/gemini-2.5-pro",
            # Ordinary identifiers that merely contain a listed word.
            "gemini-2.5-pro",
            "claude-opus-5",
        ],
    )
    def test_an_identifier_that_is_not_a_resource_still_passes_the_check(
        self, name, model
    ):
        """The refusal is narrow: a resource, never anything shaped like one."""
        try:
            vendor_for(name).validate_model(model, source="t")
        except ValueError as exc:
            assert "rather than a model build" not in str(exc), (
                f"{model!r} was refused as a resource on {name}"
            )

    @pytest.mark.parametrize(
        "model",
        [
            # Everything else Bedrock serves reaches the catch-all, scope and
            # all, where only the shared denylist applies.
            "amazon.nova-pro-v1:0",
            "us.amazon.nova-pro-v1:0",
            "meta.llama3-70b-instruct-v1:0",
            "mistral.mistral-large-2407-v1:0",
        ],
    )
    def test_a_bedrock_model_outside_the_claude_family_reaches_the_catch_all(
        self, model
    ):
        assert vendor_for("bedrock").validate_model(model, source="t") == model

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
            # The name-after-version scheme, and the alias that fronts it.
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

        The two answers are separate on purpose. These vendors refuse the dated
        *shape*, and the parse still reads the generation the identifier names,
        because a date is not part of a version — the same identifier reaches
        the parse from Bedrock, where the shape is legal.
        """
        with pytest.raises(ValueError, match="not pinned"):
            vendor_for(name).validate_model(model, source="t")
        assert claude_generation(model) == (4, 0)

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

    def test_a_family_is_a_pattern_matched_at_the_start(self):
        """The family is a `re.Pattern`, and it anchors where a prefix did.

        A prefix cannot say "broad here, strict there": a Bedrock Claude that
        omits ``anthropic.`` has to reach the Claude rule and fail its shape
        with a hint, rather than pass unpinned through the catch-all. A pattern
        can. It is matched rather than searched, so a family name buried inside
        an identifier still reaches the catch-all — ``search`` would refuse
        ``inhouse-claude-router``, which no vendor rule has an opinion on.
        """
        vendor = vendor_for("anthropic")
        assert isinstance(_CLAUDE_RULE.family, re.Pattern)
        assert vendor._rule_for("claude-opus-5") is _CLAUDE_RULE
        assert vendor._rule_for("inhouse-claude-router") is _CATCH_ALL

    @pytest.mark.parametrize("name", VENDOR_NAMES)
    @pytest.mark.parametrize(
        "model",
        ["claude-opus-5", "gemini-2.5-pro", "o3", "amazon.titan-text-express-v1", "-"],
    )
    def test_every_identifier_reaches_a_rule(self, name, model):
        """The catch-all matches everything, so `_rule_for` cannot raise.

        `_rule_for` reads the first matching rule out of a generator, and a
        generator with no match raises `StopIteration` rather than returning a
        default. The catch-all's empty pattern is what stops that, and the
        empty pattern is easy to lose when a family becomes a real one.
        """
        assert vendor_for(name)._rule_for(model) is not None


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
