"""Sampling config: the knob shared by eval and production (decision 15).

The property worth guarding is the sharing itself. An eval-only temperature is
how a suite goes green while production drifts, so there is one file, no
env-var override, and the graph binds it onto every LLM node.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stride_service import graph
from stride_service.markdown_loader import MarkdownLoader
from stride_service.model_tiers import LLM_NODES, load_model_tiers
from stride_service.sampling import (
    SamplingConfigError,
    load_sampling,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLING_PATH = PROJECT_ROOT / "config" / "sampling.toml"


def test_shipped_config_defaults_to_greedy_decoding():
    config = load_sampling(SAMPLING_PATH)

    assert config.version >= 1
    assert config.temperature == 0.0
    assert config.top_p is None  # unmeasured, so deliberately unpinned


def test_out_of_range_temperature_fails_closed(tmp_path):
    path = tmp_path / "sampling.toml"
    path.write_text("version = 1\ntemperature = 7\n")

    with pytest.raises(SamplingConfigError):
        load_sampling(path)


def test_unknown_key_fails_closed(tmp_path):
    path = tmp_path / "sampling.toml"
    path.write_text("version = 1\ntemperature = 0.0\nthinking_budget = 1024\n")

    with pytest.raises(SamplingConfigError):
        load_sampling(path)


def test_missing_file_fails_closed(tmp_path):
    with pytest.raises(SamplingConfigError, match="cannot be read"):
        load_sampling(tmp_path / "absent.toml")


def test_invalid_toml_fails_closed(tmp_path):
    path = tmp_path / "sampling.toml"
    path.write_text("version = = 1")

    with pytest.raises(SamplingConfigError, match="invalid TOML"):
        load_sampling(path)


def test_every_llm_node_is_bound_to_the_configured_sampling():
    # A node left on library defaults is a node whose behaviour no eval number
    # was taken against.
    tiers = load_model_tiers(PROJECT_ROOT / "config" / "model_tiers.toml", env={})
    sampling = load_sampling(SAMPLING_PATH)
    pipeline = graph.build_pipeline(
        skill_loader=MarkdownLoader(PROJECT_ROOT / "skills"),
        prompt_loader=MarkdownLoader(PROJECT_ROOT / "prompts"),
        resolve_model=tiers.resolve_model,
        sampling=sampling,
    )

    llm_nodes = [
        node
        for node in pipeline.workflow.graph.nodes
        if node.name in graph.TIER_NODE_BY_GRAPH_NODE
    ]
    assert len(llm_nodes) == len(LLM_NODES)
    for node in llm_nodes:
        assert node.generate_content_config.temperature == sampling.temperature
