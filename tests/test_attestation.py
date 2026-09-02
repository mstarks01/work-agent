"""Signed report attestations (#501)."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from analysis_service import verify_report
from analysis_service.attestation import (
    CANONICAL_VERSION,
    PAYLOAD_TYPE,
    Attestation,
    DuplicateKeyError,
    Keyring,
    KeyringError,
    VerificationKey,
    canonicalize,
    digest,
    load_keyring,
    load_report,
    sign,
    verify,
)
from tests.factories import sample_report

KEY = Ed25519PrivateKey.generate()
OTHER_KEY = Ed25519PrivateKey.generate()
KEY_ID = "deployment-2026-09"


def public(private: Ed25519PrivateKey) -> str:
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        PublicFormat,
    )

    raw = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def keyring(status="active", retired_at=None, key=KEY, key_id=KEY_ID) -> Keyring:
    return Keyring(
        version=1,
        keys=(
            VerificationKey(
                key_id=key_id,
                public_key=public(key),
                status=status,
                retired_at=retired_at,
            ),
        ),
    )


def report_json() -> dict:
    return json.loads(sample_report().model_dump_json())


class TestCanonicalization:
    def test_key_order_does_not_change_the_bytes(self):
        # A signature is over bytes, so "the report" has to mean exactly one
        # byte string however the JSON happened to be written.
        assert canonicalize({"b": 1, "a": 2}) == canonicalize({"a": 2, "b": 1})

    def test_insignificant_whitespace_does_not_either(self):
        loose = json.loads('{\n  "a" : 1\n}')
        assert canonicalize(loose) == canonicalize({"a": 1})

    def test_submitted_prose_is_not_escaped(self):
        # ensure_ascii off, so the bytes depend on the content rather than on a
        # serializer setting.
        assert "é".encode() in canonicalize({"a": "é"})

    def test_a_moved_value_moves_the_digest(self):
        report = report_json()
        moved = {**report, "disclaimer": report["disclaimer"] + "."}
        assert digest(report) != digest(moved)


class TestSigningAndVerifying:
    def test_a_signed_report_verifies(self):
        report = report_json()
        result = verify(report, sign(report, KEY, key_id=KEY_ID), keyring())
        assert result.verdict == "verified"
        assert result.key_id == KEY_ID
        assert result.trustworthy

    def test_the_pass_says_what_it_is_not(self):
        # The one verdict a reader is tempted to over-read.
        report = report_json()
        result = verify(report, sign(report, KEY, key_id=KEY_ID), keyring())
        assert "whether the findings are" in result.detail
        assert "certified" in result.detail

    @pytest.mark.parametrize(
        "field", ["disclaimer", "schema_version", "elements_analyzed"]
    )
    def test_any_covered_field_moving_invalidates(self, field):
        report = report_json()
        attestation = sign(report, KEY, key_id=KEY_ID)
        tampered = {**report, field: "tampered"}
        result = verify(tampered, attestation, keyring())
        assert result.verdict == "invalid"
        assert "moved" in result.detail

    def test_adding_a_field_invalidates_too(self):
        # Coverage is the whole artifact, which is why the signature is
        # detached: a field inside the report would have to be excluded from
        # its own coverage.
        report = report_json()
        attestation = sign(report, KEY, key_id=KEY_ID)
        assert (
            verify({**report, "extra": 1}, attestation, keyring()).verdict == "invalid"
        )

    def test_another_key_does_not_verify(self):
        report = report_json()
        attestation = sign(report, OTHER_KEY, key_id=KEY_ID)
        result = verify(report, attestation, keyring())
        assert result.verdict == "invalid"
        assert "does not check out" in result.detail

    def test_a_signature_cannot_be_re_attributed_to_another_key_id(self):
        # The key id is in the signed payload, so relabelling an envelope
        # breaks it rather than pointing a good signature at another signer.
        report = report_json()
        attestation = sign(report, KEY, key_id=KEY_ID)
        relabelled = attestation.model_copy(update={"key_id": "someone-else"})
        ring = keyring(key_id="someone-else")
        assert verify(report, relabelled, ring).verdict == "invalid"

    def test_a_signature_cannot_be_replayed_under_another_canonicalization(self):
        report = report_json()
        attestation = sign(report, KEY, key_id=KEY_ID)
        moved = attestation.model_copy(update={"canonical_version": 2})
        assert verify(report, moved, keyring()).verdict == "unsupported"

    def test_a_malformed_signature_is_invalid_not_a_crash(self):
        report = report_json()
        attestation = sign(report, KEY, key_id=KEY_ID)
        for bad in ("not base64!!", base64.b64encode(b"short").decode()):
            broken = attestation.model_copy(update={"signature": bad})
            assert verify(report, broken, keyring()).verdict == "invalid"


class TestTheStatesThatAreNotPassOrFail:
    def test_an_unsigned_report_is_unsigned_never_trusted(self):
        result = verify(report_json(), None, keyring())
        assert result.verdict == "unsigned"
        assert not result.trustworthy
        assert "nothing here establishes" in result.detail

    def test_an_unknown_key_says_the_signature_may_be_good_to_somebody_else(self):
        report = report_json()
        attestation = sign(report, KEY, key_id="a-key-we-do-not-hold")
        result = verify(report, attestation, keyring())
        assert result.verdict == "unknown-key"
        assert not result.trustworthy

    def test_an_unsupported_version_fails_clearly_rather_than_guessing(self):
        report = report_json()
        attestation = sign(report, KEY, key_id=KEY_ID).model_copy(
            update={"canonical_version": CANONICAL_VERSION + 1}
        )
        result = verify(report, attestation, keyring())
        assert result.verdict == "unsupported"
        assert "will not guess" in result.detail

    def test_an_unexpected_payload_type_is_refused(self):
        report = report_json()
        attestation = sign(report, KEY, key_id=KEY_ID).model_copy(
            update={"payload_type": "text/plain"}
        )
        assert verify(report, attestation, keyring()).verdict == "unsupported"


class TestRotationAndRevocation:
    def test_a_retired_key_still_verifies_what_it_signed_before_retiring(self):
        # Refusing it would invalidate every historical report on the day a
        # deployment rotated.
        report = report_json()
        attestation = sign(report, KEY, key_id=KEY_ID)
        later = datetime.now(UTC) + timedelta(days=1)
        result = verify(report, attestation, keyring("retired", retired_at=later))
        assert result.verdict == "verified"
        assert result.signer_status == "retired"

    def test_a_retired_key_does_not_verify_a_signature_claiming_a_later_time(self):
        report = report_json()
        attestation = sign(report, KEY, key_id=KEY_ID)
        earlier = datetime.now(UTC) - timedelta(days=1)
        result = verify(report, attestation, keyring("retired", retired_at=earlier))
        assert result.verdict == "invalid"
        assert "retired at" in result.detail

    def test_a_backdated_signing_time_breaks_the_signature(self):
        # The time is inside the signed bytes, so editing it on an existing
        # attestation cannot move the signature to before a retirement.
        report = report_json()
        attestation = sign(report, KEY, key_id=KEY_ID)
        retired_at = datetime.now(UTC) - timedelta(days=1)
        backdated = attestation.model_copy(
            update={"signed_at": retired_at - timedelta(days=1)}
        )
        result = verify(report, backdated, keyring("retired", retired_at=retired_at))
        assert result.verdict == "invalid"
        assert "retired at" not in result.detail

    def test_a_revoked_key_is_refused_even_where_the_signature_is_good(self):
        # A compromised key could backdate, so what it signed before anyone
        # noticed is no safer than what it signed after.
        report = report_json()
        attestation = sign(report, KEY, key_id=KEY_ID)
        result = verify(report, attestation, keyring("revoked"))
        assert result.verdict == "revoked"
        assert not result.trustworthy
        assert "including signatures that predate" in result.detail

    def test_revocation_is_checked_after_the_cryptography_not_before(self):
        # An operator needs to know whether a revoked key really signed this or
        # whether somebody is replaying its id over a forged document. Those are
        # different incidents.
        report = report_json()
        forged = sign(report, OTHER_KEY, key_id=KEY_ID)
        assert verify(report, forged, keyring("revoked")).verdict == "invalid"

    def test_a_retired_key_without_a_date_is_refused_at_load(self):
        with pytest.raises(ValueError, match="retired_at"):
            VerificationKey(key_id="k", public_key=public(KEY), status="retired")

    def test_an_active_key_may_not_carry_a_retirement_date(self):
        with pytest.raises(ValueError, match="only for a retired key"):
            VerificationKey(
                key_id="k",
                public_key=public(KEY),
                status="active",
                retired_at=datetime.now(UTC),
            )


class TestTheKeyring:
    def write(self, tmp_path, body: str):
        path = tmp_path / "keyring.toml"
        path.write_text(body, encoding="utf-8")
        return path

    def valid(self) -> str:
        return (
            "version = 1\n\n"
            "[[keys]]\n"
            f'key_id = "{KEY_ID}"\n'
            f'public_key = "{public(KEY)}"\n'
            'status = "active"\n'
        )

    def test_a_valid_keyring_loads(self, tmp_path):
        loaded = load_keyring(self.write(tmp_path, self.valid()))
        assert loaded.get(KEY_ID) is not None

    def test_an_empty_keyring_is_refused(self, tmp_path):
        # It would answer unknown-key to everything, which reads as a config
        # problem rather than as the absence of every key.
        with pytest.raises(KeyringError, match="names no keys"):
            load_keyring(self.write(tmp_path, "version = 1\nkeys = []\n"))

    def test_invalid_toml_is_refused(self, tmp_path):
        with pytest.raises(KeyringError, match="invalid TOML"):
            load_keyring(self.write(tmp_path, "not = = toml"))

    def test_a_missing_file_is_refused(self, tmp_path):
        with pytest.raises(KeyringError, match="cannot be read"):
            load_keyring(tmp_path / "nope.toml")

    def test_a_malformed_key_is_refused(self, tmp_path):
        body = self.valid().replace('status = "active"', 'status = "maybe"')
        with pytest.raises(KeyringError):
            load_keyring(self.write(tmp_path, body))

    def test_revoking_by_appending_a_second_entry_is_refused(self, tmp_path):
        # The fail-open this closes. An operator who revokes a key by adding a
        # revoked entry rather than editing the active one leaves two entries
        # under one id, and a first-match lookup returns whichever the file
        # lists first — so the revoked key verified. The keyring cannot tell
        # which entry was meant, so it refuses rather than resolving the
        # ambiguity in the direction that grants trust.
        body = self.valid() + (
            "\n[[keys]]\n"
            f'key_id = "{KEY_ID}"\n'
            f'public_key = "{public(KEY)}"\n'
            'status = "revoked"\n'
        )
        with pytest.raises(KeyringError, match="named more than once"):
            load_keyring(self.write(tmp_path, body))


class TestTheStandaloneVerifier:
    def files(self, tmp_path, *, attest=True, status="active"):
        report = report_json()
        (tmp_path / "report.json").write_text(json.dumps(report), encoding="utf-8")
        (tmp_path / "keyring.toml").write_text(
            "version = 1\n\n[[keys]]\n"
            f'key_id = "{KEY_ID}"\npublic_key = "{public(KEY)}"\n'
            f'status = "{status}"\n',
            encoding="utf-8",
        )
        if attest:
            (tmp_path / "sig.json").write_text(
                sign(report, KEY, key_id=KEY_ID).model_dump_json(), encoding="utf-8"
            )
        return tmp_path

    def run(self, tmp_path, *extra):
        return verify_report.main(
            [
                str(tmp_path / "report.json"),
                "--keyring",
                str(tmp_path / "keyring.toml"),
                *extra,
            ]
        )

    def test_it_works_from_json_alone(self, tmp_path, capsys):
        # No pydantic model is loaded, so a report from a build whose schema has
        # since moved still verifies.
        self.files(tmp_path)
        assert self.run(tmp_path, "--attestation", str(tmp_path / "sig.json")) == 0
        assert "verified" in capsys.readouterr().out

    def test_a_pass_prints_the_caveat(self, tmp_path, capsys):
        self.files(tmp_path)
        self.run(tmp_path, "--attestation", str(tmp_path / "sig.json"))
        assert "authenticity, not correctness" in capsys.readouterr().out

    def test_every_verdict_has_its_own_exit_code(self):
        # A script must be able to tell "we do not sign" from "this key was
        # compromised" without parsing prose.
        codes = verify_report.EXIT_CODES
        assert codes["verified"] == 0
        assert len(set(codes.values())) == len(codes)
        assert all(
            code != 0 for verdict, code in codes.items() if verdict != "verified"
        )

    def test_an_omitted_attestation_exits_unsigned_not_zero(self, tmp_path):
        self.files(tmp_path, attest=False)
        assert self.run(tmp_path) == verify_report.EXIT_CODES["unsigned"]

    def test_a_revoked_key_exits_with_its_own_code(self, tmp_path):
        self.files(tmp_path, status="revoked")
        code = self.run(tmp_path, "--attestation", str(tmp_path / "sig.json"))
        assert code == verify_report.EXIT_CODES["revoked"]

    def test_a_tampered_report_exits_invalid(self, tmp_path):
        self.files(tmp_path)
        report = json.loads((tmp_path / "report.json").read_text())
        report["disclaimer"] = "edited"
        (tmp_path / "report.json").write_text(json.dumps(report), encoding="utf-8")
        code = self.run(tmp_path, "--attestation", str(tmp_path / "sig.json"))
        assert code == verify_report.EXIT_CODES["invalid"]

    def test_an_unusable_keyring_is_a_usage_error_not_a_verdict(self, tmp_path):
        self.files(tmp_path)
        (tmp_path / "keyring.toml").write_text("bad = = toml", encoding="utf-8")
        assert self.run(tmp_path, "--attestation", str(tmp_path / "sig.json")) == 2


def test_the_signed_payload_binds_everything_it_must():
    """A signature covers the digest, the version, the type, the key id and the time."""
    attestation = Attestation(
        canonical_version=CANONICAL_VERSION,
        payload_type=PAYLOAD_TYPE,
        report_sha256="a" * 64,
        key_id=KEY_ID,
        signature="x",
        signed_at=datetime.now(UTC),
    )
    payload = json.loads(attestation.signed_payload())
    assert set(payload) == {
        "canonical_version",
        "key_id",
        "payload_type",
        "report_sha256",
        "signed_at",
    }


class TestTwoParsersCannotDisagreeAboutTheFile:
    """A signature covers the canonical form of the *parsed* document.

    That is what lets a verifier check a report without this project's models,
    and it is the right trade. Its cost is that parsing is where two readers can
    disagree: ``json`` keeps the last of a repeated key and other parsers keep
    the first, so one file can say two things and verify as the harmless one.
    """

    def test_a_repeated_key_is_refused_rather_than_resolved(self):
        forged = (
            '{"id": "r1", "summary": "CRITICAL RCE, unauthenticated",'
            ' "summary": "no issues found"}'
        )

        with pytest.raises(DuplicateKeyError, match="'summary' appears twice"):
            load_report(forged)

    def test_a_repeated_key_deeper_in_the_document_is_refused_too(self):
        forged = '{"analyses": [{"framework": "stride", "framework": "asvs"}]}'

        with pytest.raises(DuplicateKeyError, match="'framework' appears twice"):
            load_report(forged)

    def test_an_ordinary_report_still_loads(self):
        report = sample_report().model_dump(mode="json")

        assert load_report(json.dumps(report)) == report

    def test_an_escape_is_the_same_document_and_still_verifies(self):
        """The other half of this class is not a forgery: `\\u0041` and `A` are
        one string, so they canonicalize alike and mean the same thing."""
        assert load_report('{"id": "\\u0041"}') == {"id": "A"}
        assert canonicalize({"id": "A"}) == canonicalize(
            load_report('{"id": "\\u0041"}')
        )
