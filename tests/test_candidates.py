"""Deterministic candidate triggers.

Two families of guard. The first is that the rules fire on what they say they
fire on. The second is the boundary that makes this design safe at all: a
candidate is a lead, and nothing downstream can turn one into a finding.
"""

import pytest

from analysis_service.candidates import Candidate, generate_candidates
from analysis_service.frameworks.stride import STRIDE
from analysis_service.frameworks.stride.record import STRIDE_CATEGORIES
from analysis_service.report import Ground
from analysis_service.system_model import (
    DataFlow,
    DataStore,
    ExternalEntity,
    Process,
    SystemModel,
    TrustBoundary,
)

#: STRIDE's own rule table. The neutral engine takes the lanes and the rules as
#: arguments now, so a test about *these* rules has to name the package they
#: belong to rather than reaching for a module-level constant.
RULES = STRIDE.rules


def candidates(model) -> dict:
    """Every STRIDE lane's candidate set for one model.

    The one place this suite binds the neutral engine to STRIDE's own lanes and
    rules, so a test below reads the way it did before the engine stopped
    knowing which framework it was firing for.
    """
    return generate_candidates(model, STRIDE.lanes, RULES)


def flow(source, destination, label, **overrides):
    fields = {
        "protocol": "HTTPS",
        "authentication": "workload identity",
        "data_description": "records",
        "encryption_in_transit": "TLS 1.3",
    }
    fields.update(overrides)
    slug = f"{source.split(':', 1)[-1]}-to-{destination.split(':', 1)[-1]}"
    return DataFlow(
        id=f"flow:{slug}:{label}",
        name=label,
        source=source,
        destination=destination,
        **fields,
    )


def zoned_pair(source_kind, destination_kind):
    """Two processes in two zones, one flow from the first to the second."""
    return SystemModel(
        processes=[
            Process(
                id=f"process:{name}",
                name=name,
                technology="x",
                trust_zone=f"boundary:{name}",
                exposure="internal",
                interface_kind="non-web",
            )
            for name in ("theirs", "ours")
        ],
        data_flows=[flow("process:theirs", "process:ours", "call")],
        trust_boundaries=[
            TrustBoundary(id="boundary:theirs", name="theirs", kind=source_kind),
            TrustBoundary(id="boundary:ours", name="ours", kind=destination_kind),
        ],
    )


@pytest.fixture
def model():
    """A model shaped to fire at least one rule in every lane."""
    return SystemModel(
        external_entities=[
            ExternalEntity(
                id="entity:customer",
                name="Customer",
                kind="human",
                trust_zone="boundary:public",
                assets=["pii"],
            ),
            ExternalEntity(
                id="entity:partner",
                name="Partner",
                kind="external-system",
                trust_zone="boundary:public",
            ),
        ],
        processes=[
            Process(
                id="process:api",
                name="Api",
                technology="FastAPI",
                trust_zone="boundary:dmz",
                exposure="internet-facing",
                interface_kind="web",
            ),
            Process(
                id="process:admin",
                name="Admin",
                technology="Django",
                trust_zone="boundary:privileged",
                exposure="internal",
                interface_kind="non-web",
            ),
        ],
        data_stores=[
            DataStore(
                id="store:ledger",
                name="Ledger",
                technology="Postgres",
                trust_zone="boundary:dmz",
                data_classification="confidential",
                encryption_at_rest="unknown",
                assets=["financial", "pii"],
            )
        ],
        data_flows=[
            flow("entity:customer", "process:api", "submit", authentication="unknown"),
            flow("entity:partner", "process:api", "callback", authentication="none"),
            flow(
                "process:api",
                "store:ledger",
                "write",
                authentication="shared app password",
                encryption_in_transit="unknown",
            ),
            flow(
                "process:admin",
                "store:ledger",
                "audit",
                authentication="shared app password",
            ),
            flow(
                "process:api",
                "process:admin",
                "escalate",
                authentication="unknown",
                encryption_in_transit="none",
            ),
        ],
        trust_boundaries=[
            TrustBoundary(id="boundary:public", name="Public", kind="network"),
            TrustBoundary(id="boundary:dmz", name="Dmz", kind="network"),
            TrustBoundary(
                id="boundary:privileged", name="Privileged", kind="privilege"
            ),
        ],
    )


def fired(model, rule_id):
    return [
        candidate
        for candidate_set in candidates(model).values()
        for candidate in candidate_set.candidates
        if candidate.rule_id == rule_id
    ]


class TestRuleTable:
    def test_every_category_has_at_least_one_rule(self):
        for category in STRIDE_CATEGORIES:
            assert STRIDE.rules_for(category), category

    def test_rule_ids_are_unique(self):
        ids = [rule.rule_id for rule in RULES]
        assert len(ids) == len(set(ids))

    def test_every_rule_id_opens_with_its_category(self):
        """The lane is readable off the ID alone, in the report and in a log."""
        for rule in RULES:
            assert rule.rule_id.startswith(f"{rule.lane}-")

    def test_a_rule_cannot_file_outside_its_own_lane(self, model):
        for category, candidate_set in candidates(model).items():
            assert all(c.lane == category for c in candidate_set.candidates)
            assert candidate_set.lane == category


class TestFiring:
    def test_unverified_boundary_auth_fires_on_the_crossing(self, model):
        hits = fired(model, "spoofing-unverified-boundary-auth")
        assert {hit.element_ids[0] for hit in hits} == {
            "flow:customer-to-api:submit",
            "flow:partner-to-api:callback",
            "flow:api-to-admin:escalate",
        }

    def test_facts_distinguish_unknown_from_stated_absent(self, model):
        by_flow = {
            hit.element_ids[0]: hit.facts
            for hit in fired(model, "spoofing-unverified-boundary-auth")
        }
        assert by_flow["flow:customer-to-api:submit"]["authentication_state"] == (
            "unverified"
        )
        assert by_flow["flow:partner-to-api:callback"]["authentication_state"] == (
            "absent"
        )

    def test_external_caller_rule_reads_the_entity_kind(self, model):
        hits = fired(model, "spoofing-unverified-external-caller")
        assert [hit.element_ids for hit in hits] == [
            ("flow:partner-to-api:callback", "entity:partner")
        ]

    def test_transit_crossing_rule_needs_both_halves(self, model):
        hits = fired(model, "tampering-unprotected-transit-crossing")
        # api->ledger has unknown encryption but does not cross; api->admin does.
        assert [hit.element_ids[0] for hit in hits] == ["flow:api-to-admin:escalate"]

    def test_write_to_store_rule_carries_the_classification(self, model):
        model.data_flows[2].authentication = "unknown"
        hits = fired(model, "tampering-unverified-write-to-store")
        assert hits[0].facts["data_classification"] == "confidential"

    def test_unattributable_action_needs_a_graded_asset(self, model):
        hits = fired(model, "repudiation-unattributable-action")
        assert [hit.element_ids for hit in hits] == []
        model.data_flows[2].authentication = "none"
        hits = fired(model, "repudiation-unattributable-action")
        assert hits[0].facts["destination_assets"] == "financial, pii"

    def test_sensitive_transit_reads_both_endpoints(self, model):
        hits = fired(model, "information-disclosure-unprotected-sensitive-transit")
        assert [hit.element_ids[0] for hit in hits] == ["flow:api-to-ledger:write"]

    def test_store_at_rest_rule_fires_on_the_unknown(self, model):
        hits = fired(model, "information-disclosure-store-at-rest-unverified")
        assert hits[0].element_ids == ("store:ledger",)
        assert hits[0].facts["encryption_state"] == "unverified"

    def test_internet_exposed_counts_its_reach(self, model):
        hits = fired(model, "denial-of-service-internet-exposed-process")
        assert hits[0].element_ids == ("process:api",)
        assert hits[0].facts["inbound_flows"] == 2
        assert hits[0].facts["reachable_elements"] == 2

    def test_shared_dependency_names_its_callers(self, model):
        hits = fired(model, "denial-of-service-shared-dependency")
        assert {hit.element_ids[0] for hit in hits} == {"process:api", "store:ledger"}

    def test_privilege_crossing_reads_the_boundary_kind(self, model):
        hits = fired(model, "elevation-of-privilege-privilege-zone-crossing")
        assert hits[0].element_ids == (
            "flow:api-to-admin:escalate",
            "boundary:privileged",
        )
        assert hits[0].facts["destination_zone_kind"] == "privilege"
        assert hits[0].facts["direction"] == "into"

    def test_leaving_a_privilege_zone_is_not_a_transition(self, model):
        """The authority is on the inside, so only entering it crosses one."""
        model.data_flows.append(
            flow("process:admin", "process:api", "report", authentication="unknown")
        )
        hits = fired(model, "elevation-of-privilege-privilege-zone-crossing")
        assert [hit.facts["direction"] for hit in hits] == ["into"]

    def test_leaving_a_tenant_zone_is_a_transition(self):
        """A party we do not control reaching a zone we do."""
        hits = fired(
            zoned_pair("tenant", "network"),
            "elevation-of-privilege-privilege-zone-crossing",
        )
        assert len(hits) == 1
        assert hits[0].element_ids == ("flow:theirs-to-ours:call", "boundary:theirs")
        assert hits[0].facts["direction"] == "out-of"
        assert hits[0].facts["zone_kind"] == "tenant"

    def test_a_crossing_between_two_tenant_zones_fires_once(self):
        """Both ends qualify; the destination decides, and the facts carry both."""
        hits = fired(
            zoned_pair("tenant", "tenant"),
            "elevation-of-privilege-privilege-zone-crossing",
        )
        assert len(hits) == 1
        assert hits[0].facts["direction"] == "into"
        assert hits[0].facts["source_zone_kind"] == "tenant"
        assert hits[0].facts["destination_zone_kind"] == "tenant"

    def test_exposed_process_authority_needs_a_crossing(self, model):
        hits = fired(model, "elevation-of-privilege-inbound-from-exposed-process")
        assert [hit.element_ids[0] for hit in hits] == ["flow:api-to-admin:escalate"]

    def test_a_model_with_nothing_to_say_fires_nothing(self):
        model = SystemModel(
            processes=[
                Process(
                    id="process:only",
                    name="Only",
                    technology="x",
                    trust_zone="boundary:z",
                    exposure="internal",
                    interface_kind="non-web",
                )
            ],
            trust_boundaries=[TrustBoundary(id="boundary:z", name="Z", kind="network")],
        )
        assert all(
            not candidate_set.candidates for candidate_set in candidates(model).values()
        )


class TestShape:
    def test_every_lane_gets_an_entry_even_when_empty(self, model):
        assert set(candidates(model)) == set(STRIDE_CATEGORIES)

    def test_questions_cover_exactly_the_rules_that_fired(self, model):
        for candidate_set in candidates(model).values():
            assert set(candidate_set.questions) == {
                candidate.rule_id for candidate in candidate_set.candidates
            }

    def test_generation_is_stable_across_calls(self, model):
        first = candidates(model)
        second = candidates(model)
        assert {k: v.model_dump() for k, v in first.items()} == {
            k: v.model_dump() for k, v in second.items()
        }

    def test_facts_are_clipped(self, model):
        model.data_flows[0].authentication = "unknown, " + "x" * 500
        hit = fired(model, "spoofing-unverified-boundary-auth")[0]
        assert len(hit.facts["authentication"]) <= 200


class TestCandidatesAreNotFindings:
    """The line the whole design rests on."""

    def test_a_candidate_is_not_a_ground(self):
        """No ``Ground`` branch can carry a rule; a rule cannot justify anything."""
        assert "rule_id" not in Ground.model_fields
        kinds = Ground.model_fields["kind"].annotation
        assert "candidate" not in str(kinds)

    def test_a_candidate_carries_no_verdict_severity_or_prose(self):
        """It states a condition. It says nothing about whether it is bad."""
        assert set(Candidate.model_fields) == {
            "rule_id",
            "lane",
            "element_ids",
            "facts",
        }

    def test_nothing_outside_the_prompt_seam_reads_a_candidate(self):
        """Candidates reach the agents and stop there.

        The importers of this module are the seam that renders the prompt
        (``graph``) and the accounting that counts what was offered
        (``coverage``). Neither the critic nor the report can see one, so no
        code path exists that turns a fired rule into a threat.
        """
        from pathlib import Path

        package = Path(__file__).resolve().parents[1] / "src" / "analysis_service"
        importers = {
            path.name
            for path in package.glob("*.py")
            if "from analysis_service.candidates import" in path.read_text()
        }
        assert importers == {"coverage.py", "graph.py"}
