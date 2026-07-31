"""The vendor registry: the per-provider facts nothing else will carry.

Every model reaches the graph through ADK's ``LiteLlm``, so what varies per
provider is not *how* to call it but three facts the adapter cannot supply:

* the **router prefix** LiteLLM dispatches on (``vertex_ai/``, ``anthropic/``,
  ``openai/``), which is also the vendor half of a generation-identity
  fingerprint;
* the **credential mode**, which the vendor *implies* rather than the config
  choosing: Vertex admits no raw-API-key path under any adapter
  (``BerriAI/litellm#21036``), so ``vertex + api_key`` must be unrepresentable
  rather than validated against;
* the **floating-form rule** for model identifiers, which differs by vendor
  and, on Vertex, by model *family*.

Deliberately **not** here: the per-``(vendor, model)`` sampling support set.
``vertex_ai/`` is not one provider and the real answer lives in LiteLLM's own
config classes, so the check is a call to ``litellm`` at build time — see
:mod:`stride_service.model_gate`.

Reasoning effort is likewise *not* per-vendor data: one uniform
``reasoning_effort`` surface reaches every vendor, so the kwarg is a module
constant rather than a registry field whose value is the same everywhere.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from stride_service.errors import ConfigError

VendorName = Literal["vertex", "anthropic", "openai"]
VENDOR_NAMES: tuple[VendorName, ...] = ("vertex", "anthropic", "openai")

# The one reasoning knob, uniform across vendors: LiteLLM maps it to
# ``budget_tokens`` on Anthropic (identically via Vertex), to
# ``thinkingConfig`` on Gemini, and passes it through on OpenAI o-series.
REASONING_KWARG = "reasoning_effort"

_API_KEY_TEMPLATE = "STRIDE_{vendor}_API_KEY"

# Vertex needs a project and a location alongside ADC; they are ordinary config,
# not credentials, but the build cannot construct a working client without them.
VERTEX_PROJECT_VAR = "STRIDE_VERTEX_PROJECT"
VERTEX_LOCATION_VAR = "STRIDE_VERTEX_LOCATION"

# Application Default Credentials, named explicitly rather than discovered: the
# check is for *declared* credential material, and a filesystem probe of gcloud's
# well-known path would make the build's outcome depend on a developer's laptop
# state (OWASP A02).
ADC_VAR = "GOOGLE_APPLICATION_CREDENTIALS"


class ProviderAuthError(ConfigError):
    """Outbound provider credentials are missing or unusable.

    Distinct from :class:`stride_service.auth.AuthConfigError`, which is about
    *inbound* bearer auth on the HTTP surface. Both fail closed at build time;
    conflating them would let one subsystem's misconfiguration read as the
    other's.

    Messages name the environment variable, never its value (OWASP A09): a key
    echoed into a log or a problem+json body has leaked.
    """


class CredentialMode(StrEnum):
    """How a vendor is authenticated. The vendor implies it; config never picks."""

    API_KEY = "api_key"
    ADC = "adc"


@dataclass(frozen=True)
class _FormRule:
    """One model family's pinned-form rule.

    ``family`` is the model-name prefix this rule covers; the empty string is the
    vendor's catch-all. ``pinned`` is the shape a *pinned* build takes where the
    vendor publishes one — Vertex Claude's ``@YYYYMMDD``, Anthropic's dated
    snapshot suffix. Where a vendor's most specific stable identifier is the bare
    name (Gemini 2.5 and later ship no numbered builds), ``pinned`` is ``None``
    and only the denylist applies.
    """

    family: str
    pinned: re.Pattern[str] | None
    hint: str


# What "pinned" means, per vendor and — on Vertex — per model family. This is an
# **open-world denylist** by decision, and is explicitly weak: an allowlist of
# known builds breaks outright the moment a vendor retires one, and that risk
# runs against three catalogs. The reproducibility guarantee does not rest here
# — it rests on the *served* build read back from every response.
_ALIAS_SUFFIX = "-latest"
_PRE_GA_MARKERS = ("-preview", "-exp")

_VERTEX_DATED = re.compile(r"@\d{8}$")
_ANTHROPIC_DATED = re.compile(r"-\d{8}$")

_FORM_RULES: dict[VendorName, tuple[_FormRule, ...]] = {
    "vertex": (
        _FormRule(
            family="claude-",
            pinned=_VERTEX_DATED,
            hint="a Vertex Claude build is pinned as 'claude-...@YYYYMMDD'",
        ),
        _FormRule(family="", pinned=None, hint=""),
    ),
    "anthropic": (
        _FormRule(
            family="claude-",
            pinned=_ANTHROPIC_DATED,
            hint="an Anthropic build is pinned as 'claude-...-YYYYMMDD'",
        ),
        _FormRule(family="", pinned=None, hint=""),
    ),
    # OpenAI's o-series ships no dated form at all, and the dated gpt-* snapshots
    # are optional rather than canonical, so there is nothing to require here
    # beyond the shared denylist.
    "openai": (_FormRule(family="", pinned=None, hint=""),),
}


@dataclass(frozen=True)
class Vendor:
    """One provider's registry entry."""

    name: VendorName
    prefix: str
    credential: CredentialMode

    @property
    def litellm_provider(self) -> str:
        """The provider token LiteLLM dispatches on — the prefix without its slash.

        Derived rather than stored so the prefix stays the single source: the
        router string and the gate's ``custom_llm_provider`` can never disagree.
        """
        return self.prefix.rstrip("/")

    @property
    def api_key_var(self) -> str:
        """The env var holding this vendor's API key, where it uses one."""
        return _API_KEY_TEMPLATE.format(vendor=self.name.upper())

    def route(self, model: str) -> str:
        """The router string LiteLLM dispatches on, e.g. ``vertex_ai/gemini-2.5-pro``.

        The same join is the vendor half of a fingerprint: the served
        identifier carries no vendor, and Vertex-hosted Claude and
        Anthropic-direct return through an identical transformation, so a
        served-only hash would let a manifest blessed on one silently certify
        the other.
        """
        return f"{self.prefix}{model}"

    def validate_model(self, model: str, source: str) -> str:
        """Reject a floating model identifier; return the pinned one unchanged.

        ``source`` names where the string came from (a config key or an env var)
        so the error points ops at the knob to turn.
        """
        if not model or model != model.strip():
            raise ValueError(f"{source}: {model!r} is not a model identifier")
        if model.endswith(_ALIAS_SUFFIX):
            raise ValueError(
                f"{source}: {model!r} is a '{_ALIAS_SUFFIX}' alias;"
                " use the pinned model identifier"
            )
        marker = next((m for m in _PRE_GA_MARKERS if m in model), None)
        if marker is not None:
            raise ValueError(
                f"{source}: {model!r} is a pre-GA '{marker.lstrip('-')}' build;"
                " use the pinned model identifier"
            )
        rule = self._rule_for(model)
        if rule.pinned is not None and not rule.pinned.search(model):
            raise ValueError(f"{source}: {model!r} is not pinned — {rule.hint}")
        return model

    def _rule_for(self, model: str) -> _FormRule:
        """The first family rule matching this model; the catch-all always does."""
        rules = _FORM_RULES[self.name]
        return next(rule for rule in rules if model.startswith(rule.family))

    def credential_kwargs(self, env: Mapping[str, str]) -> dict[str, str]:
        """The auth kwargs for this vendor's ``LiteLlm``, or fail closed.

        Read **explicitly** from vendor-scoped variables rather than relying on
        LiteLLM's ambient ``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY`` pickup, so
        no credential this deployment did not declare can authenticate a run —
        an undeclared key in the process environment is exactly the ASI03
        inherited-credential path.

        Long-lived API keys are accepted with controls, not avoided: none of
        these vendors issues a short-lived token, so the residual risk is real
        and is mitigated by keeping keys env-only, out of logs, out of the
        report, and out of the fingerprint, plus rotation.
        """
        return {
            kwarg: self._require(env, var) for kwarg, var in self._credential_vars()
        }

    def _credential_vars(self) -> tuple[tuple[str, str], ...]:
        """The ``(LiteLlm kwarg, env var)`` pairs this vendor authenticates with.

        One table, two readers: :meth:`credential_kwargs` builds the adapter's
        auth from it and :attr:`required_env_vars` reports it. Deriving both
        from the same place is the point — a vendor -> env-var table copied
        into a caller drifts from the check that actually runs, and the caller
        that needs it is a *diagnostic* page whose whole value is being right
        about which variables are missing.
        """
        if self.credential is CredentialMode.API_KEY:
            return (("api_key", self.api_key_var),)
        return (
            ("vertex_project", VERTEX_PROJECT_VAR),
            ("vertex_location", VERTEX_LOCATION_VAR),
            ("vertex_credentials", ADC_VAR),
        )

    @property
    def required_env_vars(self) -> tuple[str, ...]:
        """Every environment variable this vendor needs, in check order.

        :meth:`_require` raises on the *first* missing variable, so a Vertex
        user with none of the three set would otherwise discover them one per
        restart. Callers reporting a credential failure list this whole set and
        mark the unset ones — presence only, never values (OWASP A09).
        """
        return tuple(var for _, var in self._credential_vars())

    def _require(self, env: Mapping[str, str], var: str) -> str:
        value = env.get(var, "")
        if not value.strip():
            raise ProviderAuthError(
                f"vendor {self.name!r} needs {var};"
                f" it is unset or empty (credential mode: {self.credential})"
            )
        return value.strip()


VENDORS: dict[VendorName, Vendor] = {
    "vertex": Vendor(
        name="vertex",
        prefix="vertex_ai/",
        credential=CredentialMode.ADC,
    ),
    "anthropic": Vendor(
        name="anthropic",
        prefix="anthropic/",
        credential=CredentialMode.API_KEY,
    ),
    "openai": Vendor(
        name="openai",
        prefix="openai/",
        credential=CredentialMode.API_KEY,
    ),
}


def join_served(requested_route: str, served_model: str) -> str:
    """Re-attach a requested route's vendor prefix to the build that answered.

    Providers return a bare build identifier — ``gemini-2.5-pro-002``, not
    ``vertex_ai/gemini-2.5-pro-002`` — so the vendor has to come from what was
    asked for. That join is what keeps a fingerprint honest: Vertex-hosted
    Claude and Anthropic-direct return through an identical transformation, so
    a served-only hash would let a manifest blessed on one silently certify the
    other.

    The prefix is the segment before the first ``/``, which is the shape every
    registry entry's :attr:`Vendor.prefix` takes. A requested route carrying no
    prefix — an offline stand-in bound to a bare name — yields the served build
    unchanged rather than inventing a vendor for it.
    """
    prefix, separator, _ = requested_route.partition("/")
    return f"{prefix}{separator}{served_model}" if separator else served_model


def vendor_for(name: str) -> Vendor:
    """The registry entry for a vendor name, or raise."""
    if name not in VENDORS:
        known = ", ".join(VENDOR_NAMES)
        raise ValueError(f"unknown vendor {name!r} (known: {known})")
    return VENDORS[name]
