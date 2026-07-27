"""PROTOTYPE — throwaway. Answers wayfinder ticket #6, not production code.

Question being prototyped
-------------------------
How does the registry select, **per tier** (``base``/``strong``), a
``(vendor, model)`` binding and construct **one** ``LiteLlm`` for each tier's
nodes — so that the two tiers may run different vendors at once, each carries
its own retry/timeout, sampling stays on the shared ``resolve_sampling`` path,
and an unconfigured or unknown vendor fails closed?

Supersedes the ``STRIDE_MODEL_PROVIDER`` prototype in ``d0b4aa2``, which bound
one provider to the *whole* pipeline and kept a privileged Gemini default.

The seam proposed here
----------------------
A vendor is a **registry fact**, not a config block::

    Vendor(prefix, credential_mode, reasoning_kwarg)

Config names only ``(vendor, model)`` per tier — ticket #9 settled that auth is
*derived* from the vendor, so ``vertex + api_key`` is unrepresentable rather
than validated against. ``build_tier_bindings`` turns the two config entries
into exactly **two** ``LiteLlmPlan`` objects, and ``resolve_model(node)`` hands
every node on a tier the *same* plan — one adapter instance per tier, so the
credential check fires once per tier at build time, not once per node.

What this prototype exists to make visible
------------------------------------------
Three things fell out of reading the installed ``google-adk==2.5.0`` source
(``google/adk/models/lite_llm.py``) that the ticket's framing did not assume.
They are modelled here as *derived state* so they can be driven by hand.

1. **Two offered sampling params silently vanish.** ADK's request-side map
   (``_get_completion_inputs``, lines 2404-2419) forwards only temperature,
   max_output_tokens, top_p, top_k, stop_sequences, presence_penalty and
   frequency_penalty. ``seed`` and ``thinking_budget`` are **never passed to
   LiteLLM at all** — so ``drop_params`` cannot fail closed on them, because
   LiteLLM never sees them. The sampling fingerprint would attest to a seed the
   request never carried.

2. **Retry does not survive the move, and then over-shoots.** ADK *does* honour
   ``http_options`` (lines 2763-2783): timeout becomes LiteLLM ``timeout``, and
   ``http_options.retry_options.attempts`` becomes ``num_retries`` **verbatim**.
   But ``ResilienceConfig.to_http_options()`` sets timeout *only* — retry rides
   the ``Gemini`` constructor today — so under LiteLlm-sole retry silently drops
   to a single attempt. Put ``retry_options`` on ``http_options`` and the
   semantics invert: genai ``attempts`` is a **total**, LiteLLM ``num_retries``
   is **extra**, so ``attempts=3`` becomes 4 tries.

3. **Every fingerprint changes even where nothing changed.** The LiteLLM router
   prefix is part of the model string ``_model_name`` records, so an unmoved
   Gemini tier re-hashes under ``vertex_ai/gemini-2.5-pro``.

Stand-in note
-------------
Nothing here imports ``google.adk`` or constructs a real ``LiteLlm`` (litellm
isn't installed — see the research notes). Each binding is a ``LiteLlmPlan``
describing the constructor call that *would* be made; fields map 1:1 onto the
real one. No credential value is ever read, stored or rendered — only the name
of the variable and whether it is set.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

TierName = Literal["base", "strong"]
TIER_NAMES: tuple[TierName, ...] = ("base", "strong")

# The graph's LLM nodes (real ``model_tiers.LLM_NODES``), under the base/strong
# rename settled by ticket #5.
STRIDE_CATEGORIES = (
    "spoofing",
    "tampering",
    "repudiation",
    "information-disclosure",
    "denial-of-service",
    "elevation-of-privilege",
)
NODE_TIERS: dict[str, TierName] = {
    "extract": "base",
    "repair": "base",
    **{f"analyst/{category}": "strong" for category in STRIDE_CATEGORIES},
    "critic": "strong",
    "recritic": "strong",
}


class ProviderConfigError(ValueError):
    """Unknown vendor, or a tier with no usable ``(vendor, model)``."""


class ProviderAuthError(ValueError):
    """A vendor's credential is absent at build time (ticket #9).

    Distinct from ``auth.AuthConfigError``, which is *inbound* bearer auth.
    """


# --- The registry ----------------------------------------------------------


@dataclass(frozen=True)
class Vendor:
    """One registry entry. The credential mode is a *fact*, never configured.

    ``supported`` is the sampling surface this vendor accepts, in *genai* names.
    It exists because ``drop_params`` stays fail-closed (ticket #8): a param the
    vendor does not take raises at **request** time, mid-job, after the earlier
    nodes are already paid for. Checking it at build time turns that into a
    service that refuses to start.
    """

    prefix: str
    credential: Literal["adc", "api_key"]
    reasoning_kwarg: str | None
    supported: frozenset[str]

    def api_key_var(self, name: str) -> str | None:
        """The env var this vendor's key lives in, or None for ADC vendors."""
        if self.credential != "api_key":
            return None
        return f"STRIDE_{name.upper()}_API_KEY"


# Only the *anthropic* set is verified — read off
# ``litellm/llms/anthropic/chat/transformation.py::get_supported_openai_params``
# on BerriAI/litellm ``main`` (2026-07-27): no seed, no top_k, no penalties.
# The vertex and openai sets are from vendor docs and want the same treatment
# before anything is built on them.
_ALL_PARAMS = frozenset(
    {
        "temperature",
        "top_p",
        "top_k",
        "seed",
        "max_output_tokens",
        "presence_penalty",
        "frequency_penalty",
        "thinking_budget",
    }
)

_REGISTRY: dict[str, Vendor] = {
    "vertex": Vendor("vertex_ai/", "adc", "thinking", _ALL_PARAMS),
    "anthropic": Vendor(
        "anthropic/",
        "api_key",
        "thinking",
        frozenset({"temperature", "top_p", "max_output_tokens", "thinking_budget"}),
    ),
    "openai": Vendor("openai/", "api_key", "reasoning_effort", _ALL_PARAMS - {"top_k"}),
}


def known_vendors() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


# --- Config + resilience stand-ins -----------------------------------------


@dataclass(frozen=True)
class TierProvider:
    """One tier's config entry. No auth field, by ticket #9."""

    vendor: str
    model: str


@dataclass(frozen=True)
class Resilience:
    """Stand-in for ResilienceConfig: the two decided knobs."""

    attempts: int  # TOTAL attempts (genai semantics)
    timeout_ms: int


@dataclass(frozen=True)
class TierSampling:
    """Stand-in for the real ``TierSampling`` — same fields, plain scalars."""

    temperature: float | None = 0.0
    top_p: float | None = None
    top_k: float | None = None
    seed: int | None = None
    max_output_tokens: int | None = None
    candidate_count: int = 1
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    thinking_budget: int | None = None


# What ADK 2.5.0 actually forwards to LiteLLM, read off ``_get_completion_inputs``
# (lite_llm.py:2404-2419). Anything absent here never reaches the wire.
_ADK_LITELLM_PARAMS = {
    "temperature": "temperature",
    "max_output_tokens": "max_completion_tokens",
    "top_p": "top_p",
    "top_k": "top_k",
    "stop_sequences": "stop",
    "presence_penalty": "presence_penalty",
    "frequency_penalty": "frequency_penalty",
}


# --- The plan a binding produces -------------------------------------------


@dataclass(frozen=True)
class LiteLlmPlan:
    """The ``LiteLlm(...)`` call that would be constructed for ONE tier.

    ``api_key_var`` names the variable the key was read from; the value itself
    is never carried here, so it cannot reach a log, the report, or a hash.
    """

    tier: str
    vendor: str
    model: str  # prefixed — this is what ``_model_name`` would record
    pinned_model: str  # what the config file names, unprefixed
    credential: str
    api_key_var: str | None
    reasoning: tuple[str, object] | None
    timeout_ms: int
    num_retries: int | None
    # Sampling params ADK does not forward, re-passed as constructor kwargs so
    # they reach the wire after all: {genai name: (litellm kwarg, value)}.
    rescued: dict[str, tuple[str, object]] = field(default_factory=dict)
    # Deliberately NOT set: drop_params / additional_drop_params (ticket #8).
    extra_kwargs: dict[str, object] = field(default_factory=dict)

    @property
    def effective_attempts(self) -> int:
        """Tries LiteLLM would actually make, in genai's 'total' vocabulary."""
        if self.num_retries is None:
            return 1
        return self.num_retries + 1


# --- Building the bindings -------------------------------------------------


def build_tier_bindings(
    providers: Mapping[str, TierProvider],
    sampling: Mapping[str, TierSampling],
    resilience: Resilience,
    env: Mapping[str, str],
    *,
    retry_position: str,
    rescue_unforwarded: bool,
    check_vendor_support: bool,
) -> dict[str, LiteLlmPlan]:
    """One ``LiteLlmPlan`` per tier. Fails closed on vendor and on credential.

    The last three arguments are the *open questions*, not settings — each is a
    branch this ticket has to choose:

    ``retry_position``
        Where retry is expressed. ``"none"`` models today's
        ``ResilienceConfig.to_http_options()`` (timeout only) — retry vanishes.
        ``"http_options"`` models adding ``retry_options`` to it, which ADK
        passes to LiteLLM verbatim and so over-shoots by one. ``"constructor"``
        passes ``num_retries`` as a kwarg with the ``attempts - 1`` arithmetic
        done here, which is the only position that preserves genai's *total*
        attempts semantics.
    ``rescue_unforwarded``
        Whether the binding re-passes ``seed`` / ``thinking_budget`` as
        constructor kwargs, since ADK forwards neither.
    ``check_vendor_support``
        Whether a param the vendor cannot take fails at build time rather than
        on the first request of a paid-for job.
    """
    missing = [tier for tier in TIER_NAMES if tier not in providers]
    if missing:
        raise ProviderConfigError(f"tiers missing a provider: {missing}")
    return {
        tier: _bind_tier(
            tier,
            providers[tier],
            sampling[tier],
            resilience,
            env,
            retry_position=retry_position,
            rescue_unforwarded=rescue_unforwarded,
            check_vendor_support=check_vendor_support,
        )
        for tier in TIER_NAMES
    }


def _bind_tier(
    tier: str,
    provider: TierProvider,
    sampling: TierSampling,
    resilience: Resilience,
    env: Mapping[str, str],
    *,
    retry_position: str,
    rescue_unforwarded: bool,
    check_vendor_support: bool,
) -> LiteLlmPlan:
    """Resolve one tier's vendor, verify its credential, plan the adapter."""
    try:
        vendor = _REGISTRY[provider.vendor]
    except KeyError:
        known = ", ".join(known_vendors())
        raise ProviderConfigError(
            f"tiers.{tier}: unknown vendor {provider.vendor!r};"
            f" known vendors: {known}"
        ) from None
    if not provider.model.strip():
        raise ProviderConfigError(f"tiers.{tier}: no model named")

    api_key_var = vendor.api_key_var(provider.vendor)
    if api_key_var is not None and not env.get(api_key_var, "").strip():
        # Names the variable, never the value.
        raise ProviderAuthError(
            f"tiers.{tier}: vendor {provider.vendor!r} needs {api_key_var}"
        )

    if check_vendor_support:
        unsupported = sorted(
            name
            for name, value in _set_params(sampling)
            if name not in vendor.supported
        )
        if unsupported:
            raise ProviderConfigError(
                f"tiers.{tier}: vendor {provider.vendor!r} does not accept"
                f" {', '.join(unsupported)} — drop_params is fail-closed, so"
                " this would raise on the first request instead"
            )

    reasoning = None
    if sampling.thinking_budget is not None and vendor.reasoning_kwarg:
        reasoning = (vendor.reasoning_kwarg, sampling.thinking_budget)

    rescued: dict[str, tuple[str, object]] = {}
    if rescue_unforwarded:
        # ADK merges constructor kwargs into acompletion via _additional_args
        # BEFORE generation_params (lite_llm.py:2749, 2761), and neither of
        # these is in generation_params — so a constructor value survives.
        if sampling.seed is not None:
            rescued["seed"] = ("seed", sampling.seed)
        if reasoning is not None:
            rescued["thinking_budget"] = reasoning

    return LiteLlmPlan(
        tier=tier,
        vendor=provider.vendor,
        model=f"{vendor.prefix}{provider.model}",
        pinned_model=provider.model,
        credential=vendor.credential,
        api_key_var=api_key_var,
        reasoning=reasoning,
        timeout_ms=resilience.timeout_ms,
        num_retries=_num_retries(retry_position, resilience),
        rescued=rescued,
    )


def _num_retries(position: str, resilience: Resilience) -> int | None:
    """LiteLLM ``num_retries`` for each candidate position of the retry knob.

    genai counts ``attempts`` as a **total**; LiteLLM counts ``num_retries`` as
    **extra**. ADK bridges them by passing ``attempts`` straight through
    (lite_llm.py:2777-2780), so only the constructor position — where the repo
    does the arithmetic itself — reproduces the configured number of tries.
    """
    if position == "none":
        return None
    if position == "http_options":
        return resilience.attempts
    if position == "constructor":
        return max(resilience.attempts - 1, 0)
    raise ProviderConfigError(f"unknown retry position: {position!r}")


def _set_params(sampling: TierSampling) -> list[tuple[str, object]]:
    """The tier's params that actually carry a value.

    ``candidate_count`` is excluded: it is fail-closed at 1, so it is never a
    thing the vendor is being asked to honour.
    """
    return [
        (name, value)
        for name, value in vars(sampling).items()
        if value is not None and name != "candidate_count"
    ]


def resolve_model(bindings: Mapping[str, LiteLlmPlan], node: str) -> LiteLlmPlan:
    """Every node on a tier gets that tier's ONE plan — same object, by identity."""
    try:
        tier = NODE_TIERS[node]
    except KeyError:
        raise ProviderConfigError(f"unknown LLM node: {node!r}") from None
    return bindings[tier]


# --- The hazards, as pure derivations --------------------------------------


def wire_sampling(
    sampling: TierSampling, plan: LiteLlmPlan
) -> tuple[dict[str, object], list[str]]:
    """What actually reaches LiteLLM, and which set params are lost en route.

    The second element is the finding: a param the fingerprint attests to but
    that never reaches the wire. ``drop_params`` cannot catch these — ADK never
    forwards them, so LiteLLM is never told about them. A rescued param *is* on
    the wire, just via the constructor rather than the request config.
    """
    sent: dict[str, object] = {}
    lost: list[str] = []
    for name, value in _set_params(sampling):
        mapped = _ADK_LITELLM_PARAMS.get(name)
        if mapped is not None:
            sent[mapped] = value
        elif name in plan.rescued:
            kwarg, rescued_value = plan.rescued[name]
            sent[kwarg] = rescued_value
        else:
            lost.append(name)
    return sent, lost


def fingerprint(served_model: str, sampling: TierSampling) -> str:
    """The real ``sampling_fingerprint`` shape: sha256(served model, sampling)."""
    payload = {"model": served_model, **vars(sampling)}
    blob = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()
