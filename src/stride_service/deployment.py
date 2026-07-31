"""One installation's configuration, resolved once and shared by everything.

A **Deployment** is the four config files plus the skills and prompts, located
by ``STRIDE_*`` variables that pick *which* file is read. Every consumer — the
HTTP service, the in-process engine, the first-run web app, the eval harness's
pipeline builder and the eval CLI — assembles its configuration through this
one object, so each file is read once per process, a deployment that redirects
``STRIDE_SAMPLING`` has its sweeps grading the configuration it actually runs,
and the node -> tier walk is written in a single place.

**Configs load eagerly, adapters and the graph do not.** Reading four TOML files
is cheap, needs no credentials, and is where fail-closed belongs: a deployment
either has a usable configuration or refuses to be constructed. Building the
tier adapters is the expensive, credential-requiring step, so it waits for
:meth:`Deployment.pipeline`. That split is what lets the first-run app report a
*credential* failure while still naming the vendor the config selected — it
holds a valid Deployment whose ``runner()`` raised.

**One manifest, one gate.** :meth:`gate` is memoized and :meth:`runner` consumes
it, so the manifest a job is certified against and the one the report route
enforces are the same object rather than two loads that happen to agree.

The environment is held for one purpose — deriving each vendor's credentials at
adapter-build time — and is kept out of ``repr`` and equality so a deployment
that lands in a log or a traceback cannot carry an API key with it (OWASP A09).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

from stride_service.binding import (
    NodeBinding,
    build_tier_adapters,
    make_resolve_model,
)
from stride_service.certification import (
    BlessedManifest,
    CertificationGate,
    load_manifest,
)
from stride_service.errors import ConfigError
from stride_service.graph import (
    ENTRY_EXTRACT,
    TIER_NODE_BY_GRAPH_NODE,
    Entry,
    ModelResolver,
    Pipeline,
    build_pipeline,
)
from stride_service.markdown_loader import MarkdownLoader
from stride_service.model_tiers import ModelTierConfig, TierName, load_model_tiers
from stride_service.pipeline import AdkPipelineRunner
from stride_service.resilience import ResilienceConfig, load_resilience
from stride_service.sampling import SamplingConfig, load_sampling

# The repo layout baked into the image: Markdown and config next to the
# package, not fetched at run time. A variable picks a different file; it never
# layers a second one over the first.
_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SKILLS_DIR = _REPO_ROOT / "skills"
DEFAULT_PROMPTS_DIR = _REPO_ROOT / "prompts"
DEFAULT_MODEL_TIERS_PATH = _REPO_ROOT / "config" / "model_tiers.toml"
DEFAULT_SAMPLING_PATH = _REPO_ROOT / "config" / "sampling.toml"
DEFAULT_RESILIENCE_PATH = _REPO_ROOT / "config" / "resilience.toml"
DEFAULT_BLESSED_FINGERPRINTS_PATH = _REPO_ROOT / "config" / "blessed-fingerprints.toml"

SKILLS_DIR_VAR = "STRIDE_SKILLS_DIR"
PROMPTS_DIR_VAR = "STRIDE_PROMPTS_DIR"
MODEL_TIERS_VAR = "STRIDE_TIERS_FILE"
SAMPLING_VAR = "STRIDE_SAMPLING"
RESILIENCE_VAR = "STRIDE_RESILIENCE"
BLESSED_FINGERPRINTS_VAR = "STRIDE_BLESSED_FINGERPRINTS"
REQUIRE_CERTIFIED_VAR = "STRIDE_REQUIRE_CERTIFIED"


def _path(env: Mapping[str, str], var: str, default: Path) -> Path:
    """One located file or directory: the variable's value, or the repo default.

    A set-but-empty variable raises rather than falling back, matching every
    other loader here: it is a deploy mistake, and silently reading the repo
    file instead is how a deployment ends up running on config it did not
    choose.
    """
    value = env.get(var)
    if value is None:
        return default
    if not value.strip():
        raise ConfigError(f"{var} is set but empty")
    return Path(value.strip())


@dataclass(frozen=True)
class ConfigPaths:
    """Where this deployment's configuration and Markdown live."""

    skills: Path
    prompts: Path
    model_tiers: Path
    sampling: Path
    resilience: Path
    blessed_fingerprints: Path

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Self:
        return cls(
            skills=_path(env, SKILLS_DIR_VAR, DEFAULT_SKILLS_DIR),
            prompts=_path(env, PROMPTS_DIR_VAR, DEFAULT_PROMPTS_DIR),
            model_tiers=_path(env, MODEL_TIERS_VAR, DEFAULT_MODEL_TIERS_PATH),
            sampling=_path(env, SAMPLING_VAR, DEFAULT_SAMPLING_PATH),
            resilience=_path(env, RESILIENCE_VAR, DEFAULT_RESILIENCE_PATH),
            blessed_fingerprints=_path(
                env, BLESSED_FINGERPRINTS_VAR, DEFAULT_BLESSED_FINGERPRINTS_PATH
            ),
        )


@dataclass(frozen=True)
class Deployment:
    """One installation's resolved configuration, and what it can build.

    Construct with :meth:`from_env` and pass it around; constructing a second
    one re-reads every file.
    """

    tiers: ModelTierConfig
    sampling: SamplingConfig
    resilience: ResilienceConfig
    manifest: BlessedManifest
    paths: ConfigPaths
    require_certified: bool = False
    # Held only to derive each vendor's credentials when the adapters are built.
    # Out of repr and equality: a deployment in a log must not carry a key.
    env: Mapping[str, str] = field(
        default_factory=dict, repr=False, compare=False
    )
    _built: dict[str, Any] = field(
        default_factory=dict, repr=False, compare=False
    )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Self:
        """Read and validate this deployment's configuration, or fail closed.

        Raises :class:`~stride_service.errors.ConfigError` — in one of its
        per-file subclasses — for a missing, malformed, stale-version or
        out-of-range config, rather than starting on whatever model, decoding
        parameters or retry behaviour happened to be default. No credential is
        touched and no provider is contacted here.
        """
        if env is None:
            env = os.environ
        paths = ConfigPaths.from_env(env)
        return cls(
            tiers=load_model_tiers(paths.model_tiers, env=env),
            sampling=load_sampling(paths.sampling, env=env),
            resilience=load_resilience(paths.resilience, env=env),
            manifest=load_manifest(paths.blessed_fingerprints),
            paths=paths,
            require_certified=_flag(env, REQUIRE_CERTIFIED_VAR),
            env=env,
        )

    def tier_of(self, graph_node: str) -> TierName:
        """The tier a *graph* node runs on, via its canonical tier-node name.

        The one place this walk is written: it is two mappings deep — graph
        node to canonical node, canonical node to tier — and callers that
        re-derive it are how two of them come to disagree about a node's tier.
        """
        return self.tiers.resolve_tier(TIER_NODE_BY_GRAPH_NODE[graph_node])

    def pipeline(
        self,
        *,
        entry: Entry = ENTRY_EXTRACT,
        resolve_model: ModelResolver | None = None,
    ) -> Pipeline:
        """Build the graph this deployment configures.

        Building the tier adapters runs two build-time gates — the
        supported-param check and the credential check — so an unusable
        provider selection costs nothing rather than dying on node one of a
        paid-for job. ``resolve_model`` short-circuits them deliberately: an
        offline test binding scripted models has no credentials to pass and no
        provider to call.

        Not memoized, because ``entry`` varies: the eval harness builds three
        graphs from one deployment.
        """
        if resolve_model is None:
            adapters = build_tier_adapters(
                self.tiers, self.sampling, self.resilience, env=self.env
            )
            resolve_model = make_resolve_model(adapters, self.tiers)
        return build_pipeline(
            skill_loader=MarkdownLoader(self.paths.skills),
            prompt_loader=MarkdownLoader(self.paths.prompts),
            binding=NodeBinding.from_configs(
                self.tiers, self.sampling, resolve_model, self.resilience
            ),
            entry=entry,
        )

    def gate(self) -> CertificationGate:
        """This deployment's certification gate, built once.

        Memoized so two jobs in one process can never be certified against
        different manifests, and so the gate the report route enforces is the
        same object the runner certified with.
        """
        return self._memo("gate", self._build_gate)

    def runner(self) -> AdkPipelineRunner:
        """The production runner: this deployment's graph and its gate.

        Memoized because the graph is expensive to compose — instructions are
        built once so the cacheable prefix every node shares is byte-identical
        across jobs.
        """
        return self._memo("runner", self._build_runner)

    def _build_gate(self) -> CertificationGate:
        return CertificationGate(
            manifest=self.manifest,
            tier_of=self.tier_of,
            require_certified=self.require_certified,
        )

    def _build_runner(self) -> AdkPipelineRunner:
        return AdkPipelineRunner(self.pipeline(), certification=self.gate())

    def _memo(self, key: str, build):
        if key not in self._built:
            self._built[key] = build()
        return self._built[key]


def _flag(env: Mapping[str, str], var: str) -> bool:
    """A boolean env flag, on only for an explicit affirmative."""
    return env.get(var, "").strip().lower() in ("1", "true", "yes", "on")
