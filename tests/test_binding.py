"""What the graph binds onto its LLM nodes.

The adapter half of this module is exercised through
``tests/test_deployment.py``, which builds real ``LiteLlm`` adapters and asserts
the per-tier sharing and the build-time gates. What is here is the binding
*value* — the four things an LLM node runs on, two of which are views of one
``SamplingConfig``.
"""

from __future__ import annotations

import dataclasses

import pytest

from analysis_service.binding import NodeBinding, build_tier_adapters
from analysis_service.graph import tier_node_by_graph_node
from analysis_service.model_gate import ModelGateError
from analysis_service.model_tiers import ModelConfigError, load_model_tiers
from analysis_service.resilience import load_resilience
from analysis_service.sampling import load_sampling
from tests.factories import DEFAULT_FRAMEWORKS, PROJECT_ROOT, repo_tiers

#: This install's whole selection. The node -> tier map is built per selection
#: now, so a test walking "every node" has to say which graph's nodes it means.
CARRIED_FRAMEWORKS = DEFAULT_FRAMEWORKS

DIVERGENT = """\
version = 4
[tiers.base]
temperature = 0.0
seed = 11
[tiers.strong]
temperature = 1.0
seed = 22
"""


@pytest.fixture
def tiers():
    return repo_tiers()


@pytest.fixture
def sampling():
    return load_sampling(PROJECT_ROOT / "config" / "sampling.toml", env={})


def _resolver(node: str) -> str:
    return f"model-for-{node}"


def test_both_sampling_views_come_from_one_config(tiers, tmp_path):
    """The invariant this type exists for.

    ``resolve_sampling`` is what each node actually runs on; ``tier_sampling``
    is the clear block the report publishes. Sourced separately they could
    describe different generations, and every fingerprint in that report would
    be unverifiable against the block shipped beside it.
    """
    path = tmp_path / "sampling.toml"
    path.write_text(DIVERGENT, encoding="utf-8")
    divergent = load_sampling(path, env={})

    binding = NodeBinding.from_configs(tiers, divergent, _resolver)

    for graph_node, tier_node in tier_node_by_graph_node(CARRIED_FRAMEWORKS).items():
        resolved = binding.resolve_sampling(tier_node)
        tier = tiers.resolve_tier(tier_node)
        assert resolved == binding.tier_sampling[tier], graph_node


def test_the_tier_map_is_not_re_derived(tiers, sampling):
    """The node -> tier walk comes from the tier config, as everywhere else."""
    binding = NodeBinding.from_configs(tiers, sampling, _resolver)

    assert binding.resolve_sampling("extract") == sampling.for_tier("base")
    assert binding.resolve_sampling("critic/stride") == sampling.for_tier("strong")
    # recritic shares the critic's tier, so it shares the params by construction.
    assert binding.resolve_sampling("recritic/stride") == binding.resolve_sampling(
        "critic/stride"
    )


def test_an_unknown_node_fails_closed(tiers, sampling):
    binding = NodeBinding.from_configs(tiers, sampling, _resolver)

    with pytest.raises(ModelConfigError, match="unknown LLM node"):
        binding.resolve_sampling("analyze/nonexistent")


def test_resolve_model_stays_the_callers(tiers, sampling):
    """An offline test binds scripted models through it, so it is not derived."""
    binding = NodeBinding.from_configs(tiers, sampling, _resolver)

    assert binding.resolve_model("critic") == "model-for-critic"


def test_resilience_is_optional_and_carried(tiers, sampling):
    """Optional so offline stand-ins can build a graph with no config at all."""
    resilience = load_resilience(PROJECT_ROOT / "config" / "resilience.toml", env={})

    assert NodeBinding.from_configs(tiers, sampling, _resolver).resilience is None
    with_config = NodeBinding.from_configs(tiers, sampling, _resolver, resilience)
    assert with_config.resilience is resilience


def test_the_binding_is_frozen(tiers, sampling):
    """One value handed to the graph, not a mutable bag it could edit."""
    binding = NodeBinding.from_configs(tiers, sampling, _resolver)

    with pytest.raises(dataclasses.FrozenInstanceError):
        binding.resilience = None


class TestReasoningTemperatureFloor:
    """The build-time gate for OpenAI families that pin ``temperature``.

    The residual it covers has now bitten twice: the shipped sampling pins
    ``temperature = 0.0``, LiteLLM's cost map does not know a model released
    after the pinned copy, ``check_supported`` falls through to the provider's
    base config, and the build passes a configuration the provider rejects on
    the first request.
    """

    def _binding(self, model: str, sampling_toml: str, tmp_path):
        config = tmp_path / "sampling.toml"
        config.write_text(sampling_toml)
        tiers = load_model_tiers(
            PROJECT_ROOT / "config" / "model_tiers.toml",
            env={
                "ANALYSIS_MODEL_BASE_VENDOR": "openai",
                "ANALYSIS_MODEL_BASE_MODEL": model,
                "ANALYSIS_MODEL_STRONG_VENDOR": "openai",
                "ANALYSIS_MODEL_STRONG_MODEL": model,
            },
        )
        # ``build_tier_adapters`` rather than ``NodeBinding.from_configs``: the
        # gates fire where the adapters are actually built, and ``from_configs``
        # deliberately short-circuits that by taking a resolver instead.
        return build_tier_adapters(
            tiers,
            load_sampling(config, env={}),
            load_resilience(PROJECT_ROOT / "config" / "resilience.toml"),
            env={"ANALYSIS_OPENAI_API_KEY": "sk-test-not-a-real-key"},
        )

    def test_greedy_decoding_on_a_reasoning_model_fails_the_build(self, tmp_path):
        with pytest.raises(ModelGateError, match="only at its default"):
            self._binding("gpt-5.6-terra", DIVERGENT.replace("1.0", "0.0"), tmp_path)

    def test_the_message_names_the_knob_and_the_cost(self, tmp_path):
        """Two things ops needs: which line to change, and that changing it
        gives up reproducibility."""
        with pytest.raises(ModelGateError) as excinfo:
            self._binding("gpt-5.6-terra", DIVERGENT.replace("1.0", "0.0"), tmp_path)

        message = str(excinfo.value)
        assert "config/sampling.toml" in message
        assert "greedily" in message

    def test_the_default_value_is_permitted(self, tmp_path):
        """Where this differs from the Claude rule: the parameter still exists
        on these models, so stating it at 1 asks for what it will get."""
        assert self._binding("gpt-5.6-terra", DIVERGENT.replace("0.0", "1.0"), tmp_path)

    def test_a_non_reasoning_openai_model_is_untouched(self, tmp_path):
        assert self._binding("gpt-4o", DIVERGENT.replace("1.0", "0.0"), tmp_path)
