"""The closed set of citable facts, and the only way an agent can name one.

Two properties carry the whole design and both are pinned here: the catalog is
a pure function of the validated System Model, and resolution is total over the
catalog and refuses everything else. Together they are what makes a mis-shaped
:class:`~analysis_service.report.Ground` unreachable from an agent rather than
merely rare — the last class in this module is the traceback that motivated the
cutover, asserted to be inexpressible.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from analysis_service.evidence import (
    absent_evidence_ref,
    crossing_evidence_ref,
    evidence_catalog,
    render_catalog,
    render_element_roster,
    resolve_proposals,
    unknown_evidence_ref,
)
from analysis_service.frameworks.stride import STRIDE
from analysis_service.frameworks.stride.record import ThreatProposal, ThreatProposals
from analysis_service.report import Ground
from analysis_service.system_model import UNKNOWN, DataStore, SystemModel
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

    def test_only_the_derived_kinds_are_ever_catalogued(self):
        """No quote, and no room for a conclusion: every entry is one of three
        shapes, all computed by rule from the model."""
        model = valid_model()
        model.data_flows[0].authentication = "none"

        kinds = {ground.kind for ground in evidence_catalog(model).values()}

        assert kinds == {"unknown-attribute", "absent-attribute", "derived-fact"}

    def test_a_control_the_input_states_is_absent_is_enumerated(self):
        """The gap #171 was filed for.

        The catalog used to test exact equality with the ``unknown`` sentinel,
        so a control the submitter said is *not there* — which 11 of the 12
        candidate rules fire on, through ``is_unverified`` — was a fact no
        agent could cite. It has its own entry and its own kind, because
        "nobody said" and "somebody said no" carry different threats.
        """
        model = valid_model()
        model.data_flows[0].authentication = "none; accepted by network position"

        catalog = evidence_catalog(model)

        ref = absent_evidence_ref("flow:customer-to-web-app:login", "authentication")
        assert catalog[ref] == Ground(
            kind="absent-attribute",
            element_id="flow:customer-to-web-app:login",
            attribute="authentication",
        )
        assert (
            unknown_evidence_ref("flow:customer-to-web-app:login", "authentication")
            not in catalog
        )

    def test_a_hedged_unknown_is_still_an_unknown(self):
        """``CONTEXT.md`` defines Unknown to include a voiced hedge, and
        ``control_state`` reads the leading token — so the sentinel decorated
        with the speaker's own doubt is the same fact as the bare one, and the
        catalog no longer misses it for the decoration."""
        model = valid_model()
        model.data_flows[0].authentication = "unknown; possibly a shared group account"

        catalog = evidence_catalog(model)

        ref = unknown_evidence_ref("flow:customer-to-web-app:login", "authentication")
        assert catalog[ref].kind == "unknown-attribute"

    def test_a_stated_control_reading_no_is_not_an_absence(self):
        """Only the leading token is read, which is what keeps this from
        becoming a classifier with a security opinion: ``no MFA on the password
        login`` describes a mechanism that exists."""
        model = valid_model()
        model.data_flows[0].authentication = "no MFA on the password login"

        catalog = evidence_catalog(model)

        flow_id = "flow:customer-to-web-app:login"
        assert absent_evidence_ref(flow_id, "authentication") not in catalog
        assert unknown_evidence_ref(flow_id, "authentication") not in catalog

    def test_only_a_control_attribute_can_be_stated_absent(self):
        """``unknown`` is the extraction sentinel on every field; ``none`` means
        something determinate only where the attribute names a control. A
        ``protocol`` reading ``none`` is what the input wrote, not a fact an
        agent may rest a finding on."""
        model = valid_model()
        model.data_flows[0].protocol = "none"

        catalog = evidence_catalog(model)

        assert (
            absent_evidence_ref("flow:customer-to-web-app:login", "protocol")
            not in catalog
        )

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

    Agents composed well-formed references to facts the catalog did not hold.
    A JSON array of IDs reads as a specimen of the format; these pin the
    properties that make the rendering a menu instead. The shape is the half of
    the fix that stops the reference being composed — dropping it costs its
    entry rather than the job (:class:`UnresolvedEvidence`) once it has been.
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

    def test_the_kinds_of_fact_read_differently(self):
        """An unstated attribute, a stated absence and a derived crossing are
        not interchangeable.

        An agent that conflates them cites the wrong one, so the gloss carries
        the distinction the ID prefix alone makes easy to skim past. It carries
        the whole of it for the two attribute branches: they name identical
        fields, so the right column is the only place their difference shows.
        """
        rendered = render_catalog(
            {
                "unknown:store:accounts-db:encryption_at_rest": Ground(
                    kind="unknown-attribute",
                    element_id="store:accounts-db",
                    attribute="encryption_at_rest",
                ),
                "absent:flow:a-to-b:call:authentication": Ground(
                    kind="absent-attribute",
                    element_id="flow:a-to-b:call",
                    attribute="authentication",
                ),
                "crossing:flow:a-to-b:call": Ground(
                    kind="derived-fact", flow_id="flow:a-to-b:call"
                ),
            }
        )

        assert "`encryption_at_rest` never stated" in rendered
        assert "`authentication` stated absent" in rendered
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
    """The policy #138 narrowed, and the groundless drop that finished it.

    Agents compose well-formed references to facts the catalog does not hold,
    and failing the whole analysis over one discarded six lanes of work to
    punish a citation error — 2 of 12 jobs on a live sweep. The rule is now the
    one unverified quotes have: marked per entry, dropped per claim.
    """

    def test_a_threat_survives_on_the_references_that_did_resolve(self):
        catalog = evidence_catalog(valid_model())
        proposal = sample_proposal(
            "S-01",
            evidence_refs=["crossing:flow:composed-by-the-agent", ENCRYPTION_REF],
            quotes=[],
        )

        resolution = resolve_proposals([proposal], catalog, STRIDE, "spoofing")

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

        (mark,) = resolve_proposals(
            [proposal], catalog, STRIDE, "spoofing"
        ).marks.unresolved_evidence

        assert mark.claim_id == "S-01"
        assert mark.reference == "crossing:flow:ghost"

    def test_a_quote_is_enough_to_keep_a_threat_whose_references_all_fail(self):
        """Grounds are grounds: the submitter's own words justify a finding."""
        catalog = evidence_catalog(valid_model())
        proposal = sample_proposal(
            "S-01",
            evidence_refs=["unknown:store:ghost:x"],
            quotes=[{"text": "Customers log in", "source_label": "Description"}],
        )

        resolution = resolve_proposals([proposal], catalog, STRIDE, "spoofing")

        assert len(resolution.drafts) == 1
        assert len(resolution.marks.unresolved_evidence) == 1

    def test_a_threat_left_with_no_grounds_at_all_is_dropped_and_marked(self):
        """``grounds`` is ``min_length=1``: a finding resting on nothing is the
        one thing this schema refuses to represent, and no critic could rule
        on it. It costs its entry, and the mark names the title and every
        reference it cited, so the drop is visible rather than silent.
        """
        catalog = evidence_catalog(valid_model())
        proposal = sample_proposal(
            "S-01", evidence_refs=["crossing:flow:ghost"], quotes=[]
        )

        resolution = resolve_proposals([proposal], catalog, STRIDE, "spoofing")

        assert resolution.drafts == []
        (mark,) = resolution.marks.dropped_claims
        assert mark.claim_id == "S-01"
        assert mark.title == proposal.title
        assert "crossing:flow:ghost" in mark.reason
        # No per-reference mark: it would name a claim the block does not carry.
        assert resolution.marks.unresolved_evidence == []

    def test_one_groundless_threat_does_not_take_its_lane_down_with_it(self):
        catalog = evidence_catalog(valid_model())
        proposals = [
            sample_proposal("S-01", evidence_refs=[ENCRYPTION_REF], quotes=[]),
            sample_proposal("S-02", evidence_refs=["crossing:flow:ghost"], quotes=[]),
        ]

        resolution = resolve_proposals(proposals, catalog, STRIDE, "spoofing")

        assert [draft.id for draft in resolution.drafts] == ["S-01"]
        assert [m.claim_id for m in resolution.marks.dropped_claims] == ["S-02"]

    def test_a_clean_lane_records_no_marks(self):
        catalog = evidence_catalog(valid_model())

        resolution = resolve_proposals([sample_proposal()], catalog, STRIDE, "spoofing")

        assert resolution.marks.unresolved_evidence == []


class TestResolveProposals:
    def test_a_reference_resolves_to_the_catalog_entry_it_names(self):
        catalog = evidence_catalog(valid_model())
        proposal = sample_proposal("S-01", evidence_refs=[ENCRYPTION_REF], quotes=[])

        (draft,) = resolve_proposals([proposal], catalog, STRIDE, "spoofing").drafts

        assert draft.grounds == [catalog[ENCRYPTION_REF]]

    def test_a_quote_candidate_becomes_a_quote_ground(self):
        catalog = evidence_catalog(valid_model())
        proposal = sample_proposal(
            "S-01",
            evidence_refs=[],
            quotes=[{"text": "Customers log in", "source_label": "Description"}],
        )

        (draft,) = resolve_proposals([proposal], catalog, STRIDE, "spoofing").drafts

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

        (draft,) = resolve_proposals([proposal], catalog, STRIDE, "spoofing").drafts

        assert [ground.kind for ground in draft.grounds] == [
            "quote",
            "derived-fact",
            "unknown-attribute",
        ]

    def test_resolution_changes_nothing_but_the_evidence(self):
        """The agent's other seven fields reach the report as written."""
        catalog = evidence_catalog(valid_model())

        (draft,) = resolve_proposals(
            [sample_proposal()], catalog, STRIDE, "spoofing"
        ).drafts

        assert draft == sample_draft()

    def test_resolving_twice_gives_the_same_drafts(self):
        catalog = evidence_catalog(valid_model())
        proposals = [sample_proposal("S-01"), sample_proposal("S-02")]

        assert resolve_proposals(
            proposals, catalog, STRIDE, "spoofing"
        ) == resolve_proposals(proposals, catalog, STRIDE, "spoofing")

    def test_a_reference_naming_nothing_is_reported_as_itself(self):
        """There is no near match and no repair: inferring which fact was
        *meant* is the guess this design removes."""
        catalog = evidence_catalog(valid_model())
        proposal = sample_proposal(
            "S-01", evidence_refs=["crossing:flow:not-real"], quotes=[]
        )

        resolution = resolve_proposals([proposal], catalog, STRIDE, "spoofing")

        assert resolution.drafts == []
        assert "crossing:flow:not-real" in resolution.marks.dropped_claims[0].reason

    def test_every_bad_reference_in_the_batch_is_marked(self):
        catalog = evidence_catalog(valid_model())
        proposals = [
            sample_proposal("S-01", evidence_refs=["crossing:flow:ghost"], quotes=[]),
            sample_proposal("S-02", evidence_refs=["unknown:store:ghost:x"], quotes=[]),
        ]

        resolution = resolve_proposals(proposals, catalog, STRIDE, "spoofing")

        assert [(m.claim_id, m.reason) for m in resolution.marks.dropped_claims] == [
            (
                "S-01",
                "cites only evidence this job's catalog does not contain ('crossing:flow:ghost')",
            ),
            (
                "S-02",
                "cites only evidence this job's catalog does not contain ('unknown:store:ghost:x')",
            ),
        ]

    def test_surrounding_whitespace_on_a_reference_is_not_a_defect(self):
        """Which spelling of a name arrived is mechanical; settled here rather
        than argued with in a prompt."""
        catalog = evidence_catalog(valid_model())
        proposal = sample_proposal(
            "S-01", evidence_refs=[f"  {ENCRYPTION_REF} "], quotes=[]
        )

        (draft,) = resolve_proposals([proposal], catalog, STRIDE, "spoofing").drafts

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

        (draft,) = resolve_proposals([proposal], catalog, STRIDE, "spoofing").drafts

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

        (draft,) = resolve_proposals([proposal], catalog, STRIDE, "tampering").drafts

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


class TestTheElementRoster:
    """Every ID a claim may name, as a table to select from (#306).

    `render_catalog` records why this shape exists: a reference set rendered as
    a specimen of the format invites an agent to *compose* a well-formed member
    instead of copying one, and a composed reference that resolves to nothing
    fails its whole job (#138, ADR 0012). `affected_element_ids` had only the
    constraint — "every one of them present in the System Model" — and the model
    as fenced JSON to read it out of.

    On a live end-to-end sweep a lane agent produced
    ``flow:a-to-b:label:label``, its own label concatenated twice: well-formed,
    plausible, absent from the set.
    """

    def test_every_element_appears_exactly_once(self):
        model = valid_model()
        roster = render_element_roster(model)

        for element in model.elements():
            assert roster.count(f"| `{element.id}` |") == 1

    def test_it_says_the_set_is_closed(self):
        """The sentence that makes it a roster rather than an excerpt."""
        model = valid_model()
        roster = render_element_roster(model)

        assert f"{len(list(model.elements()))} elements" in roster
        assert "this table is all of them" in roster

    def test_a_flow_reads_as_its_endpoints_not_a_zone(self):
        """A flow has no `trust_zone`, and endpoints are what distinguishes two."""
        model = valid_model()
        roster = render_element_roster(model)
        flow = model.data_flows[0]

        row = next(
            line for line in roster.splitlines() if line.startswith(f"| `{flow.id}` |")
        )
        assert f"`{flow.source}`" in row and f"`{flow.destination}`" in row

    def test_the_gloss_carries_no_attribute_values(self):
        """Those are in the System Model, and this is paid per lane per framework.

        Repeating them would buy nothing and cost the most expensive block in
        the job-varying half.
        """
        model = valid_model()
        roster = render_element_roster(model)

        for store in model.data_stores:
            assert store.data_classification not in roster

    def test_the_order_is_the_models_own(self):
        """One System Model renders one table, so a rerun is byte-identical."""
        model = valid_model()

        assert render_element_roster(model) == render_element_roster(model)
