"""What the graph binds onto its LLM nodes.

The adapter half of this module is exercised through
``tests/test_deployment.py``, which builds real ``LiteLlm`` adapters and asserts
the per-tier sharing and the build-time gates. What is here is the binding
*value* — the four things that used to travel as four parameters, two of which
are views of one ``SamplingConfig``.
"""

from __future__ import annotations

import dataclasses

import pytest

from stride_service.binding import NodeBinding
from stride_service.graph import TIER_NODE_BY_GRAPH_NODE
from stride_service.model_tiers import ModelConfigError, load_model_tiers
from stride_service.resilience import load_resilience
from stride_service.sampling import load_sampling
from tests.factories import PROJECT_ROOT

DIVERGENT = """\
version = 3
[tiers.base]
temperature = 0.0
seed = 11
[tiers.strong]
temperature = 1.0
seed = 22
"""


@pytest.fixture
def tiers():
    return load_model_tiers(PROJECT_ROOT / "config" / "model_tiers.toml", env={})


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

    for graph_node, tier_node in TIER_NODE_BY_GRAPH_NODE.items():
        resolved = binding.resolve_sampling(tier_node)
        tier = tiers.resolve_tier(tier_node)
        assert resolved == binding.tier_sampling[tier], graph_node


def test_the_tier_map_is_not_re_derived(tiers, sampling):
    """The node -> tier walk comes from the tier config, as everywhere else."""
    binding = NodeBinding.from_configs(tiers, sampling, _resolver)

    assert binding.resolve_sampling("extract") == sampling.for_tier("base")
    assert binding.resolve_sampling("critic") == sampling.for_tier("strong")
    # recritic shares the critic's tier, so it shares the params by construction.
    assert binding.resolve_sampling("recritic") == binding.resolve_sampling("critic")


def test_an_unknown_node_fails_closed(tiers, sampling):
    binding = NodeBinding.from_configs(tiers, sampling, _resolver)

    with pytest.raises(ModelConfigError, match="unknown LLM node"):
        binding.resolve_sampling("analyst/nonexistent")


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
