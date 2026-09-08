"""Tests for the mechanical checks around the critic: review and assemble."""

import pytest

from analysis_service import critic
from analysis_service.critic import (
    CriticOutputError,
    assemble_claims,
    mentioned_ids,
    review_issues,
    snap_rulings,
)
from analysis_service.fan_in import join_drafts, snap_drafts
from analysis_service.frameworks import schemas_for
from analysis_service.frameworks.stride import STRIDE
from analysis_service.report import (
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

    def test_a_needs_info_on_notes_names_no_attribute(self, model):
        """``notes`` is a pydantic field and not a security attribute.

        The reader is the evidence catalog's own list, so a field the catalog
        never writes an entry for is refused here too, and the message names
        the fields that would resolve. The live critic answered with ``notes``
        or ``description`` in 14 of the 38 reports archived under
        ``evals/runs/``, every one a question about a sentence pointed at a
        field that happened to exist.
        """
        drafts = [sample_draft("S-01")]
        rulings = [
            sample_ruling(
                "S-01",
                verdict=Verdict(
                    status="needs-info",
                    reason="the note hints at a shared account",
                    related_unknowns=[
                        UnknownRef(element_id="process:web-app", attribute="notes")
                    ],
                ),
            )
        ]
        with pytest.raises(CriticOutputError, match="That element has: technology"):
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


class TestADuplicateRejectionOfAUnitBearingDraftIsMalformed:
    """#657: a package that names a unit decides duplication by its identifier."""

    def _asvs_draft(self):
        from analysis_service.frameworks.asvs.record import DraftRequirementRuling

        return DraftRequirementRuling.model_validate(
            {
                **sample_draft("S-01").model_dump(
                    exclude={"category", "verb", "severity", "mitigations"}
                ),
                "id": "v5.0.0-6.2.1",
                "chapter": "authentication",
            }
        )

    def test_it_is_re_asked_on_a_draft_that_names_a_unit(self):
        from analysis_service.critic import review_issues

        draft = self._asvs_draft()
        rulings = [
            sample_ruling(
                draft.id,
                verdict=ProposedVerdict(
                    status="rejected", reason="dup", rejected_because="duplicate"
                ),
            )
        ]
        problems = review_issues([draft], rulings, valid_model())
        assert any("decided by its identifier" in m for m in problems.messages)

    def test_it_passes_on_a_draft_that_names_none(self):
        from analysis_service.critic import review_issues

        draft = sample_draft("S-01")
        rulings = [
            sample_ruling(
                "S-01",
                verdict=ProposedVerdict(
                    status="rejected", reason="dup", rejected_because="duplicate"
                ),
            )
        ]
        problems = review_issues([draft], rulings, valid_model())
        assert not any("decided by its identifier" in m for m in problems.messages)


def test_ruling_view_carries_the_unit_text_a_package_supplies():
    """#659: the critic judges against the requirement, not the paraphrase.

    STRIDE has no unit text, so its view carries no key; an ASVS draft carries
    the catalog's words for the requirement it rules on.
    """
    from analysis_service.frameworks.asvs.catalog import requirement_text
    from analysis_service.frameworks.asvs.record import RequirementRuling
    from analysis_service.report import Ground, Verdict

    (stride_view,) = critic._ruling_view([sample_draft("S-01", "spoofing")])
    asvs = RequirementRuling(
        id="v5.0.0-6.2.1",
        framework="asvs",
        framework_version="5.0.0",
        chapter="authentication",
        title="t",
        description="d",
        affected_element_ids=[],
        grounds=[Ground(kind="derived-fact", flow_id="flow:customer-to-web-app:login")],
        verdict=Verdict(status="confirmed"),
    )
    (asvs_view,) = critic._ruling_view([asvs])

    assert "unit_text" not in stride_view
    assert asvs_view["unit_text"] == requirement_text("V6.2.1")
    assert "8 characters" in asvs_view["unit_text"]
