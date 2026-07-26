"""OIDC JWT verifier and provider registry: verification, config, selection."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from stride_service.auth import (
    AuthConfigError,
    AuthenticationError,
    OidcJwtVerifier,
    OidcSettings,
    build_verifier,
)

ISSUER = "https://ping.example.com"
AUDIENCE = "stride-service"

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PRIVATE_PEM = _PRIVATE_KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
)
_PUBLIC_KEY = _PRIVATE_KEY.public_key()


class StaticJwksClient:
    """Serves the test public key regardless of the token's kid."""

    def get_signing_key_from_jwt(self, token: str):
        return SimpleNamespace(key=_PUBLIC_KEY)


def settings() -> OidcSettings:
    return OidcSettings(
        issuer=ISSUER, audience=AUDIENCE, jwks_url="https://ping.example.com/jwks"
    )


def verifier() -> OidcJwtVerifier:
    return OidcJwtVerifier(settings(), jwks_client=StaticJwksClient())


def make_token(**overrides) -> str:
    claims = {
        "sub": "alice",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "exp": datetime.now(UTC) + timedelta(minutes=5),
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, _PRIVATE_PEM, algorithm="RS256")


class TestOidcJwtVerifier:
    def test_valid_token_yields_subject(self):
        assert verifier().verify(make_token()) == "alice"

    def test_expired_token_rejected(self):
        token = make_token(exp=datetime.now(UTC) - timedelta(minutes=5))
        with pytest.raises(AuthenticationError):
            verifier().verify(token)

    def test_wrong_audience_rejected(self):
        with pytest.raises(AuthenticationError):
            verifier().verify(make_token(aud="another-service"))

    def test_wrong_issuer_rejected(self):
        with pytest.raises(AuthenticationError):
            verifier().verify(make_token(iss="https://evil.example.com"))

    def test_missing_subject_rejected(self):
        with pytest.raises(AuthenticationError):
            verifier().verify(make_token(sub=None))

    def test_hs256_token_rejected(self):
        claims = {
            "sub": "alice",
            "iss": ISSUER,
            "aud": AUDIENCE,
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        }
        token = jwt.encode(claims, "shared-secret".ljust(32, "x"), algorithm="HS256")
        with pytest.raises(AuthenticationError):
            verifier().verify(token)

    def test_garbage_token_rejected(self):
        with pytest.raises(AuthenticationError):
            verifier().verify("not-a-jwt")

    def test_rejection_message_is_generic(self):
        try:
            verifier().verify(make_token(aud="another-service"))
        except AuthenticationError as exc:
            assert str(exc) == "invalid or expired credentials"
        else:
            pytest.fail("expected AuthenticationError")


class TestOidcSettings:
    def test_from_env_reads_all_three_vars(self):
        env = {
            "STRIDE_OIDC_ISSUER": ISSUER,
            "STRIDE_OIDC_AUDIENCE": AUDIENCE,
            "STRIDE_OIDC_JWKS_URL": "https://ping.example.com/jwks",
        }
        loaded = OidcSettings.from_env(env, prefix="STRIDE_OIDC")
        assert loaded.issuer == ISSUER
        assert loaded.audience == AUDIENCE
        assert loaded.algorithms == ("RS256",)

    def test_from_env_fails_closed_when_missing(self):
        with pytest.raises(AuthConfigError, match="STRIDE_OIDC_AUDIENCE"):
            OidcSettings.from_env({"STRIDE_OIDC_ISSUER": ISSUER}, prefix="STRIDE_OIDC")

    def test_from_env_honours_prefix(self):
        env = {
            "STRIDE_ALT_ISSUER": ISSUER,
            "STRIDE_ALT_AUDIENCE": AUDIENCE,
            "STRIDE_ALT_JWKS_URL": "https://alt.example.com/jwks",
        }
        loaded = OidcSettings.from_env(env, prefix="STRIDE_ALT")
        assert loaded.jwks_url == "https://alt.example.com/jwks"


class TestBuildVerifier:
    def _oidc_env(self) -> dict[str, str]:
        return {
            "STRIDE_AUTH_PROVIDER": "oidc",
            "STRIDE_OIDC_ISSUER": ISSUER,
            "STRIDE_OIDC_AUDIENCE": AUDIENCE,
            "STRIDE_OIDC_JWKS_URL": "https://ping.example.com/jwks",
        }

    def test_builds_configured_provider(self):
        assert isinstance(build_verifier(self._oidc_env()), OidcJwtVerifier)

    def test_provider_selection_is_case_insensitive(self):
        env = self._oidc_env() | {"STRIDE_AUTH_PROVIDER": "  Oidc  "}
        assert isinstance(build_verifier(env), OidcJwtVerifier)

    def test_unset_provider_fails_closed(self):
        with pytest.raises(AuthConfigError, match="STRIDE_AUTH_PROVIDER"):
            build_verifier({})

    def test_unknown_provider_fails_closed(self):
        env = self._oidc_env() | {"STRIDE_AUTH_PROVIDER": "nope"}
        with pytest.raises(AuthConfigError, match="unknown auth provider 'nope'"):
            build_verifier(env)

    def test_selected_provider_still_needs_its_config(self):
        with pytest.raises(AuthConfigError, match="STRIDE_OIDC_ISSUER"):
            build_verifier({"STRIDE_AUTH_PROVIDER": "oidc"})
