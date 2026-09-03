"""Per-tier sampling configuration for the graph's LLM nodes.

Decoding parameters are pinned per tier in a versioned TOML file, and eval and
production read it from the same place. ``base`` and ``strong`` carry their own
params. The node-to-tier map lives once in ``model_tiers.toml``, and this module
reuses it through :meth:`~analysis_service.model_tiers.ModelTierConfig.resolve_tier` rather than duplicating
it.

Four things about the surface:

* ``top_k`` is not on it. It is absent from
  ``litellm.utils.get_optional_params``'s signature, so it is the one param the
  build-time gate cannot cover: LiteLLM re-injects it raw into the request body
  after the check. A wrong value would then be silent, while the fingerprint
  went on attesting to it.
* ``thinking`` is a uniform ``low``/``medium``/``high`` enum rather than a
  per-tier integer budget. ``reasoning_effort`` reaches every vendor — Anthropic
  through adaptive ``thinking`` plus ``output_config.effort``, identically
  through Vertex; Gemini through ``thinkingConfig``; OpenAI by passthrough — so
  no per-vendor budget range is mirrored here. ``auto`` and ``off`` are
  excluded. ``auto`` raises on two vendors. ``off`` is worse than unportable:
  ``gemini-2.5-pro`` with ``none`` passes the gate as ``thinkingBudget: 0`` and
  then returns a 400 at request time.
* ``max_output_tokens`` is pinned, because the default is vendor-dependent.
  Anthropic derives a 5,120 to 8,192 cap only when the caller is silent. The
  file pins it per tier, at a value sized against measured output: the tiers
  emit different things, and the strong tier rules on every draft in one pass
  and reasons against the same cap, so it needs several times what one
  extraction does. Undersizing it does not truncate visibly. The completion
  returns no text, the node writes no output key, and the next FunctionNode
  fails to bind. ``binding.py`` checks each tier's value against its model's
  published ceiling, which :func:`~analysis_service.model_gate.check_supported`
  cannot: every provider accepts the param, and only the serving model objects
  to the value.
* ``constrain_output`` decides whether the node's schema is sent at all.
  Constrained generation is the default and the better answer where a provider
  will take it. Whether a provider will take it is a property of the schema and
  of the provider's own limits, because a grammar compiler can reject a schema
  it would otherwise honour, and nothing computes that offline. It is therefore
  a per-tier choice a deployment makes rather than one derived from the vendor.
  Every LLM node carries a schema the adapter can convert, so this field is the
  only thing that decides whether one is sent.

  Turning it off is not currently a working configuration. An earlier version of
  this note claimed it gives up constrained generation only, and that was wrong.
  Measured against Claude Sonnet 4.6 with the extraction schema suppressed, the
  model fenced its JSON in a ```` ```json ```` block, which ADK hands to
  validation unstripped, so it fails before anything reads it. The model also
  omitted required fields, including every ``trust_boundaries[*].kind``. The
  repair node sits on the same tier and is equally unconstrained, so the repair
  loop does not rescue it. The field stays because the mechanism is right, but a
  tier that turns it off needs the graph to tolerate a fenced response first.

Loading fails closed (OWASP A02 and A10). An unsupported version, an unknown key
or tier, an out-of-range value, or a ``candidate_count`` other than 1 raises
:class:`SamplingConfigError`, rather than falling back to a library default. A
node that quietly runs on different sampling from what the config records
invalidates every eval result taken against it.

The value check on ``thinking`` is this module's rather than the gate's.
``reasoning_effort = "banana"`` passes ``get_optional_params`` on ``o3``, which
would be another silently wrong param. A pydantic ``Literal`` catches it here.

``ANALYSIS_SAMPLING_{TIER}_{PARAM}`` env overrides retune the offered params at
deploy time, and the service validates them exactly as it validates a file
value. An env var that names a reserved or forbidden param raises, so the live
knob surface is exactly the offered surface and no wider.
"""

from __future__ import annotations

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

from analysis_service.errors import ConfigError
from analysis_service.model_tiers import TIER_NAMES, TierName

# The only schema version this loader accepts. Versions are independent and
# exact-match across the four config files — a shared number would buy nothing
# once each file pins its own, since a stale file fails its own check.
#
# Version 5 adds the ``review`` tier's block. Every tier is required, so a
# version-4 file is short one and fails its own check — which is the point: a
# file that could omit a tier is one where moving criticism onto ``review``
# silently runs it on values nobody chose.
#
# Version 4 added ``constrain_output``, which enters the execution identity and
# therefore re-baselines every blessed number.
SUPPORTED_VERSION = 5

# The uniform reasoning surface. Deliberately not per-vendor data: the enum is
# what every vendor accepts, and the wire value it becomes (Gemini derives a
# budget from it, so "low" is 1024 tokens rather than the config's own word) is
# LiteLLM's business, not this file's.
ReasoningEffort = Literal["low", "medium", "high"]

# Env override surface: only these params are overridable. A var naming any
# other param — reserved (``candidate_count``) or forbidden — raises.
_ENV_PREFIX = "ANALYSIS_SAMPLING_"
OFFERED_PARAMS: tuple[str, ...] = (
    "temperature",
    "top_p",
    "seed",
    "thinking",
    "max_output_tokens",
    "constrain_output",
)

# The only two strings ``constrain_output`` accepts from the environment.
# Python's own truthiness would read "false" as True, which is the classic way
# a deployment ends up running the opposite of what its config says.
_BOOL_LITERALS = {"true": True, "false": False}


class SamplingConfigError(ConfigError):
    """The sampling configuration is invalid or unusable."""


class _RawTier(BaseModel):
    """One tier's decoding params exactly as written in the file / overrides.

    ``extra="forbid"`` rejects an unknown or misspelled key rather than
    silently ignoring it — which is also how a stray ``top_k`` line is caught
    rather than quietly dropped.

    No upper bound is placed on ``max_output_tokens``: the ceiling is a
    per-``(vendor, model)`` fact, and mirroring one here would be a table that
    drifts against the provider actually serving the request. It is enforced
    where the pair is known — :func:`analysis_service.binding._check_output_ceiling`
    asks the model map at build time — so "unbounded here" means unbounded by
    the *loader*, not unchecked.
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
    constrain_output: bool = True

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
    constrain_output: bool = True

    def constructor_kwargs(self) -> dict[str, Any]:
        """The params that must ride the ``LiteLlm`` constructor, not the config.

        ADK 2.5.0's request map (``lite_llm.py:2404-2419``) forwards neither
        ``seed`` nor any reasoning parameter, and fail-closed ``drop_params``
        cannot fail closed on what LiteLLM is never told. Constructor kwargs
        reach ``acompletion`` via ``_additional_args`` *before* ``generation_params``,
        so the value survives and the fingerprint stays honest.

        ``constrain_output = false`` rides the same seam for the opposite
        reason. ADK derives ``response_format`` from the node's
        ``output_schema`` and then applies ``_additional_args`` *over* it
        (``lite_llm.py:2744-2750``), so an explicit ``None`` here is what
        suppresses the schema on the wire. The node keeps its
        ``output_schema``, so the response is still validated on arrival —
        what is given up is constrained *generation*, not the check.
        """
        kwargs: dict[str, Any] = {}
        if self.seed is not None:
            kwargs["seed"] = self.seed
        if self.thinking is not None:
            kwargs["reasoning_effort"] = self.thinking
        if not self.constrain_output:
            kwargs["response_format"] = None
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


def env_var_for(tier: TierName, param: str) -> str:
    """The env var that overrides one offered param on one tier."""
    return f"{_ENV_PREFIX}{tier.upper()}_{param.upper()}"


def make_resolve_sampling(
    config: SamplingConfig, resolve_tier: Callable[[str], TierName]
) -> SamplingResolver:
    """The injected sibling of ``resolve_model``: node -> its tier's sampling.

    ``resolve_tier`` is :meth:`~analysis_service.model_tiers.ModelTierConfig.resolve_tier`, so the node -> tier
    walk lives once in the tier config and the graph stays purely node-centric.
    """

    def resolve_sampling(node: str) -> TierSampling:
        return config.for_tier(resolve_tier(node))

    return resolve_sampling


def _parse_env_value(var: str, param: str, value: str) -> float | int | bool | str:
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
    if param == "constrain_output":
        parsed = _BOOL_LITERALS.get(value.lower())
        if parsed is None:
            expected = ", ".join(sorted(_BOOL_LITERALS))
            raise SamplingConfigError(f"{var}: {value!r} is not one of {expected}")
        return parsed
    # thinking: left as the raw string for _RawTier's Literal to accept or reject.
    return value


def _apply_env_overrides(tiers_raw: object, env: Mapping[str, str]) -> None:
    """Fold ``ANALYSIS_SAMPLING_{TIER}_{PARAM}`` overrides into the raw tables.

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
