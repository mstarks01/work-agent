"""Deterministic graph analysis over a validated System Model.

The property every one of these guards is the same: identical input, identical
output, and no security claim invented on the way. A helper that reordered its
result would change the prompt bytes two otherwise-identical jobs send.
"""

import pytest

from stride_service.analysis import (
    CONTROL_ATTRIBUTES,
    control_state,
    cross_boundary_flows,
    crossing_flow_ids,
    inbound_flows,
    internet_exposed_elements,
    is_unverified,
    outbound_flows,
    reachable_from,
    sensitive_assets,
    unknown_controls,
    zone_kinds,
)
from stride_service.system_model import (
    DataFlow,
    DataStore,
    ExternalEntity,
    Process,
    SystemModel,
    TrustBoundary,
)
from tests.factories import valid_model


def flow(source, destination, label, **overrides):
    fields = {
        "protocol": "HTTPS",
        "authentication": "service account",
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


@pytest.fixture
def chain():
    """entity -> edge -> worker -> store, across three zones."""
    return SystemModel(
        external_entities=[
            ExternalEntity(
                id="entity:user",
                name="User",
                kind="human",
                trust_zone="boundary:public",
            )
        ],
        processes=[
            Process(
                id="process:edge",
                name="Edge",
                technology="nginx",
                trust_zone="boundary:dmz",
                exposure="internet-facing",
            ),
            Process(
                id="process:worker",
                name="Worker",
                technology="python",
                trust_zone="boundary:core",
                exposure="internal",
            ),
        ],
        data_stores=[
            DataStore(
                id="store:vault",
                name="Vault",
                technology="Postgres",
                trust_zone="boundary:core",
                data_classification="confidential",
                encryption_at_rest="unknown",
                assets=["pii", "secrets"],
            )
        ],
        data_flows=[
            flow("entity:user", "process:edge", "browse"),
            flow("process:edge", "process:worker", "dispatch", authentication="none"),
            flow(
                "process:worker",
                "store:vault",
                "read",
                encryption_in_transit="unknown",
            ),
        ],
        trust_boundaries=[
            TrustBoundary(id="boundary:public", name="Public", kind="network"),
            TrustBoundary(id="boundary:dmz", name="Dmz", kind="network"),
            TrustBoundary(id="boundary:core", name="Core", kind="privilege"),
        ],
    )


class TestControlState:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("unknown", "unverified"),
            ("unknown; possibly a shared group account", "unverified"),
            ("UNKNOWN", "unverified"),
            ("none", "absent"),
            ("none; accepted by network position", "absent"),
            ("none — the runner does not verify signatures", "absent"),
            ("company SSO", "stated"),
            ("", "stated"),
        ],
    )
    def test_classifies_from_the_leading_token(self, value, expected):
        assert control_state(value) == expected

    def test_a_stated_mechanism_with_a_gap_is_still_stated(self):
        """ "no MFA" describes a control that exists; absence would delete it."""
        assert control_state("password login, no MFA") == "stated"

    def test_unverified_covers_both_non_stated_states(self):
        assert is_unverified("unknown")
        assert is_unverified("none")
        assert not is_unverified("mTLS")


class TestTraversal:
    def test_inbound_and_outbound_split_by_direction(self, chain):
        assert [f.id for f in inbound_flows(chain, "process:worker")] == [
            "flow:edge-to-worker:dispatch"
        ]
        assert [f.id for f in outbound_flows(chain, "process:worker")] == [
            "flow:worker-to-vault:read"
        ]

    def test_reachable_from_is_breadth_first_and_excludes_the_start(self, chain):
        assert reachable_from(chain, "entity:user") == [
            "process:edge",
            "process:worker",
            "store:vault",
        ]
        assert reachable_from(chain, "store:vault") == []

    def test_reachable_from_terminates_on_a_cycle(self):
        model = SystemModel(
            processes=[
                Process(
                    id=f"process:{name}",
                    name=name,
                    technology="x",
                    trust_zone="boundary:z",
                    exposure="internal",
                )
                for name in ("a", "b")
            ],
            data_flows=[
                flow("process:a", "process:b", "one"),
                flow("process:b", "process:a", "two"),
            ],
            trust_boundaries=[TrustBoundary(id="boundary:z", name="Z", kind="network")],
        )
        assert reachable_from(model, "process:a") == ["process:b"]


class TestStructuralFacts:
    def test_cross_boundary_flows_matches_the_models_own_derivation(self, chain):
        assert cross_boundary_flows(chain) == chain.boundary_crossings()

    def test_crossing_flow_ids_names_every_crossing(self, chain):
        assert crossing_flow_ids(chain) == frozenset(
            {"flow:user-to-edge:browse", "flow:edge-to-worker:dispatch"}
        )

    def test_internet_exposed_excludes_unknown_exposure(self):
        model = valid_model()
        model.processes[0].exposure = "unknown"
        assert internet_exposed_elements(model) == []

    def test_unknown_controls_names_element_and_attribute(self, chain):
        controls = unknown_controls(chain)
        assert ("store:vault", "encryption_at_rest") in {
            (control.element_id, control.attribute) for control in controls
        }
        assert all(control.attribute in CONTROL_ATTRIBUTES for control in controls)
        assert all(control.state != "stated" for control in controls)

    def test_unknown_controls_distinguishes_absent_from_unverified(self, chain):
        states = {
            (control.element_id, control.attribute): control.state
            for control in unknown_controls(chain)
        }
        assert states[("flow:edge-to-worker:dispatch", "authentication")] == "absent"
        assert states[("store:vault", "encryption_at_rest")] == "unverified"

    def test_zone_kinds_maps_boundary_to_kind(self, chain):
        assert zone_kinds(chain)["boundary:core"] == "privilege"

    def test_sensitive_assets_drops_the_non_confidentiality_tags(self):
        element = Process(
            id="process:p",
            name="P",
            technology="x",
            trust_zone="boundary:z",
            exposure="internal",
            assets=["pii", "availability-critical", "reputation"],
        )
        assert sensitive_assets(element) == ("pii",)


def test_every_helper_is_stable_across_calls(chain):
    """Same model in, same bytes out — the property the prompt depends on."""
    for _ in range(3):
        assert reachable_from(chain, "entity:user") == [
            "process:edge",
            "process:worker",
            "store:vault",
        ]
        assert [c.model_dump() for c in unknown_controls(chain)] == [
            c.model_dump() for c in unknown_controls(chain)
        ]
