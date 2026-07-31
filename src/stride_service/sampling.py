"""Per-tier sampling configuration for the graph's LLM nodes.

Decoding parameters are pinned per tier in a versioned TOML file that **eval
and production read from the same place**. ``base`` and ``strong`` carry their
own params; the node -> tier map lives once in ``model_tiers.toml`` and is
reused via :meth:`ModelTierConfig.resolve_tier`, never duplicated here.

Three things about the surface:

* **``top_k`` is not on it.** It is absent from
  ``litellm.utils.get_optional_params``'s signature, so it is the one param the
  build-time gate provably cannot cover — LiteLLM re-injects it raw into the
  request body after the check — and its wrongness would be *silent* while the
  fingerprint attests to it.
* **``thinking`` is a uniform ``low``/``medium``/``high`` enum**, not a
  per-tier integer budget. ``reasoning_effort`` reaches every vendor (Anthropic
  → ``budget_tokens``, identically via Vertex; Gemini → ``thinkingConfig``;
  OpenAI → passthrough), so no per-vendor budget range is mirrored here.
  ``auto`` and ``off`` are excluded: ``auto`` raises on two vendors, and
  ``off`` is worse than unportable — ``gemini-2.5-pro`` + ``none`` *passes* the
  gate as ``thinkingBudget: 0`` and then 400s at request time.
* **``max_output_tokens`` is pinned**, because the default is vendor-dependent:
  Anthropic derives a 5,120–8,192 cap only when the caller is silent.

Loading fails closed (OWASP A02/A10): an unsupported version, an unknown key or
tier, an out-of-range value, or a ``candidate_count`` other than 1 raises
:class:`SamplingConfigError` rather than falling back to a library default — a
node quietly running on different sampling than the config records invalidates
every eval result taken against it.

The **value** check on ``thinking`` is ours, not the gate's: ``reasoning_effort
= "banana"`` passes ``get_optional_params`` on ``o3``, which would be another
silently-wrong param. A pydantic ``Literal`` catches it here.

``STRIDE_SAMPLING_{TIER}_{PARAM}`` env overrides retune the *offered* params at
deploy time, validated identically to the file value; an env var naming a
reserved or forbidden param raises, so the live-knob surface is exactly the
offered surface — no wider.
"""

from __future__ import annotations

import hashlib
import json
import os
import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal, Self

from google.genai import types
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from stride_service.errors import ConfigError
from stride_service.model_tiers import TIER_NAMES, TierName

# The only schema version this loader accepts. Versions are independent and
# exact-match across the four config files — a shared number would buy nothing
# once each file pins its own, since a stale file fails its own check.
SUPPORTED_VERSION = 3

# The uniform reasoning surface. Deliberately not per-vendor data: the enum is
# what every vendor accepts, and the wire value it becomes (Gemini derives a
# budget from it, so "low" is 1024 tokens rather than the config's own word) is
# LiteLLM's business, not this file's.
ReasoningEffort = Literal["low", "medium", "high"]

# Env override surface: only these params are overridable. A var naming any
# other param — reserved (``candidate_count``) or forbidden — raises.
_ENV_PREFIX = "STRIDE_SAMPLING_"
OFFERED_PARAMS: tuple[str, ...] = (
    "temperature",
    "top_p",
    "seed",
    "thinking",
    "max_output_tokens",
)


class SamplingConfigError(ConfigError):
    """The sampling configuration is invalid or unusable."""


class _RawTier(BaseModel):
    """One tier's decoding params exactly as written in the file / overrides.

    ``extra="forbid"`` rejects an unknown or misspelled key rather than
    silently ignoring it — which is also how a stray ``top_k`` line is caught
    rather than quietly dropped.

    No upper bound is placed on ``max_output_tokens``: the ceiling is a
    per-``(vendor, model)`` fact, and mirroring one here would be a table that
    drifts against the provider actually serving the request.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, gt=0.0, le=1.0)
    seed: int | None = None
    max_output_tokens: int | None = Field(default=None, ge=1)
    candidate_count: int = 1
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    thinking: ReasoningEffort | None = None

    @field_validator("candidate_count")
    @classmethod
    def _reserved_candidate_count(cls, value: int) -> int:
        # The Self-MoA lever: >1 silently drops candidates, so it is reserved,
        # not offered, and pinned at 1 until routed through the union/dedupe
        # path.
        if value != 1:
            raise ValueError(
                f"candidate_count must be 1 (reserved Self-MoA lever); got {value}"
            )
        return value


class _RawSampling(BaseModel):
    """The whole file's shape before per-tier resolution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int
    tiers: dict[TierName, _RawTier]


class TierSampling(BaseModel):
    """One tier's resolved decoding params, ready to bind onto a tier's adapter.

    Split three ways at the point of use, because ADK carries only some of it:

    * :meth:`constructor_kwargs` — ``seed`` and ``reasoning_effort``, which
      ADK's request map forwards *neither* of. Passing them on the
      generate-content config would mean the fingerprint attests to a seed the
      request never carried.
    * :meth:`to_generate_content_config` — the params ADK does forward.
    * :meth:`gate_params` — everything, for the build-time supported-param
      check.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    temperature: float | None = None
    top_p: float | None = None
    seed: int | None = None
    max_output_tokens: int | None = None
    candidate_count: int = 1
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    thinking: ReasoningEffort | None = None

    def constructor_kwargs(self) -> dict[str, Any]:
        """The params that must ride the ``LiteLlm`` constructor, not the config.

        ADK 2.5.0's request map (``lite_llm.py:2404-2419``) forwards neither
        ``seed`` nor any reasoning parameter, and fail-closed ``drop_params``
        cannot fail closed on what LiteLLM is never told. Constructor kwargs
        reach ``acompletion`` via ``_additional_args`` *before* ``generation_params``,
        so the value survives and the fingerprint stays honest.
        """
        kwargs: dict[str, Any] = {}
        if self.seed is not None:
            kwargs["seed"] = self.seed
        if self.thinking is not None:
            kwargs["reasoning_effort"] = self.thinking
        return kwargs

    def to_generate_content_config(self) -> types.GenerateContentConfig:
        """The ADK/GenAI object a node on this tier is configured with.

        An unset param is sent as ``None``, leaving the model's own default.
        ``seed`` and ``thinking`` are deliberately absent — see
        :meth:`constructor_kwargs`.
        """
        return types.GenerateContentConfig(
            temperature=self.temperature,
            top_p=self.top_p,
            max_output_tokens=self.max_output_tokens,
            candidate_count=self.candidate_count,
            presence_penalty=self.presence_penalty,
            frequency_penalty=self.frequency_penalty,
        )

    def gate_params(self) -> dict[str, Any]:
        """Every set param, named as LiteLLM names it, for the build-time gate.

        ``candidate_count`` is LiteLLM's ``n``. Unset params are omitted rather
        than sent as ``None``: the gate asks "would this provider accept this
        request", and a param the request will not carry is not part of it.
        """
        params: dict[str, Any] = {
            "temperature": self.temperature,
            "top_p": self.top_p,
            "seed": self.seed,
            "max_tokens": self.max_output_tokens,
            "n": self.candidate_count,
            "presence_penalty": self.presence_penalty,
            "frequency_penalty": self.frequency_penalty,
            "reasoning_effort": self.thinking,
        }
        return {name: value for name, value in params.items() if value is not None}


class SamplingConfig(BaseModel):
    """Validated per-tier sampling, keyed by tier."""

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
        """The resolved sampling params for one tier."""
        return self.tiers[tier]


SamplingResolver = Callable[[str], TierSampling]


def sampling_fingerprint(served_route: str, sampling: TierSampling) -> str:
    """One node execution's generation-identity hash: ``sha256(served route, sampling)``.

    Model and the resolved tier sampling are bound into **one** hash:
    certification is about a tier's *generation behaviour*, which is model and
    sampling jointly — splitting them lets a mismatched pair pass two green
    half-checks.

    ``served_route`` is the vendor prefix joined to the **served** build, e.g.
    ``vertex_ai/gemini-2.5-pro-002``. The served half comes from
    ``LlmResponse.model_version``, per node *execution* rather than from the
    configured string at build time. The vendor half is not decoration: a
    served identifier carries no vendor, and Vertex-hosted Claude and
    Anthropic-direct return through an identical transformation, so a
    served-only hash would let a manifest blessed on one silently certify the
    other.

    Canonical serialization — sorted keys over the resolved values plus the
    served route — so the hash is recomputable from the recorded clear block
    alone, never from some upstream state.
    """
    payload = {"model": served_route, **sampling.model_dump()}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def env_var_for(tier: TierName, param: str) -> str:
    """The env var that overrides one offered param on one tier."""
    return f"{_ENV_PREFIX}{tier.upper()}_{param.upper()}"


def make_resolve_sampling(
    config: SamplingConfig, resolve_tier: Callable[[str], TierName]
) -> SamplingResolver:
    """The injected sibling of ``resolve_model``: node -> its tier's sampling.

    ``resolve_tier`` is :meth:`ModelTierConfig.resolve_tier`, so the node -> tier
    walk lives once in the tier config and the graph stays purely node-centric.
    """

    def resolve_sampling(node: str) -> TierSampling:
        return config.for_tier(resolve_tier(node))

    return resolve_sampling


def _parse_env_value(var: str, param: str, value: str) -> float | int | str:
    """Parse an override string to its param's type; raise on a bad literal.

    Range and enum legality stay in :class:`_RawTier`, so an override is
    validated identically to a file value — this only converts the string to the
    type the file would hold.
    """
    if param in ("temperature", "top_p"):
        try:
            return float(value)
        except ValueError:
            raise SamplingConfigError(f"{var}: {value!r} is not a number") from None
    if param in ("seed", "max_output_tokens"):
        try:
            return int(value)
        except ValueError:
            raise SamplingConfigError(f"{var}: {value!r} is not an integer") from None
    # thinking: left as the raw string for _RawTier's Literal to accept or reject.
    return value


def _apply_env_overrides(tiers_raw: object, env: Mapping[str, str]) -> None:
    """Fold ``STRIDE_SAMPLING_{TIER}_{PARAM}`` overrides into the raw tables.

    Only the offered params are overridable; a var naming a reserved or
    forbidden param raises — the live-knob surface equals the offered surface,
    no wider. A set-but-empty override is a deploy mistake and raises rather
    than being silently ignored.
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

    Overrides are folded in before validation, so an out-of-range value arriving
    via the environment fails closed exactly like one in the file. Every failure
    path raises :class:`SamplingConfigError`.
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
            f" expected {SUPPORTED_VERSION}"
        )

    resolved = {
        tier: TierSampling(**raw_tier.model_dump())
        for tier, raw_tier in parsed.tiers.items()
    }
    try:
        return SamplingConfig(version=parsed.version, tiers=resolved)
    except ValidationError as exc:
        raise SamplingConfigError(f"{path}: {exc}") from exc
