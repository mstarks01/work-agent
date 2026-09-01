"""Model-tier configuration for the graph's LLM nodes.

Three vendor-neutral tiers: ``base`` runs extraction and repair, ``strong`` runs
a framework's lane agents, and ``review`` exists so that criticism can be bound
away from the analysis it checks. Each tier independently selects a ``(vendor,
model)`` **pair**, so the three may run different vendors at once, and no vendor
is privileged.

**``review`` is a place, not a policy.** Which nodes sit on it is the node map's
business, and the shipped map puts ``critic/<name>`` and ``recritic/<name>`` on
``strong`` — the same domain as the analysis, which is cheaper and is what most
deployments want. What the tier buys is that a deployment *can* move them, and
:data:`REVIEW_INDEPENDENCE` is where it says how far apart they have to be. With
two tiers the only way to make criticism distinct was to run it on ``base``,
which is a re-ask on a cheaper model than the pass it corrects — the failure
:func:`critic_pairing_issues` exists to refuse.

Vendor and model are two keys, never one router string. Three consumers need
the vendor as a *key* — the credential mode it implies, the family-branching
floating-form rule, and the two-argument build-time sampling gate — and
``vertex_ai/`` is not one provider, so a joined string would have to be parsed
back apart by all three. The prefix exists in exactly one place,
:attr:`Vendor.prefix`.

**No vendor is selected by default.** The shipped config names no ``(vendor,
model)`` pair for either tier, so a first run stops with
:func:`_require_selected_tiers`' message rather than reaching a vendor nobody
chose. "No privileged default" is therefore a property of the shipped values,
not only of the mechanism: every vendor is reached the same way, and none of
them is what you get by doing nothing.

Loading fails closed: an unselected tier, an unknown tier, vendor or node name,
a floating model identifier (from the file *or* an env var), a node missing from
the mapping, or a config version other than :data:`SUPPORTED_VERSION` raises
:class:`ModelConfigError` rather than degrading. There is no cross-tier
fallback and no compatibility shim for other schema versions.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from analysis_service.errors import ConfigError
from analysis_service.report import FRAMEWORK_NAMES
from analysis_service.vendors import VENDOR_NAMES, Vendor, VendorName, vendor_for

# The only schema version this loader accepts. A file on any other version
# fails its own check rather than being migrated in place.
#
# Version 6 adds the ``review`` tier and ``review_independence``. Both are
# required in every install: a third tier a file may omit is a third tier no
# deployment has chosen a model for, and the policy defaults to nothing because
# "how independent is your critic" is a question a deployment answers rather
# than inherits.
SUPPORTED_VERSION = 6

TierName = Literal["base", "strong", "review"]
TIER_NAMES: tuple[TierName, ...] = ("base", "strong", "review")

# How far a framework's criticism has to sit from its own analysis. A table
# rather than a chain of ``if``s, and the values are ordered weakest first so a
# reader sees that each admits strictly less than the one below it.
#
# * ``shared`` requires nothing. Criticism may run on the very model it checks,
#   which is the shipped configuration: a critic on one model still catches
#   inconsistency and unsupported claims, and it costs one tier rather than two.
# * ``distinct_model`` requires a different ``(vendor, model)`` pair. It removes
#   a single build's blind spots, and leaves the provider's.
# * ``distinct_provider`` requires a different vendor. It removes a provider's
#   blind spots too, at the cost of a second credential and a second quota.
#
# **None of these makes a review more accurate.** Independence bounds correlated
# failure; it does not make a second opinion a better one, and a deployment that
# reads ``distinct_provider`` as "more correct" has read it wrong.
ReviewIndependence = Literal["shared", "distinct_model", "distinct_provider"]
REVIEW_INDEPENDENCE: tuple[ReviewIndependence, ...] = (
    "shared",
    "distinct_model",
    "distinct_provider",
)


# The graph's LLM nodes. Deterministic FunctionNodes (validate, prepare, join,
# router, assemble) carry no model and never appear in the config. ``recritic``
# is the bounded critic re-ask: a distinct LLM node so it is pinned in its own
# right, running the same judgement as the critic and so always on the same
# tier.
#
# Three keys per framework, named for what they do. ``Analyst`` names the human
# reading the report, so it is not a node name here or anywhere else. These are
# the *tier config* keys; the graph's own node names are a separate namespace
# (they must be Python identifiers, and they carry the lane as well as the
# framework), mapped by :func:`analysis_service.graph.tier_node_by_graph_node`,
# which is built per selection because which nodes exist is a function of it.
#
# **The keys are the service's, and there is one set per framework rather than
# one per lane.** A framework's lanes all run the same judgement on the same
# tier, so six keys holding one value had no reader; what an operator actually
# chooses between is running one framework's analysis cheaper than another's.
#
# ``recritic/<name>`` is the bounded critic re-ask: a distinct node so it is
# pinned in its own right, and the loader requires it to resolve to the same
# tier as its own ``critic/<name>``. That pairing used to be a comment in the
# config file; at two keys a comment holds, and at 2N keys it drifts, so it is a
# check. A re-ask on a cheaper model than the pass it corrects is the failure
# the comment warned about.
def _framework_nodes() -> tuple[str, ...]:
    return tuple(
        f"{role}/{name}"
        for name in FRAMEWORK_NAMES
        for role in ("analyze", "critic", "recritic")
    )


FRAMEWORK_NODES: tuple[str, ...] = _framework_nodes()
LLM_NODES: tuple[str, ...] = ("extract", "repair", *FRAMEWORK_NODES)


def critic_pairing_issues(resolve_tier) -> list[str]:
    """Every framework's ``recritic`` sits on the same tier as its ``critic``.

    Takes the resolver rather than a config object so the rule can be stated
    once and checked wherever a node -> tier map exists.
    """
    return [
        f"recritic/{name} resolves to {resolve_tier(f'recritic/{name}')!r} but"
        f" critic/{name} resolves to {resolve_tier(f'critic/{name}')!r};"
        " a re-ask must not run on a cheaper model than the pass it corrects"
        for name in FRAMEWORK_NAMES
        if resolve_tier(f"recritic/{name}") != resolve_tier(f"critic/{name}")
    ]


_ENV_PREFIX = "ANALYSIS_MODEL_"
_VENDOR_FIELD = "VENDOR"
_MODEL_FIELD = "MODEL"


class ModelConfigError(ConfigError):
    """The model-tier configuration is invalid or unusable."""


def env_vars_for(tier: TierName) -> tuple[str, str]:
    """The ``(vendor, model)`` override vars for one tier."""
    stem = f"{_ENV_PREFIX}{tier.upper()}"
    return f"{stem}_{_VENDOR_FIELD}", f"{stem}_{_MODEL_FIELD}"


def validate_model_string(value: str, vendor: VendorName, source: str) -> str:
    """Require a pinned model identifier for ``vendor``; reject floating forms.

    The rule is per-vendor data on the registry entry and is deliberately an
    open-world denylist — see :mod:`analysis_service.vendors`. ``source`` names
    where the string came from so the error points at the right knob.
    """
    try:
        return vendor_for(vendor).validate_model(value, source)
    except ValueError as exc:
        raise ModelConfigError(str(exc)) from exc


class TierSelection(BaseModel):
    """One tier's ``(vendor, model)`` pair."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    vendor: VendorName
    model: str

    @property
    def vendor_entry(self) -> Vendor:
        """The registry entry for this selection's vendor."""
        return vendor_for(self.vendor)

    @property
    def route(self) -> str:
        """The LiteLLM router string, e.g. ``vertex_ai/gemini-2.5-pro``."""
        return self.vendor_entry.route(self.model)


class ModelTierConfig(BaseModel):
    """Validated per-tier selections and the node -> tier mapping."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    tiers: dict[TierName, TierSelection]
    nodes: dict[str, TierName]
    #: How far each framework's criticism must sit from its own analysis. No
    #: default: a deployment states it, because inheriting ``shared`` is how a
    #: high-assurance install ends up reviewing itself and reporting nothing
    #: unusual. See :data:`REVIEW_INDEPENDENCE`.
    review_independence: ReviewIndependence

    @model_validator(mode="after")
    def _check_complete(self) -> Self:
        missing_tiers = [tier for tier in TIER_NAMES if tier not in self.tiers]
        if missing_tiers:
            raise ValueError(f"tiers missing entries for: {missing_tiers}")
        for tier, selection in self.tiers.items():
            validate_model_string(
                selection.model, selection.vendor, source=f"tiers.{tier}.model"
            )
        unknown = sorted(set(self.nodes) - set(LLM_NODES))
        if unknown:
            raise ValueError(f"unknown node names: {unknown}")
        missing = [node for node in LLM_NODES if node not in self.nodes]
        if missing:
            raise ValueError(f"nodes missing entries for: {missing}")
        # Both cross-node rules, run here because this is the one place a
        # complete node -> tier map exists. `critic_pairing_issues` was written
        # as a free function and never called from anywhere, so the pairing the
        # config file said the loader checked was in fact unchecked; a third
        # tier is where that would first have cost something, since `review` is
        # a place a critic can move to and leave its re-ask behind.
        problems = critic_pairing_issues(self.nodes.__getitem__)
        problems += self.independence_breaches()
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def independence_breaches(self) -> list[str]:
        """Every framework whose criticism is closer to its analysis than policy allows.

        **Fails closed at load, which is why there is no runtime warning.** A
        deployment that asked for a distinct reviewer and cannot have one has a
        configuration error, not a run to annotate — annotating it would put the
        finding in the artifact of a job somebody already paid for. What the
        report carries instead is the policy itself, so a reader of a ``shared``
        run can see that the review was same-domain rather than infer it.

        Checked per framework rather than once, because the node map is per
        framework: a deployment may run one package's analysis on ``strong`` and
        another's on ``base``, and each one's critic has to be independent of its
        own analysis rather than of some other package's.
        """
        if self.review_independence == "shared":
            return []
        distinct = "vendor" if self.review_independence == "distinct_provider" else None
        breaches = []
        for name in FRAMEWORK_NAMES:
            analyze = self.tiers[self.nodes[f"analyze/{name}"]]
            critic = self.tiers[self.nodes[f"critic/{name}"]]
            if distinct == "vendor":
                shared_part = analyze.vendor == critic.vendor
                detail = f"both run vendor {analyze.vendor!r}"
            else:
                shared_part = (analyze.vendor, analyze.model) == (
                    critic.vendor,
                    critic.model,
                )
                detail = f"both run {analyze.vendor}/{analyze.model}"
            if shared_part:
                breaches.append(
                    f"review_independence is {self.review_independence!r} but"
                    f" analyze/{name} and critic/{name} are not independent:"
                    f" {detail}. Point critic/{name} and recritic/{name} at a"
                    f" tier whose selection differs, or set review_independence"
                    f' to "shared" and accept a same-domain review'
                )
        return breaches

    def resolve_tier(self, node: str) -> TierName:
        """The tier the named LLM node runs on.

        The node -> tier map lives here once; ``resolve_sampling`` reuses it via
        this method so sampling never re-derives or duplicates it.
        """
        if node not in self.nodes:
            raise ModelConfigError(f"unknown LLM node: {node!r}")
        return self.nodes[node]

    def resolve_model(self, node: str) -> TierSelection:
        """The ``(vendor, model)`` pair the named LLM node runs on."""
        return self.tiers[self.resolve_tier(node)]


def _apply_env_overrides(raw: dict[str, object], env: Mapping[str, str]) -> None:
    """Fold ``ANALYSIS_MODEL_{TIER}_{VENDOR,MODEL}`` overrides into the raw tables.

    ``_MODEL`` alone is the ops case the file header exists for — retuning a
    tier's model on a deployed revision without an image rebuild. ``_VENDOR``
    alone is a **build-time error**: it is the one half-set case nothing
    downstream catches, because a cross-vendor pair like ``anthropic`` +
    ``gemini-2.5-pro`` passes the floating-form denylist and passes the
    sampling gate (an unknown model falls back to the provider's base config),
    and there is no build-time existence check — so it would die on node one of
    a paid-for job instead.

    An unrecognised ``ANALYSIS_MODEL_*`` variable also raises rather than being
    silently ignored while the tier quietly runs the file's model.

    That namespace check runs before the table is touched, so a typo'd override
    is reported even when the file selects nothing — which, since nothing is
    selected by default, is the state a deployment configured purely by
    environment starts from. A ``tiers`` table absent from the file is created
    empty here for the same reason: ``_VENDOR`` + ``_MODEL`` together are a
    complete selection, and requiring a file edit to make them land would mean
    no deployment could configure itself from the environment alone.
    """
    known = {var for tier in TIER_NAMES for var in env_vars_for(tier)}
    unknown = sorted(
        var for var in env if var.startswith(_ENV_PREFIX) and var not in known
    )
    if unknown:
        raise ModelConfigError(
            f"unrecognised model override(s): {unknown};"
            f" expected {sorted(known)} (schema version {SUPPORTED_VERSION})"
        )

    tiers_raw = raw.setdefault("tiers", {})
    if not isinstance(tiers_raw, dict):
        # A malformed ``tiers`` shape: leave it for ModelTierConfig to reject
        # rather than applying an override against nothing.
        return

    for tier in TIER_NAMES:
        vendor_var, model_var = env_vars_for(tier)
        vendor = env.get(vendor_var)
        model = env.get(model_var)
        if vendor is not None and model is None:
            raise ModelConfigError(
                f"{vendor_var} is set without {model_var};"
                " a vendor without its model is not a usable selection"
            )
        if vendor is None and model is None:
            # Touching nothing is what lets an unselected tier stay unselected:
            # an empty table conjured here would read as a selection to
            # ``_require_selected_tiers`` and swap its message for a pydantic
            # dump about two missing fields.
            continue
        table = tiers_raw.setdefault(tier, {})
        if not isinstance(table, dict):
            raise ModelConfigError(f"tiers.{tier}: not a table")
        for var, value in ((vendor_var, vendor), (model_var, model)):
            if value is None:
                continue
            if not value.strip():
                raise ModelConfigError(f"{var} is set but empty")
            table[var.rsplit("_", 1)[-1].lower()] = value.strip()


def _require_selected_tiers(path: Path | str, tiers_raw: object) -> None:
    """Stop with an actionable message when a tier names no vendor.

    Separate from :meth:`ModelTierConfig._check_complete`, which catches the
    same gap for a config built in code and reports it as a pydantic validation
    error. This one exists for the case that is now the *shipped* state — a
    first run against a config that selects nothing — where the error is the
    entire onboarding instruction, so it names the vendors that are available
    and both places a selection can be made. A pydantic error dump at that
    moment would be accurate and useless.

    Vendor **names** only. The message is reached before any credential is
    read, and naming a variable's value here is how a key ends up in a log
    (OWASP A09).
    """
    selected = tiers_raw if isinstance(tiers_raw, dict) else {}
    # A *complete* pair, not merely a present table: `_MODEL` alone against an
    # unselected file leaves a tier holding a model and no vendor, which is the
    # same unmade choice and deserves the same instruction.
    missing = [
        tier
        for tier in TIER_NAMES
        if not (
            isinstance(selected.get(tier), dict)
            and {"vendor", "model"} <= set(selected[tier])
        )
    ]
    if not missing:
        return
    overrides = " and ".join(" + ".join(env_vars_for(tier)) for tier in missing)
    raise ModelConfigError(
        f"{path}: no vendor selected for tier(s) {missing}."
        " This service ships no default vendor, so each tier must name one"
        f" before it can run. Supported vendors: {', '.join(VENDOR_NAMES)}."
        f" Set a (vendor, model) pair per tier in the file, or set {overrides}."
        " See docs/First-Run.md step 2."
    )


def load_model_tiers(
    path: Path | str,
    env: Mapping[str, str] | None = None,
) -> ModelTierConfig:
    """Load and validate the tier config, applying env-var overrides.

    Overrides are folded in before validation, so a floating identifier arriving
    via the environment is rejected exactly like one in the file. Every failure
    path raises :class:`ModelConfigError`.
    """
    if env is None:
        env = os.environ
    try:
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ModelConfigError(f"{path}: invalid TOML: {exc}") from exc
    except OSError as exc:
        raise ModelConfigError(f"{path}: cannot be read: {exc}") from exc

    _apply_env_overrides(raw, env)

    version = raw.get("version")
    if version != SUPPORTED_VERSION:
        raise ModelConfigError(
            f"{path}: unsupported version {version!r}; expected {SUPPORTED_VERSION}"
        )

    # After the version check, so a version-2 file reports the schema it is
    # rather than the selection it is missing.
    _require_selected_tiers(path, raw.get("tiers"))

    try:
        return ModelTierConfig(**raw)
    except ValidationError as exc:
        raise ModelConfigError(f"{path}: {exc}") from exc
