"""Bearer-token verification for the `/v1` API.

Every `/v1` route requires a valid bearer token, and verification returns only
the token subject. That subject is what job ownership binds to.

The default ``oidc`` backend verifies OIDC JWTs. It checks the signature through
JWKS, and the issuer, audience and expiry, which covers any standard OIDC
identity provider. A deployment selects a backend with
``ANALYSIS_AUTH_PROVIDER``, and :func:`build_verifier` constructs it, so a new
backend is one registry entry: another OIDC instance, or a new
:class:`TokenVerifier` implementation for a different mechanism.

Everything configurable here is spelled in OIDC's own vocabulary — issuer,
audience, JWKS endpoint, and accepted signing algorithms — and nothing in this
module knows the name of an identity provider. Ping, Auth0, Entra and Okta are
all deployments of the same four variables. Where one appears in this
repository, it appears as an example value rather than as a branch.

Failure detail is asymmetric on purpose. The service logs the reason a token was
rejected, and callers see one generic :class:`AuthenticationError`, because
token errors must not become an oracle for probing the verifier.

The service holds the JWKS endpoint to two bounds the library does not impose.
It must be ``https``; see :meth:`OidcSettings._https_jwks`. Its fetch may not
stall a request past :data:`JWKS_TIMEOUT_SECONDS`. A fetch that fails or times
out raises ``PyJWKClientConnectionError``, which is a ``PyJWTError``, so it
lands on the same fail-closed path as a bad token: the service refuses the
request rather than serving it on an unverified subject.
"""

from __future__ import annotations

import logging
import os
import time
import unicodedata
from collections.abc import Callable, Mapping
from typing import Any, Protocol

import jwt
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)

_OIDC_ENV_SUFFIXES = {
    "issuer": "ISSUER",
    "audience": "AUDIENCE",
    "jwks_url": "JWKS_URL",
}

# ``algorithms`` is read from the environment like the other three, but unlike
# them it is optional: absent, the default below applies. That asymmetry is
# deliberate — an issuer or an audience nobody chose is a misconfiguration, while
# a signature algorithm nobody chose has a safe answer.
_ALGORITHMS_SUFFIX = "ALGORITHMS"

# What an OIDC provider may be trusted to have signed with. An **allowlist**,
# and the two omissions are the entire security content of it:
#
# * ``none`` — the unsigned-JWT algorithm. Accepting it makes every token
#   forgeable by anyone who can spell JSON.
# * ``HS*`` — HMAC. The keys here arrive from a JWKS endpoint and are *public*,
#   so accepting a symmetric algorithm alongside asymmetric ones is the classic
#   key-confusion attack: an attacker re-signs a token they wrote using the
#   public key as the HMAC secret, and verification passes because the verifier
#   was willing to treat a verification key as a signing key. This is why the
#   set cannot simply be "whatever PyJWT implements", and why a deployment
#   cannot widen it: an operator does not get to add ``HS256`` here by putting
#   it in a variable.
#
# What remains is the asymmetric families a standards-compliant IdP actually
# advertises. RS256 is the OIDC Core mandatory-to-implement algorithm and stays
# the default, so a deployment that sets nothing verifies exactly what it
# verified before.
ALLOWED_ALGORITHMS: frozenset[str] = frozenset(
    {
        "RS256",
        "RS384",
        "RS512",
        "PS256",
        "PS384",
        "PS512",
        "ES256",
        "ES384",
        "ES512",
        "EdDSA",
    }
)

DEFAULT_ALGORITHMS: tuple[str, ...] = ("RS256",)

# Case-folded lookup back to the canonical spelling. Needed because the JWA
# names are not uniformly upper-case: every family here is, except ``EdDSA``,
# so upper-casing an operator's input would make the one mixed-case algorithm
# in the set impossible to configure.
_CANONICAL_ALGORITHMS = {name.upper(): name for name in ALLOWED_ALGORITHMS}

# How long a JWKS fetch may hold a request before verification gives up.
# PyJWKClient's own default is 30s, which is a request-path stall an unreachable
# or slow identity provider can impose on every unverified token at once — the
# keys are cached, so this is paid on a cold cache and on every rotation. Stated
# rather than inherited: the number belongs to this service's latency budget,
# not to the library's.
JWKS_TIMEOUT_SECONDS = 5.0

# The shortest interval between two JWKS re-fetches. PyJWKClient re-fetches the
# whole key set on any `kid` it has not cached, and reads that `kid` from the
# token's UNVERIFIED header — so without this an unauthenticated caller sending a
# fresh random `kid` per request forces one outbound fetch each time, defeating
# the key cache. This caps re-fetches to one per interval: a `kid` missing from
# the cached set fails fast while the cooldown holds.
JWKS_REFRESH_COOLDOWN_SECONDS = 60.0

# Unicode general categories refused in a token subject: ``Cc`` the C0/C1
# control characters (a newline here forges a log record), ``Cf`` the invisible
# formatting ones. Matches the rule the source label already applies.
_SUBJECT_REJECTED_CATEGORIES = frozenset({"Cc", "Cf"})


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
    algorithms: tuple[str, ...] = DEFAULT_ALGORITHMS

    @field_validator("algorithms")
    @classmethod
    def _allowed_algorithms(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Hold the accepted algorithms to :data:`ALLOWED_ALGORITHMS`.

        Configurable, but from a fixed set rather than from whatever the IdP
        advertises. Reading the algorithm list out of an issuer's discovery
        document would let the party being verified decide how it is verified,
        which inverts the trust relationship the check exists to establish; and
        an unconstrained variable would let a deployment write ``HS256`` beside
        a JWKS URL and turn a public key into a signing secret.

        An empty list is refused rather than read as "no restriction": PyJWT
        treats an empty ``algorithms`` as an error anyway, and a verifier whose
        accepted set is silently empty is the shape a fail-open bug takes.
        """
        if not value:
            raise ValueError("at least one signing algorithm is required")
        rejected = sorted(set(value) - ALLOWED_ALGORITHMS)
        if rejected:
            allowed = ", ".join(sorted(ALLOWED_ALGORITHMS))
            raise ValueError(
                f"unsupported signing algorithm(s): {', '.join(rejected)}."
                f" Allowed: {allowed}. Symmetric (HS*) and unsigned ('none')"
                " algorithms are refused: JWKS keys are public, so accepting"
                " one would let a public key be used as a signing secret."
            )
        return value

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
        """Read ``<prefix>_*`` settings from the env, failing closed on any gap.

        Three variables are required and one — ``<prefix>_ALGORITHMS`` — is
        optional, defaulting to :data:`DEFAULT_ALGORITHMS`. Whatever it names is
        still checked against :data:`ALLOWED_ALGORITHMS`, so "configurable" here
        means *choosing from a vetted set*, never widening it.
        """
        env_vars = {
            field: f"{prefix}_{suffix}" for field, suffix in _OIDC_ENV_SUFFIXES.items()
        }
        # ``Any``, not ``object``: these values are handed to pydantic as
        # ``**kwargs``, and it — not this loader — decides each field's type.
        values: dict[str, Any] = {
            field: environ.get(var, "") for field, var in env_vars.items()
        }
        missing = [env_vars[field] for field, value in values.items() if not value]
        if missing:
            raise AuthConfigError(
                f"OIDC auth is not configured: set {', '.join(sorted(missing))}"
            )

        algorithms = _parse_algorithms(
            environ.get(f"{prefix}_{_ALGORITHMS_SUFFIX}", "")
        )
        if algorithms is not None:
            values["algorithms"] = algorithms

        try:
            return cls(**values)
        except ValidationError as exc:
            # Every other failure this loader can produce is an AuthConfigError,
            # and a caller that catches the configuration error should not have
            # to catch pydantic's as well to cover the same class of mistake.
            raise AuthConfigError(f"OIDC auth is misconfigured: {exc}") from exc


def _parse_algorithms(raw: str) -> tuple[str, ...] | None:
    """A comma-separated algorithm list, or ``None`` when the variable is unset.

    ``None`` rather than the default tuple, so the caller can leave the field
    absent and let the model's own default apply — one definition of the
    default instead of two that can drift.

    Names are matched case-insensitively and mapped back to their canonical JWA
    spelling, because ``rs256`` in a deployment manifest is a typo rather than a
    request for a different algorithm, and failing on it teaches nothing. A name
    that matches nothing is passed through unchanged so the validator can name
    it in the error — silently dropping an unrecognised entry would let
    ``HS256`` be configured and then quietly ignored, which reads to whoever
    wrote it as though it had been accepted.

    Empty entries from a trailing comma are dropped; a variable that is *set*
    but names nothing usable reaches the validator as an empty tuple and is
    refused there, which is the fail-closed reading — an operator who set the
    variable meant something by it.
    """
    if not raw.strip():
        return None
    names = (name.strip() for name in raw.split(",") if name.strip())
    return tuple(_CANONICAL_ALGORITHMS.get(name.upper(), name) for name in names)


def _clean_subject(subject: str) -> str:
    """Return the subject, or reject one that cannot key an owner safely.

    ``require: ["sub"]`` only proves the claim is present, so PyJWT accepts an
    empty or whitespace ``sub``. That is the sole ownership key, with no RBAC
    above it, so two callers issued a blank ``sub`` would collapse into one
    owner. A control character is rejected for a second reason: the subject
    reaches a log line, and a newline would forge a second record there
    (CWE-117).
    """
    if not subject.strip():
        raise AuthenticationError("token subject is empty")
    if any(
        unicodedata.category(char) in _SUBJECT_REJECTED_CATEGORIES for char in subject
    ):
        raise AuthenticationError("token subject contains a control character")
    return subject


class SigningKeyClient(Protocol):
    """The slice of :class:`jwt.PyJWKClient` the verifier needs."""

    def get_signing_key_from_jwt(self, token: str) -> jwt.PyJWK: ...


class _CooldownSigningKeyClient:
    """Wrap :class:`jwt.PyJWKClient` so an unknown ``kid`` cannot force a fetch.

    The bare client re-fetches the whole key set on any ``kid`` it has not
    cached, reading that ``kid`` from the token's unverified header — so an
    unauthenticated caller sending a fresh random ``kid`` per request forces one
    outbound JWKS fetch each time. This resolves against the cached set first,
    and re-fetches at most once per :data:`JWKS_REFRESH_COOLDOWN_SECONDS`: an
    unknown ``kid`` fails fast while the cooldown holds, so a genuine key
    rotation still resolves on the next refresh, but a random-``kid`` flood no
    longer reaches the network.
    """

    def __init__(self, client: jwt.PyJWKClient, *, cooldown_seconds: float) -> None:
        self._client = client
        self._cooldown_seconds = cooldown_seconds
        self._last_refresh = float("-inf")

    def get_signing_key_from_jwt(self, token: str) -> jwt.PyJWK:
        kid = jwt.get_unverified_header(token).get("kid")
        key = self._match(kid, refresh=False)
        if key is not None:
            return key
        now = time.monotonic()
        if now - self._last_refresh < self._cooldown_seconds:
            raise jwt.PyJWKClientError(
                "no signing key matches the token's kid and JWKS refresh is on cooldown"
            )
        self._last_refresh = now
        key = self._match(kid, refresh=True)
        if key is None:
            raise jwt.PyJWKClientError(f"no signing key matches kid {kid!r}")
        return key

    def _match(self, kid: str | None, *, refresh: bool) -> jwt.PyJWK | None:
        """The signing key this token names, or ``None``.

        Narrowed to keys whose ``use`` is ``sig`` or unstated, which is what
        PyJWT's own client does. An issuer that publishes an encryption key in
        the same set is saying what that key is for, and verifying a signature
        with it uses the key against its stated purpose -- so the filter is the
        issuer's declaration being honoured rather than a guess about it.
        """
        keys = [
            key
            for key in self._client.get_jwk_set(refresh=refresh).keys
            if key.public_key_use in ("sig", None)
        ]
        if kid is None:
            return keys[0] if len(keys) == 1 else None
        return next((key for key in keys if key.key_id == kid), None)


class OidcJwtVerifier:
    """Validates an OIDC JWT (signature via JWKS, issuer, audience, expiry)."""

    def __init__(
        self, settings: OidcSettings, jwks_client: SigningKeyClient | None = None
    ) -> None:
        self._settings = settings
        self._jwks_client = jwks_client or _CooldownSigningKeyClient(
            jwt.PyJWKClient(settings.jwks_url, timeout=JWKS_TIMEOUT_SECONDS),
            cooldown_seconds=JWKS_REFRESH_COOLDOWN_SECONDS,
        )

    def verify(self, token: str) -> str:
        """Return the token subject, or raise :class:`AuthenticationError`."""
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                # The PyJWK itself, not its bare key. PyJWT then binds the
                # algorithm to the key it came with, and a token naming an
                # algorithm of another family -- RS256 against the EC key whose
                # `kid` it quotes -- is an InvalidAlgorithmError, which is a
                # PyJWTError. Handed the bare key instead, the same token
                # reaches a `prepare_key` that raises TypeError, which is not.
                signing_key,
                algorithms=list(self._settings.algorithms),
                issuer=self._settings.issuer,
                audience=self._settings.audience,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        # PyJWTError is the library's own contract and TypeError/ValueError are
        # what it raises out of a key it could not use. Both are the same answer
        # here: the caller sent a token this verifier will not accept. Catching
        # only the first let the second reach the 500 handler, which turned a
        # rejected token into an oracle for the JWKS -- one kid answering
        # differently from another is what the generic message exists to
        # prevent -- and wrote a traceback per request to the operator's log.
        except (jwt.PyJWTError, TypeError, ValueError) as exc:
            logger.info("rejected bearer token: %s", exc)
            raise AuthenticationError("invalid or expired credentials") from exc
        return _clean_subject(claims["sub"])


VerifierFactory = Callable[[Mapping[str, str]], TokenVerifier]

_FACTORIES: dict[str, VerifierFactory] = {
    "oidc": lambda env: OidcJwtVerifier(
        OidcSettings.from_env(env, prefix="ANALYSIS_OIDC")
    ),
}


def build_verifier(env: Mapping[str, str] = os.environ) -> TokenVerifier:
    """Select and construct the configured auth backend; fail closed.

    The provider is chosen by ``ANALYSIS_AUTH_PROVIDER`` at deploy time — never
    by the request — so an empty or unknown value raises rather than falling
    back to a weaker or absent check.
    """
    name = env.get("ANALYSIS_AUTH_PROVIDER", "").strip().lower()
    if not name:
        raise AuthConfigError("set ANALYSIS_AUTH_PROVIDER")
    try:
        factory = _FACTORIES[name]
    except KeyError:
        known = ", ".join(sorted(_FACTORIES))
        raise AuthConfigError(
            f"unknown auth provider {name!r}; known providers: {known}"
        ) from None
    return factory(env)
