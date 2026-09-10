"""Tests for the fan-in: one framework's lane batches merged into its critic's drafts."""

import time

import pytest

from analysis_service import critic, fan_in
from analysis_service.claims import (
    MENTION_MAX_CHARS,
    Ground,
    Mitigation,
    Severity,
)
from analysis_service.fan_in import DraftJoinError, join_drafts, snap_drafts
from analysis_service.frameworks import schemas_for
from analysis_service.frameworks.stride import STRIDE
from analysis_service.frameworks.stride.record import DraftThreat
from analysis_service.sources import DEFAULT_DESCRIPTION_LABEL
from analysis_service.system_model import ModelIndex
from tests.factories import (
    sample_draft,
    sample_proposal,
    valid_model,
)

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


ELEMENT_IDS = frozenset(
    {"entity:customer", "process:web-app", "store:orders-db", "flow:a-to-b:x"}
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

    def _conditional(
        self,
        threat_id,
        title,
        element="store:orders-db",
        attribute="encryption_at_rest",
    ):
        """A draft an ``unknown`` ground settles, so no critic ever sees it."""
        return sample_draft(
            threat_id,
            "information-disclosure",
            title=title,
            verb="read",
            affected_element_ids=[element],
            grounds=[
                Ground(
                    kind="unknown-attribute",
                    element_id=element,
                    attribute=attribute,
                )
            ],
        )

    def test_two_conditional_drafts_at_one_place_become_one(self, model):
        """The duplicate no reader would otherwise make.

        A draft its grounds settle is ruled in code and never shown to a critic,
        and ``critic_view`` computes the duplicate pairs over the shown set — so
        both of these reached the report and nothing compared them.
        """
        drafts = {
            "information-disclosure": [
                self._conditional("D-01", "first"),
                self._conditional("D-02", "second"),
            ]
        }

        joined = join_drafts(drafts, STRIDE, model)

        assert [draft.title for draft in joined.drafts] == ["first"]
        (mark,) = joined.marks.dropped_claims
        assert (mark.claim_id, mark.title) == ("D-02", "second")
        assert "'D-01'" in mark.reason

    def test_two_conditional_drafts_at_two_places_both_survive(self, model):
        """The key is the action and the place, not the fact they are both open."""
        drafts = {
            "information-disclosure": [
                self._conditional("D-01", "the store"),
                self._conditional(
                    "D-02",
                    "the flow",
                    "flow:web-app-to-orders-db:store-order",
                    "encryption_in_transit",
                ),
            ]
        }

        joined = join_drafts(drafts, STRIDE, model)

        assert len(joined.drafts) == 2
        assert joined.marks.dropped_claims == []

    def test_a_reviewable_draft_is_left_to_the_critic(self, model):
        """Code drops only what no critic will read.

        A draft resting on stated facts is shown, so its duplicates are the
        critic's to rule on — and rejecting one there is a judgement about which
        description is better, which code has no business making.
        """
        drafts = {
            "information-disclosure": [
                sample_draft("D-01", "information-disclosure", title="first"),
                sample_draft("D-02", "information-disclosure", title="second"),
            ]
        }

        joined = join_drafts(drafts, STRIDE, model)

        assert [draft.title for draft in joined.drafts] == ["first", "second"]
        assert joined.marks.dropped_claims == []

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
    """Catalog membership at the fan-in, through the reader the report shares.

    Every failure here is fatal. There is no re-ask path for a category agent's
    drafts — ``repair`` is extraction-only and ``recritic`` is critic-only — so
    an unresolved reference kills the job. The rule is
    :func:`analysis_service.evidence.ground_issues`: a ground is one the
    catalog derived from this model holds, and the same reader runs over every
    report that loads.
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
        drafts = {
            "spoofing": [
                sample_draft(
                    "S-01",
                    affected_element_ids=["store:orders-db"],
                    grounds=[
                        Ground(
                            kind="unknown-attribute",
                            element_id="store:orders-db",
                            attribute="/encryption_at_rest",
                        )
                    ],
                )
            ]
        }
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

    def test_an_unknown_ground_on_an_attribute_the_model_states_is_refused(self, model):
        """The catalog decided the branch, so the branch is checked against it.

        ``exposure`` on the web app reads ``internet-facing``: no catalog entry
        exists for it, and a ground claiming it is unknown contradicts the
        model. At the fan-in this cannot happen, because the service built the
        ground from the catalog; the same reader runs over a loaded report,
        where nothing built it.
        """
        drafts = self.grounded(
            Ground(
                kind="unknown-attribute",
                element_id="process:web-app",
                attribute="exposure",
            )
        )
        with pytest.raises(DraftJoinError, match="reads as stated"):
            join_drafts(drafts, STRIDE, model, SOURCES)

    def test_an_absent_ground_on_an_unknown_control_is_refused(self, model):
        """The two attribute branches share their fields and not their state."""
        drafts = self.grounded(
            Ground(
                kind="absent-attribute",
                element_id="store:orders-db",
                attribute="encryption_at_rest",
            )
        )
        with pytest.raises(DraftJoinError, match="reads as unverified"):
            join_drafts(drafts, STRIDE, model, SOURCES)

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
        assert mark.moved == []
        assert mark.scan_complete is True
        assert joined.marks.unverified_grounds == []
        assert joined.marks.dropped_claims == []

    def test_a_repair_that_flips_a_negation_is_marked_as_such(self, model):
        """The span is substituted, as ADR 0018 decided, and the mark says
        the substitution put a negation back — the safeguard #675 D15 asked
        for, so a reader never takes a 0.95 similarity for support."""
        sources = {
            LABEL: "Customers log in to the web app, which does not store orders."
        }
        drafts = self.quoting(
            "Customers log in to the web app, which does store orders."
        )

        joined = join_drafts(drafts, STRIDE, model, sources)

        (draft,) = joined.drafts
        assert draft.grounds[0].text == sources[LABEL]
        (mark,) = joined.marks.repaired_quotes
        assert mark.moved == ["negation"]

    def test_the_critic_is_shown_what_a_repair_moved(self, model):
        drafts = self.quoting("Customers log in to the web app which stores orders")
        joined = join_drafts(drafts, STRIDE, model, SOURCES)

        (view,) = critic.critic_view(
            joined.drafts, model, repaired=joined.marks.repaired_quotes
        )

        assert view["repaired_quotes"] == [
            {
                "index": 0,
                "written": "Customers log in to the web app which stores orders",
                "moved": [],
            }
        ]
        (plain,) = critic.critic_view(joined.drafts, model)
        assert "repaired_quotes" not in plain

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


class TestTheGroundsBoundTheCitedElements:
    """#441: ``affected_element_ids`` reaches one hop from the grounds' places."""

    @pytest.fixture
    def model(self):
        return valid_model()

    def test_an_element_two_hops_away_is_dropped_and_marked(self, model):
        from analysis_service.claims import BEYOND_GROUNDS

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
        from analysis_service.claims import BEYOND_GROUNDS

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


class TestASpentBodyDeadlineFoldsNoSource:
    """The bound the fan-in actually runs under.

    ``repair_quote`` has its own guard, but the fan-in does not call it: it
    prepares each source itself and calls ``repair_prepared``. So the guard that
    matters is the one at the call site, and this is what holds it there.
    """

    def test_a_body_past_its_deadline_prepares_no_further_source(self, monkeypatch):
        def refuse(source: str):
            raise AssertionError("a source was folded past the body deadline")

        monkeypatch.setattr(fan_in, "prepare_source", refuse)
        monkeypatch.setattr(fan_in, "repair_deadline", lambda: time.thread_time() - 1)
        claims = [
            sample_draft(
                "S-01",
                grounds=[
                    Ground(
                        kind="quote",
                        text="a span this source does not contain at all",
                        source_label="description",
                    )
                ],
            )
        ]

        checked = fan_in._verify_quotes(claims, {"description": "Some other words."})

        assert checked.repaired == []
        assert [dropped.claim_id for dropped in checked.groundless] == ["S-01"]

    def test_a_body_with_time_left_still_repairs(self):
        """The guard refuses a spent body, never a live one."""
        source = "The ledger service talks to the accounts database."
        claims = [
            sample_draft(
                "S-01",
                grounds=[
                    Ground(
                        kind="quote",
                        text="The ledger service talks to the acounts database.",
                        source_label="description",
                    )
                ],
            )
        ]

        checked = fan_in._verify_quotes(claims, {"description": source})

        assert [repair.claim_id for repair in checked.repaired] == ["S-01"]


def batches(**proposals_by_lane):
    """Each lane against the batch its agent emitted, as the fan-in takes them."""
    return {
        lane: SCHEMAS.proposals.model_validate(
            {"claims": [proposal.model_dump(mode="json") for proposal in proposals]}
        )
        for lane, proposals in proposals_by_lane.items()
    }


class TestFanIn:
    """The one interface: batches in, drafts, marks, deferrals and coverage out."""

    def test_a_lane_with_no_batch_contributes_nothing(self, model):
        merged = fan_in.fan_in(
            batches(spoofing=[sample_proposal("S-01")]), STRIDE, model
        )

        assert [draft.id for draft in merged.drafts] == ["S-01"]
        assert merged.drafts[0].category == "spoofing"
        assert merged.deferred == {}

    def test_a_proposal_resolves_to_the_draft_the_fixtures_promise(self, model):
        """The catalog is derived from the same model the agent chose from."""
        merged = fan_in.fan_in(
            batches(spoofing=[sample_proposal("S-01")]), STRIDE, model
        )

        assert merged.drafts == [sample_draft("S-01")]

    def test_every_mark_is_narrowed_to_the_drafts_that_survived(self, model):
        """The #706 shape: a pass marks a draft that a later pass drops.

        The reference pass marks the invented element and keeps the draft on
        the real one; the bounds pass then finds the real one out of reach of
        the draft's grounds and drops the draft. The mark must go with it, and
        the drop must stay.
        """
        two_hops = "store:orders-db"
        proposal = sample_proposal(
            "S-01", affected_element_ids=["process:invented", two_hops]
        )
        merged = fan_in.fan_in(batches(spoofing=[proposal]), STRIDE, model, SOURCES)

        assert merged.drafts == []
        assert merged.marks.unresolved_references == []
        (dropped,) = merged.marks.dropped_claims
        assert dropped.claim_id == "S-01"

    def test_a_draft_on_a_ruled_out_unit_is_refused_with_the_reason(
        self, model, monkeypatch
    ):
        monkeypatch.setattr(
            DraftThreat, "unit_of", classmethod(lambda cls, draft: draft.id)
        )
        merged = fan_in.fan_in(
            batches(spoofing=[sample_proposal("S-01")]),
            STRIDE,
            model,
            ruled_out={"S-01": "ruled out in code by a test"},
        )

        assert merged.drafts == []
        (dropped,) = merged.marks.dropped_claims
        assert (dropped.claim_id, dropped.reason) == (
            "S-01",
            "ruled out in code by a test",
        )

    def test_coverage_is_accounted_per_lane_over_the_resolved_drafts(self, model):
        merged = fan_in.fan_in(
            batches(
                spoofing=[sample_proposal("S-01")],
                tampering=[sample_proposal("T-01", "tampering")],
            ),
            STRIDE,
            model,
        )

        by_lane = {row.lane: row.drafts for row in merged.coverage}
        assert set(by_lane) == set(STRIDE.lanes)
        assert (by_lane["spoofing"], by_lane["tampering"]) == (1, 1)


class TestSettledDuplicates:
    """Two conditional drafts are one finding only when they ask one question.

    A draft its own grounds settle never reaches a critic, so the fan-in is the
    only place two of them are compared. The key is the lane, the verb, the
    place and the unstated controls the drafts rest on. The first Baseline
    dropped a WebSocket draft for a REST draft between the same two processes:
    one place under the endpoint fold, two flows, two unstated controls, and
    the dropped draft's question about the socket's session went with it.
    """

    @pytest.fixture
    def index(self):
        return ModelIndex.of(valid_model())

    @staticmethod
    def resting_on(threat_id, attribute, category="spoofing", verb="impersonate"):
        return sample_draft(
            threat_id,
            category,
            verb=verb,
            affected_element_ids=["flow:customer-to-web-app:login"],
            grounds=[
                Ground(
                    kind="unknown-attribute",
                    element_id="flow:customer-to-web-app:login",
                    attribute=attribute,
                )
            ],
        )

    def test_one_question_asked_twice_keeps_the_first(self, index):
        drafts = [
            self.resting_on("S-01", "authentication"),
            self.resting_on("S-02", "authentication"),
        ]

        kept, dropped = fan_in._drop_settled_duplicates(drafts, index)

        assert [claim.id for claim in kept] == ["S-01"]
        assert [(mark.claim_id, mark.title) for mark in dropped] == [
            ("S-02", drafts[1].title)
        ]
        assert "'S-01'" in dropped[0].reason

    def test_two_unstated_controls_are_two_questions(self, index):
        drafts = [
            self.resting_on("S-01", "authentication"),
            self.resting_on("S-02", "encryption_in_transit"),
        ]

        kept, dropped = fan_in._drop_settled_duplicates(drafts, index)

        assert [claim.id for claim in kept] == ["S-01", "S-02"]
        assert dropped == []

    def test_two_lanes_are_two_findings(self, index):
        drafts = [
            self.resting_on("S-01", "authentication"),
            self.resting_on("T-01", "authentication", category="tampering"),
        ]

        kept, dropped = fan_in._drop_settled_duplicates(drafts, index)

        assert [claim.id for claim in kept] == ["S-01", "T-01"]
        assert dropped == []
