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
    MAX_QUOTE_CHARS,
    MAX_SUBJECTS,
    REGISTRY,
    REGISTRY_VERSION,
    SUBJECT_PREFIXES,
    UNIVERSAL_TERMS,
    Assertion,
    AssertionCatalog,
    AssertionProposal,
    CatalogIssueCode,
    CatalogProposal,
    Qualifier,
    QualifierKind,
    QuoteProposal,
    Subject,
    SubjectType,
    assertion_id,
    catalog_issues,
    conflicts,
    projection_fields,
    resolve_catalog,
    span_source,
    spans_for,
    subject_id,
    support_span,
)
from analysis_service.system_model import UNKNOWN, SystemModel, all_attribute_names

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
    def test_a_reference_predicate_points_at_exactly_one_type(self, name):
        """Two would leave the resolver guessing which one a name meant.

        A model writes a name and code builds the subject ID from it, so the
        predicate has to settle the type on its own.
        """
        predicate = REGISTRY[name]
        assert len(predicate.refers_to) <= 1

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
        prepared = span_source(SOURCE_LABEL, SOURCE)
        spans = spans_for("Shoppers sign in…shopper accounts yet", prepared)
        assert len(spans) == 2
        assert SOURCE[spans[0].start : spans[0].end] == "Shoppers sign in"

    def test_a_source_is_folded_once_for_every_quote_taken_from_it(self):
        """The fold reads every character, so it belongs to the source.

        A body building spans for a whole catalog prepares each source once.
        Folding per quote spends the whole submission once for every row.
        """
        prepared = span_source(SOURCE_LABEL, SOURCE)
        assert spans_for("session", prepared)[0] == support_span(
            "session", SOURCE_LABEL, SOURCE
        )

    def test_a_quote_past_the_bound_takes_no_span(self):
        """Refused rather than cut: a cut quote beside whole offsets is a lie."""
        assert support_span("x" * (MAX_QUOTE_CHARS + 1), SOURCE_LABEL, SOURCE) is None

    def test_the_digest_pins_the_text_the_span_was_taken_from(self):
        span = support_span("session", SOURCE_LABEL, SOURCE)
        other = support_span("session", SOURCE_LABEL, SOURCE + "and one more line.\n")
        assert span.digest != other.digest


def stated(**overrides):
    """One well-formed stated assertion, which each refusal breaks one way."""
    fields = {
        "subject": FLOW,
        "predicate": "authentication-mechanism",
        "value": "email and password",
        "basis": "stated",
        "support": span_for("sign in with email and password"),
    }
    return Assertion(**{**fields, **overrides})


def looping_pair():
    """Two assertions, each the other's premise."""
    first = stated(basis="inferred", support=[], explanation="a")
    second = stated(value="company SSO", basis="inferred", support=[], explanation="b")
    first = first.model_copy(update={"premises": [assertion_id(second)]})
    return [
        first.model_copy(),
        second.model_copy(update={"premises": [assertion_id(first)]}),
    ]


def over_the_cap(cap, make):
    return [make(index) for index in range(cap + 1)]


def moved_span(**overrides):
    return stated(
        support=[
            span_for("sign in with email and password")[0].model_copy(update=overrides)
        ]
    )


def versioned(entries, version):
    held = catalog(entries)
    held.registry_version = version
    return held


#: Every way the gate refuses a catalog, with a fixture that raises it and the
#: reason the refusal exists. **Self-completing against the enum**: a code added
#: to ``CatalogIssueCode`` fails ``test_every_refusal_code_has_a_fixture`` until
#: somebody writes the catalog that raises it. A refusal nobody has seen raised
#: looks exactly like a refusal that cannot fire.
REFUSALS: dict[str, tuple[str, AssertionCatalog, dict]] = {
    "wrong-registry-version": (
        "version 1's rows say nothing version 2 can read",
        versioned([stated()], REGISTRY_VERSION + 1),
        {"sources": SOURCES},
    ),
    "too-many-assertions": (
        "a catalog too large to read cannot be fixed by fixing its rows",
        catalog(over_the_cap(MAX_ASSERTIONS, lambda n: stated(value=f"mechanism {n}"))),
        {},
    ),
    "too-many-subjects": (
        "the same, for what the rows point at",
        AssertionCatalog(
            subjects=[
                Subject(id=f"principal:p{index}", type="principal", label=f"p{index}")
                for index in range(MAX_SUBJECTS + 1)
            ]
        ),
        {},
    ),
    "duplicate-subject": (
        "one ID naming two subjects resolves while pointing nowhere",
        catalog([], rows=((FLOW, "interaction", "one"), (FLOW, "interaction", "two"))),
        {},
    ),
    "duplicate-assertion": (
        "two rows of one identity; one row carries both spans",
        catalog([stated(), stated()]),
        {"sources": SOURCES},
    ),
    "subject-type-mismatch": (
        "a subject typed against the prefix its own ID carries",
        catalog([], rows=(("process:order-service", "principal", "order service"),)),
        {},
    ),
    "dangling-subject": (
        "a row about a subject nothing declares",
        catalog([stated(subject="flow:a-to-b:c")]),
        {},
    ),
    "dangling-binding": (
        "a graph-bound subject the model does not hold",
        catalog([stated()]),
        {"model": SystemModel(), "sources": SOURCES},
    ),
    "unknown-predicate": (
        "a predicate the registry does not hold, so nothing decides its values",
        catalog([stated(predicate="vibes")]),
        {},
    ),
    "wrong-subject-type": (
        "a predicate asked of a kind of subject it does not answer for",
        catalog(
            [stated(subject="credential:session-cookie")],
            rows=(("credential:session-cookie", "credential", "session cookie"),),
        ),
        {},
    ),
    "illegal-value": (
        "a term outside the predicate's own vocabulary",
        catalog(
            [
                Assertion(
                    subject=FLOW,
                    predicate="mfa-requirement",
                    value="probably",
                    basis="stated",
                    support=span_for("MFA"),
                )
            ]
        ),
        {"sources": SOURCES},
    ),
    "missing-reason": (
        "an unknown that does not say whether anyone asked",
        catalog(
            [
                Assertion(
                    subject=FLOW,
                    predicate="transport-encryption",
                    value=UNKNOWN,
                    basis="stated",
                )
            ]
        ),
        {},
    ),
    "unwanted-reason": (
        "a reason on a value that is not unknown",
        catalog([stated(reason="silent")]),
        {"sources": SOURCES},
    ),
    "missing-scope": (
        "a grant that names no resource and no operation",
        catalog(
            [
                Assertion(
                    subject="principal:analyst",
                    predicate="authorization-grant",
                    value="reads every column",
                    basis="stated",
                    support=span_for("reads over TLS"),
                )
            ],
            rows=(("principal:analyst", "principal", "analyst"),),
        ),
        {"sources": SOURCES},
    ),
    "unsupported-assertion": (
        "the empty-citation bypass, at the assertion layer (#925)",
        catalog([stated(support=[])]),
        {},
    ),
    "legacy-with-support": (
        "an imported value is never support-backed, whatever quote it carries",
        catalog([stated(basis="legacy")]),
        {"sources": SOURCES},
    ),
    "missing-premise": (
        "an inferred value that names nothing it rests on",
        catalog([stated(basis="inferred", support=[])]),
        {},
    ),
    "dangling-premise": (
        "a premise that is no assertion here",
        catalog(
            [
                stated(
                    basis="inferred",
                    support=[],
                    premises=["assertion:nowhere"],
                    explanation="from the session cookie",
                )
            ]
        ),
        {},
    ),
    "circular-support": (
        "support that loops reads as justified from every row on the loop",
        catalog(looping_pair()),
        {},
    ),
    "dangling-source": (
        "a span naming a source the job does not carry",
        catalog([stated()]),
        {"sources": {"Other": SOURCE}},
    ),
    "stale-digest": (
        "a source that changed under the offsets taken from it",
        catalog([stated()]),
        {"sources": {SOURCE_LABEL: SOURCE.replace("email and password", "a key")}},
    ),
    "unverifiable-span": (
        "offsets that do not hold the quote beside them",
        catalog([moved_span(start=0, end=8)]),
        {"sources": SOURCES},
    ),
    "unassessed-assessor": (
        "an assessment with nobody behind it is a model judging itself",
        catalog([stated(assessment="supported")]),
        {"sources": SOURCES},
    ),
}


class TestWhatTheGateRefuses:
    @pytest.mark.parametrize("code", sorted(REFUSALS))
    def test_the_gate_raises_it(self, code):
        why, held, reads = REFUSALS[code]
        assert code in codes(catalog_issues(held, **reads)), why

    def test_every_refusal_code_has_a_fixture(self):
        """The table answers ``CatalogIssueCode``, with nothing left over."""
        assert set(REFUSALS) == set(get_args(CatalogIssueCode))

    @pytest.mark.parametrize(
        "code", ["wrong-registry-version", "too-many-assertions", "too-many-subjects"]
    )
    def test_the_refusals_that_return_alone(self, code):
        """A catalog nobody can read reports that, rather than its rows."""
        _, held, reads = REFUSALS[code]
        assert codes(catalog_issues(held, **reads)) == [code]

    def test_a_well_formed_catalog_raises_nothing(self):
        assert catalog_issues(catalog([stated()]), sources=SOURCES) == []

    def test_an_issue_names_the_row_it_is_about(self):
        """An issue a reader cannot trace back to a row is a message, not a fault."""
        (issue,) = catalog_issues(catalog([stated(support=[])]))
        assert issue.assertion == assertion_id(stated())

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

    def test_a_binding_goes_unchecked_without_a_model(self):
        """A check that cannot be made is never a check that passed."""
        assert catalog_issues(catalog([stated()]), sources=SOURCES) == []


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


class TestResolvingAProposal:
    """What a model proposes, and what code builds from it.

    The property that carries the rest: **a resolved catalog raises nothing at
    the gate.** The resolver constructs, so every row it keeps is one
    ``catalog_issues`` passes, and every row it drops comes back as an issue
    naming why.
    """

    def model(self):
        return SystemModel.model_validate(
            {
                "external_entities": [
                    {
                        "id": "entity:shopper",
                        "name": "shopper",
                        "kind": "human",
                        "trust_zone": "boundary:internet",
                    }
                ],
                "processes": [
                    {
                        "id": "process:storefront-api",
                        "name": "storefront API",
                        "technology": "web",
                        "trust_zone": "boundary:app",
                        "exposure": "internet-facing",
                        "interface_kind": "web",
                    }
                ],
                "trust_boundaries": [
                    {"id": "boundary:internet", "name": "internet", "kind": "network"},
                    {"id": "boundary:app", "name": "app", "kind": "network"},
                ],
                "data_flows": [
                    {
                        "id": FLOW,
                        "name": "place order",
                        "source": "entity:shopper",
                        "destination": "process:storefront-api",
                        "protocol": "https",
                        "authentication": "session cookie",
                        "data_description": "an order",
                        "encryption_in_transit": "TLS",
                    }
                ],
            }
        )

    def row(self, **overrides):
        fields = {
            "subject_type": "interaction",
            "subject": FLOW,
            "predicate": "authentication-mechanism",
            "value": "email and password",
            "basis": "stated",
            "quotes": [
                QuoteProposal(
                    source_label=SOURCE_LABEL, quote="sign in with email and password"
                )
            ],
        }
        return AssertionProposal(**{**fields, **overrides})

    def resolve(self, *rows):
        return resolve_catalog(
            CatalogProposal(assertions=list(rows)), self.model(), SOURCES
        )

    def test_a_resolved_catalog_raises_nothing_at_the_gate(self):
        held, issues = self.resolve(self.row())
        assert issues == []
        assert catalog_issues(held, model=self.model(), sources=SOURCES) == []

    def test_the_subject_table_is_derived_from_the_rows(self):
        """A model that listed subjects too would have two places to spell one."""
        held, _ = self.resolve(self.row())
        assert [(s.id, s.type, s.label) for s in held.subjects] == [
            (FLOW, "interaction", "place order")
        ]

    def test_two_rows_of_one_identity_become_one_row_with_both_spans(self):
        """Two sources for one fact keep both provenances."""
        other = self.row(
            value="Email and  password",
            quotes=[
                QuoteProposal(source_label=SOURCE_LABEL, quote="email and password")
            ],
        )
        held, issues = self.resolve(self.row(), other)
        assert issues == []
        assert len(held.entries) == 1
        assert len(held.entries[0].support) == 2

    def test_a_repeated_quote_is_not_repeated_support(self):
        held, _ = self.resolve(self.row(), self.row())
        assert len(held.entries[0].support) == 1

    def test_a_stated_row_whose_quote_is_not_in_the_source_drops(self):
        """Never a fabricated span. The issue is what repair reads."""
        invented = self.row(
            predicate="transport-encryption",
            value="TLS 1.3",
            quotes=[
                QuoteProposal(source_label=SOURCE_LABEL, quote="everything is TLS 1.3")
            ],
        )
        held, issues = self.resolve(invented)
        assert held.entries == []
        assert codes(issues) == ["unsupported-assertion"]

    def test_a_quote_naming_a_source_the_job_does_not_carry_drops_its_row(self):
        held, issues = self.resolve(
            self.row(quotes=[QuoteProposal(source_label="Other", quote="anything")])
        )
        assert held.entries == []
        assert codes(issues) == ["unsupported-assertion"]

    def test_an_unregistered_predicate_drops(self):
        held, issues = self.resolve(self.row(predicate="vibes"))
        assert held.entries == []
        assert codes(issues) == ["unknown-predicate"]

    def test_a_subject_the_model_does_not_hold_drops(self):
        held, issues = self.resolve(self.row(subject="flow:nowhere:at-all"))
        assert held.entries == []
        assert codes(issues) == ["dangling-subject"]

    def test_a_near_spelling_of_an_element_id_snaps(self):
        """The same resolution a lane agent's element reference gets."""
        held, issues = self.resolve(self.row(subject=FLOW.upper()))
        assert issues == []
        assert held.entries[0].subject == FLOW

    def test_a_predicate_on_the_wrong_kind_of_subject_drops(self):
        held, issues = self.resolve(
            self.row(subject_type="credential", subject="session cookie")
        )
        assert held.entries == []
        assert codes(issues) == ["wrong-subject-type"]

    def test_a_subject_of_this_layer_s_own_is_slugged_from_its_name(self):
        """Two spellings of one principal are one subject, by the same slug."""
        grant = {
            "subject_type": "principal",
            "predicate": "authorization-grant",
            "value": "reads every column",
            "basis": "stated",
            "scope": [
                Qualifier(kind="resource", value="the warehouse"),
                Qualifier(kind="operation", value="read"),
            ],
            "quotes": [
                QuoteProposal(source_label=SOURCE_LABEL, quote="Shoppers sign in")
            ],
        }
        held, issues = self.resolve(
            AssertionProposal(subject="Shopper accounts", **grant),
            AssertionProposal(subject="shopper  accounts", **grant),
        )
        assert issues == []
        assert [s.id for s in held.subjects] == ["principal:shopper-accounts"]
        assert len(held.entries) == 1

    def test_a_reference_value_resolves_into_a_subject_the_catalog_declares(self):
        """The model writes a name; code builds the ID and declares the referent."""
        held, issues = self.resolve(
            self.row(
                predicate="credential-presented",
                value="session cookie",
                quotes=[QuoteProposal(source_label=SOURCE_LABEL, quote="a session")],
            )
        )
        assert issues == []
        assert held.entries[0].value == "credential:session-cookie"
        assert "credential:session-cookie" in {s.id for s in held.subjects}
        assert catalog_issues(held, model=self.model(), sources=SOURCES) == []

    def test_an_unknown_row_needs_no_quote(self):
        held, issues = self.resolve(
            self.row(
                predicate="storage-encryption",
                subject_type="component",
                subject="process:storefront-api",
                value=UNKNOWN,
                reason="silent",
                quotes=[],
            )
        )
        assert issues == []
        assert held.entries[0].reason == "silent"

    def test_a_reason_on_a_stated_value_is_dropped_rather_than_refused(self):
        """The row is right and the reason is noise, so the noise goes."""
        held, issues = self.resolve(self.row(reason="silent"))
        assert issues == []
        assert held.entries[0].reason is None

    def test_a_scope_survives_resolution(self):
        scoped = self.row(scope=[Qualifier(kind="principal", value="shoppers")])
        held, _ = self.resolve(scoped)
        assert held.entries[0].scope == [Qualifier(kind="principal", value="shoppers")]

    def test_a_dropped_row_names_what_it_was_about(self):
        _, issues = self.resolve(self.row(predicate="vibes"))
        assert issues[0].subject == FLOW
