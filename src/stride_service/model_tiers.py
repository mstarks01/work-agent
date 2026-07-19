"""Model-tier configuration for the graph's LLM nodes.

Implements the tier assignment from wayfinder ticket 007: exactly two named
tiers (``flash`` for extraction/normalization, ``pro`` for the six STRIDE
analysts and the critic), each a single pinned Vertex model version string. A
versioned TOML file is the source of truth for both the tier strings and the
node -> tier mapping; ``STRIDE_MODEL_FLASH`` / ``STRIDE_MODEL_PRO`` override
the tier strings at deploy time, while the node -> tier mapping is file-only.

Loading fails closed: an alias or unpinned model string (from the file *or*
an env var), an unknown tier or node name, or a node missing from the mapping
raises :class:`ModelConfigError` instead of degrading — a node silently
running on the wrong model would invalidate every eval result. There is no
cross-tier fallback anywhere here by design.
"""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from stride_service.skills import STRIDE_CATEGORIES

TierName = Literal["flash", "pro"]
TIER_NAMES: tuple[TierName, ...] = ("flash", "pro")

# The LLM nodes of the decided topology (ticket 004). Deterministic
# FunctionNodes (validate, prepare, join, router, assemble) carry no model
# and never appear in the config.
ANALYST_NODES: tuple[str, ...] = tuple(
    f"analyst/{category}" for category in STRIDE_CATEGORIES
)
LLM_NODES: tuple[str, ...] = ("extract", "repair", *ANALYST_NODES, "critic")

# A pinned Vertex model string ends in a numeric version suffix such as
# "-002". This mechanically excludes "-latest" and bare aliases like
# "gemini-2.5-pro", plus dated preview builds — none of which are
# eval-reproducible or safe on regional endpoints.
_PINNED_SUFFIX = re.compile(r"-\d{3,}$")

_ENV_PREFIX = "STRIDE_MODEL_"


class ModelConfigError(ValueError):
    """The model-tier configuration is invalid or unusable."""


def env_var_for(tier: TierName) -> str:
    """The env var that overrides one tier's model string."""
    return f"{_ENV_PREFIX}{tier.upper()}"


def validate_model_string(value: str, source: str) -> str:
    """Require a pinned Vertex model version string; reject aliases.

    ``source`` names where the string came from (file key or env var) so the
    error points ops at the right knob.
    """
    if value.endswith("-latest"):
        raise ModelConfigError(
            f"{source}: {value!r} is a '-latest' alias; pin a model version"
        )
    if not _PINNED_SUFFIX.search(value):
        raise ModelConfigError(
            f"{source}: {value!r} is not a pinned model version string"
            " (expected a numeric suffix such as '-002')"
        )
    return value


class ModelTierConfig(BaseModel):
    """Validated tier strings and node -> tier mapping."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    tiers: dict[TierName, str]
    nodes: dict[str, TierName]

    @model_validator(mode="after")
    def _check_complete(self) -> Self:
        missing_tiers = [t for t in TIER_NAMES if t not in self.tiers]
        if missing_tiers:
            raise ValueError(f"tiers missing entries for: {missing_tiers}")
        for tier, model in self.tiers.items():
            validate_model_string(model, source=f"tiers.{tier}")
        unknown = sorted(set(self.nodes) - set(LLM_NODES))
        if unknown:
            raise ValueError(f"unknown node names: {unknown}")
        missing = [n for n in LLM_NODES if n not in self.nodes]
        if missing:
            raise ValueError(f"nodes missing entries for: {missing}")
        return self

    def resolve_model(self, node: str) -> str:
        """The pinned model string the named LLM node runs on."""
        if node not in self.nodes:
            raise ModelConfigError(f"unknown LLM node: {node!r}")
        return self.tiers[self.nodes[node]]


def load_model_tiers(
    path: Path | str,
    env: Mapping[str, str] | None = None,
) -> ModelTierConfig:
    """Load and validate the tier config, applying env-var overrides.

    ``STRIDE_MODEL_FLASH`` / ``STRIDE_MODEL_PRO`` replace the corresponding
    tier string before validation, so an alias arriving via the environment
    is rejected exactly like one in the file. A set-but-empty override is a
    deploy mistake and raises rather than being silently ignored.
    """
    if env is None:
        env = os.environ
    try:
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ModelConfigError(f"{path}: invalid TOML: {exc}") from exc

    tiers = raw.get("tiers")
    if isinstance(tiers, dict):
        for tier in TIER_NAMES:
            var = env_var_for(tier)
            if var not in env:
                continue
            value = env[var]
            if not value.strip():
                raise ModelConfigError(f"{var} is set but empty")
            tiers[tier] = validate_model_string(value, source=var)

    try:
        return ModelTierConfig(**raw)
    except ValidationError as exc:
        raise ModelConfigError(f"{path}: {exc}") from exc
