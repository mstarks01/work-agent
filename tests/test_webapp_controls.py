"""A submission applies the owner's Controls, and the job keeps its own copy
(ADRs 0124 and 0127, #2154).

The owner ticks Controls at the submission, and the job copies each one. The
model exists only after the extraction, so the pause offers each copied
Control with its match, and a save states the resolution. Offline, through
the first-run app, with a runner that pauses a job that asks.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from analysis_service.controls import Control, ControlDocument
from analysis_service.job_controls import NOT_COPIED
from analysis_service.jobs import Checkpoint, InMemoryJobStore, PipelineAwaiting
from analysis_service.principals import Identity, Principal
from analysis_service.sources import text_digest
from analysis_service.system_model import Assumption, DataStore, TrustBoundary
from tests import test_webapp
from tests.factories import valid_model
from tests.test_api import auth, make_client, one_framework, one_source
from tests.test_controls import fact, row
from tests.test_webapp import LOOPBACK, SAME_ORIGIN, posted
from tests.test_webapp_questions import HELD, PausingRunner, app_for, ran
from webapp.main import OPERATOR, Workspace

tiers = test_webapp.tiers

#: Another operator, whose Controls this one must not reach.
BOB = Identity.local("bob")

LIMITS = test_webapp.WEBAPP_CONTROL_LIMITS


def zoned_model():
    """The valid model with a zone that holds each kind of element: the web
    app placed by the description, the orders DB placed by an inference, an
    audit log that no source placed, and a DMZ that holds nothing."""
    model = valid_model()
    label = model.data_stores[0].source_label
    return model.model_copy(
        update={
            "data_stores": [
                *model.data_stores,
                DataStore(
                    id="store:audit-log",
                    name="Audit Log",
                    technology="files",
                    trust_zone="unknown",
                    encryption_at_rest="unknown",
                    data_classification="internal",
                    source_label=label,
                ),
            ],
            "trust_boundaries": [
                *model.trust_boundaries,
                TrustBoundary(id="boundary:dmz", name="DMZ", kind="network"),
            ],
            "assumptions": [
                *model.assumptions,
                Assumption(
                    assumption="the database sits beside the app",
                    element_id="store:orders-db",
                    attribute="trust_zone",
                    basis="both run in the same project",
                ),
            ],
        }
    )


class ZonedRunner(PausingRunner):
    """Pauses on :func:`zoned_model`."""

    #: Whether the next run raises, so its job fails.
    fails = False

    async def run(self, job, on_node):
        if self.fails:
            raise RuntimeError("the run failed")
        if job.pauses():
            checkpoint = Checkpoint(
                system_model=zoned_model(), assertions=HELD.assertions
            )
            return PipelineAwaiting(checkpoint=checkpoint)
        return await super().run(job, on_node)


class Owner:
    """The first-run app over an in-memory store, and the operator's Controls."""

    def __init__(self, tiers) -> None:
        self.store = InMemoryJobStore()
        self.runner = ZonedRunner()
        app = app_for(tiers, self.runner, workspace=Workspace(store=self.store))
        self.client = TestClient(app, base_url=LOOPBACK)

    def owner_id(self) -> str:
        held = asyncio.run(self.store.principal(OPERATOR))
        assert held is not None
        return held.owner_id

    def keep(self, name: str, scope: dict, *, rows=None, identity=OPERATOR) -> str:
        """Keep a Control of ``scope`` with one evidence file, and return its ID."""
        text = f"{name}: on\n"
        evidence_id = f"evidence-{uuid4().hex}"
        document = ControlDocument.model_validate(
            {
                "id": f"control-{uuid4().hex}",
                "name": name,
                "scope": {"name": "", "aliases": []} | scope,
                "facts": [fact()],
                "rows": [row()] if rows is None else rows,
                "evidence": [
                    {
                        "id": evidence_id,
                        "name": f"{name}.yaml",
                        "class": "config",
                        "sha256": text_digest(text),
                        "size_bytes": len(text.encode()),
                    }
                ],
            }
        )
        held = asyncio.run(self.store.principal(identity))
        principal = None
        if held is None:
            principal = Principal.new(identity)
            owner_id = principal.owner_id
        else:
            owner_id = held.owner_id
        outcome = asyncio.run(
            self.store.add_control(
                Control.create(owner_id=owner_id, document=document),
                {evidence_id: text},
                limits=LIMITS,
                principal=principal,
            )
        )
        assert outcome == "added", outcome
        return document.id

    def submit(self, *controls: str, questions: bool = True, **more):
        body = posted("A web app talks to a database.") | {
            "questions": questions,
            "controls": list(controls),
            **more,
        }
        return self.client.post("/analyze", json=body, headers=SAME_ORIGIN)

    def paused(self, *controls: str) -> str:
        started = self.submit(*controls)
        assert started.status_code == 200, started.text
        run_id = started.json()["run"]
        assert ran(self.client, run_id) == "awaiting-answers"
        return run_id

    def questions(self, run_id: str) -> dict:
        return self.client.get(f"/workspace/runs/{run_id}").json()["questions"]

    def offer(self, run_id: str) -> dict[str, dict]:
        return {entry["name"]: entry for entry in self.questions(run_id)["controls"]}

    def save(self, run_id: str, scopes: list[dict], revision: int = 0):
        return self.client.post(
            f"/answer/{run_id}",
            json={"save": True, "revision": revision, "control_scopes": scopes},
            headers=SAME_ORIGIN,
        )


@pytest.fixture
def owner(tiers):
    return Owner(tiers)


ORDERS_DB = {"type": "store", "name": "Orders DB"}
INTERNAL = {"type": "boundary", "name": "Internal Network"}


class TestTheOffer:
    """The pause offers each copied Control with its match and its reason."""

    def test_each_match_outcome_and_reason(self, owner):
        ids = [
            owner.keep("Encrypted", ORDERS_DB),
            owner.keep(
                "WAF", {"type": "process", "name": "Edge", "aliases": ["Web App"]}
            ),
            owner.keep("SSO", {"type": "application"}),
            owner.keep("Billing", {"type": "process", "name": "Billing"}),
            owner.keep("Perimeter", {"type": "boundary", "name": "DMZ"}),
            owner.keep("Near", {"type": "store", "name": "Order DB"}),
        ]
        offer = owner.offer(owner.paused(*ids))
        assert {name: entry["match"] for name, entry in offer.items()} == {
            "Encrypted": "matched-by-name",
            "WAF": "matched-by-alias",
            "SSO": "application",
            "Billing": "missing-element",
            "Perimeter": "empty-zone",
            "Near": "missing-element",
        }
        assert offer["Encrypted"]["element"] == {
            "id": "store:orders-db",
            "name": "Orders DB",
        }
        assert offer["WAF"]["element"] == {"id": "process:web-app", "name": "Web App"}
        assert offer["Billing"]["choices"] == [
            {"id": "process:web-app", "name": "Web App"}
        ]
        # A similar name proposes nothing: no element, and nothing applies.
        assert offer["Near"]["element"] is None
        assert offer["Near"]["resolution"] == {
            "state": "left-out",
            "control": ids[5],
            "reason": "missing-element",
        }
        assert offer["Perimeter"]["resolution"]["reason"] == "empty-zone"
        assert offer["SSO"]["resolution"]["state"] == "applied"

    def test_a_zone_ticks_each_placed_element_and_lists_the_others(self, owner):
        offer = owner.offer(owner.paused(owner.keep("Segmented", INTERNAL)))
        covers = {
            cover["element"]: (cover["reason"], cover["ticked"])
            for cover in offer["Segmented"]["covers"]
        }
        assert covers == {
            "process:web-app": ("stated-zone", True),
            "store:orders-db": ("zone-inferred", False),
            "store:audit-log": ("no-zone-stated", False),
        }
        assert offer["Segmented"]["resolution"]["covers"] == [
            {"element": "process:web-app", "reason": "stated-zone", "ticked": True},
            {"element": "store:orders-db", "reason": "zone-inferred", "ticked": False},
            {"element": "store:audit-log", "reason": "no-zone-stated", "ticked": False},
        ]

    def test_a_row_of_another_package_version_is_offered_unticked(self, owner):
        rows = [row("V11.2"), row("V11", version="4.0.3")]
        offer = owner.offer(owner.paused(owner.keep("Encrypted", ORDERS_DB, rows=rows)))
        assert [(r["unit"], r["ticked"]) for r in offer["Encrypted"]["rows"]] == [
            ("V11.2", True),
            ("V11", False),
        ]

    def test_a_job_with_no_control_offers_no_key(self, owner):
        assert "controls" not in owner.questions(owner.paused())


class TestTheSave:
    """A pause save states each Control's resolution, and the service checks it."""

    def test_a_save_maps_ticks_and_leaves_out(self, owner):
        billing = owner.keep("Billing", {"type": "process", "name": "Billing"})
        zone = owner.keep("Segmented", INTERNAL)
        sso = owner.keep("SSO", {"type": "application"})
        run_id = owner.paused(billing, zone, sso)
        saved = owner.save(
            run_id,
            [
                {"control": billing, "element": "process:web-app"},
                {
                    "control": zone,
                    "element": "boundary:internal-network",
                    "ticked": ["store:orders-db"],
                },
            ],
        )
        assert saved.status_code == 200, saved.text
        offer = owner.offer(run_id)
        assert offer["Billing"]["resolution"] == {
            "state": "applied",
            "control": billing,
            "outcome": "owner-mapped",
            "element": "process:web-app",
            "covers": [],
        }
        assert [
            (cover["element"], cover["ticked"])
            for cover in offer["Segmented"]["resolution"]["covers"]
        ] == [
            ("process:web-app", False),
            ("store:orders-db", True),
            ("store:audit-log", False),
        ]
        assert offer["SSO"]["resolution"] == {
            "state": "left-out",
            "control": sso,
            "reason": "unticked",
        }

    def test_a_left_out_control_can_be_ticked_again(self, owner):
        sso = owner.keep("SSO", {"type": "application"})
        run_id = owner.paused(sso)
        assert owner.save(run_id, []).status_code == 200
        assert owner.offer(run_id)["SSO"]["resolution"]["state"] == "left-out"
        assert owner.save(run_id, [{"control": sso}], revision=1).status_code == 200
        assert owner.offer(run_id)["SSO"]["resolution"]["state"] == "applied"

    @pytest.mark.parametrize(
        ("entry", "message"),
        [
            ({"element": "store:orders-db"}, "protects a process"),
            ({"element": "process:gone"}, "is not an element of this model"),
            ({"element": "process:web-app", "ticked": ["store:audit-log"]}, "ticks"),
        ],
    )
    def test_an_element_entry_is_refused(self, owner, entry, message):
        billing = owner.keep("Billing", {"type": "process", "name": "Billing"})
        refused = owner.save(owner.paused(billing), [{"control": billing} | entry])
        assert refused.status_code == 400
        assert message in refused.json()["message"]

    def test_a_tick_outside_the_zone_list_is_refused(self, owner):
        zone = owner.keep("Segmented", INTERNAL)
        refused = owner.save(
            owner.paused(zone),
            [
                {
                    "control": zone,
                    "element": "boundary:internal-network",
                    "ticked": ["entity:customer"],
                }
            ],
        )
        assert refused.status_code == 400
        assert "outside" in refused.json()["message"]

    def test_an_empty_zone_saved_with_no_tick_is_left_out(self, owner):
        zone = owner.keep("Perimeter", {"type": "boundary", "name": "DMZ"})
        run_id = owner.paused(zone)
        saved = owner.save(run_id, [{"control": zone, "element": "boundary:dmz"}])
        assert saved.status_code == 200, saved.text
        assert owner.offer(run_id)["Perimeter"]["resolution"] == {
            "state": "left-out",
            "control": zone,
            "reason": "empty-zone",
        }

    def test_two_entries_for_one_control_are_refused(self, owner):
        sso = owner.keep("SSO", {"type": "application"})
        refused = owner.save(owner.paused(sso), [{"control": sso}, {"control": sso}])
        assert refused.status_code == 400
        assert "twice" in refused.json()["message"]

    def test_a_control_the_job_does_not_hold_is_refused_with_no_id(self, owner):
        sso = owner.keep("SSO", {"type": "application"})
        other = owner.keep("Other", {"type": "application"})
        refused = owner.save(owner.paused(sso), [{"control": other}])
        assert refused.status_code == 400
        assert refused.json()["message"] == NOT_COPIED

    def test_many_controls_resolve_to_one_element(self, owner):
        waf = owner.keep("WAF", {"type": "process", "name": "Web App"})
        limit = owner.keep("Rate limit", {"type": "process", "name": "Web App"})
        run_id = owner.paused(waf, limit)
        saved = owner.save(
            run_id,
            [
                {"control": waf, "element": "process:web-app"},
                {"control": limit, "element": "process:web-app"},
            ],
        )
        assert saved.status_code == 200, saved.text


class TestTheSubmission:
    """``/analyze`` names Controls by ID, and the job copies each one."""

    def test_a_missing_and_another_owner_s_id_get_one_refusal(self, owner):
        owner.keep("SSO", {"type": "application"})
        foreign = owner.keep("Theirs", {"type": "application"}, identity=BOB)
        missing = owner.submit(f"control-{'0' * 32}")
        theirs = owner.submit(foreign)
        assert missing.status_code == theirs.status_code == 400
        assert missing.json() == theirs.json()
        assert foreign not in theirs.text

    def test_a_repeated_id_is_refused(self, owner):
        sso = owner.keep("SSO", {"type": "application"})
        refused = owner.submit(sso, sso)
        assert refused.status_code == 400
        assert "twice" in refused.json()["message"]

    def test_a_later_edit_or_delete_changes_no_copy(self, owner):
        sso = owner.keep("SSO", {"type": "application"})
        run_id = owner.paused(sso)
        before = owner.client.get(f"/workspace/runs/{run_id}").json()
        assert asyncio.run(owner.store.delete_control(sso, owner.owner_id()))
        after = owner.client.get(f"/workspace/runs/{run_id}").json()
        assert after == before
        assert after["controls_used"] == [
            {"id": sso, "name": "SSO", "state": "applied"}
        ]
        copies = asyncio.run(owner.store.controls(run_id, owner.owner_id()))
        assert [copy.document.name for copy in copies] == ["SSO"]
        assert copies[0].evidence[0].text == "SSO: on\n"

    def test_a_job_that_does_not_pause_lists_the_default(self, owner):
        sso = owner.keep("SSO", {"type": "application"})
        billing = owner.keep("Billing", {"type": "process", "name": "Billing"})
        started = owner.submit(sso, billing, questions=False)
        assert started.status_code == 200, started.text
        run_id = started.json()["run"]
        assert ran(owner.client, run_id) == "completed"
        used = owner.client.get(f"/workspace/runs/{run_id}").json()["controls_used"]
        assert [(entry["name"], entry["state"]) for entry in used] == [
            ("SSO", "applied"),
            ("Billing", "left-out"),
        ]

    def test_a_job_that_serves_no_model_lists_each_control_pending(self, owner):
        sso = owner.keep("SSO", {"type": "application"})
        owner.runner.fails = True
        started = owner.submit(sso, questions=False)
        assert started.status_code == 200, started.text
        run_id = started.json()["run"]
        assert ran(owner.client, run_id) == "failed"
        used = owner.client.get(f"/workspace/runs/{run_id}").json()["controls_used"]
        assert used == [{"id": sso, "name": "SSO", "state": "pending"}]

    def test_a_job_with_no_control_lists_none(self, owner):
        run_id = owner.paused()
        assert (
            owner.client.get(f"/workspace/runs/{run_id}").json()["controls_used"] == []
        )


class TestTheChildren:
    """A Resumed Job and an Amendment copy the parent's copy; a follow-up
    with ``answers_from`` reads the store again."""

    def test_a_resumed_job_copies_the_parent_s_copy_and_resolution(self, owner):
        zone = owner.keep("Segmented", INTERNAL)
        run_id = owner.paused(zone)
        owner.save(
            run_id,
            [
                {
                    "control": zone,
                    "element": "boundary:internal-network",
                    "ticked": ["store:audit-log"],
                }
            ],
        )
        asyncio.run(owner.store.delete_control(zone, owner.owner_id()))
        resumed = owner.client.post(
            f"/answer/{run_id}", json={"revision": 1}, headers=SAME_ORIGIN
        )
        assert resumed.status_code == 200, resumed.text
        child = resumed.json()["run"]
        ran(owner.client, child)
        held = asyncio.run(owner.store.get(child))
        assert [copy.document.id for copy in held.controls] == [zone]
        assert held.control_scopes[0].element_ids() == ("store:audit-log",)

    def test_an_amendment_copies_the_documents_and_matches_again(self, owner):
        zone = owner.keep("Segmented", INTERNAL)
        run_id = owner.paused(zone)
        owner.save(run_id, [])
        asyncio.run(owner.store.delete_control(zone, owner.owner_id()))
        amended = owner.client.post(
            f"/amend/{run_id}",
            json={"text": "The DB sits in the internal network.", "revision": 1},
            headers=SAME_ORIGIN,
        )
        assert amended.status_code == 200, amended.text
        child = amended.json()["run"]
        assert ran(owner.client, child) == "awaiting-answers"
        assert owner.offer(child)["Segmented"]["resolution"]["state"] == "applied"

    def test_a_follow_up_with_answers_from_reads_the_store_again(self, owner):
        sso = owner.keep("SSO", {"type": "application"})
        first = owner.paused(sso)
        owner.client.post(
            f"/answer/{first}",
            json={
                "save": True,
                "revision": 0,
                "facts": [
                    {
                        "key": [
                            "store:orders-db",
                            "encryption_at_rest",
                            "",
                            "",
                            "",
                            "",
                        ],
                        "value": "AES-256",
                    }
                ],
            },
            headers=SAME_ORIGIN,
        )
        asyncio.run(owner.store.delete_control(sso, owner.owner_id()))
        renamed = owner.keep("SSO v2", {"type": "application"})
        started = owner.submit(renamed, answers_from=first)
        assert started.status_code == 200, started.text
        child = started.json()["run"]
        assert ran(owner.client, child) == "awaiting-answers"
        assert list(owner.offer(child)) == ["SSO v2"]


class TestKeepThisName:
    """ "Keep this name" appends one alias to the owner's Control."""

    def alias(self, owner, control_id: str, alias: str):
        return owner.client.post(
            f"/workspace/controls/{control_id}/aliases",
            json={"alias": alias},
            headers=SAME_ORIGIN,
        )

    def test_the_alias_is_appended_once(self, owner):
        billing = owner.keep("Billing", {"type": "process", "name": "Billing"})
        kept = self.alias(owner, billing, "Web App")
        assert kept.status_code == 200, kept.text
        assert kept.json()["aliases"] == ["Web App"]
        held = asyncio.run(owner.store.get_control(billing, owner.owner_id()))
        assert held.document.scope.aliases == ["Web App"]
        again = self.alias(owner, billing, "web app")
        assert again.status_code == 400
        assert "twice" in again.json()["message"]

    def test_an_alias_another_scope_holds_is_refused(self, owner):
        owner.keep("WAF", {"type": "process", "name": "Web App"})
        billing = owner.keep("Billing", {"type": "process", "name": "Billing"})
        assert self.alias(owner, billing, "Web App").status_code == 409

    def test_an_unknown_and_a_foreign_control_are_missing(self, owner):
        owner.keep("SSO", {"type": "application"})
        foreign = owner.keep(
            "Theirs",
            {"type": "process", "name": "X"},
            identity=BOB,
        )
        assert self.alias(owner, foreign, "Y").status_code == 404
        assert self.alias(owner, f"control-{'0' * 32}", "Y").status_code == 404

    def test_a_cross_origin_request_is_refused(self, owner):
        billing = owner.keep("Billing", {"type": "process", "name": "Billing"})
        refused = owner.client.post(
            f"/workspace/controls/{billing}/aliases", json={"alias": "Web App"}
        )
        assert refused.status_code == 403


class TestV1:
    """``/v1`` has no Controls surface."""

    @pytest.mark.parametrize("key", ["controls", "control_scopes"])
    def test_a_submission_with_a_controls_key_is_refused(self, key):
        client, store = make_client()
        body = {"sources": one_source(), "frameworks": one_framework(), key: []}
        refused = client.post("/v1/jobs", json=body, headers=auth())
        assert refused.status_code == 422
        assert not store._records

    def test_an_answer_with_control_scopes_is_refused(self):
        client, _ = make_client(runner=PausingRunner())
        body = {
            "sources": one_source(),
            "frameworks": one_framework(),
            "questions": True,
        }
        job_id = client.post("/v1/jobs", json=body, headers=auth()).json()["job_id"]
        refused = client.post(
            f"/v1/jobs/{job_id}/answers",
            json={"revision": 0, "control_scopes": []},
            headers=auth(),
        )
        assert refused.status_code == 422

    def test_a_v1_job_writes_no_controls_object(self):
        client, store = make_client()
        body = {"sources": one_source(), "frameworks": one_framework()}
        job_id = client.post("/v1/jobs", json=body, headers=auth()).json()["job_id"]
        held = asyncio.run(store.get(job_id))
        assert held.controls == []
        assert held.control_scopes is None
