"""Every assertion predicate with no graph field has a stated reader (#1242).

A fact of such a predicate reaches a lane only as an evidence row, so each
**Framework Package** states which candidate rule turns it into a lead, or why
none does. Three halves: the gate refuses a table that does not answer for the
registry; a rule the table names really fires on the predicate it is named
against; and each new rule's edges, where a lead that fires on the wrong row
means nothing.
"""

from __future__ import annotations

import dataclasses
from types import MappingProxyType

import pytest

from analysis_service.assertions import (
    ABSENT,
    Assertion,
    AssertionCatalog,
    Subject,
    SupportSpan,
)
from analysis_service.candidates import generate_candidates
from analysis_service.frameworks import (
    PACKAGES,
    FrameworkPackageError,
    NoRule,
    unprojected_predicates,
    validate_package,
)
from analysis_service.sources import text_digest
from analysis_service.system_model import UNKNOWN
from tests.factories import PROJECT_ROOT, valid_model

STRIDE = PACKAGES["stride"]
STRIDE_ROOT = PROJECT_ROOT / "frameworks" / "stride"

#: For each predicate a rule may be named against: whether the row sits on the
#: flow itself or on a credential the flow presents, and the value that leads.
#: A package naming a rule for a predicate missing here fails with a KeyError,
#: so a new reader cannot be declared without a fixture that fires it.
LEADS: dict[str, tuple[str, str]] = {
    "mfa-requirement": ("flow", ABSENT),
    "origin-verification": ("flow", ABSENT),
    "signature-verification": ("flow", ABSENT),
    "destination-verification": ("flow", ABSENT),
    "credential-sharing": ("credential", "shared"),
    "credential-rotation": ("credential", "not-rotated"),
    "credential-expiry": ("credential", "does-not-expire"),
    "credential-revocation": ("credential", ABSENT),
}

CREDENTIAL = "credential:api-key"
PRINCIPAL = "principal:customers"
SOURCE_LABEL = "Notes"
SOURCE = "Customers sign in with a shared API key."


@pytest.fixture
def model():
    return valid_model()


@pytest.fixture
def flow(model):
    return model.data_flows[0]


def fired(model, rule_id, catalog, package=STRIDE):
    return [
        candidate
        for candidate_set in generate_candidates(
            model, package.lanes, package.rules, catalog
        ).values()
        for candidate in candidate_set.candidates
        if candidate.rule_id == rule_id
    ]


def row(subject, predicate, value, **fields):
    fields.setdefault("basis", "stated")
    if value == UNKNOWN:
        fields.setdefault("reason", "silent")
    else:
        fields.setdefault("explanation", "the sources say so")
    return Assertion(subject=subject, predicate=predicate, value=value, **fields)


def catalog_on(flow, predicate, value, *, presented=True):
    """A catalog stating ``predicate`` about ``flow`` or the credential it presents."""
    where, _ = LEADS[predicate]
    subjects = [Subject(id=flow.id, type="interaction", label=flow.name)]
    entries = []
    if where == "flow":
        entries.append(row(flow.id, predicate, value))
    else:
        subjects.append(Subject(id=CREDENTIAL, type="credential", label="API key"))
        entries.append(row(CREDENTIAL, predicate, value))
        if presented:
            entries.append(row(flow.id, "credential-presented", CREDENTIAL))
    return AssertionCatalog(subjects=subjects, entries=entries)


def named_readers():
    return [
        pytest.param(package, predicate, reader, id=f"{name}-{predicate}")
        for name, package in PACKAGES.items()
        for predicate, reader in package.predicate_readers.items()
        if isinstance(reader, str)
    ]


# --- The gate -----------------------------------------------------------------


def test_every_package_answers_for_every_predicate_with_no_graph_field():
    for package in PACKAGES.values():
        assert set(package.predicate_readers) == unprojected_predicates()


def with_readers(**changes):
    readers = dict(STRIDE.predicate_readers)
    for predicate, reader in changes.items():
        if reader is None:
            del readers[predicate.replace("_", "-")]
        else:
            readers[predicate.replace("_", "-")] = reader
    return dataclasses.replace(STRIDE, predicate_readers=MappingProxyType(readers))


@pytest.mark.parametrize(
    ("package", "message"),
    [
        (with_readers(credential_sharing=None), "omits predicates"),
        (with_readers(transport_encryption=NoRule("x")), "projected or not"),
        (with_readers(credential_sharing="no-such-rule"), "does not declare"),
        (with_readers(credential_sharing=NoRule(" ")), "gives no reason"),
    ],
    ids=["omitted", "projected", "unknown-rule", "blank-reason"],
)
def test_the_gate_refuses_a_table_that_does_not_answer(package, message):
    with pytest.raises(FrameworkPackageError) as caught:
        validate_package(package, STRIDE_ROOT)
    assert message in str(caught.value)


# --- A named rule fires on the predicate it is named against -----------------


@pytest.mark.parametrize(("package", "predicate", "rule_id"), named_readers())
def test_a_named_rule_fires_on_its_predicate(model, flow, package, predicate, rule_id):
    _, value = LEADS[predicate]
    hits = fired(model, rule_id, catalog_on(flow, predicate, value), package)
    assert [hit.element_ids[0] for hit in hits] == [flow.id]


@pytest.mark.parametrize(("package", "predicate", "rule_id"), named_readers())
def test_an_unknown_row_fires_nothing(model, flow, package, predicate, rule_id):
    """Silence about a control is a question, never a lead."""
    assert fired(model, rule_id, catalog_on(flow, predicate, UNKNOWN), package) == []


# --- The edges of the new rules ------------------------------------------------


class TestACredentialReachesAFlow:
    """A credential rule places its lead through who presents the credential."""

    def test_a_credential_no_flow_presents_leads_nowhere(self, model, flow):
        held = catalog_on(flow, "credential-sharing", "shared", presented=False)
        assert fired(model, "repudiation-shared-credential", held) == []

    def test_a_per_principal_credential_does_not_lead(self, model, flow):
        held = catalog_on(flow, "credential-sharing", "per-principal")
        assert fired(model, "repudiation-shared-credential", held) == []

    def test_an_expiring_credential_does_not_lead(self, model, flow):
        held = catalog_on(flow, "credential-expiry", "expires")
        assert fired(model, "spoofing-standing-credential", held) == []

    def test_a_principal_presents_on_the_flows_its_element_originates(
        self, model, flow
    ):
        """The path the sources usually take: who holds a key, not which call."""
        span = SupportSpan(
            source_label=SOURCE_LABEL,
            digest=text_digest(SOURCE),
            start=0,
            end=len("Customers"),
            quote="Customers",
        )
        held = AssertionCatalog(
            subjects=[
                Subject(id=PRINCIPAL, type="principal", label="customers"),
                Subject(id=flow.source, type="component", label="Customer"),
                Subject(id=CREDENTIAL, type="credential", label="API key"),
            ],
            entries=[
                row(PRINCIPAL, "represented-by", flow.source, support=[span]),
                row(PRINCIPAL, "credential-presented", CREDENTIAL),
                row(CREDENTIAL, "credential-sharing", "shared"),
            ],
        )
        hits = fired(model, "repudiation-shared-credential", held)
        assert [hit.element_ids[0] for hit in hits] == [flow.id]

    def test_a_principal_with_no_identification_reaches_nothing(self, model, flow):
        """Without ``represented-by``, a principal is not an element."""
        held = AssertionCatalog(
            subjects=[
                Subject(id=PRINCIPAL, type="principal", label="customers"),
                Subject(id=CREDENTIAL, type="credential", label="API key"),
            ],
            entries=[
                row(PRINCIPAL, "credential-presented", CREDENTIAL),
                row(CREDENTIAL, "credential-sharing", "shared"),
            ],
        )
        assert fired(model, "repudiation-shared-credential", held) == []


def test_a_lead_carries_the_fact_and_its_basis(model, flow):
    """An inferred absence is a weaker lead, and the agent sees which it has."""
    held = AssertionCatalog(
        subjects=[Subject(id=flow.id, type="interaction", label=flow.name)],
        entries=[row(flow.id, "origin-verification", ABSENT, basis="inferred")],
    )
    (hit,) = fired(model, "spoofing-origin-stated-unverified", held)
    assert hit.facts["basis"] == "inferred"
    assert "origin-verification absent" in hit.facts["stated"]


def test_a_verified_check_does_not_lead(model, flow):
    held = AssertionCatalog(
        subjects=[Subject(id=flow.id, type="interaction", label=flow.name)],
        entries=[row(flow.id, "signature-verification", "verified")],
    )
    assert fired(model, "tampering-signature-stated-unverified", held) == []
