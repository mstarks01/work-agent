"""The Source Fact Bundle: its vocabulary tables, and what its resolver builds.

Four groups, and the first two are #1003's offline acceptance list. The
vocabulary tables are held to the registries they key off, so a role, a refusal
code or a placement predicate added on one side fails here rather than reading
as silence. The resolver is driven over the shapes a source can take — a
repeated line, a quote in another source, a name two mentions share, two flows
between one pair of endpoints — and each one has to fail visibly or resolve.

The third group is the accounting contract this prototype exists to measure
against: **every input row gets exactly one disposition**, so a fact that
reaches neither the graph nor the catalog is attributable rather than missing.

The fourth holds the production route still: the ambiguity rule the gate reads
and the one this module reads are one function, driven from both sides.
"""

from typing import get_args

import pytest

from analysis_service.assertions import (
    GRAPH_BOUND,
    REGISTRY,
    AssertionProposal,
    CatalogIssueCode,
    CatalogProposal,
    QuoteProposal,
    ambiguous_quote,
    conflicts,
    resolve_catalog,
    span_source,
)
from analysis_service.factbundle import (
    BUNDLE_VERSION,
    GRAPH_REFERENTS,
    LANDED,
    MAX_MENTIONS,
    PLACEMENT_PREDICATES,
    REFUSAL_DISPOSITIONS,
    ROLES,
    ZONE_ROLES,
    DispositionCode,
    FactProposal,
    InteractionProposal,
    MentionProposal,
    Resolution,
    Role,
    RowKind,
    SourceFactBundle,
    TrustBoundary,
    UnresolvedProposal,
    resolve_bundle,
    unstated_fields,
)
from analysis_service.system_model import UNKNOWN, Element, SystemModel
from analysis_service.validation import validate

LABEL = "Architecture note"
NOTE = "A worker in the core network reads jobs from a queue."


def quote(text: str, label: str = LABEL) -> list[QuoteProposal]:
    """One proposed quote, in the shape every row here carries."""
    return [QuoteProposal(source_label=label, quote=text)]


def mention(handle: str, text: str, role: str, **kwargs: object) -> MentionProposal:
    """One mention citing its own name, which is what most rows here need."""
    return MentionProposal.model_validate(
        {
            "handle": handle,
            "text": text,
            "roles": [role],
            "quotes": kwargs.pop("quotes", quote(text)),
            **kwargs,
        }
    )


def placed(handle: str, zone: str, **kwargs: object) -> FactProposal:
    """The fact that puts one mention in one zone."""
    return FactProposal.model_validate(
        {
            "handle": f"p_{handle}",
            "subject_kind": "mention",
            "subject": handle,
            "predicate": "network-membership",
            "value": zone,
            "basis": "stated",
            "quotes": quote(NOTE),
            **kwargs,
        }
    )


def worker_bundle(**kwargs: object) -> SourceFactBundle:
    """The smallest bundle that resolves to a model the validity gate accepts."""
    return SourceFactBundle.model_validate(
        {
            "mentions": [
                mention("z1", "core network", "network-zone"),
                mention("m1", "worker", "process"),
                mention("m2", "queue", "store"),
            ],
            "interactions": [
                InteractionProposal(
                    handle="i1",
                    initiator="m1",
                    receiver="m2",
                    action="read jobs",
                    quotes=quote("reads jobs from a queue"),
                )
            ],
            "facts": [placed("m1", "z1"), placed("m2", "z1")],
            **kwargs,
        }
    )


def resolved(bundle: SourceFactBundle, sources: dict[str, str] | None = None):
    """Resolve one bundle against the default source."""
    return resolve_bundle(bundle, sources if sources is not None else {LABEL: NOTE})


def row_for(resolution: Resolution, handle: str):
    """The one disposition row for one handle."""
    rows = [row for row in resolution.dispositions if row.handle == handle]
    assert len(rows) == 1, f"{handle} has {len(rows)} rows, not one"
    return rows[0]


class TestVocabulary:
    """The tables, held to the registries they key off."""

    def test_every_role_has_a_rule(self) -> None:
        assert set(ROLES) == set(get_args(Role))

    def test_zone_roles_are_the_boundary_rules(self) -> None:
        assert ZONE_ROLES == {
            role for role, rule in ROLES.items() if rule.element is TrustBoundary
        }

    def test_every_role_builds_a_legal_element(self) -> None:
        """No role leaves a required field the schema has no ``unknown`` for."""
        for role, rule in ROLES.items():
            built = rule.element.model_validate(
                {
                    "id": f"{rule.element.id_prefix}:thing",
                    "name": "thing",
                    **rule.fixed,
                    **unstated_fields(rule.element, rule.fixed),
                }
            )
            assert built.id.startswith(rule.element.id_prefix), role

    def test_a_required_closed_field_with_no_unknown_raises(self) -> None:
        """The guard that makes a new schema field a failure rather than a guess."""
        with pytest.raises(ValueError, match="admits no 'unknown'"):
            unstated_fields(TrustBoundary, ())

    def test_every_refusal_has_a_disposition(self) -> None:
        assert set(REFUSAL_DISPOSITIONS) == set(get_args(CatalogIssueCode))
        assert set(REFUSAL_DISPOSITIONS.values()) <= set(get_args(DispositionCode))

    def test_placement_predicates_are_the_ones_writing_the_zone(self) -> None:
        assert PLACEMENT_PREDICATES
        assert all(
            REGISTRY[name].projects_into == "trust_zone"
            for name in PLACEMENT_PREDICATES
        )

    def test_graph_referents_point_at_graph_subjects(self) -> None:
        assert GRAPH_REFERENTS
        assert all(REGISTRY[name].refers_to <= GRAPH_BOUND for name in GRAPH_REFERENTS)

    def test_landed_is_the_complement_of_a_gap(self) -> None:
        assert LANDED < set(get_args(DispositionCode))
        assert "bundle" in get_args(RowKind)

    def test_a_bundle_defaults_to_the_current_spelling(self) -> None:
        assert SourceFactBundle().bundle_version == BUNDLE_VERSION


class TestCitations:
    """What a quote can be, and what each shape costs the row that proposed it."""

    def test_a_resolved_bundle_passes_the_validity_gate(self) -> None:
        resolution = resolved(worker_bundle())
        assert validate(resolution.model, sources={LABEL: NOTE}) == []
        assert resolution.gaps == ()

    def test_the_record_is_what_a_consumer_already_reads(self) -> None:
        """The adapter hands on the shape every evidence consumer takes today."""
        resolution = resolved(worker_bundle())
        assert resolution.record.issues == []
        assert resolution.record.proposed == len(resolution.record.catalog.entries)

    def test_an_element_carries_the_words_it_was_taken_from(self) -> None:
        resolution = resolved(worker_bundle())
        found = resolution.model.get("process:worker")
        assert found is not None
        assert found.source_excerpt == "worker"
        assert found.source_label == LABEL

    def test_a_row_with_no_quote_is_rejected(self) -> None:
        bundle = worker_bundle()
        bundle.mentions[1] = mention("m1", "worker", "process", quotes=[])
        assert row_for(resolved(bundle), "m1").code == "uncited"

    def test_a_quote_naming_no_source_is_rejected(self) -> None:
        bundle = worker_bundle()
        bundle.mentions[1] = mention(
            "m1", "worker", "process", quotes=quote("worker", "Kickoff call")
        )
        assert row_for(resolved(bundle), "m1").code == "dangling-source"

    def test_a_quote_the_source_does_not_hold_is_rejected(self) -> None:
        bundle = worker_bundle()
        bundle.mentions[1] = mention(
            "m1", "worker", "process", quotes=quote("a batch scheduler")
        )
        assert row_for(resolved(bundle), "m1").code == "unlocatable-quote"

    def test_a_quote_the_source_holds_twice_is_rejected(self) -> None:
        """A repeated line names no one of its placements, so it cites nothing."""
        twice = "The worker runs. The worker runs. It sits in the core network."
        bundle = worker_bundle()
        bundle.mentions[1] = mention(
            "m1", "worker", "process", quotes=quote("The worker runs")
        )
        assert row_for(resolved(bundle, {LABEL: twice}), "m1").code == (
            "ambiguous-quote"
        )

    def test_a_later_quote_stands_in_for_a_refused_one(self) -> None:
        bundle = worker_bundle()
        bundle.mentions[1] = mention(
            "m1",
            "worker",
            "process",
            quotes=[*quote("a batch scheduler"), *quote("A worker")],
        )
        row = row_for(resolved(bundle), "m1")
        assert row.disposition == "consumed"

    def test_each_mention_cites_the_source_it_names(self) -> None:
        call = "The queue is drained every minute."
        bundle = worker_bundle()
        bundle.mentions[2] = mention(
            "m2", "queue", "store", quotes=quote("The queue is drained", "Kickoff call")
        )
        resolution = resolved(bundle, {LABEL: NOTE, "Kickoff call": call})
        found = resolution.model.get("store:queue")
        assert found is not None
        assert found.source_label == "Kickoff call"

    @pytest.mark.parametrize("terminator", ("\u2028", "\u2029", "\u0085"))
    def test_a_line_terminator_does_not_break_a_quote(self, terminator: str) -> None:
        """Three line terminators ``str`` knows and a split on ``"\\n"`` does not."""
        split = NOTE.replace("network reads", f"network{terminator}reads")
        bundle = worker_bundle()
        bundle.interactions[0] = InteractionProposal(
            handle="i1",
            initiator="m1",
            receiver="m2",
            action="read jobs",
            quotes=quote("core network reads jobs"),
        )
        assert row_for(resolved(bundle, {LABEL: split}), "i1").disposition == "consumed"


class TestRoles:
    """What a mention becomes, and what it stays when the bundle did not settle."""

    def test_two_roles_stay_unresolved(self) -> None:
        bundle = worker_bundle()
        bundle.mentions[1] = MentionProposal(
            handle="m1",
            text="worker",
            roles=["process", "external-system"],
            quotes=quote("worker"),
        )
        row = row_for(resolved(bundle), "m1")
        assert (row.disposition, row.code) == ("unresolved", "competing-roles")

    def test_no_role_stays_unresolved(self) -> None:
        bundle = worker_bundle()
        bundle.mentions[1] = MentionProposal(
            handle="m1", text="worker", roles=[], quotes=quote("worker")
        )
        row = row_for(resolved(bundle), "m1")
        assert (row.disposition, row.code) == ("unresolved", "no-role")

    def test_one_name_under_two_types_is_two_elements(self) -> None:
        """An **Element ID** carries its type, so ``api`` twice is not a collision."""
        text = "The api talks to the api in the core network."
        bundle = SourceFactBundle(
            mentions=[
                mention("z1", "core network", "network-zone"),
                mention("m1", "api", "process", quotes=quote("The api talks")),
                mention("m2", "api", "external-system", quotes=quote("to the api")),
            ],
            facts=[placed("m1", "z1"), placed("m2", "z1")],
        )
        resolution = resolved(bundle, {LABEL: text})
        assert {element.id for element in resolution.model.elements()} >= {
            "process:api",
            "entity:api",
        }

    def test_one_name_under_one_type_is_a_duplicate(self) -> None:
        text = "The worker in the core network. The other worker elsewhere."
        bundle = SourceFactBundle(
            mentions=[
                mention("z1", "core network", "network-zone"),
                mention("m1", "worker", "process", quotes=quote("The worker in")),
                mention("m2", "worker", "process", quotes=quote("other worker")),
            ],
            facts=[placed("m1", "z1"), placed("m2", "z1")],
        )
        row = row_for(resolved(bundle, {LABEL: text}), "m2")
        assert (row.disposition, row.code) == ("rejected", "duplicate-element")


class TestPlacement:
    """Where a component sits: stated, assumed on the sole zone, or unsupported."""

    def test_a_stated_zone_is_the_zone(self) -> None:
        resolution = resolved(worker_bundle())
        found = resolution.model.get("process:worker")
        assert found is not None
        assert found.trust_zone == "boundary:core-network"
        assert resolution.model.assumptions == []

    def test_the_sole_zone_is_taken_on_the_record(self) -> None:
        bundle = worker_bundle()
        bundle.facts = [placed("m2", "z1")]
        resolution = resolved(bundle)
        found = resolution.model.get("process:worker")
        assert found is not None
        assert found.trust_zone == "boundary:core-network"
        assumed = [
            assumption
            for assumption in resolution.model.assumptions
            if assumption.element_id == "process:worker"
        ]
        assert len(assumed) == 1
        assert assumed[0].attribute == "trust_zone"
        assert validate(resolution.model, sources={LABEL: NOTE}) == []

    def test_two_zones_and_no_fact_leaves_a_component_unsupported(self) -> None:
        text = "A worker. The core network and the admin network."
        bundle = SourceFactBundle(
            mentions=[
                mention("z1", "core network", "network-zone"),
                mention("z2", "admin network", "privilege-zone"),
                mention("m1", "worker", "process", quotes=quote("A worker")),
            ],
        )
        row = row_for(resolved(bundle, {LABEL: text}), "m1")
        assert (row.disposition, row.code) == ("unsupported", "unplaced")
        assert row in resolved(bundle, {LABEL: text}).gaps

    def test_two_placements_of_one_component_settle_nothing(self) -> None:
        text = "A worker. The core network and the admin network."
        bundle = SourceFactBundle(
            mentions=[
                mention("z1", "core network", "network-zone"),
                mention("z2", "admin network", "privilege-zone"),
                mention("m1", "worker", "process", quotes=quote("A worker")),
            ],
            facts=[
                placed("m1", "z1"),
                FactProposal(
                    handle="p_second",
                    subject_kind="mention",
                    subject="m1",
                    predicate="network-membership",
                    value="z2",
                    basis="stated",
                    quotes=quote("A worker"),
                ),
            ],
        )
        row = row_for(resolved(bundle, {LABEL: text}), "m1")
        assert (row.disposition, row.code) == ("unsupported", "competing-placement")

    def test_a_zone_handle_naming_nothing_is_refused(self) -> None:
        bundle = worker_bundle()
        bundle.facts = [placed("m1", "z9"), placed("m2", "z1")]
        row = row_for(resolved(bundle), "m1")
        assert (row.disposition, row.code) == ("unsupported", "dangling-zone")

    def test_no_zone_invents_no_boundary(self) -> None:
        """The gate refuses the model rather than the resolver inventing a zone."""
        bundle = SourceFactBundle(
            mentions=[mention("m1", "worker", "process", quotes=quote("A worker"))]
        )
        resolution = resolved(bundle)
        assert resolution.model.trust_boundaries == []
        codes = {issue.code for issue in validate(resolution.model)}
        assert "no-trust-zones" in codes


class TestInteractions:
    """What a flow is built from, and what keeps two of them apart."""

    def test_parallel_interactions_stay_separately_addressable(self) -> None:
        text = "A worker in the core network reads jobs from a queue and streams events to it."
        bundle = worker_bundle()
        bundle.interactions.append(
            InteractionProposal(
                handle="i2",
                initiator="m1",
                receiver="m2",
                action="stream events",
                quotes=quote("streams events to it"),
            )
        )
        resolution = resolved(bundle, {LABEL: text})
        assert len({flow.id for flow in resolution.model.data_flows}) == 2

    def test_two_interactions_of_one_verb_are_one_flow(self) -> None:
        bundle = worker_bundle()
        bundle.interactions.append(
            InteractionProposal(
                handle="i2",
                initiator="m1",
                receiver="m2",
                action="read jobs",
                quotes=quote("reads jobs from a queue"),
            )
        )
        row = row_for(resolved(bundle), "i2")
        assert (row.disposition, row.code) == ("rejected", "duplicate-flow")

    def test_an_endpoint_naming_a_zone_is_refused(self) -> None:
        bundle = worker_bundle()
        bundle.interactions[0] = InteractionProposal(
            handle="i1",
            initiator="m1",
            receiver="z1",
            action="read jobs",
            quotes=quote("reads jobs from a queue"),
        )
        row = row_for(resolved(bundle), "i1")
        assert (row.disposition, row.code) == ("rejected", "dangling-endpoint")

    def test_an_endpoint_the_resolver_dropped_is_refused(self) -> None:
        bundle = worker_bundle()
        bundle.mentions[2] = MentionProposal(
            handle="m2", text="queue", roles=[], quotes=quote("queue")
        )
        bundle.facts = [placed("m1", "z1")]
        row = row_for(resolved(bundle), "i1")
        assert (row.disposition, row.code) == ("rejected", "dangling-endpoint")


class TestFacts:
    """What a fact becomes, and the accounting that says so for every row."""

    def test_every_input_row_gets_one_disposition(self) -> None:
        bundle = worker_bundle(
            unresolved=[
                UnresolvedProposal(handle="u1", question="does the queue hold PII?")
            ]
        )
        resolution = resolved(bundle)
        rows = len(bundle.mentions) + len(bundle.interactions)
        rows += len(bundle.facts) + len(bundle.unresolved)
        assert len(resolution.dispositions) == rows
        assert len({row.handle for row in resolution.dispositions}) == rows

    def test_an_open_question_is_a_gap(self) -> None:
        bundle = worker_bundle(
            unresolved=[
                UnresolvedProposal(handle="u1", question="does the queue hold PII?")
            ]
        )
        row = row_for(resolved(bundle), "u1")
        assert (row.disposition, row.code) == ("unresolved", "open-question")
        assert row.message == "does the queue hold PII?"

    def test_a_predicate_with_no_graph_field_is_preserved(self) -> None:
        bundle = worker_bundle()
        bundle.facts.append(
            FactProposal(
                handle="f1",
                subject_kind="interaction",
                subject="i1",
                predicate="mfa-requirement",
                value="required",
                basis="stated",
                quotes=quote("reads jobs"),
            )
        )
        assert row_for(resolved(bundle), "f1").disposition == "preserved"

    def test_a_predicate_the_registry_has_no_entry_for_is_unsupported(self) -> None:
        bundle = worker_bundle()
        bundle.facts.append(
            FactProposal(
                handle="f1",
                subject_kind="mention",
                subject="m2",
                predicate="backup-frequency",
                value="nightly",
                basis="stated",
                quotes=quote("a queue"),
            )
        )
        row = row_for(resolved(bundle), "f1")
        assert (row.disposition, row.code) == ("unsupported", "unknown-predicate")

    def test_a_subject_naming_nothing_is_rejected(self) -> None:
        bundle = worker_bundle()
        bundle.facts.append(
            FactProposal(
                handle="f1",
                subject_kind="mention",
                subject="m9",
                predicate="storage-encryption",
                value="AES-256",
                basis="stated",
                quotes=quote("a queue"),
            )
        )
        row = row_for(resolved(bundle), "f1")
        assert (row.disposition, row.code) == ("rejected", "dangling-subject")

    def test_a_handle_of_the_wrong_kind_is_rejected(self) -> None:
        bundle = worker_bundle()
        bundle.facts.append(
            FactProposal(
                handle="f1",
                subject_kind="mention",
                subject="i1",
                predicate="storage-encryption",
                value="AES-256",
                basis="stated",
                quotes=quote("a queue"),
            )
        )
        row = row_for(resolved(bundle), "f1")
        assert (row.disposition, row.code) == ("rejected", "wrong-handle-kind")

    def test_a_reference_to_the_wrong_type_is_rejected(self) -> None:
        bundle = worker_bundle()
        bundle.facts.append(
            FactProposal(
                handle="f1",
                subject_kind="principal",
                subject="operator",
                predicate="administrative-authority",
                value="z1",
                basis="stated",
                quotes=quote("a queue"),
            )
        )
        row = row_for(resolved(bundle), "f1")
        assert (row.disposition, row.code) == ("rejected", "wrong-referent-type")

    def test_a_named_subject_needs_no_handle(self) -> None:
        bundle = worker_bundle()
        bundle.facts.append(
            FactProposal(
                handle="f1",
                subject_kind="credential",
                subject="queue token",
                predicate="credential-expiry",
                value="does-not-expire",
                basis="stated",
                quotes=quote("reads jobs"),
            )
        )
        resolution = resolved(bundle)
        assert row_for(resolution, "f1").disposition == "preserved"
        assert any(
            subject.id == "credential:queue-token"
            for subject in resolution.record.catalog.subjects
        )

    def test_an_absence_a_silence_and_a_hedge_stay_apart(self) -> None:
        bundle = worker_bundle()
        bundle.facts.extend(
            [
                FactProposal(
                    handle="f1",
                    subject_kind="mention",
                    subject="m2",
                    predicate="storage-encryption",
                    value="absent",
                    basis="stated",
                    quotes=quote("a queue"),
                ),
                FactProposal(
                    handle="f2",
                    subject_kind="interaction",
                    subject="i1",
                    predicate="transport-encryption",
                    value=UNKNOWN,
                    reason="silent",
                    basis="stated",
                ),
                FactProposal(
                    handle="f3",
                    subject_kind="interaction",
                    subject="i1",
                    predicate="authentication-mechanism",
                    value=UNKNOWN,
                    reason="hedged",
                    basis="stated",
                ),
            ]
        )
        resolution = resolved(bundle)
        held = {
            (entry.predicate, entry.value, entry.reason)
            for entry in resolution.record.catalog.entries
        }
        assert ("storage-encryption", "absent", None) in held
        assert ("transport-encryption", UNKNOWN, "silent") in held
        assert ("authentication-mechanism", UNKNOWN, "hedged") in held

    def test_two_sources_that_disagree_stay_a_conflict(self) -> None:
        call = "The queue is not encrypted at rest."
        bundle = worker_bundle()
        bundle.facts.extend(
            [
                FactProposal(
                    handle="f1",
                    subject_kind="mention",
                    subject="m2",
                    predicate="storage-encryption",
                    value="AES-256",
                    basis="stated",
                    quotes=quote("a queue"),
                ),
                FactProposal(
                    handle="f2",
                    subject_kind="mention",
                    subject="m2",
                    predicate="storage-encryption",
                    value="absent",
                    basis="stated",
                    quotes=quote("not encrypted at rest", "Kickoff call"),
                ),
            ]
        )
        resolution = resolved(bundle, {LABEL: NOTE, "Kickoff call": call})
        found = conflicts(resolution.record.catalog)
        assert [conflict.predicate for conflict in found] == ["storage-encryption"]

    def test_an_identification_this_service_inferred_is_refused(self) -> None:
        """A workload is not its account because an array put the two together."""
        bundle = worker_bundle()
        bundle.facts.append(
            FactProposal(
                handle="f1",
                subject_kind="principal",
                subject="worker account",
                predicate="represented-by",
                value="m1",
                basis="inferred",
                explanation="the names look alike",
            )
        )
        row = row_for(resolved(bundle), "f1")
        assert (row.disposition, row.code) == ("rejected", "inference-refused")


class TestHandles:
    """One namespace across four tables, and what a repeat costs every claimant."""

    def test_a_repeated_handle_rejects_every_row_that_claims_it(self) -> None:
        bundle = worker_bundle(
            unresolved=[UnresolvedProposal(handle="m1", question="which worker?")]
        )
        rows = [row for row in resolved(bundle).dispositions if row.handle == "m1"]
        assert len(rows) == 2
        assert {row.code for row in rows} == {"duplicate-handle"}
        assert {row.kind for row in rows} == {"mention", "unresolved"}

    @pytest.mark.parametrize("field", ("bundle_version", "role_version"))
    def test_another_spelling_of_the_schema_is_refused_whole(self, field: str) -> None:
        bundle = worker_bundle(**{field: 2})
        resolution = resolved(bundle)
        assert len(resolution.dispositions) == 1
        assert resolution.dispositions[0].code == "wrong-version"
        assert field in resolution.dispositions[0].message

    def test_a_bundle_over_the_cap_is_refused_alone(self) -> None:
        bundle = SourceFactBundle(
            mentions=[
                mention(f"m{index}", f"thing {index}", "process")
                for index in range(MAX_MENTIONS + 1)
            ]
        )
        resolution = resolved(bundle)
        assert len(resolution.dispositions) == 1
        assert resolution.dispositions[0].code == "too-many-rows"
        assert resolution.dispositions[0].kind == "bundle"
        assert resolution.model == SystemModel()
        assert resolution.record.proposed == 0


class TestOneAmbiguityReader:
    """The gate and this module refuse a repeated quote through one function."""

    TWICE = "The worker runs. The worker runs."

    def test_the_gate_reads_it(self) -> None:
        model = SystemModel()
        _, issues = resolve_catalog(
            CatalogProposal(
                assertions=[
                    AssertionProposal(
                        subject_type="credential",
                        subject="worker token",
                        predicate="credential-custody",
                        value="on the host",
                        basis="stated",
                        quotes=quote("The worker runs"),
                    )
                ]
            ),
            model,
            {LABEL: self.TWICE},
        )
        assert [issue.code for issue in issues] == ["ambiguous-span"]

    def test_the_bundle_reads_the_same_function(self) -> None:
        folded = span_source(LABEL, self.TWICE)
        assert folded is not None
        assert ambiguous_quote("The worker runs", folded.indexed.haystack)
        assert not ambiguous_quote(
            "The worker runs. The worker", folded.indexed.haystack
        )


def test_the_module_names_no_element_type_the_schema_lacks() -> None:
    """Every role's element class is one of the five the System Model declares."""
    assert all(rule.element in get_args(Element) for rule in ROLES.values())
