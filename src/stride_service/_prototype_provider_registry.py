"""PROTOTYPE — throwaway. Answers wayfinder ticket #6, not production code.

Question being prototyped
-------------------------
What does the ``build_model_resolver`` **registry seam** look like — the thing
that replaces the hardwired ``resilient_resolver`` (``pipeline.py:238``) so a
non-Gemini vendor can be selected by ``STRIDE_MODEL_PROVIDER`` without touching
``build_pipeline`` / ``AdkPipelineRunner``?

The load-bearing tension this prototype exists to make concrete: **resilience
is expressed in a different place per provider.** Gemini takes retries via its
*constructor* (``Gemini(retry_options=...)``) and the timeout via the
per-request ``http_options``; LiteLlm (the researched first non-Gemini vendor,
Claude on Vertex) takes *both* retry and timeout via ``http_options``
(``attempts -> num_retries``). See ``docs/research/adk-nongemini-adapters.md``.

The seam proposed here
----------------------
A provider is a **factory** keyed by name:

    ProviderFactory = Callable[[Tiers, Resilience], ProviderBinding]

and each factory returns a ``ProviderBinding`` of exactly two things:

    * ``resolve_model``  — node name -> the model to bind (a pinned string, or
                           a ready wrapper). This is today's ``ModelResolver``.
    * ``http_options``   — the per-request HTTP options EVERY node carries.

That second field is the whole point. Today ``graph.build_pipeline`` takes a
``ResilienceConfig`` and calls ``resilience.to_http_options()`` itself (timeout
only), because it *assumes Gemini* carries retry in the constructor. Under this
seam ``build_pipeline`` stops knowing about resilience at all: it just binds the
``http_options`` the provider handed it. So each provider folds resilience into
ADK *in its own terms*, behind the factory:

    * Gemini  -> resolve_model wraps ``Gemini(model, retry_options=...)``;
                 http_options carries **timeout only**  (byte-identical to today)
    * Claude  -> resolve_model wraps ``LiteLlm(model="vertex_ai/claude-...")``;
                 http_options carries **timeout + retry** (attempts->num_retries)

Unknown / unset ``STRIDE_MODEL_PROVIDER`` fails closed to the Gemini default;
an unknown *explicit* value raises, matching the repo's fail-closed config rule.

Stand-in note
-------------
This module does not import ``google.adk`` or construct real ``Gemini`` /
``LiteLlm`` objects (litellm isn't even installed — see the research note). It
represents each binding as a ``*Plan`` dataclass describing the class + kwargs
that WOULD be constructed, so the seam's shape can be driven by hand with zero
deps. The plan fields map 1:1 onto the real constructors.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field


class ModelConfigError(ValueError):
    """Unknown or misconfigured provider (mirrors the real fail-closed error)."""


# --- Stand-ins for the real config objects ---------------------------------
# The real seam takes ModelTierConfig and ResilienceConfig; these tiny shapes
# carry just the fields the factories actually read.


@dataclass(frozen=True)
class Tiers:
    """Stand-in for ModelTierConfig: node name -> pinned model string."""

    by_node: Mapping[str, str]

    def resolve_model(self, node: str) -> str:
        try:
            return self.by_node[node]
        except KeyError:
            raise ModelConfigError(f"unknown LLM node: {node!r}") from None


@dataclass(frozen=True)
class Resilience:
    """Stand-in for ResilienceConfig: the two decided knobs."""

    attempts: int  # TOTAL attempts (genai semantics)
    timeout_ms: int


# --- The plans a binding produces (stand in for real ADK objects) ----------


@dataclass(frozen=True)
class ModelPlan:
    """What resolve_model(node) would hand build_pipeline for one node.

    ``wrapper`` is the BaseLlm subclass name; ``kwargs`` its constructor args.
    ``model`` is the pinned string the report records (graph._model_name).
    """

    wrapper: str
    model: str
    kwargs: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class HttpOptionsPlan:
    """What every node's GenerateContentConfig.http_options would carry."""

    timeout_ms: int
    num_retries: int | None = None  # None => retry not expressed here


@dataclass(frozen=True)
class ProviderBinding:
    """The seam: how ONE provider binds models and expresses resilience."""

    resolve_model: Callable[[str], ModelPlan]
    http_options: HttpOptionsPlan


# --- The provider factories ------------------------------------------------


def _gemini_factory(tiers: Tiers, resilience: Resilience) -> ProviderBinding:
    """Default provider. Byte-identical to today's resilient_resolver.

    Retry rides the Gemini constructor; http_options carries timeout only.
    """

    def resolve_model(node: str) -> ModelPlan:
        model = tiers.resolve_model(node)
        return ModelPlan(
            wrapper="Gemini",
            model=model,
            # genai HttpRetryOptions(attempts=<total>) via the constructor.
            kwargs={"retry_options": f"HttpRetryOptions(attempts={resilience.attempts})"},
        )

    return ProviderBinding(
        resolve_model=resolve_model,
        http_options=HttpOptionsPlan(timeout_ms=resilience.timeout_ms),
    )


def _claude_vertex_factory(tiers: Tiers, resilience: Resilience) -> ProviderBinding:
    """Anthropic Claude on Vertex via LiteLlm (the researched first vendor).

    Retry and timeout BOTH ride http_options; the wrapper takes neither.
    ``attempts`` (total) reconciles to LiteLLM ``num_retries`` (extra) as
    ``attempts - 1`` — the off-by-one the research note calls out.
    """

    def resolve_model(node: str) -> ModelPlan:
        # Reuse the same pinned strings, prefixed for LiteLLM's Vertex router.
        model = f"vertex_ai/{tiers.resolve_model(node)}"
        return ModelPlan(wrapper="LiteLlm", model=model, kwargs={})

    return ProviderBinding(
        resolve_model=resolve_model,
        http_options=HttpOptionsPlan(
            timeout_ms=resilience.timeout_ms,
            num_retries=max(resilience.attempts - 1, 0),
        ),
    )


# --- The registry + the seam entry point -----------------------------------

ProviderFactory = Callable[[Tiers, Resilience], ProviderBinding]

_REGISTRY: dict[str, ProviderFactory] = {
    "gemini": _gemini_factory,
    "claude-vertex": _claude_vertex_factory,
}

DEFAULT_PROVIDER = "gemini"


def build_model_resolver(
    provider: str | None, tiers: Tiers, resilience: Resilience
) -> ProviderBinding:
    """Look up the provider factory and build its binding. Fail closed.

    ``provider`` is ``STRIDE_MODEL_PROVIDER``. Unset (``None``/empty) picks the
    Gemini default; an unknown explicit value raises rather than degrading.
    """
    key = (provider or DEFAULT_PROVIDER).strip()
    if not key:
        key = DEFAULT_PROVIDER
    try:
        factory = _REGISTRY[key]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY))
        raise ModelConfigError(
            f"STRIDE_MODEL_PROVIDER={provider!r} is not a known provider"
            f" (known: {known})"
        ) from None
    return factory(tiers, resilience)


def known_providers() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))
