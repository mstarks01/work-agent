"""Model-tier configuration for the graph's LLM nodes.

Exactly two vendor-neutral tiers (#5): ``base`` runs extraction and repair,
``strong`` the six STRIDE analysts, the critic and the critic re-ask. Each tier
independently selects a ``(vendor, model)`` **pair**, so the two tiers may run
different vendors at once, and no vendor is privileged.

Vendor and model are two keys, never one router string (#15 decision 1). Three
consumers need the vendor as a *key* — the credential mode it implies (#9), the
family-branching floating-form rule (#7 decision 1), and the two-argument
build-time sampling gate (#13) — and #12 proved ``vertex_ai/`` is not one
provider, so a joined string would have to be parsed back apart by all three.
The prefix exists in exactly one place, :attr:`Vendor.prefix`.

Loading fails closed: an unknown tier, vendor or node name, a floating model
identifier (from the file *or* an env var), a node missing from the mapping, or
a config version other than :data:`SUPPORTED_VERSION` raises
:class:`ModelConfigError` rather than degrading. There is no cross-tier fallback
and no compatibility shim for the version-2 schema.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from stride_service.skills import STRIDE_CATEGORIES
from stride_service.vendors import Vendor, VendorName, vendor_for

# Hard cutover (#15 decision 4): version 2 named Gemini-only tier strings under
# ``flash``/``pro`` keys and cannot be read as a ``(vendor, model)`` pair. The
# loader accepts only version 3; a version-2 file fails its own check rather
# than being migrated in place.
SUPPORTED_VERSION = 3

TierName = Literal["base", "strong"]
TIER_NAMES: tuple[TierName, ...] = ("base", "strong")

# The LLM nodes of the decided topology (ticket 004). Deterministic
# FunctionNodes (validate, prepare, join, router, assemble) carry no model and
# never appear in the config. ``recritic`` is the bounded critic re-ask (ticket
# 038 decision 3): a distinct LLM node so it is pinned in its own right, running
# the same judgement as the critic and so always on the same tier.
ANALYST_NODES: tuple[str, ...] = tuple(
    f"analyst/{category}" for category in STRIDE_CATEGORIES
)
LLM_NODES: tuple[str, ...] = ("extract", "repair", *ANALYST_NODES, "critic", "recritic")

_ENV_PREFIX = "STRIDE_MODEL_"
_VENDOR_FIELD = "VENDOR"
_MODEL_FIELD = "MODEL"


class ModelConfigError(ValueError):
    """The model-tier configuration is invalid or unusable."""


def env_vars_for(tier: TierName) -> tuple[str, str]:
    """The ``(vendor, model)`` override vars for one tier (#15 decision 2)."""
    stem = f"{_ENV_PREFIX}{tier.upper()}"
    return f"{stem}_{_VENDOR_FIELD}", f"{stem}_{_MODEL_FIELD}"


def validate_model_string(value: str, vendor: VendorName, source: str) -> str:
    """Require a pinned model identifier for ``vendor``; reject floating forms.

    The rule is per-vendor data on the registry entry and is deliberately an
    open-world denylist — see :mod:`stride_service.vendors`. ``source`` names
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
        return self

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


def _apply_env_overrides(tiers_raw: object, env: Mapping[str, str]) -> None:
    """Fold ``STRIDE_MODEL_{TIER}_{VENDOR,MODEL}`` overrides into the raw tables.

    ``_MODEL`` alone is the ops case the file header exists for — retuning a
    tier's model on a deployed revision without an image rebuild. ``_VENDOR``
    alone is a **build-time error** (#15 decision 2): it is the one half-set case
    nothing downstream catches, because a cross-vendor pair like
    ``anthropic`` + ``gemini-2.5-pro`` passes the floating-form denylist, passes
    the sampling gate (an unknown model falls back to the provider's base
    config), and #7 decision 6 declined a build-time existence check — so it
    would die on node one of a paid-for job instead.

    An unrecognised ``STRIDE_MODEL_*`` variable also raises, which is what makes
    the cutover safe: a deployment still carrying version 2's
    ``STRIDE_MODEL_FLASH`` must fail loudly, not have it silently ignored while
    the tier quietly runs the file's model.
    """
    if not isinstance(tiers_raw, dict):
        # A malformed or missing ``tiers`` shape: leave it for ModelTierConfig
        # to reject rather than applying an override against nothing.
        return

    known = {var for tier in TIER_NAMES for var in env_vars_for(tier)}
    unknown = sorted(
        var for var in env if var.startswith(_ENV_PREFIX) and var not in known
    )
    if unknown:
        raise ModelConfigError(
            f"unrecognised model override(s): {unknown};"
            f" expected {sorted(known)} (schema version {SUPPORTED_VERSION})"
        )

    for tier in TIER_NAMES:
        vendor_var, model_var = env_vars_for(tier)
        vendor = env.get(vendor_var)
        model = env.get(model_var)
        if vendor is not None and model is None:
            raise ModelConfigError(
                f"{vendor_var} is set without {model_var};"
                " a vendor without its model is not a usable selection"
            )
        table = tiers_raw.setdefault(tier, {})
        if not isinstance(table, dict):
            raise ModelConfigError(f"tiers.{tier}: not a table")
        for var, value in ((vendor_var, vendor), (model_var, model)):
            if value is None:
                continue
            if not value.strip():
                raise ModelConfigError(f"{var} is set but empty")
            table[var.rsplit("_", 1)[-1].lower()] = value.strip()


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

    _apply_env_overrides(raw.get("tiers"), env)

    version = raw.get("version")
    if version != SUPPORTED_VERSION:
        raise ModelConfigError(
            f"{path}: unsupported version {version!r};"
            f" expected {SUPPORTED_VERSION} (hard cutover, no shim)"
        )

    try:
        return ModelTierConfig(**raw)
    except ValidationError as exc:
        raise ModelConfigError(f"{path}: {exc}") from exc
