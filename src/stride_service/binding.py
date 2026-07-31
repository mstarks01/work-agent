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
* **``num_retries``**, at ``attempts - 1``. See
  :meth:`ResilienceConfig.to_num_retries` for why the arithmetic is explicit.

Constructor kwargs reach ``acompletion`` via ``_additional_args`` *before*
``generation_params``, so the value survives. ``drop_params`` is never set —
neither here nor via ``LITELLM_DROP_PARAMS`` — because LiteLLM's default is
fail-closed and the sampling fingerprint's honesty depends on it.

Two build-time gates fire per tier, so a misconfiguration costs nothing rather
than dying on node one of a paid-for job:

* the **supported-param check** (:mod:`stride_service.model_gate`);
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
from stride_service.model_gate import assert_kwarg_supported, check_supported
from stride_service.model_tiers import TIER_NAMES, ModelTierConfig, TierName
from stride_service.resilience import ResilienceConfig
from stride_service.sampling import (
    SamplingConfig,
    SamplingResolver,
    TierSampling,
    make_resolve_sampling,
)

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


def build_tier_adapters(
    tiers: ModelTierConfig,
    sampling: SamplingConfig,
    resilience: ResilienceConfig,
    env: Mapping[str, str] | None = None,
) -> dict[TierName, LiteLlm]:
    """One configured ``LiteLlm`` per tier, or fail closed before any call.

    Raises :class:`~stride_service.model_gate.ModelGateError` if a tier's
    sampling is unsupported by its ``(vendor, model)``, and
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

    adapters: dict[TierName, LiteLlm] = {}
    for tier in TIER_NAMES:
        selection = tiers.tiers[tier]
        vendor = selection.vendor_entry
        tier_sampling = sampling.for_tier(tier)
        source = f"tiers.{tier}"

        check_supported(
            vendor, selection.model, tier_sampling.gate_params(), source=source
        )
        adapters[tier] = LiteLlm(
            model=selection.route,
            **{_NUM_RETRIES_KWARG: resilience.to_num_retries()},
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
