"""Ping JWT verification for the `/v1` API.

Implements the auth rule from wayfinder ticket 008: every `/v1` route requires
a valid Ping-issued bearer JWT, checked against a configurable issuer,
audience, and JWKS endpoint. Verification returns only the token subject —
that subject is what job ownership binds to.

Failure detail is asymmetric on purpose: the *reason* a token was rejected is
logged, while callers see one generic :class:`AuthenticationError` — token
errors must not become an oracle for probing the verifier.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Protocol

import jwt
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

_ENV_VARS = {
    "issuer": "STRIDE_PING_ISSUER",
    "audience": "STRIDE_PING_AUDIENCE",
    "jwks_url": "STRIDE_PING_JWKS_URL",
}


class AuthConfigError(ValueError):
    """The Ping auth configuration is missing or unusable."""


class AuthenticationError(Exception):
    """Bearer credentials are missing, malformed, or failed verification."""


class PingAuthSettings(BaseModel):
    """Where and how Ping tokens are verified; all three knobs are deploy-time."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    issuer: str = Field(min_length=1)
    audience: str = Field(min_length=1)
    jwks_url: str = Field(min_length=1)
    algorithms: tuple[str, ...] = ("RS256",)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] = os.environ) -> PingAuthSettings:
        """Read settings from the environment, failing closed on any gap."""
        values = {field: environ.get(var, "") for field, var in _ENV_VARS.items()}
        missing = [_ENV_VARS[field] for field, value in values.items() if not value]
        if missing:
            raise AuthConfigError(
                f"Ping auth is not configured: set {', '.join(sorted(missing))}"
            )
        return cls(**values)


class SigningKeyClient(Protocol):
    """The slice of :class:`jwt.PyJWKClient` the verifier needs."""

    def get_signing_key_from_jwt(self, token: str) -> jwt.PyJWK: ...


class TokenVerifier(Protocol):
    """What the API layer requires of an auth backend."""

    def verify(self, token: str) -> str: ...


class PingJwtVerifier:
    """Validates a Ping JWT (signature via JWKS, issuer, audience, expiry)."""

    def __init__(
        self, settings: PingAuthSettings, jwks_client: SigningKeyClient | None = None
    ) -> None:
        self._settings = settings
        self._jwks_client = jwks_client or jwt.PyJWKClient(settings.jwks_url)

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
