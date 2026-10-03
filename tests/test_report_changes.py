"""What a follow-up report changed against the report its answers came from (#561).

A follow-up is matched to its earlier report by claim identity, never by prose
or by claim ID, so each case here changes the field the identity reads.
Deterministic, credential-free, and free of provider calls.
"""

from __future__ import annotations

from analysis_service.claims import UnknownRef, Verdict
from analysis_service.critic import finding_key
from analysis_service.report_changes import report_changes
from analysis_service.system_model import ModelIndex
from tests.factories import sample_report, sample_threat, valid_model
from tests.test_asvs import sample_asvs_claim

NEEDS_INFO = Verdict(
    status="needs-info",
    reason="The sources do not state this.",
    related_unknowns=[
        UnknownRef(
            element_id=valid_model().data_flows[1].id, attribute="encryption_in_transit"
        )
    ],
)


def _moves(before, after):
    return {
        (c.claim_id, c.change, c.before, c.after) for c in report_changes(before, after)
    }


def test_a_finding_the_follow_up_confirmed_reads_as_changed():
    before = sample_report([sample_threat(verdict=NEEDS_INFO)])
    after = sample_report([sample_threat()])

    assert _moves(before, after) == {("S-01", "changed", "needs-info", "confirmed")}


def test_a_finding_matches_by_identity_and_not_by_its_claim_id():
    """The follow-up numbers its findings afresh, and the title may change."""
    before = sample_report([sample_threat("S-04", title="Old words")])
    after = sample_report([sample_threat("S-01", title="New words")])

    assert _moves(before, after) == {("S-01", "unchanged", "confirmed", "confirmed")}


def test_a_new_finding_and_a_lost_one_are_both_named():
    """Another verb at the same place is another finding."""
    before = sample_report([sample_threat("S-01", verdict=NEEDS_INFO)])
    after = sample_report([sample_threat("S-02", verb="impersonate")])

    assert _moves(before, after) == {
        ("S-02", "new", None, "confirmed"),
        ("S-01", "gone", "needs-info", None),
    }


def test_a_draft_rejected_both_times_is_not_reported_as_lost():
    rejected = Verdict(status="rejected", reason="r", rejected_because="evidence")
    before = sample_report([], rejected_threats=[sample_threat(verdict=rejected)])
    after = sample_report([])

    assert report_changes(before, after) == ()


def test_a_catalog_claim_is_keyed_by_its_requirement():
    """An ASVS claim carries no verb: its identity is the requirement it rules on."""
    flows = ModelIndex.of(valid_model()).flow_endpoints
    first = sample_asvs_claim("v5.0.0-6.2.1")
    same = sample_asvs_claim("v5.0.0-6.2.1").model_copy(update={"title": "Reworded"})
    other = sample_asvs_claim("v5.0.0-6.2.2")

    assert finding_key(first, flows) == finding_key(same, flows)
    assert finding_key(first, flows) != finding_key(other, flows)


def _completed(store, report, resumption=None):
    """A completed job of alice's, holding ``report``."""
    import asyncio

    from analysis_service.jobs import JobRecord
    from analysis_service.sources import Source
    from tests.factories import admit, sample_selection

    record = JobRecord.create(
        owner_subject="alice",
        sources=[Source.description("A web app talks to a database.")],
        frameworks=sample_selection(),
    )
    if resumption is not None:
        record.resumption = resumption
    record.transition("running")
    record.report = report
    record.transition("completed")
    asyncio.run(admit(store, record))
    return record.id


def _follow_up_of(parent_id):
    from analysis_service.jobs import Checkpoint, Resumption

    return Resumption(
        parent_id=parent_id,
        checkpoint=Checkpoint(system_model=valid_model(), assertions=None),
        follow_up=True,
    )


class TestTheChangesRoute:
    """``GET /v1/jobs/{id}/changes`` serves the comparison to any client (#561)."""

    def test_a_follow_up_lists_how_each_finding_moved(self):
        from tests.test_api import auth, make_client

        client, store = make_client()
        parent = _completed(store, sample_report([sample_threat(verdict=NEEDS_INFO)]))
        child = _completed(
            store, sample_report([sample_threat()]), _follow_up_of(parent)
        )

        response = client.get(f"/v1/jobs/{child}/changes", headers=auth())

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["parent_id"] == parent
        assert [(f["change"], f["before"], f["after"]) for f in body["findings"]] == [
            ("changed", "needs-info", "confirmed")
        ]

    def test_a_job_that_is_not_a_follow_up_is_refused(self):
        from tests.test_api import auth, make_client

        client, store = make_client()
        job = _completed(store, sample_report([sample_threat()]))

        response = client.get(f"/v1/jobs/{job}/changes", headers=auth())

        assert response.status_code == 409
        assert "not a follow-up" in response.text

    def test_a_follow_up_whose_earlier_report_is_gone_is_refused(self):
        from tests.test_api import auth, make_client

        client, store = make_client()
        child = _completed(store, sample_report([]), _follow_up_of("gone"))

        response = client.get(f"/v1/jobs/{child}/changes", headers=auth())

        assert response.status_code == 409
        assert "no longer held" in response.text

    def test_another_subject_reads_nothing(self):
        from tests.test_api import auth, make_client

        client, store = make_client()
        parent = _completed(store, sample_report([]))
        child = _completed(store, sample_report([]), _follow_up_of(parent))

        response = client.get(f"/v1/jobs/{child}/changes", headers=auth("bob-token"))

        assert response.status_code == 404
