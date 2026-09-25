"""Archived emissions re-scored under today's coordinates, and every loss named (#961 step 4).

The probes are the issue's own classes: a store written as a process, a flow
pointed at the wrong endpoint, an actor under a plural, a dropped element
beside an invented one, and a reference row worded twice. Each is built off a
real corpus case so the alignment it reads is the shipped one.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import get_args

import pytest

from analysis_service.assertions import (
    ABSENT,
    GATE_REFUSALS,
    SPAN_REFUSALS,
    UNKNOWN,
    Assertion,
    AssertionCatalog,
    AssertionRecord,
    CatalogIssue,
    Subject,
    assertion_id,
    identity_parts,
)
from analysis_service.system_model import (
    DataFlow,
    SystemModel,
    flow_id_version,
    make_flow_id,
    normalize_element_ids,
    parse_flow_id,
)
from analysis_service.validation import parse_and_validate
from evals.harness import bundle, replay
from evals.harness.alignment import align, placeholder_zones
from evals.harness.archive import kind_of
from evals.harness.artifact import load_artifact
from evals.harness.bundle import (
    assertions_from_reports,
    extractions_from_reports,
    heads_from_reports,
    reports_dir,
    write_assertions,
    write_extractions,
)
from evals.harness.modes import AssertionResult, ExtractionResult
from evals.harness.reference import load_corpus
from evals.harness.run import main, replay_artifact
from tests.test_evals_alignment import case, renamed
from tests.test_evals_extraction_losses import CASE
from tests.test_evals_provenance import provenance, sampling  # noqa: F401
from tests.test_evals_stability import score, write_run

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS = REPO_ROOT / "evals" / "corpus"
EMISSIONS = REPO_ROOT / "evals" / "emissions"
ARMS = EMISSIONS / "20260917T-arms-luna-pro"


def fates(golden, model: SystemModel | None) -> dict[str, replay.ElementFate]:
    rows = replay.classify_elements(golden.model, model, align(golden, model))
    return {row.reference: row for row in rows}


def without(model: SystemModel, element_id: str) -> SystemModel:
    """The model with one element and every flow through it removed."""
    raw = model.model_dump(mode="json")
    for key in ("external_entities", "processes", "data_stores", "trust_boundaries"):
        raw[key] = [element for element in raw[key] if element["id"] != element_id]
    raw["data_flows"] = [
        flow
        for flow in raw["data_flows"]
        if flow["id"] != element_id
        and element_id not in (flow["source"], flow["destination"])
    ]
    return SystemModel.model_validate(raw)


class TestEveryBlessedElementTakesOneFate:
    @pytest.fixture(scope="class")
    def golden(self):
        return case("01")

    def test_the_blessed_model_against_itself_is_found_throughout(self, golden):
        rows = fates(golden, golden.model)

        assert {row.fate for row in rows.values()} == {"found"}
        assert len(rows) == len(list(golden.model.elements()))

    def test_no_model_leaves_every_element_omitted(self, golden):
        rows = fates(golden, None)

        assert {row.fate for row in rows.values()} == {"omitted"}

    def test_a_reader_ruled_alias_reads_as_renamed(self):
        """Case 09's spreadsheet under the name the reader ruled supported."""
        golden = case("09")
        model = renamed(golden.model, "process:catalogue-spreadsheet", "Spreadsheet")

        row = fates(golden, model)["process:catalogue-spreadsheet"]

        assert row.fate == "renamed"
        assert row.produced == "process:spreadsheet"

    def test_a_plural_reads_as_respelled_not_as_a_loss_to_rule_on(self, golden):
        model = renamed(golden.model, "entity:shopper", "Shoppers")

        row = fates(golden, model)["entity:shopper"]

        assert row.fate == "respelled"
        assert row.candidates == ("entity:shoppers",)

    def test_a_store_written_as_a_process_reads_as_mistyped(self, golden):
        copy = golden.model.model_copy(deep=True)
        store = next(s for s in copy.data_stores if s.id == "store:orders-db")
        copy.data_stores.remove(store)
        raw = copy.model_dump(mode="json")
        raw["processes"].append(
            {
                **next(p for p in raw["processes"]),
                "id": "process:orders-db",
                "name": store.name,
            }
        )
        model, _ = parse_and_validate(raw, normalize_ids=True)
        assert model is not None

        row = fates(golden, model)["store:orders-db"]

        assert row.fate == "mistyped"
        assert row.candidates == ("process:orders-db",)

    def test_a_zone_held_only_because_the_schema_requires_one_is_a_placeholder(
        self, golden
    ):
        """#961 step 6: the reader removed the DMZ expectation; the model keeps the zone."""
        raw = golden.model.model_dump(mode="json")
        zone = "boundary:storefront-dmz"
        assert zone in placeholder_zones(golden.model)
        raw["trust_boundaries"] = [
            b for b in raw["trust_boundaries"] if b["id"] != zone
        ]
        for element in raw["processes"]:
            if element["trust_zone"] == zone:
                element["trust_zone"] = "boundary:core-services"
        raw["assumptions"] = [
            a for a in raw["assumptions"] if a["element_id"] != "process:storefront-api"
        ]
        model = SystemModel.model_validate(raw)

        row = fates(golden, model)[zone]

        assert row.fate == "placeholder"
        assert "placeholder" not in replay.LOSSES

    def test_a_zone_the_source_places_something_in_is_not_a_placeholder(self, golden):
        assert "boundary:core-services" not in placeholder_zones(golden.model)

    def test_a_dropped_element_with_no_type_mate_is_omitted(self, golden):
        model = without(golden.model, "store:receipt-archive")

        rows = fates(golden, model)

        assert rows["store:receipt-archive"].fate == "omitted"
        assert (
            rows["flow:process:order-service>store:receipt-archive>append-receipt"].fate
            == "endpoint_unaligned"
        )

    def test_a_dropped_element_beside_an_invented_type_mate_is_unresolved(self, golden):
        """One missing store and one extra store: a rename nobody ruled, or not."""
        model = renamed(golden.model, "store:receipt-archive", "Ledger Vault")

        row = fates(golden, model)["store:receipt-archive"]

        assert row.fate == "unresolved"
        assert row.candidates == ("store:ledger-vault",)

    def test_a_flow_pointed_at_another_endpoint_is_misattached(self, golden):
        copy = golden.model.model_copy(deep=True)
        flow = next(
            f
            for f in copy.data_flows
            if f.id == "flow:process:order-service>store:receipt-archive>append-receipt"
        )
        flow.source = "process:storefront-api"
        model = normalize_element_ids(copy)

        row = fates(golden, model)[
            "flow:process:order-service>store:receipt-archive>append-receipt"
        ]

        assert row.fate == "misattached"
        assert row.candidates == (
            "flow:process:storefront-api>store:receipt-archive>append-receipt",
        )

    def test_the_sole_flow_under_another_label_is_renamed_and_a_second_one_is_not(
        self, golden
    ):
        """Rule 6 pairs one flow each side; beside a second flow it stays a ruling."""
        copy = golden.model.model_copy(deep=True)
        flow_id = "flow:process:order-service>store:receipt-archive>append-receipt"
        flow = next(f for f in copy.data_flows if f.id == flow_id)
        flow.name = "Nightly export"
        flow.operations = "read"
        model = normalize_element_ids(copy)

        alone = fates(golden, model)[flow_id]

        assert alone.fate == "renamed"
        assert alone.evidence == "sole"

        # A second flow the discriminators cannot tell from the first, so
        # neither rule 5 nor rule 6 has one flow to pair.
        second = flow.model_copy(update={"name": "Audit copy", "operations": "read"})
        copy.data_flows.append(second)
        beside = fates(golden, normalize_element_ids(copy))[flow_id]

        assert beside.fate == "unresolved"

    def test_a_flow_dropped_between_found_endpoints_is_omitted(self, golden):
        copy = golden.model.model_copy(deep=True)
        copy.data_flows[:] = [
            f
            for f in copy.data_flows
            if f.id != "flow:process:order-service>store:receipt-archive>append-receipt"
        ]

        row = fates(golden, copy)[
            "flow:process:order-service>store:receipt-archive>append-receipt"
        ]

        assert row.fate == "omitted"

    def test_a_wrong_fact_is_an_attribute_on_a_paired_element(self, golden):
        copy = golden.model.model_copy(deep=True)
        next(
            p for p in copy.processes if p.id == "process:order-service"
        ).exposure = "internet-facing"
        result = ExtractionResult(golden.id, copy, ())

        graded = replay.replay_extraction(golden, result)

        assert [c.key for c in graded.wrong_facts] == ["process.exposure"]
        assert graded.counts["found"] == len(list(golden.model.elements()))


def fact(
    subject: str = "store:queue",
    predicate: str = "storage-encryption",
    value: str = ABSENT,
    **overrides,
) -> Assertion:
    """One stated row, with only what a matcher test reads."""
    return Assertion(
        subject=subject, predicate=predicate, value=value, basis="stated", **overrides
    )


def graded_rows(
    references: list[Assertion], produced: list[Assertion]
) -> replay.AssertionReplay:
    """Two hand-built catalogs, graded against each other."""
    reference = replay.SignedReference(catalog=AssertionCatalog(entries=references))
    result = AssertionResult(
        "probe",
        {"assertions": []},
        AssertionRecord(proposed=0, catalog=AssertionCatalog(entries=produced)),
    )
    case = load_corpus(CORPUS)[0]
    return replay.replay_assertions(case, reference, result)


class TestOneProducedRowAnswersOneReferenceRow:
    """The matcher assigns, and an assignment is one-to-one and order-free."""

    def scoped(self, value: str) -> list:
        from analysis_service.assertions import Qualifier

        return [Qualifier(kind="resource", value=value)]

    def test_a_row_exactly_answered_keeps_its_answer(self) -> None:
        """Two rows differ only in scope, and the run answers the second.

        Reading the reference array once and taking each row's best available
        candidate spends the exact answer on the first row as a ``rescoped``
        one, so the score turns on the order the reference lists its rows.
        """
        one, two = fact(scope=self.scoped("one")), fact(scope=self.scoped("two"))
        produced = [fact(scope=self.scoped("two"))]

        forwards = graded_rows([one, two], produced)
        backwards = graded_rows([two, one], produced)

        assert forwards.counts["found"] == 1
        assert forwards.counts["found"] == backwards.counts["found"]
        assert forwards.counts["omitted"] == backwards.counts["omitted"] == 1

    def test_one_flow_does_not_credit_two_interactions(self) -> None:
        """Two interactions between one pair of endpoints are two facts.

        Dropping the flow label to compare a rename makes their keys equal, so
        a single generic row would answer both. It answers one.
        """
        ordinary = make_flow_id("process:client", "process:api", "ordinary requests")
        socket = make_flow_id("process:client", "process:api", "websocket")
        sent = make_flow_id("process:client", "process:api", "send traffic")
        references = [
            fact(ordinary, "transport-encryption", "TLS"),
            fact(socket, "transport-encryption", "TLS"),
        ]
        reference = replay.SignedReference(catalog=AssertionCatalog(entries=references))
        result = AssertionResult(
            "probe",
            {"assertions": []},
            AssertionRecord(
                proposed=0,
                catalog=AssertionCatalog(
                    entries=[fact(sent, "transport-encryption", "TLS")]
                ),
            ),
        )

        assert len(replay.aligned_rows(reference, result)) == 1


def test_the_preference_order_answers_for_every_answered_fate():
    """The table against its registry, which nothing compared.

    ``_assigned`` walks ``_PREFERRED`` to pick the best answer available for a
    reference row. A fate the tuple leaves out is never picked, so the row
    falls through to a worse one and no check fires: the table is short and
    the run still finishes.
    """
    assert frozenset(replay._PREFERRED) == frozenset(get_args(replay.AnsweredFate))
    assert len(replay._PREFERRED) == len(set(replay._PREFERRED))


class TestEveryReferenceRowTakesOneFate:
    @pytest.fixture(scope="class")
    def golden(self):
        return case("01")

    @pytest.fixture(scope="class")
    def reference(self, golden):
        catalog = replay.signed_reference(CORPUS, golden)
        if catalog is None:
            pytest.skip("case 01's facts are unsigned, so nothing grades a proposal")
        return catalog

    def graded(self, golden, reference, entries, issues=()):
        produced = AssertionCatalog(subjects=reference.subjects, entries=list(entries))
        result = AssertionResult(
            golden.id,
            {"assertions": []},
            AssertionRecord(proposed=0, catalog=produced, issues=list(issues)),
        )
        return replay.replay_assertions(golden, reference, result)

    def test_the_reference_against_itself_is_found_throughout(self, golden, reference):
        graded = self.graded(golden, reference, reference.entries)

        assert {row.fate for row in graded.rows} == {"found"}
        assert set(graded.produced.values()) == {"found"}

    def test_a_reworded_text_value_is_a_readers_question(self, golden, reference):
        row = next(e for e in reference.entries if e.predicate == "storage-encryption")
        reworded = row.model_copy(update={"value": "a key the customer manages"})
        entries = [reworded if e is row else e for e in reference.entries]

        graded = self.graded(golden, reference, entries)

        assert graded.counts["worded"] == 1
        assert graded.counts["found"] == len(reference.entries) - 1

    def test_a_reworded_term_is_a_wrong_value(self, golden, reference):
        row = next(
            e
            for e in reference.entries
            if e.predicate == "internet-exposure" and e.value == "internal"
        )
        flipped = row.model_copy(update={"value": "internet-facing"})
        entries = [flipped if e is row else e for e in reference.entries]

        graded = self.graded(golden, reference, entries)

        assert graded.counts["wrong_value"] == 1

    def test_a_stated_fact_answered_as_absent_is_a_wrong_value(self, golden, reference):
        row = next(e for e in reference.entries if e.predicate == "storage-encryption")
        denied = row.model_copy(update={"value": ABSENT})
        entries = [denied if e is row else e for e in reference.entries]

        graded = self.graded(golden, reference, entries)

        assert graded.counts["wrong_value"] == 1

    def test_a_wrong_value_is_a_wrong_claim_and_not_an_answer(self, golden, reference):
        """The reference calls the component internal; the run calls it exposed.

        The produced row carries the reference row's own fate, so a precision
        figure reading :data:`~evals.harness.replay.ADJUDICATED_WRONG` counts
        it. A row the reference disagrees with is not an answer to it.
        """
        row = next(
            e
            for e in reference.entries
            if e.predicate == "internet-exposure" and e.value == "internal"
        )
        flipped = row.model_copy(update={"value": "internet-facing"})

        graded = self.graded(golden, reference, [flipped])

        assert graded.produced == {assertion_id(flipped): "wrong_value"}
        assert set(graded.produced.values()) <= replay.ADJUDICATED_WRONG

    def test_an_inferred_answer_to_a_stated_fact_is_not_found(self, golden, reference):
        """The source states the fact and the run says it worked it out."""
        row = next(e for e in reference.entries if e.predicate == "storage-encryption")
        guessed = row.model_copy(
            update={"basis": "inferred", "explanation": "a guess", "support": []}
        )

        graded = self.graded(golden, reference, [guessed])

        fate = next(r.fate for r in graded.rows if r.reference == assertion_id(row))
        assert fate == "wrong_certainty"
        assert graded.counts["found"] == 0

    def test_dropping_an_exclusivity_the_reference_holds_is_not_found(
        self, golden, reference
    ):
        """ "The only thing we expose" is a claim about every other subject."""
        row = next(e for e in reference.entries if e.predicate == "storage-encryption")
        held = row.model_copy(update={"exclusive": True})
        signed = replay.SignedReference(
            AssertionCatalog(subjects=reference.subjects, entries=[held])
        )

        graded = self.graded(golden, signed, [row])

        assert [r.fate for r in graded.rows] == ["wrong_certainty"]

    def test_a_hedged_unknown_answered_as_silent_is_not_found(self, golden, reference):
        """A question the source raised is not a question it never raised."""
        row = next(e for e in reference.entries if e.predicate == "storage-encryption")
        hedged = row.model_copy(
            update={"value": UNKNOWN, "reason": "hedged", "support": []}
        )
        silent = hedged.model_copy(update={"reason": "silent"})
        signed = replay.SignedReference(
            AssertionCatalog(subjects=reference.subjects, entries=[hedged])
        )

        graded = self.graded(golden, signed, [silent])

        assert [r.fate for r in graded.rows] == ["wrong_certainty"]

    def test_a_signed_subject_alias_pairs_a_row_named_otherwise(
        self, golden, reference
    ):
        """The reviewer rules two spellings name one principal; the matcher reads it."""
        row = next(
            e
            for e in reference.entries
            if e.predicate == "authorization-grant"
            and e.subject == "principal:application-account"
        )
        renamed = row.model_copy(
            update={"subject": "principal:single-application-account"}
        )
        unsigned = replay.SignedReference(reference.catalog)
        signed = replay.SignedReference(
            reference.catalog,
            subject_aliases={
                "principal:single-application-account": replay.AliasTarget(row.subject)
            },
        )

        before = self.graded(golden, unsigned, [renamed])
        after = self.graded(golden, signed, [renamed])

        # Unsigned, the row sits on a subject nobody ruled on: its grant is one
        # the reference carries elsewhere, so it reads as a misattachment.
        assert before.produced == {assertion_id(renamed): "misattached"}
        assert after.produced == {assertion_id(row): "found"}
        assert next(r.fate for r in after.rows if r.reference == assertion_id(row)) == (
            "found"
        )

    def test_a_subject_alias_also_rewrites_a_value_that_points_at_it(
        self, golden, reference
    ):
        """A credential named otherwise is one credential wherever a row points at it."""
        row = next(
            e
            for e in reference.entries
            if e.predicate == "credential-presented"
            and e.value == "credential:application-account-password"
        )
        pointed = row.model_copy(update={"value": "credential:password"})
        signed = replay.SignedReference(
            reference.catalog,
            subject_aliases={"credential:password": replay.AliasTarget(row.value)},
        )

        graded = self.graded(golden, signed, [pointed])

        assert next(
            r.fate for r in graded.rows if r.reference == assertion_id(row)
        ) == ("found")

    def test_a_subject_alias_also_rewrites_a_zone_a_row_is_placed_in(
        self, golden, reference
    ):
        """A zone ruled to be the reference's zone is that zone in a value too.

        A network-membership row names its zone by **Element ID**. The subject
        of the row is the component, so a ruling about the zone reaches this row
        only through its value, and a matcher that rewrote subjects alone would
        leave a signed alias unable to repair the reference beside it.
        """
        row = next(e for e in reference.entries if e.predicate == "network-membership")
        named = row.model_copy(update={"value": "boundary:core-network"})
        signed = replay.SignedReference(
            reference.catalog,
            subject_aliases={"boundary:core-network": replay.AliasTarget(row.value)},
        )

        assert replay.under_aliases(named, signed).value == row.value

    def test_a_value_alias_holds_only_under_the_subjects_the_ruling_names(
        self, golden, reference
    ):
        """ "Password" is the application account's within its rows, not a shopper's."""
        row = next(
            e
            for e in reference.entries
            if e.predicate == "credential-presented"
            and e.value == "credential:application-account-password"
        )
        bounded = replay.AliasTarget(row.value, frozenset({row.subject}))
        signed = replay.SignedReference(
            reference.catalog, subject_aliases={"credential:password": bounded}
        )
        theirs = row.model_copy(update={"value": "credential:password"})
        shoppers = theirs.model_copy(update={"subject": "principal:shopper-accounts"})

        assert replay.under_aliases(theirs, signed).value == row.value
        assert replay.under_aliases(shoppers, signed).value == "credential:password"

    def test_a_signed_qualifier_alias_turns_a_rescoped_row_into_a_found_one(
        self, golden, reference
    ):
        row = next(
            e
            for e in reference.entries
            if e.predicate == "authorization-grant"
            and e.subject == "principal:application-account"
        )
        respelled = row.model_copy(
            update={
                "scope": [
                    q.model_copy(update={"value": "read and write"})
                    if q.kind == "operation"
                    else q
                    for q in row.scope
                ]
            }
        )
        signed = replay.SignedReference(
            reference.catalog,
            qualifier_aliases={
                ("operation", "read and write"): replay.AliasTarget("read-write")
            },
        )

        before = self.graded(
            golden, replay.SignedReference(reference.catalog), [respelled]
        )
        after = self.graded(golden, signed, [respelled])

        fate = lambda g: next(
            r.fate for r in g.rows if r.reference == assertion_id(row)
        )
        assert fate(before) == "rescoped"
        assert fate(after) == "found"

    def test_a_qualifier_alias_holds_only_under_the_subjects_the_ruling_names(
        self, golden, reference
    ):
        """ "Publish" is the write in one principal's grant and not in another's."""
        row = next(
            e
            for e in reference.entries
            if e.predicate == "authorization-grant"
            and e.subject == "principal:application-account"
        )
        respelled = row.model_copy(
            update={
                "scope": [
                    q.model_copy(update={"value": "read and write"})
                    if q.kind == "operation"
                    else q
                    for q in row.scope
                ]
            }
        )
        elsewhere = respelled.model_copy(update={"subject": "principal:order-service"})
        bounded = replay.AliasTarget("read-write", frozenset({row.subject}))
        signed = replay.SignedReference(
            reference.catalog,
            qualifier_aliases={("operation", "read and write"): bounded},
        )

        here = replay.under_aliases(respelled, signed)
        there = replay.under_aliases(elsewhere, signed)

        assert [q.value for q in here.scope if q.kind == "operation"] == ["read-write"]
        assert [q.value for q in there.scope if q.kind == "operation"] == [
            "read and write"
        ]

    def test_a_silent_unknown_nobody_produced_is_silent_not_omitted(
        self, golden, reference
    ):
        """The prompt forbids the row and the reference records the forced placement."""
        graded = self.graded(golden, reference, [])

        silent = {r.reference for r in graded.rows if r.fate == "silent"}
        expected = {
            assertion_id(e)
            for e in reference.entries
            if e.value == "unknown" and e.reason == "silent"
        }
        assert silent == expected
        assert expected, "case 01 carries silent placements under the step-3 rulings"
        assert all(
            r.fate == "omitted" for r in graded.rows if r.reference not in expected
        )

    def test_a_value_produced_against_a_silent_unknown_is_wrong(
        self, golden, reference
    ):
        row = next(
            e
            for e in reference.entries
            if e.value == "unknown" and e.reason == "silent"
        )
        placed = row.model_copy(
            update={"value": "boundary:core-services", "reason": None}
        )

        graded = self.graded(golden, reference, [placed])

        assert next(
            r.fate for r in graded.rows if r.reference == assertion_id(row)
        ) == ("wrong_value")

    def test_an_unsigned_alias_rewrites_nothing(self, tmp_path):
        """A draft alias is a draft: the signed reference carries only signed ones."""
        golden = case("01")
        source = CORPUS / golden.id
        target = tmp_path / golden.id
        target.mkdir()
        for path in source.iterdir():
            if path.is_file():
                (target / path.name).write_bytes(path.read_bytes())
        facts = json.loads((target / "facts.json").read_text("utf-8"))
        facts["aliases"] = {
            "subjects": [
                {
                    "subject": "principal:application-account",
                    "names": ["single application account"],
                    "ruling": "drafted",
                    "reviewed_by": None,
                }
            ],
            "qualifiers": [
                {
                    "kind": "operation",
                    "value": "read-write",
                    "names": ["read and write"],
                    "ruling": "signed",
                    "reviewed_by": "someone",
                }
            ],
        }
        (target / "facts.json").write_text(json.dumps(facts), "utf-8")

        signed = replay.signed_reference(tmp_path, golden)

        assert signed is not None
        assert signed.subject_aliases == {}
        assert signed.qualifier_aliases == {
            ("operation", "read and write"): replay.AliasTarget("read-write")
        }

    def test_a_dropped_row_is_omitted_and_a_new_row_is_unreviewed(
        self, golden, reference
    ):
        row = reference.entries[0]
        extra = Assertion(
            subject=row.subject,
            predicate="storage-encryption",
            value="nothing at rest",
            basis="stated",
        )
        entries = [*reference.entries[1:], extra]

        graded = self.graded(golden, reference, entries)

        assert graded.counts["omitted"] == 1
        assert graded.produced[assertion_id(extra)] == "unreviewed"

    def test_a_credential_on_the_flow_instead_of_its_principal_is_misattached(
        self, golden, reference
    ):
        """The convention the reference follows, read as a countable target."""
        row = next(
            e
            for e in reference.entries
            if e.predicate == "credential-presented"
            and e.subject == "principal:shopper-accounts"
        )
        moved = row.model_copy(
            update={"subject": "flow:entity:shopper>process:storefront-api>place-order"}
        )
        entries = [moved if e is row else e for e in reference.entries]

        graded = self.graded(golden, reference, entries)

        assert graded.counts["omitted"] == 1
        assert graded.produced[assertion_id(moved)] == "misattached"

    def test_an_inferred_fact_claimed_as_stated_is_counted(self, golden, reference):
        row = next(e for e in reference.entries if e.basis == "inferred")
        overstated = row.model_copy(update={"basis": "stated"})
        entries = [overstated if e is row else e for e in reference.entries]

        graded = self.graded(golden, reference, entries)

        assert sum(r.basis_overstated for r in graded.rows) == 1

    def test_a_rejected_row_is_counted_by_row_not_by_issue(self, golden, reference):
        from analysis_service.assertions import CatalogIssue

        issues = [
            CatalogIssue(code="illegal-value", message="x", row=3),
            CatalogIssue(code="missing-scope", message="y", row=3),
        ]

        graded = self.graded(golden, reference, reference.entries, issues)

        assert graded.rejected == 1

    def test_identity_parts_compose_the_id(self, reference):
        for entry in reference.entries:
            parts = identity_parts(entry)
            assert assertion_id(entry) == (
                f"assertion:{parts.predicate}~{parts.subject}~{parts.scope}~{parts.value}"
            )


class TestTheEndpointRidesWithTheFates:
    """Required-fact recall, read where the fates are read.

    The denominator is a property of the signed reference, so a graded sweep
    carries it and every reader — the arm endpoint, the ceiling instrument, the
    pooled replay reading — answers one rule. These hold the pair apart from
    the fate counts, which count the inferred and unknown rows too.
    """

    @pytest.fixture(scope="class")
    def golden(self):
        return case("01")

    @pytest.fixture(scope="class")
    def reference(self, golden):
        catalog = replay.signed_reference(CORPUS, golden)
        if catalog is None:
            pytest.skip("case 01's facts are unsigned, so nothing grades a proposal")
        return catalog

    def graded(self, golden, reference, entries):
        produced = AssertionCatalog(subjects=reference.subjects, entries=list(entries))
        result = AssertionResult(
            golden.id, {"assertions": []}, AssertionRecord(proposed=0, catalog=produced)
        )
        return replay.replay_assertions(golden, reference, result)

    def test_a_perfect_run_recovers_every_required_row(self, golden, reference):
        graded = self.graded(golden, reference, reference.entries)

        assert graded.required == frozenset(replay.required_rows(reference))
        assert graded.recovered == len(graded.required)

    def test_the_denominator_is_narrower_than_the_fates(self, golden, reference):
        """An inferred row and an unknown one are found and never required."""
        graded = self.graded(golden, reference, reference.entries)

        assert graded.counts["found"] > graded.recovered

    def test_a_missing_required_row_lowers_the_numerator(self, golden, reference):
        dropped = next(
            entry
            for entry in reference.entries
            if assertion_id(entry) in set(replay.required_rows(reference))
        )
        entries = [entry for entry in reference.entries if entry is not dropped]

        graded = self.graded(golden, reference, entries)

        assert graded.recovered == len(graded.required) - 1

    def test_a_missing_unrequired_row_does_not(self, golden, reference):
        required = set(replay.required_rows(reference))
        dropped = next(
            entry for entry in reference.entries if assertion_id(entry) not in required
        )
        entries = [entry for entry in reference.entries if entry is not dropped]

        graded = self.graded(golden, reference, entries)

        assert graded.recovered == len(graded.required)

    def test_the_pooled_reading_reports_the_endpoint(self, golden, reference):
        graded = self.graded(golden, reference, reference.entries)
        sweep = replay.SweepReplay(
            artifact="one.json",
            arm=replay.arm_of(load_artifact(ARMS / "arm-A.json")),
            commit="0" * 40,
            clean=True,
            corpus_digest="",
            assertions=(graded,),
        )

        pooled = replay.pooled_assertions([sweep])

        assert pooled["required"] == len(graded.required)
        assert pooled["recovered"] == graded.recovered
        assert graded.to_json()["required"] == len(graded.required)


class TestSpanValidityIsReadApartFromSupport:
    """#926 asks for three readings, not one number.

    A run can quote badly, reason badly, or have had nobody check either. The
    fate table is the second question; these are the first and the third.
    """

    @pytest.fixture(scope="class")
    def golden(self):
        return case("01")

    @pytest.fixture(scope="class")
    def reference(self, golden):
        catalog = replay.signed_reference(CORPUS, golden)
        if catalog is None:
            pytest.skip("case 01's facts are unsigned, so nothing grades a proposal")
        return catalog

    def graded(self, golden, reference, issues=()):
        produced = AssertionCatalog(
            subjects=reference.subjects, entries=list(reference.entries)
        )
        result = AssertionResult(
            golden.id,
            {},
            AssertionRecord(proposed=0, catalog=produced, issues=list(issues)),
        )
        return replay.replay_assertions(golden, reference, result)

    def test_a_clean_run_refuses_no_span(self, golden, reference):
        graded = self.graded(golden, reference)

        assert graded.span_refusals == 0
        assert graded.produced_rows == len(reference.entries)

    def test_a_span_refusal_is_counted_apart_from_every_other_kind(
        self, golden, reference
    ):
        issues = [
            CatalogIssue(code="ambiguous-span", message="twice"),
            CatalogIssue(code="missing-scope", message="no qualifier"),
        ]

        graded = self.graded(golden, reference, issues)

        assert graded.span_refusals == 1
        assert graded.refusals == {"ambiguous-span": 1, "missing-scope": 1}

    def test_every_span_refusal_is_a_gate_refusal(self):
        """A code outside the gate's own vocabulary would count nothing."""
        assert SPAN_REFUSALS <= GATE_REFUSALS

    def test_nothing_writes_a_support_assessment(self, golden, reference):
        """The coverage is zero, and a zero nobody reports reads as unasked."""
        graded = self.graded(golden, reference)

        assert graded.assessed_rows == 0
        assert graded.to_json()["assessed_rows"] == 0


class TestBothReadingsArriveTogether:
    """#1015's ruling: the strict fates and the aligned reading are one result.

    The pair is computed where the fates are, so no caller can report the
    strict figure with an empty aligned one beside it. The gap between them is
    the naming measurement, and these hold the strict half still while the
    looser half moves.
    """

    @pytest.fixture(scope="class")
    def golden(self):
        return case("01")

    @pytest.fixture(scope="class")
    def reference(self, golden):
        catalog = replay.signed_reference(CORPUS, golden)
        if catalog is None:
            pytest.skip("case 01's facts are unsigned, so nothing grades a proposal")
        return catalog

    def graded(self, golden, reference, entries):
        produced = AssertionCatalog(subjects=reference.subjects, entries=list(entries))
        result = AssertionResult(
            golden.id, {"assertions": []}, AssertionRecord(proposed=0, catalog=produced)
        )
        return replay.replay_assertions(golden, reference, result)

    def relabelled(self, reference):
        """One reference row moved to another label between the same endpoints."""
        row = next(
            entry
            for entry in reference.entries
            if entry.subject.startswith(f"{DataFlow.id_prefix}:")
        )
        parts = parse_flow_id(row.subject, flow_id_version(row.subject))
        moved = make_flow_id(parts.source, parts.destination, "another word entirely")
        return row, row.model_copy(update={"subject": moved})

    def test_a_perfect_run_credits_nothing_extra(self, golden, reference):
        """Every row is found strictly, so the gap is empty rather than doubled."""
        graded = self.graded(golden, reference, reference.entries)

        assert graded.credited == frozenset()
        assert len(graded.aligned) == graded.counts["found"]

    def test_a_relabelled_row_is_missed_strictly_and_credited_beside(
        self, golden, reference
    ):
        row, moved = self.relabelled(reference)
        entries = [moved if entry is row else entry for entry in reference.entries]

        graded = self.graded(golden, reference, entries)

        assert graded.counts["omitted"] == 1
        assert graded.credited == {assertion_id(row)}

    def test_the_looser_reading_moves_no_strict_fate(self, golden, reference):
        """The whole rule: crediting a name never changes what the fates say."""
        row, moved = self.relabelled(reference)
        entries = [moved if entry is row else entry for entry in reference.entries]

        graded = self.graded(golden, reference, entries)
        strict = {fate.reference: fate.fate for fate in graded.rows}

        assert strict[assertion_id(row)] == "omitted"
        assert graded.counts["found"] == len(reference.entries) - 1

    def test_the_pooled_reading_reports_the_pair(self, golden, reference):
        row, moved = self.relabelled(reference)
        entries = [moved if entry is row else entry for entry in reference.entries]
        graded = self.graded(golden, reference, entries)
        sweep = replay.SweepReplay(
            artifact="synthetic.json",
            arm=replay.Arm(("assert",), ("digest",), ("a/model",)),
            commit="0000000",
            clean=True,
            corpus_digest="synthetic",
            assertions=(graded,),
        )

        pool = replay.pooled_assertions([sweep])

        assert pool["found"] == pool["fates"]["found"]
        assert pool["relabelled"] == 1


class TestAnUnsignedReferenceGradesNothing:
    def test_a_case_without_a_facts_file_is_none(self, tmp_path):
        """A corpus root holding the case and no reference at all.

        Built here rather than named, because naming a corpus case that
        happens to lack a reference makes this test fail the day somebody
        writes one — which is the day the corpus got better.
        """
        golden = case("01")
        target = tmp_path / golden.id
        target.mkdir()
        for path in (CORPUS / golden.id).iterdir():
            if path.is_file() and path.name != "facts.json":
                (target / path.name).write_bytes(path.read_bytes())

        assert replay.unsigned_rows(tmp_path, golden) is None
        assert replay.signed_reference(tmp_path, golden) is None

    def test_an_unsigned_row_withholds_the_catalog(self, tmp_path):
        golden = case("01")
        source = CORPUS / golden.id
        target = tmp_path / golden.id
        target.mkdir()
        for path in source.iterdir():
            if path.is_file():
                (target / path.name).write_bytes(path.read_bytes())
        facts = json.loads((target / "facts.json").read_text("utf-8"))
        facts["rows"][0]["reviewed_by"] = None
        (target / "facts.json").write_text(json.dumps(facts), "utf-8")

        assert replay.unsigned_rows(tmp_path, golden) == 1
        assert replay.signed_reference(tmp_path, golden) is None


class TestAnArchivedCatalogIsRedrawnFromItsLabels:
    """#1044: a slug rule that moves re-keys the reference and not the archive.

    A principal, a credential and an artifact carry an ID that is a pure
    function of the words a source used, and the archive carries those words.
    So the archive can be brought forward; a graph-bound subject cannot, because
    its ID is an **Element ID** the run's own model decided.
    """

    def catalog(self, *subjects, entries=()):
        return AssertionCatalog(
            subjects=[
                Subject(id=one, type=kind, label=label) for one, kind, label in subjects
            ],
            entries=list(entries),
        )

    def row(self, subject, predicate, value):
        return Assertion(
            subject=subject, predicate=predicate, value=value, basis="stated"
        )

    def test_a_layer_own_subject_is_redrawn_from_its_label(self):
        """The archived spelling of a possessive, under today's rule."""
        held = self.catalog(
            (
                "principal:order-service-s-own-account",
                "principal",
                "order service's own account",
            ),
            entries=[
                self.row(
                    "principal:order-service-s-own-account", "mfa-requirement", "absent"
                )
            ],
        )

        moved = bundle.redrawn(held)

        assert [one.id for one in moved.subjects] == [
            "principal:order-services-own-account"
        ]
        assert moved.entries[0].subject == "principal:order-services-own-account"

    def test_a_graph_bound_subject_is_left_exactly_as_archived(self):
        """No rule here can re-derive an Element ID the run's own model decided."""
        held = self.catalog(
            ("process:order-service-s-worker", "component", "order service's worker"),
        )

        assert bundle.redrawn(held).subjects[0].id == "process:order-service-s-worker"

    def test_a_reference_value_follows_the_subject_it_names(self):
        """A credential a row points at moves with the credential."""
        held = self.catalog(
            ("credential:team-s-token", "credential", "team's token"),
            ("principal:worker", "principal", "worker"),
            entries=[
                self.row(
                    "principal:worker",
                    "credential-presented",
                    "credential:team-s-token",
                )
            ],
        )

        moved = bundle.redrawn(held)

        assert moved.entries[0].value == "credential:teams-token"

    def test_a_move_onto_a_taken_id_is_refused(self):
        """Two labels that slugged apart may slug together; merging them would
        make two subjects the run kept apart into one."""
        held = self.catalog(
            ("principal:team-s-account", "principal", "team's account"),
            ("principal:teams-account", "principal", "teams account"),
        )

        moved = bundle.redrawn(held)

        assert [one.id for one in moved.subjects] == [
            "principal:team-s-account",
            "principal:teams-account",
        ]

    def test_a_catalog_needing_no_move_is_returned_as_it_is(self):
        held = self.catalog(("principal:worker", "principal", "worker"))

        assert bundle.redrawn(held) is held

    def test_the_archive_reads_back_under_todays_rule(self):
        """The one live row, over the sweep this repository archived."""
        golden = case("01")
        result = heads_from_reports(ARMS / "arm-A.json", [golden])[golden.id]
        subjects = {one.id for one in result.catalog.subjects}

        assert "principal:order-services-own-service-account" in subjects
        assert "principal:order-service-s-own-service-account" not in subjects


class TestTheBundleReadsBackWhatItWrote:
    @pytest.fixture(scope="class")
    def golden(self):
        return case("01")

    def test_an_extraction_re_parses_to_the_model_the_sweep_scored(
        self, golden, tmp_path
    ):
        raw = golden.model.model_dump(mode="json")
        sources = {source.label: source.text for source in golden.sources}
        model, issues = parse_and_validate(raw, normalize_ids=True, sources=sources)
        out = tmp_path / "sweep.json"
        write_extractions(
            str(out),
            "extraction",
            {golden.id: ExtractionResult(golden.id, model, tuple(issues), raw=raw)},
        )

        (read,) = extractions_from_reports(out, [golden]).values()

        assert read.raw == raw
        assert read.extracted == model
        assert read.issues == tuple(issues)
        assert read.repair is None

    def test_an_end_to_end_emission_reads_back_with_its_repair(self, golden, tmp_path):
        broken = golden.model.model_dump(mode="json")
        broken["data_flows"][0]["destination"] = "process:does-not-exist"
        repaired = golden.model.model_dump(mode="json")
        out = tmp_path / "sweep.json"
        write_extractions(
            str(out),
            "end-to-end",
            {
                golden.id: ExtractionResult(
                    golden.id, None, (), raw=broken, repair=repaired
                )
            },
        )

        (read,) = extractions_from_reports(out, [golden]).values()

        assert read.raw == broken
        assert read.repair == repaired
        assert [issue.code for issue in read.issues] == ["invalid-reference"]

    def test_a_proposal_re_resolves_to_the_catalog_the_sweep_counted(
        self, golden, tmp_path
    ):
        proposal = {
            "assertions": [
                {
                    "subject_type": "component",
                    "subject": "process:storefront-api",
                    "predicate": "internet-exposure",
                    "value": "internet-facing",
                    "scope": [],
                    "basis": "stated",
                    "quotes": [
                        {
                            "source_label": golden.sources[0].label,
                            "quote": "It is the only thing we expose to the internet.",
                        }
                    ],
                    "explanation": "",
                    "exclusive": False,
                }
            ]
        }
        out = tmp_path / "sweep.json"
        catalog = AssertionCatalog()
        write_assertions(
            str(out),
            "assertions",
            {
                golden.id: AssertionResult(
                    golden.id, proposal, AssertionRecord(proposed=0, catalog=catalog)
                )
            },
        )

        (read,) = assertions_from_reports(out, [golden]).values()

        assert read.proposal == proposal
        assert [e.predicate for e in read.catalog.entries] == ["internet-exposure"]

    def test_a_missing_bundle_is_refused_by_name(self, golden, tmp_path):
        from evals.harness.modes import EvalRunError

        with pytest.raises(EvalRunError, match="does not exist"):
            extractions_from_reports(tmp_path / "none.json", [golden])


#: The artifacts alone. An emission file beside one is named by a suffix the
#: archive declares, and an artifact is the one JSON file no suffix names.
ARTIFACTS = sorted(p for p in EMISSIONS.rglob("*.json") if kind_of(p.name) is None)


class TestTheArchivedEmissionsReplay:
    """The imported archive: every artifact loads, sits on one arm, and replays."""

    @pytest.fixture(scope="class")
    def corpus(self):
        return load_corpus(CORPUS)

    def test_the_archive_is_not_empty(self):
        assert len(ARTIFACTS) >= 21

    @pytest.mark.parametrize("path", ARTIFACTS, ids=[p.stem for p in ARTIFACTS])
    def test_every_artifact_carries_its_emissions_beside_it(self, path):
        loaded = load_artifact(path)
        directory = reports_dir(path)
        files = sorted(directory.iterdir())

        assert files, f"{directory} holds no emission"
        assert {kind_of(f.name) for f in files} == {
            "extraction" if loaded.mode == "extraction" else "assertions"
        }
        assert {f.name.split(".")[0] for f in files} <= set(loaded.cases)
        replay.arm_of(loaded)

    def test_every_extraction_emission_replays(self, corpus):
        sweeps = [
            replay_artifact(path, corpus, CORPUS)
            for path in ARTIFACTS
            if load_artifact(path).mode == "extraction"
        ]
        pool = replay.pooled_extraction(sweeps)

        assert pool["emissions"] == sum(
            len(list(reports_dir(p).iterdir()))
            for p in ARTIFACTS
            if load_artifact(p).mode == "extraction"
        )
        assert sum(pool["fates"].values()) == sum(
            len(list(next(c for c in corpus if c.id == r.case_id).model.elements()))
            for s in sweeps
            for r in s.extractions
        )
        assert pool["by_type"]["flow"]["endpoint_unaligned"] > 0

    def test_an_arm_is_the_model_that_answered_the_node_not_the_tier_table(
        self, tmp_path
    ):
        """Two sweeps on one tier table are two arms when the node ran on two tiers."""
        source = EMISSIONS / "20260914T201030Z-assert-model-benchmark" / "luna-r1.json"
        luna = replay.arm_of(load_artifact(source))
        raw = json.loads(source.read_text("utf-8"))
        terra_model = "openrouter/openai/gpt-5.6-terra"
        for execution in raw["provenance"]["node_runs"]["assert"]:
            execution["tier"] = "strong"
            execution["requested_model"] = terra_model
            execution["served_model"] = terra_model
        # The fingerprint the loader recomputes for that route and tier, so the
        # edited copy is a consistent artifact rather than a refused one.
        terra_fingerprint = (
            "16dcff0b2bbb77672bdf209bd81bee3a2a4b78dd73b680ee1df2ada91013363f"
        )
        for execution in raw["provenance"]["node_runs"]["assert"]:
            execution["generation_fingerprint"] = terra_fingerprint
        identities = raw["provenance"]["generation_identities"]
        identities["strong"] = identities.pop("base") | {
            "requested_models": [terra_model],
            "served_models": [terra_model],
            "fingerprints": [terra_fingerprint],
        }
        moved = tmp_path / "terra-r1.json"
        moved.write_text(json.dumps(raw), "utf-8")

        terra = replay.arm_of(load_artifact(moved))

        assert luna.models == ("openrouter/openai/gpt-5.6-luna",)
        assert terra.models == ("openrouter/openai/gpt-5.6-terra",)
        assert terra.instructions == luna.instructions
        assert terra != luna

    def test_the_spread_is_read_over_the_sweeps_that_ran_every_case(self, corpus):
        """A ceiling is priced against the sd of the fate's per-sweep count."""
        sweeps = [
            replay_artifact(path, corpus, CORPUS)
            for path in ARTIFACTS
            if load_artifact(path).mode == "extraction"
        ]
        by_arm = replay.by_arm(sweeps)
        before = next(
            arm
            for arm in by_arm
            if any(digest.startswith("88f0740b") for digest in arm.instructions)
            and any("luna" in model for model in arm.models)
        )

        spread = replay.pooled_extraction(by_arm[before])["spread"]

        # Six sweeps on the arm; the preflight ran one case and two ran eleven.
        assert spread["sweeps"] == 3
        assert spread["sd"]["found"] == 7.23
        assert spread["by_type"]["entity"]["omitted"] == 0.0

    def test_one_sweep_has_no_spread(self, corpus):
        (sweep,) = [
            replay_artifact(path, corpus, CORPUS)
            for path in ARTIFACTS
            if path.name.endswith("preflight-01.json")
        ]

        spread = replay.pooled_extraction([sweep])["spread"]

        assert spread == {
            "sweeps": 1,
            "sd": dict.fromkeys(replay.FATES),
            "by_type": {
                kind: dict.fromkeys(replay.FATES)
                for kind in ("boundary", "entity", "flow", "process", "store")
            },
        }

    def test_the_command_runs_over_the_archive(self, tmp_path, capsys):
        out = tmp_path / "replay.json"
        code = main(["replay", *map(str, ARTIFACTS[:2]), "--out", str(out)])

        assert code == 0
        printed = capsys.readouterr().out
        assert "targets" in printed
        written = json.loads(out.read_text("utf-8"))
        assert set(written) == {"coordinates", "arms", "sweeps"}
        assert len(written["sweeps"]) == 2

    def test_a_sweep_of_another_mode_is_refused_by_name(
        self,
        corpus,
        tmp_path,
        sampling,  # noqa: F811
    ):
        """An analysis sweep keeps its model inside its report, which this does not read."""
        from evals.harness.modes import EvalRunError

        path = write_run(
            tmp_path, "analysis.json", provenance(sampling), [score(CASE, 4, [0])]
        )

        with pytest.raises(EvalRunError, match="analysis sweep keeps no emission"):
            replay_artifact(path, corpus, CORPUS)

    def test_an_end_to_end_sweep_replays_its_first_pass(
        self,
        corpus,
        tmp_path,
        sampling,  # noqa: F811
    ):
        """The paired runs #961 step 6 asks for are end-to-end, and they grade here."""
        golden = next(c for c in corpus if c.id == CASE)
        path = write_run(
            tmp_path,
            "e2e.json",
            provenance(sampling),
            [score(CASE, 4, [0])],
            mode="end-to-end",
            instruction=[
                {
                    "framework": "(shared)",
                    "node": "extract",
                    "tokens": 1,
                    "sha256": "a" * 64,
                }
            ],
            models={
                "tiers_config_version": 9,
                "tiers": {
                    "base": {"vendor": "openrouter", "model": "x", "upstreams": []}
                },
            },
        )
        raw = golden.model.model_dump(mode="json")
        write_extractions(
            str(path),
            "end-to-end",
            {golden.id: ExtractionResult(golden.id, None, (), raw=raw)},
        )

        sweep = replay_artifact(path, corpus, CORPUS)

        (graded,) = sweep.extractions
        assert graded.counts["found"] == len(list(golden.model.elements()))

    def test_a_case_nobody_signed_is_skipped_by_name(self, corpus, tmp_path):
        bench = next(p for p in ARTIFACTS if "luna-r1" in p.name)
        old = tmp_path / "corpus"
        shutil.copytree(CORPUS, old, ignore=shutil.ignore_patterns("facts.json"))

        sweep = replay_artifact(bench, load_corpus(old), old)

        assert sweep.assertions == ()
        assert sweep.skipped == {
            "01-payments-checkout": "no facts file, so nothing grades it"
        }


class TestBindingAProposalToAnExtractedGraph:
    """Finding 9: fact omission and binding failure, counted apart."""

    @pytest.fixture(scope="class")
    def golden(self):
        return case("01")

    @pytest.fixture(scope="class")
    def proposal(self, golden):
        bench = next(p for p in ARTIFACTS if "luna-r1" in p.name)
        return assertions_from_reports(bench, [golden])[golden.id]

    def bound(self, golden, proposal, model, graph="graph"):
        return replay.bind_assertions(
            golden, proposal, ExtractionResult(golden.id, model, ()), graph
        )

    def test_the_blessed_graph_binds_every_row_the_blessed_resolver_kept(
        self, golden, proposal
    ):
        binding = self.bound(golden, proposal, golden.model)

        assert binding.parsed
        assert not any(binding.counts[fate] for fate in replay.BIND_LOSSES)
        assert binding.counts["bound"] + binding.counts["own"] > 0
        assert binding.counts["refused"] == len(
            {issue.row for issue in proposal.issues if issue.row is not None}
        )
        assert binding.offered == binding.offered_blessed

    def test_a_relabelled_flow_is_a_binding_the_alignment_would_take(
        self, golden, proposal
    ):
        """The flow keeps its endpoints and its discriminators under another
        label, so the resolver's snap misses it and the alignment pairs it."""
        flow = "flow:process:order-service>store:orders-db>read-write-orders"
        assert any(row["subject"] == flow for row in proposal.proposal["assertions"])
        model = renamed(golden.model, flow, "database access")
        binding = self.bound(golden, proposal, model)

        renamed_rows = [row for row in binding.rows if row.fate == "renamed"]
        assert {row.subject for row in renamed_rows} == {flow}
        assert {row.aligned for row in renamed_rows} == {
            "flow:process:order-service>store:orders-db>database-access"
        }
        assert binding.counts["omitted"] == 0

    def test_a_referent_the_graph_names_otherwise_is_charged_to_the_value(
        self, golden, proposal
    ):
        """A zone a component sits in is a reference value, and the alias
        ruling pairs the blessed zone with the produced one."""
        zone_rows = [
            row
            for row in proposal.proposal["assertions"]
            if row["predicate"] == "network-membership"
        ]
        assert zone_rows, "the fixture proposal places a component in a zone"
        model = renamed(golden.model, "boundary:core-services", "core network")
        binding = self.bound(golden, proposal, model)

        referent_rows = [row for row in binding.rows if row.fate == "referent_renamed"]
        assert referent_rows
        assert {row.aligned for row in referent_rows} == {"boundary:core-network"}
        assert all(row.predicate == "network-membership" for row in referent_rows)

    def test_a_dropped_element_is_an_omission_of_the_graph(self, golden, proposal):
        model = without(golden.model, "process:storefront-api")
        binding = self.bound(golden, proposal, model)

        omitted = [row for row in binding.rows if row.fate == "omitted"]
        assert omitted
        assert all(not row.aligned for row in omitted)

    def test_an_emission_that_did_not_parse_binds_nothing(self, golden, proposal):
        binding = self.bound(golden, proposal, None)

        assert not binding.parsed
        assert binding.rows == ()
        assert binding.offered == 0
        assert binding.offered_blessed > 0

    def test_the_archive_binds_and_the_command_runs(self, tmp_path, capsys):
        proposals = [p for p in ARTIFACTS if "luna-r1" in p.name]
        graphs = [p for p in ARTIFACTS if "preflight-01" in p.name]
        out = tmp_path / "bind.json"

        code = main(
            [
                "bind",
                *map(str, proposals),
                "--graphs",
                *map(str, graphs),
                "--out",
                str(out),
            ]
        )

        assert code == 0
        assert "bound" in capsys.readouterr().out
        written = json.loads(out.read_text("utf-8"))
        assert set(written) == {"coordinates", "arms", "bindings"}
        assert written["bindings"]
