"""Binding a tier's ``(vendor, model, sampling, resilience)`` to one adapter.

**One ``LiteLlm`` per tier**, shared by that tier's nodes — ten LLM nodes, two
adapters — owning every parameter ADK will not carry for us.

Three things ride the constructor rather than the generate-content config,
each for the same underlying reason — ADK's request map forwards them nowhere,
and a param LiteLLM is never told cannot be caught by its fail-closed
``drop_params``:

* **``seed``** and **``reasoning_effort``**. Put on the config instead, they
  would vanish silently while ``sampling_fingerprint`` went on attesting to a
  seed the request never carried.
* **``num_retries``**, pinned at **zero**. Not because retry is off — it is one
  layer up, in :mod:`stride_service.retry` — but because this is the kwarg that
  keeps the library's own retry layer, and the provider SDK's beneath it, down
  to exactly one request per call. Left at ``attempts - 1`` it multiplied to
  ``2 * attempts - 1`` requests per node, uncoordinated across the six category agents,
  which is the burst that turns one 429 into a storm. The adapters this module
  builds are :func:`~stride_service.retry.retrying_llm_class` subclasses sharing
  one process-wide budget.

Constructor kwargs reach ``acompletion`` via ``_additional_args`` *before*
``generation_params``, so the value survives. ``drop_params`` is never set —
neither here nor via ``LITELLM_DROP_PARAMS`` — because LiteLLM's default is
fail-closed and the sampling fingerprint's honesty depends on it.

Five build-time gates fire per tier, so a misconfiguration costs nothing rather
than dying on node one of a paid-for job:

* the **supported-param check** (:mod:`stride_service.model_gate`);
* the **output-ceiling check** below, which the supported-param check cannot
  make: every vendor *accepts* ``max_output_tokens``, and only the serving model
  objects to a value above what it will produce;
* the **removed-``temperature`` check** below, which covers that check's one
  documented blind spot on Claude;
* the **native-structured-output check** below. Every LLM node binds an
  ``output_schema``, and a model the provider library cannot constrain natively
  gets that constraint *emulated* — which sends an unresolved schema and fails
  at output validation mid-job, the one failure shape the other gates cannot
  see because both the request and the response are well-formed;
* the **credential check** (:meth:`Vendor.credential_kwargs`), which fires once
  per tier and fails closed under :class:`ProviderAuthError`.

No vendor is privileged: every model reaches its provider through ``LiteLlm``.
ADK emits a warning when a Gemini model is used through LiteLLM; that warning
is the visible cost of no privileged default, not a misconfiguration.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

# Imported before anything that could pull in ``litellm``: this module's import
# is what pins the model-cost map to the installed copy. See
# :func:`stride_service.model_gate._import_litellm_hermetically`.
from stride_service.model_gate import (
    ModelGateError,
    assert_kwarg_supported,
    check_supported,
    emulates_structured_output,
    output_ceiling,
)
from stride_service.model_tiers import (
    LLM_NODES,
    TIER_NAMES,
    ModelTierConfig,
    TierName,
)
from stride_service.resilience import ResilienceConfig
from stride_service.retry import retrying_llm_class
from stride_service.sampling import (
    SamplingConfig,
    SamplingResolver,
    TierSampling,
    make_resolve_sampling,
)
from stride_service.vendors import Vendor, claude_generation

if TYPE_CHECKING:
    # Both deliberately type-only. A runtime ``from stride_service.graph import``
    # here would sort *above* the ``model_gate`` import and break the ordering
    # the comment above depends on.
    from google.adk.models.lite_llm import LiteLlm

    from stride_service.graph import ModelResolver

# LiteLLM counts retries after the first try; ``ResilienceConfig`` counts total
# attempts. The name is asserted against LiteLLM's own parameter list at build
# time because it is accepted as ``**kwargs`` — a misspelling would otherwise be
# swallowed and silently revert retry to a single try.
_NUM_RETRIES_KWARG = "num_retries"

# ``constrain_output = false`` suppresses the schema by passing ``response_format``
# as an explicit ``None`` constructor kwarg, which ADK applies over the one it
# derived from the node's output_schema.
#
# Deliberately **not** run through ``assert_kwarg_supported``: that asks
# LiteLLM's ``all_litellm_params``, which is its own extra-kwarg registry —
# ``num_retries`` is in it, ``response_format`` is not, because the latter is an
# OpenAI-spec parameter on the completion signature rather than a LiteLLM
# addition. Asserting it there fails on a correct name, which is worse than not
# asserting: the misspelling risk is covered instead by the test that reads the
# suppressed value back off the built adapter.

# Anthropic removed ``temperature`` from Claude 4.7 onward: only the model's own
# default is accepted and a request carrying the param is rejected. LiteLLM
# knows this, and ``check_supported`` does catch it — but only for models
# already in the pinned copy's model-cost map. A Claude released after that copy
# falls back to the provider's base config and passes, which is the residual
# ``model_gate`` documents ("not a de-facto existence check"). Since the shipped
# sampling pins ``temperature = 0.0``, that residual is not theoretical: it is
# every Anthropic deployment naming a model newer than the pin, dying on node
# one of a paid-for job.
#
# Deliberately a **generation floor, not a support table**. Decision #12 removed
# the per-``(vendor, model)`` sampling set from the registry because mirroring
# what LiteLLM computes forks a subsystem that drifts; a floor does not fork it,
# and when LiteLLM's map catches up this check becomes redundant rather than
# contradictory. 4.6 still accepts ``temperature`` and is deliberately left
# alone, so the floor is 4.7 rather than the service's own 4.6 minimum.
_TEMPERATURE_REMOVED_FROM = (4, 7)


def _check_temperature_unset(
    model: str, sampling: TierSampling, source: str
) -> None:
    """Fail closed when a Claude generation that removed ``temperature`` is sent one.

    Keyed on the **model**, not the vendor: Vertex-hosted Claude is the same
    model under the same removal, so a vendor-keyed rule would pass exactly the
    configuration it exists to stop. A non-Claude model parses to ``None`` and
    is left entirely to :func:`check_supported`.
    """
    generation = claude_generation(model)
    if sampling.temperature is None or generation is None:
        return
    if generation >= _TEMPERATURE_REMOVED_FROM:
        removed = ".".join(str(part) for part in _TEMPERATURE_REMOVED_FROM)
        raise ModelGateError(
            f"{source}: Claude {removed} and later do not accept 'temperature',"
            f" and {model!r} would reject the request; remove the temperature"
            " line for this tier in config/sampling.toml. Unsetting it leaves"
            " the model's own default, which is the only value these"
            " generations serve."
        )


def _check_output_ceiling(
    vendor: Vendor, model: str, sampling: TierSampling, source: str
) -> None:
    """Fail closed when a tier asks for more output than its model will serve.

    ``max_output_tokens`` is pinned rather than left to a vendor-derived
    default, and the value that fits a tier's job is a property of the *job* —
    the critic rules on every draft in one pass — while the ceiling is a
    property of the model. The two are set independently and there is no reason
    they agree, so the pair is checked here: every provider accepts the
    parameter, and an over-ceiling value is rejected by the serving model at
    request time, which is node one of a paid-for job.

    An unrecognised model yields no ceiling and is not gated, the same
    open-world residual :func:`check_supported` documents.
    """
    if sampling.max_output_tokens is None:
        return
    ceiling = output_ceiling(vendor, model)
    if ceiling is None or sampling.max_output_tokens <= ceiling:
        return
    raise ModelGateError(
        f"{source}: {vendor.name} serves at most {ceiling} output tokens for"
        f" {model!r}, and this tier asks for {sampling.max_output_tokens}."
        " Lower max_output_tokens for this tier in config/sampling.toml, or"
        " select a model whose ceiling covers it — a tier that asks for more"
        " than the model will produce is rejected on its first request."
    )


def _check_native_structured_output(
    vendor: Vendor, model: str, sampling: TierSampling, source: str
) -> None:
    """Fail closed when a tier's model would get *emulated* schema constraint.

    A tier whose model falls to LiteLLM's synthesised-tool path is not merely
    taking a different route to the same place — it sends a ``$defs``-bearing
    schema the provider will not resolve, and the node fails on the response it
    validates rather than on the request it made. That is the most expensive
    shape a failure can take here: it survives the build, survives the request,
    and dies at output validation on node one.

    Scoped to tiers that actually send a schema. A tier running
    ``constrain_output = false`` sends none, so how the provider *would* have
    constrained one is not a fact about anything that happens — checking it
    there would reject a configuration on the strength of a request it never
    makes.

    Checked per tier at build time for the same reason as everything else in
    this module — a misconfiguration should cost nothing.
    """
    if not sampling.constrain_output:
        return
    if emulates_structured_output(vendor, model):
        raise ModelGateError(
            f"{source}: {vendor.name} cannot constrain {model!r} to a schema"
            " natively, so the provider library would emulate it with a"
            " synthesised tool and send the graph's schema with its $defs"
            " unresolved. Every LLM node binds an output schema, so this fails"
            " at output validation mid-job rather than here. Choose a model"
            " whose provider supports schema-constrained output directly."
        )


def build_tier_adapters(
    tiers: ModelTierConfig,
    sampling: SamplingConfig,
    resilience: ResilienceConfig,
    env: Mapping[str, str] | None = None,
) -> dict[TierName, LiteLlm]:
    """One configured ``LiteLlm`` per tier, or fail closed before any call.

    Raises :class:`~stride_service.model_gate.ModelGateError` if a tier's
    sampling is unsupported by its ``(vendor, model)`` — whether LiteLLM says so
    or :func:`_check_temperature_unset` does — and
    :class:`~stride_service.vendors.ProviderAuthError` if its credentials are
    missing.
    """
    if env is None:
        env = os.environ

    # Deferred so that importing this module never costs the ADK LiteLLM import
    # for callers that only want the helpers; by this point ``model_gate`` has
    # already pinned the cost map, so the ordering guarantee holds either way.
    from google.adk.models.lite_llm import LiteLlm

    assert_kwarg_supported(_NUM_RETRIES_KWARG)

    # One policy, so one budget, shared by both tiers and every node on them.
    # A per-tier budget would let the six category agents storm the strong tier while
    # the base tier's untouched allowance sat beside it; a storm is a property
    # of the process, not of a tier. Capacity is one retry per LLM node in the
    # graph — what a single job may spend from a cold bucket.
    policy = resilience.retry_policy(budget_capacity=len(LLM_NODES))
    retrying = retrying_llm_class(LiteLlm, policy)

    adapters: dict[TierName, LiteLlm] = {}
    for tier in TIER_NAMES:
        selection = tiers.tiers[tier]
        vendor = selection.vendor_entry
        tier_sampling = sampling.for_tier(tier)
        source = f"tiers.{tier}"

        check_supported(
            vendor, selection.model, tier_sampling.gate_params(), source=source
        )
        _check_temperature_unset(selection.model, tier_sampling, source)
        _check_output_ceiling(vendor, selection.model, tier_sampling, source)
        _check_native_structured_output(vendor, selection.model, tier_sampling, source)
        adapters[tier] = retrying(
            model=selection.route,
            # Zero, and not because retry is off: it is one layer up, in
            # ``stride_service.retry``. This kwarg is what keeps the library's
            # own layer — and the provider SDK's, which it sets from this same
            # value — down to exactly one request per call, so ``attempts``
            # means requests instead of half of a product with them.
            **{_NUM_RETRIES_KWARG: 0},
            **tier_sampling.constructor_kwargs(),
            **vendor.credential_kwargs(env),
        )
    return adapters


def make_resolve_model(
    adapters: Mapping[TierName, LiteLlm], tiers: ModelTierConfig
):
    """Node -> the adapter for its tier, the ``ModelResolver`` the graph wants.

    The node -> tier walk stays in the tier config, so this never re-derives it.
    Nodes on one tier share an adapter instance by design: the credential and
    supported-param checks then fire once per tier rather than once per node.
    """

    def resolve_model(node: str) -> LiteLlm:
        return adapters[tiers.resolve_tier(node)]

    return resolve_model


@dataclass(frozen=True)
class NodeBinding:
    """Everything the graph binds onto an LLM node, as one value.

    ``resolve_sampling`` and ``tier_sampling`` are *views of the same object*:
    the first hands each node its tier's decoding params, the second is the
    clear block the report records for those same tiers. Sourced from different
    :class:`~stride_service.sampling.SamplingConfig` objects they would
    disagree silently — every node running on one config while the report
    attested to another, leaving each ``sampling_fingerprint`` unverifiable
    against the block shipped beside it, which is precisely what the
    fingerprint exists to make impossible. :meth:`from_configs` derives both
    from one config, so that disagreement is not a bug to catch but a state
    that cannot be written down.

    ``resolve_model`` stays a caller-supplied callable rather than being derived
    here: an offline test binds scripted models through it, and taking that away
    would mean the graph could only be built by something holding credentials.
    """

    resolve_model: ModelResolver
    resolve_sampling: SamplingResolver
    tier_sampling: dict[TierName, TierSampling]
    resilience: ResilienceConfig | None = None

    @classmethod
    def from_configs(
        cls,
        tiers: ModelTierConfig,
        sampling: SamplingConfig,
        resolve_model: ModelResolver,
        resilience: ResilienceConfig | None = None,
    ) -> Self:
        """Bind one tier config and one sampling config onto the graph's nodes.

        The node -> tier walk comes from ``tiers`` and is not re-derived; both
        sampling views come from ``sampling`` and cannot disagree.
        """
        return cls(
            resolve_model=resolve_model,
            resolve_sampling=make_resolve_sampling(sampling, tiers.resolve_tier),
            tier_sampling=dict(sampling.tiers),
            resilience=resilience,
        )
