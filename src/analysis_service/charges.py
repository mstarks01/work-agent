"""What a provider says it charged, carried out of the response it arrives in.

A cost has two sources, and they answer different questions. The token
arithmetic in ``evals/harness/prices.py`` multiplies counted tokens by published
rates: it is reproducible, it works for every vendor, and it is the only answer
where a vendor states nothing. A **reported charge** is what the provider says
the account was charged for this call, and it is the only answer where no
published rate describes the route — an aggregator's slug reaches many endpoints
at many rates, so there is no rate to multiply by.

OpenRouter is the vendor that states one. litellm asks for it on every request
and keeps it in ``_hidden_params``, ADK reads the token counters beside it and
drops the rest, and nothing downstream ever saw it. This module is the path from
that field to :attr:`analysis_service.report.NodeRun.reported_charge_usd`.

**Three seams, because the value and the record are never in the same hand.**

* The client subclass is the only code that holds litellm's own response, so it
  is where the figure is read and judged. It publishes what may be recorded
  into a :class:`~contextvars.ContextVar`.
* The adapter subclass is the only code that holds the response ADK builds, so
  it is where the figure is stamped. It clears the variable before each call, so
  a figure captured for one adapter can never be stamped onto another's
  response.
* :func:`recordable_charge` is the judgement: it refuses a declaration the
  provider contradicts, and then asks :func:`records_reported_charge` whether
  the declared arrangement covers the whole call. ``analysis_service.binding``
  installs the client wherever a vendor reports a charge, and hands it the
  arrangement that tier declared.

A ``ContextVar`` rather than an attribute on the client: one adapter is shared by
every node on its tier, and those nodes run concurrently. Each concurrent node
runs in its own :class:`asyncio.Task`, which copies the context, so a value set
inside one node's call is invisible to the others.

**The provider is asked whether the declaration is true, never what it should
be.** OpenRouter states ``is_byok`` in the same ``usage`` block it states the
charge in. A flag that disagrees with the declared arrangement stops the run,
because one of the two is wrong about every figure the run would record. A flag
that agrees, or is absent, changes nothing — a figure worth recording only
where a provider also describes its own charge would rest on that description.

Measured live on 2026-09-11, and recorded in
`docs/research/openrouter-reported-charge.md`: one call through this path
reported ``2.54e-06``, litellm filed it where this module reads, and the
executor stamped that same figure on the node. OpenRouter's own generation
record for the call said ``is_byok: false``, ``total_cost`` equal to it and no
separate upstream charge — which is what ``ChargeMode.DIRECT`` claims.

**A figure that covers part of a call is not recorded.** Under a provider key of
the operator's own, OpenRouter reports the routing fee it charged and the
upstream provider charges the operator elsewhere, so the reported figure is
about a twentieth of the spend. Recording it would put a number on the record
that reads as a cost and is not one, and the arithmetic it displaced was at
least honest about what it counted.
"""

from __future__ import annotations

import contextvars
import logging
import math
from collections.abc import Mapping
from typing import Any

from analysis_service.errors import ConfigError
from analysis_service.vendors import ChargeMode, Vendor

logger = logging.getLogger(__name__)


class ChargeModeMismatchError(ConfigError):
    """The provider says it charged under an arrangement nobody declared.

    A configuration error, and it fails closed for the reason every other one
    does: the declaration decides what a recorded figure means, so a wrong one
    does not spoil a single number — it spoils every number the run records.
    Under the arrangement this deployment did not declare, the figure is either
    a twentieth of what a call cost or a whole charge being discarded, and
    neither is something to note and carry on from.

    It costs one node's tokens to find out, and the message names the key to
    change. That is the same trade
    :func:`analysis_service.retry._reject_truncated` makes: fail the node rather
    than keep what it produced.

    Not transient, so the retry driver gives up at once rather than paying for
    the same contradiction three times.
    """


#: Which arrangement a provider's own flag names. A table rather than a branch,
#: so an arrangement added to :class:`~analysis_service.vendors.ChargeMode` is
#: not judged by a reader that knows two — a missing key states nothing rather
#: than guessing.
_STATED_ARRANGEMENT: Mapping[bool, ChargeMode] = {
    True: ChargeMode.OWN_UPSTREAM_KEY,
    False: ChargeMode.DIRECT,
}

#: The provider's own name for the flag. OpenRouter states it in the ``usage``
#: block of every completion, beside the charge, and again in its generation
#: record. litellm reads ``cost`` out of that block and keeps the rest of it
#: verbatim, so the flag arrives as an attribute where it arrives at all
#: (measured on 1.97.0; see `docs/research/openrouter-reported-charge.md`).
_BYOK_FIELD = "is_byok"

#: The ``custom_metadata`` key under which a response carries the charge its
#: provider reported, in USD. ``analysis_service.execution`` reads it off the
#: event by this name, exactly as it reads ``attempts``.
CHARGE_METADATA_KEY = "reported_charge_usd"

#: Where litellm files a charge a provider reported. Its OpenRouter config sets
#: ``usage.include`` on every request and copies ``usage.cost`` out of the
#: response body into this header slot, in the ``transform_response`` of its
#: OpenRouter chat config (measured on litellm 1.97.0). It is a pinned
#: dependency's
#: internal spelling, which is why :func:`reported_charge_of` treats every part
#: of the path as absent-able rather than asserting the shape.
_COST_HEADER = "llm_provider-x-litellm-response-cost"

_reported: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "reported_charge", default=None
)


def records_reported_charge(vendor: Vendor, mode: ChargeMode | None) -> bool:
    """Whether a charge this vendor reports is what one of its calls cost.

    The one reader of the rule, asked by :func:`recordable_charge` for every
    response that carries a figure. Three ways to answer no, and the last is
    the one this exists for:

    * the vendor reports no charge, so there is nothing to record;
    * no arrangement is declared, which for a vendor that reports a charge the
      loader has already refused — the check is here as well because this
      function is also reachable from a hand-built configuration;
    * the declared arrangement says the reported figure covers part of the call.

    ``mode`` comes from
    :meth:`analysis_service.model_tiers.ModelTierConfig.charge_mode`, which is
    the deployment's declaration rather than a guess from the response.
    """
    if not vendor.reports_charge or mode is None:
        return False
    report = vendor.charges.get(mode)
    return report is not None and report.covers_whole_call


def stated_arrangement_of(response: Any) -> ChargeMode | None:
    """The arrangement the provider says this call ran under, if it says.

    ``None`` where nothing says: a provider that states no flag, a flag whose
    value is not a real boolean, and every vendor that reports no charge at all.
    Absence is not a contradiction — it is the ordinary case, and it leaves the
    declaration standing.

    ``isinstance(..., bool)`` and not a truth test, because the lookup below is
    a dict keyed by ``True`` and ``False``: in Python ``1`` and ``0`` are equal
    to those keys, so an integer flag would silently name an arrangement the
    provider never stated.
    """
    usage = getattr(response, "usage", None)
    stated = getattr(usage, _BYOK_FIELD, None)
    if not isinstance(stated, bool):
        return None
    return _STATED_ARRANGEMENT.get(stated)


def recordable_charge(
    vendor: Vendor, mode: ChargeMode | None, response: Any
) -> float | None:
    """The figure this response may contribute to the record, or ``None``.

    **The one reader of what a response is worth as money**, and it asks two
    questions in order. First, does the provider contradict the declaration —
    because a figure recorded under the wrong arrangement is worse than no
    figure, and the contradiction is about the arrangement rather than about
    the number, so it is checked even where no charge arrived. Second, does the
    declared arrangement say the figure covers the whole call.

    The provider's statement **contradicts** a declaration and never supplies
    one. A deployment that declared nothing is not corrected into an
    arrangement by a vendor's own flag: the mechanism is declared, and this is
    the material.
    """
    stated = stated_arrangement_of(response)
    if stated is not None and mode is not None and stated != mode:
        raise ChargeModeMismatchError(
            f"charges.{vendor.name} declares {mode.value!r}, and {vendor.name!r}"
            f" says this call ran under {stated.value!r}. What a reported charge"
            " covers depends on which one is true, so the run stops rather than"
            " record a figure that means the other. Correct the declaration in"
            " config/model_tiers.toml, or point the deployment at the account"
            " it describes."
        )
    if not records_reported_charge(vendor, mode):
        return None
    return reported_charge_of(response)


def reported_charge_of(response: Any) -> float | None:
    """The charge litellm read off ``response``, or ``None`` where it read none.

    Every step of the path can be absent and two of them can be the wrong shape,
    so each is checked rather than assumed: a streamed wrapper carries no
    ``_hidden_params`` at all, a provider that reports nothing carries the
    mapping without the key, and the value has been a string in litellm's header
    dictionaries before it was a float here.

    A value that is negative, infinite or not a number is refused and logged. A
    charge of exactly zero is kept: a cached or free-tier call really can cost
    nothing, and turning that into "unreported" would hide a measurement.
    """
    hidden = getattr(response, "_hidden_params", None)
    if not isinstance(hidden, Mapping):
        return None
    headers = hidden.get("additional_headers")
    if not isinstance(headers, Mapping):
        return None
    raw = headers.get(_COST_HEADER)
    if raw is None:
        return None
    try:
        charge = float(raw)
    except (TypeError, ValueError):
        logger.warning("provider reported a charge that is not a number; ignoring it")
        return None
    if not math.isfinite(charge) or charge < 0:
        logger.warning("provider reported a charge outside the plausible range")
        return None
    return charge


def charge_capturing_client_class(client_cls: type) -> type:
    """A ``LiteLLMClient`` subclass that reads what each call's provider said.

    Takes the class rather than importing it, for the reason
    :func:`analysis_service.retry.retrying_llm_class` gives: this module stays
    free of the provider libraries at import time, and ``binding`` already holds
    the class.

    Installed on the adapters of **every** vendor that reports a charge, not
    only where the figure is recordable. A deployment that declares the wrong
    arrangement is wrong in both directions — one records a fee as a cost, the
    other discards a whole charge — and a client installed only for the first
    could never see the second. :func:`recordable_charge` is what decides which
    figure survives, and it raises where the provider contradicts the
    declaration.

    Each instance carries the vendor and the arrangement its tier declared,
    because one process can bind two tiers to two vendors and a class shared by
    both cannot answer for either.
    """

    class ChargeCapturingClient(client_cls):
        """One tier's client, reading what its provider said about money."""

        def __init__(self, vendor: Vendor, mode: ChargeMode | None) -> None:
            super().__init__()
            self.vendor = vendor
            self.mode = mode

        async def acompletion(self, *args: Any, **kwargs: Any) -> Any:
            response = await super().acompletion(*args, **kwargs)
            _reported.set(recordable_charge(self.vendor, self.mode, response))
            return response

    return ChargeCapturingClient


def charge_reporting_llm_class(litellm_cls: type) -> type:
    """A ``LiteLlm`` subclass that stamps the reported charge onto its responses.

    Built for **every** bound tier, whether or not its vendor reports a charge,
    because clearing the variable is what keeps one tier's figure off another
    tier's response. Two adapters can run in one task, and a value left behind
    by the first would otherwise be read as the second's.

    **One call's charge is stamped once.** The variable is cleared before the
    call and again as soon as a response carries the figure, so a call that
    yields more than one response cannot put the same charge on two of them —
    which a caller summing the rows would read as twice the money. Every node
    here binds an output schema and so never streams, which is what makes one
    response the ordinary case rather than the guaranteed one.
    """

    class ChargeReportingLlm(litellm_cls):
        """One tier's adapter, carrying a reported charge to the record."""

        async def generate_content_async(self, llm_request, stream: bool = False):
            _reported.set(None)
            async for response in super().generate_content_async(llm_request, stream):
                charge = _reported.get()
                if charge is not None:
                    _reported.set(None)
                    response.custom_metadata = {
                        **(response.custom_metadata or {}),
                        CHARGE_METADATA_KEY: charge,
                    }
                yield response

    return ChargeReportingLlm
