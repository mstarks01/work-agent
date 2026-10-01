"""The switch that refuses every live provider call in this process.

Offline validation is the normal way to finish engineering work here, and a
check that silently reaches a provider spends money nobody approved. Turn
:data:`OFFLINE_ENV` on and every call that would leave this process for a
provider raises :class:`LiveInferenceRefused` first.

**One reader.** Every call site asks :func:`refuse_live_inference`, and the
two sites are the places a request leaves for a provider:
:class:`~analysis_service.provider.InProcessExecutor`, which every graph node,
every retry attempt and every single-node replay crosses, and
:func:`~analysis_service.model_gate.completion`.

A scripted model, an archived response or a transport a test supplies never
reaches either site, so offline mode admits them unchanged. A replay that has
no recorded response and asks a provider instead is refused: a miss fails
closed rather than turning into a paid call.

This guards against an accident. It is not consent, and it does not raise or
replace the spend gate in ``evals/harness/consent.py``.
"""

from __future__ import annotations

import os

from analysis_service.config_files import env_flag

#: On for ``1``, ``true``, ``yes`` or ``on``, as every other boolean flag here.
#: The test suite sets it for every test, so a test that reaches a provider
#: fails rather than spends.
OFFLINE_ENV = "ANALYSIS_OFFLINE"


class LiveInferenceRefused(RuntimeError):
    """Offline mode refused a call that would have reached a provider.

    Not a provider failure, so the retry loop never sees it and never asks
    again: it is raised before the call, not classified after one.
    """


def refuse_live_inference(route: str) -> None:
    """Raise if offline mode is on, naming the route the call would have taken."""
    if env_flag(os.environ, OFFLINE_ENV):
        raise LiveInferenceRefused(
            f"offline mode ({OFFLINE_ENV} is set) refused a live call to"
            f" {route!r}. Supply a recorded or scripted response, or unset"
            f" {OFFLINE_ENV} for a paid run the user approved."
        )
