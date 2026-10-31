"""What the extraction read, listed for review at the pause (#2029 package 8)."""

from __future__ import annotations

from analysis_service.detected_facts import detected_facts
from tests.factories import valid_model
from tests.test_api import auth
from tests.test_links import catalog_client
from tests.test_pause import waiting


def _part(element_id: str) -> dict:
    return next(p for p in detected_facts(valid_model()) if p["element"] == element_id)


def _facts(element_id: str) -> dict:
    return {f["attribute"]: f for f in _part(element_id)["facts"]}


def test_a_stated_fact_is_listed_and_an_open_one_is_not():
    store = _facts("store:orders-db")
    assert store["technology"]["value"] == "Cloud SQL Postgres"
    assert store["assets"]["value"] == "business-critical-data, pii"
    assert "encryption_at_rest" not in store, "an open fact is asked, not listed"
    assert _part("store:orders-db")["excerpt"] == "orders are stored in Postgres"


def test_an_inferred_value_carries_its_basis():
    process = _facts("process:web-app")
    assert (
        process["exposure"]["inferred"]
        == "customers reach it directly from the browser"
    )
    assert process["technology"]["inferred"] == ""


def test_a_flow_names_its_ends_and_a_zone_is_named():
    flow = valid_model().data_flows[0]
    facts = _facts(flow.id)
    names = {e.id: e.name for e in valid_model().elements()}
    assert facts["source"]["value"] == names[flow.source]
    assert facts["destination"]["value"] == names[flow.destination]
    zone = _facts("process:web-app")["trust_zone"]["value"]
    assert zone == names["boundary:internal-network"]


def test_only_a_waiting_job_sends_them():
    client, store = catalog_client()
    job = waiting(store)
    read = client.get(f"/v1/jobs/{job}/questions", headers=auth()).json()
    assert [p["element"] for p in read["detected_facts"]] == [
        e.id for e in valid_model().elements()
    ]
