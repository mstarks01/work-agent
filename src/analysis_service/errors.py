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

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, NamedTuple

from pydantic import ValidationError

if TYPE_CHECKING:
    from alembic.util import CommandError

    from analysis_service.store_settings import StoreConfigError


class ConfigError(ValueError):
    """Configuration this process cannot run on. Fix it and restart.

    Never carries a credential *value* — subclasses name the environment
    variable that is unset, never what it contains (OWASP A09).
    """


def config_refusal(exc: ConfigError | StoreConfigError | CommandError) -> str:
    """The one line a command prints for ``exc`` in place of a traceback.

    A :class:`~analysis_service.store_settings.StoreConfigError` names the
    store setting to fix, and an Alembic :class:`~alembic.util.CommandError`
    names the revision the database holds. Neither holds a secret or the
    database URL.
    """
    return f"cannot run on this configuration: {exc}"


def error_place(error: Mapping[str, Any]) -> str:
    """Where one pydantic error sits: a dotted path of declared names and indexes.

    **A refusal names the field, never what the caller sent.** An
    ``extra_forbidden`` error sits at a key the caller made up, so its path
    stops at the object that holds that key.
    """
    loc = error["loc"][:-1] if error["type"] == "extra_forbidden" else error["loc"]
    return ".".join(str(part) for part in loc)


#: How many complaints a refusal carries. A body that is wrong in ten thousand
#: places is wrong; a caller needs to see that and the first few, not all of
#: them. Rendering every one made the refusal amplify the request 43 times.
MAX_RENDERED_ERRORS = 20


class ShownError(NamedTuple):
    """One error a refusal names: where it sits, and what the rule said."""

    #: The place from :func:`error_place`; empty for the entry that counts the rest.
    place: str
    message: str


def shown_errors(errors: Sequence[Mapping[str, Any]]) -> list[ShownError]:
    """The place and message of each error a refusal names: the first
    :data:`MAX_RENDERED_ERRORS`, then one entry with no place that counts the
    rest."""
    shown = [
        ShownError(error_place(error), error["msg"])
        for error in errors[:MAX_RENDERED_ERRORS]
    ]
    if len(errors) > MAX_RENDERED_ERRORS:
        hidden = len(errors) - MAX_RENDERED_ERRORS
        shown.append(ShownError("", f"and {hidden} more problems not shown"))
    return shown


def refusal_text(exc: ValueError) -> str:
    """What a refusal says for ``exc``.

    A :class:`~pydantic.ValidationError` prints each input it refused, so it
    is read as each place and rule instead, through :func:`shown_errors`.
    """
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{error.place}: {error.message}" if error.place else error.message
            for error in shown_errors(exc.errors(include_input=False))
        )
    return str(exc)
