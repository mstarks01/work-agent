"""Every ASVS reference record says what the submission can conclude, and a new one must too.

The applicability matrix answers whether a requirement is in play. It cannot
answer the question a submitter actually acts on: *what do I do next*. A run that
names the right requirement and then asks for more description of a property only
the source code settles scores full recall and gives the wrong instruction, which
is the failure #471 exists to measure.

So each record carries an expected **disposition**, and this counts them. It is
the shape ``tests/test_verb_coverage.py`` settled into: a per-case count rather
than one total, so a case losing records and another gaining them cannot cancel
out, and a case arriving unannotated fails here rather than quietly shrinking a
denominator nobody reads.

**The vocabulary is checked against production's own.** A disposition naming a
kind of evidence exists to be compared against what a lane agent can ask for, so
the table is held to ``RequirementProposal.needs_evidence``'s closed set rather
than to a second list somebody remembers to update.

Deterministic over the corpus and free of provider calls, so it gates on every PR.
"""

from __future__ import annotations

from typing import get_args, get_type_hints

from analysis_service.frameworks.asvs.record import RequirementProposal
from evals import verify_corpus
from evals.harness.reference import (
    DISPOSITION_FOR_EVIDENCE,
    AsvsDisposition,
    ReferenceRequirement,
    load_corpus,
)

#: How many ASVS reference records each case carries, all of which must carry a
#: disposition. Spelled per case for the reason ``CLAIMS_PER_CASE`` is.
RECORDS_PER_CASE: dict[str, int] = {
    "01-payments-checkout": 17,
    "02-iot-fleet-telemetry": 8,
    "04-ml-inference-service": 10,
    "05-cookbook-queue-webapp": 7,
    "06-cookbook-online-game": 6,
    "08-sso-identity-broker": 12,
    "09-cookbook-sokify-retail": 7,
    "10-cookbook-generic-cms": 8,
    "11-sparse-shift-scheduling": 8,
    "12-overclaiming-supplier-portal": 10,
    "13-dispatch-control-plane": 6,
}


def _records(case) -> list[ReferenceRequirement]:
    return [
        reference
        for reference in case.references.get("asvs") or ()
        if isinstance(reference, ReferenceRequirement)
    ]


def _corpus() -> dict[str, list[ReferenceRequirement]]:
    return {
        case.id: _records(case)
        for case in load_corpus(verify_corpus.CORPUS_DIR)
        if _records(case)
    }


def test_every_case_carrying_asvs_records_is_counted():
    """A new ASVS case fails here rather than going unannotated."""
    assert sorted(_corpus()) == sorted(RECORDS_PER_CASE)


def test_each_case_carries_the_records_it_is_counted_for():
    counted = {case_id: len(records) for case_id, records in _corpus().items()}

    assert counted == RECORDS_PER_CASE


def test_every_record_carries_an_expected_disposition():
    """``None`` is legal on the model and absent from the corpus.

    The field allows it so that a record can be added before its judgement is
    made. Nothing is in that state, and this is what keeps it so: an unjudged
    record scores for applicability and drops out of the routing metrics, which
    is a silent shrinking of a denominator rather than a visible gap.
    """
    unjudged = sorted(
        f"{case_id}:{record.requirement}"
        for case_id, records in _corpus().items()
        for record in records
        if record.disposition is None
    )

    assert not unjudged, (
        f"these ASVS records carry no expected disposition: {unjudged}."
        " Add one, or the routing metrics silently stop measuring them."
    )


def test_the_corpus_exercises_every_disposition():
    """A vocabulary the corpus never uses is a metric nothing tests.

    ``needs-code``, ``needs-config`` and ``needs-people`` are the three the
    false-prose-request rate is denominated in, and ``not-applicable`` is the
    only one a rejection can satisfy. A corpus missing any of them would report
    a rate over an empty denominator and read as a clean run.
    """
    used = {
        record.disposition
        for records in _corpus().values()
        for record in records
        if record.disposition is not None
    }

    assert used == set(get_args(AsvsDisposition))


def test_the_disposition_table_matches_the_evidence_kinds_production_can_ask_for():
    """The table against its registry, which is what stops a silent gap.

    A kind added to ``needs_evidence`` without an entry here would defer at
    runtime and score as nothing, because no case could express the expectation
    it belongs to. The empty string is production's *I ruled* answer rather than
    a request for evidence, so it is excluded rather than mapped.
    """
    hints = get_type_hints(RequirementProposal)
    kinds = {kind for kind in get_args(hints["needs_evidence"]) if kind}

    assert set(DISPOSITION_FOR_EVIDENCE) == kinds
    assert set(DISPOSITION_FOR_EVIDENCE.values()) <= set(get_args(AsvsDisposition))
