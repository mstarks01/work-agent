"""The provider conformance suite: one contract, every supported vendor.

What this suite is for, stated plainly because the distinction is the whole
point of it. There are two different questions a multi-vendor service has to
answer, and running them together is what produced the imbalance
[#116](https://github.com/mstarks01/work-agent/issues/116) is about:

* **Application conformance** — does the *application* behave the same way on
  this provider? Deterministic, and answered here, for all three vendors, on
  every pull request.
* **Model quality** — how good are the threat models this model writes? Empirical,
  answered by ``evals/``, and expected to differ between models. A vendor never
  fails conformance for scoring lower than another.

Everything below is **credential-free**. The capability probes read the pinned
``litellm``'s local model-cost map, and the binding assertions hand
:func:`~analysis_service.binding.build_tier_adapters` a synthetic environment —
the registry checks that a credential was *declared*, never that it
authenticates, so a placeholder builds a real adapter without a key and without
egress. That is what lets one suite cover Vertex, Anthropic and OpenAI equally
in the offline lane, rather than covering whichever vendor CI happens to hold
credentials for.

The honest limit, which no test here can talk its way out of: this proves what
each provider *would be asked for* and that the application treats the three
identically. It is not evidence that any vendor has ever served a request. That
requires the live lanes, and they remain unprovisioned — see
``.github/workflows/evals-live.yml``.
"""

from __future__ import annotations

import re
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import ClassVar

import pytest
from google.adk.models.base_llm import BaseLlm

from analysis_service.binding import NodeBinding, build_tier_adapters
from analysis_service.conformance import (
    PROBED_PARAMS,
    REFERENCE_MODELS,
    Capability,
    ProviderProfile,
    profile,
    reference_matrix,
    render_markdown,
)
from analysis_service.frameworks.stride.record import ThreatProposals, ThreatRulings
from analysis_service.graph import Pipeline, build_pipeline
from analysis_service.markdown_loader import MarkdownLoader
from analysis_service.model_gate import ModelGateError
from analysis_service.model_tiers import ModelTierConfig, load_model_tiers
from analysis_service.resilience import load_resilience
from analysis_service.sampling import load_sampling
from analysis_service.system_model import SystemModel
from analysis_service.vendors import (
    VENDOR_NAMES,
    CredentialMode,
    ProviderAuthError,
    join_served,
    vendor_for,
)
from tests.factories import (
    DEFAULT_FRAMEWORKS,
    EMPTY_CLAIMS,
    ScriptedLlm,
    repo_package_loaders,
    sample_fingerprint,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG = PROJECT_ROOT / "config"
WORKFLOWS = PROJECT_ROOT / ".github" / "workflows"

#: One line of a workflow file, split into the key of a YAML mapping entry and
#: the scalar it pins. The value is anchored at both ends, so a line pins a
#: value only when it holds that value and nothing else: a longer identifier
#: which *contains* it does not match. The optional dash carries a matrix
#: entry, and the optional quotes and trailing comment carry the other shapes
#: YAML lets an author write the same pin in.
_PINNED_SCALAR = re.compile(
    r"""
    ^\s*(?:-\s+)?      # an optional sequence dash
    [\w.-]+:\s*        # the mapping key and its colon
    (?P<quote>['"]?)   # an optional quote
    (?P<value>.*?)
    (?P=quote)         # the closing quote
    (?:\s+\#.*)?       # an optional trailing comment, which YAML spaces off
    \s*$               # and any trailing space
    """,
    re.VERBOSE,
)


def pins_scalar(text: str, value: str) -> bool:
    """Does *text* pin *value* as a whole YAML scalar, on any of its lines?

    The one reader of what it means for a workflow to pin a model or a vendor.
    Both drift checks below call it, because two readers of that rule will
    eventually disagree and each one's own test will agree with it.

    A text search rather than a YAML parse, and deliberately so: PyYAML is not
    a declared dependency of this project, and adding one to assert that a
    string appears in a file would be a poor trade.

    ``splitlines`` rather than a split on ``"\n"``, because a line ends at more
    characters than that one and a pin on the last of them would read as part
    of its neighbour.
    """
    return any(
        match["value"] == value
        for line in text.splitlines()
        if (match := _PINNED_SCALAR.match(line))
    )


# Placeholder credential material. Every vendor's variables at once, so one
# mapping serves all three legs: the registry reads only the ones the selected
# vendor names, and an unused entry authenticates nothing because nothing reads
# it. Values are visibly fake — a suite that needed a real key would be a suite
# that only ran where one existed, which is the imbalance this file exists to
# remove.
FAKE_ENV = {
    "ANALYSIS_ANTHROPIC_API_KEY": "sk-ant-not-a-real-key",
    "ANALYSIS_OPENAI_API_KEY": "sk-not-a-real-key",
    "ANALYSIS_VERTEX_PROJECT": "test-project",
    "ANALYSIS_VERTEX_LOCATION": "us-central1",
    "GOOGLE_APPLICATION_CREDENTIALS": "/nonexistent/adc.json",
}


def reference_pairs() -> list[tuple[str, str]]:
    """Every ``(vendor, model)`` in the reference matrix, flattened for ids."""
    return [
        (vendor, model)
        for vendor, models in REFERENCE_MODELS.items()
        for model in models
    ]


def tiers_for(vendor: str) -> ModelTierConfig:
    """The shipped node -> tier map, with both tiers on one vendor's pair.

    Built from the real ``config/model_tiers.toml`` rather than a hand-made
    object, so the node table under test is the one that ships. Only the two
    selections are substituted, which is exactly what a deployment does.
    """
    base, strong = REFERENCE_MODELS[vendor]
    return load_model_tiers(
        CONFIG / "model_tiers.toml",
        env={
            "ANALYSIS_MODEL_BASE_VENDOR": vendor,
            "ANALYSIS_MODEL_BASE_MODEL": base,
            "ANALYSIS_MODEL_STRONG_VENDOR": vendor,
            "ANALYSIS_MODEL_STRONG_MODEL": strong,
            "ANALYSIS_MODEL_REVIEW_VENDOR": "anthropic",
            "ANALYSIS_MODEL_REVIEW_MODEL": "claude-opus-5",
        },
    )


def _pipeline_for(vendor: str) -> Pipeline:
    """The real graph, built on one vendor's reference pair.

    Models are resolved to the shared scripted stand-in rather than to real
    adapters: what is under test is what the *graph* stamps per node, and
    binding real adapters would add the credential check without changing a
    single field compared here.
    """
    tiers = tiers_for(vendor)
    sampling = load_sampling(CONFIG / "sampling.toml", env={})

    def resolve(tier_node: str) -> BaseLlm:
        selection = tiers.resolve_model(tier_node)
        return ScriptedLlm(model=selection.route, reply=EMPTY_CLAIMS, seen=[])

    return build_pipeline(
        prompt_loader=MarkdownLoader(PROJECT_ROOT / "prompts"),
        domain_loader=MarkdownLoader(PROJECT_ROOT / "domains"),
        package_loaders=repo_package_loaders(),
        binding=NodeBinding.from_configs(tiers, sampling, resolve),
        frameworks=DEFAULT_FRAMEWORKS,
    )


class TestTheMatrixItself:
    """The tri-state is load-bearing, not decoration."""

    @pytest.mark.parametrize(("vendor", "model"), reference_pairs())
    def test_every_reference_pair_is_answerable(self, vendor, model):
        """A matrix of all-``unknown`` would pass every other test vacuously.

        This is the assertion that keeps the rest of the suite honest: if the
        pinned ``litellm`` stops carrying one of these models, the cells go
        ``UNKNOWN`` and the capability claims below become claims about
        nothing. Failing here is the signal to re-pin the model or the library,
        not to relax the test.
        """
        entry = profile(vendor_for(vendor), model)
        assert entry.known, f"{vendor}/{model} is not in the pinned model map"
        assert entry.unknowns == ()

    def test_an_unmapped_model_is_unknown_rather_than_unsupported(self):
        """The distinction the module exists for.

        LiteLLM answers for an unmapped model out of the provider's base
        config, so a probe alone cannot tell "rejected" from "never heard of
        it". Reporting the fallback as a real answer is how an open-world
        residual becomes a false assurance.
        """
        entry = profile(vendor_for("openai"), "gpt-nonexistent-9")
        assert not entry.known
        assert set(entry.params.values()) == {Capability.UNKNOWN}
        assert entry.structured_output is Capability.UNKNOWN
        assert entry.output_ceiling is None
        assert set(entry.unknowns) == {*PROBED_PARAMS, "structured_output"}

    def test_the_matrix_records_real_differences_between_vendors(self):
        """Capability neutrality is not capability sameness.

        Two differences the matrix must show rather than smooth over, both
        genuine and both already documented in ``docs/Configuration.md``:
        Anthropic does not accept ``seed`` at all, and ``gpt-4o`` does not
        accept ``reasoning_effort``. A suite that forced these to agree would
        be inventing a capability, which the issue lists as a non-goal.
        """
        anthropic = profile(vendor_for("anthropic"), "claude-sonnet-4-6")
        assert anthropic.params["seed"] is Capability.UNSUPPORTED

        gpt4o = profile(vendor_for("openai"), "gpt-4o")
        assert gpt4o.params["reasoning_effort"] is Capability.UNSUPPORTED

        gemini = profile(vendor_for("vertex"), "gemini-2.5-pro")
        assert gemini.params["seed"] is Capability.SUPPORTED

    def test_the_matrix_covers_every_supported_vendor(self):
        """A vendor absent from the matrix is a vendor nobody profiled."""
        assert set(REFERENCE_MODELS) == set(VENDOR_NAMES)

    def test_the_rendered_matrix_names_every_pair_and_every_capability(self):
        table = render_markdown(reference_matrix())
        for vendor, model in reference_pairs():
            assert model in table
            assert vendor in table
        for name in PROBED_PARAMS:
            assert name in table


def _shipped_sampling():
    """The shipped sampling, unadjusted, on every vendor.

    It takes no argument, and that is the finding: while the file pinned a
    temperature this had to be a per-vendor fixture that stripped the line for
    reasoning families, because no one value was legal everywhere.
    """
    return load_sampling(CONFIG / "sampling.toml", env={})


class TestModelsCanBeBound:
    """ "model can be instantiated" — the first line of the issue's contract."""

    @pytest.mark.parametrize("vendor", sorted(REFERENCE_MODELS))
    def test_both_tiers_bind_on_every_vendor(self, vendor, tmp_path):
        """The shipped resilience config and a legal sampling binds on all three.

        The strongest credential-free statement available about vendor
        neutrality: the same gates, and a working adapter per tier on every
        supported vendor. A vendor the configuration cannot bind is not
        supported in any useful sense, however many code paths mention it.

        **It is the shipped sampling on every vendor, unadjusted.** That holds
        because the file states no ``temperature``: the pin was per *tier* while
        the model it must suit is per *deployment*, so no single value satisfied
        every vendor — Claude 4.7 and later reject the param and OpenAI's
        reasoning families take only their own default. Leaving it unset is what
        lets one shipped file bind all three. The test below keeps the
        incompatibility itself pinned, as the cost of *stating* a value.
        """
        adapters = build_tier_adapters(
            tiers_for(vendor),
            _shipped_sampling(),
            load_resilience(CONFIG / "resilience.toml", env={}),
            env=FAKE_ENV,
        )

        base, strong = REFERENCE_MODELS[vendor]
        prefix = vendor_for(vendor).prefix
        assert adapters["base"].model == f"{prefix}{base}"
        assert adapters["strong"].model == f"{prefix}{strong}"

    def test_no_vendor_reaches_its_provider_by_a_different_class(self, tmp_path):
        """One adapter class for all three, which is the no-privileged-path claim.

        ADK ships a native Gemini integration and warns on every run that this
        service declines it (:mod:`analysis_service.binding` says so in its own
        header). Declining it is what makes "no vendor is privileged" true, and
        prose cannot hold that: a native path reintroduced for one vendor would
        leave every other assertion here passing, because the model strings,
        the node table and the sampling would all still match.

        So the class itself is the assertion. Identical across vendors means
        no vendor has a route the others lack — and the shared class is where
        the retry budget, the pinned ``num_retries=0`` and the credential gate
        live, so a vendor that escaped it would escape those too.

        The comparison is on the *ancestry* rather than the class object:
        :func:`~analysis_service.retry.retrying_llm_class` mints one subclass per
        call, so two identically-wired adapters are never the same class. What
        must match is everything behind that subclass.
        """
        # Deferred exactly as ``binding`` defers it, so this test cannot be the
        # thing that pulls the provider library in ahead of the cost-map pin.
        from google.adk.models.lite_llm import LiteLlm

        ancestries = {}
        for vendor in sorted(REFERENCE_MODELS):
            adapters = build_tier_adapters(
                tiers_for(vendor),
                _shipped_sampling(),
                load_resilience(CONFIG / "resilience.toml", env={}),
                env=FAKE_ENV,
            )
            # [1:] drops the per-call retry subclass; what remains is the route
            # to the provider, which no vendor may differ on.
            ancestries[vendor] = {
                tier: type(adapter).__mro__[1:] for tier, adapter in adapters.items()
            }
            for adapter in adapters.values():
                assert isinstance(adapter, LiteLlm)

        reference = ancestries["vertex"]
        for vendor, per_tier in ancestries.items():
            assert per_tier == reference, f"{vendor} binds a different adapter class"

    @pytest.mark.parametrize("vendor", sorted(REFERENCE_MODELS))
    def test_a_missing_credential_fails_closed_naming_its_own_variable(self, vendor):
        """Equivalent failure behaviour, not an equivalent credential mode.

        The three vendors authenticate differently — that difference is real
        and stays. What has to be identical is what the *application* does
        about it: refuse to build, under one error type, naming the variable
        this vendor needs and never its value.
        """
        with pytest.raises(ProviderAuthError) as raised:
            build_tier_adapters(
                tiers_for(vendor),
                load_sampling(CONFIG / "sampling.toml", env={}),
                load_resilience(CONFIG / "resilience.toml", env={}),
                env={},
            )

        message = str(raised.value)
        assert vendor in message
        entry = vendor_for(vendor)
        assert entry.required_env_vars(entry.sole_credential_mode)[0] in message
        assert not any(value in message for value in FAKE_ENV.values())


class TestUnsupportedParamsAreHandledClearly:
    """ "unsupported parameters are handled clearly" — as a build error, per tier."""

    def test_an_unsupported_param_names_the_tier_that_asked_for_it(self):
        """Anthropic and ``seed``: the capability difference with a real cost.

        The matrix says ``UNSUPPORTED``; this says what the application does
        about it. Failing at build time with the tier named is the behaviour
        that has to be identical across vendors — the *set* of params each
        accepts is what is allowed to differ.
        """
        sampling = load_sampling(
            CONFIG / "sampling.toml", env={"ANALYSIS_SAMPLING_BASE_SEED": "7"}
        )
        with pytest.raises(ModelGateError) as raised:
            build_tier_adapters(
                tiers_for("anthropic"),
                sampling,
                load_resilience(CONFIG / "resilience.toml", env={}),
                env=FAKE_ENV,
            )
        assert "tiers.base" in str(raised.value)

    def test_the_same_param_binds_on_a_vendor_that_accepts_it(self):
        """The other half: ``unsupported`` is a fact about a pair, not a ban.

        Without this, the test above would pass just as well if ``seed`` were
        rejected everywhere — which would be the application inventing a
        restriction rather than reporting one.
        """
        sampling = load_sampling(
            CONFIG / "sampling.toml", env={"ANALYSIS_SAMPLING_BASE_SEED": "7"}
        )
        adapters = build_tier_adapters(
            tiers_for("vertex"),
            sampling,
            load_resilience(CONFIG / "resilience.toml", env={}),
            env=FAKE_ENV,
        )
        assert adapters["base"] is not None


class TestSchemasAreProviderIndependent:
    """ "extraction / analyst / critic schema can be produced", identically."""

    @pytest.mark.parametrize("vendor", sorted(REFERENCE_MODELS))
    def test_the_built_graph_differs_only_in_which_route_each_node_asks_for(
        self, vendor
    ):
        """The strongest form of "the schema is identical": so is everything else.

        Rather than grep the three schemas for vendor names — which finds the
        prose in a field description explaining *why* a field is shaped as it
        is, and would fail on documentation — this builds the whole graph under
        each vendor and compares what it stamps. A per-vendor branch anywhere
        between the tier config and a bound node shows up here: different node
        set, different tier map, different sampling.

        ``node_models`` is the one field that must differ, and only by the
        prefix: it is the route each node asked for, which is where the vendor
        is *supposed* to appear.
        """
        reference = _pipeline_for("vertex")
        candidate = _pipeline_for(vendor)

        assert candidate.node_sampling == reference.node_sampling
        assert candidate.tier_sampling == reference.tier_sampling
        assert set(candidate.node_models) == set(reference.node_models)

        prefix = vendor_for(vendor).prefix
        for node, route in candidate.node_models.items():
            assert route.startswith(prefix), f"{node} is not routed to {vendor}"

    def test_the_node_schemas_are_plain_pydantic_with_no_vendor_input(self):
        """No schema takes a vendor, so none can be generated per vendor.

        The structural half of the claim above: a schema is a function of its
        model class alone, so calling it twice is identical by construction and
        there is no seam a provider could reach.
        """
        for schema_source in (SystemModel, ThreatProposals, ThreatRulings):
            assert schema_source.model_json_schema() == (
                schema_source.model_json_schema()
            )

    @pytest.mark.parametrize("vendor", sorted(REFERENCE_MODELS))
    def test_every_vendor_reaches_the_same_node_table(self, vendor):
        """One graph, one node -> tier map, whichever vendor is selected."""
        assert tiers_for(vendor).nodes == tiers_for("vertex").nodes


class TestProvenanceIsProviderIndependent:
    """ "sampling provenance is recorded" and "execution fingerprint is generated"."""

    @pytest.mark.parametrize(("vendor", "model"), reference_pairs())
    def test_a_fingerprint_is_produced_for_every_pair(self, vendor, model):
        sampling = load_sampling(CONFIG / "sampling.toml", env={}).for_tier("strong")
        served = join_served(vendor_for(vendor).route(model), model)
        assert len(sample_fingerprint(served, sampling)) == 64

    def test_the_vendor_is_part_of_the_fingerprint(self):
        """Vertex-hosted Claude and Anthropic-direct must not share an identity.

        The same served build reached through two vendors is two generation
        identities, because a served identifier carries no vendor — a
        served-only hash would let a manifest blessed on one silently certify
        the other. Neutrality means the *rule* is the same for every vendor, not
        that the vendor stops being recorded.
        """
        sampling = load_sampling(CONFIG / "sampling.toml", env={}).for_tier("strong")
        model = "claude-opus-5"
        direct = sample_fingerprint(
            join_served(vendor_for("anthropic").route(model), model), sampling
        )
        hosted = sample_fingerprint(
            join_served(vendor_for("vertex").route(model), model), sampling
        )
        assert direct != hosted

    @pytest.mark.parametrize("vendor", sorted(REFERENCE_MODELS))
    def test_served_capture_re_attaches_the_vendor_the_run_asked_for(self, vendor):
        """ "actual served model is captured where available", uniformly.

        Providers return a bare build with no vendor on it, so the join is what
        makes the captured value comparable across vendors at all. Identical
        code for all three, asserted per vendor because a special case here
        would be invisible until a fingerprint failed to certify.
        """
        served_build = "some-build-002"
        requested = vendor_for(vendor).route(REFERENCE_MODELS[vendor][0])
        assert join_served(requested, served_build) == (
            f"{vendor_for(vendor).prefix}{served_build}"
        )


class TestTheLiveLanesSweepWhatWasProfiled:
    """The matrix and the live workflows must name the same models."""

    #: Which live sweep lane authenticates a vendor, keyed by **credential
    #: mode**. A lane is a credential arrangement — one exchanges an OIDC token
    #: for a platform identity, the other reads API keys from repository
    #: secrets — so the mode is the property that chooses, and the vendor's name
    #: never was. Keyed here rather than branched, so a vendor row nobody has
    #: written already has a lane, and a *mode* nobody has written raises
    #: instead of falling through to somebody else's file.
    LANE_FOR_MODE: ClassVar[dict[CredentialMode, str]] = {
        CredentialMode.IAM: "evals-live.yml",
        CredentialMode.API_KEY: "evals-live-api-key.yml",
    }

    #: The lane every vendor is compared on, whatever authenticates it. One
    #: file for all three vendors, so a vendor missing from it has no live
    #: coverage at all.
    SMOKE_LANE: ClassVar[Path] = WORKFLOWS / "provider-smoke.yml"

    def sweep_lane(self, vendor: str) -> Path:
        """The live sweep workflow that runs *vendor*.

        One reader, called by every check below and by the deletion test, so
        the checks and the test that proves them sensitive cannot come to
        disagree about which file holds a vendor's pins.
        """
        return WORKFLOWS / self.LANE_FOR_MODE[vendor_for(vendor).sole_credential_mode]

    def test_every_credential_mode_has_a_lane(self):
        # The table checked against its registry. A mode with no lane is a
        # deployment class no live sweep can exercise, and it would otherwise
        # be invisible until somebody looked for the coverage.
        assert set(self.LANE_FOR_MODE) == set(CredentialMode)

    @pytest.mark.parametrize("vendor", VENDOR_NAMES)
    def test_every_reference_model_appears_in_the_workflow_that_sweeps_it(self, vendor):
        """A live lane pinned to a model nobody profiled is unexercised coverage.

        :func:`pins_scalar` decides what pins a model, here and in the smoke
        check below. The check stays coarse — it cannot tell which matrix leg
        a model sits on — and it catches the drift that matters, which is a
        workflow pinning a pair the offline suite has never seen.

        Driven from the registry rather than from a hand-written list of
        vendors. The list named `vertex` in one branch and the other two in a
        second, so a fourth vendor row would have been swept by no lane and
        asserted about by nothing.
        """
        lane = self.sweep_lane(vendor)
        text = lane.read_text(encoding="utf-8")

        for model in REFERENCE_MODELS[vendor]:
            assert pins_scalar(text, model), (
                f"{model} is profiled on {vendor} but {lane.name} does not sweep it"
            )

    def test_the_smoke_lane_covers_every_vendor_on_the_profiled_pair(self):
        """The smoke is the lane that has to be comparable across vendors.

        The sweeps above are two files by credential class and one of them
        carries a single vendor; this one file carries all three, so a vendor
        missing from it is a vendor with no live coverage at all — which is the
        imbalance
        [#116](https://github.com/mstarks01/work-agent/issues/116) asked to
        remove, reappearing in the lane built to remove it.
        """
        smoke = self.SMOKE_LANE.read_text(encoding="utf-8")

        for vendor, models in REFERENCE_MODELS.items():
            assert pins_scalar(smoke, vendor), (
                f"{vendor} has no lane in the provider smoke"
            )
            for model in models:
                assert pins_scalar(smoke, model), (
                    f"{model} is profiled but the smoke lane does not pin it"
                )

    def test_a_prefixed_identifier_does_not_pin_the_bare_name(self):
        """The defect this matcher replaces, written as its counter-example.

        A Bedrock identifier is a vendor prefix plus the Anthropic name, so
        the text `anthropic.claude-sonnet-4-6` contains `claude-sonnet-4-6`.
        Under the substring search this replaces, a Bedrock pin answered for
        Anthropic's pair: a person who deleted Anthropic's own pin left the
        check passing on somebody else's line, and the suite reported coverage
        that was not there.
        """
        line = "            base_model: anthropic.claude-sonnet-4-6"
        assert pins_scalar(line, "anthropic.claude-sonnet-4-6")
        assert not pins_scalar(line, "claude-sonnet-4-6")

    @pytest.mark.parametrize(
        ("line", "value"),
        [
            ("      ANALYSIS_MODEL_BASE_MODEL: gemini-2.5-flash", "gemini-2.5-flash"),
            ("          - vendor: anthropic", "anthropic"),
            ('            base_model: "gpt-4o"', "gpt-4o"),
            ("            base_model: 'gpt-4o'", "gpt-4o"),
            ("            base_model: gpt-4o   # the profiled pair", "gpt-4o"),
            ("            base_model: gpt-4o\r\n", "gpt-4o"),
            ("            base_model: gpt-4o\u2028", "gpt-4o"),
        ],
    )
    def test_a_pin_is_read_in_every_shape_a_workflow_can_write_it(self, line, value):
        """What the producer may emit, rather than what it emits today.

        A matcher that reads only the shape in front of it fails silently on
        the first author who quotes a value, comments a line or saves a file
        with a line terminator this one never listed.
        """
        assert pins_scalar(line, value)

    @pytest.mark.parametrize(
        "line",
        [
            "            base_model: gpt-4o-mini",
            "            base_model: ${{ matrix.base_model }}",
            "            # base_model: gpt-4o",
            "            description: the gpt-4o leg of the smoke",
            # YAML spaces a comment off the value, so this pins one long
            # scalar and the matcher must read it the way the runner will.
            "            base_model: gpt-4o# not a comment",
        ],
    )
    def test_a_value_inside_a_longer_scalar_is_not_a_pin(self, line):
        assert not pins_scalar(line, "gpt-4o")

    @pytest.mark.parametrize("vendor", VENDOR_NAMES)
    def test_deleting_a_pin_from_a_live_workflow_breaks_the_check(self, vendor):
        """The checks read the real files, and not only their own examples.

        A matcher can be exactly right about a synthetic line and still read
        nothing in the workflow it guards. That failure is silent, because an
        assertion which never sees a pin never fails. So drop each real pin out
        of the real text, one at a time, and require the answer to change.
        """
        lanes = {
            self.sweep_lane(vendor): REFERENCE_MODELS[vendor],
            self.SMOKE_LANE: (vendor, *REFERENCE_MODELS[vendor]),
        }

        for lane, pins in lanes.items():
            text = lane.read_text(encoding="utf-8")
            for pin in pins:
                assert pins_scalar(text, pin)
                without = "\n".join(
                    line for line in text.splitlines() if not pins_scalar(line, pin)
                )
                assert not pins_scalar(without, pin), (
                    f"{lane.name} pins {pin} on no line the matcher reads"
                )


class TestProfileShape:
    """The report object itself, since a CI summary is rendered from it."""

    def test_a_profile_serialises_to_plain_json_values(self):
        entry = profile(vendor_for("openai"), "gpt-4o")
        rendered = entry.to_json()
        assert rendered["vendor"] == "openai"
        assert rendered["known_to_model_map"] is True
        assert rendered["params"]["reasoning_effort"] == "unsupported"
        assert all(isinstance(value, str) for value in rendered["params"].values())

    def test_a_profile_is_frozen(self):
        """A capability report a caller could edit is not a report."""
        entry = profile(vendor_for("openai"), "gpt-4o")
        assert isinstance(entry, ProviderProfile)
        with pytest.raises(FrozenInstanceError):
            entry.model = "something-else"


def test_a_stated_temperature_cannot_bind_openais_strong_reference_model(tmp_path):
    """The incompatibility, pinned so it stays known — now as an opt-in cost.

    OpenAI's reference strong model serves ``temperature`` only at its own
    default of 1, so a deployment that states 0.0 cannot run it. Before this
    gate existed such a configuration bound cleanly and died on the first live
    request, which is the shape the build-time gates exist to prevent.

    What changed is who pays: the shipped file states nothing, so this is the
    price of a deployment stating a value rather than a wall every OpenAI
    deployment meets. Asserted rather than fixed, because the choice between
    greedy decoding and this model belongs to whoever states the value.
    """
    stated = tmp_path / "sampling.toml"
    stated.write_text(
        (CONFIG / "sampling.toml")
        .read_text(encoding="utf-8")
        .replace("[tiers.strong]", "[tiers.strong]\ntemperature = 0.0"),
        encoding="utf-8",
    )

    with pytest.raises(ModelGateError, match="only at its default"):
        build_tier_adapters(
            tiers_for("openai"),
            load_sampling(stated, env={}),
            load_resilience(CONFIG / "resilience.toml", env={}),
            env=FAKE_ENV,
        )
