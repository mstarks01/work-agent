"""Ping JWT verifier: signature, issuer, audience, expiry, and config."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from stride_service.auth import (
    AuthConfigError,
    AuthenticationError,
    PingAuthSettings,
    PingJwtVerifier,
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


def settings() -> PingAuthSettings:
    return PingAuthSettings(
        issuer=ISSUER, audience=AUDIENCE, jwks_url="https://ping.example.com/jwks"
    )


def verifier() -> PingJwtVerifier:
    return PingJwtVerifier(settings(), jwks_client=StaticJwksClient())


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


class TestPingJwtVerifier:
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


class TestPingAuthSettings:
    def test_from_env_reads_all_three_vars(self):
        env = {
            "STRIDE_PING_ISSUER": ISSUER,
            "STRIDE_PING_AUDIENCE": AUDIENCE,
            "STRIDE_PING_JWKS_URL": "https://ping.example.com/jwks",
        }
        loaded = PingAuthSettings.from_env(env)
        assert loaded.issuer == ISSUER
        assert loaded.audience == AUDIENCE
        assert loaded.algorithms == ("RS256",)

    def test_from_env_fails_closed_when_missing(self):
        with pytest.raises(AuthConfigError, match="STRIDE_PING_AUDIENCE"):
            PingAuthSettings.from_env({"STRIDE_PING_ISSUER": ISSUER})
