"""Model-tier configuration for the graph's LLM nodes.

There are three vendor-neutral tiers. ``base`` runs extraction and repair.
``strong`` runs a framework's lane agents. ``review`` exists so a deployment can
bind criticism away from the analysis it checks. Each tier selects its own
``(vendor, model)`` pair, so the three may run different vendors at once, and no
vendor is privileged.

``review`` is a place rather than a policy. Which nodes sit on it is the node
map's business, and the shipped map puts ``critic/<name>`` and
``recritic/<name>`` on ``strong``. That is the same domain as the analysis,
which is cheaper and is what most deployments want. What the tier buys is that a
deployment can move them, and ``review_independence`` in the config file is
where it says how far apart they have to be. With two tiers, the only way to make criticism
distinct was to run it on ``base``, which is a re-ask on a cheaper model than
the pass it corrects. :func:`critic_pairing_issues` exists to refuse that.

Vendor and model are two keys rather than one router string. Three consumers
need the vendor as a key: the credential mode it implies, the family-branching
floating-form rule, and the two-argument build-time sampling gate. ``vertex_ai/``
is also not one provider, so a joined string would have to be parsed back apart
by all three. The prefix exists in exactly one place, :attr:`Vendor.prefix`.

No vendor is selected by default. The shipped config names no ``(vendor, model)``
pair for any tier, so a first run stops with :func:`_require_selected_tiers`'
message rather than reaching a vendor nobody chose. "No privileged default" is
therefore a property of the shipped values as well as of the mechanism: every
vendor is reached the same way, and none of them is what an operator gets by
doing nothing.

Loading fails closed. An unselected tier, an unknown tier, vendor or node name,
a floating model identifier from the file or from an env var, an undeclared
credential mode for a vendor that allows more than one, a node missing from the
mapping, or a config version other than :data:`SUPPORTED_VERSION`
raises :class:`ModelConfigError` rather than degrading. There is no cross-tier
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
from analysis_service.vendors import (
    CREDENTIAL_MODES,
    VENDOR_NAMES,
    CredentialMode,
    Vendor,
    VendorName,
    vendor_for,
)

# The only schema version this loader accepts. A file on any other version
# fails its own check rather than being migrated in place.
#
# Version 7 adds the ``[credentials]`` table: a deployment declares which
# credential mode it uses for a vendor that allows more than one. Bedrock is the
# vendor that has the choice, so a file selecting it names a mode there or in
# ``ANALYSIS_MODEL_CREDENTIALS_BEDROCK``. Every shipped file leaves the table
# out, because no shipped file selects a vendor at all.
SUPPORTED_VERSION = 7

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
_CREDENTIALS_STEM = f"{_ENV_PREFIX}CREDENTIALS"


class ModelConfigError(ConfigError):
    """The model-tier configuration is invalid or unusable."""


def env_vars_for(tier: TierName) -> tuple[str, str]:
    """The ``(vendor, model)`` override vars for one tier."""
    stem = f"{_ENV_PREFIX}{tier.upper()}"
    return f"{stem}_{_VENDOR_FIELD}", f"{stem}_{_MODEL_FIELD}"


def credentials_env_var_for(vendor: VendorName) -> str:
    """The var declaring one vendor's credential mode.

    Keyed by vendor and not by tier, exactly as the ``[credentials]`` table is:
    a mode describes the deployment's relationship with a vendor, and a per-tier
    variable could name two identities for one vendor in one process.

    This exists so a deployment can select a multi-mode vendor from the
    environment alone. Every other selection already moves that way — a tier's
    vendor and model both do — and a credential mode that moved only in the file
    would mean a container image had to be rebuilt to run the vendor it already
    carries.
    """
    return f"{_CREDENTIALS_STEM}_{vendor.upper()}"


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
        """The LiteLLM router string: the vendor's prefix joined to the model."""
        return self.vendor_entry.route(self.model)


class ModelTierConfig(BaseModel):
    """Validated per-tier selections and the node -> tier mapping."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    tiers: dict[TierName, TierSelection]
    nodes: dict[str, TierName]
    #: Which credential mode this deployment declares for each vendor. Keyed by
    #: vendor rather than by tier: a mode describes the deployment's
    #: relationship with a vendor, and a per-tier key could name two identities
    #: for one vendor in one process.
    #:
    #: Empty in every shipped file, because no shipped file selects a vendor.
    #: :meth:`_credential_mode_problems` is what makes the table
    #: self-completing: a key for a single-mode vendor is an error, and a
    #: missing key for a multi-mode vendor is an error, so a vendor row that
    #: gains a second mode cannot ship without an operator declaring one.
    #: :func:`credentials_env_var_for` names the variable that fills it from the
    #: environment.
    credentials: dict[VendorName, CredentialMode] = Field(default_factory=dict)
    #: How far each framework's criticism must sit from its own analysis. No
    #: default: a deployment states it, because inheriting ``shared`` is how a
    #: high-assurance install ends up reviewing itself and reporting nothing
    #: unusual. :data:`ReviewIndependence` names the three settings.
    review_independence: ReviewIndependence

    @model_validator(mode="after")
    def _check_complete(self) -> Self:
        # The tiers this map runs something on, not all of them. `build_adapters`
        # binds no adapter for an unused tier, so demanding a selection for one
        # asked an operator to choose a model no request reaches.
        in_use = set(self.nodes.values())
        missing_tiers = [
            tier for tier in TIER_NAMES if tier in in_use and tier not in self.tiers
        ]
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
        problems += self._credential_mode_problems()
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def _credential_mode_problems(self) -> list[str]:
        """Every vendor whose declared mode is absent, spurious or not allowed.

        Both halves read :data:`~analysis_service.vendors.CREDENTIAL_MODES`, so
        the rule follows the registry rather than a second copy of it. A key for
        a single-mode vendor is an error because it is not a choice, and letting
        it sit there would let a file state a mode the registry has since
        replaced.

        Only vendors a tier actually selects are required to declare. A
        multi-mode vendor nobody calls needs no identity.
        """
        problems = []
        selected = {selection.vendor for selection in self.tiers.values()}
        for vendor, mode in self.credentials.items():
            allowed = CREDENTIAL_MODES[vendor]
            if len(allowed) == 1:
                problems.append(
                    f"credentials.{vendor} is set, but {vendor!r} allows only"
                    f" {allowed[0].value!r}, so there is nothing to choose;"
                    " remove the key"
                )
            elif mode not in allowed:
                names = ", ".join(sorted(m.value for m in allowed))
                problems.append(
                    f"credentials.{vendor} is {mode.value!r}, which {vendor!r}"
                    f" does not allow (it allows: {names})"
                )
        for vendor in sorted(selected - set(self.credentials)):
            allowed = CREDENTIAL_MODES[vendor]
            if len(allowed) > 1:
                names = ", ".join(sorted(m.value for m in allowed))
                problems.append(
                    f"vendor {vendor!r} allows more than one credential mode"
                    f" ({names}), so credentials.{vendor} must declare which"
                    " one this deployment uses"
                )
        return problems

    def credential_mode(self, vendor: VendorName) -> CredentialMode:
        """The credential mode this deployment uses for one vendor.

        The one reader of the declared-mode rule. A vendor with a single mode
        needs no declaration and gets that mode; a vendor with a choice has
        already been required to declare one by
        :meth:`_credential_mode_problems`, so the lookup cannot fall through to
        a guess.
        """
        declared = self.credentials.get(vendor)
        return (
            declared
            if declared is not None
            else vendor_for(vendor).sole_credential_mode
        )

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

    ``ANALYSIS_MODEL_CREDENTIALS_{VENDOR}`` folds into the ``[credentials]``
    table the same way, and the loader's own rules are what judge the result: a
    declaration for a single-mode vendor is an error whether it arrived from the
    file or from the environment, and so is a mode the vendor does not allow.

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
    known |= {credentials_env_var_for(vendor) for vendor in VENDOR_NAMES}
    unknown = sorted(
        var for var in env if var.startswith(_ENV_PREFIX) and var not in known
    )
    if unknown:
        raise ModelConfigError(
            f"unrecognised model override(s): {unknown};"
            f" expected {sorted(known)} (schema version {SUPPORTED_VERSION})"
        )

    _apply_credential_overrides(raw, env)

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


def _apply_credential_overrides(raw: dict[str, object], env: Mapping[str, str]) -> None:
    """Fold ``ANALYSIS_MODEL_CREDENTIALS_{VENDOR}`` into the raw table.

    The value is written through unvalidated: pydantic rejects a mode outside
    :class:`~analysis_service.vendors.CredentialMode`, and
    :meth:`ModelTierConfig._credential_mode_problems` rejects one the vendor
    does not allow. Judging it here would be a second reader of both rules.
    """
    declared = {
        vendor: env[var]
        for vendor in VENDOR_NAMES
        if (var := credentials_env_var_for(vendor)) in env
    }
    if not declared:
        return
    table = raw.setdefault("credentials", {})
    if not isinstance(table, dict):
        raise ModelConfigError("credentials: not a table")
    for vendor, mode in declared.items():
        if not mode.strip():
            raise ModelConfigError(
                f"{credentials_env_var_for(vendor)} is set but empty"
            )
        table[vendor] = mode.strip()


def tiers_in_use(nodes_raw: object) -> set[str]:
    """The tiers the node map actually runs something on.

    A tier is a place a node can sit, and an empty place costs nothing to leave
    empty: :func:`~analysis_service.binding.build_tier_adapters` already skips a tier nothing is bound to, so
    a selection for one is a pair no request ever reaches. Requiring it anyway
    made a first run name a vendor and a model for a tier the shipped map does
    not use, which is a choice with no consequence -- and the answer the config
    file suggested was to repeat the ``strong`` pair, which is a choice that
    says nothing at all.

    The reason the requirement existed still holds and is kept: the day somebody
    moves ``critic/*`` onto ``review`` is the wrong day to find out no model was
    chosen for it. That day is a node-map edit, and this is read on that edit,
    so the check fires then -- at the same moment, with the same message.
    """
    nodes = nodes_raw if isinstance(nodes_raw, dict) else {}
    return {tier for tier in nodes.values() if tier in TIER_NAMES}


def _require_selected_tiers(
    path: Path | str, tiers_raw: object, nodes_raw: object
) -> None:
    """Stop with an actionable message when a tier in use names no vendor.

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
    in_use = tiers_in_use(nodes_raw)
    missing = [
        tier
        for tier in TIER_NAMES
        if tier in in_use
        and not (
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
    _require_selected_tiers(path, raw.get("tiers"), raw.get("nodes"))

    try:
        return ModelTierConfig(**raw)
    except ValidationError as exc:
        raise ModelConfigError(f"{path}: {exc}") from exc
