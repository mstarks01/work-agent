"""The closed set of citable facts, and the only way an agent can name one.

Two properties carry the whole design and both are pinned here: the catalog is
a pure function of the validated System Model, and resolution is total over the
catalog and refuses everything else. Together they are what makes a mis-shaped
:class:`~analysis_service.claims.Ground` unreachable from an agent rather than
merely rare — the last class in this module is the traceback that motivated the
cutover, asserted to be inexpressible.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from analysis_service.assertions import (
    ABSENT,
    UNPROJECTED,
    Assertion,
    AssertionCatalog,
    Qualifier,
    Subject,
    apply_projection,
    assertion_id,
    catalog_coverage,
)
from analysis_service.claims import (
    ASSERTION_GROUNDS,
    CONDITIONAL_GROUNDS,
    GROUND_TERM_MAX_CHARS,
    Ground,
)
from analysis_service.critic import critic_view
from analysis_service.evidence import (
    ASSERTION_GLOSSES,
    absent_evidence_ref,
    crossing_evidence_ref,
    evidence_catalog,
    ground_gloss,
    ground_issues,
    render_catalog,
    render_element_roster,
    resolve_proposals,
    unknown_evidence_ref,
)
from analysis_service.frameworks.stride import STRIDE
from analysis_service.frameworks.stride.record import (
    DraftThreat,
    ThreatProposal,
    ThreatProposals,
)
from analysis_service.system_model import UNKNOWN, DataStore, SystemModel
from tests.factories import (
    sample_draft,
    sample_proposal,
    sample_report,
    valid_model,
)

ENCRYPTION_REF = "unknown:store:orders-db:encryption_at_rest"
LOGIN_CROSSING_REF = "crossing:flow:entity:customer>process:web-app>login"
LOGIN_FLOW = "flow:entity:customer>process:web-app>login"
SHOPPERS = Subject(id="principal:shoppers", type="principal", label="shopper accounts")
COOKIE = Subject(
    id="credential:session-cookie", type="credential", label="session cookie"
)
LOGIN = Subject(id=LOGIN_FLOW, type="interaction", label="login")


def row(**overrides):
    """One settled row the graph has no field for: no second factor for shoppers.

    Inferred rather than stated so the fixture carries no span, which keeps
    these tests about the catalog rather than about locating a quote.
    """
    fields = {
        "subject": SHOPPERS.id,
        "predicate": "mfa-requirement",
        "value": ABSENT,
        "basis": "inferred",
        "explanation": "the description names password login and nothing else",
    }
    return Assertion(**{**fields, **overrides})


def assertions(*rows, subjects=(SHOPPERS, COOKIE)):
    return AssertionCatalog(subjects=list(subjects), entries=list(rows))


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
            kind="derived-fact", flow_id="flow:entity:customer>process:web-app>login"
        )

    def test_an_undecidable_crossing_is_not_catalogued(self):
        """ADR 0039 rule 4, at the seam a lane agent reads.

        A flow whose endpoint the sources never placed derives a crossing with
        ``decided=False``. The catalog is what an agent may cite and a critic
        lets stand, so an entry for it would say a boundary was crossed — the
        one thing rule 4 says an undecidable crossing does not establish. The
        unplaced zone still reaches the agent as its own ``unknown`` entry.
        """
        model = valid_model()
        model.data_stores[0].trust_zone = UNKNOWN
        flow_id = "flow:process:web-app>store:orders-db>store-order"

        (crossing,) = [
            one for one in model.boundary_crossings() if one.flow_id == flow_id
        ]
        catalog = evidence_catalog(model)

        assert crossing.decided is False
        assert crossing_evidence_ref(flow_id) not in catalog
        assert unknown_evidence_ref("store:orders-db", "trust_zone") in catalog

    @pytest.mark.parametrize(
        "unplaced",
        [
            ["process:web-app"],
            ["store:orders-db"],
            ["process:web-app", "store:orders-db"],
        ],
    )
    def test_an_undecidable_crossing_offers_its_unplaced_endpoint_instead(
        self, unplaced
    ):
        """The row ``prompts/analyze.md`` sends a lane agent to, tested here.

        The prompt tells an agent that an undecidable crossing cannot be cited
        and that the unplaced endpoint's ``trust_zone`` can. That is a claim
        about this catalog, so the two readers are tested against each other
        rather than each against its own expectation: for every undecidable
        crossing, every endpoint the sources left unplaced publishes its own
        ``unknown`` row.
        """
        model = valid_model()
        by_id = {element.id: element for element in model.zoned_elements()}
        for element_id in unplaced:
            by_id[element_id].trust_zone = UNKNOWN
        flows = {flow.id: flow for flow in model.data_flows}

        catalog = evidence_catalog(model)

        undecided = [
            crossing for crossing in model.boundary_crossings() if not crossing.decided
        ]
        assert undecided
        for crossing in undecided:
            flow = flows[crossing.flow_id]
            endpoints = [
                endpoint
                for endpoint, zone in (
                    (flow.source, crossing.source_zone),
                    (flow.destination, crossing.destination_zone),
                )
                if zone == UNKNOWN
            ]
            assert endpoints
            assert crossing_evidence_ref(crossing.flow_id) not in catalog
            for endpoint in endpoints:
                assert unknown_evidence_ref(endpoint, "trust_zone") in catalog

    def test_a_claim_cannot_ground_on_an_undecidable_crossing(self):
        """The other half: naming it is refused rather than silently accepted."""
        model = valid_model()
        model.data_stores[0].trust_zone = UNKNOWN
        claim = sample_draft(
            grounds=[
                Ground(
                    kind="derived-fact",
                    flow_id="flow:process:web-app>store:orders-db>store-order",
                )
            ]
        )

        issues = ground_issues([claim], model)

        assert issues and "not a decided boundary crossing" in issues[0]

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

        with_rows = evidence_catalog(model, assertions(row())).values()
        assert {ground.kind for ground in with_rows} == kinds | {"assertion"}

    def test_a_control_the_input_states_is_absent_is_enumerated(self):
        """The gap #171 was filed for.

        A catalog testing exact equality with the ``unknown`` sentinel would
        make a control the submitter said is *not there* — which 11 of the 12
        candidate rules fire on, through ``is_unverified`` — a fact no agent
        could cite. It has its own entry and its own kind, because
        "nobody said" and "somebody said no" carry different threats.
        """
        model = valid_model()
        model.data_flows[0].authentication = "none; accepted by network position"

        catalog = evidence_catalog(model)

        ref = absent_evidence_ref(
            "flow:entity:customer>process:web-app>login", "authentication"
        )
        assert catalog[ref] == Ground(
            kind="absent-attribute",
            element_id="flow:entity:customer>process:web-app>login",
            attribute="authentication",
        )
        assert (
            unknown_evidence_ref(
                "flow:entity:customer>process:web-app>login", "authentication"
            )
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

        ref = unknown_evidence_ref(
            "flow:entity:customer>process:web-app>login", "authentication"
        )
        assert catalog[ref].kind == "unknown-attribute"

    def test_a_stated_control_reading_no_is_not_an_absence(self):
        """Only the leading token is read, which is what keeps this from
        becoming a classifier with a security opinion: ``no MFA on the password
        login`` describes a mechanism that exists."""
        model = valid_model()
        model.data_flows[0].authentication = "no MFA on the password login"

        catalog = evidence_catalog(model)

        flow_id = "flow:entity:customer>process:web-app>login"
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
            absent_evidence_ref(
                "flow:entity:customer>process:web-app>login", "protocol"
            )
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

    def test_a_settled_row_the_graph_has_no_field_for_is_enumerated(self):
        """The fact the layer exists to carry: "no MFA" as a row a claim may cite.

        Ten of the corpus's 21 stated mechanism values state an absence, and
        the catalog offered nothing for any of them because the attribute
        read as ``stated``. The row's own identity is the reference, so the
        row and the entry can never spell one fact two ways.
        """
        held = assertions(row())
        catalog = evidence_catalog(valid_model(), held)

        assert catalog[assertion_id(row())] == Ground(
            kind="assertion", assertion=assertion_id(row())
        )

    def test_no_catalog_is_the_catalog_before_the_layer_existed(self):
        """``None`` and an empty catalog both offer the model's own rows, and
        nothing else — a job that ran no pass and a pass that settled nothing
        show an agent the same table."""
        model = valid_model()

        assert evidence_catalog(model, None) == evidence_catalog(model)
        assert evidence_catalog(model, assertions()) == evidence_catalog(model)

    def test_a_row_the_graph_carries_is_cited_through_the_field(self):
        """One fact, one reader: a projected mechanism reaches every rule
        through ``authentication``, so a second entry for it would be a second
        reader of one fact, and the two could disagree."""
        mechanism = row(
            subject=LOGIN_FLOW,
            predicate="authentication-mechanism",
            value="email and password",
        )
        held = assertions(mechanism, subjects=(LOGIN,))
        model, applied = apply_projection(valid_model(), held)
        catalog = evidence_catalog(model, held)

        assert [projection.reason for projection in applied] == ["stated"]
        assert "authentication-mechanism" not in UNPROJECTED
        assert assertion_id(mechanism) not in catalog

    def test_a_row_the_graph_does_not_carry_is_cited_as_itself(self):
        """The same row over a model that does not hold its value: routing is
        by what reached the graph, never by the predicate's name (#926)."""
        mechanism = row(
            subject=LOGIN_FLOW,
            predicate="authentication-mechanism",
            value="email and password",
        )
        catalog = evidence_catalog(
            valid_model(), assertions(mechanism, subjects=(LOGIN,))
        )

        assert assertion_id(mechanism) in catalog

    @pytest.mark.parametrize(
        "unsettled",
        [
            row(basis="legacy", explanation=""),
            row(assessment="unsupported", assessor="reviewer/1"),
            row(assessment="unresolved", assessor="reviewer/1"),
        ],
        ids=["legacy", "unsupported", "unresolved"],
    )
    def test_a_row_nobody_may_rest_on_is_never_offered(self, unsettled):
        """A legacy row is never support, and an
        assessed-unsupported row is set aside — none is a fact an agent may
        cite, so none is in the table an agent selects from."""
        catalog = evidence_catalog(valid_model(), assertions(unsettled))

        assert assertion_id(unsettled) not in catalog

    def test_an_open_question_is_offered_as_one(self):
        """The gap this closes: a fact the sources were asked and left open.

        An element attribute nobody stated is already offered, so an agent can
        raise a conditional claim on it. A predicate with no graph field had no
        such offer, and its subject may be a principal, so the attribute walk
        above could never reach it.
        """
        open_row = row(value=UNKNOWN, reason="silent", explanation="")
        catalog = evidence_catalog(valid_model(), assertions(open_row))

        assert catalog[assertion_id(open_row)].kind == "unknown-assertion"

    def test_a_settled_row_and_an_open_one_are_different_entries(self):
        """The identity carries the value, so the two never collide."""
        settled_row = row()
        open_row = row(value=UNKNOWN, reason="silent", explanation="")
        catalog = evidence_catalog(valid_model(), assertions(settled_row, open_row))

        assert catalog[assertion_id(settled_row)].kind == "assertion"
        assert catalog[assertion_id(open_row)].kind == "unknown-assertion"

    def test_a_claim_cannot_rest_on_an_open_question_by_calling_it_settled(self):
        """Both kinds are offered, so membership alone cannot tell them apart."""
        open_row = row(value=UNKNOWN, reason="silent", explanation="")
        claim = sample_draft(
            grounds=[Ground(kind="assertion", assertion=assertion_id(open_row))]
        )

        issues = ground_issues([claim], valid_model(), assertions(open_row))

        assert issues and "leaves that row open" in issues[0]

    def test_a_claim_cannot_call_a_settled_fact_an_open_question_either(self):
        """The mirror, because either direction misreads the catalog."""
        settled_row = row()
        held = assertions(settled_row)
        claim = sample_draft(
            grounds=[
                Ground(kind="unknown-assertion", assertion=assertion_id(settled_row))
            ]
        )

        issues = ground_issues([claim], valid_model(), held)

        assert issues and "settles that row" in issues[0]

    def test_a_conflict_settles_nothing_on_either_side(self):
        """Two rows that disagree stay visible in the catalog and ground nothing:
        a definite entry for either would be the service picking a side no
        adjudication recorded."""
        absent = row()
        required = row(value="required")
        catalog = evidence_catalog(valid_model(), assertions(absent, required))

        assert assertion_id(absent) not in catalog
        assert assertion_id(required) not in catalog

    def test_a_scoped_row_is_offered_with_its_scope_and_answers_nothing_wider(self):
        """A second factor required for administrators is a fact — for
        administrators. It sits beside an unscoped absence rather than
        suppressing it, because the two are different questions."""
        admins = row(
            value="required",
            scope=[Qualifier(kind="principal", value="administrators")],
        )
        catalog = evidence_catalog(valid_model(), assertions(row(), admins))

        assert assertion_id(row()) in catalog
        assert assertion_id(admins) in catalog
        rendered = render_catalog(catalog, assertions(row(), admins))
        assert "where principal is administrators" in rendered

    def test_an_assertion_row_leaves_the_models_own_rows_untouched(self):
        """Additive by construction: an unknown attribute on the login flow is
        still a question the input left open, whatever the catalog settles
        about the principal that uses it."""
        with_rows = evidence_catalog(valid_model(), assertions(row()))
        without = evidence_catalog(valid_model())

        assert {ref: with_rows[ref] for ref in without} == without

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
    entry rather than the job (:class:`~analysis_service.claims.UnresolvedEvidence`) once it has been.
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
                "absent:flow:process:a>process:b>call:authentication": Ground(
                    kind="absent-attribute",
                    element_id="flow:process:a>process:b>call",
                    attribute="authentication",
                ),
                "crossing:flow:process:a>process:b>call": Ground(
                    kind="derived-fact", flow_id="flow:process:a>process:b>call"
                ),
            }
        )

        assert "`encryption_at_rest` never stated" in rendered
        assert "`authentication` stated absent" in rendered
        assert "crosses a trust boundary" in rendered

    def test_an_assertion_row_glosses_what_it_states_and_who_said_so(self):
        """The one entry whose left column is a digest, so the right column
        carries the subject, the predicate, the value and the basis. An
        inference reads as one, and a graph-bound subject reads as its ID."""
        inferred = row()
        owned = Assertion(
            subject="process:web-app",
            predicate="tenant-ownership",
            value=SHOPPERS.id,
            basis="inferred",
            explanation="the description reads as the shoppers' own deployment",
        )
        kept = Assertion(
            subject=COOKIE.id,
            predicate="credential-custody",
            value="the browser's cookie jar",
            basis="inferred",
            explanation="a session cookie lives in the browser",
        )
        held = assertions(inferred, owned, kept)
        rendered = render_catalog(evidence_catalog(valid_model(), held), held)

        assert (
            "`mfa-requirement` inferred absent for shopper accounts (principal)"
            in rendered
        )
        assert (
            "`tenant-ownership` inferred `shopper accounts` on `process:web-app`"
            in (rendered)
        )
        assert (
            "`credential-custody` inferred `the browser's cookie jar` for session"
            " cookie (credential)" in rendered
        )

    def test_an_assertion_row_cannot_render_without_its_catalog(self):
        """A bare identity is nothing an agent could select on, so the renderer
        refuses rather than printing one."""
        catalog = evidence_catalog(valid_model(), assertions(row()))

        with pytest.raises(KeyError):
            render_catalog(catalog)

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


class TestTheCriticReadsTheRowItIsAskedToRuleOn:
    """An assertion ground carries the row's identity and no fact (#1082).

    The value and the scope are digests inside that identity, so a critic
    handed the ground alone could not read what the row states — and it is
    asked whether the claim follows from the facts it cites. One reader,
    ``ground_gloss``, so the table the lane agent selected from and the view
    the critic rules on cannot say two things about one row.
    """

    def test_the_value_and_the_scope_reach_the_critic(self):
        stated = row(
            predicate="credential-lifetime",
            subject=COOKIE.id,
            value="twelve hours",
            basis="inferred",
            scope=[Qualifier(kind="operation", value="release jobs")],
        )
        held = assertions(stated)
        ground = evidence_catalog(valid_model(), held)[assertion_id(stated)]
        draft = sample_draft("S-01", grounds=[ground])

        (view,) = critic_view([draft], valid_model(), assertions=held)

        (fact,) = view["assertion_facts"]
        assert fact["assertion"] == assertion_id(stated)
        assert "twelve hours" in fact["says"]
        assert "release jobs" in fact["says"]

    def test_the_critic_and_the_lane_agent_read_one_sentence(self):
        """The gloss is the table's own, so the two cannot be told two things."""
        held = assertions(row())
        ground = evidence_catalog(valid_model(), held)[assertion_id(row())]
        draft = sample_draft("S-01", grounds=[ground])

        (view,) = critic_view([draft], valid_model(), assertions=held)

        assert view["assertion_facts"][0]["says"] in render_catalog(
            evidence_catalog(valid_model(), held), held
        )
        assert view["assertion_facts"][0]["says"] == ground_gloss(ground, held)

    def test_a_job_that_ran_no_catalog_carries_no_key(self):
        (view,) = critic_view([sample_draft("S-01")], valid_model())

        assert "assertion_facts" not in view


class TestAnAssertionGroundIsHeldToTheCatalog:
    """The report's load check and the fan-in's, through the one reader."""

    def test_a_ground_naming_a_settled_row_raises_nothing(self):
        held = assertions(row())
        draft = sample_draft(
            grounds=[Ground(kind="assertion", assertion=assertion_id(row()))]
        )

        assert ground_issues([draft], valid_model(), held) == []

    def test_a_ground_naming_a_row_the_catalog_does_not_settle_is_reported(self):
        draft = sample_draft(
            grounds=[Ground(kind="assertion", assertion=assertion_id(row()))]
        )

        (issue,) = ground_issues([draft], valid_model(), assertions())
        assert "does not offer" in issue
        (issue,) = ground_issues([draft], valid_model())
        assert "does not offer" in issue


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

        resolution = resolve_proposals(
            [proposal], catalog, STRIDE, "spoofing", valid_model()
        )

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
            [proposal], catalog, STRIDE, "spoofing", valid_model()
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

        resolution = resolve_proposals(
            [proposal], catalog, STRIDE, "spoofing", valid_model()
        )

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

        resolution = resolve_proposals(
            [proposal], catalog, STRIDE, "spoofing", valid_model()
        )

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

        resolution = resolve_proposals(
            proposals, catalog, STRIDE, "spoofing", valid_model()
        )

        assert [draft.id for draft in resolution.drafts] == ["S-01"]
        assert [m.claim_id for m in resolution.marks.dropped_claims] == ["S-02"]

    def test_a_clean_lane_records_no_marks(self):
        catalog = evidence_catalog(valid_model())

        resolution = resolve_proposals(
            [sample_proposal()], catalog, STRIDE, "spoofing", valid_model()
        )

        assert resolution.marks.unresolved_evidence == []


class TestResolveProposals:
    def test_a_reference_resolves_to_the_catalog_entry_it_names(self):
        catalog = evidence_catalog(valid_model())
        proposal = sample_proposal("S-01", evidence_refs=[ENCRYPTION_REF], quotes=[])

        (draft,) = resolve_proposals(
            [proposal], catalog, STRIDE, "spoofing", valid_model()
        ).drafts

        assert draft.grounds == [catalog[ENCRYPTION_REF]]

    def test_a_quote_candidate_becomes_a_quote_ground(self):
        catalog = evidence_catalog(valid_model())
        proposal = sample_proposal(
            "S-01",
            evidence_refs=[],
            quotes=[{"text": "Customers log in", "source_label": "Description"}],
        )

        (draft,) = resolve_proposals(
            [proposal], catalog, STRIDE, "spoofing", valid_model()
        ).drafts

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

        (draft,) = resolve_proposals(
            [proposal], catalog, STRIDE, "spoofing", valid_model()
        ).drafts

        assert [ground.kind for ground in draft.grounds] == [
            "quote",
            "derived-fact",
            "unknown-attribute",
        ]

    def test_resolution_changes_nothing_but_the_evidence(self):
        """The agent's other seven fields reach the report as written."""
        catalog = evidence_catalog(valid_model())

        (draft,) = resolve_proposals(
            [sample_proposal()], catalog, STRIDE, "spoofing", valid_model()
        ).drafts

        assert draft == sample_draft()

    def test_resolving_twice_gives_the_same_drafts(self):
        catalog = evidence_catalog(valid_model())
        proposals = [sample_proposal("S-01"), sample_proposal("S-02")]

        assert resolve_proposals(
            proposals, catalog, STRIDE, "spoofing", valid_model()
        ) == resolve_proposals(proposals, catalog, STRIDE, "spoofing", valid_model())

    def test_a_reference_naming_nothing_is_reported_as_itself(self):
        """There is no near match and no repair: inferring which fact was
        *meant* is the guess this design removes."""
        catalog = evidence_catalog(valid_model())
        proposal = sample_proposal(
            "S-01", evidence_refs=["crossing:flow:not-real"], quotes=[]
        )

        resolution = resolve_proposals(
            [proposal], catalog, STRIDE, "spoofing", valid_model()
        )

        assert resolution.drafts == []
        assert "crossing:flow:not-real" in resolution.marks.dropped_claims[0].reason

    def test_every_bad_reference_in_the_batch_is_marked(self):
        catalog = evidence_catalog(valid_model())
        proposals = [
            sample_proposal("S-01", evidence_refs=["crossing:flow:ghost"], quotes=[]),
            sample_proposal("S-02", evidence_refs=["unknown:store:ghost:x"], quotes=[]),
        ]

        resolution = resolve_proposals(
            proposals, catalog, STRIDE, "spoofing", valid_model()
        )

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

    def test_a_blank_reference_is_skipped_rather_than_marked(self):
        """A mark names what the catalog does not hold; an empty string names nothing.

        ``evidence_refs`` is a free list the model fills, and a mark's reference
        cannot be empty. So a blank entry earns no mark, and the claim stands on
        the references that did resolve.
        """
        catalog = evidence_catalog(valid_model())
        proposal = sample_proposal(
            "S-01", evidence_refs=["", ENCRYPTION_REF], quotes=[]
        )

        resolution = resolve_proposals(
            [proposal], catalog, STRIDE, "spoofing", valid_model()
        )

        (draft,) = resolution.drafts
        assert draft.grounds == [catalog[ENCRYPTION_REF]]
        assert resolution.marks.unresolved_evidence == []

    def test_surrounding_whitespace_on_a_reference_is_not_a_defect(self):
        """Which spelling of a name arrived is mechanical; settled here rather
        than argued with in a prompt."""
        catalog = evidence_catalog(valid_model())
        proposal = sample_proposal(
            "S-01", evidence_refs=[f"  {ENCRYPTION_REF} "], quotes=[]
        )

        (draft,) = resolve_proposals(
            [proposal], catalog, STRIDE, "spoofing", valid_model()
        ).drafts

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

        (draft,) = resolve_proposals(
            [proposal], catalog, STRIDE, "spoofing", valid_model()
        ).drafts

        assert draft.grounds[0].kind == "derived-fact"
        assert draft.grounds[0].flow_id == "flow:entity:customer>process:web-app>login"
        assert not draft.grounds[0].attribute

    def test_an_agent_cannot_name_a_lane_or_a_threat_id_at_all(self):
        """The category-letter mismatch, gone the way the mis-shape went.

        An agent that restated its own category and composed an ID whose
        letter had to agree with it would hold two spellings of a constant the
        graph fills in at build time, and a disagreement between them would fail
        the node and cancel the five sibling lanes.
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

        (draft,) = resolve_proposals(
            [proposal], catalog, STRIDE, "tampering", valid_model()
        ).drafts

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
    ``flow:process:a>process:b>label:label``, its own label concatenated twice: well-formed,
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


class TestAnAbsenceIsAGround:
    """#412: a claim about what a system does not have needs a ground that says so.

    Every other branch names something present, so a framework that rules a
    unit out of scope had nothing honest to cite and reached for an unrelated
    quote instead. This branch's referent is the whole model.
    """

    def test_a_term_the_model_names_nowhere_becomes_a_ground(self):
        catalog = evidence_catalog(valid_model())
        proposal = sample_proposal(
            "S-01", evidence_refs=[], quotes=[], absent_elements=["ldap"]
        )

        (draft,) = resolve_proposals(
            [proposal], catalog, STRIDE, "spoofing", valid_model()
        ).drafts

        assert draft.grounds == [Ground(kind="absent-element", term="ldap")]

    def test_a_term_the_model_does_name_is_refused(self):
        """The check that stops this branch asserting an absence that is not one."""
        model = valid_model()
        model.processes[0].description += " It queries an LDAP directory."
        proposal = sample_proposal(
            "S-01", evidence_refs=[], quotes=[], absent_elements=["ldap"]
        )

        resolution = resolve_proposals(
            [proposal], evidence_catalog(model), STRIDE, "spoofing", model
        )

        assert resolution.drafts == []
        (mark,) = resolution.marks.dropped_claims
        assert "absent-element:ldap" in mark.reason

    def test_the_term_is_lowercased_and_stripped(self):
        catalog = evidence_catalog(valid_model())
        proposal = sample_proposal(
            "S-01", evidence_refs=[], quotes=[], absent_elements=["  LDAP  "]
        )

        (draft,) = resolve_proposals(
            [proposal], catalog, STRIDE, "spoofing", valid_model()
        ).drafts

        assert draft.grounds == [Ground(kind="absent-element", term="ldap")]

    def test_a_term_longer_than_the_ground_holds_costs_its_entry(self):
        """The field holds one term, so a sentence in it names no term.

        ``absent_elements`` is a free list the model fills, and the ground it
        builds is bounded. An over-long entry leaves the way a contradicted one
        does — as a mark — because the alternative is a raise in the merge node
        that costs the run every lane it already paid for.
        """
        catalog = evidence_catalog(valid_model())
        sentence = "no directory service " * 10
        assert len(sentence) > GROUND_TERM_MAX_CHARS
        proposal = sample_proposal(
            "S-01", evidence_refs=[], quotes=[], absent_elements=[sentence]
        )

        resolution = resolve_proposals(
            [proposal], catalog, STRIDE, "spoofing", valid_model()
        )

        assert resolution.drafts == []
        (mark,) = resolution.marks.dropped_claims
        assert "cites only evidence" in mark.reason

    def test_a_blank_term_earns_no_mark(self):
        """The rule the two sibling lists follow: an empty string names nothing.

        It cannot be filtered where a blank evidence reference is, because the
        ``absent-element:`` prefix has already made the string non-blank by
        then. So a mark reading ``absent-element:`` with no term after it named
        nothing for a reader to chase.
        """
        catalog = evidence_catalog(valid_model())
        proposal = sample_proposal(
            "S-01", evidence_refs=[], quotes=[], absent_elements=["   ", "ldap"]
        )

        resolution = resolve_proposals(
            [proposal], catalog, STRIDE, "spoofing", valid_model()
        )

        (draft,) = resolution.drafts
        assert draft.grounds == [Ground(kind="absent-element", term="ldap")]
        assert resolution.marks.unresolved_evidence == []

    def test_an_absence_alone_justifies_a_proposal(self):
        """The min-one rule runs over three lists, not two."""
        assert sample_proposal(
            "S-01", evidence_refs=[], quotes=[], absent_elements=["ldap"]
        )

    def test_a_proposal_citing_nothing_at_all_is_still_refused(self):
        with pytest.raises(ValidationError, match="justifies itself with nothing"):
            sample_proposal("S-01", evidence_refs=[], quotes=[], absent_elements=[])


class TestWhichGroundsMakeAClaimConditional:
    """`CONDITIONAL_GROUNDS` is the one reader, and a seventh kind must join it.

    The mark it feeds licenses a threat to offer no countermeasure. So a kind
    missing from the set reads as a well-formed claim rather than as an error,
    which is why the set is named once and checked here rather than spelled at
    each seam.
    """

    def _marks(self, ground):
        return DraftThreat.claim_marks([sample_draft(grounds=[ground], mitigations=[])])

    def test_an_open_assertion_licenses_a_threat_with_no_countermeasure(self):
        """You cannot name a control for a fact nobody has stated yet."""
        ground = Ground(
            kind="unknown-assertion",
            assertion="assertion:mfa-requirement~principal:shoppers~any~unknown",
        )
        assert self._marks(ground).missing_mitigations == []

    def test_a_settled_assertion_does_not(self):
        """A fact in hand: "put the control in" is always available."""
        ground = Ground(
            kind="assertion",
            assertion="assertion:mfa-requirement~principal:shoppers~any~absent",
        )
        assert self._marks(ground).missing_mitigations != []

    def test_the_set_holds_the_two_questions_and_neither_absence(self):
        assert CONDITIONAL_GROUNDS == {"unknown-attribute", "unknown-assertion"}


class TestWhatTheAssertionPassReached:
    """Coverage as a derived count, because a short catalog and a quiet system
    look identical without one.

    ADR 0034 rejected storing this. It is computed from the rows and the model,
    so it cannot drift from what it summarises.
    """

    def test_a_job_that_ran_no_pass_reports_nothing(self):
        """`None` is every job on a deployment that has not set the flag."""
        assert sample_report().assertion_coverage is None

    def test_the_denominator_is_what_this_model_could_hold(self):
        """Not what the corpus holds, and not what the pass happened to ask.

        The count is every (subject, predicate) slot this model admits, so a
        predicate added for a subject type it carries raises it. That is the
        denominator doing its job: a new question the layer can ask is one more
        thing a quiet catalog has left unanswered.
        """
        held = catalog_coverage(AssertionCatalog(), valid_model())

        assert held.reachable == 10 and held.settled == 0

    def test_a_settled_row_counts_against_that_denominator(self):
        """A row on a flow, under a predicate that has a graph field."""
        mechanism = row(
            subject=LOGIN_FLOW, predicate="authentication-mechanism", value="mTLS"
        )
        flow = Subject(id=LOGIN_FLOW, type="interaction", label="login")
        held = catalog_coverage(assertions(mechanism, subjects=(flow,)), valid_model())

        assert held.settled == 1

    def test_an_open_question_is_counted_and_never_settles_an_attribute(self):
        """Coverage stated as rows: the sources raised it and left it open."""
        held = catalog_coverage(
            assertions(row(value=UNKNOWN, reason="silent", explanation="")),
            valid_model(),
        )

        assert held.open == 1 and held.settled == 0

    def test_a_fact_about_a_principal_is_counted_apart(self):
        """The part of the catalog a reader can find nowhere else."""
        held = AssertionCatalog(
            subjects=[
                Subject(
                    id="principal:shoppers", type="principal", label="shopper accounts"
                )
            ],
            entries=[
                Assertion(
                    subject="principal:shoppers",
                    predicate="mfa-requirement",
                    value=ABSENT,
                    basis="inferred",
                    explanation="the description names password login only",
                )
            ],
        )
        counted = catalog_coverage(held, valid_model())

        assert counted.own_subjects == 1 and counted.settled == 0


def test_every_assertion_ground_kind_has_a_gloss():
    """The table against the set that names its keys.

    ``_gloss`` dispatches assertion grounds through
    :data:`~analysis_service.evidence.ASSERTION_GLOSSES`, and a kind in
    :data:`~analysis_service.claims.ASSERTION_GROUNDS` with no entry would fall
    past the dispatch and read as an unstated attribute. That is a wrong
    sentence in a lane agent's catalog rather than a raise, so the comparison
    is here.
    """
    assert set(ASSERTION_GLOSSES) == set(ASSERTION_GROUNDS)
