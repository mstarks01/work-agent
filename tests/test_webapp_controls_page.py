"""The Controls page and its routes: an owner lists, writes, edits and
deletes Controls (#2157, ADR 0127).

Offline, through the first-run app over an in-memory store.
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest
from fastapi.testclient import TestClient

from analysis_service.claims import (
    ControlRowRef,
    RefusedControl,
    ReturnedClaim,
    Verdict,
)
from analysis_service.frameworks.asvs.catalog import requirement_text
from analysis_service.jobs import PipelineCompleted
from analysis_service.owner_files import NO_TEXT, NOT_PLAIN_TEXT
from tests import test_webapp
from tests.factories import sample_analysis
from tests.test_controls import fact, row
from tests.test_webapp import LOOPBACK, SAME_ORIGIN
from tests.test_webapp_controls import BOB, ORDERS_DB, Owner, ZonedRunner
from tests.test_webapp_questions import app_for, ran
from webapp.main import Workspace

tiers = test_webapp.tiers

LIMITS = test_webapp.WEBAPP_CONTROL_LIMITS


@pytest.fixture
def owner(tiers):
    return Owner(tiers)


def body(**changed) -> dict:
    """A save the page sends: one fact, one row and one new evidence file."""
    return {
        "name": "Orders DB encrypted at rest",
        "scope": {"type": "store", "name": "Orders DB", "aliases": ["orders database"]},
        "facts": [fact()],
        "rows": [row()],
        "evidence": [{"name": "rds.tf", "class": "code", "text": "encrypted = on\n"}],
        **changed,
    }


def create(owner, **changed):
    return owner.client.post(
        "/workspace/controls", json=body(**changed), headers=SAME_ORIGIN
    )


def replace(owner, control_id: str, sent: dict):
    return owner.client.put(
        f"/workspace/controls/{control_id}", json=sent, headers=SAME_ORIGIN
    )


def delete(owner, control_id: str):
    return owner.client.delete(f"/workspace/controls/{control_id}", headers=SAME_ORIGIN)


def listed(owner) -> list[dict]:
    answered = owner.client.get("/workspace/controls")
    assert answered.status_code == 200, answered.text
    return answered.json()


class TestCreate:
    def test_a_save_is_listed_with_no_evidence_text(self, owner):
        created = create(owner)
        assert created.status_code == 201, created.text
        control = created.json()
        assert re.fullmatch(r"control-[0-9a-f]{32}", control["id"])
        [file] = control["evidence"]
        assert re.fullmatch(r"evidence-[0-9a-f]{32}", file["id"])
        assert (file["name"], file["class"], file["size_bytes"]) == (
            "rds.tf",
            "code",
            15,
        )
        assert "text" not in file
        assert listed(owner) == [control]
        text = asyncio.run(
            owner.store.read_evidence(control["id"], file["id"], owner.owner_id())
        )
        assert text == "encrypted = on\n"

    @pytest.mark.parametrize(
        ("changed", "message"),
        [
            ({"name": " "}, "Give the Control a name."),
            (
                {"scope": {"type": "store", "name": "", "aliases": []}},
                "Name what the Control protects, or choose the whole application.",
            ),
            (
                {"facts": [], "rows": []},
                (
                    "Add at least one fact or one row. A Control with neither"
                    " changes nothing."
                ),
            ),
            (
                {"facts": [fact(), fact(value="AES-128")]},
                (
                    "The fact “encryption_at_rest” appears twice. One Control"
                    " writes one value for one fact."
                ),
            ),
            ({"rows": [row(), row()]}, "The row V11.2 appears twice."),
            (
                {"facts": [fact("trust_zone", "boundary:dmz")]},
                (
                    "A Control places no element, so it writes no trust_zone."
                    " Protect the zone instead."
                ),
            ),
            (
                {
                    "facts": [
                        {
                            "key": ["", "encryption_at_rest", "", "", "", "database"],
                            "value": "yes",
                        }
                    ]
                },
                "A fact names one fact, in one spelling.",
            ),
            (
                {"scope": {"type": "application"}},
                (
                    "The whole application takes only a capability. Choose the"
                    " part that an attribute or a question is about."
                ),
            ),
            (
                {"facts": [{"key": ["", "", "", "", "", "database"], "value": "yes"}]},
                (
                    "A part takes its attributes and the questions about it. A"
                    " capability is a fact of the whole application: protect the"
                    " whole application for it."
                ),
            ),
            (
                {"rows": [row(unit="V99.9")]},
                "unit 'V99.9' is not a unit of framework 'asvs'",
            ),
            (
                {"scope": {"type": "flow", "name": "Orders", "aliases": []}},
                (
                    "Input should be 'application', 'entity', 'process', 'store'"
                    " or 'boundary'"
                ),
            ),
        ],
    )
    def test_each_document_refusal(self, owner, changed, message):
        refused = create(owner, **changed)
        assert refused.status_code == 400, refused.text
        assert refused.json()["message"].endswith(message)
        assert listed(owner) == []

    def test_a_repeated_name_is_refused(self, owner):
        assert create(owner).status_code == 201
        again = create(owner, scope={"type": "application"}, facts=[])
        assert again.status_code == 409
        assert again.json()["message"] == (
            "You already have a Control named “Orders DB encrypted at rest”. Give"
            " this Control a different name."
        )

    def test_an_alias_another_scope_holds_is_refused(self, owner):
        owner.keep("WAF", {"type": "store", "name": "Orders database"})
        refused = create(owner)
        assert refused.status_code == 409
        assert refused.json()["message"] == (
            "Another Control protects a scope with one of these names. Remove the"
            " alias, or change the name."
        )

    def test_the_count_limit(self, owner):
        for number in range(LIMITS.max_controls):
            owner.keep(f"Control {number}", {"type": "application"})
        refused = create(owner)
        assert refused.status_code == 409
        assert refused.json()["message"] == (
            "Controls holds 50 Controls, the most it can keep. Delete a Control"
            " before you add another."
        )

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            (
                "x" * (LIMITS.max_evidence_bytes + 1),
                (
                    f"This file is larger than the {LIMITS.max_evidence_bytes:,}"
                    " bytes one evidence file can hold."
                ),
            ),
            ("a\x00b", NOT_PLAIN_TEXT),
            ("  \n", NO_TEXT),
            (
                "region = eu\npassword = hunter2!\n",
                (
                    "Line 2 of “rds.tf” holds a password, secret or token set to a"
                    " value. Remove it, then add the file again: the service stores"
                    " no credential."
                ),
            ),
        ],
    )
    def test_each_evidence_refusal(self, owner, text, message):
        sent = [{"name": "rds.tf", "class": "code", "text": text}]
        refused = create(owner, evidence=sent)
        assert refused.status_code == 400, refused.text
        assert refused.json()["message"] == message

    def test_the_evidence_count_limit(self, owner):
        sent = [
            {"name": f"{number}.txt", "class": "people", "text": "signed\n"}
            for number in range(LIMITS.max_evidence + 1)
        ]
        refused = create(owner, evidence=sent)
        assert refused.status_code == 400
        assert refused.json()["message"] == (
            "A Control holds at most 10 evidence files. Remove a file before you"
            " add another."
        )

    @pytest.mark.parametrize(
        "evidence",
        [
            [{"name": " ", "class": "code", "text": "x\n"}],
            [{"name": "a" * 201, "class": "code", "text": "x\n"}],
            [
                {"name": "a.txt", "class": "code", "text": "x\n"},
                {"name": "a.txt", "class": "code", "text": "y\n"},
            ],
            [{"name": "a.txt", "class": "secret", "text": "x\n"}],
        ],
    )
    def test_each_evidence_name_and_class_refusal(self, owner, evidence):
        assert create(owner, evidence=evidence).status_code == 400

    def test_an_unknown_key_is_refused(self, owner):
        refused = create(owner, owner_id="someone")
        assert refused.status_code == 400


class TestReplace:
    def test_an_edit_replaces_the_control_and_keeps_named_evidence(self, owner):
        control = create(owner).json()
        [kept] = control["evidence"]
        sent = body(
            name="Orders DB with KMS",
            evidence=[
                {"id": kept["id"]},
                {"name": "kms.txt", "class": "config", "text": "key = orders\n"},
            ],
        ) | {"updated_at": control["updated_at"]}
        replaced = replace(owner, control["id"], sent)
        assert replaced.status_code == 200, replaced.text
        edited = replaced.json()
        assert edited["id"] == control["id"]
        assert edited["name"] == "Orders DB with KMS"
        assert [file["name"] for file in edited["evidence"]] == ["rds.tf", "kms.txt"]
        assert edited["evidence"][0] == kept
        assert listed(owner) == [edited]

    def test_an_edit_that_read_an_old_version_is_refused(self, owner):
        control = create(owner).json()
        first = body(name="First") | {"updated_at": control["updated_at"]}
        first["evidence"] = [{"id": control["evidence"][0]["id"]}]
        assert replace(owner, control["id"], first).status_code == 200
        second = replace(owner, control["id"], first | {"name": "Second"})
        assert second.status_code == 409
        assert second.json()["message"] == (
            "This Control changed in another tab or window. Reload the page, then"
            " edit it again."
        )

    def test_another_owner_s_control_is_missing(self, owner):
        foreign = owner.keep("Theirs", {"type": "application"}, identity=BOB)
        sent = body() | {"updated_at": "2026-11-04T00:00:00Z"}
        assert replace(owner, foreign, sent).status_code == 404
        assert replace(owner, f"control-{'0' * 32}", sent).status_code == 404

    def test_an_evidence_id_the_control_does_not_hold_is_refused(self, owner):
        control = create(owner).json()
        sent = body(evidence=[{"id": f"evidence-{'0' * 32}"}]) | {
            "updated_at": control["updated_at"]
        }
        assert replace(owner, control["id"], sent).status_code == 400


class TestDelete:
    def test_a_delete_removes_the_control(self, owner):
        control = create(owner).json()
        assert delete(owner, control["id"]).status_code == 204
        assert listed(owner) == []
        gone = delete(owner, control["id"])
        assert gone.status_code == 404

    def test_another_owner_s_control_is_missing(self, owner):
        foreign = owner.keep("Theirs", {"type": "application"}, identity=BOB)
        assert delete(owner, foreign).status_code == 404
        assert delete(owner, "not-an-id").status_code == 404
        held = asyncio.run(owner.store.principal(BOB))
        assert asyncio.run(owner.store.get_control(foreign, held.owner_id))


class TestCrossOrigin:
    def test_each_write_route_refuses_a_cross_origin_request(self, owner):
        control = create(owner).json()
        client = owner.client
        sent = body() | {"updated_at": control["updated_at"]}
        path = f"/workspace/controls/{control['id']}"
        refused = [
            client.post("/workspace/controls", json=body(name="Other")),
            client.put(path, json=sent),
            client.delete(path),
        ]
        assert [answer.status_code for answer in refused] == [403, 403, 403]
        assert listed(owner) == [control]


class TestThePage:
    def test_the_page_carries_a_strict_nonce_csp(self, owner):
        page = owner.client.get("/controls")
        assert page.status_code == 200
        csp = page.headers["Content-Security-Policy"]
        assert "default-src 'none'" in csp
        assert "connect-src 'self'" in csp
        assert "unsafe-inline" not in csp
        nonce = re.search(r"script-src 'nonce-([^']+)'", csp).group(1)
        for _, attributes in re.findall(r"<(script|style)([^>]*)>", page.text):
            assert f'nonce="{nonce}"' in attributes

    def test_the_page_reads_the_unit_labels_from_the_catalog(self, owner):
        page = owner.client.get("/controls").text
        data = re.search(
            r'<script type="application/json" id="controls_page"[^>]*>(.*?)</script>',
            page,
            re.DOTALL,
        ).group(1)
        payload = json.loads(data)
        asvs = {unit["id"]: unit for unit in payload["units"]["asvs"]["units"]}
        assert asvs["V2.4.1"]["label"] == requirement_text("V2.4.1")
        assert asvs["V2"]["label"] == "Validation and Business Logic"
        assert asvs["V2.4"]["grain"] == "section" and asvs["V2.4"]["label"]
        assert payload["units"]["asvs"]["version"] == "5.0.0"
        assert {unit["id"] for unit in payload["units"]["stride"]["units"]} >= {
            "spoofing",
            "tampering",
        }
        assert payload["limits"]["max_controls"] == LIMITS.max_controls

    def test_the_sidebar_lists_controls_between_files_and_settings(self, owner):
        page = owner.client.get("/controls").text
        files = page.index('href="/files"')
        controls = page.index('href="/controls"')
        settings = page.index('href="/settings"')
        assert files < controls < settings


class RulingRunner(ZonedRunner):
    """Rules on the rows of one Control in the report it completes: the
    critic applies the ``spoofing`` row on two STRIDE drafts, and refuses the
    ``tampering`` row on a claim that went back to the deferred set."""

    control = ""

    async def run(self, job, on_node):
        outcome = await super().run(job, on_node)
        if job.pauses() or not self.control:
            return outcome
        report = outcome.report
        [block] = [one for one in report.analyses if one.framework == "stride"]
        [claim] = block.claims
        verdict = Verdict(
            status="rejected",
            reason="The owner's file says “WAF: on” (WAF.yaml).",
            rejected_because="control",
            control_row=ControlRowRef(control=self.control, unit="spoofing"),
        )
        returned = ReturnedClaim(
            claim_id="stride-returned",
            unit="tampering",
            needs="code",
            refused_controls=[
                RefusedControl(
                    control=self.control,
                    unit="tampering",
                    reason="It shows no <b>signing</b> at https://evil.example.",
                )
            ],
        )
        # Two drafts on one row, each ruled on by the same quoted line.
        ruled = sample_analysis(
            [],
            [
                claim.model_copy(update={"verdict": verdict}),
                claim.model_copy(update={"id": f"{claim.id}-2", "verdict": verdict}),
            ],
            returned_claims=[returned],
        )
        analyses = [ruled if one is block else one for one in report.analyses]
        return PipelineCompleted(
            report=report.model_copy(update={"analyses": analyses})
        )


@pytest.fixture
def ruled(tiers):
    owner = Owner(tiers)
    owner.runner = RulingRunner()
    owner.client = TestClient(
        app_for(tiers, owner.runner, workspace=Workspace(store=owner.store)),
        base_url=LOOPBACK,
    )
    stride = [
        row(unit, framework="stride", version="1.0")
        for unit in ("spoofing", "tampering", "repudiation")
    ]
    waf = owner.keep("WAF", {"type": "application"}, rows=stride, facts=[])
    db = owner.keep("Encrypted", ORDERS_DB)
    gone = owner.keep("Billing", {"type": "process", "name": "Billing"})
    owner.runner.control = waf
    run_id = owner.paused(waf, db, gone)
    started = owner.answer(run_id, revision=0)
    assert started.status_code == 200, started.text
    child = started.json()["run"]
    ran(owner.client, child)
    held = asyncio.run(owner.store.get(child))
    assert held.status == "completed", held.error
    return owner, child, (waf, db, gone)


class TestTheReportSection:
    def test_the_json_export_carries_each_fact_state_and_row_outcome(self, ruled):
        owner, child, _ = ruled
        exported = owner.client.get(f"/report/{child}/export.json")
        assert exported.status_code == 200, exported.text
        assert "attachment" in exported.headers["content-disposition"]
        body = exported.json()
        assert body["report"]["job"]["id"] == child
        section = {entry["name"]: entry for entry in body["controls"]}
        assert list(section) == ["WAF", "Encrypted", "Billing"]
        rows = {one["unit"]: one for one in section["WAF"]["rows"]}
        assert {unit: one["outcome"] for unit, one in rows.items()} == {
            "spoofing": "applied",
            "tampering": "refused",
            "repudiation": "no-finding",
        }
        assert [one["ruling"] for one in rows["spoofing"]["rulings"]] == [
            "applied",
            "applied",
        ]
        assert rows["spoofing"]["quoted"] == [
            {"file": "WAF.yaml", "class": "config", "line": 1, "text": "WAF: on"}
        ]
        assert rows["tampering"]["rulings"] == [
            {
                "claim": "stride-returned",
                "ruling": "refused",
                "reason": "It shows no <b>signing</b> at https://evil.example.",
            }
        ]
        assert rows["repudiation"]["meaning"].startswith("No finding")
        assert section["WAF"]["outcome"] == "application"
        assert [
            (fact["value"], fact["state"]) for fact in section["Encrypted"]["facts"]
        ] == [("AES-256", "written")]
        assert section["Encrypted"]["elements"] == ["Orders DB"]
        assert (section["Billing"]["state"], section["Billing"]["reason"]) == (
            "left-out",
            "missing-element",
        )

    def test_the_markdown_export_carries_the_section_as_text(self, ruled):
        owner, child, _ = ruled
        exported = owner.client.get(f"/report/{child}/export.md")
        assert exported.status_code == 200
        assert exported.headers["content-type"].startswith("text/markdown")
        text = exported.text
        assert "## Controls" in text
        assert "### WAF" in text
        assert "- Row: stride spoofing — applied." in text
        assert "- Row: stride tampering — refused." in text
        assert "- Row: stride repudiation — no-finding." in text
        assert "It shows no \\<b\\>signing\\</b\\> at https\\://evil.example." in text
        assert "Evidence: WAF.yaml (config), line 1: WAF: on" in text
        assert "AES-256 — written" in text
        assert "Protects the process “Billing”: left out, missing-element." in text

    def test_the_report_page_carries_the_section(self, ruled):
        owner, child, _ = ruled
        page = owner.client.get(f"/report/{child}").text
        data = re.search(
            r'<script type="application/json" id="controls"[^>]*>(.*?)</script>',
            page,
            re.DOTALL,
        ).group(1)
        assert [entry["name"] for entry in json.loads(data)] == [
            "WAF",
            "Encrypted",
            "Billing",
        ]
        assert "\\u003cb>signing" in data

    def test_an_unknown_report_is_missing(self, ruled):
        owner, _, _ = ruled
        for path in ("export.md", "export.json"):
            assert owner.client.get(f"/report/job-missing/{path}").status_code == 404


@pytest.mark.parametrize(
    ("value", "escaped"),
    [
        ("see https://evil.example/x", "see https\\://evil.example/x"),
        ("www.evil.example", "www\\.evil.example"),
        ("_www.evil.example", "\\_www\\.evil.example"),
        ("*WWW.evil.example", "\\*WWW\\.evil.example"),
        ("mail a@b.example", "mail a\\@b.example"),
        ("[x](javascript:y) <b>", "\\[x\\](javascript:y) \\<b\\>"),
        ("AES-256. Done.", "AES-256. Done."),
    ],
)
def test_the_markdown_export_escapes_markup_and_autolinks(value, escaped):
    from webapp.report_export import _text

    assert _text(value) == escaped


class TestTheTables:
    """Each table of the pages names each member of its set, so no value
    shows as ``undefined``."""

    def test_the_form_words_each_type_reason_and_state(self):
        from typing import get_args

        from analysis_service.controls import ScopeType
        from analysis_service.job_controls import CoverReason, LeftOutReason, UsedState
        from tests.test_webapp import _table_keys

        assert _table_keys("form_controls.js", "TYPES") == set(get_args(ScopeType))
        assert _table_keys("form_controls.js", "REASONS") == {
            *get_args(LeftOutReason),
            *get_args(CoverReason),
        }
        assert _table_keys("form_controls.js", "STATES") == set(get_args(UsedState))

    def test_the_page_tables_name_each_member(self):
        from typing import get_args

        from analysis_service.controls import ScopeType
        from analysis_service.job_controls import ROW_MEANINGS, RowOutcome
        from analysis_service.sources import EvidenceClass
        from webapp.controls_page import EVIDENCE_LABELS, SCOPE_LABELS

        assert set(SCOPE_LABELS) == set(get_args(ScopeType))
        assert set(EVIDENCE_LABELS) == set(get_args(EvidenceClass))
        assert set(ROW_MEANINGS) == set(get_args(RowOutcome))

    def test_the_fact_kind_tables_name_each_scope_and_kind(self):
        from typing import get_args

        from analysis_service.controls import (
            SCOPE_FACT_KINDS,
            SCOPE_FACT_REFUSALS,
            ControlFactKind,
            ScopeType,
        )
        from webapp.controls_page import _FACT_OFFERS

        assert set(SCOPE_FACT_KINDS) == set(get_args(ScopeType))
        assert set(SCOPE_FACT_REFUSALS) == set(get_args(ScopeType))
        assert set(_FACT_OFFERS) == set(get_args(ControlFactKind))
        assert set().union(*SCOPE_FACT_KINDS.values()) == set(get_args(ControlFactKind))


EVIDENCE = (
    "rate_limit:\n"
    "  enabled: true\n"
    "  requests: 100 a minute for each client\n"
    "waf: enabled\n"
)


@pytest.mark.parametrize(
    ("reason", "lines"),
    [
        ("The file says “requests: 100 a minute for each client”.", [3]),
        ('The file says "waf: enabled" (waf.yaml).', [4]),
        ("The file says `requests: 100 a minute for each client`.", [3]),
        ("The file says ‘enabled: true’.", [2]),
        ("It quotes “enabled”, a word of many lines.", []),
        ("The file says waf: enabled, with no quotation.", []),
        ("“waf: enabled” and again “waf: enabled”.", [4]),
        ("The file says ‘the owner’s requests: 100 a minute for each client’.", [3]),
    ],
)
def test_an_evidence_line_counts_only_where_a_quotation_quotes_it(reason, lines):
    from analysis_service.job_controls import CopiedEvidence, _quoted

    file = CopiedEvidence.model_validate(
        {
            "id": f"evidence-{'0' * 32}",
            "name": "waf.yaml",
            "class": "config",
            "sha256": "0" * 64,
            "text": EVIDENCE,
        }
    )
    assert sorted({line.line for line in _quoted(reason, [file])}) == lines
