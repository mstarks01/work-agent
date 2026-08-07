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
from stride_service.model_gate import ModelGateError, output_ceiling
from stride_service.model_tiers import LLM_NODES, ModelConfigError
from stride_service.vendors import ProviderAuthError, vendor_for
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

    assert deployment.tiers.version == 4
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
        "version = 4\n[tiers.base]\ntemperature = 0.25\n[tiers.strong]\n",
        encoding="utf-8",
    )

    deployment = Deployment.from_env(env=VERTEX_TIERS | {SAMPLING_VAR: str(custom)})

    assert deployment.sampling.for_tier("base").temperature == 0.25
    # Not merged with the repo file: the strong tier is what the chosen file says.
    assert deployment.sampling.for_tier("strong").temperature is None
    assert deployment.paths.sampling == custom


def test_default_dir_prefers_the_bundled_copy_when_present(tmp_path, monkeypatch):
    """A wheel install bundles skills/prompts under stride_service/_bundled/."""
    from stride_service import deployment as module

    bundled_skills = tmp_path / "skills"
    bundled_skills.mkdir()
    monkeypatch.setattr(module, "_BUNDLED_DIR", tmp_path)

    assert module._default_dir("skills") == bundled_skills


def test_default_dir_falls_back_to_the_repo_copy_when_unbundled(tmp_path, monkeypatch):
    """An editable install has no _bundled/ -- every other test here relies on this."""
    from stride_service import deployment as module

    monkeypatch.setattr(module, "_BUNDLED_DIR", tmp_path / "does-not-exist")

    assert module._default_dir("skills") == module._REPO_ROOT / "skills"


def test_default_config_path_prefers_the_bundled_copy_when_present(tmp_path, monkeypatch):
    from stride_service import deployment as module

    bundled_config = tmp_path / "config"
    bundled_config.mkdir()
    bundled_file = bundled_config / "sampling.toml"
    bundled_file.write_text("version = 4\n", encoding="utf-8")
    monkeypatch.setattr(module, "_BUNDLED_DIR", tmp_path)

    assert module._default_config_path("sampling.toml") == bundled_file


def test_default_config_path_falls_back_to_the_repo_copy_when_unbundled(
    tmp_path, monkeypatch
):
    from stride_service import deployment as module

    monkeypatch.setattr(module, "_BUNDLED_DIR", tmp_path / "does-not-exist")

    assert (
        module._default_config_path("sampling.toml")
        == module._REPO_ROOT / "config" / "sampling.toml"
    )


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


def test_every_llm_node_carries_the_retry_loop():
    """The wiring, without which every retry test still passes and nothing retries."""
    pipeline = Deployment.from_env(env=VERTEX_ENV).pipeline()
    nodes = {node.name: node for node in pipeline.workflow.graph.nodes}

    for name in graph.TIER_NODE_BY_GRAPH_NODE:
        policy = getattr(type(nodes[name].model), "retry_policy", None)
        assert policy is not None, f"{name} has no retry loop"
        assert policy.attempts == 3


def test_both_tiers_draw_on_one_shared_retry_budget():
    """A storm is a property of the process, not of a tier.

    Two budgets would let the six category agents exhaust the strong tier's allowance
    while the base tier's sat untouched beside it — and both are pointed at the
    same provider quota.
    """
    pipeline = Deployment.from_env(env=VERTEX_ENV).pipeline()
    nodes = {node.name: node for node in pipeline.workflow.graph.nodes}

    budgets = {
        id(type(nodes[name].model).retry_policy.budget)
        for name in graph.TIER_NODE_BY_GRAPH_NODE
    }
    assert len(budgets) == 1
    # Capacity is one retry per LLM node in the graph: what one job may spend
    # from a cold bucket.
    budget = type(nodes[graph.CRITIC_NODE].model).retry_policy.budget
    assert budget.capacity == len(LLM_NODES)
    assert budget.ratio == 0.1


def test_the_pipeline_binds_retry_and_timeout():
    """Every LLM node carries the retry policy and the deadline."""
    from google.adk.models.lite_llm import LiteLlm

    pipeline = Deployment.from_env(env=VERTEX_ENV).pipeline()
    critic = {node.name: node for node in pipeline.workflow.graph.nodes}[
        graph.CRITIC_NODE
    ]

    assert isinstance(critic.model, LiteLlm)
    # Zero on purpose: the library's retry layer is off so it cannot set the
    # provider SDK's max_retries from it, and the loop runs one level up in
    # stride_service.retry, where a shared budget can bound it.
    assert critic.model._additional_args["num_retries"] == 0
    assert critic.generate_content_config.http_options.timeout == 300000


def test_drop_params_is_never_set_so_litellm_stays_fail_closed():
    """The sampling fingerprint's honesty depends on it (map Notes, #8)."""
    pipeline = Deployment.from_env(env=VERTEX_ENV).pipeline()
    nodes = {node.name: node for node in pipeline.workflow.graph.nodes}

    assert "drop_params" not in nodes[graph.CRITIC_NODE].model._additional_args


def test_env_overrides_the_retry_attempts_without_touching_the_model():
    deployment = Deployment.from_env(env=VERTEX_ENV | {"STRIDE_RETRY_ATTEMPTS": "5"})
    pipeline = deployment.pipeline()
    nodes = {node.name: node for node in pipeline.workflow.graph.nodes}

    # The override reaches the retry policy, not the adapter: the library's
    # layer stays off whatever attempts says.
    assert nodes[graph.CRITIC_NODE].model._additional_args["num_retries"] == 0
    assert deployment.resilience.attempts == 5
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
        "STRIDE_MODEL_BASE_MODEL": "claude-sonnet-4-6",
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


# --- The removed-temperature gate -------------------------------------------

# Anthropic removed `temperature` from Claude 4.7 onward. `claude-opus-5` is the
# case that matters: the pinned LiteLLM carries no entry for it, so
# `check_supported` falls back to the provider's base config and *passes* — the
# residual `model_gate` documents, and the whole reason the second check exists.
# `claude-sonnet-5` is in that map and LiteLLM rejects it itself, which is why it
# is deliberately not the model under test.
CLAUDE_5 = "claude-opus-5"

# A Claude that clears *every* gate: generation >= 4.7 so the temperature floor
# still applies to it, and on the pinned litellm's native structured-output
# path so the schema check passes. The two rules need different models to be
# demonstrated independently.
CLAUDE_NATIVE = "claude-opus-4-7"

ANTHROPIC_ENV = {
    "STRIDE_MODEL_BASE_VENDOR": "anthropic",
    "STRIDE_MODEL_BASE_MODEL": CLAUDE_5,
    "STRIDE_MODEL_STRONG_VENDOR": "anthropic",
    "STRIDE_MODEL_STRONG_MODEL": CLAUDE_5,
    # A name, not a secret: the loader checks a variable is declared, never that
    # it authenticates, and nothing here reaches a provider.
    "STRIDE_ANTHROPIC_API_KEY": "sk-ant-not-a-real-key",
}

NO_TEMPERATURE = """\
version = 4
[tiers.base]
max_output_tokens = 8192
[tiers.strong]
max_output_tokens = 8192
"""


def test_a_claude_that_removed_temperature_fails_the_build():
    """The shipped sampling pins temperature = 0.0, which 4.7+ rejects outright."""
    with pytest.raises(ModelGateError) as excinfo:
        Deployment.from_env(env=ANTHROPIC_ENV).pipeline()

    message = str(excinfo.value)
    assert "tiers.base" in message
    assert "temperature" in message
    # The message has to name the file to edit; this is a first-run wall.
    assert "config/sampling.toml" in message


def test_the_temperature_gate_is_keyed_on_the_model_not_the_vendor():
    """Vertex-hosted Claude is the same model under the same removal.

    A vendor-keyed rule would pass exactly the configuration the check exists
    to stop, so the selection here is deliberately `vertex` + a Claude.
    """
    env = VERTEX_ENV | {
        "STRIDE_MODEL_BASE_VENDOR": "vertex",
        "STRIDE_MODEL_BASE_MODEL": CLAUDE_5,
    }

    with pytest.raises(ModelGateError, match="temperature"):
        Deployment.from_env(env=env).pipeline()


def test_unsetting_temperature_lets_the_same_selection_build(tmp_path):
    """The fix the error message names, end to end."""
    path = tmp_path / "sampling.toml"
    path.write_text(NO_TEMPERATURE, encoding="utf-8")
    env = ANTHROPIC_ENV | {
        "STRIDE_SAMPLING": str(path),
        "STRIDE_MODEL_BASE_MODEL": CLAUDE_NATIVE,
        "STRIDE_MODEL_STRONG_MODEL": CLAUDE_NATIVE,
    }

    pipeline = Deployment.from_env(env=env).pipeline()

    assert pipeline.node_models[graph.CRITIC_NODE] == f"anthropic/{CLAUDE_NATIVE}"


def test_asking_for_more_output_than_the_model_serves_fails_the_build():
    """The truncation failure, moved from request time to build time.

    Every provider accepts ``max_output_tokens``, so the supported-param gate
    passes an over-ceiling value and the serving model rejects it on node one.
    Gemini 2.5 publishes 65,535, so this asks for more.
    """
    env = VERTEX_ENV | {"STRIDE_SAMPLING_BASE_MAX_OUTPUT_TOKENS": "200000"}

    with pytest.raises(ModelGateError) as excinfo:
        Deployment.from_env(env=env).pipeline()

    message = str(excinfo.value)
    assert "tiers.base" in message
    assert "65535" in message and "200000" in message
    # The message has to name the file to edit, like every other build wall.
    assert "config/sampling.toml" in message


def test_the_shipped_caps_clear_the_ceilings_of_a_selectable_model():
    """The raised caps are only safe if the gate they rely on agrees."""
    pipeline = Deployment.from_env(env=VERTEX_ENV).pipeline()

    assert pipeline.tier_sampling["base"].max_output_tokens == 16384
    assert pipeline.tier_sampling["strong"].max_output_tokens == 64000


@pytest.mark.parametrize(
    ("vendor", "model"),
    [
        ("vertex", "gemini-2.5-pro"),
        ("anthropic", "claude-sonnet-4-6"),
        ("anthropic", "claude-opus-4-6"),
        ("openai", "gpt-5.6"),
    ],
)
def test_the_strong_cap_fits_every_model_selectable_on_that_tier(vendor, model):
    """What pins ``strong`` to 64,000 rather than a rounder 65,536.

    The tier is not a Vertex tier — a deployment picks its vendor, so the cap
    has to clear the *lowest* ceiling any of them publishes, and Claude Sonnet
    4.6's 64,000 is that floor. This asks litellm directly rather than through a
    build, so a model whose ceiling drops below the shipped cap surfaces here as
    a named failure instead of as a build wall in somebody's deployment.
    """
    ceiling = output_ceiling(vendor_for(vendor), model)

    assert ceiling is not None, f"{vendor}/{model} left the cost map"
    assert ceiling >= 64000


def test_a_value_exactly_at_the_ceiling_still_builds():
    """The gate bounds the ask at the ceiling, not below it."""
    env = VERTEX_ENV | {"STRIDE_SAMPLING_STRONG_MAX_OUTPUT_TOKENS": "65535"}

    pipeline = Deployment.from_env(env=env).pipeline()

    assert pipeline.tier_sampling["strong"].max_output_tokens == 65535


def test_a_model_without_native_schema_support_fails_the_build(tmp_path):
    """The expensive failure shape: well-formed request, well-formed response.

    Where a provider cannot constrain output to a schema directly, the library
    emulates it with a synthesised tool and forwards the schema's `$defs`
    unresolved. Nothing rejects the request, so without this gate the job dies
    at output validation on node one. Temperature is unset here so the floor
    does not fire first and mask which check is under test.
    """
    path = tmp_path / "sampling.toml"
    path.write_text(NO_TEMPERATURE, encoding="utf-8")

    with pytest.raises(ModelGateError) as excinfo:
        Deployment.from_env(
            env=ANTHROPIC_ENV | {"STRIDE_SAMPLING": str(path)}
        ).pipeline()

    message = str(excinfo.value)
    assert "tiers.base" in message
    assert "$defs" in message and "schema" in message


def test_the_schema_gate_catches_vertex_hosted_claude_too():
    """Not an Anthropic-only problem, and the reason it is asked as a call.

    Under the pinned library, Vertex-hosted Claude takes the emulated path for
    *every* generation — including ones the direct vendor serves natively. A
    rule keyed on the model would have called this configuration fine.
    """
    env = VERTEX_ENV | {"STRIDE_MODEL_BASE_MODEL": "claude-sonnet-4-6"}

    with pytest.raises(ModelGateError, match=r"\$defs"):
        Deployment.from_env(env=env).pipeline()


def test_gemini_and_openai_are_untouched_by_the_schema_gate():
    """No false positives: both constrain schemas natively and must still build."""
    pipeline = Deployment.from_env(env=VERTEX_ENV).pipeline()

    assert pipeline.node_models[graph.CRITIC_NODE] == "vertex_ai/gemini-2.5-pro"


UNCONSTRAINED_BASE = """\
version = 4
[tiers.base]
max_output_tokens = 8192
constrain_output = false
[tiers.strong]
max_output_tokens = 8192
constrain_output = false
"""


def test_an_unconstrained_tier_suppresses_the_schema_on_the_adapter(tmp_path):
    """The seam: an explicit None over the one ADK derived from output_schema.

    Read off the built adapter rather than asserted about the config, because
    the value only does anything if it survives into ``_additional_args``.
    """
    path = tmp_path / "sampling.toml"
    path.write_text(UNCONSTRAINED_BASE, encoding="utf-8")
    env = ANTHROPIC_ENV | {
        "STRIDE_SAMPLING": str(path),
        "STRIDE_MODEL_BASE_MODEL": CLAUDE_NATIVE,
        "STRIDE_MODEL_STRONG_MODEL": CLAUDE_NATIVE,
    }

    pipeline = Deployment.from_env(env=env).pipeline()
    nodes = {node.name: node for node in pipeline.workflow.graph.nodes}
    extract = nodes[graph.EXTRACT_NODE]

    assert "response_format" in extract.model._additional_args
    assert extract.model._additional_args["response_format"] is None
    # The node keeps its schema, so the response is still validated on arrival.
    assert extract.output_schema is not None


def test_a_constrained_tier_leaves_the_derived_schema_alone():
    """The default must not carry the key at all — None would suppress it."""
    pipeline = Deployment.from_env(env=VERTEX_ENV).pipeline()
    nodes = {node.name: node for node in pipeline.workflow.graph.nodes}

    assert "response_format" not in nodes[graph.EXTRACT_NODE].model._additional_args


def test_the_schema_gate_is_scoped_to_tiers_that_send_a_schema(tmp_path):
    """The narrowing: a model rejected while constrained is fine unconstrained.

    `claude-opus-5` takes the emulated path, so the gate stops it — but only
    because a schema would be sent. Turn that off and there is no emulated
    request to object to, so the same selection must build.
    """
    # Both legs run without temperature so the 4.7 floor cannot fire first and
    # mask which gate is under test; the only difference is constrain_output.
    constrained = tmp_path / "constrained.toml"
    constrained.write_text(NO_TEMPERATURE, encoding="utf-8")
    path = tmp_path / "sampling.toml"
    path.write_text(UNCONSTRAINED_BASE, encoding="utf-8")

    with pytest.raises(ModelGateError, match=r"\$defs"):
        Deployment.from_env(
            env=ANTHROPIC_ENV | {"STRIDE_SAMPLING": str(constrained)}
        ).pipeline()

    pipeline = Deployment.from_env(
        env=ANTHROPIC_ENV | {"STRIDE_SAMPLING": str(path)}
    ).pipeline()

    assert pipeline.node_models[graph.EXTRACT_NODE] == f"anthropic/{CLAUDE_5}"


def test_claude_4_6_keeps_the_pinned_temperature():
    """The floor is 4.7, not the service's own 4.6 minimum.

    4.6 still accepts the param, so pinned greedy decoding survives there
    rather than being swept up by a vendor-wide ban.
    """
    env = ANTHROPIC_ENV | {
        "STRIDE_MODEL_BASE_MODEL": "claude-sonnet-4-6",
        "STRIDE_MODEL_STRONG_MODEL": "claude-sonnet-4-6",
    }

    pipeline = Deployment.from_env(env=env).pipeline()

    assert pipeline.node_models[graph.CRITIC_NODE] == "anthropic/claude-sonnet-4-6"


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

    app = create_app(deployment=deployment, store=InMemoryJobStore(), verifier=object())

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
