"""A submission applies the owner's Controls, and the job keeps its own copy
(ADRs 0124 and 0127, #2154).

The owner ticks Controls at the submission, and the job copies each one. The
model exists only after the extraction, so the pause offers each copied
Control with its match, and a save states the resolution. Offline, through
the first-run app, with a runner that pauses a job that asks.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from analysis_service.controls import (
    Control,
    ControlDocument,
    ControlScope,
    scope_phrase,
)
from analysis_service.fact_answers import FactAnswer
from analysis_service.graph import (
    PAUSE_NODE,
    STATE_CONTROL_LINES,
    STATE_FACT_ANSWERS,
)
from analysis_service.job_answers import JobAnswers
from analysis_service.job_controls import (
    COVER_MEANINGS,
    FACT_MEANINGS,
    NOT_COPIED,
    resolutions,
)
from analysis_service.job_state import StoredRefusal, job_state
from analysis_service.jobs import (
    Admission,
    Checkpoint,
    InMemoryJobStore,
    PipelineAwaiting,
)
from analysis_service.links import with_link_answers
from analysis_service.pipeline import _seeded_state
from analysis_service.principals import Identity, Principal
from analysis_service.sources import text_digest
from analysis_service.system_model import Assumption, DataStore, TrustBoundary
from tests import test_webapp
from tests.factories import valid_model
from tests.test_api import auth, make_client, one_framework, one_source
from tests.test_controls import fact, row
from tests.test_webapp import LOOPBACK, SAME_ORIGIN, posted
from tests.test_webapp_questions import (
    FACT,
    HELD,
    PausingRunner,
    _run_form_script,
    app_for,
    ran,
)
from webapp.main import _NOT_CONTINUED, OPERATOR, Workspace
from webapp.page_words import INITIAL_PROGRESS_WORDS

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

    #: Whether each run raises, so its job fails.
    fails = False
    #: Set by a test that reads the line of the pause: the run sends the
    #: pause node and waits for the event.
    held: threading.Event | None = None
    #: How many runs extracted a description and paused.
    heads = 0

    async def run(self, job, on_node):
        if self.fails:
            raise RuntimeError("the run failed")
        if job.pauses():
            self.heads += 1
            if self.held is not None:
                await on_node(PAUSE_NODE)
                while not self.held.is_set():
                    await asyncio.sleep(0.01)
            checkpoint = Checkpoint(
                system_model=zoned_model(), assertions=HELD.assertions
            )
            return PipelineAwaiting(checkpoint=checkpoint)
        return await super().run(job, on_node)


class Owner:
    """The first-run app over an in-memory store, and the operator's Controls."""

    def __init__(self, tiers) -> None:
        self.tiers = tiers
        self.store = InMemoryJobStore()
        self.runner = ZonedRunner()
        self.restart()

    def restart(self) -> None:
        """Serve a new app over the same store, as a restart of the process
        does: the store keeps its jobs, and the workspace runs no task."""
        self.workspace = Workspace(store=self.store)
        app = app_for(self.tiers, self.runner, workspace=self.workspace)
        self.client = TestClient(app, base_url=LOOPBACK)

    def owner_id(self) -> str:
        held = asyncio.run(self.store.principal(OPERATOR))
        assert held is not None
        return held.owner_id

    def keep(
        self, name: str, scope: dict, *, rows=None, facts=None, identity=OPERATOR
    ) -> str:
        """Keep a Control of ``scope`` with one evidence file, and return its ID."""
        text = f"{name}: on\n"
        evidence_id = f"evidence-{uuid4().hex}"
        document = ControlDocument.model_validate(
            {
                "id": f"control-{uuid4().hex}",
                "name": name,
                "scope": {"name": "", "aliases": []} | scope,
                "facts": (
                    ([] if scope["type"] == "application" else [fact()])
                    if facts is None
                    else facts
                ),
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

    def answer(self, run_id: str, **body):
        return self.client.post(f"/answer/{run_id}", json=body, headers=SAME_ORIGIN)

    def resumed(self, run_id: str, revision: int = 0, **body) -> str:
        """Continue ``run_id``, and return the Resumed Job once it ran."""
        started = self.answer(run_id, revision=revision, **body)
        assert started.status_code == 200, started.text
        child = started.json()["run"]
        assert ran(self.client, child) == "completed"
        return child

    def answers_source(self, run_id: str) -> list[str]:
        held = asyncio.run(self.store.get(run_id))
        assert held is not None
        [source] = [one for one in held.whole_sources() if one.kind == "answers"]
        return source.text.splitlines()

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
        for entry in offer.values():
            scope = ControlScope.model_validate(entry["scope"])
            assert entry["scope_phrase"] == scope_phrase(scope)
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
            {"id": "process:web-app", "name": "Web App", "covers": []}
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
        assert all(
            cover["meaning"] == COVER_MEANINGS[cover["reason"]]
            for cover in offer["Segmented"]["covers"]
        )
        assert offer["Segmented"]["resolution"]["covers"] == [
            {"element": "process:web-app", "reason": "stated-zone", "ticked": True},
            {"element": "store:orders-db", "reason": "zone-inferred", "ticked": False},
            {"element": "store:audit-log", "reason": "no-zone-stated", "ticked": False},
        ]

    def test_a_zone_choice_lists_the_parts_of_that_zone(self, owner):
        moved = owner.keep("Moved", {"type": "boundary", "name": "Office"})
        run_id = owner.paused(moved)
        choices = {
            choice["id"]: choice for choice in owner.offer(run_id)["Moved"]["choices"]
        }
        assert list(choices) == [
            "boundary:internet",
            "boundary:internal-network",
            "boundary:dmz",
        ]
        assert [
            (cover["element"], cover["name"], cover["ticked"])
            for cover in choices["boundary:internal-network"]["covers"]
        ] == [
            ("process:web-app", "Web App", True),
            ("store:orders-db", "Orders DB", False),
            ("store:audit-log", "Audit Log", False),
        ]
        mapped = [
            {
                "control": moved,
                "element": "boundary:internal-network",
                "ticked": ["process:web-app"],
            }
        ]
        assert owner.save(run_id, mapped).status_code == 200
        resolution = owner.offer(run_id)["Moved"]["resolution"]
        assert (resolution["state"], resolution["outcome"]) == (
            "applied",
            "owner-mapped",
        )

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

    def test_an_element_of_another_type_is_refused_in_the_scope_words(self, owner):
        partner = owner.keep("Partner", {"type": "entity", "name": "Partner"})
        refused = owner.save(
            owner.paused(partner),
            [{"control": partner, "element": "process:web-app"}],
        )
        assert refused.status_code == 400
        assert (
            "“Partner” protects an external entity, and process:web-app is not one."
            in refused.json()["message"]
        )

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

    def test_a_control_that_changes_nothing_in_the_analysis_is_refused(self, owner):
        # An ASVS row and no fact change nothing in a STRIDE-only analysis.
        asvs_only = owner.keep("TLS", {"type": "application"})
        stride = posted("x", ("stride",))["frameworks"]
        refused = owner.submit(asvs_only, frameworks=stride)
        assert refused.status_code == 400
        assert refused.json()["message"].startswith("The Control “TLS” states no fact")
        # A fact applies in any framework, so the same rows with a fact pass.
        with_fact = owner.keep("Encrypted", ORDERS_DB)
        assert owner.submit(with_fact, frameworks=stride).status_code == 200

    def test_a_row_of_another_version_changes_nothing(self, owner):
        # The row names STRIDE at a version this service does not carry, so
        # the copy leaves it unticked and the Control changes nothing.
        old = owner.keep(
            "Old", {"type": "application"}, rows=[row("spoofing", "stride", "0.9")]
        )
        listed = owner.client.get("/workspace/controls").json()
        assert [entry["current_rows"] for entry in listed] == [[False]]
        stride = posted("x", ("stride",))["frameworks"]
        refused = owner.submit(old, frameworks=stride)
        assert refused.status_code == 400
        assert "at the version this service carries" in refused.json()["message"]

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
            {"id": sso, "name": "SSO", "state": "applied", "state_text": "applied"}
        ]
        copies = asyncio.run(owner.store.controls(run_id, owner.owner_id()))
        assert [copy.document.name for copy in copies] == ["SSO"]
        assert copies[0].evidence[0].text == "SSO: on\n"

    def test_a_job_that_serves_no_model_lists_each_control_pending(self, owner):
        sso = owner.keep("SSO", {"type": "application"})
        owner.runner.fails = True
        started = owner.submit(sso)
        assert started.status_code == 200, started.text
        run_id = started.json()["run"]
        assert ran(owner.client, run_id) == "failed"
        used = owner.client.get(f"/workspace/runs/{run_id}").json()["controls_used"]
        assert used == [
            {
                "id": sso,
                "name": "SSO",
                "state": "pending",
                "state_text": "not matched yet",
            }
        ]

    def test_a_withheld_report_lists_each_control_pending(self, owner, monkeypatch):
        sso = owner.keep("SSO", {"type": "application"})
        resumed = owner.client.post(
            f"/answer/{owner.paused(sso)}", json={"revision": 0}, headers=SAME_ORIGIN
        )
        assert resumed.status_code == 200, resumed.text
        run_id = resumed.json()["run"]
        assert ran(owner.client, run_id) == "completed"
        monkeypatch.setattr(JobAnswers, "withheld", lambda self, record: object())
        used = owner.client.get(f"/workspace/runs/{run_id}").json()["controls_used"]
        assert used == [
            {
                "id": sso,
                "name": "SSO",
                "state": "pending",
                "state_text": "not matched yet",
            }
        ]

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

    def test_an_alias_over_the_label_bound_is_refused_with_the_scope_rule(self, owner):
        billing = owner.keep("Billing", {"type": "process", "name": "Billing"})
        refused = self.alias(owner, billing, "a" * 201)
        assert refused.status_code == 400, refused.text
        assert "an alias is over 200 characters" in refused.json()["message"]

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


DB_KEY = ["store:orders-db", "encryption_at_rest", "", "", "", ""]
EDGE_KEY = ["process:web-app", "exposure", "", "", "", ""]
DB_LINE = (
    'The encryption_at_rest of store:orders-db is "AES-256". From the Control'
    ' "Encrypted".'
)


def states(record) -> list[tuple]:
    return [
        (entry.name, list(fact.answer.key), fact.answer.value, fact.state, fact.stated)
        for entry in record.control_facts
        for fact in entry.facts
    ]


def answered(questions: dict) -> dict[str, dict]:
    return {row["label"]: row for row in questions["answered_early"]}


class TestTheFacts:
    """A Control fills only an open fact, on the rung between an own answer
    and a carried answer (ADR 0125, #2155)."""

    def test_an_open_fact_is_written_and_a_stated_fact_is_recorded(self, owner):
        db = owner.keep("Encrypted", ORDERS_DB)
        edge = owner.keep(
            "Edge",
            {"type": "process", "name": "Web App"},
            facts=[fact("exposure", "internal")],
        )
        child = owner.resumed(owner.paused(db, edge))
        held = asyncio.run(owner.store.get(child))
        assert states(held) == [
            ("Encrypted", DB_KEY, "AES-256", "written", ""),
            ("Edge", EDGE_KEY, "internal", "stated", "internet-facing"),
        ]
        assert owner.answers_source(child) == [DB_LINE]
        assert [list(fact.key) for fact in held.answers.facts] == [DB_KEY]

    def test_a_control_fact_is_not_asked_and_is_marked(self, owner):
        run_id = owner.paused(owner.keep("Encrypted", ORDERS_DB))
        questions = owner.questions(run_id)
        assert DB_KEY not in [row["key"] for row in questions["early_questions"]]
        row = answered(questions)["Is data stored in Orders DB encrypted?"]
        assert row["answer"]["value"] == "AES-256"
        assert row["origin"]["rung"] == "control"
        assert row["origin"]["name"] == "Encrypted"

    def test_an_own_answer_lies_over_the_control_fact(self, owner):
        run_id = owner.paused(owner.keep("Encrypted", ORDERS_DB))
        saved = owner.answer(
            run_id, save=True, revision=0, facts=[{"key": DB_KEY, "value": "KMS"}]
        )
        assert saved.status_code == 200, saved.text
        row = answered(owner.questions(run_id))[
            "Is data stored in Orders DB encrypted?"
        ]
        assert (row["answer"]["value"], row["origin"]["rung"]) == ("KMS", "answer")
        assert states(asyncio.run(owner.store.get(run_id))) == [
            ("Encrypted", DB_KEY, "AES-256", "changed", "")
        ]
        child = owner.resumed(run_id, revision=1)
        held = asyncio.run(owner.store.get(child))
        assert states(held)[0][3] == "changed"
        assert owner.answers_source(child) == [
            DB_LINE,
            'The encryption_at_rest of store:orders-db is "KMS".',
        ]
        assert [fact.value for fact in held.written_answers().facts] == ["KMS"]

    def test_an_unknown_answer_takes_the_fact_back_for_the_job(self, owner):
        run_id = owner.paused(owner.keep("Encrypted", ORDERS_DB))
        child = owner.resumed(run_id, facts=[{"key": DB_KEY, "value": "unknown"}])
        held = asyncio.run(owner.store.get(child))
        assert states(held)[0][3] == "changed"
        assert [fact.value for fact in held.written_answers().facts] == ["unknown"]

    def test_the_follow_up_cannot_take_a_control_fact_back(self, owner):
        child = owner.resumed(owner.paused(owner.keep("Encrypted", ORDERS_DB)))
        refused = owner.answer(
            child, revision=0, facts=[{"key": DB_KEY, "value": "unknown"}]
        )
        assert refused.status_code == 400
        assert "an earlier answer settled this fact" in refused.json()["message"]

    def test_two_controls_with_two_values_are_refused(self, owner):
        aes = owner.keep("Encrypted", ORDERS_DB)
        kms = owner.keep("KMS", ORDERS_DB, facts=[fact(value="KMS")])
        run_id = owner.paused(aes, kms)
        message = (
            "The Controls “Encrypted” and “KMS” write “encryption_at_rest” on"
            " Orders DB with two values: “AES-256” and “KMS”. Untick one of them,"
            " then continue."
        )
        both = [
            {"control": control, "element": "store:orders-db"} for control in (aes, kms)
        ]
        for body in ({"save": True, "control_scopes": both}, {}):
            refused = owner.answer(run_id, revision=0, **body)
            assert refused.status_code == 400
            assert refused.json()["message"] == message
        untick = [{"control": aes, "element": "store:orders-db"}]
        saved = owner.answer(run_id, revision=0, save=True, control_scopes=untick)
        assert saved.status_code == 200, saved.text

    def test_two_controls_with_one_value_are_both_writers(self, owner):
        aes = owner.keep("Encrypted", ORDERS_DB)
        again = owner.keep("Encrypted again", ORDERS_DB)
        child = owner.resumed(owner.paused(aes, again))
        held = asyncio.run(owner.store.get(child))
        assert [(state[0], state[3]) for state in states(held)] == [
            ("Encrypted", "written"),
            ("Encrypted again", "written"),
        ]
        assert owner.answers_source(child) == [
            DB_LINE,
            DB_LINE.replace('"Encrypted"', '"Encrypted again"'),
        ]

    def test_the_pause_says_what_became_of_each_control_fact(self, owner):
        run_id = owner.paused(owner.keep("Encrypted", ORDERS_DB))
        facts = owner.questions(run_id)["control_facts"]
        assert [(fact["state"], fact["meaning"]) for fact in facts] == [
            ("written", FACT_MEANINGS["written"])
        ]

    def test_an_untick_takes_the_control_fact_away(self, owner):
        run_id = owner.paused(owner.keep("Encrypted", ORDERS_DB))
        saved = owner.answer(run_id, save=True, revision=0, control_scopes=[])
        assert saved.status_code == 200, saved.text
        questions = owner.questions(run_id)
        assert DB_KEY in [row["key"] for row in questions["early_questions"]]
        assert questions["control_facts"] == []

    def test_a_carried_answer_to_a_control_fact_is_dropped(self, owner):
        first = owner.paused()
        saved = owner.answer(
            first, save=True, revision=0, facts=[{"key": DB_KEY, "value": "KMS"}]
        )
        assert saved.status_code == 200, saved.text
        db = owner.keep("Encrypted", ORDERS_DB)
        started = owner.submit(db, answers_from=first)
        assert started.status_code == 200, started.text
        child = started.json()["run"]
        assert ran(owner.client, child) == "awaiting-answers"
        questions = owner.questions(child)
        assert [
            (row["key"], row["reason"]) for row in questions["carried_dropped"]
        ] == [(DB_KEY, "control-states")]
        row = answered(questions)["Is data stored in Orders DB encrypted?"]
        assert (row["answer"]["value"], row["origin"]["rung"]) == ("AES-256", "control")

    def test_the_run_composes_the_answers_source_of_the_resumed_job(self, owner):
        run_id = owner.paused(owner.keep("Encrypted", ORDERS_DB))
        flow = ["flow:process:web-app>store:orders-db>store-order"]
        own = {"key": [*flow, "encryption_in_transit", "", "", "", ""], "value": "TLS"}
        job = asyncio.run(owner.store.get(owner.resumed(run_id, facts=[own])))
        [source] = [one for one in job.sources if one.kind == "answers"]
        seeded = _seeded_state(job)
        [composed] = with_link_answers(
            [],
            [],
            [FactAnswer.model_validate(one) for one in seeded[STATE_FACT_ANSWERS]],
            [
                (name, FactAnswer.model_validate(one))
                for name, one in seeded[STATE_CONTROL_LINES]
            ],
        )
        assert composed.text == source.text
        assert source.text.splitlines()[0] == DB_LINE

    def test_a_final_report_takes_a_correction_of_a_control_fact(self, owner):
        child = owner.resumed(owner.paused(owner.keep("Encrypted", ORDERS_DB)))
        final = owner.resumed(child, facts=[dict(FACT)])
        corrected = owner.client.post(
            f"/correct/{final}",
            json={"facts": [{"key": DB_KEY, "value": "KMS"}], "revision": 0},
            headers=SAME_ORIGIN,
        )
        assert corrected.status_code == 200, corrected.text
        page = owner.client.get(f"/report/{final}").text
        match = re.search(r'id="corrections"[^>]*>(.*?)</script>', page, re.DOTALL)
        rows = {tuple(row["key"]): row for row in json.loads(match.group(1))["answers"]}
        assert rows[tuple(DB_KEY)]["answer"]["value"] == "KMS"
        assert rows[tuple(DB_KEY)]["corrected"] is True


def end_frames(stream: str) -> dict[str, dict]:
    """Each frame after the store's events, by its event name."""
    return {
        name: json.loads(data)
        for name, data in re.findall(r"event: (\S+)\ndata: (.*)\n", stream)
        if name != "status" and name != "node"
    }


class TestTheAutomaticContinue:
    """A submission with Controls and no questions pauses, and the service
    continues it at once with no own answers (ADR 0125, #2170)."""

    @pytest.fixture(autouse=True)
    def one_loop(self, owner):
        """Every request on one event loop, as the server runs them: the
        continue runs in the job's task after the submission answered."""
        with owner.client:
            yield

    def continued(self, owner, *controls: str) -> tuple[str, dict[str, dict]]:
        """Submit ``controls`` with no questions, and read the run's stream
        to its end. Returns the run and its end frames."""
        started = owner.submit(*controls, questions=False)
        assert started.status_code == 200, started.text
        run_id = started.json()["run"]
        stream = owner.client.get(f"/events/{run_id}").text
        return run_id, end_frames(stream)

    def test_the_job_completes_with_each_control_fact_written(self, owner):
        _, frames = self.continued(owner, owner.keep("Encrypted", ORDERS_DB))
        # The stream sends "continuing" where the continue still runs when
        # the run's events end, and the page follows "continued" either way.
        assert list(frames)[-1] == "continued"
        assert set(frames) <= {"continuing", "continued"}
        child = frames["continued"]["run"]
        assert ran(owner.client, child) == "completed"
        assert owner.answers_source(child) == [DB_LINE]
        assert states(asyncio.run(owner.store.get(child))) == [
            ("Encrypted", DB_KEY, "AES-256", "written", "")
        ]
        assert owner.runner.heads == 1

    @pytest.mark.parametrize(
        ("questions", "words"),
        [(False, "Preparing your Controls…"), (True, "Generating questions…")],
    )
    def test_the_line_before_the_pause_names_what_the_pause_is_for(
        self, owner, questions, words
    ):
        owner.runner.held = threading.Event()
        control = owner.keep("Encrypted", ORDERS_DB)
        started = owner.submit(control, questions=questions)
        release = threading.Timer(0.3, owner.runner.held.set)
        release.start()
        try:
            stream = owner.client.get(f"/events/{started.json()['run']}").text
        finally:
            release.cancel()
            owner.runner.held.set()
        lines = re.findall(r"event: node\ndata: (.*)\n", stream)
        assert [json.loads(line)["words"] for line in lines] == [
            INITIAL_PROGRESS_WORDS["description"],
            words,
        ]

    def test_the_paused_job_reads_as_its_resumed_job(self, owner):
        """The parent waits with no task, and its Resumed Job locks it, so the
        status route serves the child and shows no reason for a pause."""
        run_id, frames = self.continued(owner, owner.keep("Encrypted", ORDERS_DB))
        child = frames["continued"]["run"]
        assert ran(owner.client, child) == "completed"
        shown = owner.client.get(f"/workspace/runs/{run_id}").json()
        assert shown["status"] == "completed"
        assert "questions" not in shown

    def test_the_analysis_shows_the_continued_job_and_asks_nothing(self, owner):
        _, frames = self.continued(owner, owner.keep("Encrypted", ORDERS_DB))
        child = frames["continued"]["run"]
        assert ran(owner.client, child) == "completed"
        # The list shows the job that continues the pause, not the pause.
        [shown] = owner.client.get("/workspace/runs").json()
        assert (shown["run"], shown["status"], shown["pauses"]) == (
            child,
            "completed",
            False,
        )
        # Its report has no questions, and takes no answers.
        page = owner.client.get(f"/report/{child}").text
        assert re.search(r'id="asks"[^>]*>false<', page)
        assert re.search(r'id="fact_questions"[^>]*>\[\]<', page)
        assert re.search(r'id="link_questions"[^>]*>\[\]<', page)
        # No answer passes, not even a new value for a fact a Control wrote.
        for sent in (dict(FACT), {"key": DB_KEY, "value": "KMS"}):
            refused = owner.answer(child, revision=0, facts=[sent])
            assert refused.status_code == 400
            assert "asks no questions" in refused.json()["message"]
        saved = owner.answer(child, revision=0, facts=[dict(FACT)], save=True)
        assert saved.status_code == 400

    def test_a_paused_analysis_that_asks_nobody_takes_no_answer_and_no_amendment(
        self, owner
    ):
        aes = owner.keep("Encrypted", ORDERS_DB)
        kms = owner.keep("KMS", ORDERS_DB, facts=[fact(value="KMS")])
        run_id, frames = self.continued(owner, aes, kms)
        assert "refusal" in frames["questions"]
        asked = owner.questions(run_id)
        assert asked["asks"] is False
        assert asked["early_questions"] == []
        # The pause takes an untick, but no answer of its own.
        own = owner.answer(run_id, revision=0, facts=[dict(FACT)])
        assert own.status_code == 400
        assert "asks no questions" in own.json()["message"]
        amended = owner.client.post(
            f"/amend/{run_id}",
            json={"text": "The API runs behind a gateway.", "revision": 0},
            headers=SAME_ORIGIN,
        )
        assert amended.status_code == 400
        assert "takes no amendment" in amended.json()["message"]

    def test_each_control_takes_its_default_resolution(self, owner):
        db = owner.keep("Encrypted", ORDERS_DB)
        billing = owner.keep("Billing", {"type": "process", "name": "Billing"})
        _, frames = self.continued(owner, db, billing)
        child = frames["continued"]["run"]
        assert ran(owner.client, child) == "completed"
        held = asyncio.run(owner.store.get(child))
        copies = asyncio.run(owner.store.controls(child, owner.owner_id()))
        resolved = {
            entry.control: entry
            for entry in resolutions(copies, held.control_scopes, zoned_model())
        }
        assert resolved[db].state == "applied"
        assert (resolved[billing].state, resolved[billing].reason) == (
            "left-out",
            "missing-element",
        )

    def test_a_conflict_leaves_the_job_paused_until_an_untick(self, owner):
        aes = owner.keep("Encrypted", ORDERS_DB)
        kms = owner.keep("KMS", ORDERS_DB, facts=[fact(value="KMS")])
        run_id, frames = self.continued(owner, aes, kms)
        assert list(frames)[-1] == "questions"
        assert set(frames) <= {"continuing", "questions"}
        message = (
            "The Controls “Encrypted” and “KMS” write “encryption_at_rest” on"
            " Orders DB with two values: “AES-256” and “KMS”. Untick one of them,"
            " then continue."
        )
        assert frames["questions"]["refusal"] == message
        assert owner.questions(run_id)["refusal"] == message
        assert asyncio.run(owner.store.get(run_id)).status == "awaiting-answers"
        untick = [{"control": aes, "element": "store:orders-db"}]
        child = owner.resumed(run_id, control_scopes=untick)
        assert states(asyncio.run(owner.store.get(child)))[0][0] == "Encrypted"
        assert owner.runner.heads == 1

    def test_a_manual_continue_takes_the_stored_refusal_away(self, owner):
        aes = owner.keep("Encrypted", ORDERS_DB)
        kms = owner.keep("KMS", ORDERS_DB, facts=[fact(value="KMS")])
        run_id, frames = self.continued(owner, aes, kms)
        assert "refusal" in frames["questions"]
        owner.runner.fails = True
        untick = [{"control": aes, "element": "store:orders-db"}]
        started = owner.answer(run_id, revision=0, control_scopes=untick)
        assert started.status_code == 200, started.text
        assert ran(owner.client, started.json()["run"]) == "failed"
        assert asyncio.run(owner.store.get(run_id)).continue_refusal is None
        assert owner.questions(run_id)["refusal"] == _NOT_CONTINUED

    def test_a_record_read_before_the_refusal_shows_the_stored_refusal(self, owner):
        """The task can store its refusal and end between a route's read and
        its check, so the reason comes from the store."""
        aes = owner.keep("Encrypted", ORDERS_DB)
        kms = owner.keep("KMS", ORDERS_DB, facts=[fact(value="KMS")])
        run_id, frames = self.continued(owner, aes, kms)
        stale = asyncio.run(owner.store.get(run_id)).model_copy(
            update={"continue_refusal": None}
        )
        state = asyncio.run(job_state(owner.store, stale, held=False))
        assert state.refusal == StoredRefusal(frames["questions"]["refusal"])

    def test_a_refusal_outlives_a_restart(self, owner):
        aes = owner.keep("Encrypted", ORDERS_DB)
        kms = owner.keep("KMS", ORDERS_DB, facts=[fact(value="KMS")])
        run_id, frames = self.continued(owner, aes, kms)
        message = frames["questions"]["refusal"]
        owner.restart()
        with owner.client:
            assert owner.questions(run_id)["refusal"] == message
            stream = owner.client.get(f"/events/{run_id}").text
            assert list(end_frames(stream)) == ["questions"]
            assert end_frames(stream)["questions"]["refusal"] == message
            untick = [{"control": aes, "element": "store:orders-db"}]
            child = owner.resumed(run_id, control_scopes=untick)
            assert asyncio.run(owner.store.get(run_id)).continue_refusal is None
            assert ran(owner.client, child) == "completed"

    def test_a_job_the_app_did_not_continue_before_a_restart_waits(
        self, owner, monkeypatch
    ):
        async def ended_first(*_):
            """The process ends after the pause and before the continue."""

        with monkeypatch.context() as patched:
            patched.setattr(Workspace, "_continue", ended_first)
            run_id, _ = self.continued(owner, owner.keep("Encrypted", ORDERS_DB))
        owner.restart()
        with owner.client:
            stream = owner.client.get(f"/events/{run_id}").text
            assert list(end_frames(stream)) == ["questions"]
            assert end_frames(stream)["questions"]["refusal"] == _NOT_CONTINUED
            assert owner.questions(run_id)["refusal"] == _NOT_CONTINUED
            held = asyncio.run(owner.store.get(run_id))
            assert held.status == "awaiting-answers"
            assert asyncio.run(owner.store.resumed_by(run_id, owner.owner_id())) is None
            child = owner.resumed(run_id)
            assert owner.answers_source(child) == [DB_LINE]
        assert owner.runner.heads == 1

    def test_an_admission_refusal_leaves_the_job_paused(self, owner, monkeypatch):
        reserve = owner.store.reserve
        reserved = []

        async def second_at_ceiling(record, **kwargs):
            """Admit the submission, and refuse the continue once."""
            reserved.append(record.id)
            if len(reserved) == 2:
                return Admission(outcome="at_ceiling", active=1)
            return await reserve(record, **kwargs)

        monkeypatch.setattr(owner.store, "reserve", second_at_ceiling)
        run_id, frames = self.continued(owner, owner.keep("Encrypted", ORDERS_DB))
        assert frames["questions"]["refusal"] == (
            "An analysis is already running. Wait for it to finish."
        )
        child = owner.resumed(run_id)
        assert owner.answers_source(child) == [DB_LINE]
        assert owner.runner.heads == 1

    def test_a_job_whose_run_fails_does_not_continue(self, owner):
        owner.runner.fails = True
        run_id, frames = self.continued(owner, owner.keep("Encrypted", ORDERS_DB))
        assert list(frames) == ["failed"]
        assert asyncio.run(owner.store.resumed_by(run_id, owner.owner_id())) is None

    def test_a_submission_with_no_control_and_no_question_does_not_pause(self, owner):
        started = owner.submit(questions=False)
        assert started.status_code == 200, started.text
        assert ran(owner.client, started.json()["run"]) == "completed"
        assert owner.runner.heads == 0

    def test_the_page_follows_the_continue_to_the_resumed_job(self):
        steps = """
await ids.analyze.listeners.submit({ preventDefault() {} }); await settle();
streams[0].listeners.continuing({ data: "{}" });
calls.push({ status: ids["status-text"].textContent });
streams[0].listeners.continued({ data: JSON.stringify({ run: "r2" }) });
"""
        seen = _run_form_script(steps)
        assert seen["calls"][-1] == {"status": "Continuing with your Controls…"}
        assert seen["streams"] == ["/events/r1", "/events/r2"]

    def test_the_page_shows_the_refusal_of_the_continue(self):
        steps = """
await ids.analyze.listeners.submit({ preventDefault() {} }); await settle();
streams[0].listeners.questions({ data: JSON.stringify({ run: "r1",
  link_questions: [], early_questions: [], revision: 0, refusal: "Untick one." }) });
await settle();
calls.push({ problem: ids["answer-problem"].textContent });
"""
        seen = _run_form_script(steps)
        assert seen["calls"][-1] == {"problem": "Untick one."}
        assert seen["asked"]
        assert [call["url"] for call in seen["calls"][:-1]] == ["/analyze"]
