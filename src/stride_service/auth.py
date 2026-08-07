"""Bearer-token verification for the `/v1` API.

Every `/v1` route requires a valid bearer token, and verification returns only
the token subject — that subject is what job ownership binds to.

The default ``oidc`` backend verifies OIDC JWTs (signature via JWKS, issuer,
audience, expiry), which covers any standard OIDC identity provider. Backends
are selected at deploy time by ``STRIDE_AUTH_PROVIDER`` and constructed through
:func:`build_verifier`, so a new backend is one registry entry — another OIDC
instance, or a new :class:`TokenVerifier` implementation for a different
mechanism entirely.

Failure detail is asymmetric on purpose: the *reason* a token was rejected is
logged, while callers see one generic :class:`AuthenticationError` — token
errors must not become an oracle for probing the verifier.

The JWKS endpoint is held to two bounds the library does not impose: it must be
``https`` (see :meth:`OidcSettings._https_jwks`), and its fetch may not stall a
request past :data:`JWKS_TIMEOUT_SECONDS`. A fetch that fails or times out
raises ``PyJWKClientConnectionError``, which is a ``PyJWTError`` and so lands on
the same fail-closed path as a bad token: the request is refused rather than
served on an unverified subject.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from typing import Protocol

import jwt
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)

_OIDC_ENV_SUFFIXES = {
    "issuer": "ISSUER",
    "audience": "AUDIENCE",
    "jwks_url": "JWKS_URL",
}

# How long a JWKS fetch may hold a request before verification gives up.
# PyJWKClient's own default is 30s, which is a request-path stall an unreachable
# or slow identity provider can impose on every unverified token at once — the
# keys are cached, so this is paid on a cold cache and on every rotation. Stated
# rather than inherited: the number belongs to this service's latency budget,
# not to the library's.
JWKS_TIMEOUT_SECONDS = 5.0


class AuthConfigError(ValueError):
    """The auth configuration is missing or unusable."""


class AuthenticationError(Exception):
    """Bearer credentials are missing, malformed, or failed verification."""


class TokenVerifier(Protocol):
    """What the API layer requires of an auth backend."""

    def verify(self, token: str) -> str: ...


class OidcSettings(BaseModel):
    """Where and how OIDC JWTs are verified; all three knobs are deploy-time."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    issuer: str = Field(min_length=1)
    audience: str = Field(min_length=1)
    jwks_url: str = Field(min_length=1)
    algorithms: tuple[str, ...] = ("RS256",)

    @field_validator("jwks_url")
    @classmethod
    def _https_jwks(cls, value: str) -> str:
        """Refuse a JWKS URL that is not HTTPS.

        The signing keys fetched from here are the whole basis on which a token
        is trusted, so plaintext transport makes token verification forgeable by
        anyone on the path: substitute the key set, sign your own token, become
        any subject. Refused rather than warned about, and with no loopback
        exemption — this is deploy-time configuration, and a local identity
        provider that speaks HTTP is a test fixture, which reaches
        :class:`OidcJwtVerifier` by constructing it directly rather than through
        the environment.
        """
        if not value.lower().startswith("https://"):
            raise ValueError("jwks_url must be an https:// URL")
        return value

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] = os.environ, *, prefix: str
    ) -> OidcSettings:
        """Read ``<prefix>_*`` settings from the env, failing closed on any gap."""
        env_vars = {
            field: f"{prefix}_{suffix}" for field, suffix in _OIDC_ENV_SUFFIXES.items()
        }
        values = {field: environ.get(var, "") for field, var in env_vars.items()}
        missing = [env_vars[field] for field, value in values.items() if not value]
        if missing:
            raise AuthConfigError(
                f"OIDC auth is not configured: set {', '.join(sorted(missing))}"
            )
        try:
            return cls(**values)
        except ValidationError as exc:
            # Every other failure this loader can produce is an AuthConfigError,
            # and a caller that catches the configuration error should not have
            # to catch pydantic's as well to cover the same class of mistake.
            raise AuthConfigError(f"OIDC auth is misconfigured: {exc}") from exc


class SigningKeyClient(Protocol):
    """The slice of :class:`jwt.PyJWKClient` the verifier needs."""

    def get_signing_key_from_jwt(self, token: str) -> jwt.PyJWK: ...


class OidcJwtVerifier:
    """Validates an OIDC JWT (signature via JWKS, issuer, audience, expiry)."""

    def __init__(
        self, settings: OidcSettings, jwks_client: SigningKeyClient | None = None
    ) -> None:
        self._settings = settings
        self._jwks_client = jwks_client or jwt.PyJWKClient(
            settings.jwks_url, timeout=JWKS_TIMEOUT_SECONDS
        )

    def verify(self, token: str) -> str:
        """Return the token subject, or raise :class:`AuthenticationError`."""
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=list(self._settings.algorithms),
                issuer=self._settings.issuer,
                audience=self._settings.audience,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            logger.info("rejected bearer token: %s", exc)
            raise AuthenticationError("invalid or expired credentials") from exc
        return claims["sub"]


VerifierFactory = Callable[[Mapping[str, str]], TokenVerifier]

_FACTORIES: dict[str, VerifierFactory] = {
    "oidc": lambda env: OidcJwtVerifier(
        OidcSettings.from_env(env, prefix="STRIDE_OIDC")
    ),
}


def build_verifier(env: Mapping[str, str] = os.environ) -> TokenVerifier:
    """Select and construct the configured auth backend; fail closed.

    The provider is chosen by ``STRIDE_AUTH_PROVIDER`` at deploy time — never
    by the request — so an empty or unknown value raises rather than falling
    back to a weaker or absent check.
    """
    name = env.get("STRIDE_AUTH_PROVIDER", "").strip().lower()
    if not name:
        raise AuthConfigError("set STRIDE_AUTH_PROVIDER")
    try:
        factory = _FACTORIES[name]
    except KeyError:
        known = ", ".join(sorted(_FACTORIES))
        raise AuthConfigError(
            f"unknown auth provider {name!r}; known providers: {known}"
        ) from None
    return factory(env)
