"""Deterministic graph analysis over a validated System Model.

The property every one of these guards is the same: identical input, identical
output, and no security claim invented on the way. A helper that reordered its
result would change the prompt bytes two otherwise-identical jobs send.
"""

import pytest

from analysis_service import analysis
from analysis_service.analysis import (
    CONTROL_ATTRIBUTES,
    control_state,
    cross_boundary_flows,
    crossing_facts,
    crossing_flow_ids,
    crossings_by_flow,
    inbound_flows,
    internet_exposed_elements,
    is_unverified,
    outbound_flows,
    reachable_from,
    sensitive_assets,
    states_a_protocol,
    unknown_controls,
    zone_kinds,
)
from analysis_service.system_model import (
    CORE_ASSET_TAGS,
    UNKNOWN,
    BoundaryCrossing,
    DataFlow,
    DataStore,
    ExternalEntity,
    Process,
    SystemModel,
    TrustBoundary,
    make_flow_id,
)
from tests.factories import valid_model


def flow(source, destination, label, **overrides):
    fields = {
        "protocol": "HTTPS",
        "authentication": "service account",
        "data_description": "records",
        "encryption_in_transit": "TLS 1.3",
        "operations": "unknown",
    }
    fields.update(overrides)
    return DataFlow(
        id=make_flow_id(source, destination, label),
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
                interface_kind="web",
            ),
            Process(
                id="process:worker",
                name="Worker",
                technology="python",
                trust_zone="boundary:core",
                exposure="internal",
                interface_kind="non-web",
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
            ("", "unverified"),
            ("   ", "unverified"),
        ],
    )
    def test_classifies_from_the_leading_token(self, value, expected):
        assert control_state(value) == expected

    def test_a_blank_control_is_unverified_and_never_stated(self):
        """The audit's reproduced defect: silence read as a control.

        A model whose ``authentication`` was an empty string passed the validity
        gate, and ``is_unverified`` then said the flow carried a verified
        control — which suppressed every candidate rule that asks about a
        missing one. The gate reports the blank now, and this is what holds if
        one arrives anyway.
        """
        assert control_state("") == "unverified"
        assert is_unverified("")
        assert not states_a_protocol("")

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
            "flow:process:edge>process:worker>dispatch"
        ]
        assert [f.id for f in outbound_flows(chain, "process:worker")] == [
            "flow:process:worker>store:vault>read"
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
                    interface_kind="non-web",
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
            {
                "flow:entity:user>process:edge>browse",
                "flow:process:edge>process:worker>dispatch",
            }
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
        assert (
            states[("flow:process:edge>process:worker>dispatch", "authentication")]
            == "absent"
        )
        assert states[("store:vault", "encryption_at_rest")] == "unverified"

    def test_zone_kinds_maps_boundary_to_kind(self, chain):
        assert zone_kinds(chain)["boundary:core"] == "privilege"

    def test_sensitive_assets_drops_a_tag_this_rule_did_not_admit(self):
        """A deployment tag is held, and its disclosure is still not a loss.

        ``extra_asset_tags`` extends the vocabulary, so an element can carry a
        tag ``SENSITIVE_ASSET_TAGS`` never listed. What that tag means to the
        deployment is the deployment's business; this rule may not read it as a
        confidentiality loss, so it drops out here (#877).
        """
        element = Process(
            id="process:p",
            name="P",
            technology="x",
            trust_zone="boundary:z",
            exposure="internal",
            interface_kind="non-web",
            assets=["pii", "cardholder-data"],
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


def test_a_term_fires_at_the_start_of_a_word_or_as_a_whole_word():
    """#429: ``sso`` fired inside ``processor`` and raised an OAuth lead.

    A leading boundary always. The trailing one is per term: a stem reaches
    its inflections, and a term marked ``$`` reaches the whole word only.
    """
    from analysis_service.analysis import matches_term

    assert not matches_term("sso", "our card processor is a third party")
    assert not matches_term("sso", "an associate signs in")
    assert matches_term("sso", "company sso")
    assert matches_term("authenticat", "an authenticated session")
    assert matches_term("http", "https post")
    assert matches_term("java", "a javascript front end")
    assert not matches_term("java$", "a javascript front end")
    assert matches_term("java$", "a java service")
    assert not matches_term("log$", "the login flow")


class TestTheAssetVocabularyNamesWhatAnElementHolds:
    """Every shipped asset tag is a class of data a source can state.

    An attacker acts on data. What a failure would cost — reputation, whether
    an outage matters — is a judgement about the business, so it is not a tag
    and the person reading the report owns it (#877). A deployment that wants
    to model one adds it through ``extra_asset_tags``.
    """

    def test_every_shipped_tag_is_one_whose_disclosure_is_a_loss(self):
        assert analysis.SENSITIVE_ASSET_TAGS == CORE_ASSET_TAGS, (
            "a tag added to CORE_ASSET_TAGS must name something an element"
            " holds, or it does not belong in the shipped vocabulary"
        )

    def test_a_configured_tag_is_not_assumed_to_be_disclosed(self):
        """The two sets are equal and not the same set.

        ``allowed_asset_tags`` lets a deployment extend the vocabulary, and a
        tag it adds names something that deployment models. Nothing here may
        rule that its disclosure is itself a loss.
        """
        assert "cardholder-data" not in analysis.SENSITIVE_ASSET_TAGS

    def test_comparable_tags_are_sorted_and_deduplicated(self):
        """Both readers compare the result, so neither may read an emitted order."""
        assert analysis.comparable_asset_tags(["pii", "credentials", "pii"]) == (
            "credentials",
            "pii",
        )


class TestACrossingSaysWhichZoneWasAssumed:
    """#1052: a rule keyed on a crossing owes its reader the inference behind it.

    A **Boundary Crossing** is derived from two zones and either may be a
    placement this service inferred rather than read. Over the thirteen corpus
    cases 29 of 43 crossings rest on at least one, so a lead that said only
    ``crosses_boundary`` told a lane agent the same thing whether both sources
    stated the zones or neither did.
    """

    def crossing(self, source_zone: str, destination_zone: str, assumed):
        return BoundaryCrossing(
            flow_id="flow:process:a>store:b>write",
            source_zone=source_zone,
            destination_zone=destination_zone,
            assumed_endpoints=list(assumed),
        )

    def flow(self):
        return DataFlow(
            id="flow:process:a>store:b>write",
            name="write",
            source="process:a",
            destination="store:b",
            protocol=UNKNOWN,
            authentication=UNKNOWN,
            data_description=UNKNOWN,
            encryption_in_transit=UNKNOWN,
        )

    def test_a_crossing_both_sources_state_is_flagged_neither_side(self) -> None:
        facts = crossing_facts(
            self.crossing("boundary:x", "boundary:y", ()), self.flow()
        )

        assert facts["source_zone_assumed"] is False
        assert facts["destination_zone_assumed"] is False

    def test_each_side_is_named_by_the_endpoint_it_belongs_to(self) -> None:
        """One assumed endpoint says which side, which a count could not."""
        held = self.crossing("boundary:x", "boundary:y", ("store:b",))

        facts = crossing_facts(held, self.flow())

        assert facts["source_zone_assumed"] is False
        assert facts["destination_zone_assumed"] is True

    def test_both_sides_are_named(self) -> None:
        held = self.crossing("boundary:x", "boundary:y", ("process:a", "store:b"))

        facts = crossing_facts(held, self.flow())

        assert facts["source_zone_assumed"] is True
        assert facts["destination_zone_assumed"] is True

    def test_the_two_readers_of_which_flows_cross_agree(self) -> None:
        """``crossing_flow_ids`` reads the mapping rather than deriving again."""
        model = valid_model()

        assert crossing_flow_ids(model) == frozenset(crossings_by_flow(model))


class TestACrossingHasThreeOutcomes:
    """ADR 0039 rule 2: both zones known and different, known and equal, or unknown.

    The third is the one this decision adds, and it is what lets a model
    holding an unplaced component be analysed at all: an endpoint the sources
    never placed answers ``undecidable`` rather than stopping the derivation.
    """

    def unplaced(self, model, element_id: str):
        for element in model.zoned_elements():
            if element.id == element_id:
                element.trust_zone = UNKNOWN
        return model

    def test_an_unplaced_endpoint_derives_an_undecidable_crossing(self) -> None:
        model = self.unplaced(valid_model(), "process:web-app")

        crossings = crossings_by_flow(model)
        undecided = [one for one in crossings.values() if not one.decided]

        assert undecided, "a flow touching an unplaced component raises a crossing"
        assert all(
            UNKNOWN in (one.source_zone, one.destination_zone) for one in undecided
        )

    def test_an_undecidable_crossing_says_so_to_every_rule(self) -> None:
        """The flag rides in the facts, so no rule re-derives it from the zones."""
        model = self.unplaced(valid_model(), "process:web-app")
        crossings = crossings_by_flow(model)
        flows = {flow.id: flow for flow in model.data_flows}

        decided = {
            crossing_facts(crossing, flows[flow_id])["crossing_decided"]
            for flow_id, crossing in crossings.items()
        }

        assert False in decided

    def test_two_equal_known_zones_are_still_not_a_crossing(self) -> None:
        """The outcome the third reading must not swallow."""
        model = valid_model()
        zone = model.processes[0].trust_zone
        for element in model.zoned_elements():
            element.trust_zone = zone

        assert crossings_by_flow(model) == {}

    def test_a_dangling_endpoint_still_fails_closed(self) -> None:
        """An invalid model is refused; an unplaced one is answered."""
        model = valid_model()
        model.data_flows[0].destination = "store:nowhere"

        with pytest.raises(ValueError, match="cannot derive crossings"):
            model.boundary_crossings()
