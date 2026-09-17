"""The bounded repair pass: what one operation may do, and what a batch costs.

Four groups. The tables are held to the literal they key off, so an operation
kind added on one side fails here. The mechanics are #1003's own acceptance
list: a source-backed component omitted from a graph is added through the
common route, missing evidence prevents the addition, and a stale precondition,
a missing dependency or a cycle cannot leave half a change behind. The
corrections group holds the shape a correction takes, which is a retraction and
an addition rather than an edit. The last group drives the rollback: a batch
whose result fails a gate is discarded whole and the original output stands.
"""

from typing import Any, get_args

import pytest
from pydantic import BaseModel

from analysis_service.assertions import (
    Assertion,
    AssertionCatalog,
    AssertionRecord,
    Subject,
    assertion_id,
)
from analysis_service.factbundle import (
    FactProposal,
    InteractionProposal,
    MentionProposal,
    SourceFactBundle,
    ZonedElement,
    resolve_bundle,
)
from analysis_service.patch import (
    ADDITIONS,
    MAX_OPERATIONS,
    PATCH_VERSION,
    PAYLOAD_FIELDS,
    PAYLOADS,
    Operation,
    OperationKind,
    PatchBatch,
    PatchResult,
    Precondition,
    apply_patch,
    gaps,
)
from analysis_service.system_model import Process, SystemModel, TrustBoundary
from analysis_service.validation import validate
from tests.test_factbundle import LABEL, NOTE, quote

SOURCES = {LABEL: NOTE}


def base() -> tuple[SystemModel, AssertionRecord]:
    """A graph that names the zone and the queue, and never names the worker."""
    resolution = resolve_bundle(
        SourceFactBundle(
            mentions=[
                MentionProposal(
                    handle="z1",
                    text="core network",
                    roles=["network-zone"],
                    quotes=quote("the core network"),
                ),
                MentionProposal(
                    handle="m2",
                    text="queue",
                    roles=["store"],
                    quotes=quote("from a queue"),
                ),
            ],
            facts=[
                FactProposal(
                    handle="p2",
                    subject_kind="mention",
                    subject="m2",
                    predicate="network-membership",
                    value="z1",
                    basis="stated",
                    quotes=quote(NOTE),
                )
            ],
        ),
        SOURCES,
    )
    return resolution.model, resolution.record


def add_worker(handle: str = "o1", **kwargs: Any) -> Operation:
    """The operation that adds the component the base graph never named."""
    return Operation.model_validate(
        {
            "handle": handle,
            "kind": "add-element",
            "reason": "the source names a worker and the graph has none",
            "element": MentionProposal(
                handle="ignored",
                text="worker",
                roles=["process"],
                quotes=kwargs.pop("quotes", quote("A worker")),
            ),
            **kwargs,
        }
    )


def place(handle: str, subject: str, **kwargs: Any) -> Operation:
    """The operation that puts one component in the core network."""
    return Operation.model_validate(
        {
            "handle": handle,
            "kind": "add-assertion",
            "reason": "the source places the worker",
            "assertion": FactProposal(
                handle="ignored",
                subject_kind="mention",
                subject=subject,
                predicate="network-membership",
                value="boundary:core-network",
                basis="stated",
                quotes=quote(NOTE),
            ),
            **kwargs,
        }
    )


def reads(handle: str, initiator: str, receiver: str = "store:queue") -> Operation:
    """The operation that adds the interaction between two components."""
    return Operation(
        handle=handle,
        kind="add-interaction",
        reason="the source says the worker reads the queue",
        interaction=InteractionProposal(
            handle="ignored",
            initiator=initiator,
            receiver=receiver,
            action="read jobs",
            quotes=quote("reads jobs from a queue"),
        ),
    )


def applied(result: PatchResult, handle: str) -> None:
    """Assert one operation landed, naming what stopped it when it did not."""
    outcome = outcome_for(result, handle)
    assert outcome.state == "applied", f"{handle}: {outcome.code} {outcome.message}"


def outcome_for(result: PatchResult, handle: str):
    """The one outcome for one operation handle."""
    found = [outcome for outcome in result.outcomes if outcome.handle == handle]
    assert len(found) == 1, f"{handle} has {len(found)} outcomes, not one"
    return found[0]


class TestTables:
    """The operation tables, held to the literal and to the bundle's schema."""

    def test_every_kind_has_a_payload(self) -> None:
        assert set(PAYLOADS) == set(get_args(OperationKind))

    def test_every_payload_field_is_an_operation_field(self) -> None:
        assert PAYLOAD_FIELDS <= set(Operation.model_fields)

    def test_every_addition_names_a_bundle_table(self) -> None:
        tables = {PAYLOADS[kind].table for kind in ADDITIONS}
        assert tables
        assert tables <= set(SourceFactBundle.model_fields)

    def test_a_kind_that_adds_nothing_names_no_table(self) -> None:
        kinds = set(get_args(OperationKind))
        assert {kind for kind in kinds if not PAYLOADS[kind].table} == (
            kinds - ADDITIONS
        )

    def test_every_reference_field_is_a_field_of_its_payload(self) -> None:
        rows: dict[str, type[BaseModel]] = {
            "mentions": MentionProposal,
            "interactions": InteractionProposal,
            "facts": FactProposal,
        }
        for kind in ADDITIONS:
            rule = PAYLOADS[kind]
            assert set(rule.names) <= set(rows[rule.table].model_fields), kind

    def test_a_kind_fills_the_field_its_rule_names(self) -> None:
        """The table reads a payload by name, so the fields it names are read."""
        interaction = reads("o1", "a")
        assert interaction.interaction is not None
        assert (interaction.element, interaction.assertion) == (None, None)
        element = add_worker()
        assert element.element is not None
        assert (element.interaction, element.assertion) == (None, None)
        fact = place("o2", "o1")
        assert fact.assertion is not None
        assert (fact.element, fact.interaction) == (None, None)


class TestAdding:
    """A component the first pass missed, added through the common route."""

    def test_a_source_backed_component_is_added(self) -> None:
        model, record = base()
        assert model.get("process:worker") is None
        result = apply_patch(
            PatchBatch(operations=[add_worker(), place("o2", "o1"), reads("o3", "o1")]),
            model,
            record,
            SOURCES,
        )
        assert not result.rolled_back
        for handle in ("o1", "o2", "o3"):
            applied(result, handle)
        added = result.model.get("process:worker")
        assert isinstance(added, ZonedElement)
        assert added.trust_zone == "boundary:core-network"
        assert validate(result.model, sources=SOURCES) == []

    def test_the_addition_keeps_what_the_graph_already_held(self) -> None:
        model, record = base()
        result = apply_patch(
            PatchBatch(operations=[add_worker(), place("o2", "o1")]),
            model,
            record,
            SOURCES,
        )
        assert result.model.get("store:queue") is not None
        assert {entry.subject for entry in result.record.catalog.entries} == {
            "store:queue",
            "process:worker",
        }

    def test_missing_evidence_prevents_the_addition(self) -> None:
        """The mechanics are the bundle's, so a quote nobody can find refuses."""
        model, record = base()
        result = apply_patch(
            PatchBatch(operations=[add_worker(quotes=quote("a batch scheduler"))]),
            model,
            record,
            SOURCES,
        )
        assert outcome_for(result, "o1").code == "unlocatable-quote"
        assert result.model.get("process:worker") is None

    def test_an_interaction_reaches_a_component_already_there(self) -> None:
        model, record = base()
        result = apply_patch(
            PatchBatch(operations=[add_worker(), place("o2", "o1"), reads("o3", "o1")]),
            model,
            record,
            SOURCES,
        )
        assert [flow.destination for flow in result.model.data_flows] == ["store:queue"]

    def test_a_question_changes_no_artifact(self) -> None:
        model, record = base()
        result = apply_patch(
            PatchBatch(
                operations=[
                    Operation(
                        handle="o1",
                        kind="mark-unresolved",
                        reason="nobody says what protects the queue",
                        question="is the queue encrypted at rest?",
                    )
                ]
            ),
            model,
            record,
            SOURCES,
        )
        assert outcome_for(result, "o1").state == "unresolved"
        assert result.applied == ()
        assert [row.message for row in gaps(result)] == [
            "is the queue encrypted at rest?"
        ]


class TestRefusals:
    """Nothing lands on a stale reading, a missing dependency, or a cycle."""

    def test_a_stale_absent_precondition_refuses(self) -> None:
        model, record = base()
        result = apply_patch(
            PatchBatch(
                operations=[
                    add_worker(
                        preconditions=[
                            Precondition(state="absent", target="store:queue")
                        ]
                    )
                ]
            ),
            model,
            record,
            SOURCES,
        )
        assert outcome_for(result, "o1").code == "stale-precondition"

    def test_a_stale_present_precondition_refuses(self) -> None:
        model, record = base()
        result = apply_patch(
            PatchBatch(
                operations=[
                    add_worker(
                        preconditions=[
                            Precondition(state="present", target="process:worker")
                        ]
                    )
                ]
            ),
            model,
            record,
            SOURCES,
        )
        assert outcome_for(result, "o1").code == "stale-precondition"

    def test_a_precondition_the_state_holds_lets_the_operation_stand(self) -> None:
        model, record = base()
        held = assertion_id(record.catalog.entries[0])
        result = apply_patch(
            PatchBatch(
                operations=[
                    add_worker(
                        preconditions=[
                            Precondition(state="absent", target="process:worker"),
                            Precondition(state="present", target=held),
                        ]
                    ),
                    place("o2", "o1"),
                ]
            ),
            model,
            record,
            SOURCES,
        )
        applied(result, "o1")

    def test_a_refused_operation_refuses_what_names_it(self) -> None:
        """The resolver refused the element, so it refuses the rows reaching it."""
        model, record = base()
        result = apply_patch(
            PatchBatch(
                operations=[
                    add_worker(quotes=quote("a batch scheduler")),
                    place("o2", "o1"),
                    reads("o3", "o1"),
                ]
            ),
            model,
            record,
            SOURCES,
        )
        assert outcome_for(result, "o2").code == "dangling-subject"
        assert outcome_for(result, "o3").code == "dangling-endpoint"
        assert result.applied == ()
        assert result.model.get("process:worker") is None

    def test_a_shape_refusal_refuses_what_names_it(self) -> None:
        """The walk before the resolver, which no later reader would make."""
        model, record = base()
        malformed = add_worker().model_copy(update={"question": "and what else?"})
        result = apply_patch(
            PatchBatch(operations=[malformed, reads("o3", "o1")]),
            model,
            record,
            SOURCES,
        )
        assert outcome_for(result, "o1").code == "wrong-payload"
        assert outcome_for(result, "o3").code == "refused-dependency"
        assert "o1" in outcome_for(result, "o3").message

    def test_a_refusal_reaches_the_end_of_a_chain(self) -> None:
        model, record = base()
        chained = Operation(
            handle="o4",
            kind="add-assertion",
            reason="the source names the transport",
            assertion=FactProposal(
                handle="ignored",
                subject_kind="interaction",
                subject="o3",
                predicate="transport-encryption",
                value="TLS 1.3",
                basis="stated",
                quotes=quote("reads jobs"),
            ),
        )
        result = apply_patch(
            PatchBatch(
                operations=[
                    add_worker(quotes=quote("a batch scheduler")),
                    place("o2", "o1"),
                    reads("o3", "o1"),
                    chained,
                ]
            ),
            model,
            record,
            SOURCES,
        )
        assert outcome_for(result, "o4").code == "dangling-subject"
        assert result.applied == ()

    def test_operations_that_reach_each_other_all_refuse(self) -> None:
        model, record = base()
        result = apply_patch(
            PatchBatch(operations=[reads("o1", "o2", "o2"), reads("o2", "o1", "o1")]),
            model,
            record,
            SOURCES,
        )
        assert {outcome_for(result, handle).code for handle in ("o1", "o2")} == {
            "circular-operation"
        }

    def test_an_operation_carrying_the_wrong_payload_refuses(self) -> None:
        model, record = base()
        wrong = Operation(
            handle="o1",
            kind="add-element",
            reason="the source names a worker",
            interaction=InteractionProposal(
                handle="ignored",
                initiator="a",
                receiver="b",
                action="read jobs",
                quotes=quote("reads jobs"),
            ),
        )
        result = apply_patch(PatchBatch(operations=[wrong]), model, record, SOURCES)
        assert outcome_for(result, "o1").code == "wrong-payload"

    def test_an_operation_carrying_two_payloads_refuses(self) -> None:
        model, record = base()
        both = add_worker().model_copy(update={"question": "and what else?"})
        result = apply_patch(PatchBatch(operations=[both]), model, record, SOURCES)
        assert outcome_for(result, "o1").code == "wrong-payload"

    @pytest.mark.parametrize(
        ("batch", "code"),
        (
            (
                PatchBatch(patch_version=PATCH_VERSION + 1, operations=[]),
                "wrong-version",
            ),
            (
                PatchBatch(operations=[add_worker(), add_worker()]),
                "duplicate-handle",
            ),
        ),
    )
    def test_a_batch_refused_whole_reports_one_row(
        self, batch: PatchBatch, code: str
    ) -> None:
        model, record = base()
        result = apply_patch(batch, model, record, SOURCES)
        assert result.rolled_back
        assert [outcome.code for outcome in result.outcomes] == [code]
        assert result.model is model
        assert result.record is record

    def test_a_batch_over_the_cap_is_refused_whole(self) -> None:
        model, record = base()
        batch = PatchBatch(
            operations=[add_worker(f"o{index}") for index in range(MAX_OPERATIONS + 1)]
        )
        result = apply_patch(batch, model, record, SOURCES)
        assert [outcome.code for outcome in result.outcomes] == ["too-many-operations"]


class TestCorrections:
    """A correction is a retraction and an addition, never an edit."""

    def test_a_retraction_drops_the_row(self) -> None:
        model, record = base()
        held = assertion_id(record.catalog.entries[0])
        result = apply_patch(
            PatchBatch(
                operations=[
                    Operation(
                        handle="o1",
                        kind="retract-assertion",
                        reason="no source places the queue",
                        retract=held,
                    )
                ]
            ),
            model,
            record,
            SOURCES,
        )
        applied(result, "o1")
        assert result.record.catalog.entries == []
        assert result.record.catalog.subjects == []

    def test_a_correction_leaves_the_new_value_alone_in_the_catalog(self) -> None:
        model = base()[0]
        model.data_stores[0].encryption_at_rest = "AES-256"
        stated = AssertionRecord(
            proposed=1,
            catalog=AssertionCatalog(
                subjects=[Subject(id="store:queue", type="component", label="queue")],
                entries=[
                    Assertion(
                        subject="store:queue",
                        predicate="storage-encryption",
                        value="AES-128",
                        basis="inferred",
                        explanation="the note does not say which cipher",
                    )
                ],
            ),
        )
        wrong = assertion_id(stated.catalog.entries[0])
        result = apply_patch(
            PatchBatch(
                operations=[
                    Operation(
                        handle="o1",
                        kind="retract-assertion",
                        reason="the value was inferred and the source states one",
                        retract=wrong,
                    ),
                    Operation(
                        handle="o2",
                        kind="add-assertion",
                        reason="the source states the cipher",
                        assertion=FactProposal(
                            handle="ignored",
                            subject_kind="mention",
                            subject="store:queue",
                            predicate="storage-encryption",
                            value="AES-256",
                            basis="stated",
                            quotes=quote("from a queue"),
                        ),
                    ),
                ]
            ),
            model,
            stated,
            SOURCES,
        )
        assert not result.rolled_back
        assert [
            (entry.predicate, entry.value) for entry in result.record.catalog.entries
        ] == [("storage-encryption", "AES-256")]


class TestRollback:
    """A batch whose result fails a gate leaves the original output standing."""

    def test_a_gate_refusal_discards_the_whole_batch(self) -> None:
        """A review over a graph the gate already refuses changes nothing."""
        broken = SystemModel(
            processes=[
                Process(
                    id="process:worker",
                    name="worker",
                    technology="unknown",
                    trust_zone="boundary:missing",
                    exposure="unknown",
                    interface_kind="unknown",
                    source_excerpt="A worker",
                    source_label=LABEL,
                )
            ],
            trust_boundaries=[
                TrustBoundary(
                    id="boundary:core-network",
                    name="core network",
                    kind="network",
                    source_excerpt="the core network",
                    source_label=LABEL,
                )
            ],
        )
        record = AssertionRecord(proposed=0, catalog=AssertionCatalog())
        result = apply_patch(
            PatchBatch(
                operations=[
                    Operation(
                        handle="o1",
                        kind="add-assertion",
                        reason="the source names the queue nobody modelled",
                        assertion=FactProposal(
                            handle="ignored",
                            subject_kind="mention",
                            subject="process:worker",
                            predicate="internet-exposure",
                            value="internal",
                            basis="stated",
                            quotes=quote("A worker in the core network"),
                        ),
                    )
                ]
            ),
            broken,
            record,
            SOURCES,
        )
        assert result.rolled_back
        assert result.model is broken
        assert result.record is record
        assert result.applied == ()
        codes = [outcome.code for outcome in result.outcomes]
        assert "gate-refused" in codes
        assert "batch-rolled-back" in codes
        assert any(
            "invalid-reference" in outcome.message for outcome in result.refusals
        )

    def test_an_empty_batch_changes_nothing(self) -> None:
        model, record = base()
        result = apply_patch(PatchBatch(), model, record, SOURCES)
        assert not result.rolled_back
        assert result.outcomes == ()
        assert [element.id for element in result.model.elements()] == [
            element.id for element in model.elements()
        ]
        assert result.record.catalog.entries == record.catalog.entries
