"""Where a node's stable instruction ends, marked for a provider that caches it.

A node's instruction is a template: repo-authored text, then the first
``{placeholder}`` ADK fills with job data. Everything before that placeholder
is identical on every call the node makes, so a provider that caches a prompt
prefix can reuse it across jobs. OpenAI's GPT-5.6 family reuses a prefix only
up to an explicit breakpoint: its default mode writes nearly the whole prompt
at 1.25x the input price and reads back only a repeated whole system message
(#1400, measured live).

Three parts, one per layer:

* :func:`stable_prefix` reads the stable text off a node's template at build
  time. The template is what :func:`~analysis_service.graph.instruction_digest`
  hashes, and nothing here changes it, so no execution identity moves.
* :func:`remember_stable_prefix` is the node's model callback. It records the
  prefix for the call that follows, in a context variable, the way
  :mod:`analysis_service.charges` carries a call's charge the other way.
* :func:`cache_marking_client_class` splits the system message at that prefix,
  marks the first block, and asks for explicit caching. Where the system text
  does not start with the recorded prefix, the request goes out unchanged.

Which vendor and model take the marker is the vendor row's
:attr:`~analysis_service.vendors.Vendor.prompt_cache`, read once at build time.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextvars import ContextVar
from typing import Any

#: The provider fields, as OpenAI and OpenRouter spell them for GPT-5.6 and
#: later. The breakpoint goes on a content block; the options at the request
#: root, where ``mode: explicit`` turns off OpenAI's own breakpoints.
BREAKPOINT_FIELD = "prompt_cache_breakpoint"
OPTIONS_FIELD = "prompt_cache_options"
EXPLICIT = {"mode": "explicit"}

#: The stable prefix of the instruction the current node is about to send.
_stable_prefix: ContextVar[str | None] = ContextVar("stable_prefix", default=None)


def stable_prefix(template: str) -> str:
    """The template's text before its first brace.

    ADK fills only placeholders, and every placeholder opens with a brace, so
    the text before the first brace is sent unchanged on every call. A brace
    that opens no placeholder only makes the prefix shorter, never wrong.
    """
    return template.partition("{")[0]


def remember_stable_prefix(prefix: str) -> Callable[..., None]:
    """A model callback that records ``prefix`` for the call that follows."""

    def callback(callback_context: Any, llm_request: Any) -> None:
        _stable_prefix.set(prefix)

    return callback


def marked(messages: Sequence[Any], prefix: str | None) -> list[Any] | None:
    """``messages`` with the system text split at ``prefix``, or ``None``.

    ``None`` where there is nothing to mark: no prefix, no system message
    first, a system message that is not plain text, or one that does not start
    with the prefix.
    """
    if not prefix or not messages:
        return None
    first = messages[0]
    content = first.get("role") == "system" and first.get("content")
    if not isinstance(content, str) or not content.startswith(prefix):
        return None
    rest = content[len(prefix) :]
    blocks = [{"type": "text", "text": prefix, BREAKPOINT_FIELD: dict(EXPLICIT)}]
    if rest:
        blocks.append({"type": "text", "text": rest})
    return [{**first, "content": blocks}, *messages[1:]]


def cache_marking_client_class(client_cls: type) -> type:
    """A ``LiteLLMClient`` subclass that marks the stable prefix on each call.

    Takes the class, as :func:`analysis_service.charges.charge_capturing_client_class`
    does, so the two layers compose in either order.
    """

    class CacheMarkingClient(client_cls):
        """One tier's client, marking where each node's stable prompt ends."""

        async def acompletion(
            self, model: Any, messages: Any, tools: Any, **kwargs: Any
        ) -> Any:
            marked_messages = marked(messages, _stable_prefix.get())
            if marked_messages is not None:
                messages = marked_messages
                kwargs["extra_body"] = {
                    **(kwargs.get("extra_body") or {}),
                    OPTIONS_FIELD: dict(EXPLICIT),
                }
            return await super().acompletion(
                model=model, messages=messages, tools=tools, **kwargs
            )

    return CacheMarkingClient
