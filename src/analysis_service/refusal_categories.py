"""The category a provider states when its classifier refuses a completion.

Anthropic sends ``stop_details`` beside ``stop_reason="refusal"``, and its
``category`` names the classifier that stopped the completion, such as
``cyber`` or ``bio``. The category tells an operator which policy refused a
node. For example, a relaxation of the ``cyber`` classifier changes nothing for
a ``bio`` refusal.

litellm maps the stop onto ``finish_reason="content_filter"`` and keeps no
trace of ``stop_details`` on the response it returns (measured on 1.104.0).
The raw body reaches one public seam: litellm calls a ``logger_fn`` that a
caller passes to ``acompletion`` with the call's details, and those details
hold the body under ``original_response``. So the category takes two layers,
as :mod:`analysis_service.charges` does:

* The client subclass passes a ``logger_fn`` that keeps the body of that one
  call. It parses the category after the call and publishes it into a
  :class:`~contextvars.ContextVar`.
* The adapter subclass clears the variable before each call and stamps the
  category onto the response's ``custom_metadata``. The stamp crosses the
  executor seam on :class:`~analysis_service.provider.GenerationResult`, and
  :func:`analysis_service.retry.reject_unfinished` puts it on the error.

The ``logger_fn`` closure belongs to one call, so concurrent calls on one
adapter never see each other's bodies.

**The category is an open set at the provider.** A token that matches
:data:`_CATEGORY` is kept as the provider wrote it. An absent, null or
malformed value is ``None``, which the error states as
:data:`CATEGORY_NOT_STATED`. The provider's ``explanation`` is prose and is
never read.

**Only Anthropic's shape is read.** A body without a top-level
``stop_details`` gives ``None``. An OpenAI-shaped body has no category field.
A Gemini body names a harm category in ``safetyRatings``, and this module does
not read it, so a Gemini refusal also reads as not stated.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

#: The ``custom_metadata`` key under which a response carries the refusal
#: category its provider stated.
REFUSAL_CATEGORY_METADATA_KEY = "refusal_category"

#: What an error says where the provider stated no valid category. It has a
#: space, so no token that :data:`_CATEGORY` admits can equal it.
CATEGORY_NOT_STATED = "not stated"

#: A category as Anthropic spells one: a short lowercase token, such as
#: ``cyber``, ``bio`` or ``general_harms``. Anthropic's documentation names
#: categories and states no grammar for them, so this shape is an assumption.
#: A stated value outside it is logged and reads as not stated.
_CATEGORY = re.compile(r"[a-z_]{1,40}", re.ASCII)

#: The ``log_event_type`` of the details litellm passes after the provider
#: answered. The same ``logger_fn`` also receives the details before the call.
_POST_CALL = "post_api_call"

_stated_category: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "refusal_category", default=None
)


def refusal_category_of(body: str) -> str | None:
    """The refusal category a raw response body states, or ``None``.

    ``None`` for a body that is not JSON, has no ``stop_details`` object of
    type ``refusal``, or holds a category that is null or does not match
    :data:`_CATEGORY`.

    The one reader of the category a response body states, and the row "the
    refusal category a response body states" of ``ONE_READERS`` in
    ``tests/test_one_reader_lints.py`` refuses a second.
    """
    try:
        parsed = json.loads(body)
    except ValueError:
        return None
    details = parsed.get("stop_details") if isinstance(parsed, Mapping) else None
    if not isinstance(details, Mapping) or details.get("type") != "refusal":
        return None
    category = details.get("category")
    if isinstance(category, str) and _CATEGORY.fullmatch(category):
        return category
    if category is not None:
        logger.warning(
            "provider stated a refusal category of an unknown shape; reading it"
            " as not stated"
        )
    return None


def refusal_capturing_client_class(client_cls: type) -> type:
    """A ``LiteLLMClient`` subclass that reads the refusal category of each call.

    Takes the class rather than importing it, as
    :func:`analysis_service.charges.charge_capturing_client_class` does, so
    the layers compose in any order.
    """

    class RefusalCapturingClient(client_cls):
        """One tier's client, keeping each call's raw body to read its category."""

        async def acompletion(
            self, model: Any, messages: Any, tools: Any, **kwargs: Any
        ) -> Any:
            bodies: list[str] = []

            def keep_body(details: Mapping[str, Any]) -> None:
                body = details.get("original_response")
                if details.get("log_event_type") == _POST_CALL and isinstance(
                    body, str
                ):
                    bodies.append(body)

            response = await super().acompletion(
                model=model,
                messages=messages,
                tools=tools,
                logger_fn=keep_body,
                **kwargs,
            )
            _stated_category.set(refusal_category_of(bodies[-1]) if bodies else None)
            return response

    return RefusalCapturingClient


def refusal_reporting_llm_class(litellm_cls: type) -> type:
    """A ``LiteLlm`` subclass that stamps a call's refusal category.

    Cleared before each call and after it is stamped, as
    :func:`analysis_service.charges.charge_reporting_llm_class` clears the
    charge, so one tier's category never lands on another tier's response.
    """

    class RefusalReportingLlm(litellm_cls):
        """One tier's adapter, carrying a refusal category to the error."""

        async def generate_content_async(self, llm_request, stream: bool = False):
            _stated_category.set(None)
            async for response in super().generate_content_async(llm_request, stream):
                category = _stated_category.get()
                if category is not None:
                    _stated_category.set(None)
                    response.custom_metadata = {
                        **(response.custom_metadata or {}),
                        REFUSAL_CATEGORY_METADATA_KEY: category,
                    }
                yield response

    return RefusalReportingLlm
