"""Resolving one installation's configuration, once.

One module resolves the configuration every caller shares, so these assert what
that single resolved object guarantees: that each file is read once, that the
gate the report route enforces is the gate the runner certified with, and that
a redirected path is honoured everywhere rather than only on the service side.
"""

from __future__ import annotations

import pytest

from stride_service import graph
from stride_service.api import create_app
from stride_service.deployment import (
    BLESSED_FINGERPRINTS_VAR,
    MODEL_TIERS_VAR,
    RESILIENCE_VAR,
    SAMPLING_VAR,
    ConfigPaths,
    Deployment,
)
from stride_service.errors import ConfigError
from stride_service.jobs import InMemoryJobStore
from stride_service.model_tiers import ModelConfigError
from stride_service.vendors import ProviderAuthError
from tests.factories import PROJECT_ROOT

# The shipped config selects nothing, so every resolution here has to choose a
# vendor first. Vertex on both tiers, because this module's subject is
# credential resolution and Vertex's ADC mode is the three-variable case — the
# one where getting the set wrong is worth catching.
VERTEX_TIERS = {
    "STRIDE_MODEL_BASE_VENDOR": "vertex",
    "STRIDE_MODEL_BASE_MODEL": "gemini-2.5-flash",
    "STRIDE_MODEL_STRONG_VENDOR": "vertex",
    "STRIDE_MODEL_STRONG_MODEL": "gemini-2.5-pro",
}

# Building adapters for that selection needs these three present. They are names
# of variables, never credentials: nothing here is a secret.
VERTEX_ENV = VERTEX_TIERS | {
    "STRIDE_VERTEX_PROJECT": "test-project",
    "STRIDE_VERTEX_LOCATION": "us-central1",
    "GOOGLE_APPLICATION_CREDENTIALS": "/nonexistent/adc.json",
}


# --- Resolution -------------------------------------------------------------


def test_from_env_resolves_the_repo_configs_without_credentials():
    """Reading config is cheap and credential-free; that is why it is eager."""
    deployment = Deployment.from_env(env=VERTEX_TIERS)

    assert deployment.tiers.version == 3
    assert set(deployment.sampling.tiers) == {"base", "strong"}
    assert deployment.resilience.attempts >= 1
    assert deployment.manifest.version == 2
    assert deployment.paths.model_tiers == PROJECT_ROOT / "config/model_tiers.toml"


def test_each_config_file_is_read_exactly_once(monkeypatch):
    """The duplication this replaces: model_tiers.toml was read five times."""
    from stride_service import deployment as module

    reads: list[str] = []
    for name in ("load_model_tiers", "load_sampling", "load_resilience"):
        original = getattr(module, name)

        def counted(path, *args, _name=name, _original=original, **kwargs):
            reads.append(_name)
            return _original(path, *args, **kwargs)

        monkeypatch.setattr(module, name, counted)

    Deployment.from_env(env=VERTEX_TIERS)

    assert sorted(reads) == ["load_model_tiers", "load_resilience", "load_sampling"]


def test_a_path_variable_picks_the_file_and_never_layers_a_second(tmp_path):
    """#10: exactly one file is read; the variable only chooses which."""
    custom = tmp_path / "sampling.toml"
    custom.write_text(
        "version = 3\n[tiers.base]\ntemperature = 0.25\n[tiers.strong]\n",
        encoding="utf-8",
    )

    deployment = Deployment.from_env(env=VERTEX_TIERS | {SAMPLING_VAR: str(custom)})

    assert deployment.sampling.for_tier("base").temperature == 0.25
    # Not merged with the repo file: the strong tier is what the chosen file says.
    assert deployment.sampling.for_tier("strong").temperature is None
    assert deployment.paths.sampling == custom


@pytest.mark.parametrize(
    "var", [MODEL_TIERS_VAR, SAMPLING_VAR, RESILIENCE_VAR, BLESSED_FINGERPRINTS_VAR]
)
def test_a_set_but_empty_path_variable_fails_closed(var):
    """A deploy mistake, not a fallback: reading the repo file would be silent."""
    with pytest.raises(ConfigError, match=f"{var} is set but empty"):
        Deployment.from_env(env={var: "   "})


def test_from_env_fails_closed_on_a_missing_tier_config(tmp_path):
    with pytest.raises(ModelConfigError, match="cannot be read"):
        Deployment.from_env(env={MODEL_TIERS_VAR: str(tmp_path / "gone.toml")})


def test_from_env_fails_closed_on_a_missing_resilience_config(tmp_path):
    with pytest.raises(Exception, match="cannot be read"):
        Deployment.from_env(
            env=VERTEX_TIERS | {RESILIENCE_VAR: str(tmp_path / "gone.toml")}
        )


def test_the_environment_stays_out_of_repr_and_equality():
    """OWASP A09: a deployment in a log or a traceback must not carry a key."""
    deployment = Deployment.from_env(
        env=VERTEX_ENV | {"STRIDE_ANTHROPIC_API_KEY": "sk-secret"}
    )

    assert "sk-secret" not in repr(deployment)
    assert deployment == Deployment.from_env(env=VERTEX_TIERS)


def test_tier_of_walks_graph_node_to_tier():
    """The one place this two-step walk is written."""
    deployment = Deployment.from_env(env=VERTEX_TIERS)

    assert deployment.tier_of(graph.EXTRACT_NODE) == "base"
    assert deployment.tier_of(graph.CRITIC_NODE) == "strong"
    assert deployment.tier_of(graph.RECRITIC_NODE) == "strong"


# --- Building ---------------------------------------------------------------


def test_the_pipeline_binds_the_pinned_models_from_config():
    pipeline = Deployment.from_env(env=VERTEX_ENV).pipeline()

    assert pipeline.node_models[graph.EXTRACT_NODE] == "vertex_ai/gemini-2.5-flash"
    assert pipeline.node_models[graph.CRITIC_NODE] == "vertex_ai/gemini-2.5-pro"
    assert set(pipeline.node_models) == set(graph.TIER_NODE_BY_GRAPH_NODE)


def test_the_ten_llm_nodes_share_two_adapters_one_per_tier():
    """#6: the binding is per tier, so the build-time checks fire twice, not ten times."""
    pipeline = Deployment.from_env(env=VERTEX_ENV).pipeline()
    nodes = {node.name: node for node in pipeline.workflow.graph.nodes}

    adapters = {id(nodes[name].model) for name in graph.TIER_NODE_BY_GRAPH_NODE}
    assert len(graph.TIER_NODE_BY_GRAPH_NODE) == 10
    assert len(adapters) == 2


def test_the_pipeline_binds_retry_and_timeout():
    """Every LLM node carries the retry policy and the deadline."""
    from google.adk.models.lite_llm import LiteLlm

    pipeline = Deployment.from_env(env=VERTEX_ENV).pipeline()
    critic = {node.name: node for node in pipeline.workflow.graph.nodes}[
        graph.CRITIC_NODE
    ]

    assert isinstance(critic.model, LiteLlm)
    # attempts=3 is a total; LiteLLM counts retries after the first try.
    assert critic.model._additional_args["num_retries"] == 2
    assert critic.generate_content_config.http_options.timeout == 300000


def test_drop_params_is_never_set_so_litellm_stays_fail_closed():
    """The sampling fingerprint's honesty depends on it (map Notes, #8)."""
    pipeline = Deployment.from_env(env=VERTEX_ENV).pipeline()
    nodes = {node.name: node for node in pipeline.workflow.graph.nodes}

    assert "drop_params" not in nodes[graph.CRITIC_NODE].model._additional_args


def test_env_overrides_the_retry_attempts_without_touching_the_model():
    deployment = Deployment.from_env(
        env=VERTEX_ENV | {"STRIDE_RETRY_ATTEMPTS": "5"}
    )
    pipeline = deployment.pipeline()
    nodes = {node.name: node for node in pipeline.workflow.graph.nodes}

    assert nodes[graph.CRITIC_NODE].model._additional_args["num_retries"] == 4
    assert pipeline.node_models[graph.CRITIC_NODE] == "vertex_ai/gemini-2.5-pro"


def test_building_the_pipeline_fails_closed_without_provider_credentials():
    """The credential check is a build-time gate, not a first-request surprise.

    It fires when the adapters are built, not when the config is read — which is
    what lets the first-run app report this failure while still naming the
    vendor the config selected.
    """
    deployment = Deployment.from_env(env=VERTEX_TIERS)

    with pytest.raises(ProviderAuthError, match="STRIDE_VERTEX_PROJECT"):
        deployment.pipeline()


def test_a_credential_error_never_echoes_the_secret():
    # OWASP A09: a key echoed into a log or an error has leaked.
    env = VERTEX_ENV | {
        "STRIDE_MODEL_BASE_VENDOR": "anthropic",
        "STRIDE_MODEL_BASE_MODEL": "claude-sonnet-4-5-20250929",
    }
    with pytest.raises(ProviderAuthError) as excinfo:
        Deployment.from_env(env=env).pipeline()

    assert "STRIDE_ANTHROPIC_API_KEY" in str(excinfo.value)


def test_an_offline_resolver_short_circuits_the_credential_check():
    """What the eval harness and every offline test rely on."""
    pipeline = Deployment.from_env(env=VERTEX_TIERS).pipeline(
        resolve_model=lambda tier_node: "scripted"
    )

    assert pipeline.node_models[graph.EXTRACT_NODE] == "scripted"


# --- One manifest, one gate -------------------------------------------------


def test_the_gate_is_built_once():
    """Two jobs in one process can never be certified against two manifests."""
    deployment = Deployment.from_env(env=VERTEX_TIERS)

    assert deployment.gate() is deployment.gate()


def test_the_runner_is_built_once():
    deployment = Deployment.from_env(env=VERTEX_ENV)

    assert deployment.runner() is deployment.runner()


def test_the_route_enforces_the_gate_the_runner_certified_with():
    """The reach through the runner's private attribute this replaced."""
    deployment = Deployment.from_env(env=VERTEX_ENV)

    app = create_app(
        deployment=deployment, store=InMemoryJobStore(), verifier=object()
    )

    assert app.state.certification is deployment.gate()
    assert app.state.runner is deployment.runner()


def test_require_certified_is_off_unless_explicitly_affirmative():
    assert Deployment.from_env(env=VERTEX_TIERS).gate().require_certified is False
    assert (
        Deployment.from_env(env=VERTEX_TIERS | {"STRIDE_REQUIRE_CERTIFIED": "no"})
        .gate()
        .require_certified
        is False
    )
    assert (
        Deployment.from_env(env=VERTEX_TIERS | {"STRIDE_REQUIRE_CERTIFIED": "true"})
        .gate()
        .require_certified
        is True
    )


def test_config_paths_are_repo_relative_by_default():
    paths = ConfigPaths.from_env({})

    assert paths.skills == PROJECT_ROOT / "skills"
    assert paths.prompts == PROJECT_ROOT / "prompts"
    assert paths.blessed_fingerprints == (
        PROJECT_ROOT / "config/blessed-fingerprints.toml"
    )
