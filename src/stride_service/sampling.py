"""Per-tier sampling configuration for the graph's LLM nodes.

Implements the model-tuning effort (wayfinder tickets 02 / 04 / 05): decoding
parameters are pinned per model class (tier) in a versioned TOML file that
**eval and production read from the same place** (ticket 009 decision 15).
``flash`` and ``pro`` carry their own params; the node -> tier map lives once
in ``model_tiers.toml`` and is reused via :meth:`ModelTierConfig.resolve_tier`,
never duplicated here.

Loading fails closed (OWASP A02/A10): an unsupported version, an unknown key or
tier, an out-of-range value, a ``candidate_count`` other than 1, or a
per-class-illegal thinking budget raises :class:`SamplingConfigError` instead
of falling back to a library default — a node quietly running on different
sampling than the config records invalidates every eval result taken against
it.

``STRIDE_SAMPLING_{TIER}_{PARAM}`` env overrides retune the *offered* params
(``temperature``, ``top_p``, ``seed``, ``thinking``) at deploy time, validated
identically to the file value; an env var naming a reserved or forbidden param
raises ("not overridable"), so the live-knob surface is exactly the offered
surface — no wider (ticket 03 §3). Overrides flow into the resolved values, so
the run's provenance fingerprint (tickets 03 / 07) captures them.

This module is the config layer only; :mod:`stride_service.graph` wires
``resolve_sampling`` onto each node so every node runs its own tier's decoding
params (ticket 06).
"""

from __future__ import annotations

import hashlib
import json
import os
import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal, Self

from google.genai import types
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from stride_service.model_tiers import TIER_NAMES, TierName

# Hard cutover (ticket 02 decision 6): the loader accepts only version 2 and
# fail-closes on anything else. There is no v1 shim.
SUPPORTED_VERSION = 2

# Both classes cap output at this ceiling (ticket 04); unset leaves the request
# uncapped up to it.
MODEL_OUTPUT_CEILING = 65_536

# Per-class thinking-budget legality (tickets 01 / 04). ``flash`` may disable
# thinking (0); ``pro``'s floor is 128 and 0 is a 400. Dynamic allocation (-1)
# is requested via "auto", never as a raw in-range integer.
THINKING_RANGE: dict[TierName, tuple[int, int]] = {
    "flash": (0, 24_576),
    "pro": (128, 32_768),
}
_THINKING_DYNAMIC = -1

# The mixed scalar a tier's ``thinking`` field accepts in the file / overrides.
ThinkingScalar = Literal["off", "auto"] | int

# Env override surface (ticket 03 §3): only these params are overridable. A var
# naming any other param — reserved (``candidate_count``) or forbidden — raises.
_ENV_PREFIX = "STRIDE_SAMPLING_"
OFFERED_PARAMS: tuple[str, ...] = ("temperature", "top_p", "seed", "thinking")


class SamplingConfigError(ValueError):
    """The sampling configuration is invalid or unusable."""


class _RawTier(BaseModel):
    """One tier's decoding params exactly as written in the file / overrides.

    ``extra="forbid"`` rejects an unknown or misspelled key rather than silently
    ignoring it. Every param is optional: an omitted key means "unset — leave
    the model's own default", which is how the shipped file expresses the params
    ticket 04 found have no publishable per-class constant.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, gt=0.0, le=1.0)
    top_k: float | None = Field(default=None, gt=0.0)  # typed float in genai
    seed: int | None = None
    max_output_tokens: int | None = Field(default=None, ge=1, le=MODEL_OUTPUT_CEILING)
    candidate_count: int = 1
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    thinking: ThinkingScalar | None = None

    @field_validator("candidate_count")
    @classmethod
    def _reserved_candidate_count(cls, value: int) -> int:
        # The Self-MoA lever (ticket 009): >1 silently drops candidates, so it
        # is reserved, not offered, and pinned at 1 until routed through the
        # union/dedupe path.
        if value != 1:
            raise ValueError(
                f"candidate_count must be 1 (reserved Self-MoA lever); got {value}"
            )
        return value


class _RawSampling(BaseModel):
    """The whole file's shape before per-tier thinking is resolved.

    ``extra="forbid"`` rejects a stray top-level key; ``dict[TierName, ...]``
    rejects an unknown tier name. Both tiers being *present* is enforced on the
    resolved :class:`SamplingConfig`, after overrides have had their chance to
    add one.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int
    tiers: dict[TierName, _RawTier]


class TierSampling(BaseModel):
    """One tier's resolved decoding params, ready to bind onto a node.

    Produced by the loader from a validated :class:`_RawTier` once the tier is
    known: the mixed ``thinking`` scalar is resolved to a class-legal
    ``thinking_budget`` (or ``None`` = the model's preset). Frozen; every field
    maps straight onto :class:`~google.genai.types.GenerateContentConfig`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    temperature: float | None = None
    top_p: float | None = None
    top_k: float | None = None
    seed: int | None = None
    max_output_tokens: int | None = None
    candidate_count: int = 1
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    thinking_budget: int | None = None

    def to_generate_content_config(self) -> types.GenerateContentConfig:
        """The ADK/GenAI object a node on this tier is configured with.

        An unset param is sent as ``None`` (the model applies its own default);
        an unset ``thinking_budget`` means no ``thinking_config`` at all, which
        leaves the model's preset per-class budget (ticket 04).
        """
        thinking_config = (
            types.ThinkingConfig(thinking_budget=self.thinking_budget)
            if self.thinking_budget is not None
            else None
        )
        return types.GenerateContentConfig(
            temperature=self.temperature,
            top_p=self.top_p,
            top_k=self.top_k,
            seed=self.seed,
            max_output_tokens=self.max_output_tokens,
            candidate_count=self.candidate_count,
            presence_penalty=self.presence_penalty,
            frequency_penalty=self.frequency_penalty,
            thinking_config=thinking_config,
        )


class SamplingConfig(BaseModel):
    """Validated per-tier sampling, keyed by model class.

    Mirrors :class:`~stride_service.model_tiers.ModelTierConfig`: both tiers are
    required, ``extra="forbid"`` and ``frozen`` throughout.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int
    tiers: dict[TierName, TierSampling]

    @model_validator(mode="after")
    def _check_complete(self) -> Self:
        missing = [tier for tier in TIER_NAMES if tier not in self.tiers]
        if missing:
            raise ValueError(f"tiers missing entries for: {missing}")
        return self

    def for_tier(self, tier: TierName) -> TierSampling:
        """The resolved sampling params for one model class."""
        return self.tiers[tier]


SamplingResolver = Callable[[str], TierSampling]


def sampling_fingerprint(served_model: str, sampling: TierSampling) -> str:
    """One node's generation-identity hash: ``sha256(served model, sampling)``.

    Model and the resolved tier sampling are bound into **one** hash (ticket 03
    §1): the eval gate certifies a tier's *generation behaviour*, which is model
    and sampling jointly — splitting them lets a mismatched pair pass two green
    half-checks. Keyed on the **served** model (per node, ticket 026) so a node
    served a different build gets a different fingerprint and that drift is
    visible.

    Canonical serialization — sorted keys over the resolved values plus the
    served model — so the hash is recomputable from the recorded clear block and
    served ``model`` alone, never from some upstream state. sha256 matches
    :attr:`~stride_service.report.InputRef.source_sha256`.
    """
    payload = {"model": served_model, **sampling.model_dump()}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def env_var_for(tier: TierName, param: str) -> str:
    """The env var that overrides one offered param on one tier."""
    return f"{_ENV_PREFIX}{tier.upper()}_{param.upper()}"


def resolve_thinking(value: ThinkingScalar | None, tier: TierName) -> int | None:
    """Resolve the mixed ``thinking`` scalar to a class-legal budget (ticket 04).

    Unset (``None``) leaves the model's preset per-class budget — today's
    default, which is *not* dynamic allocation. ``"auto"`` requests dynamic
    (``-1``); ``"off"`` disables (``flash`` only — ``pro``'s floor is 128, so it
    raises); an int is checked against the class range.
    """
    if value is None:
        return None
    if value == "auto":
        return _THINKING_DYNAMIC
    low, high = THINKING_RANGE[tier]
    if value == "off":
        if low > 0:
            raise SamplingConfigError(
                f"tiers.{tier}: thinking cannot be 'off'"
                f" (floor is {low}; 0 is a 400)"
            )
        return 0
    if not low <= value <= high:
        raise SamplingConfigError(
            f"tiers.{tier}: thinking budget {value} out of range [{low}, {high}]"
            " (use 'auto' for dynamic allocation)"
        )
    return value


def make_resolve_sampling(
    config: SamplingConfig, resolve_tier: Callable[[str], TierName]
) -> SamplingResolver:
    """The injected sibling of ``resolve_model``: node -> its tier's sampling.

    ``resolve_tier`` is :meth:`ModelTierConfig.resolve_tier`, so the node -> tier
    walk lives once in the tier config and the graph stays purely node-centric
    (ticket 02 decision 7). :func:`stride_service.graph.build_pipeline` wires the
    returned resolver onto each node (ticket 06).
    """

    def resolve_sampling(node: str) -> TierSampling:
        return config.for_tier(resolve_tier(node))

    return resolve_sampling


def _parse_env_value(var: str, param: str, value: str) -> float | int | str:
    """Parse an override string to its param's type; raise on a bad literal.

    Range and per-class legality stay in :class:`_RawTier` and
    :func:`resolve_thinking`, so an override is validated identically to a file
    value — this only converts the string to the type the file would hold.
    """
    if param in ("temperature", "top_p"):
        try:
            return float(value)
        except ValueError:
            raise SamplingConfigError(f"{var}: {value!r} is not a number") from None
    if param == "seed":
        try:
            return int(value)
        except ValueError:
            raise SamplingConfigError(f"{var}: {value!r} is not an integer") from None
    # thinking: the mixed scalar.
    if value in ("off", "auto"):
        return value
    try:
        return int(value)
    except ValueError:
        raise SamplingConfigError(
            f"{var}: {value!r} is not 'off', 'auto', or an integer budget"
        ) from None


def _apply_env_overrides(tiers_raw: object, env: Mapping[str, str]) -> None:
    """Fold ``STRIDE_SAMPLING_{TIER}_{PARAM}`` overrides into the raw tables.

    Only the offered params are overridable; a var naming a reserved or
    forbidden param raises ("not overridable") — the live-knob surface equals
    the offered surface, no wider (ticket 03 §3). A set-but-empty override is a
    deploy mistake and raises rather than being silently ignored. Values land in
    the raw tier table and are then validated by :class:`_RawTier` exactly like
    a file value.
    """
    if not isinstance(tiers_raw, dict):
        # A malformed / missing ``tiers`` shape: leave it for _RawSampling to
        # reject rather than silently accepting an override against nothing.
        return
    for var, value in env.items():
        if not var.startswith(_ENV_PREFIX):
            continue
        tier_token, _, param_token = var[len(_ENV_PREFIX) :].partition("_")
        tier = tier_token.lower()
        param = param_token.lower()
        if tier not in TIER_NAMES:
            raise SamplingConfigError(f"{var}: unknown tier {tier_token!r}")
        if param not in OFFERED_PARAMS:
            offered = ", ".join(OFFERED_PARAMS)
            raise SamplingConfigError(
                f"{var}: {param_token or '(missing param)'} is not overridable"
                f" (only {offered})"
            )
        if not value.strip():
            raise SamplingConfigError(f"{var} is set but empty")
        table = tiers_raw.setdefault(tier, {})
        if not isinstance(table, dict):
            raise SamplingConfigError(f"tiers.{tier}: not a table")
        table[param] = _parse_env_value(var, param, value.strip())


def load_sampling(
    path: Path | str, env: Mapping[str, str] | None = None
) -> SamplingConfig:
    """Load and validate the per-tier sampling config, applying env overrides.

    ``STRIDE_SAMPLING_{TIER}_{PARAM}`` overrides are folded in before validation,
    so an out-of-range value arriving via the environment fails closed exactly
    like one in the file. Every failure path raises :class:`SamplingConfigError`.
    """
    if env is None:
        env = os.environ
    try:
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise SamplingConfigError(f"{path}: invalid TOML: {exc}") from exc
    except OSError as exc:
        raise SamplingConfigError(f"{path}: cannot be read: {exc}") from exc

    _apply_env_overrides(raw.get("tiers"), env)

    try:
        parsed = _RawSampling(**raw)
    except ValidationError as exc:
        raise SamplingConfigError(f"{path}: {exc}") from exc

    if parsed.version != SUPPORTED_VERSION:
        raise SamplingConfigError(
            f"{path}: unsupported version {parsed.version};"
            f" expected {SUPPORTED_VERSION} (hard cutover, no v1 shim)"
        )

    resolved = {
        tier: TierSampling(
            temperature=raw_tier.temperature,
            top_p=raw_tier.top_p,
            top_k=raw_tier.top_k,
            seed=raw_tier.seed,
            max_output_tokens=raw_tier.max_output_tokens,
            candidate_count=raw_tier.candidate_count,
            presence_penalty=raw_tier.presence_penalty,
            frequency_penalty=raw_tier.frequency_penalty,
            thinking_budget=resolve_thinking(raw_tier.thinking, tier),
        )
        for tier, raw_tier in parsed.tiers.items()
    }
    try:
        return SamplingConfig(version=parsed.version, tiers=resolved)
    except ValidationError as exc:
        raise SamplingConfigError(f"{path}: {exc}") from exc
