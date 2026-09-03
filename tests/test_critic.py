"""Tests for the mechanical checks around the critic: join and assemble."""

import time

import pytest

from analysis_service import critic
from analysis_service.critic import (
    CriticOutputError,
    DraftJoinError,
    assemble_claims,
    join_drafts,
    mentioned_ids,
    review_issues,
    snap_drafts,
    snap_rulings,
)
from analysis_service.frameworks import schemas_for
from analysis_service.frameworks.stride import STRIDE
from analysis_service.frameworks.stride.record import DraftThreat
from analysis_service.report import (
    MENTION_MAX_CHARS,
    REASON_MAX_CHARS,
    Ground,
    Mitigation,
    ProposedVerdict,
    Severity,
    UnknownRef,
    Verdict,
)
from analysis_service.sources import DEFAULT_DESCRIPTION_LABEL
from tests.factories import sample_draft, sample_ruling, valid_model

#: The five model-facing shapes STRIDE's own nodes speak in. ``assemble_claims``
#: needs the ruled record to build; the package itself carries the draft.
SCHEMAS = schemas_for("stride")

LABEL = DEFAULT_DESCRIPTION_LABEL
# The job's one source, as the executor hands it to the fan-in.
SOURCES = {LABEL: "Customers log in to the web app, which stores orders."}
# A flow the sample model really derives as a boundary crossing.
CROSSING = "flow:customer-to-web-app:login"


@pytest.fixture
def model():
    return valid_model()


def mitigation(summary="Set HttpOnly and Secure on cookies"):
    return Mitigation(summary=summary)


def severity(likelihood="medium", impact="high"):
    return Severity(
        likelihood=likelihood, impact=impact, justification="Stated model fact."
    )


class TestJoinDrafts:
    def test_merges_in_canonical_stride_order(self, model):
        merged = join_drafts(
            {
                "tampering": [sample_draft("T-01", "tampering")],
                "spoofing": [sample_draft("S-01")],
                "repudiation": [sample_draft("R-01", "repudiation")],
            },
            STRIDE,
            model,
        )
        assert [draft.id for draft in merged.drafts] == ["S-01", "T-01", "R-01"]

    def test_absent_categories_contribute_nothing(self, model):
        joined = join_drafts({"spoofing": [sample_draft()]}, STRIDE, model)
        assert joined.drafts == [sample_draft()]

    def test_empty_analysis_is_legal(self, model):
        assert join_drafts({}, STRIDE, model).drafts == []

    def test_a_claim_naming_only_absent_elements_is_dropped_and_marked(self, model):
        """A finding about nothing is not a finding. It costs the claim, never
        the job, and the mark names what was cited."""
        drafts = {
            "spoofing": [sample_draft(affected_element_ids=["process:does-not-exist"])]
        }

        joined = join_drafts(drafts, STRIDE, model)

        assert joined.drafts == []
        (mark,) = joined.marks.dropped_claims
        assert mark.claim_id == "S-01"
        assert "process:does-not-exist" in mark.reason
        assert joined.marks.unresolved_references == []

    def test_an_absent_element_beside_a_real_one_costs_only_itself(self, model):
        """The rule every other citation has: the reference is dropped and
        marked, and the claim stands on the elements that resolved."""
        drafts = {
            "spoofing": [
                sample_draft(affected_element_ids=["process:web-app", "process:ghost"])
            ]
        }

        joined = join_drafts(drafts, STRIDE, model)

        (draft,) = joined.drafts
        assert draft.affected_element_ids == ["process:web-app"]
        assert [
            (m.claim_id, m.element_id) for m in joined.marks.unresolved_references
        ] == [("S-01", "process:ghost")]
        assert joined.marks.dropped_claims == []

    def test_a_respelled_element_reference_resolves_and_is_canonicalized(self, model):
        """The fold runs before the check, and the report carries the job's ID."""
        drafts = {"spoofing": [sample_draft(affected_element_ids=["Process:Web-App"])]}
        [joined] = join_drafts(drafts, STRIDE, model).drafts
        assert joined.affected_element_ids == ["process:web-app"]

    def test_a_duplicate_id_keeps_the_first_draft_and_drops_the_rest(self, model):
        """Deterministic: the lane order is the package's own, so the same
        draft survives on every run."""
        drafts = {
            "spoofing": [
                sample_draft("S-01", title="first"),
                sample_draft("S-01", title="second"),
            ]
        }

        joined = join_drafts(drafts, STRIDE, model)

        assert [draft.title for draft in joined.drafts] == ["first"]
        (mark,) = joined.marks.dropped_claims
        assert (mark.claim_id, mark.title) == ("S-01", "second")
        assert "repeats the ID" in mark.reason

    def test_a_lane_and_its_drafts_cannot_disagree_about_the_category(self, model):
        """There is nothing left here to check, and that is the point.

        A draft's category is stamped from the lane by ``resolve_proposals``
        rather than written by the agent, so this seam would be comparing a
        value against the one it was copied from. Whether a threat *belongs* in
        its lane is a question about the finding's content, and it is the
        critic's second judgement step.
        """
        drafts = {"spoofing": [sample_draft("T-01", "tampering")]}

        assert join_drafts(drafts, STRIDE, model).drafts[0].category == "tampering"


class TestGroundReferences:
    """Set membership at the fan-in, one branch at a time.

    Every failure here is fatal. There is no re-ask path for a category agent's
    drafts — ``repair`` is extraction-only and ``recritic`` is critic-only — so
    an unresolved reference kills the job, which is why the checks are exactly
    set membership and go no further.
    """

    def grounded(self, *grounds, threat_id="S-01"):
        return {"spoofing": [sample_draft(threat_id, grounds=list(grounds))]}

    def test_a_quote_naming_a_source_the_job_never_carried_is_unverified(self, model):
        """The label is the agent's, so a label naming nothing is a quote that
        cannot be found — marked, and groundless if it stood alone."""
        drafts = self.grounded(
            Ground(kind="quote", text="anything", source_label="Never submitted"),
            Ground(kind="derived-fact", flow_id=CROSSING),
        )

        joined = join_drafts(drafts, STRIDE, model, SOURCES)

        (mark,) = joined.marks.unverified_grounds
        assert "not one of this job's sources" in mark.reason
        assert "Never submitted" in mark.reason
        assert len(joined.drafts) == 1

    def test_an_unknown_attribute_on_an_element_that_does_not_exist(self, model):
        drafts = self.grounded(
            Ground(
                kind="unknown-attribute",
                element_id="store:ghost",
                attribute="encryption_at_rest",
            )
        )
        with pytest.raises(DraftJoinError, match="not in the system model"):
            join_drafts(drafts, STRIDE, model, SOURCES)

    def test_an_attribute_the_element_type_does_not_have(self, model):
        """An ExternalEntity has no ``encryption_at_rest``; naming one is a guess."""
        drafts = self.grounded(
            Ground(
                kind="unknown-attribute",
                element_id="entity:customer",
                attribute="encryption_at_rest",
            )
        )
        with pytest.raises(DraftJoinError, match="does not have"):
            join_drafts(drafts, STRIDE, model, SOURCES)

    def test_a_pointer_spelled_attribute_resolves(self, model):
        """A field name arriving as ``/exposure`` is the field ``exposure``.

        The spelling is the provider's, not the prompt's, and it used to kill
        the job here — six lanes of drafts thrown away over a leading slash on
        a name whose element really does carry the field.
        """
        drafts = self.grounded(
            Ground(
                kind="unknown-attribute",
                element_id="process:web-app",
                attribute="/exposure",
            )
        )
        assert join_drafts(drafts, STRIDE, model, SOURCES).drafts

    def test_a_pointer_spelled_attribute_the_element_lacks_still_fails(self, model):
        drafts = self.grounded(
            Ground(
                kind="unknown-attribute",
                element_id="entity:customer",
                attribute="/encryption_at_rest",
            )
        )
        with pytest.raises(DraftJoinError, match="does not have"):
            join_drafts(drafts, STRIDE, model, SOURCES)

    def test_an_absent_attribute_on_an_element_that_does_not_exist(self, model):
        """The absent branch resolves through the same check as the unknown one
        — it names the same two fields — and says which branch failed."""
        drafts = self.grounded(
            Ground(
                kind="absent-attribute",
                element_id="store:ghost",
                attribute="encryption_at_rest",
            )
        )
        with pytest.raises(DraftJoinError, match="an absent attribute"):
            join_drafts(drafts, STRIDE, model, SOURCES)

    def test_an_absent_attribute_is_never_checked_against_its_value(self, model):
        """Set membership only, matched to the depth ``related_unknowns`` is
        checked at: requiring the attribute to actually read ``none`` would
        encode a judgement as a mechanical rule, and the catalog is what
        decided the branch in the first place."""
        drafts = self.grounded(
            Ground(
                kind="absent-attribute",
                element_id="process:web-app",
                attribute="exposure",
            )
        )
        assert join_drafts(drafts, STRIDE, model, SOURCES).drafts

    def test_a_derived_fact_naming_a_flow_that_does_not_cross(self, model):
        drafts = self.grounded(
            Ground(kind="derived-fact", flow_id="flow:not-a:crossing")
        )
        with pytest.raises(DraftJoinError, match="not a derived boundary crossing"):
            join_drafts(drafts, STRIDE, model, SOURCES)

    def test_a_respelled_source_label_resolves_to_the_jobs_label(self, model):
        """A quote whose label differs only in case cites the caller's bytes."""
        drafts = self.grounded(
            Ground(
                kind="quote",
                text="log in to the web app",
                source_label=LABEL.upper(),
            )
        )
        joined = join_drafts(drafts, STRIDE, model, SOURCES)
        assert joined.drafts[0].grounds[0].source_label == LABEL
        assert joined.marks.unverified_grounds == []

    def test_the_label_half_does_not_run_without_sources(self, model):
        """The gate's own escape: no set to check against is not a wrong citation."""
        drafts = self.grounded(
            Ground(kind="quote", text="anything", source_label="Never submitted")
        )
        assert join_drafts(drafts, STRIDE, model).drafts


class TestMentionedIds:
    """What counts as an element ID written into prose."""

    @pytest.mark.parametrize(
        "description, expected",
        [
            ("An attacker reaches process:web-app.", ["process:web-app"]),
            (
                "Rides `flow:customer-to-web-app:login` inward.",
                ["flow:customer-to-web-app:login"],
            ),
            (
                "From entity:customer through process:web-app to store:orders-db",
                ["entity:customer", "process:web-app", "store:orders-db"],
            ),
            ("Crosses boundary:internet.", ["boundary:internet"]),
            ("Spelled Process:Web-App by the agent.", ["Process:Web-App"]),
        ],
    )
    def test_ids_are_found_however_they_are_written(self, description, expected):
        assert mentioned_ids(description) == expected

    @pytest.mark.parametrize(
        "description",
        [
            "Process: the web app transforms orders.",  # a colon in prose
            "It runs on Cloud Run and stores to Postgres.",
            "The store holds orders; the process reads them.",
            "TLS 1.3 protects the hop.",
        ],
    )
    def test_ordinary_prose_produces_nothing(self, description):
        """A miss is the acceptable failure here; a false alarm is not."""
        assert mentioned_ids(description) == []

    def test_a_flow_keeps_both_segments(self):
        """``store:orders-db:x`` must not read as a store followed by junk."""
        assert mentioned_ids("see store:orders-db:x") == ["store:orders-db"]

    def test_trailing_punctuation_is_not_part_of_the_id(self):
        assert mentioned_ids("reaches process:web-app, then stops.") == [
            "process:web-app"
        ]


class TestUnresolvedMentions:
    """Marked, never fatal: the fan-in has no re-ask path to spend on prose."""

    def drafted(self, description, threat_id="S-01"):
        return {"spoofing": [sample_draft(threat_id, description=description)]}

    def test_an_id_the_model_contains_is_not_marked(self, model):
        joined = join_drafts(
            self.drafted("An attacker reaches process:web-app."), STRIDE, model
        )
        assert joined.marks.unresolved_mentions == []

    def test_an_id_the_model_lacks_is_marked_and_the_job_survives(self, model):
        joined = join_drafts(
            self.drafted("It pivots into process:ghost."), STRIDE, model
        )

        assert [(m.claim_id, m.mention) for m in joined.marks.unresolved_mentions] == [
            ("S-01", "process:ghost")
        ]
        assert len(joined.drafts) == 1

    def test_the_exemplar_systems_ids_are_what_this_really_catches(self, model):
        """analyze.md forbids citing its worked exemplar; nothing checked it."""
        joined = join_drafts(
            self.drafted(
                "The attacker posts to process:web-api and reaches store:accounts-db."
            ),
            STRIDE,
            model,
        )
        assert [m.mention for m in joined.marks.unresolved_mentions] == [
            "process:web-api",
            "store:accounts-db",
        ]

    def test_a_respelled_id_resolves_rather_than_being_marked(self, model):
        joined = join_drafts(
            self.drafted("Reaches Process:Web-App first."), STRIDE, model
        )
        assert joined.marks.unresolved_mentions == []

    def test_marks_are_per_mention_not_per_threat(self, model):
        joined = join_drafts(
            self.drafted("From process:ghost through store:phantom."), STRIDE, model
        )
        assert [m.mention for m in joined.marks.unresolved_mentions] == [
            "process:ghost",
            "store:phantom",
        ]


class TestMissingMitigations:
    """Empty is licensed for one reason, and the grounds say whether it holds."""

    def drafted(self, *, mitigations, grounds=None):
        fields = {"mitigations": mitigations}
        if grounds is not None:
            fields["grounds"] = grounds
        return {"spoofing": [sample_draft("S-01", **fields)]}

    def test_a_threat_carrying_a_countermeasure_is_not_marked(self, model):
        joined = join_drafts(self.drafted(mitigations=[mitigation()]), STRIDE, model)
        assert joined.marks.missing_mitigations == []

    def test_empty_with_no_unknown_behind_it_is_marked(self, model):
        joined = join_drafts(self.drafted(mitigations=[]), STRIDE, model)
        assert [m.claim_id for m in joined.marks.missing_mitigations] == ["S-01"]

    def test_empty_on_a_threat_conditional_on_an_unknown_is_licensed(self, model):
        """The one case the prompt allows, recognized by the branch its trigger picks."""
        joined = join_drafts(
            self.drafted(
                mitigations=[],
                grounds=[
                    Ground(
                        kind="unknown-attribute",
                        element_id="store:orders-db",
                        attribute="encryption_at_rest",
                    )
                ],
            ),
            STRIDE,
            model,
        )
        assert joined.marks.missing_mitigations == []

    def test_the_mark_never_costs_the_finding(self, model):
        joined = join_drafts(self.drafted(mitigations=[]), STRIDE, model)
        assert [draft.id for draft in joined.drafts] == ["S-01"]


class TestNumberingGaps:
    """A lane's own drafts should run 01..N; a gap is reported, never repaired."""

    def test_a_contiguous_lane_reports_nothing(self):
        assert (
            DraftThreat.lane_diagnostics([sample_draft("S-01"), sample_draft("S-02")])
            == []
        )

    def test_a_gap_is_named_with_the_ids_that_produced_it(self):
        [gap] = DraftThreat.lane_diagnostics(
            [sample_draft("S-01"), sample_draft("S-02"), sample_draft("S-05")]
        )
        assert "spoofing" in gap
        assert "S-01, S-02, S-05" in gap
        assert "01..03" in gap

    def test_a_lane_not_starting_at_01_is_a_gap(self):
        assert DraftThreat.lane_diagnostics(
            [sample_draft("S-02"), sample_draft("S-03")]
        )

    def test_lanes_are_numbered_independently(self):
        """Each agent numbers within its own category, so each is judged alone."""
        assert (
            DraftThreat.lane_diagnostics(
                [
                    sample_draft("S-01"),
                    sample_draft("T-01", "tampering"),
                    sample_draft("T-02", "tampering"),
                ]
            )
            == []
        )

    def test_only_the_offending_lane_is_named(self):
        gaps = DraftThreat.lane_diagnostics(
            [
                sample_draft("S-01"),
                sample_draft("T-01", "tampering"),
                sample_draft("T-03", "tampering"),
            ]
        )
        assert len(gaps) == 1
        assert "tampering" in gaps[0]

    def test_the_ids_are_left_exactly_as_the_agent_wrote_them(self, model):
        """Reported, never renumbered: an ID must not move between two runs."""
        drafts = {"spoofing": [sample_draft("S-01"), sample_draft("S-05")]}
        assert [d.id for d in join_drafts(drafts, STRIDE, model).drafts] == [
            "S-01",
            "S-05",
        ]


class TestQuoteVerification:
    """Marked per entry, failed closed per threat."""

    def quoting(self, text, threat_id="S-01", extra=()):
        grounds = [Ground(kind="quote", text=text, source_label=LABEL), *extra]
        return {"spoofing": [sample_draft(threat_id, grounds=grounds)]}

    def test_a_verifying_quote_is_not_marked(self, model):
        joined = join_drafts(
            self.quoting("log in to the web app"), STRIDE, model, SOURCES
        )
        assert joined.marks.unverified_grounds == []

    def test_an_unfindable_quote_beside_a_good_ground_is_marked_not_fatal(self, model):
        """One bad quote beside good ones is still a justified finding."""
        drafts = self.quoting(
            "a sentence the submitter never wrote",
            extra=[Ground(kind="derived-fact", flow_id=CROSSING)],
        )
        joined = join_drafts(drafts, STRIDE, model, SOURCES)

        assert len(joined.drafts) == 1
        assert [(m.claim_id, m.index) for m in joined.marks.unverified_grounds] == [
            ("S-01", 0)
        ]
        assert LABEL in joined.marks.unverified_grounds[0].reason

    def test_a_body_of_adversarial_quotes_stops_at_the_body_deadline(
        self, model, monkeypatch
    ):
        """Tested against the rung, not against its own expectation: each of
        these quotes alone runs to the per-scan deadline, so the body's cost is
        the count times that unless one deadline covers them all. Three of
        them under a half-second body bound must finish in about that, where
        the per-scan bound alone would let them run for three scans.
        """
        from analysis_service import grounding

        monkeypatch.setattr(grounding, "MAX_REPAIR_SECONDS_PER_BODY", 0.5)
        quote = " ".join(["ab"] * 133)
        source = " ".join(["ba"] * 147)
        drafts = {
            "spoofing": [
                sample_draft(
                    f"S-0{i}",
                    grounds=[Ground(kind="quote", text=quote, source_label=LABEL)],
                )
                for i in range(1, 4)
            ]
        }
        started = time.thread_time()

        joined = join_drafts(drafts, STRIDE, model, {LABEL: source})

        assert time.thread_time() - started < 1.5
        assert len(joined.marks.dropped_claims) == 3

    def test_a_refused_quote_near_the_source_is_repaired_and_marked(self, model):
        """The ground now carries the submitter's words, and the mark carries
        the agent's, so the substitution is on the record. A repaired quote
        verifies, so it is never also marked unverified, and a claim resting on
        it alone is not groundless."""
        drafts = self.quoting("Customers log in to the web app which stores orders")

        joined = join_drafts(drafts, STRIDE, model, SOURCES)

        (draft,) = joined.drafts
        assert draft.grounds[0].text == SOURCES[LABEL]
        (mark,) = joined.marks.repaired_quotes
        assert (mark.claim_id, mark.index) == ("S-01", 0)
        assert mark.written == "Customers log in to the web app which stores orders"
        assert 0.9 <= mark.similarity <= 1.0
        assert joined.marks.unverified_grounds == []
        assert joined.marks.dropped_claims == []

    def test_a_claim_whose_every_ground_fails_is_dropped_and_marked(self, model):
        """The claim, not the job: one misquote on a claim that carries nothing
        else must not discard every other lane's work. The mark keeps the
        title and the quote the ladder could not find, because nothing else
        persists the draft and the next such drop would otherwise be a guess.
        """
        drafts = self.quoting("a sentence the submitter never wrote")
        drafts["tampering"] = [sample_draft("T-01", "tampering")]

        joined = join_drafts(drafts, STRIDE, model, SOURCES)

        assert [draft.id for draft in joined.drafts] == ["T-01"]
        (mark,) = joined.marks.dropped_claims
        assert mark.claim_id == "S-01"
        assert mark.title == drafts["spoofing"][0].title
        assert "a sentence the submitter never wrote" in mark.reason
        assert LABEL in mark.reason
        assert joined.marks.unverified_grounds == []

    def test_an_unresolvable_label_on_a_lone_quote_drops_the_claim(self, model):
        """A draft citing a source that does not exist and a draft quoting one
        that does wrongly are different faults, and the mark's reason tells
        them apart — but both leave the claim with nothing that verifies."""
        drafts = self.quoting("log in to the web app")
        drafts["spoofing"][0].grounds[0].source_label = "no-such-source"

        joined = join_drafts(drafts, STRIDE, model, SOURCES)

        assert joined.drafts == []
        (mark,) = joined.marks.dropped_claims
        assert "no-such-source" in mark.reason

    def test_the_text_check_does_not_run_without_sources(self, model):
        joined = join_drafts(self.quoting("never written anywhere"), STRIDE, model)
        assert joined.marks.unverified_grounds == []


class TestAssembleThreats:
    def test_confirmed_and_needs_info_stay_together(self, model):
        drafts = [sample_draft("S-01"), sample_draft("S-02")]
        rulings = [
            sample_ruling("S-01"),
            sample_ruling(
                "S-02",
                verdict=Verdict(
                    status="needs-info",
                    reason="encryption at rest is unknown",
                    related_unknowns=[
                        UnknownRef(
                            element_id="store:orders-db", attribute="encryption_at_rest"
                        )
                    ],
                ),
            ),
        ]
        threats, rejected = assemble_claims(drafts, rulings, model, SCHEMAS)
        assert [t.id for t in threats] == ["S-01", "S-02"]
        assert rejected == []

    def test_rejected_threats_ride_in_the_audit_array(self, model):
        drafts = [sample_draft("S-01"), sample_draft("S-02")]
        rulings = [
            sample_ruling("S-01"),
            sample_ruling(
                "S-02",
                verdict=Verdict(
                    status="rejected",
                    reason="duplicate of S-01",
                    rejected_because="duplicate",
                ),
            ),
        ]
        threats, rejected = assemble_claims(drafts, rulings, model, SCHEMAS)
        assert [t.id for t in threats] == ["S-01"]
        assert [t.id for t in rejected] == ["S-02"]

    def test_actionable_threats_are_sorted_most_severe_first(self, model):
        drafts = [
            sample_draft("S-01", severity=severity("low", "low")),
            sample_draft("S-02", severity=severity("high", "high")),
            sample_draft("S-03", severity=severity("medium", "high")),
        ]
        rulings = [sample_ruling(f"S-0{n}") for n in (1, 2, 3)]
        threats, _ = assemble_claims(drafts, rulings, model, SCHEMAS)
        assert [t.id for t in threats] == ["S-02", "S-03", "S-01"]

    def test_ties_break_on_threat_id(self, model):
        drafts = [sample_draft("S-02"), sample_draft("S-01")]
        rulings = [sample_ruling("S-02"), sample_ruling("S-01")]
        threats, _ = assemble_claims(drafts, rulings, model, SCHEMAS)
        assert [t.id for t in threats] == ["S-01", "S-02"]

    def test_a_dropped_draft_fails_closed(self, model):
        drafts = [sample_draft("S-01"), sample_draft("S-02")]
        with pytest.raises(CriticOutputError, match="dropped draft 'S-02'"):
            assemble_claims(drafts, [sample_ruling("S-01")], model, SCHEMAS)

    def test_an_invented_threat_fails_closed(self, model):
        with pytest.raises(CriticOutputError, match="no lane agent drafted"):
            assemble_claims(
                [sample_draft("S-01")], [sample_ruling("S-02")], model, SCHEMAS
            )

    def test_a_duplicated_ruling_fails_closed(self, model):
        drafts = [sample_draft("S-01")]
        rulings = [sample_ruling("S-01"), sample_ruling("S-01")]
        with pytest.raises(CriticOutputError, match="used by 2 drafts"):
            assemble_claims(drafts, rulings, model, SCHEMAS)

    def test_needs_info_unknowns_must_resolve(self, model):
        drafts = [sample_draft("S-01")]
        rulings = [
            sample_ruling(
                "S-01",
                verdict=Verdict(
                    status="needs-info",
                    reason="unverified control",
                    related_unknowns=[
                        UnknownRef(element_id="store:ghost", attribute="encryption")
                    ],
                ),
            )
        ]
        with pytest.raises(CriticOutputError, match="hangs its needs-info verdict"):
            assemble_claims(drafts, rulings, model, SCHEMAS)

    def test_a_question_with_no_place_in_the_model_resolves(self, model):
        """The second spelling passes the seam with no element to point at.

        A framework ruling on requirements asks most of its questions about a
        codebase rather than about an element, and the system model holds no
        field for one. Before this spelling existed, the only legal answer was
        to point at whichever attribute resolved on whichever element was
        nearest — an entry that passes the check and tells a reader nothing.
        """
        drafts = [sample_draft("S-01")]
        rulings = [
            sample_ruling(
                "S-01",
                verdict=Verdict(
                    status="needs-info",
                    reason="the input does not say whether queries are parameterized",
                    related_unknowns=[
                        UnknownRef(subject="are database queries parameterized")
                    ],
                ),
            )
        ]

        assembled = assemble_claims(drafts, rulings, model, SCHEMAS)

        assert assembled.claims[0].verdict.related_unknowns[0].subject

    def test_an_entry_naming_neither_an_element_nor_a_subject_is_a_fault(self, model):
        """Saying nothing is still a fault. The rule is what must be answered."""
        drafts = [sample_draft("S-01")]
        rulings = [
            sample_ruling(
                "S-01",
                verdict=Verdict(
                    status="needs-info",
                    reason="unsettled",
                    related_unknowns=[UnknownRef(subject="   ")],
                ),
            )
        ]

        with pytest.raises(CriticOutputError, match="nothing says what has to be"):
            assemble_claims(drafts, rulings, model, SCHEMAS)

    def test_a_bad_attribute_names_the_ones_the_element_does_have(self, model):
        """The re-ask is told what is available, not only what is wrong.

        An attribute is a fixed field per element *type*, so a critic reaching
        for one that exists on another type has named a real attribute in the
        wrong place. Without the available set it repoints at whatever resolves
        everywhere, which is how a question becomes an answerless pointer.
        """
        drafts = [sample_draft("S-01")]
        rulings = [
            sample_ruling(
                "S-01",
                verdict=Verdict(
                    status="needs-info",
                    reason="unsettled",
                    related_unknowns=[
                        UnknownRef(element_id="store:orders-db", attribute="exposure")
                    ],
                ),
            )
        ]

        with pytest.raises(CriticOutputError) as raised:
            assemble_claims(drafts, rulings, model, SCHEMAS)

        assert "That element has:" in str(raised.value)
        assert "encryption_at_rest" in str(raised.value)
        assert "`subject`" in str(raised.value)

    def test_a_needs_info_attribute_the_element_lacks_is_a_fault(self, model):
        """Checked to the same depth as the grounds surface, and no deeper.

        A ``store:orders-db`` has no ``exposure``, so this is a hallucinated
        attribute rather than a debatable one. It routes to the bounded re-ask
        rather than killing the job, which is the whole reason its twin on the
        grounds side stops at existence too.
        """
        drafts = [sample_draft("S-01")]
        rulings = [
            sample_ruling(
                "S-01",
                verdict=Verdict(
                    status="needs-info",
                    reason="unverified control",
                    related_unknowns=[
                        UnknownRef(element_id="store:orders-db", attribute="exposure")
                    ],
                ),
            )
        ]
        with pytest.raises(CriticOutputError, match="does not have"):
            assemble_claims(drafts, rulings, model, SCHEMAS)

    def test_empty_analysis_assembles_to_empty_arrays(self, model):
        assert assemble_claims([], [], model, SCHEMAS) == ([], [])


class TestVerdictShapeIsReAskableRatherThanFatal:
    """The three rules a ``Verdict``'s own status implies, checked at this seam.

    Each one arrives as a :class:`ProposedVerdict`, which is what the critic
    node emits and what its ``output_schema`` therefore accepts without
    raising. That is the point: enforcing these in the schema means enforcing
    them at the node boundary, where a raise takes the critic's single pass
    over every draft in the job with it, and the bounded re-ask built for
    exactly this class of problem never runs.
    """

    @pytest.fixture
    def model(self):
        return valid_model()

    def _rulings(self, **verdict):
        return [sample_ruling("S-01", verdict=ProposedVerdict(**verdict))]

    def test_a_needs_info_naming_no_unknown_is_reported(self, model):
        problems = review_issues(
            [sample_draft("S-01")],
            self._rulings(status="needs-info", reason="unclear"),
            model,
        )

        assert "names no unknown attribute" in "; ".join(problems.messages)

    def test_unknowns_on_a_verdict_that_is_not_needs_info_are_reported(self, model):
        problems = review_issues(
            [sample_draft("S-01")],
            self._rulings(
                status="confirmed",
                related_unknowns=[
                    UnknownRef(element_id="store:orders-db", attribute="technology")
                ],
            ),
            model,
        )

        assert "only meaningful on a needs-info verdict" in "; ".join(problems.messages)

    def test_a_rejection_without_a_reason_is_reported(self, model):
        problems = review_issues(
            [sample_draft("S-01")], self._rulings(status="rejected"), model
        )

        assert "states no reason" in "; ".join(problems.messages)

    def test_the_threat_is_implicated_so_the_re_ask_can_read_it(self, model):
        """Neither naming the unknown nor writing the reason can be done from an
        ID — both are claims about a specific threat."""
        problems = review_issues(
            [sample_draft("S-01")], self._rulings(status="rejected"), model
        )

        assert problems.implicated == frozenset({"S-01"})

    def test_two_independent_faults_on_one_ruling_are_two_messages(self, model):
        """A merged message would leave the second to be found on a pass that
        no longer exists."""
        problems = review_issues(
            [sample_draft("S-01")],
            self._rulings(
                status="rejected",
                rejected_because="evidence",
                related_unknowns=[
                    UnknownRef(element_id="store:orders-db", attribute="technology")
                ],
            ),
            model,
        )

        assert len(problems.messages) == 2

    def test_a_rejection_naming_no_step_is_reported(self, model):
        """The rejected array is an audit trail, and the step is a field."""
        problems = review_issues(
            [sample_draft("S-01")],
            self._rulings(status="rejected", reason="ungrounded"),
            model,
        )

        assert "names no check in rejected_because" in "; ".join(problems.messages)

    def test_a_step_on_a_verdict_that_is_not_rejected_is_reported(self, model):
        problems = review_issues(
            [sample_draft("S-01")],
            self._rulings(status="confirmed", rejected_because="lane"),
            model,
        )

        assert "only meaningful on a rejected verdict" in "; ".join(problems.messages)

    def test_a_malformed_threat_id_reads_as_a_drop_and_an_invention(self, model):
        """Both halves of the same typo, each nameable by the re-ask.

        The pattern this replaces said only "that is not an ID". These two say
        which draft went unruled and which ID nobody drafted, which is what a
        re-ask needs to put it right.
        """
        ruling = sample_ruling("S-01").model_copy(update={"id": "S-1"})

        problems = review_issues([sample_draft("S-01")], [ruling], model)

        assert "dropped draft 'S-01'" in "; ".join(problems.messages)
        assert "'S-1', which no lane agent drafted" in "; ".join(problems.messages)
        assert problems.implicated == frozenset({"S-01"})

    def test_assembly_still_fails_closed_on_one(self, model):
        """Re-askable is not ignorable: nothing reaches the report on rulings
        the seam refused."""
        with pytest.raises(CriticOutputError, match="states no reason"):
            assemble_claims(
                [sample_draft("S-01")],
                self._rulings(status="rejected"),
                model,
                SCHEMAS,
            )

    def test_a_passing_ruling_is_promoted_to_the_reports_own_verdict(self, model):
        """``ProposedVerdict`` in, ``Verdict`` out — so a threat on the report
        carries the shape the report defines, whatever the critic emitted."""
        threats, _ = assemble_claims(
            [sample_draft("S-01")], self._rulings(status="confirmed"), model, SCHEMAS
        )

        assert type(threats[0].verdict) is Verdict


class TestRulingsMergeOntoDrafts:
    """A ruling supplies judgement; every other field comes from the draft."""

    def test_the_agents_own_fields_survive_the_critic_untouched(self, model):
        draft = sample_draft(
            "S-01",
            title="Session cookie theft",
            description="Stolen cookies let an attacker impersonate the customer.",
            affected_element_ids=["flow:customer-to-web-app:login"],
        )
        (threat,), _ = assemble_claims([draft], [sample_ruling("S-01")], model, SCHEMAS)
        assert threat.title == draft.title
        assert threat.description == draft.description
        assert threat.affected_element_ids == draft.affected_element_ids
        assert threat.mitigations == draft.mitigations

    def test_a_ruling_without_severity_keeps_the_agents_rating(self, model):
        draft = sample_draft("S-01", severity=severity("low", "medium"))
        (threat,), _ = assemble_claims([draft], [sample_ruling("S-01")], model, SCHEMAS)
        assert threat.severity == draft.severity
        assert threat.severity.level == "low"

    def test_a_ruling_with_severity_replaces_the_rating_and_its_justification(
        self, model
    ):
        draft = sample_draft("S-01", severity=severity("low", "low"))
        corrected = Severity(
            likelihood="high",
            impact="high",
            justification="The model states the flow is unauthenticated.",
        )
        rulings = [sample_ruling("S-01", severity=corrected)]
        (threat,), _ = assemble_claims([draft], rulings, model, SCHEMAS)
        assert threat.severity.likelihood == "high"
        assert threat.severity.justification == corrected.justification
        assert threat.severity.level == "critical"

    def test_the_critics_judgements_reach_the_threat(self, model):
        rulings = [sample_ruling("S-01", confidence="medium")]
        (threat,), _ = assemble_claims([sample_draft("S-01")], rulings, model, SCHEMAS)
        assert threat.confidence == "medium"
        assert threat.verdict.status == "confirmed"

    def test_threats_are_built_in_draft_order_not_ruling_order(self, model):
        drafts = [sample_draft("S-01"), sample_draft("S-02")]
        rulings = [
            sample_ruling(
                "S-02",
                verdict=Verdict(
                    status="rejected", reason="dup", rejected_because="duplicate"
                ),
            ),
            sample_ruling(
                "S-01",
                verdict=Verdict(
                    status="rejected", reason="dup", rejected_because="duplicate"
                ),
            ),
        ]
        _, rejected = assemble_claims(drafts, rulings, model, SCHEMAS)
        assert [t.id for t in rejected] == ["S-01", "S-02"]


ELEMENT_IDS = frozenset(
    {"entity:customer", "process:web-app", "store:orders-db", "flow:a-to-b:x"}
)


class TestSnapDrafts:
    def test_affected_elements_arrive_in_the_jobs_spelling(self):
        draft = sample_draft(affected_element_ids=["Process:Web-App"])
        [snapped] = snap_drafts([draft], ELEMENT_IDS)
        assert snapped.affected_element_ids == ["process:web-app"]

    def test_an_unresolvable_reference_is_left_as_written(self):
        """The check that reports it can only name what the agent typed."""
        draft = sample_draft(affected_element_ids=["process:ghost"])
        [snapped] = snap_drafts([draft], ELEMENT_IDS)
        assert snapped.affected_element_ids == ["process:ghost"]

    @pytest.mark.parametrize(
        "ground, field, expected",
        [
            (
                Ground(
                    kind="unknown-attribute",
                    element_id="Process:Web-App",
                    attribute="exposure",
                ),
                "element_id",
                "process:web-app",
            ),
            (
                Ground(
                    kind="absent-attribute",
                    element_id="Process:Web-App",
                    attribute="exposure",
                ),
                "element_id",
                "process:web-app",
            ),
            (
                Ground(kind="derived-fact", flow_id="Flow:A-to-B:X"),
                "flow_id",
                "flow:a-to-b:x",
            ),
        ],
    )
    def test_each_ground_branch_snaps_its_own_reference(self, ground, field, expected):
        draft = sample_draft(grounds=[ground])
        [snapped] = snap_drafts([draft], ELEMENT_IDS)
        assert getattr(snapped.grounds[0], field) == expected

    def test_a_quote_label_snaps_to_the_jobs_label(self):
        draft = sample_draft(
            grounds=[
                Ground(
                    kind="quote", text="anything", source_label="system  DESCRIPTION"
                )
            ]
        )
        [snapped] = snap_drafts([draft], ELEMENT_IDS, {"System description"})
        assert snapped.grounds[0].source_label == "System description"

    def test_without_labels_a_quote_is_untouched(self):
        """The in-process engine carries no sources, so there is nothing to snap."""
        draft = sample_draft(
            grounds=[Ground(kind="quote", text="anything", source_label="Whatever")]
        )
        [snapped] = snap_drafts([draft], ELEMENT_IDS)
        assert snapped.grounds[0].source_label == "Whatever"

    def test_nothing_but_references_changes(self):
        draft = sample_draft(affected_element_ids=["Process:Web-App"])
        [snapped] = snap_drafts([draft], ELEMENT_IDS)
        assert snapped.model_dump(exclude={"affected_element_ids"}) == draft.model_dump(
            exclude={"affected_element_ids"}
        )


class TestSnapRulings:
    def test_a_needs_info_element_snaps(self):
        ruling = sample_ruling(
            verdict=Verdict(
                status="needs-info",
                reason="exposure unknown",
                related_unknowns=[
                    UnknownRef(element_id="Process:Web-App", attribute="exposure")
                ],
            )
        )
        [snapped] = snap_rulings([ruling], ELEMENT_IDS)
        assert snapped.verdict.related_unknowns[0].element_id == "process:web-app"

    def test_a_ruling_carrying_no_unknowns_is_unchanged(self):
        ruling = sample_ruling()
        [snapped] = snap_rulings([ruling], ELEMENT_IDS)
        assert snapped == ruling


class TestAFreeStringFromAnAgentCannotOverrunAMark:
    """A mark's fields are bounded and the agent text they quote is not.

    Both writers below read a list or a description the model filled, so
    neither has a length of its own. A raise here lands in the merge node,
    where it costs the run every lane that already answered.
    """

    def test_a_blank_element_id_is_skipped_rather_than_marked(self):
        model = valid_model()
        draft = sample_draft("S-01", affected_element_ids=["store:orders-db", ""])

        joined = join_drafts({"spoofing": [draft]}, STRIDE, model)

        assert joined.marks.unresolved_references == []

    def test_a_long_mention_is_cut_to_the_marks_bound(self):
        model = valid_model()
        draft = sample_draft(
            "S-01",
            description=(
                "The threat reaches store:" + "a" * 400 + " and reads it there."
            ),
        )

        joined = join_drafts({"spoofing": [draft]}, STRIDE, model)

        (mark,) = joined.marks.unresolved_mentions
        assert len(mark.mention) == MENTION_MAX_CHARS


class TestAnUnknownGroundSettlesTheVerdict:
    """#439: a draft citing an ``unknown-attribute`` ground is conditional by
    its own evidence, so the verdict is decided in code and the critic only
    chooses between ``needs-info`` and ``rejected``."""

    @pytest.fixture
    def model(self):
        return valid_model()

    def _draft(self):
        return sample_draft(
            "S-01",
            grounds=[
                Ground(
                    kind="unknown-attribute",
                    element_id="store:orders-db",
                    attribute="encryption_at_rest",
                )
            ],
        )

    def test_a_confirmation_is_reported_for_the_re_ask(self, model):
        problems = review_issues([self._draft()], [sample_ruling("S-01")], model)

        assert "cannot be confirmed" in "; ".join(problems.messages)
        assert problems.implicated == frozenset({"S-01"})

    def test_a_draft_the_critic_never_saw_is_ruled_from_the_grounds(self, model):
        from analysis_service.critic import unsettled_drafts

        other = sample_draft("S-02")
        assert unsettled_drafts([self._draft(), other]) == [other]
        assert not review_issues([self._draft()], [], model)
        assembled = assemble_claims([self._draft()], [], model, SCHEMAS)
        (claim,) = assembled.claims
        assert (claim.verdict.status, claim.confidence) == ("needs-info", "low")
        assert claim.verdict.related_unknowns == [
            UnknownRef(element_id="store:orders-db", attribute="encryption_at_rest")
        ]

    def test_a_bare_needs_info_is_completed_from_the_grounds(self, model):
        ruling = sample_ruling(
            "S-01", confidence="low", verdict=ProposedVerdict(status="needs-info")
        )

        assert not review_issues([self._draft()], [ruling], model)
        assembled = assemble_claims([self._draft()], [ruling], model, SCHEMAS)
        verdict = assembled.claims[0].verdict
        assert verdict.related_unknowns == [
            UnknownRef(element_id="store:orders-db", attribute="encryption_at_rest")
        ]
        assert "encryption_at_rest" in verdict.reason

    def test_many_unknowns_are_counted_rather_than_named(self):
        """The composed reason has a maximum length, and the pairs do not.

        A draft can rest on more unknowns than the field holds a sentence for.
        The reason names the count instead of the pairs, and
        ``related_unknowns`` still carries every pair, so nothing is lost.
        """
        draft = sample_draft(
            "S-01",
            grounds=[
                Ground(
                    kind="unknown-attribute",
                    element_id=f"store:orders-db-{index:03d}",
                    attribute="protection_of_data_at_rest",
                )
                for index in range(40)
            ],
        )

        ruling = type(draft).settled_by_grounds(draft)

        assert len(ruling.verdict.reason) <= REASON_MAX_CHARS
        assert "40 attributes" in ruling.verdict.reason
        assert len(ruling.verdict.related_unknowns) == 40

    def test_the_critics_own_unknowns_and_reason_are_kept(self, model):
        ruling = sample_ruling(
            "S-01",
            confidence="low",
            verdict=ProposedVerdict(
                status="needs-info",
                reason="Also depends on who can reach the store.",
                related_unknowns=[
                    UnknownRef(element_id="store:orders-db", attribute="technology")
                ],
            ),
        )

        assembled = assemble_claims([self._draft()], [ruling], model, SCHEMAS)
        verdict = assembled.claims[0].verdict
        assert verdict.reason == "Also depends on who can reach the store."
        assert [ref.attribute for ref in verdict.related_unknowns] == [
            "technology",
            "encryption_at_rest",
        ]

    def test_a_rejection_still_stands(self, model):
        ruling = sample_ruling(
            "S-01",
            verdict=ProposedVerdict(
                status="rejected",
                reason="filed in the wrong lane",
                rejected_because="lane",
            ),
        )

        assert not review_issues([self._draft()], [ruling], model)

    def test_a_draft_with_no_unknown_ground_is_untouched(self, model):
        assert not review_issues([sample_draft("S-01")], [sample_ruling("S-01")], model)


class TestDuplicateGroups:
    """#440: one action at one place is a comparison of two fields, made in code."""

    @pytest.fixture
    def model(self):
        return valid_model()

    def test_one_verb_at_one_place_is_marked_across_lanes(self, model):
        from analysis_service.critic import duplicate_groups

        drafts = [
            sample_draft("S-01", verb="forge", affected_element_ids=[CROSSING]),
            sample_draft(
                "T-01",
                "tampering",
                verb="forge",
                affected_element_ids=["entity:customer", "process:web-app"],
            ),
            sample_draft("S-02", verb="replay", affected_element_ids=[CROSSING]),
        ]

        assert duplicate_groups(drafts, model) == {"S-01": ["T-01"], "T-01": ["S-01"]}

    def test_a_draft_with_no_verb_is_never_compared(self, model):
        from analysis_service.critic import duplicate_groups
        from analysis_service.frameworks.asvs.record import DraftRequirementRuling

        ruling = DraftRequirementRuling.model_validate(
            {
                **sample_draft("S-01").model_dump(
                    exclude={"category", "verb", "severity", "mitigations"}
                ),
                "id": "v5.0.0-6.2.1",
                "chapter": "authentication",
            }
        )

        assert duplicate_groups([ruling, ruling], model) == {}


class TestTheGroundsBoundTheCitedElements:
    """#441: ``affected_element_ids`` reaches one hop from the grounds' places."""

    @pytest.fixture
    def model(self):
        return valid_model()

    def test_an_element_two_hops_away_is_dropped_and_marked(self, model):
        from analysis_service.report import BEYOND_GROUNDS

        drafts = {
            "spoofing": [
                sample_draft(affected_element_ids=[CROSSING, "store:orders-db"])
            ]
        }

        joined = join_drafts(drafts, STRIDE, model)

        (draft,) = joined.drafts
        assert draft.affected_element_ids == [CROSSING]
        (mark,) = joined.marks.unresolved_references
        assert (mark.element_id, mark.reason) == ("store:orders-db", BEYOND_GROUNDS)

    def test_a_claim_on_quotes_alone_is_bounded_by_what_its_prose_cites(self, model):
        from analysis_service.report import BEYOND_GROUNDS

        drafts = {
            "spoofing": [
                sample_draft(
                    grounds=[
                        Ground(
                            kind="quote",
                            text="log in to the web app",
                            source_label=LABEL,
                        )
                    ],
                    description=f"A stolen cookie rides {CROSSING} as the customer.",
                    affected_element_ids=[CROSSING, "entity:customer"],
                )
            ]
        }

        joined = join_drafts(drafts, STRIDE, model)

        (draft,) = joined.drafts
        assert draft.affected_element_ids == [CROSSING]
        (mark,) = joined.marks.unresolved_references
        assert (mark.element_id, mark.reason) == ("entity:customer", BEYOND_GROUNDS)

    def test_the_endpoints_of_a_cited_flow_are_in_reach(self, model):
        drafts = {
            "spoofing": [
                sample_draft(
                    affected_element_ids=[
                        CROSSING,
                        "entity:customer",
                        "process:web-app",
                    ]
                )
            ]
        }

        joined = join_drafts(drafts, STRIDE, model)

        assert joined.marks.unresolved_references == []

    def test_a_claim_on_quotes_alone_is_bounded_by_its_own_prose(self, model):
        quote = Ground(
            kind="quote", text="Customers log in to the web app", source_label=LABEL
        )
        drafts = {
            "spoofing": [
                sample_draft(
                    grounds=[quote],
                    description="An attacker rides `entity:customer`'s session.",
                    affected_element_ids=["entity:customer", "store:orders-db"],
                )
            ]
        }

        joined = join_drafts(drafts, STRIDE, model)

        (draft,) = joined.drafts
        assert draft.affected_element_ids == ["entity:customer"]

    def test_a_claim_whose_grounds_reach_none_of_its_elements_is_dropped(self, model):
        drafts = {"spoofing": [sample_draft(affected_element_ids=["store:orders-db"])]}

        joined = join_drafts(drafts, STRIDE, model)

        assert joined.drafts == []
        (dropped,) = joined.marks.dropped_claims
        assert "do not reach" in dropped.reason


class TestAMisfiledVerbIsRejectedInCode:
    """#442: the lane a verb belongs to is a table, so the ruling is the table's."""

    @pytest.fixture
    def model(self):
        return valid_model()

    def test_a_confirmation_becomes_a_rejection_naming_the_lane(self, model):
        draft = sample_draft("S-01", verb="flood")

        assembled = assemble_claims([draft], [sample_ruling("S-01")], model, SCHEMAS)

        assert assembled.claims == []
        (rejected,) = assembled.rejected_claims
        assert rejected.verdict.status == "rejected"
        assert "denial-of-service" in rejected.verdict.reason


class TestRatingDisagreements:
    """#444: one fact pattern with two ratings is a comparison of four fields."""

    def test_two_drafts_with_one_pattern_and_two_ratings_name_each_other(self):
        from analysis_service.critic import rating_disagreements

        drafts = [
            sample_draft("S-01", severity=severity("medium", "high")),
            sample_draft("S-02", severity=severity("high", "high")),
            sample_draft("S-03", verb="replay", severity=severity("low", "low")),
        ]

        assert rating_disagreements(drafts) == {"S-01": ["S-02"], "S-02": ["S-01"]}

    def test_agreeing_ratings_are_not_named(self):
        from analysis_service.critic import rating_disagreements

        drafts = [sample_draft("S-01"), sample_draft("S-02")]

        assert rating_disagreements(drafts) == {}


class TestAnAbsenceRidesTheCriticPath:
    """#412: the fifth branch names no element, and four seams assumed one did.

    Each of them read "the place this ground is about" as an element ID or a
    flow ID, so a ground that is about the whole model reached them carrying
    neither. The suite could not see it: nothing drove this branch past
    resolution.
    """

    @pytest.fixture
    def draft(self):
        return sample_draft(
            "S-01", grounds=[Ground(kind="absent-element", term="ldap")]
        )

    def test_it_survives_the_join(self, draft):
        model = valid_model()

        joined = join_drafts({"spoofing": [draft]}, STRIDE, model)

        assert [claim.id for claim in joined.drafts] == ["S-01"]
        assert joined.drafts[0].grounds[0].term == "ldap"

    def test_snapping_leaves_it_alone(self, draft):
        """There is no ID to canonicalise: a term is a word, not a reference."""
        [snapped] = snap_drafts([draft], ELEMENT_IDS)

        assert snapped.grounds == [Ground(kind="absent-element", term="ldap")]

    def test_it_reaches_the_report(self, draft):
        model = valid_model()

        assembled = assemble_claims([draft], [sample_ruling("S-01")], model, SCHEMAS)

        (claim,) = assembled.claims
        assert claim.grounds == [Ground(kind="absent-element", term="ldap")]

    def test_it_names_no_place(self):
        """The property four seams now read, rather than four ``or`` chains."""
        assert Ground(kind="absent-element", term="ldap").place == ""
        assert Ground(kind="quote", text="t", source_label="s").place == ""
        assert (
            Ground(kind="unknown-attribute", element_id="e", attribute="a").place == "e"
        )
        assert Ground(kind="derived-fact", flow_id="f").place == "f"


def test_ruling_view_keeps_every_field_the_critic_rules_on():
    """The guard on ``_ruling_view``'s ``exclude_defaults``.

    Narrowing the prompt view is only free while nothing a verdict is reached
    from can fall through it. A ``DraftThreat`` field added with a default
    would vanish here whenever it held that default — so this names the fields
    the five steps read and fails the moment one stops arriving.
    """
    draft = sample_draft("S-01", "spoofing")

    (view,) = critic._ruling_view([draft])

    for field in ("id", "category", "description", "affected_element_ids"):
        assert field in view, f"the critic rules on {field!r}"
    assert view["severity"]["likelihood"] and view["severity"]["justification"]
    # Step 5 reads grounds for relevance, so each entry keeps its own branch.
    assert [ground["kind"] for ground in view["grounds"]] == ["quote", "derived-fact"]
    assert view["grounds"][0]["text"] == "Customers log in to the web app"
    assert view["grounds"][1]["flow_id"] == "flow:customer-to-web-app:login"


def test_ruling_view_drops_what_no_verdict_is_reached_from():
    """Mitigations and a Ground's empty branches, gone from the prompt only."""
    draft = sample_draft("S-01", "spoofing")
    assert draft.mitigations, "the fixture must carry one for this to prove anything"

    (view,) = critic._ruling_view([draft])

    assert "mitigations" not in view
    # A quote carries text and source_label; the other four fields are the
    # empty string its own validator requires them to be.
    assert set(view["grounds"][0]) == {"kind", "text", "source_label"}
    assert set(view["grounds"][1]) == {"kind", "flow_id"}


def test_ruling_view_names_the_drafts_that_share_an_action():
    """#440: the critic reads a marked pair rather than hunting for one."""
    draft = sample_draft("S-01")

    (view,) = critic._ruling_view([draft], {"S-01": ["T-01"]})
    (bare,) = critic._ruling_view([draft])

    assert view["same_action_as"] == ["T-01"]
    assert "same_action_as" not in bare


def test_ruling_view_says_when_a_verb_belongs_to_another_lane():
    """#442: the critic reads a settled lane error rather than judging it."""
    (view,) = critic._ruling_view([sample_draft("S-01", verb="flood")])
    (clean,) = critic._ruling_view([sample_draft("S-01")])

    assert "denial-of-service" in view["filed_in_wrong_lane"]
    assert "filed_in_wrong_lane" not in clean


def test_ruling_view_names_the_drafts_rated_unlike():
    """#444: the critic reads the calibration pair rather than finding it."""
    (view,) = critic._ruling_view(
        [sample_draft("S-01")], rated_unlike={"S-01": ["S-02"]}
    )

    assert view["rated_unlike"] == ["S-02"]


class TestOneReviewCall:
    """The check and the re-ask view come out of one call, over one set.

    Before this, a graph node called four functions in the right order and
    composed the view itself. What that risked is the thing the pair exists to
    prevent: the messages naming one set of drafts and the view carrying
    another.
    """

    def test_a_pass_that_reconciles_is_accepted_with_its_count(self):
        drafts = [sample_draft("S-01")]
        rulings = [sample_ruling("S-01")]

        outcome = critic.review(drafts, rulings, valid_model())

        assert isinstance(outcome, critic.Accepted)
        assert outcome.count == 1

    def test_a_dropped_ruling_revises(self):
        drafts = [sample_draft("S-01"), sample_draft("T-01", category="tampering")]

        outcome = critic.review(drafts, [sample_ruling("S-01")], valid_model())

        assert isinstance(outcome, critic.Revision)
        assert outcome.messages

    def test_the_roster_is_every_drafted_id_and_the_view_is_not(self):
        """The re-ask reproduces rulings, not drafts.

        An ID carries the whole of a claim it need not read, so the roster is
        the covering set and only the drafts a structural fix cannot be made
        without reading travel in full.
        """
        drafts = [sample_draft("S-01"), sample_draft("T-01", category="tampering")]

        outcome = critic.review(drafts, [sample_ruling("S-01")], valid_model())

        assert outcome.roster == ["S-01", "T-01"]
        assert [draft["id"] for draft in outcome.unreconciled] == ["T-01"]

    def test_every_draft_the_view_carries_is_one_a_message_names(self):
        """The property the pair exists for, over a pass that drops both."""
        drafts = [sample_draft("S-01"), sample_draft("T-01", category="tampering")]

        outcome = critic.review(drafts, [], valid_model())

        shown = {draft["id"] for draft in outcome.unreconciled}
        assert shown
        for draft_id in shown:
            assert any(draft_id in message for message in outcome.messages), (
                f"{draft_id} travels in full but no message says why"
            )


class TestTheCriticView:
    def test_the_pairs_are_computed_over_every_shown_draft(self):
        """``only`` narrows what is rendered and nothing else.

        A duplicate is a relation between two drafts, so narrowing the set
        before pairing would leave a draft paired with nothing and read as
        unique — which is exactly the judgement the critic is being spared.
        """
        drafts = [sample_draft("S-01"), sample_draft("T-01", category="tampering")]
        model = valid_model()

        (narrowed,) = critic.critic_view(drafts, model, only={"S-01"})
        whole = critic.critic_view(drafts, model)

        assert narrowed["same_action_as"] == ["T-01"]
        assert narrowed == next(view for view in whole if view["id"] == "S-01")

    def test_narrowing_to_nothing_renders_nothing(self):
        drafts = [sample_draft("S-01")]

        assert critic.critic_view(drafts, valid_model(), only=set()) == []

    def test_both_critic_passes_read_one_view(self):
        """The fan-in and the re-ask reach the same function.

        A draft the first pass was shown and the re-ask renders differently
        would be a second opinion about what a claim is.
        """
        drafts = [sample_draft("S-01"), sample_draft("T-01", category="tampering")]
        model = valid_model()

        first_pass = critic.critic_view(drafts, model)
        re_ask = critic.critic_view(drafts, model, only={"S-01", "T-01"})

        assert first_pass == re_ask
