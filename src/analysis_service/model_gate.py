"""The build-time supported-param gate: ask LiteLLM, do not mirror it.

An unsupported sampling param must fail the build rather than the first request.
LiteLLM's ``drop_params`` default is fail-closed, so an unsupported param
otherwise raises mid-job, after earlier nodes have already been paid for.

The gate is a call rather than a table. It runs
``litellm.utils.get_optional_params`` for raise or no-raise, and discards its
output. A table cannot express what the answer depends on. Supportedness is a
function of ``(vendor, model)`` rather than of vendor, because ``vertex_ai/`` is
not one provider: LiteLLM dispatches on the model-string prefix to four config
classes, so ``vertex_ai/claude-*`` and ``vertex_ai/gemini-*`` disagree. The
first has no ``seed``, because it subclasses ``AnthropicConfig``, and the second
takes one. Some constraints are on a value instead, such as o-series
``temperature`` having to be exactly ``1``. That entry point runs both
``_check_valid_arg`` and ``_map_openai_params``, so one call catches the value
constraints too. ``get_supported_openai_params`` is merely the gate's input.

Two things the gate cannot do, both handled elsewhere:

* ``top_k`` is absent from its signature entirely, which is why ``top_k`` is not
  part of the sampling config surface at all;
* ``reasoning_effort="banana"`` passes on ``o3``, so the enum's value check is a
  pydantic ``Literal`` in :mod:`analysis_service.sampling`.

It is also not a de-facto existence check. An unrecognised model falls back to
the provider's base config rather than raising. The narrow residual is that a
future o-series under an unrecognised naming pattern would pass the build.

``get_optional_params`` is not a documented public API. This module's tests
exercise it directly, which is what makes a ``litellm`` version bump a change
that must re-run the probe, rather than a silent dependency bump.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from typing import Any, Literal

from analysis_service.errors import ConfigError
from analysis_service.offline import refuse_live_inference
from analysis_service.vendors import SchemaRule, Vendor


class ModelGateError(ConfigError):
    """A tier's ``(vendor, model, sampling)`` combination cannot be requested."""


#: Every ``LITELLM_LOCAL_*`` switch litellm defines, set together rather than
#: chosen between. Each one turns off a remote config fetch. One of them guards
#: the Anthropic beta headers, which litellm fetches at request time from its
#: own URL. Whoever serves that JSON chooses the outgoing ``anthropic-beta``
#: header, and the only integrity check is a non-empty dict with a known
#: provider key.
#:
#: Setting a switch for a feature this service does not use costs nothing, so
#: no switch is left to a judgement of which ones matter. ``test_model_gate``
#: compares this tuple against litellm's own source, so a bump that adds another
#: fails the offline suite rather than opening a quiet egress.
LITELLM_LOCAL_SWITCHES = (
    "LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS",
    "LITELLM_LOCAL_AUTOROUTER_PRESETS",
    "LITELLM_LOCAL_BLOG_POSTS",
    "LITELLM_LOCAL_MODEL_COST_MAP",
    "LITELLM_LOCAL_POLICY_TEMPLATES",
)


def _import_litellm_hermetically() -> Any:
    """Import ``litellm`` with its model-cost map pinned to the installed copy.

    ``litellm`` fetches remote config in more than one place, and this pins every
    one of them. The model-cost map is the first: it is fetched from
    ``BerriAI/litellm@main`` **at import**, and
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
            " analysis_service.model_gate must be imported first"
        )
    for switch in LITELLM_LOCAL_SWITCHES:
        os.environ[switch] = "True"
    # The set is plural, and the names do not rhyme. The Anthropic beta
    # headers, for one, come from a different URL behind a different variable,
    # at request time rather than at import. Whoever serves that JSON chooses
    # the outgoing `anthropic-beta` header verbatim, and litellm's only
    # integrity check is that the response is a non-empty dict with a known
    # provider key: no signature and no digest.
    #
    # A version bump must re-check this list against litellm's own source. It
    # is not derivable from the pin, which is the whole reason the pin is not
    # enough.
    import litellm

    return litellm


_litellm = _import_litellm_hermetically()


def library_sends_no_native_schema(vendor: Vendor, model: str) -> bool:
    """Whether the pinned library will not put a schema on the wire for this pair.

    **The probe's own answer, with no map lookup in it**, and the one reader of
    it. Two shapes mean the same thing to a caller, and
    :func:`emulates_structured_output` spells them differently:

    * it returns ``True`` where LiteLLM would satisfy the constraint with a
      synthesised tool, so the model is asked to follow the schema and nothing
      makes it;
    * it *raises* where LiteLLM will not map ``response_format`` at all — 79 of
      the pinned map's rows under a registered prefix, Bedrock's Cohere text
      models among them. A schema does not reach a model that refuses the
      parameter carrying it.

    **Narrowed, because the set was measured.** Swept over every row of the
    pinned map under a registered prefix, plus an unmapped probe per vendor,
    this call raises ``UnsupportedParamsError`` and nothing else — 80 times.
    So a different exception is a fact nobody here has met, and it propagates
    rather than being read as a refusal. That is the opposite of
    :func:`model_info` one function down, where litellm raises ``Exception``
    itself and there is no type to match on; the two look alike and are not the
    same case.

    Both are facts about what the installed library does with a request, which
    is why they belong together and why this is what a **gate** reads.
    :func:`native_structured_output` reads it too and then consults the map,
    because a report also has to say "the map does not know". A gate must not
    consult the map: ``supports_response_schema`` is a claim in a data file, and
    #819 measured one that was wrong.
    """
    return schema_support(vendor, model) != "native"


#: How the pinned library would carry a response schema for one pair.
SchemaSupport = Literal["native", "emulated", "refused"]


def schema_support(vendor: Vendor, model: str) -> SchemaSupport:
    """Whether the pinned library sends a schema natively, emulates it, or refuses it.

    The one reader of the probe. ``emulated`` is LiteLLM's synthesised tool;
    ``refused`` is a model that does not take ``response_format`` at all, which
    the call reports by raising ``UnsupportedParamsError``.
    """
    try:
        return "emulated" if emulates_structured_output(vendor, model) else "native"
    except _litellm.exceptions.UnsupportedParamsError:
        return "refused"


def native_structured_output(vendor: Vendor, model: str) -> bool | None:
    """Whether a response schema reaches this model natively, or ``None`` if unknown.

    The tri-state a boolean cannot express. ``None``
    means the pinned map carries an entry for this pair and that entry says
    nothing about response schemas — 2029 of its entries are silent, against
    881 that say yes and 72 that say no.

    **A report may not turn that silence into a no.** LiteLLM's lookup returns
    ``False`` for a silent entry, so a matrix built on it printed
    ``unsupported`` for ``openrouter/anthropic/claude-sonnet-4.6`` — a pair that
    honours a schema when asked. Measured live on 2026-09-11: the request
    carried ``response_format`` and the reply parsed as JSON against it. The map
    had simply not caught up with the slug.

    Emulation is checked first and is definitive. Where LiteLLM would satisfy
    the constraint with a synthesised tool, the schema does not reach the model
    natively whatever the map claims, and
    :func:`~analysis_service.ladder.rungs_for` leaves the native rung off that
    tier's ladder on the same fact.

    The map's own key is read rather than LiteLLM's lookup, because the lookup
    is where the two answers were collapsed. An unmapped pair yields ``None``
    here for the same reason :func:`output_ceiling` yields ``None``: nobody
    knows, and saying so is the open-world residual this module reports rather
    than hides.

    **A model that refuses ``response_format`` outright is a third shape, and
    it raises.** 33 of the 445 mapped models under a registered prefix do —
    Bedrock's Cohere text models among them — because
    :func:`emulates_structured_output` asks what LiteLLM would map the param
    to, and for those it maps nothing and raises ``UnsupportedParamsError``.
    That is a definitive ``False``: a schema does not reach a model that will
    not take the parameter carrying it. It is caught here rather than left to
    the caller, because the caller is a *report* and a matrix that raises
    answers nothing at all.
    """
    if library_sends_no_native_schema(vendor, model):
        return False
    info = model_info(vendor, model)
    if info is None:
        return None
    supported = info.get("supports_response_schema")
    return supported if isinstance(supported, bool) else None


# A minimal schema-constrained request, used only to ask LiteLLM *which way* it
# would satisfy the constraint. The schema's shape is deliberately irrelevant:
# the branch is keyed on the model, not on what is being asked for, so a
# two-field object answers the same question the graph's real schemas would and
# keeps this module from importing them.
_SCHEMA_PROBE: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "probe",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
    },
}


def model_info(vendor: Vendor, model: str) -> dict[str, Any] | None:
    """LiteLLM's map entry for this ``(vendor, model)``, or ``None`` if unmapped.

    The one place the open-world residual is *observable* rather than merely
    documented. Everything else in this module answers a supportedness question
    by asking LiteLLM and reading the answer, and for an unmapped model LiteLLM
    answers from the provider's base config instead of saying it does not know.
    That fallback is right for a gate — refusing to run a model the map has not
    caught up with is worse than letting it through — and wrong for a *report*,
    which must be able to distinguish "this provider does not support it" from
    "nobody here knows". :mod:`analysis_service.conformance` is that reader.

    Returned whole rather than as a bool, since the only two callers both want a
    field out of it and a second lookup would ask LiteLLM the same question
    twice.

    LiteLLM answers an unmapped model in two shapes. It raises
    ``ModelNotMappedError``, or, where a family rule matches the name, it
    returns an entry built from that rule, with no price and a ``key`` that is
    not in ``litellm.model_cost``. Both are unmapped here, so a report never
    calls a guessed entry known.

    A third shape is an internal LiteLLM error while it builds the entry. It
    raises a bare ``Exception`` that carries the not-mapped message, and that
    error propagates. It is a fault in the map or in LiteLLM, not a model
    nobody knows, so it fails closed rather than reading as unmapped.
    """
    try:
        info = _litellm.get_model_info(
            model=model, custom_llm_provider=vendor.litellm_provider
        )
    except _litellm.exceptions.ModelNotMappedError:
        return None
    return info if info["key"] in _litellm.model_cost else None


#: Each :data:`~analysis_service.vendors.SchemaRule`, as the function that
#: applies it.
#:
#: ``bounds_described`` is the pinned litellm's own rule for Claude's
#: constrained decoding, which litellm applies on the direct Anthropic path. One
#: function for every vendor that serves Claude keeps the bound text identical,
#: and no second copy of its labels exists here. It is idempotent, so the direct
#: path, where litellm applies it again, sends what it sent before.
SCHEMA_RULES: dict[SchemaRule, Callable[[Mapping[str, Any]], dict[str, Any]]] = {
    "as_built": dict,
    "bounds_described": _litellm.AnthropicConfig.filter_anthropic_output_schema,
}


def output_ceiling(vendor: Vendor, model: str) -> int | None:
    """The most output tokens this ``(vendor, model)`` will serve, if known.

    Asked of LiteLLM's model-cost map rather than mirrored in a table here, for
    the reason :mod:`analysis_service.sampling`'s raw tier gives for not bounding
    ``max_output_tokens`` at load time: the ceiling is a per-``(vendor, model)``
    fact, and a copy of it drifts against the provider actually serving the
    request.

    Deliberately **not** part of :func:`check_supported`. That gate is a
    raise/no-raise question about whether a param can be *sent*, and
    ``max_output_tokens`` above the ceiling sends perfectly well — every vendor
    accepts the parameter, and only the serving model objects, at request time,
    on node one of a paid-for job.

    ``None`` where the map has no entry, which is the same open-world residual
    :func:`check_supported` carries: an unrecognised model is not gated, because
    the alternative is refusing to run a model the map has not caught up with.
    """
    info = model_info(vendor, model)
    if info is None:
        return None
    ceiling = info.get("max_output_tokens")
    return ceiling if isinstance(ceiling, int) else None


def emulates_structured_output(vendor: Vendor, model: str) -> bool:
    """Whether schema constraint reaches this model *emulated*, not natively.

    Some providers constrain output to a schema directly; where a model is not
    known to support that, LiteLLM falls back to synthesising a single tool
    whose input schema is the response schema and forcing a call to it. The two
    are not equivalent, and the difference is silent at build time. The native
    path constrains decoding, so the response matches the schema. The emulated
    path only asks: the model usually follows the tool's input schema, and
    nothing makes it. A response that does not match fails the node's own
    output validation after the call is paid for.

    ``$defs`` is not the difference. Under the pinned litellm, the direct
    Anthropic path resolves ``$ref``/``$defs`` before it sends, but the Bedrock
    native path sends them as-is, and Bedrock documents internal references as
    supported. The emulated path sends them as-is too. How often a model
    breaks the schema on the emulated path is not measured here; the gate
    refuses it because no guarantee exists.

    Asked as a **call**, like :func:`check_supported`, and detected by its
    signature rather than by mirroring which models are on which path: the
    presence of LiteLLM's own internal response-format tool in the mapped
    params. A table of supported models here would be the thing this module
    exists not to be, and would drift the moment a model is added upstream.

    Deliberately **not** the question "is a schema honoured at all", which
    litellm's own ``supports_response_schema`` answers. That one says ``True``
    for models on both paths, so it cannot see this difference — and it says
    ``False`` for a silent map entry too, which is why nothing in this service
    reads it directly.
    """
    params = _litellm.utils.get_optional_params(
        model=model,
        custom_llm_provider=vendor.litellm_provider,
        response_format=_SCHEMA_PROBE,
    )
    tools = params.get("tools") or []
    return any(
        _tool_name(tool) == _litellm.constants.RESPONSE_FORMAT_TOOL_NAME
        for tool in tools
    )


def _tool_name(tool: Any) -> str | None:
    """A tool's name under either wire shape LiteLLM emits.

    Anthropic-shaped tools carry ``name`` at the top level; OpenAI-shaped ones
    nest it under ``function``. Reading both keeps the check provider-neutral.
    """
    if not isinstance(tool, dict):
        return None
    nested = tool.get("function")
    if isinstance(nested, dict) and "name" in nested:
        return nested["name"]
    return tool.get("name")


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
    refuse_live_inference(str(kwargs.get("model")))
    return _litellm.completion(**kwargs)


def assert_kwarg_supported(name: str) -> None:
    """Fail closed if LiteLLM does not recognise a constructor kwarg by that name.

    ADK's ``LiteLlm`` takes ``**kwargs`` and forwards them verbatim, so a
    misspelled parameter is not an error anywhere — it is simply carried along
    and ignored. For ``num_retries`` that would silently revert retry to a
    single try, and nothing downstream would show it.
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
