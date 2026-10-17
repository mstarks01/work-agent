"""The shared base for every fail-closed configuration error, and the one
reader of where a request's validation error sits.

Four loaders refuse to build on bad config — the model tiers
(:class:`~analysis_service.model_tiers.ModelConfigError`), vendor credentials
(:class:`~analysis_service.vendors.ProviderAuthError`), LiteLLM's startup
parameter check (:class:`~analysis_service.model_gate.ModelGateError`) and
sampling (:class:`~analysis_service.sampling.SamplingConfigError`). Each names a
different knob, and code that *handles* the four uniformly should not have to
enumerate them.

That caller is the first-run web app, which turns any of the four into a
diagnostic page instead of a traceback. Enumerating them in a four-tuple
``except`` would make it depend on this package's internal error taxonomy, and
the day a fifth config loader lands the page silently regresses to the
traceback it exists to replace.

Catching bare :class:`ValueError` is wrong outright:
:class:`~analysis_service.engine.EngineInputError` is also a ``ValueError``, and
conflating "your config is broken" (fix it and restart) with "that description
was too long" (retype it) loses the distinction the page is built on.
``ConfigError`` keeps ``ValueError`` as its own base so existing handlers and
pydantic's coercion path are unaffected.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError


class ConfigError(ValueError):
    """Configuration this process cannot run on. Fix it and restart.

    Never carries a credential *value* — subclasses name the environment
    variable that is unset, never what it contains (OWASP A09).
    """


def error_place(error: Mapping[str, Any]) -> str:
    """Where one pydantic error sits: a dotted path of declared names and indexes.

    **A refusal names the field, never what the caller sent.** An
    ``extra_forbidden`` error sits at a key the caller made up, so its path
    stops at the object that holds that key.
    """
    loc = error["loc"][:-1] if error["type"] == "extra_forbidden" else error["loc"]
    return ".".join(str(part) for part in loc)


def refusal_text(exc: ValueError) -> str:
    """What a refusal says for ``exc``.

    A :class:`~pydantic.ValidationError` prints each input it refused, so it
    is read as each place and rule instead.
    """
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{error_place(error)}: {error['msg']}"
            for error in exc.errors(include_input=False)
        )
    return str(exc)
