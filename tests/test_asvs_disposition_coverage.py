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
    "01-payments-checkout": 18,
    "02-iot-fleet-telemetry": 5,
    "04-ml-inference-service": 10,
    "05-cookbook-queue-webapp": 7,
    "06-cookbook-online-game": 6,
    "08-sso-identity-broker": 12,
    "09-cookbook-sokify-retail": 7,
    "10-cookbook-generic-cms": 7,
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


def test_the_corpus_exercises_the_alternate_route():
    """A field no record uses is a mechanism nothing tests (#659).

    Widening is per record and each one is a judgement, so this asserts the
    corpus uses the field at all rather than pinning which records do. The
    label read that #226 owes may add more.
    """
    widened = {
        f"{case_id}:{record.requirement}": record.also_acceptable
        for case_id, records in _corpus().items()
        for record in records
        if record.also_acceptable
    }

    assert widened, (
        "no ASVS record names a second acceptable route, so `also_acceptable`"
        " is a mechanism nothing exercises"
    )
    # Every alternate is a route, which the record model already refuses to
    # break; asserted here too because this is the file that reads the corpus.
    assert all(
        set(routes)
        <= {"needs-more-prose", "needs-code", "needs-config", "needs-people"}
        for routes in widened.values()
    )


def test_an_exemplar_question_names_the_kind_the_corpus_expects():
    """An exemplar teaches a lane which kind of evidence settles a requirement,
    and the corpus scores the lane on the same answer. Two exemplars taught
    `people` where the read reference said prose (#738), and a third did the
    same for V8.1.1. Direction is the system's own and is not compared: an
    exemplar system may lack what a corpus case has. Code against config is the
    system's own too: a token signature's primitive sits in a broker's
    configuration and a password check's hash in a web API's code, and V11.4.1
    is labelled both ways. Prose against people is the contract's question,
    whether a submitter's own words can settle a requirement or only a person
    the job cannot reach, and that answer does not move with the system. So
    only that axis is compared.
    """
    from analysis_service.frameworks import PACKAGES
    from analysis_service.frameworks.asvs.catalog import CHAPTER_NUMBERS
    from tests.test_prompt_lints import exemplar_proposals

    expected = {
        requirement_id: {
            (case_id, record.disposition)
            for case_id, records in _corpus().items()
            for record in records
            if record.requirement == requirement_id
            and record.disposition in DISPOSITION_FOR_EVIDENCE.values()
        }
        for requirement_id in {
            record.requirement for records in _corpus().values() for record in records
        }
    }
    contract_axis = {"needs-more-prose", "needs-people"}
    disagreements = []
    for lane in PACKAGES["asvs"].lanes:
        for proposal in exemplar_proposals("asvs", lane):
            if proposal.direction != "question":
                continue
            requirement_id = f"V{CHAPTER_NUMBERS[lane]}.{proposal.requirement}"
            taught = DISPOSITION_FOR_EVIDENCE[proposal.needs_evidence]
            disagreements += [
                f"{requirement_id}: exemplar teaches {taught}, case {case_id} expects {labelled}"
                for case_id, labelled in sorted(expected.get(requirement_id, ()))
                if labelled != taught and {labelled, taught} <= contract_axis
            ]
    assert not disagreements, (
        "an exemplar routes a question to a kind the corpus does not expect:\n  "
        + "\n  ".join(disagreements)
        + "\nDecide which side is right; a human-read case's label wins."
    )
