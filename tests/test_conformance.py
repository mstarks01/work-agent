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
:func:`~stride_service.binding.build_tier_adapters` a synthetic environment —
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

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from google.adk.models.base_llm import BaseLlm

from stride_service.binding import NodeBinding, build_tier_adapters
from stride_service.conformance import (
    PROBED_PARAMS,
    REFERENCE_MODELS,
    Capability,
    ProviderProfile,
    profile,
    reference_matrix,
    render_markdown,
)
from stride_service.graph import Pipeline, build_pipeline
from stride_service.markdown_loader import MarkdownLoader
from stride_service.model_gate import ModelGateError
from stride_service.model_tiers import ModelTierConfig, load_model_tiers
from stride_service.report import ThreatProposals, ThreatRulings
from stride_service.resilience import load_resilience
from stride_service.sampling import load_sampling, sampling_fingerprint
from stride_service.system_model import SystemModel
from stride_service.vendors import (
    VENDOR_NAMES,
    ProviderAuthError,
    join_served,
    openai_reasoning_model,
    vendor_for,
)
from tests.factories import EMPTY_THREATS, ScriptedLlm

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG = PROJECT_ROOT / "config"

# Placeholder credential material. Every vendor's variables at once, so one
# mapping serves all three legs: the registry reads only the ones the selected
# vendor names, and an unused entry authenticates nothing because nothing reads
# it. Values are visibly fake — a suite that needed a real key would be a suite
# that only ran where one existed, which is the imbalance this file exists to
# remove.
FAKE_ENV = {
    "STRIDE_ANTHROPIC_API_KEY": "sk-ant-not-a-real-key",
    "STRIDE_OPENAI_API_KEY": "sk-not-a-real-key",
    "STRIDE_VERTEX_PROJECT": "test-project",
    "STRIDE_VERTEX_LOCATION": "us-central1",
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
            "STRIDE_MODEL_BASE_VENDOR": vendor,
            "STRIDE_MODEL_BASE_MODEL": base,
            "STRIDE_MODEL_STRONG_VENDOR": vendor,
            "STRIDE_MODEL_STRONG_MODEL": strong,
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
        return ScriptedLlm(model=selection.route, reply=EMPTY_THREATS, seen=[])

    return build_pipeline(
        skill_loader=MarkdownLoader(PROJECT_ROOT / "skills"),
        prompt_loader=MarkdownLoader(PROJECT_ROOT / "prompts"),
        knowledge_loader=MarkdownLoader(PROJECT_ROOT / "knowledge"),
        binding=NodeBinding.from_configs(tiers, sampling, resolve),
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


def _bindable_sampling(vendor: str, tmp_path):
    """The shipped sampling, with ``temperature`` unset where a model pins it.

    Reasoning families serve ``temperature`` only at their own default, so a
    tier running one cannot also decode greedily. Unsetting rather than stating
    1 keeps the choice with the deployment.
    """
    shipped = (CONFIG / "sampling.toml").read_text()
    if not any(openai_reasoning_model(m) for m in REFERENCE_MODELS[vendor]):
        return load_sampling(CONFIG / "sampling.toml", env={})
    adjusted = tmp_path / "sampling.toml"
    adjusted.write_text(
        "\n".join(
            line
            for line in shipped.splitlines()
            if not line.strip().startswith("temperature")
        )
    )
    return load_sampling(adjusted, env={})


class TestModelsCanBeBound:
    """ "model can be instantiated" — the first line of the issue's contract."""

    @pytest.mark.parametrize("vendor", sorted(REFERENCE_MODELS))
    def test_both_tiers_bind_on_every_vendor(self, vendor, tmp_path):
        """The shipped resilience config and a legal sampling binds on all three.

        The strongest credential-free statement available about vendor
        neutrality: the same gates, and a working adapter per tier on every
        supported vendor. A vendor the configuration cannot bind is not
        supported in any useful sense, however many code paths mention it.

        **It is no longer the *shipped* sampling on every vendor, and that is a
        finding rather than a concession.** ``config/sampling.toml`` pins
        ``temperature = 0.0`` for greedy decoding, and OpenAI's reference strong
        model is a reasoning family that serves the parameter only at its own
        default — so the two cannot both hold. The pin is per *tier* and the
        model is per *deployment*, so no single shipped value satisfies every
        vendor. What binds here is the shipped config with that one parameter
        adjusted where the model requires it; the test below pins the
        incompatibility itself so it cannot be forgotten.
        """
        adapters = build_tier_adapters(
            tiers_for(vendor),
            _bindable_sampling(vendor, tmp_path),
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
        service declines it (:mod:`stride_service.binding` says so in its own
        header). Declining it is what makes "no vendor is privileged" true, and
        prose cannot hold that: a native path reintroduced for one vendor would
        leave every other assertion here passing, because the model strings,
        the node table and the sampling would all still match.

        So the class itself is the assertion. Identical across vendors means
        no vendor has a route the others lack — and the shared class is where
        the retry budget, the pinned ``num_retries=0`` and the credential gate
        live, so a vendor that escaped it would escape those too.

        The comparison is on the *ancestry* rather than the class object:
        :func:`~stride_service.retry.retrying_llm_class` mints one subclass per
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
                _bindable_sampling(vendor, tmp_path),
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
        assert vendor_for(vendor).required_env_vars[0] in message
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
            CONFIG / "sampling.toml", env={"STRIDE_SAMPLING_BASE_SEED": "7"}
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
            CONFIG / "sampling.toml", env={"STRIDE_SAMPLING_BASE_SEED": "7"}
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
        assert len(sampling_fingerprint(served, sampling)) == 64

    def test_the_vendor_is_part_of_the_fingerprint(self):
        """Vertex-hosted Claude and Anthropic-direct must not share an identity.

        The same served build reached through two vendors is two generation
        identities, because a served identifier carries no vendor — a
        served-only hash would let a manifest blessed on one silently certify
        the other. Neutrality means the *rule* is the same for every vendor, not
        that the vendor stops being recorded.
        """
        sampling = load_sampling(CONFIG / "sampling.toml", env={}).for_tier("strong")
        model = "claude-opus-4-6"
        direct = sampling_fingerprint(
            join_served(vendor_for("anthropic").route(model), model), sampling
        )
        hosted = sampling_fingerprint(
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

    def test_every_reference_model_appears_in_the_workflow_that_sweeps_it(self):
        """A live lane pinned to a model nobody profiled is unexercised coverage.

        A text search rather than a YAML parse, and deliberately so: PyYAML is
        not a declared dependency of this project, and adding one to assert a
        string appears in a file would be a poor trade. The check is coarse —
        it cannot tell which matrix leg a model sits on — and it catches the
        drift that matters, which is a workflow pinning a pair the offline
        suite has never seen.
        """
        workflows = PROJECT_ROOT / ".github" / "workflows"
        vertex = (workflows / "evals-live.yml").read_text(encoding="utf-8")
        api_key = (workflows / "evals-live-api-key.yml").read_text(encoding="utf-8")

        for model in REFERENCE_MODELS["vertex"]:
            assert model in vertex, f"{model} is profiled but no live lane sweeps it"
        for vendor in ("anthropic", "openai"):
            for model in REFERENCE_MODELS[vendor]:
                assert model in api_key, (
                    f"{model} is profiled but no live lane sweeps it"
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
        smoke = (
            PROJECT_ROOT / ".github" / "workflows" / "provider-smoke.yml"
        ).read_text(encoding="utf-8")

        for vendor, models in REFERENCE_MODELS.items():
            assert vendor in smoke, f"{vendor} has no lane in the provider smoke"
            for model in models:
                assert model in smoke, (
                    f"{model} is profiled but the smoke lane does not pin it"
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


def test_the_shipped_temperature_cannot_bind_openais_strong_reference_model():
    """The incompatibility the reasoning floor surfaced, pinned so it stays known.

    ``config/sampling.toml`` pins ``temperature = 0.0``; OpenAI's reference
    strong model serves that parameter only at its own default of 1. Before the
    floor existed this bound cleanly and died on the first live request, which
    is the shape the build-time gates exist to prevent.

    Asserted rather than fixed, because fixing it is a choice between two
    things this test cannot make: give up greedy decoding on the strong tier
    for every vendor, or accept that one shipped sampling file cannot serve
    every supported model.
    """
    with pytest.raises(ModelGateError, match="only at its default"):
        build_tier_adapters(
            tiers_for("openai"),
            load_sampling(CONFIG / "sampling.toml", env={}),
            load_resilience(CONFIG / "resilience.toml", env={}),
            env=FAKE_ENV,
        )
