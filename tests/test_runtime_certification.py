"""The service certifies each job it completes, and the envelope acts on it.

Covers the runtime half of certification: the runner attaches a verdict to the
job record, and ``GET /v1/jobs/{id}/report`` withholds a report whose
generation identity this deployment has not blessed. Nothing here reaches a
provider — the runner is a stand-in and the manifests are built in memory.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from analysis_service.api import create_app
from analysis_service.certification import (
    MANIFEST_VERSION,
    BlessedManifest,
    CertificationGate,
    CertifyResult,
)
from analysis_service.jobs import (
    InMemoryJobStore,
    JobRecord,
    NodeCallback,
    PipelineCompleted,
    PipelineOutcome,
    StubPipelineRunner,
)
from analysis_service.sources import SourceLimits
from tests.factories import DEFAULT_FRAMEWORKS, SEEDING_BUDGET
from tests.test_api import FakeVerifier, auth, submit

FP_A = "a" * 64


class CertifyingRunner(StubPipelineRunner):
    """A stub runner that stamps a fixed verdict onto every completed job."""

    def __init__(self, result: CertifyResult) -> None:
        self._result = result

    async def run(self, job: JobRecord, on_node: NodeCallback) -> PipelineOutcome:
        for node in self.nodes:
            await on_node(node)
        return PipelineCompleted(
            report=self._stub_report(job), certification=self._result
        )


# Certification is what these test; the input bounds only have to exist.
TEST_LIMITS = SourceLimits(max_total_bytes=100 * 1024, max_sources=10)


def gate(require_certified: bool = False) -> CertificationGate:
    return CertificationGate(
        manifest=BlessedManifest(
            version=MANIFEST_VERSION, tiers={"base": frozenset({FP_A})}
        ),
        tier_of=lambda _node: "base",
        require_certified=require_certified,
    )


def make_client(result: CertifyResult, gate_policy: CertificationGate) -> TestClient:
    app = create_app(
        store=InMemoryJobStore(),
        runner=CertifyingRunner(result),
        verifier=FakeVerifier(),
        limits=TEST_LIMITS,
        job_deadline_seconds=30,
        max_active_jobs=10,
        budget=SEEDING_BUDGET,
        frameworks=DEFAULT_FRAMEWORKS,
    )
    app.state.certification = gate_policy
    return TestClient(app)


def fetch_report(client: TestClient):
    job_id = submit(client)
    return client.get(f"/v1/jobs/{job_id}/report", headers=auth())


class TestTheVerdictReachesTheRecord:
    def test_a_completed_job_carries_its_certification(self):
        client = make_client(CertifyResult(certified=True), gate())
        job_id = submit(client)
        record = client.app.state.store._records[job_id]
        assert record.certification is not None
        assert record.certification.certified


class TestAnnotateByDefault:
    def test_an_uncertified_report_is_still_served(self):
        # The manifest ships empty, so a default-on gate would fail every run on
        # day one — which is how a gate teaches people to bypass it.
        response = fetch_report(
            make_client(CertifyResult(certified=False), gate(require_certified=False))
        )
        assert response.status_code == 200
        assert "analyses" in response.json()

    def test_a_certified_report_is_served(self):
        response = fetch_report(
            make_client(CertifyResult(certified=True), gate(require_certified=True))
        )
        assert response.status_code == 200


class TestRequireCertifiedWithholds:
    def test_an_uncertified_report_is_withheld_when_the_knob_is_on(self):
        result = CertifyResult(
            certified=False,
            uncertified=({"node": "critic", "fingerprint": FP_A},),
        )
        response = fetch_report(make_client(result, gate(require_certified=True)))

        assert response.status_code == 409
        assert response.headers["content-type"].startswith("application/problem+json")

    def test_the_problem_body_names_the_nodes_but_not_the_analysis(self):
        result = CertifyResult(
            certified=False,
            uncertified=({"node": "critic", "fingerprint": FP_A},),
        )
        body = fetch_report(make_client(result, gate(require_certified=True))).json()

        assert body["uncertified_nodes"] == [{"node": "critic", "fingerprint": FP_A}]
        # Enough for the operator who turned the knob on; nothing that leaks the
        # analysis past a gate that just decided not to serve it.
        assert "threats" not in body
        assert "system_model" not in body

    def test_the_job_still_reads_as_completed(self):
        # Withholding the report must not fail the job: a failed job carries no
        # report at all, and the fingerprints that prove the drift live in it.
        client = make_client(
            CertifyResult(certified=False), gate(require_certified=True)
        )
        job_id = submit(client)
        assert client.get(f"/v1/jobs/{job_id}", headers=auth()).json()["status"] == (
            "completed"
        )


class TestUnexercisedWithholdsUnconditionally:
    @pytest.mark.parametrize("require_certified", [False, True])
    def test_an_unexercised_tier_withholds_either_way(self, require_certified):
        # An assertion rather than a measurement: unreachable on any run that
        # produces a report, so its cost is zero and one shared knob would make
        # the free half inert for every default deployment.
        result = CertifyResult(certified=True, unexercised=("strong",))
        response = fetch_report(
            make_client(result, gate(require_certified=require_certified))
        )
        assert response.status_code == 409
        assert response.json()["unexercised_tiers"] == ["strong"]


class TestNothingReachesTheClientView:
    def test_the_status_view_never_mentions_certification(self):
        # Operator-only: a client learns about certification exactly when its
        # deployment opted into caring.
        client = make_client(CertifyResult(certified=False), gate())
        job_id = submit(client)
        body = client.get(f"/v1/jobs/{job_id}", headers=auth()).json()
        assert "certification" not in body
        assert "certified" not in body

    def test_a_deployment_with_no_gate_serves_the_report(self):
        # The offline stand-ins carry no manifest; absence of a gate must not
        # withhold, or every test double would 409.
        app = create_app(
            store=InMemoryJobStore(),
            runner=CertifyingRunner(CertifyResult(certified=False)),
            verifier=FakeVerifier(),
            limits=TEST_LIMITS,
            job_deadline_seconds=30,
            max_active_jobs=10,
            budget=SEEDING_BUDGET,
            frameworks=DEFAULT_FRAMEWORKS,
        )
        app.state.certification = None
        assert fetch_report(TestClient(app)).status_code == 200
