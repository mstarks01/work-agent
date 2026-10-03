"""The format ladder: how a tier sends its node schema, and when it moves down (ADR 0058).

A tier has a ladder of rungs, best first. Each rung is one way to send a node
schema to the provider. :func:`rungs_for` builds the ladder when the service
starts, from the tier's ``structured_output`` setting and what the pinned
library would send for the pair. :class:`Ladder` holds the ladder while the
service runs. A provider refusal that :func:`refusal_of` names moves a request
one rung down, and :data:`REFUSAL_SCOPE` says what the move covers: the whole
tier, or only the refused schema.

The executor in :mod:`analysis_service.provider` holds one translator per rung
and asks the :class:`Ladder` which rung to send on. Nothing here sends a
request.

**It imports nothing from the package when it loads.**
:mod:`analysis_service.retry` reads the refusal tables, and ``retry`` stays
free of the provider libraries at import time. So :func:`rungs_for` imports
:mod:`analysis_service.model_gate`, which loads litellm, only when it runs.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from analysis_service.sampling import TierSampling
    from analysis_service.vendors import Vendor

#: How a node schema travelled: in the provider's structured-output field, as a
#: tool's parameters, or stated in the request text.
SchemaPath = Literal["native", "tool", "prompt"]

#: The rungs a tier's schema can travel on, best first. Each rung names the
#: path it records; ``unconstrained`` is a tier that sends no schema.
Rung = Literal["native", "forced_tool", "offered_tool", "prompt", "unconstrained"]
PATH_OF_RUNG: Mapping[Rung, SchemaPath | None] = {
    "native": "native",
    "forced_tool": "tool",
    "offered_tool": "tool",
    "prompt": "prompt",
    "unconstrained": None,
}

#: The tool a tool rung sends a node schema as. The forced rung forces the
#: model to call it, and the executor turns the call back into the JSON text a
#: node reads.
OUTPUT_TOOL_NAME = "submit_answer"

#: The translator arguments each rung adds. ``response_format=None`` keeps any
#: structured-output field off the wire, because ADK applies the translator's
#: arguments over the one it derives. The forced ``tool_choice`` rides the
#: translator because ADK forwards no tool choice from a request.
RUNG_KWARGS: Mapping[Rung, Mapping[str, Any]] = {
    "native": {},
    "forced_tool": {
        "response_format": None,
        "tool_choice": {"type": "function", "function": {"name": OUTPUT_TOOL_NAME}},
    },
    "offered_tool": {"response_format": None},
    "prompt": {"response_format": None},
    "unconstrained": {},
}

#: Why a provider refused how a schema was sent. Any of these moves the request
#: one rung down.
SchemaRefusal = Literal[
    "grammar_too_large",
    "forced_tool_refused",
    "tools_refused",
    "no_endpoint",
    "schema_field_refused",
]

#: The messages that mean a provider refused how a schema was sent, by rule,
#: checked in this order. Matched in lower case. The record keeps the rule name
#: and never the message, which can quote the prompt back (OWASP LLM02). None
#: of these was confirmed by a live call unless its comment says so.
SCHEMA_REFUSALS: Mapping[SchemaRefusal, tuple[str, ...]] = {
    # Anthropic, against ``SystemModel``: recorded in ``config/sampling.toml``.
    "grammar_too_large": ("compiled grammar is too large",),
    # Anthropic refuses a forced tool together with extended thinking, and a
    # model with thinking always on cannot turn it off. Matches the field name
    # on every vendor: ``tool_choice``, or Bedrock's ``toolChoice``.
    "forced_tool_refused": ("forces tool use", "tool_choice", "toolchoice"),
    # A model that takes no tools at all, in the words vendors use for it.
    "tools_refused": (
        "does not support tool",
        "doesn't support tool",
        "tool use is not supported",
        "tools are not supported",
        "function calling is not",
        "toolconfig",
    ),
    # OpenRouter, where no upstream serves the parameters the request carries.
    "no_endpoint": ("no endpoints found",),
    # A 400 that names a field which carries the schema: OpenAI's and
    # OpenRouter's ``response_format``, Anthropic's ``output_format``,
    # Bedrock's ``outputConfig``, Gemini's ``response_schema``. AWS documents
    # a 400 for an unsupported schema keyword on Bedrock, and does not document
    # the message (structured-outputs page, read 2026-10-02).
    "schema_field_refused": (
        "outputconfig",
        "output_config",
        "output_format",
        "response_format",
        "response_schema",
        "responseschema",
        "response_json_schema",
        "responsejsonschema",
        "json_schema",
        "structured output",
    ),
}

#: What a refusal is a property of. A ``pair`` refusal moves the whole tier one
#: rung down, because the provider refuses how the schema is sent for every
#: request to that ``(vendor, model)``. A ``schema`` refusal moves only the
#: schema the provider refused, because the same model takes a smaller schema
#: on the same rung.
RefusalScope = Literal["pair", "schema"]
REFUSAL_SCOPE: Mapping[SchemaRefusal, RefusalScope] = {
    "grammar_too_large": "schema",
    "forced_tool_refused": "pair",
    "tools_refused": "pair",
    "no_endpoint": "pair",
    "schema_field_refused": "pair",
}

#: The statuses a refusal arrives with: a bad request, and OpenRouter's 404
#: when no upstream serves the request.
_REFUSAL_STATUSES = frozenset({400, 404})

#: A tool in the shape litellm takes, for the build-time check that this pair
#: accepts tool calls at all.
_PROBE_TOOL = {
    "type": "function",
    "function": {"name": OUTPUT_TOOL_NAME, "parameters": {"type": "object"}},
}


def refusal_of(exc: BaseException) -> SchemaRefusal | None:
    """The :data:`SCHEMA_REFUSALS` rule a provider error matches, or ``None``."""
    if getattr(exc, "status_code", None) not in _REFUSAL_STATUSES:
        return None
    message = str(exc).lower()
    for rule, phrases in SCHEMA_REFUSALS.items():
        if any(phrase in message for phrase in phrases):
            return rule
    return None


def rungs_for(
    vendor: Vendor, model: str, sampling: TierSampling, source: str
) -> tuple[Rung, ...]:
    """The rungs this tier can send its node schema on, best first.

    ``native`` uses the provider's own structured-output field, which
    constrains decoding. ``forced_tool`` sends the schema as a tool's
    parameters and forces the call. ``offered_tool`` offers the tool and lets
    the model call it. ``prompt`` states the schema in the request text. Each
    rung below ``native`` asks the model to follow the schema rather than
    making it, and the schema re-ask in
    :class:`~analysis_service.provider.ExecutedLlm` covers an answer that does
    not.

    The setting decides which rungs are allowed:

    * ``auto`` allows every rung the pair supports, and ends with ``prompt``,
      which every model takes;
    * ``native`` allows ``native`` alone, and a pair the library would not send
      a native schema for is a startup error;
    * ``tool`` allows the two tool rungs, and a pair that takes no tool call is
      a startup error.

    ``forced_tool`` is left out where the tier sets ``thinking``, because
    Anthropic refuses a forced tool together with extended thinking.

    Under ``constrain_output = false`` no schema is sent, and the ladder is the
    one ``unconstrained`` rung.

    **It reads the probe and never the map.**
    :func:`~analysis_service.model_gate.schema_support` and
    :func:`~analysis_service.model_gate.check_supported` ask the installed
    library what it would do with each request. ``supports_response_schema`` is
    a claim in a data file, and #819 measured one that was wrong.
    """
    from analysis_service.model_gate import ModelGateError, schema_support

    if not sampling.constrain_output:
        return ("unconstrained",)
    setting = sampling.structured_output
    native = schema_support(vendor, model) == "native"
    if setting == "native":
        if not native:
            raise ModelGateError(
                f"{source}: {vendor.name} cannot constrain {model!r} to a schema"
                ' natively, and this tier sets structured_output = "native".'
                ' Set "auto" or "tool" to send the schema another way, or'
                " choose a model whose provider supports structured output."
            )
        return ("native",)
    rungs: list[Rung] = []
    if setting == "auto" and native:
        rungs.append("native")
    tool_rungs: tuple[Rung, ...] = ("offered_tool",)
    if sampling.thinking is None:
        tool_rungs = ("forced_tool", "offered_tool")
    rungs.extend(
        rung
        for rung in tool_rungs
        if _accepts(vendor, model, {"tools": [_PROBE_TOOL], **RUNG_KWARGS[rung]})
    )
    if setting == "auto":
        rungs.append("prompt")
    if not rungs:
        raise ModelGateError(
            f"{source}: {vendor.name} {model!r} takes no tool call, and this tier"
            ' sets structured_output = "tool". Set "auto" to state the'
            " schema in the request instead."
        )
    return tuple(rungs)


def _accepts(vendor: Vendor, model: str, params: dict[str, Any]) -> bool:
    """Whether the pinned library would send these params for this pair."""
    from analysis_service.model_gate import ModelGateError, check_supported

    try:
        check_supported(vendor, model, params, source="ladder")
    except ModelGateError:
        return False
    return True


class Ladder:
    """One tier's rungs, and the rung each request is sent on.

    A ``pair`` refusal moves the whole tier, because the provider refuses that
    rung for every request to this ``(vendor, model)``. A ``schema`` refusal
    moves only the schema it refused, so the tier's other nodes keep their
    rung. A move stays for the life of the process and never goes back up.
    Lanes run together on one tier, so another lane may move the tier first:
    each step only grows.
    """

    def __init__(self, rungs: Sequence[Rung]) -> None:
        if not rungs:
            raise ValueError("a ladder needs at least one rung")
        self._rungs = tuple(rungs)
        self._step = 0
        #: The step for each schema a provider refused on its own, keyed by
        #: :func:`_schema_key`.
        self._schema_steps: dict[str, int] = {}

    @property
    def rungs(self) -> tuple[Rung, ...]:
        """Every rung, best first."""
        return self._rungs

    @property
    def rung(self) -> Rung:
        """The tier's rung: where a request goes if its schema was not refused alone."""
        return self._rungs[self._step]

    def step_for(self, schema: Mapping[str, Any] | None) -> int:
        """The step a request with this output schema is sent on."""
        return max(self._step, self._schema_steps.get(_schema_key(schema), 0))

    def moved_below(
        self, step: int, schema: Mapping[str, Any] | None, refusal: SchemaRefusal
    ) -> int | None:
        """The step to send on after ``refusal`` at ``step``, or ``None`` on the last rung.

        The move is kept, by :data:`REFUSAL_SCOPE`, for every later request it
        covers.
        """
        if step + 1 >= len(self._rungs):
            return None
        key = _schema_key(schema)
        if REFUSAL_SCOPE[refusal] == "pair":
            self._step = max(self._step, step + 1)
        else:
            self._schema_steps[key] = max(self._schema_steps.get(key, 0), step + 1)
        return self.step_for(schema)


def _schema_key(schema: Mapping[str, Any] | None) -> str:
    """One node schema, as the key a refusal of it alone is kept under."""
    return json.dumps(schema, sort_keys=True, default=str)
