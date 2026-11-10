"""The Controls page and its routes: an owner lists, writes, edits and
deletes Controls (#2157, ADR 0127).

Offline, through the first-run app over an in-memory store.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from analysis_service import PACKAGES, Report
from analysis_service.api import max_body_bytes, max_control_body_bytes
from analysis_service.claims import (
    UNKNOWN_KEY_FIELDS,
    ControlRowRef,
    RefusedControl,
    ReturnedClaim,
    UnknownRef,
    Verdict,
)
from analysis_service.controls import (
    ALIASES_FULL,
    CONTROL_ROW_FIELD_MAX_CHARS,
    MAX_CONTROL_ALIASES,
    MAX_CONTROL_EVIDENCE_CEILING,
    MAX_CONTROL_FACTS,
    MAX_CONTROL_ROWS,
    ROWS_FULL,
    SCOPE_WORDS,
    ControlRow,
    ControlScope,
    evidence_full,
    scope_phrase,
)
from analysis_service.fact_answers import FACT_VALUE_MAX_CHARS
from analysis_service.frameworks import lane_of
from analysis_service.frameworks.asvs.catalog import requirement_text
from analysis_service.job_controls import FACT_MEANINGS, MATCH_MEANINGS
from analysis_service.jobs import PipelineCompleted
from analysis_service.owner_files import NO_TEXT, NOT_PLAIN_TEXT, NOT_UTF_8
from analysis_service.sources import MAX_LABEL_CHARS
from analysis_service.system_model import ELEMENT_ID_MAX_CHARS, ELEMENT_TYPE_NOUNS
from tests import test_webapp
from tests.factories import sample_analysis
from tests.source_tree import REPO_ROOT
from tests.test_controls import LINE_ENDS, fact, row
from tests.test_doc_copy_lints import SHIPPED_RESILIENCE
from tests.test_structural_readers import sound_report
from tests.test_webapp import LOOPBACK, SAME_ORIGIN, WEBAPP_LIMITS
from tests.test_webapp_controls import BOB, ORDERS_DB, Owner, ZonedRunner
from tests.test_webapp_questions import app_for, ran
from webapp.controls_page import ControlSave
from webapp.main import Workspace
from webapp.report_export import (
    CSV_COLUMNS,
    JUDGEMENT_CELLS,
    judgement_cells,
    report_csv,
)

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
                    "The whole application takes no fact. Name the units the"
                    " Control covers, or choose the part that a fact is about."
                ),
            ),
            (
                {"facts": [{"key": ["", "", "", "", "", "database"], "value": "yes"}]},
                (
                    "A Control states a protection: the questions about the"
                    " part, its encryption at rest or its exposure. The"
                    " description states the rest."
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
                    "Line 2 of “rds.tf” looks like it holds a password, key, secret"
                    " or token set to a value. The service did not save the file."
                    " Remove the value, then add the file again."
                ),
            ),
        ],
    )
    def test_each_evidence_refusal(self, owner, text, message):
        sent = [{"name": "rds.tf", "class": "code", "text": text}]
        refused = create(owner, evidence=sent)
        assert refused.status_code == 400, refused.text
        assert refused.json()["message"] == message

    def test_a_lone_surrogate_is_refused_as_not_utf_8(self, owner):
        """The digest of a new file encodes strict UTF-8, so the save refuses
        a lone surrogate before the store reads the evidence rules."""
        sent = [{"name": "rds.tf", "class": "code", "text": "half \ud800 a pair"}]
        refused = owner.client.post(
            "/workspace/controls",
            content=json.dumps(body(evidence=sent)),
            headers=SAME_ORIGIN | {"content-type": "application/json"},
        )
        assert refused.status_code == 400, refused.text
        assert refused.json()["message"] == NOT_UTF_8

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

    def test_a_body_past_the_job_guard_reaches_the_control_rules(self, owner):
        """A save with more text than one job's body holds is read, so its
        refusal names the rule it breaks and not the body size."""
        sent = [
            {"name": f"{number}.txt", "class": "people", "text": '"' * 10_000}
            for number in range(LIMITS.max_evidence + 1)
        ]
        assert len(json.dumps(body(evidence=sent))) > max_body_bytes(WEBAPP_LIMITS)
        refused = create(owner, evidence=sent)
        assert refused.status_code == 400, refused.text
        assert refused.json()["message"].startswith("A Control holds at most 10")

    def test_the_guard_holds_a_full_control_at_the_shipped_limits(self):
        limits = SHIPPED_RESILIENCE.control_limits()
        text = '"' * limits.max_evidence_bytes
        sent = [
            {"name": "a" * 200, "class": "code", "text": text}
            for _ in range(limits.max_evidence)
        ]
        full = json.dumps(body(evidence=sent)).encode()
        assert len(full) > max_body_bytes(SHIPPED_RESILIENCE.source_limits())
        assert len(full) <= max_control_body_bytes(limits)

    def test_the_guard_counts_the_bounds_the_models_state(self):
        """The guard counts each Control field from a constant. A field that
        the models add, or a bound that they raise, breaks this test and not a
        save of a full Control."""

        def longest(model) -> dict[str, int | None]:
            return {
                name: next(
                    (
                        rule.max_length
                        for rule in field.metadata
                        if hasattr(rule, "max_length")
                    ),
                    None,
                )
                for name, field in model.model_fields.items()
            }

        assert set(ControlSave.model_fields) == {
            "name",
            "scope",
            "facts",
            "rows",
            "evidence",
        }
        assert set(longest(ControlRow).values()) == {CONTROL_ROW_FIELD_MAX_CHARS}
        keys = longest(UnknownRef)
        assert all(
            keys[name] is None or keys[name] <= ELEMENT_ID_MAX_CHARS
            for name in UNKNOWN_KEY_FIELDS
        )

    def test_the_guard_holds_a_control_with_every_field_at_its_bound(self):
        """Every bounded field at its bound, in 4-byte characters, and evidence
        text that JSON escapes to twice its bytes, as a page sends it."""
        limits = SHIPPED_RESILIENCE.control_limits()

        def wide(chars: int) -> str:
            return "\U0001f600" * chars

        label = wide(MAX_LABEL_CHARS)
        sent = {
            "name": label,
            "scope": {
                "type": "store",
                "name": label,
                "aliases": [label] * MAX_CONTROL_ALIASES,
            },
            "facts": [
                {
                    "key": [wide(ELEMENT_ID_MAX_CHARS)] * len(UNKNOWN_KEY_FIELDS),
                    "value": wide(FACT_VALUE_MAX_CHARS),
                }
            ]
            * MAX_CONTROL_FACTS,
            "rows": [
                dict.fromkeys(
                    ("framework", "unit", "version"), wide(CONTROL_ROW_FIELD_MAX_CHARS)
                )
            ]
            * MAX_CONTROL_ROWS,
            "evidence": [
                {
                    "name": label,
                    "class": "code",
                    "text": '"' * limits.max_evidence_bytes,
                }
            ]
            * limits.max_evidence,
        }
        full = json.dumps(sent, ensure_ascii=False).encode()
        assert len(full) <= max_control_body_bytes(limits)

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

    def test_a_fact_with_a_detail_is_refused(self, owner):
        refused = create(owner, facts=[fact() | {"detail": "but not the logs"}])
        assert refused.status_code == 400
        assert refused.json()["message"] == "A fact of a Control takes no detail."

    def test_a_list_of_evidence_over_the_ceiling_is_refused(self, owner):
        sent = [
            {"name": f"{number}.txt", "class": "people", "text": "signed\n"}
            for number in range(MAX_CONTROL_EVIDENCE_CEILING + 1)
        ]
        assert create(owner, evidence=sent).status_code == 400


class TestReplace:
    def test_an_edit_replaces_the_control_and_keeps_named_evidence(self, owner):
        control = create(owner).json()
        [kept] = control["evidence"]
        sent = body(
            name="Orders DB with KMS",
            evidence=[
                {"id": kept["id"], "class": "code"},
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
        first["evidence"] = [{"id": control["evidence"][0]["id"], "class": "code"}]
        assert replace(owner, control["id"], first).status_code == 200
        second = replace(owner, control["id"], first | {"name": "Second"})
        assert second.status_code == 409
        assert second.json() == {
            "message": (
                "This Control changed in another tab or window after you opened it."
            ),
            "current": listed(owner)[0],
        }
        assert second.json()["current"]["name"] == "First"

    def test_a_stale_edit_of_a_removed_file_answers_with_the_saved_version(self, owner):
        control = create(owner).json()
        [kept] = control["evidence"]
        held = {"id": kept["id"], "class": "code"}
        first = body(evidence=[]) | {"updated_at": control["updated_at"]}
        assert replace(owner, control["id"], first).status_code == 200
        stale = body(evidence=[held]) | {"updated_at": control["updated_at"]}
        answered = replace(owner, control["id"], stale)
        assert answered.status_code == 409
        assert answered.json()["current"]["evidence"] == []

    def test_another_owner_s_control_is_missing(self, owner):
        foreign = owner.keep("Theirs", {"type": "application"}, identity=BOB)
        sent = body() | {"updated_at": "2026-11-04T00:00:00Z"}
        assert replace(owner, foreign, sent).status_code == 404
        assert replace(owner, f"control-{'0' * 32}", sent).status_code == 404

    def test_an_evidence_id_the_control_does_not_hold_is_refused(self, owner):
        control = create(owner).json()
        sent = body(evidence=[{"id": f"evidence-{'0' * 32}", "class": "code"}]) | {
            "updated_at": control["updated_at"]
        }
        assert replace(owner, control["id"], sent).status_code == 400

    def test_an_edit_gives_a_held_file_the_class_it_names(self, owner):
        control = create(owner).json()
        [kept] = control["evidence"]
        sent = body(evidence=[{"id": kept["id"], "class": "people"}]) | {
            "updated_at": control["updated_at"]
        }
        replaced = replace(owner, control["id"], sent)
        assert replaced.status_code == 200, replaced.text
        assert replaced.json()["evidence"] == [kept | {"class": "people"}]
        text = asyncio.run(
            owner.store.read_evidence(control["id"], kept["id"], owner.owner_id())
        )
        assert text == "encrypted = on\n"

    def test_a_held_file_with_no_class_is_refused(self, owner):
        control = create(owner).json()
        sent = body(evidence=[{"id": control["evidence"][0]["id"]}]) | {
            "updated_at": control["updated_at"]
        }
        assert replace(owner, control["id"], sent).status_code == 400


def evidence(owner, control_id: str, evidence_id: str):
    return owner.client.get(f"/workspace/controls/{control_id}/evidence/{evidence_id}")


class TestEvidenceRead:
    def test_a_file_is_an_attachment_of_plain_text_under_its_name(self, owner):
        name = 'rds "orders"; filename=x ü.tf'
        control = create(
            owner, evidence=[{"name": name, "class": "code", "text": "a = 1\n"}]
        ).json()
        [file] = control["evidence"]
        answered = evidence(owner, control["id"], file["id"])
        assert answered.status_code == 200
        assert answered.text == "a = 1\n"
        assert answered.headers["content-type"] == "text/plain; charset=utf-8"
        assert answered.headers["x-content-type-options"] == "nosniff"
        assert answered.headers["content-disposition"] == (
            'attachment; filename="evidence.txt";'
            " filename*=UTF-8''rds%20%22orders%22%3B%20filename%3Dx%20%C3%BC.tf"
        )

    def test_a_file_the_owner_cannot_read_is_missing(self, owner):
        control = create(owner).json()
        [file] = control["evidence"]
        foreign = owner.keep("Theirs", {"type": "application"}, identity=BOB)
        missing = [
            evidence(owner, control["id"], f"evidence-{'0' * 32}"),
            evidence(owner, foreign, file["id"]),
            evidence(owner, f"control-{'0' * 32}", file["id"]),
            evidence(owner, control["id"], "evidence-x"),
            evidence(owner, "control-x", file["id"]),
        ]
        assert [answered.status_code for answered in missing] == [404] * 5
        assert missing[0].json()["message"] == (
            "This evidence file is no longer in the Control."
        )
        assert delete(owner, control["id"]).status_code == 204
        assert evidence(owner, control["id"], file["id"]).status_code == 404


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
        payload = test_webapp._island(
            owner.client.get("/controls").text, "controls_page"
        )
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
        # The page checks each count with the sentence the save refuses with.
        assert payload["limits"]["max_aliases"] == MAX_CONTROL_ALIASES
        assert payload["limits"]["max_rows"] == MAX_CONTROL_ROWS
        assert payload["at_limit"] == {
            "aliases": ALIASES_FULL,
            "rows": ROWS_FULL,
            "evidence": evidence_full(LIMITS.max_evidence),
        }
        # A STRIDE finding always names an element; an ASVS finding may name none.
        assert payload["units"]["stride"]["names_an_element"] is True
        assert payload["units"]["asvs"]["names_an_element"] is False

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
    #: Whether the returned claim also refuses the ``spoofing`` row, so the
    #: row is applied on two drafts and refused on one.
    mixed = False

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
                ),
                *(
                    [
                        RefusedControl(
                            control=self.control,
                            unit="spoofing",
                            reason="It shows no check of the token's issuer.",
                        )
                    ]
                    if self.mixed
                    else []
                ),
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
    # A row of another version, which the job leaves out. The owner types
    # the version, so it holds markup the export must escape.
    stride.append(row("denial-of-service", framework="stride", version="<b>0.9</b>"))
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
            "denial-of-service": "left-out",
        }
        assert rows["spoofing"]["tally"] == "Ruled out: 2"
        assert rows["spoofing"]["title"] == "stride spoofing (version 1.0)"
        assert rows["denial-of-service"]["title"] == (
            "stride denial-of-service (version <b>0.9</b>)"
        )
        assert rows["denial-of-service"]["outcome_text"] == "left out"
        assert rows["repudiation"]["outcome_text"] == "no finding"
        assert rows["repudiation"]["tally"] == ""
        assert rows["denial-of-service"]["meaning"] == (
            "Left out: this row names version <b>0.9</b> of stride, and the"
            " analysis ran version 1.0, so the critic did not judge it."
        )
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
        assert section["WAF"]["meaning"] == MATCH_MEANINGS["application"]
        assert [
            (fact["value"], fact["state"]) for fact in section["Encrypted"]["facts"]
        ] == [("AES-256", "written")]
        assert section["Encrypted"]["facts"][0]["meaning"] == FACT_MEANINGS["written"]
        assert section["Encrypted"]["elements"] == [
            {"id": "store:orders-db", "name": "Orders DB"}
        ]
        assert (section["Billing"]["state"], section["Billing"]["reason"]) == (
            "left-out",
            "missing-element",
        )

    def test_the_markdown_export_gives_each_outcome_its_version_and_sentence(
        self, ruled
    ):
        """A row of each :data:`~analysis_service.job_controls.RowOutcome`
        shows in the Markdown export with its version, its counts and the
        sentence the page shows."""
        from typing import get_args

        from analysis_service.job_controls import (
            RowOutcome,
            RowRuling,
            SectionEntry,
        )
        from webapp.report_export import controls_markdown

        owner, child, _ = ruled
        sent = owner.client.get(f"/report/{child}/export.json").json()["controls"]
        entry = SectionEntry.model_validate(sent[0])
        [template] = entry.rows[:1]
        rows = [
            template.model_copy(
                update={
                    "unit": outcome,
                    "title": f"stride {outcome} (version {template.version})",
                    "outcome": outcome,
                    "meaning": f"The sentence of {outcome}.",
                    "quoted": [],
                    "tally": "Ruled out: 1 · Refused: 1"
                    if outcome == "partly-applied"
                    else "",
                    "rulings": [
                        RowRuling(claim="A-1", ruling="applied", reason="yes"),
                        RowRuling(claim="A-2", ruling="refused", reason="no"),
                    ]
                    if outcome == "partly-applied"
                    else [],
                }
            )
            for outcome in get_args(RowOutcome)
        ]
        text = "\n".join(controls_markdown([entry.model_copy(update={"rows": rows})]))
        for one in rows:
            label = f" · {one.label}" if one.label else ""
            tally = f"{one.tally}. " if one.tally else ""
            assert f"- Row: {one.title}{label} — {tally}{one.meaning}" in text
        assert "  - Ruled out: A-1. yes" in text
        assert "  - Refused: A-2. no" in text

    def test_the_page_and_the_markdown_export_read_the_title_of_a_row(self):
        """The page and the Markdown export read ``SectionRow.title`` for the
        framework, unit and version of a row, so neither spells that phrase.
        The CSV export has its own phrase, which ``docs/Web-App.md`` states."""
        import inspect

        from webapp.report_export import controls_markdown

        script = test_webapp.page_javascript("report_view.js")
        assert "${row.title}" in script
        assert "row.title" in inspect.getsource(controls_markdown)
        assert "(version " not in script + inspect.getsource(controls_markdown)

    def test_the_page_and_the_export_show_one_summary_of_each_stage(self, ruled):
        from analysis_service.job_controls import SectionEntry, section_summary

        owner, child, _ = ruled
        sent = owner.client.get(f"/report/{child}/export.json").json()["controls"]
        summary = section_summary([SectionEntry.model_validate(one) for one in sent])
        assert summary == [
            ("Match", "3 copied: 2 applied, 1 left out."),
            ("Facts", "1 written."),
            ("Rows", "1 applied, 1 refused, 3 no finding, 1 left out."),
        ]
        page = owner.client.get(f"/report/{child}").text
        assert test_webapp._island(page, "controls_summary") == [
            list(line) for line in summary
        ]
        markdown = owner.client.get(f"/report/{child}/export.md").text
        assert (
            "## Controls\n\n- Match: 3 copied: 2 applied, 1 left out.\n"
            "- Facts: 1 written.\n"
            "- Rows: 1 applied, 1 refused, 3 no finding, 1 left out.\n\n### WAF"
        ) in markdown

    def test_a_stage_with_no_count_says_none(self, ruled):
        from analysis_service.job_controls import SectionEntry, section_summary

        owner, child, _ = ruled
        sent = owner.client.get(f"/report/{child}/export.json").json()["controls"]
        billing = SectionEntry.model_validate(sent[2]).model_copy(update={"rows": []})
        assert section_summary([billing]) == [
            ("Match", "1 copied: 1 left out."),
            ("Facts", "none."),
            ("Rows", "none."),
        ]

    def test_the_markdown_export_carries_the_section_as_text(self, ruled):
        owner, child, _ = ruled
        exported = owner.client.get(f"/report/{child}/export.md")
        assert exported.status_code == 200
        assert exported.headers["content-type"].startswith("text/markdown")
        text = exported.text
        assert "## Controls" in text
        assert "### WAF" in text
        assert (
            "- Row: stride spoofing (version 1.0) — Ruled out: 2. Applied: the critic"
            in text
        )
        assert (
            "- Row: stride tampering (version 1.0) — Refused: 1. Refused: the critic"
            in text
        )
        assert (
            "- Row: stride denial-of-service (version \\<b\\>0.9\\</b\\>) — Left"
            " out: this row names version \\<b\\>0.9\\</b\\> of stride, and the"
            " analysis ran version 1.0" in text
        )
        assert "<b>0.9" not in text
        assert "  - Refused: stride-returned. It shows no" in text
        assert "  - Ruled out: " in text
        assert ": refused." not in text and ": applied." not in text
        assert "- Row: stride repudiation (version 1.0) — No finding: no draft" in text
        assert "— no-finding" not in text
        assert "It shows no \\<b\\>signing\\</b\\> at https\\://evil.example." in text
        assert "Evidence: WAF.yaml (config), line 1: WAF: on" in text
        assert "AES-256 — the Control wrote it onto an open fact" in text
        assert (
            "Session cookie theft enables account takeover: rejected, because the"
            " owner states that a Control covers its unit." in text
        )
        for key in ("— written", "rejected for", "(confirmed)", "(needs-info)"):
            assert key not in text
        assert (
            "Protects the process “Billing”: left out, because no part of this"
            " description has the name or an alias." in text
        )
        assert "Protects the whole application: it applies with no match." in text
        assert "Protects the data store “Orders DB”: " in text
        assert "the store “" not in text
        assert "applied to Orders DB." in text

    def test_the_exports_and_the_pages_read_one_scope_phrase(self, ruled):
        """The Markdown export, the JSON export, the report page and the
        Controls list (the form page and the Controls page) name each scope
        with :func:`scope_phrase`."""
        owner, child, _ = ruled
        page = owner.client.get(f"/report/{child}").text
        sent = owner.client.get(f"/report/{child}/export.json").json()["controls"]
        markdown = owner.client.get(f"/report/{child}/export.md").text
        held = owner.client.get("/workspace/controls").json()
        for entry in [*test_webapp._island(page, "controls"), *sent, *held]:
            phrase = scope_phrase(ControlScope.model_validate(entry["scope"]))
            assert entry["scope_phrase"] == phrase
            assert f"Protects {phrase}: " in markdown

    def test_the_page_reads_the_state_words_and_the_ruling_lists(self, ruled):
        """The page gets the words of each state and the name of each ruling
        list from the tables that the Markdown export reads."""
        from analysis_service.job_controls import RESOLUTION_WORDS, RULING_LISTS

        owner, child, _ = ruled
        page = owner.client.get(f"/report/{child}").text
        assert test_webapp._island(page, "ruling_lists") == dict(RULING_LISTS)
        states = {
            entry["state"]: entry["state_text"]
            for entry in test_webapp._island(page, "controls")
        }
        assert states == dict(RESOLUTION_WORDS)

    def test_the_csv_export_holds_a_row_for_each_finding(self, ruled):
        owner, child, _ = ruled
        exported = owner.client.get(f"/report/{child}/export.csv")
        assert exported.status_code == 200
        assert exported.headers["content-type"].startswith("text/csv")
        assert 'filename="report.csv"' in exported.headers["content-disposition"]
        assert exported.text.startswith("\ufeff")
        header, *rows = csv.reader(exported.text.removeprefix("\ufeff").splitlines())
        assert tuple(header) == CSV_COLUMNS
        assert dict(zip(header, rows[0], strict=True)) == {
            "framework": "stride",
            "id": "S-01",
            "title": "Session cookie theft enables account takeover",
            "status": "rejected",
            "severity": "high",
            "category": "spoofing",
            "affected elements": "Customer → Web App",
            "description": "Stolen session cookies let an attacker impersonate"
            " the customer against the web app.",
            "mitigation": "Set HttpOnly and Secure on cookies",
            "controls": "WAF (spoofing, version 1.0): applied",
        }
        assert [one[1] for one in rows] == ["S-01", "S-01-2"]

    def test_the_report_page_carries_the_section(self, ruled):
        owner, child, _ = ruled
        page = owner.client.get(f"/report/{child}").text
        assert [entry["name"] for entry in test_webapp._island(page, "controls")] == [
            "WAF",
            "Encrypted",
            "Billing",
        ]
        (sent,) = re.findall(
            r'<script type="application/json" id="controls"[^>]*>(.*?)</script>',
            page,
        )
        assert "\\u003cb>signing" in sent

    def test_an_unknown_report_is_missing(self, ruled):
        owner, _, _ = ruled
        for path in ("export.md", "export.json", "export.csv"):
            assert owner.client.get(f"/report/job-missing/{path}").status_code == 404


@pytest.mark.parametrize("framework", sorted(PACKAGES))
def test_the_csv_export_reads_each_lane_through_lane_of(framework):
    report = sound_report(framework)
    (block,) = [one for one in report.analyses if one.framework == framework]
    header, *rows = csv.reader(io.StringIO(report_csv(report, [])))
    cells = [dict(zip(header, one, strict=True)) for one in rows]
    claims = [*block.claims, *block.rejected_claims]
    assert [one["id"] for one in cells] == [claim.id for claim in claims]
    assert [one["category"] for one in cells] == [
        str(lane_of(claim) or "") for claim in claims
    ]
    assert {one["framework"] for one in cells} == {framework}


def test_the_judgement_cells_name_each_judgement_field_of_each_record():
    """The table is keyed by the field, so a new package needs no row. Each
    judgement field a package's record declares has a row, and each row's
    field has its judgement type wherever a record declares it. The CSV reads
    the verdict as the status, so the verdict has no row."""
    from tests.test_evals_content import _is_shared_judgement, _ruled_records

    records = _ruled_records()
    declared = {
        name
        for record in records
        for name, field in record.model_fields.items()
        if _is_shared_judgement(field.annotation)
    }
    assert declared - {"verdict"} == set(JUDGEMENT_CELLS)
    assert all(
        _is_shared_judgement(record.model_fields[name].annotation)
        for record in records
        for name in JUDGEMENT_CELLS
        if name in record.model_fields
    )


@pytest.mark.parametrize("framework", sorted(PACKAGES))
def test_the_report_page_reads_the_judgement_cells_the_csv_writes(framework):
    """``severityLevel`` and ``mitigationSummaries`` in report_view.js read the
    fields that :data:`JUDGEMENT_CELLS` reads, so each claim of each package is
    put to both."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node on PATH to run the page's script")
    script = test_webapp.page_javascript("report_view.js")
    helpers = "\n".join(
        re.search(rf"^\s*const {name} = .*;$", script, re.MULTILINE).group(0)
        for name in ("severityLevel", "mitigationSummaries")
    )
    claims = [
        claim
        for block in sound_report(framework).analyses
        for claim in [*block.claims, *block.rejected_claims]
    ]
    assert claims
    dumped = json.dumps([claim.model_dump(mode="json") for claim in claims])
    program = (
        f"{helpers}\nconst claims = {dumped};\n"
        "console.log(JSON.stringify(claims.map((t) =>"
        ' [severityLevel(t), mitigationSummaries(t).join(" | ")])));'
    )
    done = subprocess.run(
        [node, "-"], input=program, capture_output=True, text=True, check=True
    )
    assert json.loads(done.stdout) == [
        [cells["severity"], cells["mitigations"]]
        for cells in map(judgement_cells, claims)
    ]


@pytest.mark.parametrize("lead", ["=", "+", "-", "@", "\t", "\r", "\n", "＝", "＋"])
def test_a_csv_cell_that_reads_as_a_formula_starts_with_an_apostrophe(ruled, lead):
    owner, child, _ = ruled
    body = owner.client.get(f"/report/{child}/export.json").json()["report"]
    claim = body["analyses"][0]["rejected_claims"][0]
    claim["title"] = f'{lead}HYPERLINK("https://evil.example", "a, b")'
    claim["description"] = 'Two lines,\nwith "quotes".'
    text = report_csv(Report.model_validate(body), [])
    row = list(csv.reader(io.StringIO(text)))[1]
    assert row[2] == f'\'{lead}HYPERLINK("https://evil.example", "a, b")'
    assert row[7] == 'Two lines,\nwith "quotes".'


@pytest.mark.parametrize(
    ("title", "cell"),
    [
        # A spreadsheet that splits on ";" or on a tab starts a cell there, and
        # it starts a row at a line break, because the quote is not at the
        # start of its cell.
        ("Spoofed login;=cmd|' /C calc'!A0;", "Spoofed login;'=cmd|' /C calc'!A0;"),
        ("Spoofed login\t@SUM(1)", "Spoofed login\t'@SUM(1)"),
        ("Spoofed login;＝1", "Spoofed login;'＝1"),
        ("Spoofed login;+1", "Spoofed login;'+1"),
        ('Spoofed login;"=1+1;x', "Spoofed login;'\"=1+1;x"),
        ('Spoofed login\t"@SUM(1)\tx', "Spoofed login\t'\"@SUM(1)\tx"),
        ('Spoofed login\n"=1+1\nx', "Spoofed login\n'\"=1+1\nx"),
        ("A well-known key; a second point", "A well-known key; a second point"),
        ("Tokens = secrets - salt", "Tokens = secrets - salt"),
        ("Two lines\r\n\n=text\n- a bullet", "Two lines\r\n\n'=text\n'- a bullet"),
        ("Two lines\r\n\nof text - plain", "Two lines\r\n\nof text - plain"),
    ],
)
def test_a_csv_cell_has_no_formula_after_a_separator(ruled, title, cell):
    owner, child, _ = ruled
    body = owner.client.get(f"/report/{child}/export.json").json()["report"]
    body["analyses"][0]["rejected_claims"][0]["title"] = title
    text = report_csv(Report.model_validate(body), [])
    for delimiter in ",;\t":
        cells = [
            part
            for row in csv.reader(io.StringIO(text), delimiter=delimiter)
            for part in row
        ]
        assert not [part for part in cells if part.startswith(tuple("=+-@＝＋－＠"))]
    assert list(csv.reader(io.StringIO(text)))[1][2] == cell


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


@pytest.mark.parametrize(
    "sent",
    [
        {"name": "waf.yaml", "evidence_class": "config", "text": "waf: on\n"},
        {"id": "evidence-" + "0" * 32, "evidence_class": "config"},
    ],
)
def test_an_evidence_entry_names_its_class_by_the_key_the_page_sends(sent):
    """The body's key is ``class``. The field name is no second key."""
    from pydantic import TypeAdapter, ValidationError

    from webapp.controls_page import HeldEvidence, NewEvidence

    entry = TypeAdapter(NewEvidence | HeldEvidence)
    with pytest.raises(ValidationError):
        entry.validate_python(sent)
    entry.validate_python(
        {key.replace("evidence_class", "class"): value for key, value in sent.items()}
    )


class TestTheTables:
    """Each table of the pages names each member of its set, so no value
    shows as ``undefined``."""

    def test_no_page_script_copies_a_sentence_of_a_service_table(self):
        """The pause, the reopened analysis, the report page and the Controls
        page read these sentences from the service, so no script holds one."""
        from analysis_service.job_controls import (
            COVER_MEANINGS,
            RULING_LISTS,
            USED_STATE_WORDS,
        )
        from tests.test_webapp import page_javascript
        from webapp.controls_page import SCOPE_HINTS

        scripts = "".join(
            page_javascript(name)
            for name in ("form_controls.js", "report_view.js", "controls.js")
        )
        sentences = [
            *COVER_MEANINGS.values(),
            *FACT_MEANINGS.values(),
            *filter(None, SCOPE_HINTS.values()),
            USED_STATE_WORDS["pending"],
            "left out, because",
        ]
        assert not [sentence for sentence in sentences if sentence in scripts]
        assert not [word for word in RULING_LISTS.values() if f'"{word}"' in scripts]

    def test_each_row_meaning_formats_with_the_row_values(self):
        """The section formats every meaning with the row's version, its
        framework and the version the analysis ran, so no meaning holds a
        brace that the format reads."""
        from analysis_service.job_controls import ROW_MEANINGS

        for meaning in ROW_MEANINGS.values():
            meaning.format(version="v", framework="f", ran="r")

    def test_the_page_tables_name_each_member(self):
        from typing import get_args

        from analysis_service.controls import ScopeType
        from analysis_service.job_controls import (
            COVER_MEANINGS,
            FACT_COUNT_WORDS,
            LEFT_OUT_MEANINGS,
            RESOLUTION_WORDS,
            ROW_MEANINGS,
            RULING_LISTS,
            USED_STATE_WORDS,
            CoverReason,
            FactState,
            LeftOutReason,
            MatchOutcome,
            ResolutionState,
            RowOutcome,
            RowVerdict,
            UsedState,
        )
        from analysis_service.sources import EvidenceClass
        from webapp.controls_page import EVIDENCE_LABELS, SCOPE_HINTS

        assert set(SCOPE_HINTS) == set(get_args(ScopeType))
        assert set(COVER_MEANINGS) == set(get_args(CoverReason))
        assert set(RESOLUTION_WORDS) == set(get_args(ResolutionState))
        assert set(USED_STATE_WORDS) == set(get_args(UsedState))
        assert set(RULING_LISTS) == set(get_args(RowVerdict))

        assert set(SCOPE_WORDS) == set(get_args(ScopeType))
        assert set(EVIDENCE_LABELS) == set(get_args(EvidenceClass))
        assert set(ROW_MEANINGS) == set(get_args(RowOutcome))
        assert set(FACT_COUNT_WORDS) == set(get_args(FactState))
        assert set(MATCH_MEANINGS) == set(get_args(MatchOutcome))
        assert set(LEFT_OUT_MEANINGS) == set(get_args(LeftOutReason))
        assert set(FACT_MEANINGS) == set(get_args(FactState))

    def test_the_element_type_nouns_name_each_prefix_in_glossary_words(self):
        """Each element type prefix has a noun, and each noun is a
        GLOSSARY.md term."""
        from typing import get_args

        from analysis_service.system_model import ElementPrefix

        assert set(ELEMENT_TYPE_NOUNS) == set(get_args(ElementPrefix))
        glossary = (REPO_ROOT / "GLOSSARY.md").read_text(encoding="utf-8")
        for noun in ELEMENT_TYPE_NOUNS.values():
            assert f"**{noun.title()}**" in glossary, noun

    def test_no_page_script_holds_an_element_type_or_scope_type_word(self):
        """Each page names an element type or a scope type with the words the
        server sends, which the Markdown export reads too. A noun that equals
        its key is also the key a script compares, so only the other words
        are refused.

        The test reads a word as a whole string in any of the three quotes,
        the form a table of words takes. It does not read a word in a sentence
        of a page, because page prose names a type in its own words, as in "a
        rectangle is an external entity".
        """
        words = {
            word
            for key, word in [
                *ELEMENT_TYPE_NOUNS.items(),
                *((key, words.noun) for key, words in SCOPE_WORDS.items()),
                *((key, words.label) for key, words in SCOPE_WORDS.items()),
            ]
            if word != key
        }
        for name in test_webapp.STATIC_SCRIPTS:
            script = test_webapp.page_javascript(name)
            for word in words:
                for quote in "\"'`":
                    assert f"{quote}{word}{quote}" not in script, (name, word)

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

    def test_the_page_offers_only_statements_of_a_protection(self):
        from webapp.controls_page import scope_types

        offered = {
            row["type"]: {one["label"] for one in row["statements"]}
            for row in scope_types()
        }
        assert offered["application"] == set()
        assert "Encrypts its data at rest." in offered["store"]
        assert "Only the internal network can reach it." in offered["process"]
        assert "The internet cannot reach it." in offered["process"]
        assert not any("?" in label for label in set().union(*offered.values()))

    def test_each_statement_the_page_offers_is_one_the_document_keeps(self):
        # The page and the document read the protective answer from two
        # places, so each tick must save.
        from analysis_service.controls import ControlDocument
        from webapp.controls_page import scope_types

        for scope_row in scope_types():
            for one in scope_row["statements"]:
                value = one["answer"] or "AES-256 by the KMS"
                fact = (
                    {"key": one["key"], "facets": {one["facet"]: value}}
                    if one["facet"]
                    else {"key": one["key"], "value": value}
                )
                scope = {"type": scope_row["type"], "name": "Orders DB", "aliases": []}
                held = ControlDocument.model_validate(
                    {
                        "id": "control-" + "a" * 32,
                        "name": "Tick",
                        "scope": scope,
                        "facts": [fact],
                        "rows": [],
                        "evidence": [],
                    }
                )
                assert held.facts, one["label"]


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


@pytest.mark.parametrize("end", LINE_ENDS)
def test_a_quoted_line_counts_each_line_end_a_text_may_hold(end):
    """Each line end that ``text_refusal`` lets through starts a line, as it
    does in an editor."""
    from analysis_service.job_controls import CopiedEvidence, _quoted

    file = CopiedEvidence.model_validate(
        {
            "id": f"evidence-{'0' * 32}",
            "name": "waf.yaml",
            "class": "config",
            "sha256": "0" * 64,
            "text": EVIDENCE.replace("\n", end),
        }
    )
    [line] = _quoted("The file says “waf: enabled”.", [file])
    assert (line.line, line.text) == (4, "waf: enabled")
