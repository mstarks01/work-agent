"""The Control document and the evidence rules (ADRs 0123, 0124 and 0127)."""

from __future__ import annotations

import time
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from analysis_service.controls import (
    MAX_CONTROL_ALIASES,
    MAX_CONTROL_FACTS,
    MAX_CONTROL_ROWS,
    ControlDocument,
    ControlLimits,
    EvidenceRefusal,
    ScopeType,
    control_refusal,
    credential_hit,
)
from analysis_service.fact_answers import STORED
from analysis_service.frameworks import PACKAGES
from analysis_service.owner_files import NO_TEXT, NOT_PLAIN_TEXT, NOT_UTF_8
from analysis_service.sources import text_digest
from analysis_service.system_model import ElementPrefix

CONTROL_ID = "control-" + "c" * 32
EVIDENCE_ID = "evidence-" + "e" * 32


def fact(attribute: str = "encryption_at_rest", value: str = "AES-256") -> dict:
    return {"key": ["", attribute, "", "", "", ""], "value": value}


def row(unit: str = "V11.2", framework: str = "asvs", version: str = "5.0.0") -> dict:
    return {"framework": framework, "unit": unit, "version": version}


def evidence(name: str = "rds.tf", **changed: Any) -> dict:
    return {
        "id": EVIDENCE_ID,
        "name": name,
        "class": "code",
        "sha256": "0" * 64,
        "size_bytes": 10,
        **changed,
    }


def document(**changed: Any) -> dict:
    return {
        "id": CONTROL_ID,
        "name": "Orders DB encrypted at rest",
        "scope": {"type": "store", "name": "Orders DB", "aliases": ["orders database"]},
        "facts": [fact()],
        "rows": [row()],
        "evidence": [evidence()],
        **changed,
    }


def refusal(**changed: Any) -> str:
    with pytest.raises(ValidationError) as refused:
        ControlDocument.model_validate(document(**changed))
    return str(refused.value)


class TestTheDocument:
    def test_a_document_round_trips_through_its_json(self):
        held = ControlDocument.model_validate(document())
        assert ControlDocument.model_validate_json(held.model_dump_json()) == held
        assert held.evidence[0].evidence_class == "code"
        assert held.model_dump()["evidence"][0]["class"] == "code"

    def test_an_application_scope_needs_no_name(self):
        scope = {"type": "application", "name": "", "aliases": []}
        held = ControlDocument.model_validate(document(scope=scope))
        assert held.scope.model_dump() == scope

    @pytest.mark.parametrize("name", ["", "   "])
    def test_a_blank_name_is_refused(self, name):
        assert "Give the Control a name." in refusal(name=name)

    def test_a_flow_scope_is_refused(self):
        scope = {"type": "flow", "name": "Orders", "aliases": []}
        assert "scope.type" in refusal(scope=scope)

    @pytest.mark.parametrize("name", ["", "  "])
    def test_a_named_scope_with_no_name_is_refused(self, name):
        scope = {"type": "boundary", "name": name, "aliases": []}
        assert (
            "Name what the Control protects, or choose the whole application."
            in refusal(scope=scope)
        )

    def test_an_application_scope_with_a_name_is_refused(self):
        scope = {"type": "application", "name": "Shop", "aliases": []}
        assert "The whole application takes no name and no alias." in refusal(
            scope=scope
        )

    def test_a_scope_name_with_no_letter_or_digit_is_refused(self):
        scope = {"type": "process", "name": "!!!", "aliases": []}
        assert "“!!!” has no letter or digit" in refusal(scope=scope)

    def test_neither_a_fact_nor_a_row_is_refused(self):
        assert (
            "Add at least one fact or one row. A Control with neither changes"
            " nothing." in refusal(facts=[], rows=[])
        )

    def test_a_fact_or_a_row_alone_is_enough(self):
        ControlDocument.model_validate(document(rows=[]))
        ControlDocument.model_validate(document(facts=[]))

    def test_a_fact_twice_is_refused(self):
        twice = [fact(), fact(value="AES-128")]
        assert (
            "The fact “encryption_at_rest” appears twice. One Control writes one"
            " value for one fact." in refusal(facts=twice)
        )

    def test_a_fact_with_an_element_id_is_refused(self):
        placed = {"key": ["store:orders-db", "encryption_at_rest", "", "", "", ""]}
        assert "names no element" in refusal(facts=[{**placed, "value": "AES"}])

    def test_a_row_twice_is_refused(self):
        assert "The row V11.2 appears twice." in refusal(rows=[row(), row()])

    @pytest.mark.parametrize(
        ("framework", "unit", "reason"),
        [
            ("nist", "AC-2", "is not one this build carries"),
            ("asvs", "V99.1", "is not a unit of framework 'asvs'"),
            ("stride", "V11.2", "is not a unit of framework 'stride'"),
        ],
    )
    def test_a_row_outside_the_packages_set_is_refused(self, framework, unit, reason):
        assert reason in refusal(rows=[row(unit, framework)])

    def test_a_stored_row_reads_back_whatever_the_package_now_holds(self):
        """A later catalog can drop a unit, and the owner's record still reads."""
        held = ControlDocument.model_validate(
            document(rows=[row("V99.1")]), context=STORED
        )
        assert held.rows[0].unit == "V99.1"

    @pytest.mark.parametrize(
        "aliases",
        [["edge", "edge"], ["Orders DB"], ["orders-db"], ["edge", "Edge!"]],
    )
    def test_a_repeated_alias_is_refused(self, aliases):
        scope = {"type": "store", "name": "Orders DB", "aliases": aliases}
        assert "appears twice in what the Control protects" in refusal(scope=scope)

    def test_an_evidence_name_twice_is_refused(self):
        files = [evidence(), evidence(id="evidence-" + "f" * 32)]
        assert "You already have an evidence file named “rds.tf”" in refusal(
            evidence=files
        )

    def test_an_evidence_id_twice_is_refused(self):
        files = [evidence(), evidence(name="other.tf")]
        assert "appears twice" in refusal(evidence=files)

    @pytest.mark.parametrize("name", ["", " ", "a\nb"])
    def test_an_evidence_name_that_is_not_one_plain_line_is_refused(self, name):
        assert "evidence.0.name" in refusal(evidence=[evidence(name)])

    def test_an_evidence_class_outside_the_set_is_refused(self):
        assert "evidence.0.class" in refusal(evidence=[evidence(**{"class": "prose"})])

    @pytest.mark.parametrize(
        ("field", "items"),
        [
            (
                "facts",
                [
                    {"key": ["", "", "", f"subject {at}", "", ""], "value": "yes"}
                    for at in range(MAX_CONTROL_FACTS + 1)
                ],
            ),
            (
                "rows",
                [row(unit.id) for unit in PACKAGES["asvs"].units][
                    : MAX_CONTROL_ROWS + 1
                ],
            ),
        ],
    )
    def test_the_lists_take_fixed_bounds(self, field, items):
        assert ControlDocument.model_validate(document(**{field: items[:-1]}))
        assert f"{field}\n  List should have at most" in refusal(**{field: items})

    def test_the_aliases_take_a_fixed_bound(self):
        aliases = [f"alias {at}" for at in range(MAX_CONTROL_ALIASES + 1)]
        scope = {"type": "store", "name": "Orders DB", "aliases": aliases[:-1]}
        assert ControlDocument.model_validate(document(scope=scope))
        assert "scope.aliases\n  List should have at most" in refusal(
            scope={**scope, "aliases": aliases}
        )


LIMITS = ControlLimits(max_controls=50, max_evidence=2, max_evidence_bytes=102_400)


def text_document(text: str, name: str = "waf.yaml") -> tuple[ControlDocument, dict]:
    """A document with one evidence file of ``text``, and the text by its ID."""
    held = ControlDocument.model_validate(
        document(
            evidence=[
                evidence(
                    name,
                    sha256=text_digest(text) if is_utf_8(text) else "0" * 64,
                    size_bytes=len(text.encode("utf-8", "surrogatepass")),
                )
            ]
        )
    )
    return held, {EVIDENCE_ID: text}


def is_utf_8(text: str) -> bool:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def evidence_message(text: str) -> str | None:
    held, texts = text_document(text)
    refused = control_refusal(held, texts, limits=LIMITS)
    if refused is None:
        return None
    assert refused.evidence_id == EVIDENCE_ID
    return refused.message


class TestTheEvidenceRules:
    """An evidence file is plain text with no credential (ADR 0127)."""

    def test_plain_text_passes(self):
        assert evidence_message("rate_limit:\n  requests: 100\n") is None

    def test_a_file_over_the_count_is_refused(self):
        files = [evidence(f"{at}.txt", id=f"evidence-{at:032x}") for at in range(3)]
        held = ControlDocument.model_validate(document(evidence=files))
        refused = control_refusal(held, {}, limits=LIMITS)
        assert refused == EvidenceRefusal(
            None,
            "A Control holds at most 2 evidence files. Remove a file before you"
            " add another.",
        )

    def test_a_file_over_the_byte_limit_is_refused(self):
        assert evidence_message("x" * 102_401) == (
            "This file is larger than the 102,400 bytes one evidence file can hold."
        )

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("half \ud800 a pair", NOT_UTF_8),
            ("a bell \x07 rings", NOT_PLAIN_TEXT),
            (" \n\t ", NO_TEXT),
        ],
    )
    def test_the_owner_file_text_rules_apply(self, text, message):
        assert evidence_message(text) == message

    @pytest.mark.parametrize(
        ("line", "shape"),
        [
            ("-----BEGIN RSA PRIVATE KEY-----", "a private key block"),
            ("-----BEGIN OPENSSH PRIVATE KEY-----", "a private key block"),
            ("aws_access_key_id = AKIAIOSFODNN7EXAMPLE", "a cloud access key ID"),
            ("key: ASIAIOSFODNN7EXAMPLE", "a cloud access key ID"),
            ("api_key: sk-ant-api03-abcdefghijklmnopqrstuv", "a vendor token"),
            ("OPENAI=sk-proj-abcdefghijklmnopqrstuvwx", "a vendor token"),
            ("auth ghp_" + "a" * 36, "a vendor token"),
            ("bot xoxb-1234567890-abcdefghij", "a vendor token"),
            ('password = "hunter2"', "a password, secret or token set to a value"),
            ("client_secret: 'changeme'", "a password, secret or token set to a value"),
            ("DB_PASSWORD=s3cr3t!", "a password, secret or token set to a value"),
            ("  token: abc123def", "a password, secret or token set to a value"),
            ('"apiToken": "abc"', "a password, secret or token set to a value"),
        ],
    )
    def test_a_credential_is_refused_by_line_and_shape(self, line, shape):
        message = evidence_message(f"first line\nsecond line\n{line}\nlast line\n")
        assert message == (
            f"Line 3 of “waf.yaml” holds {shape}. Remove it, then add the file"
            " again: the service stores no credential."
        )

    @pytest.mark.parametrize(
        "line",
        [
            "password: str",
            "    password: Optional[str] = None",
            'token = os.environ["TOKEN"]',
            "password: ${DB_PASSWORD}",
            'secret = "{}".format(value)',
            "if password == other:",
            "Tokens rotate every 90 days.",
            "The password policy: twelve characters at least.",
            "token: os.environ",
            'password = ""',
        ],
    )
    def test_a_line_that_sets_no_literal_value_passes(self, line):
        assert evidence_message(f"{line}\n") is None

    def test_the_first_hit_is_the_one_named(self):
        text = "ok\npassword = 'a1'\n-----BEGIN EC PRIVATE KEY-----\n"
        assert evidence_message(text).startswith("Line 2 of")


def test_a_document_with_no_name_key_is_refused():
    held = document()
    del held["name"]
    with pytest.raises(ValidationError, match="name\n  Field required"):
        ControlDocument.model_validate(held)


def test_the_scope_types_are_the_element_prefixes_but_a_flow():
    assert set(get_args(ScopeType)) == {"application"} | (
        set(get_args(ElementPrefix)) - {"flow"}
    )


@pytest.mark.parametrize(
    ("line", "shape"),
    [
        ("-----BEGIN PGP PRIVATE KEY BLOCK-----", "a private key block"),
        (
            'password = "' + "x" * 400 + '"',
            "a password, secret or token set to a value",
        ),
        ("token: eyJ" + "a" * 400, "a password, secret or token set to a value"),
        ("secret: '" + "b" * 300, "a password, secret or token set to a value"),
    ],
)
def test_a_long_or_unclosed_credential_is_refused(line, shape):
    assert evidence_message(f"{line}\n") == (
        f"Line 1 of “waf.yaml” holds {shape}. Remove it, then add the file again:"
        " the service stores no credential."
    )


@pytest.mark.parametrize(
    "line", ["token=" * 17_000, 'password="' * 10_000, "token: " + "a" * 100_000]
)
def test_a_hostile_line_is_read_in_linear_time(line):
    started = time.perf_counter()
    credential_hit(line)
    assert time.perf_counter() - started < 1
