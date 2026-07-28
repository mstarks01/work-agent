"""The build-time supported-param gate: ask LiteLLM, don't mirror it.

#6 decision 3 established that an unsupported sampling param must fail the
*build*, not the first request: LiteLLM's ``drop_params`` default is fail-closed,
so an unsupported param otherwise raises mid-job after earlier nodes have
already been paid for. #12 then falsified the mechanism that decision assumed —
a ``frozenset`` of supported names per vendor — on two counts:

* supportedness is a function of ``(vendor, model)``, not vendor. ``vertex_ai/``
  is not one provider: LiteLLM dispatches on the model-string prefix to four
  config classes, so ``vertex_ai/claude-*`` (no ``seed``, it subclasses
  ``AnthropicConfig``) and ``vertex_ai/gemini-*`` (``seed`` fine) disagree under
  a single ``vertex`` entry;
* set membership cannot express a *value* constraint, and o-series
  ``temperature`` is constrained to exactly ``1``.

So the check survives and its implementation does not (#13). The gate is a
**call**, not a table: ``litellm.utils.get_optional_params`` run for raise /
no-raise with its output discarded. That entry point — rather than
``get_supported_openai_params``, which is merely the gate's input — runs both
``_check_valid_arg`` *and* ``_map_openai_params``, so the o-series value
constraint is caught by the same call.

Two things the gate provably cannot do, both handled elsewhere:

* ``top_k`` is absent from its signature entirely, so #15 decision 3 removed
  ``top_k`` from the config surface rather than leaving it silently unchecked;
* ``reasoning_effort="banana"`` *passes* on ``o3``, so the enum's value check is
  a pydantic ``Literal`` in :mod:`stride_service.sampling`.

It is also **not** a de-facto existence check: an unrecognised model falls back
to the provider's base config rather than raising, which is consistent with #7
decision 6 declining a build-time existence check. The narrow residual is that a
future o-series under an unrecognised naming pattern would pass the build.

``get_optional_params`` is not a documented public API. It is exercised
directly by this module's tests, which is what makes a ``litellm`` version bump
a change that must re-run the probe rather than a silent dependency bump.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from stride_service.vendors import Vendor


class ModelGateError(ValueError):
    """A tier's ``(vendor, model, sampling)`` combination cannot be requested."""


def _import_litellm_hermetically() -> Any:
    """Import ``litellm`` with its model-cost map pinned to the installed copy.

    ``litellm`` fetches that map from ``BerriAI/litellm@main`` **at import**, and
    the map backs the gate's own conditionals — so pinning the package version
    alone pins nothing, and the gate's verdict would depend on a network fetch
    made at process start. Setting ``LITELLM_LOCAL_MODEL_COST_MAP`` first removes
    both the nondeterminism and an unrequested startup egress (OWASP A02/A08).

    The variable only takes effect before the first import, so an already-imported
    ``litellm`` means it arrived too late — that fails closed rather than
    proceeding against a map of unknown provenance.
    """
    if "litellm" in sys.modules:
        raise ModelGateError(
            "litellm was imported before its local model-cost map was pinned;"
            " stride_service.model_gate must be imported first"
        )
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    import litellm

    return litellm


_litellm = _import_litellm_hermetically()


def supports_structured_output(vendor: Vendor, model: str) -> bool:
    """Whether this ``(vendor, model)`` can be constrained to a response schema.

    Separate from :func:`check_supported` because it is not a raise/no-raise
    question: ``response_format`` is an accepted *parameter* on every provider,
    but only some models honour it as a schema rather than as a hint. A caller
    that depends on parsing structured output needs the stronger fact.
    """
    return bool(
        _litellm.utils.supports_response_schema(
            model=model, custom_llm_provider=vendor.litellm_provider
        )
    )


def completion(**kwargs: Any) -> Any:
    """Issue one request through the same pinned ``litellm`` the gate checks.

    A thin passthrough, and the point is the *sameness*: a gate that validates
    against one copy of the library while the request goes out through another
    proves nothing. Everything that issues a request goes through here, so the
    hermetic model-cost map and the exact version pin cover the call as well as
    the check.

    Deliberately not wrapped in retry or error translation — callers own their
    own failure semantics, and ``num_retries`` rides the kwargs like any other
    provider parameter.
    """
    return _litellm.completion(**kwargs)


def assert_kwarg_supported(name: str) -> None:
    """Fail closed if LiteLLM does not recognise a constructor kwarg by that name.

    ADK's ``LiteLlm`` takes ``**kwargs`` and forwards them verbatim, so a
    misspelled parameter is not an error anywhere — it is simply carried along
    and ignored. For ``num_retries`` that would silently revert retry to a single
    try, which is precisely the failure this project added retry to prevent, and
    nothing downstream would show it.
    """
    if name not in _litellm.utils.all_litellm_params:
        raise ModelGateError(
            f"{name!r} is not a litellm parameter; it would be forwarded and"
            " ignored rather than taking effect"
        )


def check_supported(
    vendor: Vendor, model: str, params: dict[str, Any], source: str
) -> None:
    """Raise if this provider would reject these sampling params for this model.

    ``source`` names the tier whose config is being checked so the error points
    at the knob to turn. The return value of the underlying call is deliberately
    discarded: the gate is the raise, not the mapped parameter set.
    """
    try:
        _litellm.utils.get_optional_params(
            model=model,
            custom_llm_provider=vendor.litellm_provider,
            **params,
        )
    except Exception as exc:
        raise ModelGateError(
            f"{source}: {vendor.name} cannot serve {model!r} with the configured"
            f" sampling — {exc}"
        ) from exc
