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

from analysis_service.analysis import ABSENT_WORD
from analysis_service.assertions import (
    _QUALIFIED_BECAUSE,
    ABSENT,
    BASIS_RANK,
    CATALOG_REFUSALS,
    GATE_REFUSALS,
    GRAPH_BOUND,
    MAX_ASSERTIONS,
    MAX_QUOTE_CHARS,
    MAX_SPANS,
    MAX_SUBJECTS,
    PROJECTION_EFFECT,
    PROJECTS_UNDER,
    QUALIFIED_SPELLING,
    REGISTRY,
    REGISTRY_VERSION,
    SUBJECT_PREFIXES,
    UNIVERSAL_TERMS,
    UNPROJECTED,
    Assertion,
    AssertionCatalog,
    AssertionProposal,
    AssertionRecord,
    Assessment,
    Basis,
    CatalogIssueCode,
    CatalogProposal,
    ProjectionReason,
    Qualifier,
    QualifierKind,
    QuoteProposal,
    Subject,
    SubjectType,
    _named,
    admissible,
    answer,
    apply_projection,
    assertion_id,
    catalog_issues,
    conflicts,
    contradiction_issues,
    contradictions,
    merged,
    offered,
    project,
    projected_attribute,
    projection_fields,
    quarantined,
    referent_type,
    resolve_catalog,
    settled,
    span_source,
    spans_for,
    subject_id,
    support_span,
)
from analysis_service.claims import Ground
from analysis_service.evidence import evidence_catalog, unknown_evidence_ref
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

# A source that states one fact twice, which is what makes a quote of it name
# neither copy. Two sections of one runbook say the same sentence, which is how
# a real submission repeats itself.
REPEATED_LABEL = "Runbook"
REPEATED = (
    "Nightly batch: the order service reads the bucket over TLS.\n"
    "Receipts land in a bucket the order service reads.\n"
    "Failover: the order service reads the bucket over TLS.\n"
)
REPEATED_SOURCES = {REPEATED_LABEL: REPEATED}

FLOW = "flow:entity:shopper>process:storefront-api>place-order"
WEBHOOK = "flow:entity:card-processor>process:settlement-webhook>post-settlement"


def span_for(quote):
    """The one span ``quote`` occupies in :data:`SOURCE`, refusing an absent one."""
    span = support_span(quote, SOURCE_LABEL, SOURCE)
    assert span is not None, f"the fixture source does not hold {quote!r}"
    return [span]


def repeated_span(quote="the order service reads the bucket over TLS"):
    """The span ``quote`` takes in :data:`REPEATED`, which holds it twice."""
    span = support_span(quote, REPEATED_LABEL, REPEATED)
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

    def test_most_predicates_project_into_nothing(self):
        """ADR 0034's figure, re-derived rather than asserted in its prose.

        It is the whole argument for the catalog: the graph has no field for
        most of what this release scopes. `represented-by` is one of them by
        construction — it says which element a subject *is*, and no element has
        an attribute for that.

        The count moves two ways and the ratio is the claim. A predicate for a
        field the graph already holds joins the projecting side, which
        `data-classification` did; one for a fact the graph has no field for
        joins the other, which `origin-verification`, `credential-lifetime` and `credential-revocation`
        did.
        """
        unprojected = len(REGISTRY) - len(projection_fields())

        assert (len(REGISTRY), unprojected) == (21, 14)
        assert unprojected > len(projection_fields())

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

    def test_a_type_this_layer_owns_declares_exactly_one_prefix(self):
        """``subject_id`` reads the single member rather than choosing.

        It does ``next(iter(prefixes))``, which is the one-of-several shape
        that hides an ambiguity wherever the set can hold two. Here it cannot,
        and this is what says so: a second prefix on one of these types would
        make the built ID depend on set ordering.
        """
        for subject_type, prefixes in SUBJECT_PREFIXES.items():
            if subject_type in GRAPH_BOUND:
                continue
            assert len(prefixes) == 1, subject_type

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
        catalog([stated(subject="flow:process:a>process:b>c")]),
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
    "ambiguous-span": (
        (
            "a source that states the fact twice holds the quote in both"
            " places, so offsets into the first copy name neither"
        ),
        catalog([stated(support=repeated_span())]),
        {"sources": REPEATED_SOURCES},
    ),
    "exclusive-without-support": (
        (
            "an exclusivity claim closes the world for a predicate, so it needs"
            " a source rather than a basis of its own"
        ),
        catalog(
            [stated(basis="inferred", support=[], explanation="a", exclusive=True)]
        ),
        {},
    ),
    "inference-refused": (
        (
            "an identification this service guessed moves every fact about the"
            " subject onto the wrong element, with nothing to show it moved"
        ),
        catalog(
            [
                Assertion(
                    subject="principal:shoppers",
                    predicate="represented-by",
                    value="entity:shopper",
                    basis="inferred",
                    explanation="the names look alike",
                )
            ],
            rows=(
                ("principal:shoppers", "principal", "shopper accounts"),
                ("entity:shopper", "component", "shopper"),
            ),
        ),
        {"sources": SOURCES},
    ),
    "unassessed-assessor": (
        "an assessment with nobody behind it is a model judging itself",
        catalog([stated(assessment="supported")]),
        {"sources": SOURCES},
    ),
    "too-many-spans": (
        (
            "a statement resting on more spans than the cap is a summary, and a"
            " row cut to the cap would claim less support than it cited"
        ),
        catalog([stated(support=span_for("Shoppers sign in") * (MAX_SPANS + 1))]),
        {"sources": SOURCES},
    ),
}


class TestWhatTheGateRefuses:
    @pytest.mark.parametrize("code", sorted(REFUSALS))
    def test_the_gate_raises_it(self, code):
        why, held, reads = REFUSALS[code]
        assert code in codes(catalog_issues(held, **reads)), why

    def test_every_refusal_code_has_a_fixture(self):
        """The table answers every code the gate raises, with nothing left over.

        ``graph-contradiction`` is outside it and has its own class below: the
        gate refuses rows, and that code refuses none.
        """
        assert set(REFUSALS) == GATE_REFUSALS

    def test_the_codes_outside_the_gate_are_the_ones_that_drop_no_row(self):
        assert set(get_args(CatalogIssueCode)) - GATE_REFUSALS == {
            "graph-contradiction",
            "support-truncated",
        }

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


class TestWhatAConsumerMayRestOn:
    """The typed query, and the one rule behind "is this row a fact".

    Phase 4's acceptance criteria, each as a probe: a control on one
    interaction must not suppress an unknown on another, a mechanism must not
    imply a second factor, an unsupported or mis-scoped row must not silently
    settle anything, and a conflict stays visible with no side picked.
    """

    def row(self, **overrides):
        fields = {
            "subject": FLOW,
            "predicate": "mfa-requirement",
            "value": ABSENT,
            "basis": "stated",
            "support": span_for("we have not rolled out MFA"),
        }
        return Assertion(**{**fields, **overrides})

    def test_the_unprojected_predicates_are_read_off_the_registry(self):
        assert UNPROJECTED == {
            name for name, predicate in REGISTRY.items() if not predicate.projects_into
        }
        assert "mfa-requirement" in UNPROJECTED
        assert "authentication-mechanism" not in UNPROJECTED

    def test_a_predicate_nobody_asked_about_is_unasked(self):
        held = catalog([self.row()])

        assert answer(held, FLOW, "credential-rotation").standing == "unasked"
        assert answer(held, WEBHOOK, "mfa-requirement").standing == "unasked"

    def test_a_stated_absence_is_answered_and_held(self):
        held = catalog([self.row()])
        found = answer(held, FLOW, "mfa-requirement")

        assert found.standing == "answered"
        assert found.holding(ABSENT) == (self.row(),)
        assert settled(held) == (self.row(),)

    def test_a_mechanism_never_implies_a_second_factor(self):
        """The registry keys the question, so a stated mechanism on a flow
        leaves every other predicate on that flow unasked."""
        held = catalog([stated()])

        assert answer(held, FLOW, "mfa-requirement").standing == "unasked"
        assert answer(held, FLOW, "authorization-grant").standing == "unasked"

    def test_a_control_on_one_interaction_leaves_anothers_unknown_alone(self):
        rows = (
            (FLOW, "interaction", "place order"),
            (WEBHOOK, "interaction", "post settlement"),
        )
        unknown = Assertion(
            subject=WEBHOOK,
            predicate="mfa-requirement",
            value=UNKNOWN,
            basis="stated",
            reason="silent",
        )
        held = catalog([self.row(value="required"), unknown], rows=rows)

        assert answer(held, FLOW, "mfa-requirement").standing == "answered"
        assert answer(held, WEBHOOK, "mfa-requirement").standing == "unknown"
        assert unknown not in settled(held)

    def test_an_unsupported_row_is_set_aside_rather_than_read_as_a_fact(self):
        judged = self.row(assessment="unsupported", assessor="reviewer/1")
        found = answer(catalog([judged]), FLOW, "mfa-requirement")

        assert found.rows == (judged,)
        assert found.settled == ()
        assert found.standing == "unknown"

    def test_a_legacy_row_is_never_support(self):
        legacy = self.row(basis="legacy", support=[])
        assert answer(catalog([legacy]), FLOW, "mfa-requirement").settled == ()

    def test_a_conflict_settles_nothing_and_stays_visible(self):
        held = catalog([self.row(), self.row(value="required")])
        found = answer(held, FLOW, "mfa-requirement")

        assert found.standing == "conflicting"
        assert found.settled == ()
        assert len(found.conflicts) == 1
        assert settled(held) == ()

    def test_a_scoped_row_is_settled_for_its_scope_and_nothing_wider(self):
        admins = self.row(
            value="required",
            scope=[Qualifier(kind="principal", value="administrators")],
        )
        held = catalog([self.row(), admins])
        found = answer(held, FLOW, "mfa-requirement")

        assert found.standing == "answered"
        assert found.settled == (self.row(), admins)
        assert found.holding(ABSENT) == (self.row(),)
        assert not found.holding(ABSENT)[0].scope

    def test_an_inference_is_settled_and_labelled_by_its_basis(self):
        inferred = self.row(basis="inferred", support=[], explanation="password only")
        (found,) = answer(catalog([inferred]), FLOW, "mfa-requirement").settled

        assert found.basis == "inferred"

    def test_the_longest_identity_fits_the_ground_that_cites_it(self):
        """The ground's bound is measured off the schema, never guessed.

        Every part of an identity is bounded by a field on the row, so the
        longest one is composable: the longest subject, the longest
        registered predicate, a scoped key and the longest term value.
        """
        longest = Assertion(
            subject="principal:" + "x" * (300 - len("principal:")),
            predicate=max(REGISTRY, key=len),
            value="y" * 200,
            basis="stated",
            scope=[Qualifier(kind="principal", value="z")],
        )
        bound = next(
            meta.max_length
            for meta in Ground.model_fields["assertion"].metadata
            if hasattr(meta, "max_length")
        )

        assert len(assertion_id(longest)) <= bound

    def test_the_two_readers_agree(self):
        """``settled`` over the catalog is the union of every answer's settled rows."""
        admins = self.row(
            value="required",
            scope=[Qualifier(kind="principal", value="administrators")],
        )
        held = catalog([self.row(), admins, stated()])
        asked = dict.fromkeys(
            (entry.subject, entry.predicate) for entry in held.entries
        )

        assert list(settled(held)) == [
            entry
            for subject, predicate in asked
            for entry in answer(held, subject, predicate).settled
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

    def resolve(self, *rows, sources=None):
        return resolve_catalog(
            CatalogProposal(assertions=list(rows)),
            self.model(),
            SOURCES if sources is None else sources,
        )

    def quoting(self, quote):
        """One row quoting ``quote`` from :data:`REPEATED`, the twice-told source."""
        return self.row(
            predicate="transport-encryption",
            value="TLS",
            quotes=[QuoteProposal(source_label=REPEATED_LABEL, quote=quote)],
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

    def test_a_reference_value_naming_nothing_names_the_referent_type(self):
        """The repair pass reads this message, so it names what it asked for.

        It named the predicate's *value kind* instead, so every refusal of a
        reference read "names no reference this predicate takes" whatever the
        predicate pointed at. Its two siblings name the type.
        """
        _, issues = self.resolve(
            self.row(
                predicate="network-membership",
                subject_type="component",
                subject="process:storefront-api",
                value="a zone nobody declared",
            )
        )
        (issue,) = issues
        assert issue.code == "illegal-value"
        assert "names no zone" in issue.message

    def test_a_quote_the_source_holds_twice_drops_as_ambiguous(self):
        """#926: a repeated quote keeps its ambiguity rather than taking a side.

        The matcher answers the first placement, which is *an* answer. A row
        built on it cites the first copy and says nothing about which copy the
        statement rests on, so the gate refuses it — and the reason is its own,
        because "not found in the source" would send repair looking for a
        quote that is in there twice.
        """
        held, issues = self.resolve(
            self.quoting("the order service reads the bucket over TLS"),
            sources=REPEATED_SOURCES,
        )
        assert held.entries == []
        assert codes(issues) == ["ambiguous-span"]

    def test_a_quote_the_source_holds_once_still_resolves(self):
        """The other half of the bound: one placement is not ambiguous."""
        held, issues = self.resolve(
            self.quoting("Receipts land in a bucket"), sources=REPEATED_SOURCES
        )
        assert issues == []
        assert len(held.entries) == 1

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
        assert issues[0].row == 0

    def test_a_row_the_gate_would_refuse_never_enters_the_catalog(self):
        """The probe from #961: a grant with no scope was kept, then refused.

        The resolver built the row, the catalog counted it, and only a later
        ``catalog_issues`` said ``missing-scope``. Now the gate's own rules
        run on the built row, and the drop is recorded against the proposed
        row — so no subject is declared for it either.
        """
        grant = self.row(
            subject_type="principal",
            subject="Shopper",
            predicate="authorization-grant",
            value="write all orders",
        )
        held, issues = self.resolve(grant)

        assert held.entries == []
        assert held.subjects == []
        assert codes(issues) == ["missing-scope"]
        assert issues[0].row == 0
        assert catalog_issues(held, model=self.model(), sources=SOURCES) == []

    def test_one_row_can_draw_several_refusals_and_is_one_dropped_row(self):
        """Issues are reasons; rows dropped are distinct ``row`` values."""
        twice = self.row(
            subject_type="principal",
            subject="Shopper",
            predicate="authorization-grant",
            value="write all orders",
            basis="inferred",
            quotes=[],
        )
        held, issues = self.resolve(twice, self.row())

        assert len(held.entries) == 1
        assert codes(issues) == ["missing-premise", "missing-scope"]
        assert {issue.row for issue in issues} == {0}

    def test_a_row_citing_more_spans_than_the_cap_drops_rather_than_cut(self):
        """Five quotes of two fragments each locate ten spans; the cap is eight."""
        cut = self.row(
            quotes=[
                QuoteProposal(source_label=SOURCE_LABEL, quote=quote)
                for quote in (
                    "Shoppers … password",
                    "sign in … session",
                    "email … cookie",
                    "card processor … webhook",
                    "rolled out … accounts",
                )
            ]
        )
        held, issues = self.resolve(cut)

        assert held.entries == []
        assert codes(issues) == ["too-many-spans"]

    def test_two_orderings_of_one_proposal_build_one_catalog(self):
        """The probe from #961: the merged basis followed arrival order.

        An inferred row and a stated row of one identity merge to one row.
        It reads ``stated`` whichever arrived first, because a source that
        states the value outranks a reader that inferred it, and the two
        catalogs are equal.
        """
        inferred = self.row(basis="inferred", quotes=[], explanation="from the flow")
        first, _ = self.resolve(inferred, self.row())
        second, _ = self.resolve(self.row(), inferred)

        assert first.entries[0].basis == "stated"
        assert first == second

    def test_a_proposal_over_the_cap_is_refused_before_a_row_is_read(self):
        """The bound lives here, because the schema cannot carry it.

        A root array carrying ``maxItems`` is a shape one vendor refuses, so
        the cap is enforced where code reads the output rather than where a
        provider has to accept it.
        """
        rows = [
            self.row(value=f"mechanism {index}") for index in range(MAX_ASSERTIONS + 1)
        ]
        held, issues = self.resolve(*rows)

        assert held.entries == []
        assert codes(issues) == ["too-many-assertions"]


class TestTheProjection:
    """What one graph attribute would hold, built from the rows.

    The rule is that the projection **never picks**. Where the catalog holds
    more than one string's worth, it writes ``unknown`` and says which kind of
    loss that was.
    """

    def rows(self, *entries):
        return catalog(list(entries))

    def row(self, **overrides):
        fields = {
            "subject": FLOW,
            "predicate": "transport-encryption",
            "value": "TLS",
            "basis": "stated",
            "support": span_for("Shoppers sign in"),
        }
        return Assertion(**{**fields, **overrides})

    def test_one_unscoped_value_is_the_value(self):
        (projected,) = project(self.rows(self.row()))
        assert (projected.attribute, projected.value, projected.reason) == (
            "encryption_in_transit",
            "TLS",
            "stated",
        )

    def test_a_stated_absence_is_the_word_the_graph_reads_as_absent(self):
        """``none`` and ``absent`` are one fact in two vocabularies."""
        (projected,) = project(self.rows(self.row(value=ABSENT)))
        assert (projected.value, projected.reason) == ("none", "absent")

    def test_two_values_under_one_attribute_project_to_unknown(self):
        (projected,) = project(self.rows(self.row(), self.row(value=ABSENT)))
        assert (projected.value, projected.reason) == (UNKNOWN, "several-values")

    def test_a_scoped_value_projects_to_unknown(self):
        """A string cannot carry the qualifier, and writing it bare would
        generalise a control the source scoped."""
        scoped = self.row(scope=[Qualifier(kind="environment", value="staging")])
        (projected,) = project(self.rows(scoped))
        assert (projected.value, projected.reason) == (UNKNOWN, "scoped")

    def test_two_compatible_predicates_on_one_field_are_compatible(self):
        """A mechanism and the credential it presents share `authentication`,
        and both hold: the projection declines without calling it a conflict."""
        mechanism = self.row(predicate="authentication-mechanism", value="a password")
        credential = self.row(
            predicate="credential-presented", value="credential:session-cookie"
        )
        held = AssertionCatalog(
            subjects=[
                *subjects((FLOW, "interaction", "place order")),
                *subjects(
                    ("credential:session-cookie", "credential", "session cookie")
                ),
            ],
            entries=[mechanism, credential],
        )
        (projected,) = project(held)
        assert (projected.attribute, projected.reason) == (
            "authentication",
            "compatible",
        )

    def test_a_stated_absence_beside_a_presented_credential_is_several_predicates(
        self,
    ):
        """No mechanism, and a credential presented: the two cannot both hold."""
        mechanism = self.row(predicate="authentication-mechanism", value=ABSENT)
        credential = self.row(
            predicate="credential-presented", value="credential:session-cookie"
        )
        held = AssertionCatalog(
            subjects=[
                *subjects((FLOW, "interaction", "place order")),
                *subjects(
                    ("credential:session-cookie", "credential", "session cookie")
                ),
            ],
            entries=[mechanism, credential],
        )
        (projected,) = project(held)
        assert projected.reason == "several-predicates"

    def test_an_unknown_row_projects_the_sentinel(self):
        silent = self.row(value=UNKNOWN, reason="silent", support=[])
        (projected,) = project(self.rows(silent))
        assert (projected.value, projected.reason) == (UNKNOWN, "unknown")

    def test_an_attribute_no_row_reaches_is_absent_from_the_result(self):
        """Filling it would assert silence rather than report it."""
        projected = project(self.rows(self.row()))
        assert [one.attribute for one in projected] == ["encryption_in_transit"]

    def test_a_projection_names_the_rows_behind_it(self):
        (projected,) = project(self.rows(self.row(), self.row(value=ABSENT)))
        assert len(projected.rows) == 2
        assert all(one.startswith("assertion:") for one in projected.rows)

    def test_a_row_found_unsupported_projects_no_value(self):
        """The probe from #961: an unsupported row projected as stated."""
        judged = self.row(assessment="unsupported", assessor="audit")
        (projected,) = project(self.rows(judged))
        assert (projected.value, projected.reason) == (UNKNOWN, "unsupported")
        assert projected.rows == (assertion_id(judged),)

    def test_a_row_left_unresolved_projects_no_value(self):
        judged = self.row(assessment="unresolved", assessor="audit")
        (projected,) = project(self.rows(judged))
        assert (projected.value, projected.reason) == (UNKNOWN, "unsupported")

    def test_a_set_aside_row_is_not_a_second_value(self):
        """The standing row's value projects; the refused one neither adds nor blocks."""
        refused = self.row(value="none", assessment="unsupported", assessor="audit")
        (projected,) = project(self.rows(self.row(), refused))
        assert (projected.value, projected.reason) == ("TLS", "stated")

    def test_a_subject_of_this_layer_s_own_projects_into_nothing(self):
        """A principal has no element, so no attribute of one can hold its facts."""
        held = AssertionCatalog(
            subjects=subjects(("credential:build-token", "credential", "build token")),
            entries=[
                Assertion(
                    subject="credential:build-token",
                    predicate="credential-rotation",
                    value="not-rotated",
                    basis="stated",
                    support=span_for("Shoppers sign in"),
                )
            ],
        )
        assert project(held) == ()

    def test_the_order_is_stable(self):
        held = self.rows(self.row(), self.row(predicate="authentication-mechanism"))
        assert [one.attribute for one in project(held)] == [
            "authentication",
            "encryption_in_transit",
        ]


class TestWhatAProjectionWillNotReach:
    """The two ways a legal row has no attribute to land in."""

    def test_a_predicate_whose_field_belongs_to_another_element_type(self):
        """``authentication-mechanism`` takes a component; only a flow has the field.

        Without this the projection named an attribute the element does not
        declare, and the comparison beside it read the absent field as
        ``unknown``.
        """
        held = AssertionCatalog(
            subjects=subjects(("process:order-service", "component", "order service")),
            entries=[
                Assertion(
                    subject="process:order-service",
                    predicate="authentication-mechanism",
                    value="a service account",
                    basis="stated",
                    support=span_for("Shoppers sign in"),
                )
            ],
        )
        assert project(held) == ()

    def test_a_store_predicate_on_a_store_does_reach_its_field(self):
        """The positive control: the same predicate family, on the type that has it."""
        held = AssertionCatalog(
            subjects=subjects(("store:receipts", "component", "receipts")),
            entries=[
                Assertion(
                    subject="store:receipts",
                    predicate="storage-encryption",
                    value="a customer-managed key",
                    basis="stated",
                    support=span_for("Shoppers sign in"),
                )
            ],
        )
        (projected,) = project(held)
        assert projected.attribute == "encryption_at_rest"

    def test_a_subject_the_catalog_does_not_type_reaches_nothing(self):
        """Graph-bound is the subject table's answer, not a prefix's."""
        held = AssertionCatalog(
            subjects=[],
            entries=[
                Assertion(
                    subject=FLOW,
                    predicate="transport-encryption",
                    value="TLS",
                    basis="stated",
                    support=span_for("Shoppers sign in"),
                )
            ],
        )
        assert project(held) == ()


PROJECTIONS: dict[str, str] = {
    "stated": "one unscoped value is the value it holds",
    "absent": "a stated absence is the word the graph reads as absent",
    "unknown": "only unknown rows, none of them a hedge, leave it unsettled",
    "hedged": "only unknown rows, and a speaker voiced the doubt",
    "scoped": "a string cannot carry the qualifier the source attached",
    "several-values": "picking between two values would drop one",
    "several-predicates": "two predicates' facts on one field cannot both hold",
    "compatible": "several values that all hold, which one string cannot carry",
    "unsupported": "a row found unsupported, or left unresolved, states nothing",
    "legacy": "a row imported from before this layer is never support",
}


def test_every_assessment_says_whether_it_projects():
    """``PROJECTS_UNDER`` answers ``Assessment``, with nothing left over."""
    assert set(PROJECTS_UNDER) == set(get_args(Assessment))


def test_every_basis_has_a_merge_rank():
    """``BASIS_RANK`` answers ``Basis``, with nothing left over."""
    assert set(BASIS_RANK) == set(get_args(Basis))


def test_every_projection_reason_is_explained():
    """The table answers ``ProjectionReason``, with nothing left over.

    A reason added to the enum fails here until somebody says what it means,
    which is the same guard ``REFUSALS`` puts on the gate's codes.
    """
    assert set(PROJECTIONS) == set(get_args(ProjectionReason))


class TestWhatTheFirstLiveRunFound:
    """Two defects a $0.002 pre-flight found that no fixture had.

    Both are about the second spelling of one thing: a model writing a name
    where code wanted an identifier, and code writing an identifier where a
    field holds prose.
    """

    def model(self):
        return TestResolvingAProposal().model()

    def resolve(self, *rows):
        return resolve_catalog(
            CatalogProposal(assertions=list(rows)), self.model(), SOURCES
        )

    def test_a_reference_written_as_a_name_resolves(self):
        """The live run wrote ``core services`` where the zone is ``boundary:...``.

        An **Element ID** is the slug of the element's name, so comparing the
        slugs asks the question the ID derivation already answers.
        """
        held, issues = self.resolve(
            AssertionProposal(
                subject_type="component",
                subject="storefront API",
                predicate="network-membership",
                value="app",
                basis="stated",
                quotes=[
                    QuoteProposal(source_label=SOURCE_LABEL, quote="Shoppers sign in")
                ],
            )
        )

        assert issues == []
        assert held.entries[0].subject == "process:storefront-api"
        assert held.entries[0].value == "boundary:app"

    def test_an_ambiguous_name_resolves_to_nothing(self):
        """Guessing which element a word meant is the thing this must not do."""
        labels = {"a:one": "shopper", "b:two": "Shopper"}
        assert _named("shopper", labels, {"a", "b"}) == ""
        assert _named("!!!", {"a:one": "shopper"}, {"a"}) == ""

    def test_a_name_is_ambiguous_only_among_the_types_asked_for(self):
        """A zone named after the entity inside it is ordinary (#961).

        Asked for the *zone* ``card processor``, the entity of that name is
        not a candidate, so the one boundary resolves. Asked across both
        types, the same word is ambiguous and resolves to nothing.
        """
        labels = {
            "entity:card-processor": "card processor",
            "boundary:card-processor": "card processor",
        }
        assert _named("card processor", labels, {"boundary"}) == (
            "boundary:card-processor"
        )
        assert _named("card processor", labels, {"entity", "boundary"}) == ""

    def test_a_credential_reference_projects_its_label(self):
        """``authentication`` holds prose, so an ID there is a value nobody writes."""
        held, _ = self.resolve(
            AssertionProposal(
                subject_type="interaction",
                subject=FLOW,
                predicate="credential-presented",
                value="session cookie",
                basis="stated",
                quotes=[QuoteProposal(source_label=SOURCE_LABEL, quote="a session")],
            )
        )

        (projected,) = project(held)
        assert (projected.attribute, projected.value) == (
            "authentication",
            "session cookie",
        )

    def test_a_zone_reference_projects_its_identifier(self):
        """``trust_zone`` holds an Element ID by the validity gate's own rule."""
        held, _ = self.resolve(
            AssertionProposal(
                subject_type="component",
                subject="process:storefront-api",
                predicate="network-membership",
                value="boundary:app",
                basis="stated",
                quotes=[
                    QuoteProposal(source_label=SOURCE_LABEL, quote="Shoppers sign in")
                ],
            )
        )

        (projected,) = project(held)
        assert (projected.attribute, projected.value) == ("trust_zone", "boundary:app")


class TestTheOneReaderOfAReferentType:
    """``referent_type`` answers for every predicate, not only a referring one.

    Three sites read "which subject type does this reference point at": this
    function, ``prompts._value_form`` and ``evals.harness.replay``. The last
    two spelled ``next(iter(predicate.refers_to))`` again because this one was
    private, and that spelling has no answer for the 12 predicates that refer
    to nothing.
    """

    def test_a_referring_predicate_names_its_one_type(self):
        assert referent_type(REGISTRY["credential-presented"]) == "credential"
        assert referent_type(REGISTRY["network-membership"]) == "zone"

    def test_a_predicate_that_refers_to_nothing_answers_none(self):
        """The shape the second readers missed. ``next(iter(...))`` raised here."""
        assert referent_type(REGISTRY["mfa-requirement"]) is None
        assert referent_type(REGISTRY["credential-custody"]) is None

    def test_every_registered_predicate_has_an_answer(self):
        """No predicate makes this raise, which is what the second readers did."""
        for name, predicate in REGISTRY.items():
            found = referent_type(predicate)
            assert (found is not None) == bool(predicate.refers_to), name

    def test_a_term_predicate_with_a_bad_term_draws_illegal_value(self):
        """Why the None matters: this row reaches the replay's referent branch.

        ``illegal-value`` is not a reference predicate's code alone, so a
        reader keyed on it has to answer for a predicate that refers to
        nothing.
        """
        row = Assertion(
            subject=FLOW,
            predicate="mfa-requirement",
            value="sometimes",
            basis="inferred",
            explanation="x",
        )
        assert codes(catalog_issues(catalog([row]))) == ["illegal-value"]
        assert referent_type(REGISTRY[row.predicate]) is None


class TestAGraphAttributeAndItsRowsStatingOpposites:
    """The audit's own defect: an explicit absence standing in the graph as a control.

    ``contradictions`` is the one reader, and what it must *not* do matters as
    much as what it must: two wordings of one mechanism are one answer spelled
    twice, and reporting those would put a finding on every run.
    """

    def model(self, authentication="session cookie"):
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
                        "authentication": authentication,
                        "data_description": "an order",
                        "encryption_in_transit": "TLS",
                    }
                ],
            }
        )

    def test_a_stated_absence_under_a_graph_control_is_reported(self):
        """The defect #926 was opened for, now visible in the report."""
        held = catalog([stated(value=ABSENT)])
        found = contradictions(held, self.model())
        assert [(entry.attribute, entry.carried) for entry in found] == [
            ("authentication", "session cookie")
        ]

    def test_a_graph_absence_under_a_stated_row_is_reported_too(self):
        """The mirror, because either side can be the wrong one."""
        found = contradictions(catalog([stated()]), self.model(authentication="none"))
        assert len(found) == 1 and found[0].projected == "email and password"

    def test_two_wordings_of_one_mechanism_are_not_a_contradiction(self):
        """Otherwise every run reports one, and the finding means nothing."""
        assert contradictions(catalog([stated()]), self.model()) == ()

    def test_silence_on_either_side_is_not_an_opposite(self):
        """A graph attribute nobody stated is the projection doing its job."""
        assert contradictions(catalog([stated()]), self.model(UNKNOWN)) == ()

    def test_an_issue_names_both_values_so_a_reader_can_see_the_pair(self):
        """Which of the two is wrong is not decidable here.

        The projected half is spelled ``"none"`` rather than ``absent``: a
        projection writes the word the **System Model** uses, which is what
        :func:`~analysis_service.analysis.control_state` reads as an absence.
        The two spellings of one fact are why this is asserted rather than
        assumed.
        """
        issues = contradiction_issues(catalog([stated(value=ABSENT)]), self.model())
        assert codes(issues) == ["graph-contradiction"]
        assert "'session cookie'" in issues[0].message
        assert f"'{ABSENT_WORD}'" in issues[0].message

    def test_the_row_stays_settled_because_it_is_not_a_refused_row(self):
        """A contradiction makes a fact visible; it never drops one."""
        held = catalog([stated(value=ABSENT)])
        assert len(settled(held)) == 1
        assert all(
            issue.row is None for issue in contradiction_issues(held, self.model())
        )


class TestTheProjectionBecomesTheGraphsValue:
    """ADR 0034's migration, applied where the catalog answers and nowhere else.

    What it must *not* do carries the weight. A projection with nothing to add —
    rows all unknown, rows imported as legacy, values that all hold — leaves
    what extraction wrote. One whose rows say the attribute is *not settled* —
    a conflict, a scope, a row set aside — writes a qualified ``unknown``
    rather than leave a definite value authoritative over them (#926).
    """

    def catalog_of(
        self, value, *, basis="stated", predicate="authentication-mechanism"
    ):
        return catalog(
            [
                Assertion(
                    subject=FLOW,
                    predicate=predicate,
                    value=value,
                    basis=basis,
                    explanation="" if basis == "stated" else "no login step is named",
                    support=span_for("sign in with email and password")
                    if basis == "stated"
                    else [],
                )
            ]
        )

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

    def _flow(self, model):
        return model.data_flows[0]

    def test_a_stated_absence_replaces_a_vague_graph_value(self):
        """The 7 corrections: the graph hid an absence the sources stated."""
        updated, applied = apply_projection(self.model(), self.catalog_of(ABSENT))

        assert self._flow(updated).authentication == ABSENT_WORD
        assert [entry.reason for entry in applied] == ["absent"]

    def test_a_compatible_projection_leaves_the_attribute_alone(self):
        """Two predicates on one attribute that both hold: one string cannot
        carry both, and the graph's own value contradicts neither."""
        held = catalog(
            [
                stated(),
                Assertion(
                    subject=FLOW,
                    predicate="credential-presented",
                    value="credential:api-key",
                    basis="stated",
                    support=span_for("sign in with email and password"),
                ),
            ],
            rows=(
                (FLOW, "interaction", "place order"),
                ("credential:api-key", "credential", "api key"),
            ),
        )
        updated, applied = apply_projection(self.model(), held)

        assert self._flow(updated).authentication == "session cookie"
        assert applied == ()

    def test_an_empty_catalog_returns_the_model_unchanged(self):
        updated, applied = apply_projection(self.model(), AssertionCatalog())

        assert updated == self.model() and applied == ()

    def test_an_inferred_projection_records_an_assumption(self):
        """A value this service inferred into a graph attribute needs its record."""
        updated, _ = apply_projection(
            self.model(), self.catalog_of(ABSENT, basis="inferred")
        )

        assert [(a.element_id, a.attribute) for a in updated.assumptions] == [
            (FLOW, "authentication")
        ]

    def test_a_stated_projection_records_no_assumption(self):
        """The sources said it, so this service inferred nothing."""
        updated, _ = apply_projection(self.model(), self.catalog_of(ABSENT))

        assert updated.assumptions == []

    def test_a_projection_the_gate_would_refuse_is_discarded_whole(self):
        """Fail closed: a zone reference naming no boundary would dangle."""
        held = catalog(
            [
                Assertion(
                    subject="process:storefront-api",
                    predicate="network-membership",
                    value="boundary:nowhere",
                    basis="stated",
                    support=span_for("sign in with email and password"),
                )
            ],
            rows=(("process:storefront-api", "component", "storefront API"),),
        )
        updated, applied = apply_projection(self.model(), held)

        assert updated.processes[0].trust_zone == "boundary:app"
        assert applied == ()


def test_every_projection_reason_has_an_effect():
    """``PROJECTION_EFFECT`` answers ``ProjectionReason``, with nothing left over."""
    assert set(PROJECTION_EFFECT) == set(get_args(ProjectionReason))


def test_every_qualifying_reason_says_what_happened():
    """A reason that qualifies, with no words for the qualification, would
    raise at the first projection that reached it."""
    assert set(_QUALIFIED_BECAUSE) == {
        reason for reason, effect in PROJECTION_EFFECT.items() if effect == "qualify"
    }


def test_every_projected_field_says_how_a_qualified_unknown_is_spelled():
    """A field a predicate projects into, with no spelling, would raise at the
    first conflict on it rather than here."""
    assert set(QUALIFIED_SPELLING) == set(projection_fields().values())


class TestTheImplementationAudit:
    """The seven defects #926's implementation audit reproduced, one class each.

    Each test is the audit's own reproduction, kept as the regression test: a
    row the gate refused still reached the graph, a row the projection could not
    fit left a definite value standing over it, a legal row reached no reader,
    and a bound was crossed in silence.
    """

    model = TestTheProjectionBecomesTheGraphsValue.model

    def _flow(self, model):
        return model.data_flows[0]

    def _cites(self, model, held, row):
        return assertion_id(row) in evidence_catalog(model, held)

    # --- 2: a projection that does not settle leaves no definite value ---

    @pytest.mark.parametrize(
        ("entries", "reason"),
        [
            ([stated(), stated(value=ABSENT)], "several-values"),
            ([stated(scope=[Qualifier(kind="principal", value="admins")])], "scoped"),
            ([stated(assessment="unsupported", assessor="human:test")], "unsupported"),
        ],
        ids=["conflict", "scoped", "unsupported"],
    )
    def test_an_unsettled_projection_writes_a_qualified_unknown(self, entries, reason):
        updated, applied = apply_projection(self.model(), catalog(entries))

        (projection,) = applied
        assert projection.reason == reason
        written = self._flow(updated).authentication
        assert written.startswith(f"{UNKNOWN}; ")
        assert unknown_evidence_ref(FLOW, "authentication") in evidence_catalog(
            updated, catalog(entries)
        )

    def test_a_qualified_unknown_names_what_the_rows_state(self):
        scoped = stated(scope=[Qualifier(kind="principal", value="admins")])
        updated, _ = apply_projection(self.model(), catalog([scoped]))

        assert "email and password (principal admins)" in (
            self._flow(updated).authentication
        )

    def test_a_scoped_row_is_cited_as_itself(self):
        """The attribute cannot carry it, so the table does."""
        scoped = stated(scope=[Qualifier(kind="principal", value="admins")])
        held = catalog([scoped])
        updated, _ = apply_projection(self.model(), held)

        assert self._cites(updated, held, scoped)

    def test_a_bare_field_qualifies_to_the_sentinel_alone(self):
        """``trust_zone`` holds an Element ID, so nothing may follow the word."""
        rows = (("process:storefront-api", "component", "storefront API"),)
        held = catalog(
            [
                stated(
                    subject="process:storefront-api",
                    predicate="network-membership",
                    value=zone,
                )
                for zone in ("boundary:app", "boundary:internet")
            ],
            rows=(
                *rows,
                ("boundary:app", "zone", "app"),
                ("boundary:internet", "zone", "internet"),
            ),
        )
        updated, applied = apply_projection(self.model(), held)

        assert [p.reason for p in applied] == ["several-values"]
        assert updated.processes[0].trust_zone == UNKNOWN

    # --- A hedge is not silence (#926 review of this branch) ---

    def hedge(self, **overrides):
        return stated(
            **{
                "value": UNKNOWN,
                "reason": "hedged",
                "support": span_for("we have not rolled out MFA"),
                **overrides,
            }
        )

    def test_a_hedge_qualifies_a_definite_value(self):
        """The sources said they are unsure; extraction's value does not stand."""
        held = catalog([self.hedge()])
        updated, applied = apply_projection(self.model(), held)

        assert [p.reason for p in applied] == ["hedged"]
        written = self._flow(updated).authentication
        assert written.startswith(f"{UNKNOWN}; the sources voice uncertainty")
        assert '"we have not rolled out MFA"' in written
        assert unknown_evidence_ref(FLOW, "authentication") in evidence_catalog(
            updated, held
        )

    def test_a_hedge_without_words_still_qualifies(self):
        updated, _ = apply_projection(self.model(), catalog([self.hedge(support=[])]))

        assert self._flow(updated).authentication == (
            f"{UNKNOWN}; the sources voice uncertainty about this"
        )

    @pytest.mark.parametrize("reason", ["silent", "unmeasured", "truncated"])
    def test_silence_leaves_the_attribute(self, reason):
        """The pass not finding an answer is not the sources giving none."""
        held = catalog([self.hedge(reason=reason, support=[])])
        updated, applied = apply_projection(self.model(), held)

        assert [p.reason for p in project(held)] == ["unknown"]
        assert applied == ()
        assert self._flow(updated).authentication == "session cookie"

    def test_one_hedge_among_silent_rows_is_a_hedge(self):
        held = catalog(
            [
                self.hedge(),
                self.hedge(
                    reason="silent",
                    support=[],
                    scope=[Qualifier(kind="principal", value="admins")],
                ),
            ]
        )
        assert [p.reason for p in project(held)] == ["hedged"]

    def test_a_stated_value_outranks_a_hedge(self):
        """A value beside a doubt is the ordinary stated projection."""
        held = catalog([stated(), self.hedge()])
        assert [p.reason for p in project(held)] == ["stated"]

    # --- 5: legacy is never support, for the projection either ---

    def test_a_legacy_row_does_not_project(self):
        held = catalog([stated(basis="legacy", support=[])])
        updated, applied = apply_projection(self.model(), held)

        assert [p.reason for p in project(held)] == ["legacy"]
        assert applied == ()
        assert self._flow(updated).authentication == "session cookie"

    @pytest.mark.parametrize("basis", sorted(get_args(Basis)))
    @pytest.mark.parametrize("assessment", sorted(get_args(Assessment)))
    def test_the_projection_and_settled_agree_on_which_rows_count(
        self, basis, assessment
    ):
        """One eligibility rule, asked by both, over every basis and assessment."""
        row = stated(
            basis=basis,
            assessment=assessment,
            assessor="" if assessment == "unchecked" else "human:test",
            support=[] if basis == "legacy" else stated().support,
            explanation="" if basis in ("stated", "legacy") else "because",
        )
        (projection,) = project(catalog([row]))

        assert (
            (projection.reason == "stated")
            == admissible(row)
            == bool(settled(catalog([row])))
        )

    # --- 3: a refusal quarantines the row before any reader sees it ---

    def test_a_stale_row_is_not_projected_or_cited(self):
        moved = {label: text + " changed" for label, text in SOURCES.items()}
        record = AssertionRecord.over(
            catalog([stated()]), self.model(), moved, proposed=1
        )

        assert codes(record.issues) == ["stale-digest"]
        assert record.catalog.entries == []
        assert record.refused_rows() == 1
        assert apply_projection(self.model(), record.catalog)[1] == ()

    def test_a_dropped_premise_strands_the_inference_on_it(self):
        """Quarantine runs to a fixed point: a row resting on a refused one goes."""
        premise = stated(support=[])
        inference = stated(
            value="company SSO",
            basis="inferred",
            support=[],
            explanation="rests on the first",
            premises=[assertion_id(premise)],
        )
        record = AssertionRecord.over(
            catalog([premise, inference]), self.model(), SOURCES, proposed=2
        )

        assert record.catalog.entries == []
        assert set(codes(record.issues)) == {
            "unsupported-assertion",
            "dangling-premise",
        }

    @pytest.mark.parametrize("code", sorted(CATALOG_REFUSALS))
    def test_a_refused_catalog_keeps_nothing(self, code):
        _, held, reads = REFUSALS[code]
        assert quarantined(held, catalog_issues(held, **reads)).entries == []

    def test_a_graph_contradiction_quarantines_nothing(self):
        record = AssertionRecord.over(
            catalog([stated(value=ABSENT)]), self.model(), SOURCES, proposed=1
        )

        assert codes(record.issues) == ["graph-contradiction"]
        assert len(record.catalog.entries) == 1
        assert record.refused_rows() == 0

    # --- 4: a legal row reaches a reader whether or not its field exists ---

    def test_a_mechanism_on_a_component_is_cited_as_itself(self):
        row = stated(subject="process:storefront-api")
        held = catalog(
            [row], rows=(("process:storefront-api", "component", "storefront API"),)
        )

        assert catalog_issues(held, model=self.model(), sources=SOURCES) == []
        assert projected_attribute(row.predicate, row.subject) == ""
        assert project(held) == ()
        assert self._cites(self.model(), held, row)

    def test_a_projected_row_the_graph_holds_is_not_cited_twice(self):
        held = catalog([stated()])
        updated, _ = apply_projection(self.model(), held)

        assert self._flow(updated).authentication == "email and password"
        assert offered(held, updated) == ()

    def test_a_projected_row_the_graph_does_not_hold_is_cited(self):
        """A projection discarded whole leaves its rows with no other reader."""
        held = catalog([stated()])

        assert offered(held, self.model()) == (stated(),)

    def test_compatible_rows_are_cited_as_themselves(self):
        mechanism = stated()
        credential = stated(
            predicate="credential-presented", value="credential:session-cookie"
        )
        held = catalog(
            [mechanism, credential],
            rows=(
                (FLOW, "interaction", "place order"),
                ("credential:session-cookie", "credential", "session cookie"),
            ),
        )
        updated, _ = apply_projection(self.model(), held)

        assert self._flow(updated).authentication == "session cookie"
        assert offered(held, updated) == (mechanism, credential)

    # --- 6: a merge that crosses the span bound says so ---

    def test_a_merge_past_the_span_bound_is_recorded(self):
        passages = [
            f"Segment {index} says password authentication applies."
            for index in range(MAX_SPANS + 1)
        ]
        rows = [
            AssertionProposal(
                subject_type="interaction",
                subject=FLOW,
                predicate="authentication-mechanism",
                value="password",
                basis="stated",
                quotes=[QuoteProposal(source_label="test", quote=passage)],
            )
            for passage in passages
        ]
        record = AssertionRecord.of(
            CatalogProposal(assertions=rows), self.model(), {"test": " ".join(passages)}
        )

        (entry,) = record.catalog.entries
        assert len(entry.support) == MAX_SPANS
        assert codes(record.issues) == ["support-truncated"]
        assert record.refused_rows() == 0

    def test_a_merge_outside_the_resolver_is_left_for_the_gate(self):
        """``merged`` does not cut, so the patch route refuses rather than loses."""
        many = [
            f"Segment {index} says password authentication applies."
            for index in range(MAX_SPANS + 1)
        ]
        text = " ".join(many)
        rows = [stated(support=[support_span(quote, "test", text)]) for quote in many]
        joined = catalog(rows[:1])
        for row in rows[1:]:
            joined = merged(joined, catalog([row]))

        (entry,) = joined.entries
        assert len(entry.support) == MAX_SPANS + 1
        assert "too-many-spans" in codes(catalog_issues(joined))
