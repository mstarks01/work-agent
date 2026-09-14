"""The assertion catalog: its registry, its identity rule and its gate.

Three groups. The registry is held to the vocabularies and to the element
schema, so a predicate that names a subject type or a graph field neither
declares fails here. The identity rule is held to what ADR 0034 says it is: a
pure function of four parts, and not a cross-run alignment. The gate has one
test per refusal, plus the three audit probes (#925) this layer answers and the
one it deliberately does not.
"""

from typing import get_args

import pytest
from pydantic import ValidationError

from analysis_service.assertions import (
    ABSENT,
    GRAPH_BOUND,
    MAX_ASSERTIONS,
    REGISTRY,
    REGISTRY_VERSION,
    SUBJECT_PREFIXES,
    UNIVERSAL_TERMS,
    Assertion,
    AssertionCatalog,
    Qualifier,
    QualifierKind,
    Subject,
    SubjectType,
    assertion_id,
    catalog_issues,
    conflicts,
    projection_fields,
    spans_for,
    subject_id,
    support_span,
)
from analysis_service.system_model import UNKNOWN, all_attribute_names

SOURCE_LABEL = "System description"

# The audit's own sentence (#925), hard-wrapped the way a corpus source is.
SOURCE = (
    "Shoppers sign in with email and password and get a session\n"
    "cookie; we have not rolled out MFA for shopper accounts yet.\n"
    "The card processor calls our settlement webhook. Receipts land\n"
    "in a bucket the order service reads over TLS with its own\n"
    "service account.\n"
)
SOURCES = {SOURCE_LABEL: SOURCE}

FLOW = "flow:shopper-to-storefront-api:place-order"
WEBHOOK = "flow:card-processor-to-settlement-webhook:post-settlement"


def span_for(quote):
    """The one span ``quote`` occupies in :data:`SOURCE`, refusing an absent one."""
    span = support_span(quote, SOURCE_LABEL, SOURCE)
    assert span is not None, f"the fixture source does not hold {quote!r}"
    return [span]


def subjects(*rows):
    return [Subject(id=row[0], type=row[1], label=row[2]) for row in rows]


def catalog(entries, *, rows=((FLOW, "interaction", "place order"),)):
    return AssertionCatalog(subjects=subjects(*rows), entries=list(entries))


def codes(issues):
    return sorted(issue.code for issue in issues)


class TestTheRegistryAnswersItsVocabularies:
    """Each entry is held to the enums and to the element schema it names."""

    @pytest.mark.parametrize("name", sorted(REGISTRY))
    def test_a_predicate_names_declared_subject_types(self, name):
        predicate = REGISTRY[name]
        assert predicate.subjects
        assert predicate.subjects <= set(get_args(SubjectType))
        assert predicate.refers_to <= set(get_args(SubjectType))

    @pytest.mark.parametrize("name", sorted(REGISTRY))
    def test_a_predicate_scope_requirement_names_declared_kinds(self, name):
        assert set(REGISTRY[name].requires) <= set(get_args(QualifierKind))

    @pytest.mark.parametrize("name", sorted(REGISTRY))
    def test_a_value_kind_brings_what_it_needs(self, name):
        """A ``term`` predicate has a vocabulary and a ``reference`` a referent.

        The other way round too: free text declaring a vocabulary would be a
        vocabulary nothing reads.
        """
        predicate = REGISTRY[name]
        assert bool(predicate.terms) == (predicate.value == "term")
        assert bool(predicate.refers_to) == (predicate.value == "reference")

    @pytest.mark.parametrize("name", sorted(REGISTRY))
    def test_a_vocabulary_never_respells_a_universal_term(self, name):
        """``absent`` and ``unknown`` are legal everywhere, so no entry repeats them."""
        assert not REGISTRY[name].terms & UNIVERSAL_TERMS

    @pytest.mark.parametrize("name", sorted(REGISTRY))
    def test_a_predicate_carries_its_meaning(self, name):
        assert REGISTRY[name].meaning.strip()

    def test_every_projection_names_a_field_the_schema_declares(self):
        """A predicate cannot be authoritative for a field that does not exist."""
        assert set(projection_fields().values()) <= set(all_attribute_names())

    def test_nine_of_the_fourteen_predicates_project_into_nothing(self):
        """ADR 0034's figure, re-derived rather than asserted in its prose.

        It is the whole argument for the catalog: the graph has no field for
        most of what the first release scopes.
        """
        assert len(REGISTRY) == 14
        assert len(REGISTRY) - len(projection_fields()) == 9

    def test_two_predicates_can_project_into_one_field(self):
        """A mechanism and the credential it presents share one string today.

        Which is why a projection is loss-aware rather than a copy.
        """
        assert projection_fields()["authentication-mechanism"] == "authentication"
        assert projection_fields()["credential-presented"] == "authentication"


class TestSubjectIdentity:
    def test_every_subject_type_declares_its_prefixes(self):
        assert set(SUBJECT_PREFIXES) == set(get_args(SubjectType))

    def test_the_graph_bound_types_are_the_three_the_schema_names(self):
        assert GRAPH_BOUND == {"component", "interaction", "zone"}

    def test_a_subject_id_is_deterministic(self):
        assert (
            subject_id("principal", "Shopper accounts") == "principal:shopper-accounts"
        )
        assert (
            subject_id("principal", "shopper  accounts") == "principal:shopper-accounts"
        )

    def test_a_graph_bound_type_has_no_id_to_build(self):
        """Its ID is the element's, so a second spelling of it is refused."""
        with pytest.raises(ValueError, match="graph-bound"):
            subject_id("interaction", "place order")

    def test_an_unknown_subject_type_is_refused(self):
        with pytest.raises(ValueError, match="unknown subject type"):
            subject_id("widget", "anything")

    def test_a_label_is_not_the_identity(self):
        """Renaming the display label leaves every row pointing at the same thing."""
        subject = Subject(
            id="principal:shopper-accounts", type="principal", label="Shoppers"
        )
        renamed = subject.model_copy(update={"label": "Retail customers"})
        assert renamed.id == subject.id


class TestAssertionIdentity:
    def stated(self, **overrides):
        fields = {
            "subject": FLOW,
            "predicate": "authentication-mechanism",
            "value": "email and password",
            "basis": "stated",
            "support": span_for("sign in with email and password"),
        }
        return Assertion(**{**fields, **overrides})

    def test_it_is_a_pure_function_of_its_four_parts(self):
        assert assertion_id(self.stated()) == assertion_id(self.stated())

    def test_two_sources_for_one_fact_are_one_assertion(self):
        """Different support, same identity: one row carries both spans."""
        other = self.stated(support=span_for("email and password"))
        assert assertion_id(other) == assertion_id(self.stated())

    def test_a_basis_or_an_assessment_never_moves_the_identity(self):
        assert assertion_id(self.stated(basis="legacy", support=[])) == assertion_id(
            self.stated()
        )

    def test_the_wording_of_a_text_value_is_folded(self):
        """The citation ladder's own fold, so two spellings are one value."""
        assert assertion_id(self.stated(value="Email and  password")) == assertion_id(
            self.stated()
        )

    def test_a_different_text_value_is_a_different_assertion(self):
        assert assertion_id(self.stated(value="company SSO")) != assertion_id(
            self.stated()
        )

    def test_scope_order_does_not_move_the_identity(self):
        one = self.stated(
            scope=[
                Qualifier(kind="principal", value="shoppers"),
                Qualifier(kind="environment", value="production"),
            ]
        )
        other = self.stated(
            scope=[
                Qualifier(kind="environment", value="production"),
                Qualifier(kind="principal", value="shoppers"),
            ]
        )
        assert assertion_id(one) == assertion_id(other)

    def test_a_scope_narrows_the_identity(self):
        scoped = self.stated(scope=[Qualifier(kind="principal", value="shoppers")])
        assert assertion_id(scoped) != assertion_id(self.stated())

    def test_a_term_value_is_spelled_in_the_identity(self):
        """A term is already canonical, so the ID stays readable where it can."""
        row = Assertion(
            subject=FLOW,
            predicate="mfa-requirement",
            value=ABSENT,
            basis="inferred",
            premises=["assertion:x"],
            explanation="nobody mentioned a second factor",
        )
        assert assertion_id(row).endswith(f"~{ABSENT}")

    def test_an_unregistered_predicate_has_no_identity(self):
        """The value key depends on the predicate, so the gate reports it first."""
        with pytest.raises(KeyError):
            assertion_id(self.stated(predicate="vibes"))


class TestASpanIsLocatedByCode:
    def test_a_span_names_the_submitter_s_own_words(self):
        span = support_span("we have not rolled out MFA", SOURCE_LABEL, SOURCE)
        assert SOURCE[span.start : span.end] == "we have not rolled out MFA"

    def test_a_span_survives_a_hard_wrap(self):
        """The quote straddles a newline the submitter never sees."""
        span = support_span("get a session cookie", SOURCE_LABEL, SOURCE)
        assert SOURCE[span.start : span.end] == "get a session\ncookie;"

    def test_a_quote_the_source_does_not_hold_yields_no_span(self):
        assert support_span("MFA is enforced everywhere", SOURCE_LABEL, SOURCE) is None

    def test_a_cut_quote_yields_one_span_per_fragment(self):
        spans = spans_for("Shoppers sign in…shopper accounts yet", SOURCE_LABEL, SOURCE)
        assert len(spans) == 2
        assert SOURCE[spans[0].start : spans[0].end] == "Shoppers sign in"

    def test_the_digest_pins_the_text_the_span_was_taken_from(self):
        span = support_span("session", SOURCE_LABEL, SOURCE)
        other = support_span("session", SOURCE_LABEL, SOURCE + "and one more line.\n")
        assert span.digest != other.digest


class TestWhatTheGateRefuses:
    def stated(self, **overrides):
        fields = {
            "subject": FLOW,
            "predicate": "authentication-mechanism",
            "value": "email and password",
            "basis": "stated",
            "support": span_for("sign in with email and password"),
        }
        return Assertion(**{**fields, **overrides})

    def test_a_well_formed_catalog_raises_nothing(self):
        assert catalog_issues(catalog([self.stated()]), sources=SOURCES) == []

    def test_an_issue_names_the_row_it_is_about(self):
        """An issue a reader cannot trace back to a row is a message, not a fault."""
        (issue,) = catalog_issues(catalog([self.stated(support=[])]))
        assert issue.assertion == assertion_id(self.stated())

    def test_a_catalog_keyed_by_another_registry_is_refused(self):
        """Fail closed rather than read version 1's rows under version 2's rules."""
        held = catalog([self.stated()])
        held.registry_version = REGISTRY_VERSION + 1
        assert codes(catalog_issues(held, sources=SOURCES)) == [
            "wrong-registry-version"
        ]

    def test_an_unregistered_predicate(self):
        issues = catalog_issues(catalog([self.stated(predicate="vibes")]))
        assert codes(issues) == ["unknown-predicate"]

    def test_a_subject_nothing_declares(self):
        issues = catalog_issues(catalog([self.stated(subject="flow:a-to-b:c")]))
        assert "dangling-subject" in codes(issues)

    def test_a_predicate_on_the_wrong_kind_of_subject(self):
        rows = (("credential:session-cookie", "credential", "session cookie"),)
        issues = catalog_issues(
            catalog([self.stated(subject="credential:session-cookie")], rows=rows)
        )
        assert "wrong-subject-type" in codes(issues)

    def test_a_subject_typed_against_its_own_prefix(self):
        rows = (("process:order-service", "principal", "order service"),)
        issues = catalog_issues(catalog([], rows=rows))
        assert codes(issues) == ["subject-type-mismatch"]

    def test_a_subject_declared_twice(self):
        rows = (
            (FLOW, "interaction", "place order"),
            (FLOW, "interaction", "place order again"),
        )
        issues = catalog_issues(catalog([], rows=rows))
        assert codes(issues) == ["duplicate-subject"]

    def test_a_term_outside_the_predicate_s_vocabulary(self):
        row = Assertion(
            subject=FLOW,
            predicate="mfa-requirement",
            value="probably",
            basis="stated",
            support=span_for("MFA"),
        )
        assert "illegal-value" in codes(catalog_issues(catalog([row]), sources=SOURCES))

    def test_a_reference_to_a_subject_of_the_wrong_type(self):
        rows = (
            (FLOW, "interaction", "place order"),
            ("process:order-service", "component", "order service"),
        )
        row = Assertion(
            subject=FLOW,
            predicate="credential-presented",
            value="process:order-service",
            basis="stated",
            support=span_for("service account"),
        )
        issues = catalog_issues(catalog([row], rows=rows), sources=SOURCES)
        assert "illegal-value" in codes(issues)

    def test_an_unknown_that_does_not_say_why(self):
        row = Assertion(
            subject=FLOW,
            predicate="transport-encryption",
            value=UNKNOWN,
            basis="stated",
        )
        assert "missing-reason" in codes(catalog_issues(catalog([row])))

    def test_an_unknown_needs_no_span(self):
        """There is nothing to quote when the sources do not answer."""
        row = Assertion(
            subject=FLOW,
            predicate="transport-encryption",
            value=UNKNOWN,
            basis="stated",
            reason="silent",
        )
        assert catalog_issues(catalog([row]), sources=SOURCES) == []

    def test_a_reason_on_a_value_that_is_not_unknown(self):
        issues = catalog_issues(
            catalog([self.stated(reason="silent")]), sources=SOURCES
        )
        assert codes(issues) == ["unwanted-reason"]

    def test_a_grant_with_no_resource_and_no_operation(self):
        rows = (("principal:analyst", "principal", "analyst"),)
        row = Assertion(
            subject="principal:analyst",
            predicate="authorization-grant",
            value="reads every column",
            basis="stated",
            support=span_for("reads over TLS"),
        )
        issues = catalog_issues(catalog([row], rows=rows), sources=SOURCES)
        assert codes(issues) == ["missing-scope"]

    def test_a_stated_value_that_cites_nothing(self):
        """The empty-citation bypass, at the assertion layer (#925)."""
        issues = catalog_issues(catalog([self.stated(support=[])]))
        assert codes(issues) == ["unsupported-assertion"]

    def test_a_legacy_value_that_claims_support(self):
        issues = catalog_issues(catalog([self.stated(basis="legacy")]), sources=SOURCES)
        assert codes(issues) == ["legacy-with-support"]

    def test_an_inferred_value_that_names_no_premise(self):
        issues = catalog_issues(catalog([self.stated(basis="inferred", support=[])]))
        assert codes(issues) == ["missing-premise"]

    def test_a_derived_value_that_names_no_rule(self):
        issues = catalog_issues(catalog([self.stated(basis="derived", support=[])]))
        assert codes(issues) == ["missing-premise"]

    def test_a_premise_that_names_no_assertion_here(self):
        row = self.stated(
            basis="inferred",
            support=[],
            premises=["assertion:nowhere"],
            explanation="from the session cookie",
        )
        assert "dangling-premise" in codes(catalog_issues(catalog([row])))

    def test_support_that_supports_itself(self):
        """A row that is its own premise reads as justified from every step."""
        row = self.stated(basis="inferred", support=[], explanation="itself")
        row = row.model_copy(update={"premises": [assertion_id(row)]})
        assert "circular-support" in codes(catalog_issues(catalog([row])))

    def test_support_that_loops_through_another_row(self):
        first = self.stated(basis="inferred", support=[], explanation="a")
        second = self.stated(
            value="company SSO", basis="inferred", support=[], explanation="b"
        )
        first = first.model_copy(update={"premises": [assertion_id(second)]})
        second = second.model_copy(update={"premises": [assertion_id(first)]})
        issues = catalog_issues(catalog([first, second]))
        assert "circular-support" in codes(issues)

    def test_an_assessment_with_nobody_behind_it(self):
        issues = catalog_issues(
            catalog([self.stated(assessment="supported")]), sources=SOURCES
        )
        assert codes(issues) == ["unassessed-assessor"]

    def test_a_span_naming_a_source_the_job_does_not_carry(self):
        issues = catalog_issues(catalog([self.stated()]), sources={"Other": SOURCE})
        assert codes(issues) == ["dangling-source"]

    def test_a_span_whose_source_changed_underneath_it(self):
        moved = {SOURCE_LABEL: SOURCE.replace("email and password", "a hardware key")}
        issues = catalog_issues(catalog([self.stated()]), sources=moved)
        assert codes(issues) == ["stale-digest"]

    def test_a_span_whose_offsets_do_not_hold_its_quote(self):
        span = span_for("sign in with email and password")[0]
        moved = span.model_copy(update={"start": 0, "end": 8})
        issues = catalog_issues(
            catalog([self.stated(support=[moved])]), sources=SOURCES
        )
        assert codes(issues) == ["unverifiable-span"]

    def test_two_rows_sharing_one_identity(self):
        issues = catalog_issues(
            catalog([self.stated(), self.stated()]), sources=SOURCES
        )
        assert "duplicate-assertion" in codes(issues)

    def test_a_catalog_over_the_cap_returns_that_alone(self):
        """A catalog too large to read cannot be fixed by fixing its rows."""
        rows = [
            self.stated(value=f"mechanism {index}")
            for index in range(MAX_ASSERTIONS + 1)
        ]
        assert codes(catalog_issues(catalog(rows))) == ["too-many-assertions"]

    def test_a_binding_the_model_does_not_hold_needs_the_model(self):
        """Without a model there is nothing to resolve the binding against."""
        from analysis_service.system_model import SystemModel

        assert catalog_issues(catalog([self.stated()]), sources=SOURCES) == []
        issues = catalog_issues(
            catalog([self.stated()]), model=SystemModel(), sources=SOURCES
        )
        assert "dangling-binding" in codes(issues)


class TestConflictsAreDerived:
    def row(self, **overrides):
        fields = {
            "subject": FLOW,
            "predicate": "transport-encryption",
            "value": "TLS",
            "basis": "stated",
            "support": span_for("over TLS"),
        }
        return Assertion(**{**fields, **overrides})

    def test_one_value_is_no_conflict(self):
        assert conflicts(catalog([self.row()])) == ()

    def test_two_values_where_one_may_hold(self):
        found = conflicts(catalog([self.row(), self.row(value="no encryption at all")]))
        assert len(found) == 1
        assert found[0].predicate == "transport-encryption"
        assert found[0].values == ("TLS", "no encryption at all")

    def test_two_values_where_several_may_hold(self):
        """Mutual TLS and a bearer token are both true of one connection."""
        mechanism = self.row(predicate="authentication-mechanism")
        both = [mechanism, mechanism.model_copy(update={"value": "a bearer token"})]
        assert conflicts(catalog(both)) == ()

    def test_a_stated_absence_beside_a_stated_mechanism(self):
        mechanism = self.row(predicate="authentication-mechanism")
        found = conflicts(
            catalog([mechanism, mechanism.model_copy(update={"value": ABSENT})])
        )
        assert len(found) == 1

    def test_an_unknown_never_conflicts(self):
        """A row saying nobody stated it answers nothing the other row claims."""
        silent = self.row(value=UNKNOWN, basis="legacy", support=[], reason="silent")
        assert conflicts(catalog([self.row(), silent])) == ()

    def test_a_different_scope_is_a_different_question(self):
        scoped = self.row(
            value="no encryption at all",
            scope=[Qualifier(kind="environment", value="staging")],
        )
        assert conflicts(catalog([self.row(), scoped])) == ()


class TestTheAuditProbes:
    """What this layer answers of #925's probes, and what it still does not."""

    def stated(self, **overrides):
        fields = {
            "subject": FLOW,
            "predicate": "mfa-requirement",
            "value": ABSENT,
            "basis": "stated",
            "support": span_for("we have not rolled out MFA"),
        }
        return Assertion(**{**fields, **overrides})

    def test_reversing_a_control_writes_a_different_assertion(self):
        """ "No MFA" into "MFA enforced" is a new row, not an edited string.

        The graph's ``authentication`` field reads both as ``stated``, which is
        the probe no extraction figure reaches. Here the two are separate
        identities, so a comparison against a reference sees a value that is
        absent and a value that is present.
        """
        reversed_row = self.stated(value="required")
        assert assertion_id(reversed_row) != assertion_id(self.stated())

    def test_a_reversal_cannot_read_as_verified(self):
        """Its span verifies, because the words really are in the source.

        Whether they *support* ``required`` is a judgement, and the assessment
        starts at ``unchecked`` with nobody behind it. So a reversal is a
        visible unassessed row rather than an invisible upgrade to a fact.
        """
        reversed_row = self.stated(value="required")
        assert reversed_row.assessment == "unchecked"
        assert catalog_issues(catalog([reversed_row]), sources=SOURCES) == []

    def test_a_control_on_one_interaction_leaves_another_alone(self):
        """Borrowing the receipt path's control cannot remove the webhook's row.

        The graph's catalog drops an ``unknown`` entry the moment a control
        reads ``stated``, so a borrowed value silently erased the webhook's
        uncertainty. Here the webhook's own row is a row, and a row about
        another subject does not touch it.
        """
        rows = (
            (FLOW, "interaction", "place order"),
            (WEBHOOK, "interaction", "post settlement"),
        )
        unknown = Assertion(
            subject=WEBHOOK,
            predicate="authentication-mechanism",
            value=UNKNOWN,
            basis="stated",
            reason="silent",
        )
        borrowed = Assertion(
            subject=FLOW,
            predicate="authentication-mechanism",
            value="its own service account",
            basis="stated",
            support=span_for("with its own\nservice account"),
        )
        held = catalog([unknown, borrowed], rows=rows)
        assert catalog_issues(held, sources=SOURCES) == []
        assert any(entry.subject == WEBHOOK for entry in held.entries)

    def test_a_blank_citation_cannot_make_a_supported_model(self):
        assert codes(catalog_issues(catalog([self.stated(support=[])]))) == [
            "unsupported-assertion"
        ]


class TestTheCatalogRoundTrips:
    def test_a_dumped_catalog_reloads_unchanged(self):
        held = catalog(
            [
                Assertion(
                    subject=FLOW,
                    predicate="mfa-requirement",
                    value=ABSENT,
                    basis="stated",
                    support=span_for("not rolled out MFA"),
                    scope=[Qualifier(kind="principal", value="shopper accounts")],
                )
            ]
        )
        assert AssertionCatalog.model_validate_json(held.model_dump_json()) == held

    def test_an_unexpected_field_is_refused(self):
        with pytest.raises(ValidationError):
            AssertionCatalog.model_validate({"entries": [], "notes": "hello"})
