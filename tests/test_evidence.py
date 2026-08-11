"""The closed set of citable facts, and the only way an agent can name one.

Two properties carry the whole design and both are pinned here: the catalog is
a pure function of the validated System Model, and resolution is total over the
catalog and refuses everything else. Together they are what makes a mis-shaped
:class:`~stride_service.report.Ground` unreachable from an agent rather than
merely rare — the last class in this module is the traceback that motivated the
cutover, asserted to be inexpressible.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from stride_service.evidence import (
    EvidenceResolutionError,
    crossing_evidence_ref,
    evidence_catalog,
    render_catalog,
    resolve_proposals,
    unknown_evidence_ref,
)
from stride_service.report import Ground, ThreatProposal, ThreatProposals
from stride_service.system_model import UNKNOWN, DataStore, SystemModel
from tests.factories import sample_draft, sample_proposal, valid_model

ENCRYPTION_REF = "unknown:store:orders-db:encryption_at_rest"
LOGIN_CROSSING_REF = "crossing:flow:customer-to-web-app:login"


class TestEvidenceCatalog:
    def test_every_unknown_attribute_is_enumerated(self):
        catalog = evidence_catalog(valid_model())

        assert catalog[ENCRYPTION_REF] == Ground(
            kind="unknown-attribute",
            element_id="store:orders-db",
            attribute="encryption_at_rest",
        )

    def test_every_boundary_crossing_is_enumerated(self):
        catalog = evidence_catalog(valid_model())

        assert catalog[LOGIN_CROSSING_REF] == Ground(
            kind="derived-fact", flow_id="flow:customer-to-web-app:login"
        )

    def test_an_attribute_the_model_states_is_not_evidence_of_an_unknown(self):
        """The catalog says a fact is unstated, never that a control is weak.

        ``store:orders-db`` names its technology, so there is no unknown to
        cite about it — and an entry that existed anyway would be an invitation
        to ground a finding on a fact the model does contain.
        """
        catalog = evidence_catalog(valid_model())

        assert unknown_evidence_ref("store:orders-db", "technology") not in catalog

    def test_only_the_two_derived_kinds_are_ever_catalogued(self):
        """No quote, and no room for a conclusion: every entry is one of two
        shapes, both computed by rule from the model."""
        kinds = {ground.kind for ground in evidence_catalog(valid_model()).values()}

        assert kinds == {"unknown-attribute", "derived-fact"}

    def test_identity_and_provenance_fields_are_not_attributes(self):
        """``notes`` holding the word is a sentence, not an unstated control."""
        model = valid_model()
        model.data_stores[0].notes = UNKNOWN

        catalog = evidence_catalog(model)

        assert unknown_evidence_ref("store:orders-db", "notes") not in catalog

    def test_the_same_model_yields_the_same_catalog_in_the_same_order(self):
        """Stable IDs *and* stable order, which is what lets a ref be compared
        across runs, samples and reports."""
        model = valid_model()

        assert list(evidence_catalog(model)) == list(evidence_catalog(model))

    def test_no_two_facts_can_collide_on_one_reference(self):
        """Element IDs are unique in a validated model and an attribute appears
        once on an element, so the mapping is injective — a dict silently
        dropping a second fact is the failure this rules out."""
        model = valid_model()
        refs = [
            unknown_evidence_ref(element.id, attribute)
            for element in model.elements()
            for attribute in type(element).model_fields
            if getattr(element, attribute, None) == UNKNOWN
        ] + [
            crossing_evidence_ref(crossing.flow_id)
            for crossing in model.boundary_crossings()
        ]

        assert len(refs) == len(set(refs))
        assert len(evidence_catalog(model)) == len(set(refs))

    def test_an_invalid_model_produces_no_catalog_at_all(self):
        """Fails closed exactly as ``boundary_crossings`` does: a catalog built
        over a dangling endpoint would offer evidence about a system nobody
        described."""
        model = valid_model()
        model.data_flows[0].source = "process:not-here"

        with pytest.raises(ValueError, match="not a zoned element"):
            evidence_catalog(model)

    def test_a_model_with_nothing_unknown_and_no_crossing_is_empty_not_absent(self):
        empty = SystemModel(
            data_stores=[
                DataStore(
                    id="store:ledger",
                    name="Ledger",
                    technology="PostgreSQL",
                    trust_zone="boundary:core",
                    data_classification="confidential",
                    encryption_at_rest="CMEK",
                )
            ]
        )

        assert evidence_catalog(empty) == {}


class TestRenderCatalog:
    """The shape agents select from — a fix for #138, not a presentation choice.

    Agents composed well-formed references to facts the catalog did not hold,
    which fails the whole job. A JSON array of IDs reads as a specimen of the
    format; these pin the properties that make the rendering a menu instead.
    """

    def test_every_entry_appears_as_its_own_row(self):
        catalog = evidence_catalog(valid_model())
        rendered = render_catalog(catalog)

        for ref in catalog:
            assert f"| `{ref}` |" in rendered

    def test_it_is_not_a_list_an_agent_could_pattern_complete(self):
        rendered = render_catalog(evidence_catalog(valid_model()))

        assert not rendered.lstrip().startswith("[")
        assert "| cite this exactly |" in rendered

    def test_it_states_how_many_facts_there_are(self):
        """The count is what makes the set readable as closed rather than as a sample."""
        catalog = evidence_catalog(valid_model())

        assert f"{len(catalog)} facts" in render_catalog(catalog)

    def test_the_two_kinds_of_fact_read_differently(self):
        """An unstated attribute and a derived crossing are not interchangeable.

        An agent that conflates them cites the wrong one, so the gloss carries
        the distinction the ID prefix alone makes easy to skim past.
        """
        rendered = render_catalog(
            {
                "unknown:store:accounts-db:encryption_at_rest": Ground(
                    kind="unknown-attribute",
                    element_id="store:accounts-db",
                    attribute="encryption_at_rest",
                ),
                "crossing:flow:a-to-b:call": Ground(
                    kind="derived-fact", flow_id="flow:a-to-b:call"
                ),
            }
        )

        assert "`encryption_at_rest` never stated" in rendered
        assert "crosses a trust boundary" in rendered

    def test_an_empty_catalog_renders_no_rows(self):
        """A model with every control stated and no crossing is legal, if rare."""
        rendered = render_catalog({})

        assert "0 facts" in rendered
        assert "| `" not in rendered

    def test_rendering_is_stable_across_calls(self):
        """Two runs over one model must send byte-identical instructions."""
        catalog = evidence_catalog(valid_model())

        assert render_catalog(catalog) == render_catalog(catalog)

    def test_row_order_is_the_catalogs_own(self):
        """Which is the model's, so a diff between runs means the model moved."""
        catalog = evidence_catalog(valid_model())
        rendered = render_catalog(catalog)
        positions = [rendered.index(f"| `{ref}` |") for ref in catalog]

        assert positions == sorted(positions)


class TestABadReferenceCostsItsEntryNotTheJob:
    """The fail-closed policy #138 narrowed.

    Agents compose well-formed references to facts the catalog does not hold,
    and failing the whole analysis over one discarded six lanes of work to
    punish a citation error — 2 of 12 jobs on a live sweep. The rule is now the
    one unverified quotes already had: marked per entry, failed closed per
    threat.
    """

    def test_a_threat_survives_on_the_references_that_did_resolve(self):
        catalog = evidence_catalog(valid_model())
        proposal = sample_proposal(
            "S-01",
            evidence_refs=["crossing:flow:composed-by-the-agent", ENCRYPTION_REF],
            quotes=[],
        )

        resolution = resolve_proposals([proposal], catalog, "spoofing")

        (draft,) = resolution.drafts
        assert draft.grounds == [catalog[ENCRYPTION_REF]]

    def test_the_dropped_reference_is_recorded_against_its_threat(self):
        """Dropped, not rendered: no ground was ever built from it.

        Which is what separates this from an unverified quote — that is a real
        ground whose text could not be found, so it still renders with a mark
        beside it. Here there is nothing to render, and the mark is the only
        trace a reader gets.
        """
        catalog = evidence_catalog(valid_model())
        proposal = sample_proposal(
            "S-01", evidence_refs=["crossing:flow:ghost", ENCRYPTION_REF], quotes=[]
        )

        (mark,) = resolve_proposals([proposal], catalog, "spoofing").unresolved

        assert mark.threat_id == "S-01"
        assert mark.reference == "crossing:flow:ghost"

    def test_a_quote_is_enough_to_keep_a_threat_whose_references_all_fail(self):
        """Grounds are grounds: the submitter's own words justify a finding."""
        catalog = evidence_catalog(valid_model())
        proposal = sample_proposal(
            "S-01",
            evidence_refs=["unknown:store:ghost:x"],
            quotes=[{"text": "Customers log in", "source_label": "Description"}],
        )

        resolution = resolve_proposals([proposal], catalog, "spoofing")

        assert len(resolution.drafts) == 1
        assert len(resolution.unresolved) == 1

    def test_a_threat_left_with_no_grounds_at_all_still_fails_the_job(self):
        """Where the line is, and why it is there.

        ``grounds`` is ``min_length=1``: a finding resting on nothing is the one
        thing this schema refuses to represent, and no critic could rule on it.
        Dropping the threat instead would delete a finding silently, which is
        the worst outcome a security tool has available.
        """
        catalog = evidence_catalog(valid_model())
        proposal = sample_proposal(
            "S-01", evidence_refs=["crossing:flow:ghost"], quotes=[]
        )

        with pytest.raises(EvidenceResolutionError, match="nothing is left"):
            resolve_proposals([proposal], catalog, "spoofing")

    def test_one_groundless_threat_does_not_take_its_lane_down_with_it(self):
        """Only the threat that cannot stand fails, and it fails the job.

        The batch still reports together, so a run that dies says everything
        that was wrong rather than the first thing.
        """
        catalog = evidence_catalog(valid_model())
        proposals = [
            sample_proposal("S-01", evidence_refs=[ENCRYPTION_REF], quotes=[]),
            sample_proposal("S-02", evidence_refs=["crossing:flow:ghost"], quotes=[]),
        ]

        with pytest.raises(EvidenceResolutionError) as excinfo:
            resolve_proposals(proposals, catalog, "spoofing")

        assert "'S-02'" in str(excinfo.value)
        assert "'S-01'" not in str(excinfo.value)

    def test_a_clean_lane_records_no_marks(self):
        catalog = evidence_catalog(valid_model())

        resolution = resolve_proposals([sample_proposal()], catalog, "spoofing")

        assert resolution.unresolved == []


class TestResolveProposals:
    def test_a_reference_resolves_to_the_catalog_entry_it_names(self):
        catalog = evidence_catalog(valid_model())
        proposal = sample_proposal("S-01", evidence_refs=[ENCRYPTION_REF], quotes=[])

        (draft,) = resolve_proposals([proposal], catalog, "spoofing").drafts

        assert draft.grounds == [catalog[ENCRYPTION_REF]]

    def test_a_quote_candidate_becomes_a_quote_ground(self):
        catalog = evidence_catalog(valid_model())
        proposal = sample_proposal(
            "S-01",
            evidence_refs=[],
            quotes=[{"text": "Customers log in", "source_label": "Description"}],
        )

        (draft,) = resolve_proposals([proposal], catalog, "spoofing").drafts

        assert draft.grounds == [
            Ground(kind="quote", text="Customers log in", source_label="Description")
        ]

    def test_quotes_lead_the_resolved_list_and_evidence_follows(self):
        """The order marks are indexed against, so it is fixed rather than
        incidental — a reader following an ``UnverifiedGround`` back to its
        quote depends on it."""
        catalog = evidence_catalog(valid_model())
        proposal = sample_proposal(
            "S-01",
            evidence_refs=[LOGIN_CROSSING_REF, ENCRYPTION_REF],
            quotes=[{"text": "Customers log in", "source_label": "Description"}],
        )

        (draft,) = resolve_proposals([proposal], catalog, "spoofing").drafts

        assert [ground.kind for ground in draft.grounds] == [
            "quote",
            "derived-fact",
            "unknown-attribute",
        ]

    def test_resolution_changes_nothing_but_the_evidence(self):
        """The agent's other seven fields reach the report as written."""
        catalog = evidence_catalog(valid_model())

        (draft,) = resolve_proposals([sample_proposal()], catalog, "spoofing").drafts

        assert draft == sample_draft()

    def test_resolving_twice_gives_the_same_drafts(self):
        catalog = evidence_catalog(valid_model())
        proposals = [sample_proposal("S-01"), sample_proposal("S-02")]

        assert resolve_proposals(proposals, catalog, "spoofing") == resolve_proposals(
            proposals, catalog, "spoofing"
        )

    def test_a_reference_naming_nothing_fails_deterministically(self):
        """Reported as itself. There is no near match and no repair: inferring
        which fact was *meant* is the guess this design removes."""
        catalog = evidence_catalog(valid_model())
        proposal = sample_proposal(
            "S-01", evidence_refs=["crossing:flow:not-real"], quotes=[]
        )

        with pytest.raises(EvidenceResolutionError, match="crossing:flow:not-real"):
            resolve_proposals([proposal], catalog, "spoofing")

    def test_every_bad_reference_in_the_batch_is_reported_at_once(self):
        """The fan-in has no re-ask path, so it gets one chance to say what was
        wrong — and an agent that misread the catalog usually did so twice."""
        catalog = evidence_catalog(valid_model())
        proposals = [
            sample_proposal("S-01", evidence_refs=["crossing:flow:ghost"], quotes=[]),
            sample_proposal("S-02", evidence_refs=["unknown:store:ghost:x"], quotes=[]),
        ]

        with pytest.raises(EvidenceResolutionError) as excinfo:
            resolve_proposals(proposals, catalog, "spoofing")

        assert "crossing:flow:ghost" in str(excinfo.value)
        assert "unknown:store:ghost:x" in str(excinfo.value)

    def test_surrounding_whitespace_on_a_reference_is_not_a_defect(self):
        """Which spelling of a name arrived is mechanical; settled here rather
        than argued with in a prompt."""
        catalog = evidence_catalog(valid_model())
        proposal = sample_proposal(
            "S-01", evidence_refs=[f"  {ENCRYPTION_REF} "], quotes=[]
        )

        (draft,) = resolve_proposals([proposal], catalog, "spoofing").drafts

        assert draft.grounds == [catalog[ENCRYPTION_REF]]


class TestTheMisShapeIsUnreachable:
    """The traceback that motivated the cutover, asserted inexpressible.

    A ``derived-fact`` carrying an ``attribute`` and no ``flow_id`` killed a
    node, and with it all six lanes. The point is not that the agent is now
    told not to do that — it is that the field it did it in no longer exists on
    anything an agent emits.
    """

    def test_an_agent_cannot_emit_a_ground_at_all(self):
        with pytest.raises(ValidationError, match="extra_forbidden"):
            ThreatProposal.model_validate(
                sample_proposal().model_dump()
                | {"grounds": [{"kind": "derived-fact", "flow_id": "f"}]}
            )

    def test_the_recorded_failure_payload_has_no_home_in_the_node_schema(self):
        """The exact shape from the traceback: a branch declared one way and
        filled in another."""
        with pytest.raises(ValidationError, match="extra_forbidden"):
            ThreatProposals.model_validate(
                {
                    "threats": [
                        sample_proposal().model_dump()
                        | {
                            "grounds": [
                                {
                                    "kind": "derived-fact",
                                    "attribute": "authentication",
                                    "flow_id": "",
                                }
                            ]
                        }
                    ]
                }
            )

    def test_a_resolved_draft_carries_the_branch_the_catalog_holds(self):
        """An agent selects; code constructs. There is no field through which
        the two could disagree."""
        catalog = evidence_catalog(valid_model())
        proposal = sample_proposal(
            "S-01", evidence_refs=[LOGIN_CROSSING_REF], quotes=[]
        )

        (draft,) = resolve_proposals([proposal], catalog, "spoofing").drafts

        assert draft.grounds[0].kind == "derived-fact"
        assert draft.grounds[0].flow_id == "flow:customer-to-web-app:login"
        assert not draft.grounds[0].attribute

    def test_an_agent_cannot_name_a_lane_or_a_threat_id_at_all(self):
        """The category-letter mismatch, gone the way the mis-shape went.

        An agent used to restate its own category and compose an ID whose
        letter had to agree with it — two spellings of a constant the graph
        fills in at build time, and a disagreement between them failed the node
        and cancelled the five sibling lanes.
        """
        assert "id" not in ThreatProposal.model_fields
        assert "category" not in ThreatProposal.model_fields
        with pytest.raises(ValidationError, match="extra_forbidden"):
            ThreatProposal.model_validate(
                sample_proposal().model_dump() | {"id": "S-01", "category": "tampering"}
            )

    def test_the_lane_supplies_the_letter_and_the_category(self):
        catalog = evidence_catalog(valid_model())
        proposal = sample_proposal("S-07", evidence_refs=[ENCRYPTION_REF], quotes=[])

        (draft,) = resolve_proposals([proposal], catalog, "tampering").drafts

        assert draft.id == "T-07"
        assert draft.category == "tampering"

    def test_a_sequence_has_no_spelling_to_get_wrong(self):
        """Why the field is an integer. A two-digit string would have brought
        the node-boundary raise back as a pattern mismatch on ``"1"``."""
        assert (
            ThreatProposal.model_validate(
                sample_proposal().model_dump() | {"sequence": "7"}
            ).sequence
            == 7
        )

    def test_a_proposal_justifying_itself_with_nothing_is_refused(self):
        """``grounds``' ``min_length=1``, expressed over the pair of lists."""
        with pytest.raises(ValidationError, match="at least one evidence"):
            sample_proposal("S-01", evidence_refs=[], quotes=[])
