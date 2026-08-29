"""One installation's configuration, resolved once and shared by everything.

A **Deployment** is the five config files plus the three text roots — the shared
prompt bodies, the domain packs, and one **Framework Package** per framework
this install carries — located by ``ANALYSIS_*`` variables that pick *which* file
is read. Every consumer — the HTTP service, the in-process engine, the first-run
web app, the eval harness's pipeline builder and the eval CLI — assembles its
configuration through this one object, so each file is read once per process, a
deployment that redirects ``ANALYSIS_SAMPLING`` has its sweeps grading the
configuration it actually runs, and the node -> tier walk is written in a single
place.

**Configs load eagerly, adapters and the graph do not.** Reading five TOML files
is cheap, needs no credentials, and is where fail-closed belongs: a deployment
either has a usable configuration or refuses to be constructed. The package gate
runs here too, for the same reason and at the same moment — a package that
declares a lane with no prompt must not reach a model call, and
``config/frameworks.toml`` naming a framework this build does not carry is a
deploy mistake rather than a job's problem. Building the tier adapters is the
expensive, credential-requiring step, so it waits for :meth:`pipeline`. That
split is what lets the first-run app report a *credential* failure while still
naming the vendor the config selected — it holds a valid Deployment whose
``runner()`` raised.

**A graph is built for one framework selection, so a runner is too.** A job
names its frameworks, and the nodes, the state keys and the instruction digest
are all functions of that selection (:func:`~analysis_service.graph.build_pipeline`
says why). :meth:`runner` therefore takes the selection and memoizes on it: an
install carrying two frameworks serves three selections and builds at most three
graphs, each one composed the first time a job asks for it rather than all of
them at startup. The expensive shared-prefix composition is still paid once per
selection rather than once per job, which is the property the memo exists for.

**One manifest, one gate.** :meth:`gate` is memoized across every selection and
:meth:`runner` consumes it, so the manifest a job is certified against and the
one the report route enforces are the same object rather than two loads that
happen to agree. It survives per-selection graphs because a graph node name
carries its framework: the node -> tier map is built over everything this
install *carries*, which is a superset of any one selection's nodes and cannot
collide across them.

The environment is held for one purpose — deriving each vendor's credentials at
adapter-build time — and is kept out of ``repr`` and equality so a deployment
that lands in a log or a traceback cannot carry an API key with it (OWASP A09).
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

from analysis_service.binding import (
    NodeBinding,
    build_tier_adapters,
    make_resolve_model,
)
from analysis_service.certification import (
    BlessedManifest,
    CertificationGate,
    load_manifest,
)
from analysis_service.errors import ConfigError
from analysis_service.framework_config import load_frameworks
from analysis_service.frameworks import validate_packages
from analysis_service.graph import (
    ENTRY_EXTRACT,
    Entry,
    ModelResolver,
    Pipeline,
    build_pipeline,
    tier_node_by_graph_node,
)
from analysis_service.markdown_loader import MarkdownLoader
from analysis_service.model_tiers import ModelTierConfig, TierName, load_model_tiers
from analysis_service.pipeline import AdkPipelineRunner
from analysis_service.report import FrameworkName
from analysis_service.resilience import ResilienceConfig, load_resilience
from analysis_service.sampling import SamplingConfig, load_sampling

# Two layouts resolve to the same defaults, not fetched at run time either way.
# A wheel built from this project bundles prompts/, domains/, frameworks/ and
# config/ under analysis_service/_bundled/ (see pyproject.toml's force-include),
# so an external `pip install analysis-service` needs nothing else. An editable/dev
# install has no _bundled/ -- hatchling's editable mode links back to the
# source tree rather than copying force-included data -- so this repo's own
# tests, evals and CI fall through to the checkout's top-level directories
# instead. Either way a variable picks a different file outright; it never
# layers a second one over whichever default applied.
_PACKAGE_DIR = Path(__file__).resolve().parent
_BUNDLED_DIR = _PACKAGE_DIR / "_bundled"
_REPO_ROOT = _PACKAGE_DIR.parents[1]


def _default_dir(name: str) -> Path:
    bundled = _BUNDLED_DIR / name
    return bundled if bundled.is_dir() else _REPO_ROOT / name


def _default_config_path(filename: str) -> Path:
    bundled = _BUNDLED_DIR / "config" / filename
    return bundled if bundled.is_file() else _REPO_ROOT / "config" / filename


DEFAULT_PROMPTS_DIR = _default_dir("prompts")
DEFAULT_DOMAINS_DIR = _default_dir("domains")
DEFAULT_FRAMEWORKS_DIR = _default_dir("frameworks")
DEFAULT_MODEL_TIERS_PATH = _default_config_path("model_tiers.toml")
DEFAULT_SAMPLING_PATH = _default_config_path("sampling.toml")
DEFAULT_RESILIENCE_PATH = _default_config_path("resilience.toml")
DEFAULT_BLESSED_FINGERPRINTS_PATH = _default_config_path("blessed-fingerprints.toml")
DEFAULT_FRAMEWORKS_PATH = _default_config_path("frameworks.toml")

# The three text roots. ``ANALYSIS_KNOWLEDGE_DIR`` is gone rather than renamed: a
# **Reference Note** and a **Worked Case** are selected by one package's own
# fired rules, so they moved under that package's root and the service-wide
# corpus they used to sit in has no remaining reader. ``ANALYSIS_SKILLS_DIR``
# became ``ANALYSIS_DOMAINS_DIR`` for the mirror-image reason — what stayed shared
# is exactly the **Domain Pack**s, whose key is the System Model rather than any
# framework.
PROMPTS_DIR_VAR = "ANALYSIS_PROMPTS_DIR"
DOMAINS_DIR_VAR = "ANALYSIS_DOMAINS_DIR"
FRAMEWORKS_DIR_VAR = "ANALYSIS_FRAMEWORKS_DIR"
MODEL_TIERS_VAR = "ANALYSIS_TIERS_FILE"
SAMPLING_VAR = "ANALYSIS_SAMPLING"
RESILIENCE_VAR = "ANALYSIS_RESILIENCE"
BLESSED_FINGERPRINTS_VAR = "ANALYSIS_BLESSED_FINGERPRINTS"
FRAMEWORKS_VAR = "ANALYSIS_FRAMEWORKS_FILE"
REQUIRE_CERTIFIED_VAR = "ANALYSIS_REQUIRE_CERTIFIED"


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
    """Where this deployment's configuration and Markdown live.

    ``frameworks`` is the *text* root every package hangs under, and
    ``frameworks_file`` is ``config/frameworks.toml``, which says which of them
    this install carries. Two variables rather than one because they answer
    different questions and a deployment may well redirect one without the
    other: swapping in a tailored corpus is not the same act as turning a
    framework off.
    """

    prompts: Path
    domains: Path
    frameworks: Path
    model_tiers: Path
    sampling: Path
    resilience: Path
    blessed_fingerprints: Path
    frameworks_file: Path

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Self:
        return cls(
            prompts=_path(env, PROMPTS_DIR_VAR, DEFAULT_PROMPTS_DIR),
            domains=_path(env, DOMAINS_DIR_VAR, DEFAULT_DOMAINS_DIR),
            frameworks=_path(env, FRAMEWORKS_DIR_VAR, DEFAULT_FRAMEWORKS_DIR),
            model_tiers=_path(env, MODEL_TIERS_VAR, DEFAULT_MODEL_TIERS_PATH),
            sampling=_path(env, SAMPLING_VAR, DEFAULT_SAMPLING_PATH),
            resilience=_path(env, RESILIENCE_VAR, DEFAULT_RESILIENCE_PATH),
            blessed_fingerprints=_path(
                env, BLESSED_FINGERPRINTS_VAR, DEFAULT_BLESSED_FINGERPRINTS_PATH
            ),
            frameworks_file=_path(env, FRAMEWORKS_VAR, DEFAULT_FRAMEWORKS_PATH),
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
    #: The frameworks this install carries, in ``config/frameworks.toml`` order.
    #: Every one of them passed the package gate before this object existed. A
    #: job selects from this set; it is not itself a selection, and it is never
    #: used as a default for one.
    frameworks: tuple[FrameworkName, ...]
    paths: ConfigPaths
    require_certified: bool = False
    # Held only to derive each vendor's credentials when the adapters are built.
    # Out of repr and equality: a deployment in a log must not carry a key.
    env: Mapping[str, str] = field(default_factory=dict, repr=False, compare=False)
    _built: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Self:
        """Read and validate this deployment's configuration, or fail closed.

        Raises :class:`~analysis_service.errors.ConfigError` — in one of its
        per-file subclasses — for a missing, malformed, stale-version or
        out-of-range config, rather than starting on whatever model, decoding
        parameters or retry behaviour happened to be default. No credential is
        touched and no provider is contacted here.

        The package gate runs on this path, once per carried framework, and it
        is what makes every later lookup a defect rather than a configuration
        problem: by the time a job names a framework, that package is known to
        be registered, well-formed, present on disk, and to have the three
        ``model_tiers.toml`` keys its nodes resolve on.
        """
        if env is None:
            env = os.environ
        paths = ConfigPaths.from_env(env)
        tiers = load_model_tiers(paths.model_tiers, env=env)
        frameworks = load_frameworks(paths.frameworks_file, env=env)
        validate_packages(frameworks, paths.frameworks, tuple(tiers.nodes))
        return cls(
            tiers=tiers,
            sampling=load_sampling(paths.sampling, env=env),
            resilience=load_resilience(paths.resilience, env=env),
            manifest=load_manifest(paths.blessed_fingerprints),
            frameworks=frameworks,
            paths=paths,
            require_certified=_flag(env, REQUIRE_CERTIFIED_VAR),
            env=env,
        )

    def selection(
        self, frameworks: Sequence[FrameworkName]
    ) -> tuple[FrameworkName, ...]:
        """One job's framework selection, checked against what this install carries.

        Non-empty, and every name carried here. The HTTP surface refuses both of
        those on the input ladder, before a job record exists; this is the same
        rule stated where the graph is actually built, so an in-process caller
        that never touches the ladder cannot reach a ``KeyError`` in the
        registry instead of a sentence naming what went wrong.

        Order is the caller's and is preserved: it is the order the report's
        blocks carry, and the envelope checks the two agree.
        """
        if not frameworks:
            raise ConfigError(
                "a job must select at least one framework;"
                f" this deployment carries {list(self.frameworks)}"
            )
        unknown = [name for name in frameworks if name not in self.frameworks]
        if unknown:
            raise ConfigError(
                f"this deployment does not carry {unknown};"
                f" it carries {list(self.frameworks)}"
            )
        return tuple(frameworks)

    def tier_of(self, graph_node: str) -> TierName:
        """The tier a *graph* node runs on, via its canonical tier-node name.

        The one place this walk is written: it is two mappings deep — graph
        node to canonical node, canonical node to tier — and callers that
        re-derive it are how two of them come to disagree about a node's tier.

        Built over everything this install **carries** rather than over one
        job's selection, which is what keeps a single gate serving every
        selection: a graph node name carries its own framework, so the carried
        map is a superset of any selection's with no key belonging to two.
        """
        return self.tiers.resolve_tier(self.tier_nodes()[graph_node])

    def tier_nodes(self) -> Mapping[str, str]:
        """Every LLM graph node this install can run, against its tier key.

        The map :meth:`tier_of` walks, exposed because callers that need to ask
        *which nodes are LLM nodes* — the provider smoke's checks — would
        otherwise re-derive it from the framework list and drift from the map the
        gate actually resolves against.
        """
        return self._memo(
            "tier_nodes", lambda: tier_node_by_graph_node(self.frameworks)
        )

    def pipeline(
        self,
        frameworks: Sequence[FrameworkName],
        *,
        entry: Entry = ENTRY_EXTRACT,
        resolve_model: ModelResolver | None = None,
    ) -> Pipeline:
        """Build the graph this deployment configures, for one selection.

        ``frameworks`` is which frameworks this graph runs, in the order its
        reports' blocks will carry. It is required and unordered-by-default for
        the reason ``config/frameworks.toml`` carries no default set: falling
        back to everything carried would make one submission mean different
        things on two installs.

        Building the tier adapters runs two build-time gates — the
        supported-param check and the credential check — so an unusable
        provider selection costs nothing rather than dying on node one of a
        paid-for job. ``resolve_model`` short-circuits them deliberately: an
        offline test binding scripted models has no credentials to pass and no
        provider to call.

        Not memoized, because ``entry`` varies: the eval harness builds three
        graphs from one deployment. :meth:`runner` memoizes the production
        entry, which is the one a job pays for twice.
        """
        selection = self.selection(frameworks)
        if resolve_model is None:
            adapters = build_tier_adapters(
                self.tiers, self.sampling, self.resilience, env=self.env
            )
            resolve_model = make_resolve_model(adapters, self.tiers)
        return build_pipeline(
            prompt_loader=MarkdownLoader(self.paths.prompts),
            domain_loader=MarkdownLoader(self.paths.domains),
            package_loaders={
                name: MarkdownLoader(self.paths.frameworks / name) for name in selection
            },
            binding=NodeBinding.from_configs(
                self.tiers, self.sampling, resolve_model, self.resilience
            ),
            frameworks=selection,
            entry=entry,
        )

    def gate(self) -> CertificationGate:
        """This deployment's certification gate, built once.

        Memoized so two jobs in one process can never be certified against
        different manifests, and so the gate the report route enforces is the
        same object the runner certified with. One gate serves every selection
        — see :meth:`tier_of` for why that is sound.
        """
        return self._memo("gate", self._build_gate)

    def runner(self, frameworks: Sequence[FrameworkName]) -> AdkPipelineRunner:
        """The production runner for one framework selection: its graph and the gate.

        Memoized **per selection**, because the graph is expensive to compose —
        instructions are built once so the cacheable prefix every node shares is
        byte-identical across jobs — and because a graph is built for one
        selection. Two jobs naming the same frameworks in the same order share
        one runner; naming them in a different order does not, since order is
        the report's block order and a different order is a different graph.
        """
        selection = self.selection(frameworks)
        return self._memo(
            f"runner:{','.join(selection)}",
            lambda: AdkPipelineRunner(
                self.pipeline(selection), certification=self.gate()
            ),
        )

    def _build_gate(self) -> CertificationGate:
        return CertificationGate(
            manifest=self.manifest,
            tier_of=self.tier_of,
            require_certified=self.require_certified,
        )

    def _memo(self, key: str, build):
        if key not in self._built:
            self._built[key] = build()
        return self._built[key]


def _flag(env: Mapping[str, str], var: str) -> bool:
    """A boolean env flag, on only for an explicit affirmative."""
    return env.get(var, "").strip().lower() in ("1", "true", "yes", "on")
