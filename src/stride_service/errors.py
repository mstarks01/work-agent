"""The shared base for every fail-closed configuration error.

Four loaders refuse to build on bad config — the model tiers
(:class:`~stride_service.model_tiers.ModelConfigError`), vendor credentials
(:class:`~stride_service.vendors.ProviderAuthError`), LiteLLM's startup
parameter check (:class:`~stride_service.model_gate.ModelGateError`) and
sampling (:class:`~stride_service.sampling.SamplingConfigError`). Each names a
different knob, and code that *handles* the four uniformly should not have to
enumerate them.

That caller is the first-run web app (#28 decision 6), which turns any of the
four into a diagnostic page instead of a traceback. Enumerating them in a
four-tuple ``except`` would make it depend on this package's internal error
taxonomy, and the day a fifth config loader lands the page silently regresses
to the traceback it exists to replace.

Catching bare :class:`ValueError` is wrong outright:
:class:`~stride_service.engine.EngineInputError` is also a ``ValueError``, and
conflating "your config is broken" (fix it and restart) with "that description
was too long" (retype it) loses the distinction the page is built on.
``ConfigError`` keeps ``ValueError`` as its own base so existing handlers and
pydantic's coercion path are unaffected.
"""

from __future__ import annotations


class ConfigError(ValueError):
    """Configuration this process cannot run on. Fix it and restart.

    Never carries a credential *value* — subclasses name the environment
    variable that is unset, never what it contains (OWASP A09).
    """
