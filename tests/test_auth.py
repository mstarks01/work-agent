"""OIDC JWT verifier and provider registry: verification, config, selection."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from pydantic import ValidationError

from analysis_service.auth import (
    ALLOWED_ALGORITHMS,
    DEFAULT_ALGORITHMS,
    JWKS_TIMEOUT_SECONDS,
    AuthConfigError,
    AuthenticationError,
    OidcJwtVerifier,
    OidcSettings,
    _CooldownSigningKeyClient,
    build_verifier,
)

ISSUER = "https://idp.example.com"
AUDIENCE = "analysis-service"

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PRIVATE_PEM = _PRIVATE_KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
)
_PUBLIC_KEY = _PRIVATE_KEY.public_key()


def _jwk(public_key, algorithm: str) -> jwt.PyJWK:
    """The public key as a PyJWK, which is what a real JWKS client returns.

    A stand-in that answered a bare key would let the verifier be tested against
    a contract the live client does not honour, and the difference is the one
    that matters here: PyJWT binds an algorithm to a PyJWK and not to a bare
    key.
    """
    if algorithm == "RS256":
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(public_key, as_dict=True)
    else:
        jwk = jwt.algorithms.ECAlgorithm.to_jwk(public_key, as_dict=True)
    return jwt.PyJWK({**jwk, "alg": algorithm, "use": "sig"})


class StaticJwksClient:
    """Serves the test public key regardless of the token's kid."""

    def get_signing_key_from_jwt(self, token: str) -> jwt.PyJWK:
        return _jwk(_PUBLIC_KEY, "RS256")


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

    def test_empty_subject_rejected(self):
        # ``require: ["sub"]`` only proves presence, so an empty subject passes
        # PyJWT. It is the sole ownership key, so two callers issued a blank sub
        # would collapse into one owner.
        with pytest.raises(AuthenticationError):
            verifier().verify(make_token(sub=""))

    def test_whitespace_subject_rejected(self):
        with pytest.raises(AuthenticationError):
            verifier().verify(make_token(sub="   "))

    def test_control_character_subject_rejected(self):
        # A newline in the subject would forge a second record on the log line
        # the subject reaches (CWE-117).
        with pytest.raises(AuthenticationError):
            verifier().verify(make_token(sub="alice\nCRITICAL forged"))

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
            "ANALYSIS_OIDC_ISSUER": ISSUER,
            "ANALYSIS_OIDC_AUDIENCE": AUDIENCE,
            "ANALYSIS_OIDC_JWKS_URL": "https://idp.example.com/jwks",
        }
        loaded = OidcSettings.from_env(env, prefix="ANALYSIS_OIDC")
        assert loaded.issuer == ISSUER
        assert loaded.audience == AUDIENCE
        assert loaded.algorithms == ("RS256",)

    def test_from_env_fails_closed_when_missing(self):
        with pytest.raises(AuthConfigError, match="ANALYSIS_OIDC_AUDIENCE"):
            OidcSettings.from_env(
                {"ANALYSIS_OIDC_ISSUER": ISSUER}, prefix="ANALYSIS_OIDC"
            )

    def test_from_env_honours_prefix(self):
        env = {
            "ANALYSIS_ALT_ISSUER": ISSUER,
            "ANALYSIS_ALT_AUDIENCE": AUDIENCE,
            "ANALYSIS_ALT_JWKS_URL": "https://alt.example.com/jwks",
        }
        loaded = OidcSettings.from_env(env, prefix="ANALYSIS_ALT")
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
            "ANALYSIS_OIDC_ISSUER": ISSUER,
            "ANALYSIS_OIDC_AUDIENCE": AUDIENCE,
            "ANALYSIS_OIDC_JWKS_URL": "https://idp.example.com/jwks",
        } | extra

    def test_the_default_is_unchanged_when_nothing_is_configured(self):
        # A deployment that sets nothing must verify exactly what it verified
        # before this knob existed.
        loaded = OidcSettings.from_env(self._env(), prefix="ANALYSIS_OIDC")
        assert loaded.algorithms == DEFAULT_ALGORITHMS == ("RS256",)

    def test_an_allowed_algorithm_can_be_configured(self):
        env = self._env(ANALYSIS_OIDC_ALGORITHMS="ES256")
        loaded = OidcSettings.from_env(env, prefix="ANALYSIS_OIDC")
        assert loaded.algorithms == ("ES256",)

    def test_several_algorithms_can_be_configured(self):
        env = self._env(ANALYSIS_OIDC_ALGORITHMS="RS256, ES256")
        loaded = OidcSettings.from_env(env, prefix="ANALYSIS_OIDC")
        assert loaded.algorithms == ("RS256", "ES256")

    def test_names_are_matched_case_insensitively(self):
        # 'rs256' in a deployment manifest is a typo, not a different algorithm.
        env = self._env(ANALYSIS_OIDC_ALGORITHMS="rs256")
        assert OidcSettings.from_env(env, prefix="ANALYSIS_OIDC").algorithms == (
            "RS256",
        )

    def test_eddsa_keeps_its_mixed_case_spelling(self):
        # The one non-upper-case name in the set; upper-casing input blindly
        # would make it the one algorithm nobody could configure.
        env = self._env(ANALYSIS_OIDC_ALGORITHMS="eddsa")
        assert OidcSettings.from_env(env, prefix="ANALYSIS_OIDC").algorithms == (
            "EdDSA",
        )

    @pytest.mark.parametrize("algorithm", ["HS256", "HS384", "HS512"])
    def test_symmetric_algorithms_are_refused(self, algorithm):
        """The key-confusion attack, refused at configuration time.

        JWKS keys are *public*. A verifier willing to accept an HMAC algorithm
        beside them will happily verify a token an attacker signed using the
        public key as the shared secret, because it treated a verification key
        as a signing key.
        """
        env = self._env(ANALYSIS_OIDC_ALGORITHMS=algorithm)
        with pytest.raises(AuthConfigError, match="unsupported signing algorithm"):
            OidcSettings.from_env(env, prefix="ANALYSIS_OIDC")

    def test_the_none_algorithm_is_refused(self):
        env = self._env(ANALYSIS_OIDC_ALGORITHMS="none")
        with pytest.raises(AuthConfigError, match="unsupported signing algorithm"):
            OidcSettings.from_env(env, prefix="ANALYSIS_OIDC")

    def test_one_bad_entry_rejects_the_whole_list(self):
        # Not "drop the bad one and carry on": an operator who wrote HS256 is
        # owed an error, and a silently-ignored entry reads as an accepted one.
        env = self._env(ANALYSIS_OIDC_ALGORITHMS="RS256,HS256")
        with pytest.raises(AuthConfigError, match="HS256"):
            OidcSettings.from_env(env, prefix="ANALYSIS_OIDC")

    def test_a_variable_set_to_only_separators_fails_closed(self):
        # Set-but-empty is an operator who meant something; it must not silently
        # fall back to the default.
        env = self._env(ANALYSIS_OIDC_ALGORITHMS=",,")
        with pytest.raises(AuthConfigError, match="at least one"):
            OidcSettings.from_env(env, prefix="ANALYSIS_OIDC")

    def test_an_unset_variable_is_not_the_same_as_an_empty_one(self):
        env = self._env(ANALYSIS_OIDC_ALGORITHMS="   ")
        assert OidcSettings.from_env(env, prefix="ANALYSIS_OIDC").algorithms == (
            "RS256",
        )

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
            "ANALYSIS_AUTH_PROVIDER": "oidc",
            "ANALYSIS_OIDC_ISSUER": ISSUER,
            "ANALYSIS_OIDC_AUDIENCE": AUDIENCE,
            "ANALYSIS_OIDC_JWKS_URL": "https://idp.example.com/jwks",
        }

    def test_builds_configured_provider(self):
        assert isinstance(build_verifier(self._oidc_env()), OidcJwtVerifier)

    def test_provider_selection_is_case_insensitive(self):
        env = self._oidc_env() | {"ANALYSIS_AUTH_PROVIDER": "  Oidc  "}
        assert isinstance(build_verifier(env), OidcJwtVerifier)

    def test_unset_provider_fails_closed(self):
        with pytest.raises(AuthConfigError, match="ANALYSIS_AUTH_PROVIDER"):
            build_verifier({})

    def test_unknown_provider_fails_closed(self):
        env = self._oidc_env() | {"ANALYSIS_AUTH_PROVIDER": "nope"}
        with pytest.raises(AuthConfigError, match="unknown auth provider 'nope'"):
            build_verifier(env)

    def test_selected_provider_still_needs_its_config(self):
        with pytest.raises(AuthConfigError, match="ANALYSIS_OIDC_ISSUER"):
            build_verifier({"ANALYSIS_AUTH_PROVIDER": "oidc"})


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
            "ANALYSIS_OIDC_ISSUER": ISSUER,
            "ANALYSIS_OIDC_AUDIENCE": AUDIENCE,
            "ANALYSIS_OIDC_JWKS_URL": "http://idp.example.com/jwks",
        }
        with pytest.raises(AuthConfigError, match="https"):
            OidcSettings.from_env(env, prefix="ANALYSIS_OIDC")

    def test_the_jwks_fetch_carries_this_services_timeout(self):
        # Stated rather than inherited: PyJWKClient's own default is 30s, which
        # is a request-path stall an unreachable provider can impose. The client
        # is wrapped by the refresh-cooldown guard, so the timeout sits on the
        # PyJWKClient it holds.
        verifier = OidcJwtVerifier(settings())

        assert verifier._jwks_client._client.timeout == JWKS_TIMEOUT_SECONDS


class _FakeKey:
    def __init__(self, kid: str):
        self.key_id = kid
        self.key = _PUBLIC_KEY


class _FakeJwkSet:
    def __init__(self, keys):
        self.keys = keys


class _CountingJwkClient:
    """A PyJWKClient stand-in that counts only the refreshing fetches."""

    timeout = JWKS_TIMEOUT_SECONDS

    def __init__(self, kids):
        self._kids = list(kids)
        self.refreshes = 0

    def get_jwk_set(self, refresh: bool = False) -> _FakeJwkSet:
        if refresh:
            self.refreshes += 1
        return _FakeJwkSet([_FakeKey(kid) for kid in self._kids])


class TestRefreshCooldown:
    """An unknown kid cannot force a JWKS refetch on every request."""

    @staticmethod
    def _token(kid: str) -> str:
        return jwt.encode(
            {"sub": "alice"}, _PRIVATE_PEM, algorithm="RS256", headers={"kid": kid}
        )

    def test_unknown_kid_refetches_at_most_once_per_cooldown(self):
        client = _CooldownSigningKeyClient(
            _CountingJwkClient(["known"]), cooldown_seconds=3600.0
        )
        with pytest.raises(jwt.PyJWKClientError):
            client.get_signing_key_from_jwt(self._token("random-1"))
        with pytest.raises(jwt.PyJWKClientError):
            client.get_signing_key_from_jwt(self._token("random-2"))
        assert client._client.refreshes == 1

    def test_known_kid_resolves_from_the_cached_set(self):
        counting = _CountingJwkClient(["known"])
        client = _CooldownSigningKeyClient(counting, cooldown_seconds=3600.0)
        key = client.get_signing_key_from_jwt(self._token("known"))
        assert key.key_id == "known"
        assert counting.refreshes == 0


class TestAKeyOfTheWrongFamily:
    """A JWKS carrying two key families while two algorithms are configured is
    an ordinary state during a rotation, and the docs describe it.

    The verifier looks a key up by ``kid`` alone, so a token can name the EC
    key's ``kid`` and the RS256 algorithm. That has to be one more rejected
    token. Handed a bare key, PyJWT raised ``TypeError`` out of ``prepare_key``
    instead, which is not a ``PyJWTError``: it escaped the verifier, reached the
    500 handler, and made one ``kid`` answer differently from another -- the
    oracle the single generic message exists to prevent.
    """

    def _ec_verifier(self) -> OidcJwtVerifier:
        ec_key = ec.generate_private_key(ec.SECP256R1())

        class MixedJwksClient:
            """Answers with an EC key whatever algorithm the token names."""

            def get_signing_key_from_jwt(self, token: str) -> jwt.PyJWK:
                return _jwk(ec_key.public_key(), "ES256")

        configured = OidcSettings(
            issuer=ISSUER,
            audience=AUDIENCE,
            jwks_url="https://idp.example.com/jwks",
            algorithms=("RS256", "ES256"),
        )
        return OidcJwtVerifier(configured, jwks_client=MixedJwksClient())

    def test_it_is_a_rejected_token_and_not_an_internal_error(self):
        with pytest.raises(AuthenticationError):
            self._ec_verifier().verify(make_token())

    def test_the_message_is_the_same_one_every_other_refusal_gives(self):
        """The whole point: this ``kid`` must be indistinguishable from any
        other rejected token."""
        try:
            self._ec_verifier().verify(make_token())
        except AuthenticationError as exc:
            assert str(exc) == "invalid or expired credentials"
        else:
            pytest.fail("expected AuthenticationError")
