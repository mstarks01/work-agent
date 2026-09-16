"""The reference facts: resolvable, agreeing with the blessed model, and signed or not.

A case's ``facts.json`` is the denominator for required-fact recall and the
standard for unsupported-assertion precision (#961 step 3, #926 Phase 6). That
makes two things load-bearing. Every row must be one the assertion gate would
keep, or the reference asks a producer for a row the gate would refuse. And
every row must be a person's ruling, or the figure measures how closely a
model agrees with the agent that drafted the reference.

So these checks hold the rows to the resolver, hold the catalog they build
against the blessed model's own control attributes — the two readers of one
source, tested against each other rather than each against itself — and read
``reviewed_by``, so an unsigned case can be run and read but never quoted.

Deterministic, credential-free, and free of provider calls.
"""

from __future__ import annotations

import pytest

from analysis_service.assertions import project
from evals import verify_corpus
from evals.harness.modes import PROJECTION_COMPARED, reachable_controls
from evals.harness.reference import load_case
from evals.reference_facts import (
    DRAFTER,
    drafted_cases,
    facts_path,
    load_facts,
    reference_catalog,
)

#: The cases #961 step 3 names for the first sitting, each of which must carry
#: a draft: mixed authentication and MFA, actor naming and an unsupported key
#: fact, an executable spreadsheet, a sparse description, and parallel
#: interfaces.
AUDITED: tuple[str, ...] = (
    "01-payments-checkout",
    "04-ml-inference-service",
    "09-cookbook-sokify-retail",
    "11-sparse-shift-scheduling",
    "13-dispatch-control-plane",
)

DRAFTED = drafted_cases(verify_corpus.CORPUS_DIR)
IDS = [case_dir.name for case_dir in DRAFTED]


@pytest.fixture(scope="module", params=DRAFTED, ids=IDS)
def drafted(request):
    """One drafted case: its facts, its golden case, and the catalog they build."""
    case_dir = request.param
    facts = load_facts(case_dir)
    case = load_case(case_dir)
    sources = {source.label: source.text for source in case.sources}
    return case, facts, reference_catalog(facts, case.model, sources)


def test_every_audited_case_carries_a_draft():
    """Step 3's five cases each have a file; a sixth is welcome and unlisted."""
    missing = [
        case_id
        for case_id in AUDITED
        if not facts_path(verify_corpus.CORPUS_DIR / case_id).is_file()
    ]

    assert missing == []


def test_the_file_names_its_own_case(drafted):
    case, facts, _ = drafted

    assert facts.case == case.id


def test_every_row_resolves_through_the_gate(drafted):
    """Resolving raised nothing, so every row is one a produced catalog could hold."""
    _, facts, catalog = drafted

    assert len(catalog.entries) == len(facts.rows), "two rows share one identity"


def test_no_alias_is_signed_by_whoever_drafted_it(drafted):
    """An alias is a ruling like a row: the drafter's name cannot sign one."""
    _, facts, _ = drafted
    assert all(entry.reviewed_by != DRAFTER for entry in facts.aliases.entries)


def test_an_alias_names_a_subject_of_the_layers_own(drafted):
    """An element's other names live in case.json, where the alignment reads them."""
    _, facts, catalog = drafted
    held = {subject.id for subject in catalog.subjects}
    for ruling in facts.aliases.subjects:
        assert ruling.subject in held, (
            f"{ruling.subject} is not a subject the rows name"
        )
        assert ruling.subject.split(":")[0] in {"principal", "credential", "artifact"}
        assert all(alias != ruling.subject for alias in ruling.alias_ids)


def test_a_qualifier_alias_names_a_scope_the_rows_carry(drafted):
    _, facts, catalog = drafted
    carried = {
        (qualifier.kind, qualifier.value)
        for entry in catalog.entries
        for qualifier in entry.scope
    }
    for ruling in facts.aliases.qualifiers:
        assert (ruling.kind, ruling.value) in carried, (
            f"{ruling.kind}={ruling.value!r} scopes no reference row"
        )


def test_an_element_alias_is_refused_here():
    from pydantic import ValidationError

    from evals.reference_facts import SubjectAlias

    with pytest.raises(ValidationError, match="belongs in case.json"):
        SubjectAlias(subject="process:web-app", names=["app"], ruling="no")


def test_no_row_is_signed_by_whoever_drafted_it(drafted):
    """The signature means a second reader, or it means nothing."""
    _, facts, _ = drafted

    for row in facts.rows:
        assert row.reviewed_by != DRAFTER, row.rationale
    for dispute in facts.disputed:
        assert dispute.reviewed_by != DRAFTER, dispute.basis


def test_a_ruling_names_who_made_it(drafted):
    """A dispute ruled and unsigned is a ruling nobody made."""
    _, facts, _ = drafted

    for dispute in facts.disputed:
        assert (dispute.ruling is None) == (dispute.reviewed_by is None), dispute.basis


def test_every_dispute_names_a_value_the_model_still_holds(drafted):
    """An entry the model no longer matches is spent: delete it."""
    case, facts, _ = drafted
    by_id = {element.id: element for element in case.model.elements()}

    for dispute in facts.disputed:
        element = by_id[dispute.element_id]
        assert hasattr(element, dispute.attribute), dispute.basis
        assert str(getattr(element, dispute.attribute)) == dispute.blessed, (
            dispute.basis
        )


def test_the_rows_reproduce_every_control_the_blessed_model_states(drafted):
    """The reference catalog and the blessed attributes agree, both ways.

    Both are readings of one source. Every ``(element, attribute)`` pair the
    blessed model states or states absent must be reached by a projection of
    the reference rows that agrees with it, compared the way the field is
    compared everywhere else. And every projection that states a value must
    land on a pair the blessed model states too, so the reference cannot
    quietly assert a control the model leaves unverified. A pair the drafter
    disputes is exempt in both directions until somebody rules on it.

    **A placement the schema requires is not a fact the reference must hold.**
    A zoned element needs a zone whether or not the source gives one, and the
    model's assumptions list is where it says a value was inferred. Where that
    list names the pair, the reference decides which kind of inference it was:
    a row with an inferred basis that agrees says the source supports it, and
    a row reading unknown says the value is a placeholder and keeps the pair
    out of the required-fact denominator. A pair no assumption names must
    agree, because nothing in the model admits it was inferred.
    """
    case, facts, catalog = drafted
    by_id = {element.id: element for element in case.model.elements()}
    reachable = reachable_controls(case.model)
    disputed = {(dispute.element_id, dispute.attribute) for dispute in facts.disputed}
    assumed = {(entry.element_id, entry.attribute) for entry in case.model.assumptions}
    projected = {
        (projection.element_id, projection.attribute): projection
        for projection in project(catalog)
    }

    for key, stratum in reachable.items():
        if key in disputed:
            continue
        projection = projected.get(key)
        assert projection is not None, f"no row reaches {key}"
        if projection.reason == "unknown" and key in assumed:
            continue
        assert projection.reason == stratum, f"{key}: {projection.reason}"
        compared = PROJECTION_COMPARED[projection.attribute]
        blessed = str(getattr(by_id[key[0]], key[1]))
        assert compared(projection.value) == compared(blessed), key
    for key, projection in projected.items():
        if key in disputed or projection.reason not in ("stated", "absent"):
            continue
        assert key in reachable, f"{key} is stated by the reference and not the model"


def test_an_unsigned_case_is_named_rather_than_trusted(drafted):
    """The gate on quoting this case's facts as a denominator.

    Not a failure while the rows are unsigned: running the drafts is how a
    reader sees what they are asked to sign. What it must never do is pass
    silently, because a recall figure over an unsigned reference looks exactly
    like one over a signed reference.
    """
    case, facts, _ = drafted
    unsigned = [row for row in facts.rows if row.reviewed_by is None]

    if unsigned:
        pytest.skip(
            f"{case.id}: {len(unsigned)} of {len(facts.rows)} rows are unsigned, so"
            " this file states what an agent proposed and not what a reader ruled."
            " It may be run; it may not be quoted as a required-fact denominator."
        )
    assert all(row.reviewed_by for row in facts.rows)
