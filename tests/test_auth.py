"""OIDC JWT verifier and provider registry: verification, config, selection."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import ValidationError

from stride_service.auth import (
    ALLOWED_ALGORITHMS,
    DEFAULT_ALGORITHMS,
    JWKS_TIMEOUT_SECONDS,
    AuthConfigError,
    AuthenticationError,
    OidcJwtVerifier,
    OidcSettings,
    build_verifier,
)

ISSUER = "https://idp.example.com"
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
        issuer=ISSUER, audience=AUDIENCE, jwks_url="https://idp.example.com/jwks"
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
            "STRIDE_OIDC_JWKS_URL": "https://idp.example.com/jwks",
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


class TestSigningAlgorithms:
    """Configurable, from a vetted set — never widened by configuration.

    RS256-only was a historical assumption rather than a decision
    ([#116](https://github.com/mstarks01/work-agent/issues/116)): the field
    existed but nothing read it from the environment, so a standards-compliant
    IdP signing ES256 could not be pointed at this service at all. It is now
    deploy-time configuration, and the allowlist is what keeps "configurable"
    from meaning "whatever the operator or the IdP says".
    """

    def _env(self, **extra: str) -> dict[str, str]:
        return {
            "STRIDE_OIDC_ISSUER": ISSUER,
            "STRIDE_OIDC_AUDIENCE": AUDIENCE,
            "STRIDE_OIDC_JWKS_URL": "https://idp.example.com/jwks",
        } | extra

    def test_the_default_is_unchanged_when_nothing_is_configured(self):
        # A deployment that sets nothing must verify exactly what it verified
        # before this knob existed.
        loaded = OidcSettings.from_env(self._env(), prefix="STRIDE_OIDC")
        assert loaded.algorithms == DEFAULT_ALGORITHMS == ("RS256",)

    def test_an_allowed_algorithm_can_be_configured(self):
        env = self._env(STRIDE_OIDC_ALGORITHMS="ES256")
        loaded = OidcSettings.from_env(env, prefix="STRIDE_OIDC")
        assert loaded.algorithms == ("ES256",)

    def test_several_algorithms_can_be_configured(self):
        env = self._env(STRIDE_OIDC_ALGORITHMS="RS256, ES256")
        loaded = OidcSettings.from_env(env, prefix="STRIDE_OIDC")
        assert loaded.algorithms == ("RS256", "ES256")

    def test_names_are_matched_case_insensitively(self):
        # 'rs256' in a deployment manifest is a typo, not a different algorithm.
        env = self._env(STRIDE_OIDC_ALGORITHMS="rs256")
        assert OidcSettings.from_env(env, prefix="STRIDE_OIDC").algorithms == ("RS256",)

    def test_eddsa_keeps_its_mixed_case_spelling(self):
        # The one non-upper-case name in the set; upper-casing input blindly
        # would make it the one algorithm nobody could configure.
        env = self._env(STRIDE_OIDC_ALGORITHMS="eddsa")
        assert OidcSettings.from_env(env, prefix="STRIDE_OIDC").algorithms == ("EdDSA",)

    @pytest.mark.parametrize("algorithm", ["HS256", "HS384", "HS512"])
    def test_symmetric_algorithms_are_refused(self, algorithm):
        """The key-confusion attack, refused at configuration time.

        JWKS keys are *public*. A verifier willing to accept an HMAC algorithm
        beside them will happily verify a token an attacker signed using the
        public key as the shared secret, because it treated a verification key
        as a signing key.
        """
        env = self._env(STRIDE_OIDC_ALGORITHMS=algorithm)
        with pytest.raises(AuthConfigError, match="unsupported signing algorithm"):
            OidcSettings.from_env(env, prefix="STRIDE_OIDC")

    def test_the_none_algorithm_is_refused(self):
        env = self._env(STRIDE_OIDC_ALGORITHMS="none")
        with pytest.raises(AuthConfigError, match="unsupported signing algorithm"):
            OidcSettings.from_env(env, prefix="STRIDE_OIDC")

    def test_one_bad_entry_rejects_the_whole_list(self):
        # Not "drop the bad one and carry on": an operator who wrote HS256 is
        # owed an error, and a silently-ignored entry reads as an accepted one.
        env = self._env(STRIDE_OIDC_ALGORITHMS="RS256,HS256")
        with pytest.raises(AuthConfigError, match="HS256"):
            OidcSettings.from_env(env, prefix="STRIDE_OIDC")

    def test_a_variable_set_to_only_separators_fails_closed(self):
        # Set-but-empty is an operator who meant something; it must not silently
        # fall back to the default.
        env = self._env(STRIDE_OIDC_ALGORITHMS=",,")
        with pytest.raises(AuthConfigError, match="at least one"):
            OidcSettings.from_env(env, prefix="STRIDE_OIDC")

    def test_an_unset_variable_is_not_the_same_as_an_empty_one(self):
        env = self._env(STRIDE_OIDC_ALGORITHMS="   ")
        assert OidcSettings.from_env(env, prefix="STRIDE_OIDC").algorithms == ("RS256",)

    def test_the_allowlist_admits_no_symmetric_or_unsigned_algorithm(self):
        # Guards the guard: the constant itself, so widening it is a deliberate
        # edit to a test rather than an unnoticed addition.
        assert not any(name.startswith("HS") for name in ALLOWED_ALGORITHMS)
        assert "none" not in {name.lower() for name in ALLOWED_ALGORITHMS}

    def test_a_configured_algorithm_reaches_the_verifier(self):
        # The knob has to arrive where jwt.decode reads it; a setting that
        # validates and is then dropped is worse than no setting.
        configured = OidcSettings(
            issuer=ISSUER,
            audience=AUDIENCE,
            jwks_url="https://idp.example.com/jwks",
            algorithms=("ES256",),
        )
        checker = OidcJwtVerifier(configured, jwks_client=StaticJwksClient())
        # The token is RS256-signed and the verifier now accepts only ES256, so
        # rejection is the proof that the configured list is the one in force.
        with pytest.raises(AuthenticationError):
            checker.verify(make_token())


class TestBuildVerifier:
    def _oidc_env(self) -> dict[str, str]:
        return {
            "STRIDE_AUTH_PROVIDER": "oidc",
            "STRIDE_OIDC_ISSUER": ISSUER,
            "STRIDE_OIDC_AUDIENCE": AUDIENCE,
            "STRIDE_OIDC_JWKS_URL": "https://idp.example.com/jwks",
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


class TestJwksTransport:
    def test_plaintext_jwks_url_is_refused(self):
        # The signing keys fetched from here are the whole basis on which a
        # token is trusted, so plaintext transport makes verification forgeable
        # by anyone on the path.
        with pytest.raises(ValidationError, match="https"):
            OidcSettings(
                issuer=ISSUER,
                audience=AUDIENCE,
                jwks_url="http://idp.example.com/jwks",
            )

    def test_plaintext_jwks_url_from_env_is_an_auth_config_error(self):
        # Every other failure this loader produces is an AuthConfigError, and a
        # caller covering configuration mistakes should not have to catch
        # pydantic's exception as well to cover the same class of mistake.
        env = {
            "STRIDE_OIDC_ISSUER": ISSUER,
            "STRIDE_OIDC_AUDIENCE": AUDIENCE,
            "STRIDE_OIDC_JWKS_URL": "http://idp.example.com/jwks",
        }
        with pytest.raises(AuthConfigError, match="https"):
            OidcSettings.from_env(env, prefix="STRIDE_OIDC")

    def test_the_jwks_fetch_carries_this_services_timeout(self):
        # Stated rather than inherited: PyJWKClient's own default is 30s, which
        # is a request-path stall an unreachable provider can impose.
        verifier = OidcJwtVerifier(settings())

        assert verifier._jwks_client.timeout == JWKS_TIMEOUT_SECONDS
