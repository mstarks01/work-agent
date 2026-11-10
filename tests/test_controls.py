"""The Control document and the evidence rules (ADRs 0123, 0124 and 0127)."""

from __future__ import annotations

import time
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from analysis_service.analysis import CONTROL_ATTRIBUTES
from analysis_service.capabilities import CAPABILITIES
from analysis_service.controls import (
    ALIASES_FULL,
    ATTRIBUTE_STATEMENTS,
    MAX_CONTROL_ALIASES,
    MAX_CONTROL_FACTS,
    MAX_CONTROL_ROWS,
    PROTECTIVE_ATTRIBUTES,
    ROWS_FULL,
    Control,
    ControlDocument,
    ControlLimits,
    EvidenceRefusal,
    ScopeType,
    control_refusal,
    credential_hit,
)
from analysis_service.fact_answers import STORED
from analysis_service.frameworks import PACKAGES
from analysis_service.job_controls import CopiedControl
from analysis_service.owner_files import (
    NO_TEXT,
    NOT_PLAIN_TEXT,
    NOT_UTF_8,
    text_refusal,
)
from analysis_service.sources import text_digest
from analysis_service.system_model import (
    CONTROL_VALUE_MAX_CHARS,
    ElementPrefix,
    ZonedElement,
    attribute_names,
)

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


def copied_control() -> CopiedControl:
    """The job's copy of :func:`document`, whose evidence text matches its
    digest."""
    text = "encrypted: true\n"
    held = ControlDocument.model_validate(
        document(evidence=[evidence(sha256=text_digest(text), size_bytes=len(text))])
    )
    return CopiedControl.of(
        Control.create(owner_id="alice", document=held), {EVIDENCE_ID: text}
    )


def test_a_document_answers_an_evidence_file_by_its_id():
    held = ControlDocument.model_validate(document())
    found = held.file(EVIDENCE_ID)
    assert found is not None
    assert found.name == "rds.tf"
    assert held.file("evidence-" + "f" * 32) is None


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
        held = ControlDocument.model_validate(document(scope=scope, facts=[]))
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

    def test_the_rows_take_a_fixed_bound(self):
        rows = [row(unit.id) for unit in PACKAGES["asvs"].units][: MAX_CONTROL_ROWS + 1]
        assert ControlDocument.model_validate(document(rows=rows[:-1]))
        assert ROWS_FULL in refusal(rows=rows)

    def test_a_fact_with_a_detail_is_refused(self):
        assert "takes no detail" in refusal(facts=[fact() | {"detail": "but not logs"}])

    def test_the_protective_attributes_are_the_control_attributes_of_a_scope(self):
        # Each control attribute that a scope's element holds is protective,
        # but data_classification, which describes the data.
        held = {
            attribute
            for kind in get_args(ZonedElement)
            for attribute in attribute_names(kind.model_construct())
        }
        assert set(PROTECTIVE_ATTRIBUTES) == (set(CONTROL_ATTRIBUTES) & held) - {
            "data_classification"
        }

    def test_the_facts_take_a_fixed_bound_before_their_kinds(self):
        # A part takes fewer legal facts than the bound, so the bound refuses a
        # long list before the kind rule reads a fact of it.
        facts = [
            {"key": ["", "", "", "", "", capability], "value": "yes"}
            for capability in list(CAPABILITIES)[: MAX_CONTROL_FACTS + 1]
        ]
        assert "facts\n  List should have at most" in refusal(facts=facts)

    @pytest.mark.parametrize(
        ("scope_type", "key", "message"),
        [
            ("application", "", "The whole application takes no fact."),
            ("store", "", "A Control states a protection:"),
            ("store", "technology", "A Control states a protection:"),
            ("store", "data_classification", "A Control states a protection:"),
        ],
    )
    def test_a_fact_that_describes_the_system_is_refused(
        self, scope_type, key, message
    ):
        # A Capability or a descriptive attribute says what the application
        # does, not what protects it.
        name = "" if scope_type == "application" else "Orders DB"
        scope = {"type": scope_type, "name": name, "aliases": []}
        held = ["", key, "", "", "", "" if key else "websocket"]
        assert message in refusal(scope=scope, facts=[{"key": held, "value": "yes"}])

    @pytest.mark.parametrize(
        ("attribute", "question", "answer"),
        [
            ("exposure", "", {"value": "internet-facing"}),
            ("encryption_at_rest", "", {"value": "unknown"}),
            ("encryption_at_rest", "", {"value": "none"}),
            ("encryption_at_rest", "", {"value": "not encrypted"}),
            ("encryption_at_rest", "", {"value": "TBD"}),
            ("", "physical-access", {"value": "yes"}),
            ("", "destination", {"value": "no"}),
            ("", "stored-copy-access", {"facets": {"encrypted": "no"}}),
            ("", "reachability", {"facets": {"internet": "yes"}}),
            ("", "update-process", {"facets": {"scheduled": "unknown"}}),
            ("", "update-process", {"facets": {"tested": "not applicable"}}),
        ],
    )
    def test_an_answer_that_states_no_protection_is_refused(
        self, attribute, question, answer
    ):
        # A Control states what protects the part. A harmful or an empty
        # answer states a weakness or nothing.
        fact = {"key": ["", attribute, "", "", question, ""], **answer}
        assert "A Control states only a protection." in refusal(facts=[fact])

    @pytest.mark.parametrize(
        ("attribute", "question", "answer"),
        [
            ("exposure", "", {"value": "internal"}),
            ("encryption_at_rest", "", {"value": "AES-256 by the KMS"}),
            ("", "physical-access", {"value": "no"}),
            ("", "destination", {"value": "yes"}),
            ("", "reachability", {"facets": {"internet": "no"}}),
            ("", "update-process", {"facets": {"scheduled": "yes", "tested": "yes"}}),
        ],
    )
    def test_a_protective_answer_is_kept(self, attribute, question, answer):
        fact = {"key": ["", attribute, "", "", question, ""], **answer}
        assert ControlDocument.model_validate(document(facts=[fact]))

    def test_a_value_longer_than_the_part_holds_is_refused(self):
        # The model's attribute holds CONTROL_VALUE_MAX_CHARS, so a longer
        # value would fail when the job writes it.
        fact = {"key": ["", "encryption_at_rest", "", "", "", ""]}
        held = "AES-256 " + "k" * (CONTROL_VALUE_MAX_CHARS - 8)
        assert ControlDocument.model_validate(document(facts=[fact | {"value": held}]))
        assert "the most the part holds" in refusal(
            facts=[fact | {"value": held + "k"}]
        )

    def test_each_protective_attribute_has_a_statement(self):
        assert set(ATTRIBUTE_STATEMENTS) == set(PROTECTIVE_ATTRIBUTES)

    def test_the_aliases_take_a_fixed_bound(self):
        aliases = [f"alias {at}" for at in range(MAX_CONTROL_ALIASES + 1)]
        scope = {"type": "store", "name": "Orders DB", "aliases": aliases[:-1]}
        assert ControlDocument.model_validate(document(scope=scope))
        assert ALIASES_FULL in refusal(scope={**scope, "aliases": aliases})


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
            ('password = "hunter2"', "a password, key, secret or token set to a value"),
            (
                "client_secret: 'changeme'",
                "a password, key, secret or token set to a value",
            ),
            ("DB_PASSWORD=s3cr3t!", "a password, key, secret or token set to a value"),
            ("  token: abc123def", "a password, key, secret or token set to a value"),
            ('"apiToken": "abc"', "a password, key, secret or token set to a value"),
        ],
    )
    def test_a_credential_is_refused_by_line_and_shape(self, line, shape):
        message = evidence_message(f"first line\nsecond line\n{line}\nlast line\n")
        assert message == (
            f"Line 3 of “waf.yaml” looks like it holds {shape}. The service did"
            " not save the file. Remove the value, then add the file again."
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
            'log.info("password: " + pw)',
            "print('token=', tok)",
            'raise ValueError("bad token: " + str(t))',
            "x = 'secret=' + s",
            'header = "Bearer token: " + t',
            'log.info("password: " + pw + " set")',
            'logger.debug("token=" + tok + ", user=" + user)',
            "raise ValueError('bad token: ' + t + ' for ' + u)",
            'msg = "token: " + t + "."',
            'password: "********"',
            'x = "token="+tok+"x"',
            'print("token:", tok, "x")',
        ],
    )
    def test_a_line_that_sets_no_literal_value_passes(self, line):
        assert evidence_message(f"{line}\n") is None

    def test_the_first_hit_is_the_one_named(self):
        text = "ok\npassword = 'a1'\n-----BEGIN EC PRIVATE KEY-----\n"
        assert evidence_message(text).startswith("Line 2 of")


SETTING = "a password, key, secret or token set to a value"


@pytest.mark.parametrize(
    "line",
    [
        "api_key = abc123",
        "apikey: 'k'",
        'aws_access_key = "abc"',
        "private_key: abc123",
        "DB_CREDENTIAL=s3cr3t!",
    ],
)
def test_a_key_or_credential_name_set_to_a_value_is_refused(line):
    assert credential_hit(f"{line}\n", name="app.py") == (1, SETTING)


@pytest.mark.parametrize(
    "line",
    [
        "api_key = abc123 # prod",
        "api_key = abc123;",
        "api_key = abc123,",
        "api_key = abc123 ;",
        "api_key = abc123\t# prod",
        "token: abc123 # prod",
        "token: abc123;",
    ],
)
def test_an_unquoted_value_ends_at_a_comment_a_semicolon_or_a_comma(line):
    """The file is not a settings file, so only the table reads the line."""
    assert credential_hit(f"{line}\n", name="notes.txt") == (1, SETTING)


@pytest.mark.parametrize(
    "line",
    [
        "Password: 12 characters minimum",
        "Token: 15 minute lifetime",
        "token: n/a for this system",
        "def f(token: bytes32, n: int):",
        "password = pw1 + salt",
        "Password: 12, then a symbol",
        "    token: u32,",
    ],
)
def test_an_unquoted_value_does_not_end_at_a_space_or_at_a_comma_after_a_colon(line):
    """A space after the value starts prose or an expression. A comma after a
    value that follows a colon ends a type or a clause of a sentence."""
    assert credential_hit(f"{line}\n", name="notes.txt") is None


def test_a_keyword_argument_with_a_digit_before_a_comma_is_refused():
    """A known limit of the table: ``pw1`` is a variable, and the line reads
    as ``password=pw1,`` in a settings list does."""
    line = "connect(password=pw1, host=h)\n"
    assert credential_hit(line, name="notes.txt") == (1, SETTING)


@pytest.mark.parametrize(
    "name",
    [
        ".env",
        ".env.production",
        "conf/.env.local",
        "prod.env",
        "app.ini",
        "a.properties",
        "setup.cfg",
        "nginx.conf",
        "APP.ENV",
    ],
)
@pytest.mark.parametrize(
    "line",
    ["password = swordfish", "API_KEY=abc", "export TOKEN=abc", "  secret: plain"],
)
def test_a_settings_file_refuses_a_credential_name_with_any_value(name, line):
    assert credential_hit(f"# settings\n{line}\n", name=name) == (2, SETTING)


@pytest.mark.parametrize(
    "line",
    [
        "DB_PASSWORD=${DB_PASSWORD}",
        "PASSWORD=",
        'secret: "********"',
        "password: ****",
        "# password = swordfish",
        "password_file=/etc/app/password",
        "TOKEN=<your token>",
    ],
)
def test_a_settings_file_passes_a_reference_mask_or_empty_value(line):
    assert credential_hit(f"{line}\n", name="app.env") is None


@pytest.mark.parametrize("name", ["app.py", "values.yaml", "main.tf", "app.envy"])
def test_another_file_passes_a_plain_word_value(name):
    """Outside a settings file, ``password = swordfish`` can set a variable, so
    a plain word value with no digit or symbol passes."""
    assert credential_hit("password = swordfish\n", name=name) is None


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
            "a password, key, secret or token set to a value",
        ),
        ("token: eyJ" + "a" * 400, "a password, key, secret or token set to a value"),
        ("secret: '" + "b" * 300, "a password, key, secret or token set to a value"),
        (
            'secret: "+q3Zr8vT0aLk9mW2xY7bN1cD4eF6gH8j="',
            "a password, key, secret or token set to a value",
        ),
        ("password: '*Xk9#mQ2!vL'", "a password, key, secret or token set to a value"),
        ('password: ")Xk9#mQ2!vL"', "a password, key, secret or token set to a value"),
        ('password: ",Xk9#mQ2!vL"', "a password, key, secret or token set to a value"),
    ],
)
def test_a_long_or_unclosed_credential_is_refused(line, shape):
    assert evidence_message(f"{line}\n") == (
        f"Line 1 of “waf.yaml” looks like it holds {shape}. The service did not"
        " save the file. Remove the value, then add the file again."
    )


#: The line ends that :func:`~analysis_service.owner_files.text_refusal`
#: lets through.
LINE_ENDS = ["\n", "\r\n", "\r", "\u2028", "\u2029"]


def test_each_other_splitlines_break_is_refused():
    """``str.splitlines`` also breaks on six characters that are not in
    :data:`LINE_ENDS`. ``text_refusal`` refuses each of them, so the line ends
    that ``text_lines`` meets are the five the tests use."""
    breaks = [
        chr(point)
        for point in range(0x2100)
        if len(f"a{chr(point)}b".splitlines()) == 2
    ]
    others = [char for char in breaks if char not in LINE_ENDS]
    assert others == ["\v", "\f", "\x1c", "\x1d", "\x1e", "\x85"]
    for char in others:
        refusal = text_refusal(f"a{char}b", max_bytes=99, too_large="")
        assert refusal == NOT_PLAIN_TEXT


@pytest.mark.parametrize("end", LINE_ENDS)
def test_a_credential_line_counts_each_line_end_a_text_may_hold(end):
    """Each line end that ``text_refusal`` lets through starts a line, as it
    does in an editor."""
    text = end.join(["first line", "second line", "DB_PASSWORD=s3cr3t!", ""])
    assert credential_hit(text, name="waf.yaml") == (
        3,
        "a password, key, secret or token set to a value",
    )


@pytest.mark.parametrize(
    "line",
    [
        "token=" * 17_000,
        ("token=" * 42 + " ") * 400,
        'token="+' + " " * 100_000,
        "password: ')" + " " * 100_000,
        "token=a1" + " " * 100_000 + "x",
        ("token=a1" + " " * 250 + "x") * 400,
    ],
)
def test_a_hostile_line_is_read_in_linear_time(line):
    """Each line holds a hostile run after an assignment. An unquoted value
    with no 256-character bound reads each value to the end of the first line,
    and a joined-string check with two unbounded space quantifiers tries each
    split of one run of spaces between them. Each takes seconds. The last two
    lines hold an unquoted value, a run of spaces and no end of the value."""
    started = time.perf_counter()
    credential_hit(line, name="app.env")
    assert time.perf_counter() - started < 1


def test_a_pause_save_ticks_at_most_one_model_of_elements():
    from analysis_service.job_controls import ControlScopeEntry
    from analysis_service.validation import MAX_ELEMENTS

    ticked = [f"store:part-{n}" for n in range(MAX_ELEMENTS + 1)]
    assert ControlScopeEntry(control=CONTROL_ID, ticked=ticked[:-1])
    with pytest.raises(ValidationError):
        ControlScopeEntry(control=CONTROL_ID, ticked=ticked)
