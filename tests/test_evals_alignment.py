"""One alignment, read by every extraction figure and by the loss instrument (#961).

The probes here are the audit's own: an approved alias that read as a dropped
initiator, an invented interaction that kept full interaction recall, and one
missing store that licensed twenty rename candidates. Each is the harness that
proved the defect, kept as the regression test.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest

from analysis_service.system_model import (
    SystemModel,
    flow_label,
    make_flow_id,
    normalize_element_ids,
)
from evals.harness import alignment, modes
from evals.harness.alignment import (
    EVIDENCE,
    FLOW_DISCRIMINATORS,
    Alignment,
    Pair,
    align,
    protocol_state,
)
from evals.harness.extraction_losses import attribute_handoff
from evals.harness.reference import ElementAlias, load_case, load_corpus
from evals.harness.stability import load_runs
from tests.test_evals_extraction_losses import write_report_model
from tests.test_evals_provenance import provenance, sampling  # noqa: F401
from tests.test_evals_stability import score as score_block
from tests.test_evals_stability import write_run

CORPUS = Path(__file__).resolve().parents[1] / "evals" / "corpus"


def case(prefix: str):
    return load_case(next(CORPUS.glob(prefix + "-*")))


def scored(golden, model: SystemModel) -> modes.ExtractionScore:
    return modes.score_extraction(golden, modes.ExtractionResult(golden.id, model, ()))


def relabelled(model: SystemModel, flow_id: str, label: str) -> SystemModel:
    """The model with one flow under another label, its ID re-derived."""
    raw = model.model_dump(mode="json")
    flow = next(f for f in raw["data_flows"] if f["id"] == flow_id)
    flow["name"] = label
    flow["id"] = make_flow_id(flow["source"], flow["destination"], label)
    return SystemModel.model_validate(raw)


def renamed(model: SystemModel, element_id: str, name: str) -> SystemModel:
    """The model with one node renamed, IDs and references re-derived."""
    copy = model.model_copy(deep=True)
    next(element for element in copy.elements() if element.id == element_id).name = name
    return normalize_element_ids(copy)


class TestAnApprovedAliasReachesEveryFigure:
    """Case 09's catalogue spreadsheet under the name the reader ruled supported.

    Before the alignment, ``sourced_recall`` saw the ruling and nothing else
    did: initiator recall read 0.0, interaction recall 0.875 and comparison
    coverage 0.884 over a model that kept every node and interaction.
    """

    @pytest.fixture(scope="class")
    def probe(self):
        golden = case("09")
        model = renamed(golden.model, "process:catalogue-spreadsheet", "Spreadsheet")
        return golden, scored(golden, model)

    def test_the_alias_is_one_pair_on_the_readers_words(self, probe):
        _, score = probe
        (pair,) = score.alignment.by_evidence("alias")

        assert pair.reference == "process:catalogue-spreadsheet"
        assert pair.produced == "process:spreadsheet"
        assert pair.excerpt

    def test_the_initiator_is_kept(self, probe):
        _, score = probe

        assert score.initiator_recall == 1.0
        assert score.initiators_missing == frozenset()

    def test_every_incident_interaction_is_kept(self, probe):
        _, score = probe

        assert score.aligned_interaction_recall == 1.0
        assert score.aligned_interaction_precision == 1.0
        assert score.interaction_recall < 1.0, "the strict fold stays as it was"

    def test_the_attributes_of_the_renamed_element_are_compared(self, probe):
        _, score = probe

        assert score.comparison_coverage == 1.0
        assert score.aligned_recall == score.aligned_precision == 1.0
        assert score.actor_recall == 1.0

    def test_the_ruling_reaches_sourced_recall_through_the_same_pair(self, probe):
        """``sourced_recall`` and the alignment are one reader of the ruling."""
        _, score = probe

        assert [credit.reference for credit in score.aliased] == [
            "process:catalogue-spreadsheet"
        ]
        assert score.sourced_recall > score.endpoint_recall

    def test_deleting_the_actor_still_lowers_both(self):
        """The acceptance's other half: a real omission is not forgiven."""
        golden = case("09")
        model = golden.model.model_copy(deep=True)
        model.processes = [
            p for p in model.processes if p.id != "process:catalogue-spreadsheet"
        ]
        model.data_flows = [
            f
            for f in model.data_flows
            if "process:catalogue-spreadsheet" not in (f.source, f.destination)
        ]
        score = scored(golden, model)

        assert score.initiator_recall < 1.0
        assert score.aligned_interaction_recall < 1.0
        assert "process:catalogue-spreadsheet" in score.alignment.unaligned_reference


class TestAnUnruledRenameStaysUnaligned:
    """Case 04's two actors under plausible names nobody ruled on.

    The audit reproduced initiator recall 0.0 here with every element and flow
    retained. That number is right: no reader has ruled, and nothing infers a
    rename. What changed is that the score now says so — the two are
    unaligned, and the extras are unreviewed rather than additions.

    The names the audit probed with, the source's own "Other teams' backends"
    and "ML engineers", were ruled aliases on 2026-09-16 (#961 step 3), so
    they now align and the second test says so. The unruled pair here is two
    names the source never uses.
    """

    def test_nothing_is_inferred(self):
        golden = case("04")
        model = renamed(golden.model, "entity:calling-service", "Consumer backends")
        model = renamed(model, "entity:ml-engineer", "Model publishers")
        score = scored(golden, model)

        assert score.aliased == ()
        assert score.actor_recall == 0.0
        assert score.initiator_recall == 0.0
        assert set(score.extra_status.values()) == {"unreviewed"}

    def test_a_ruled_alias_aligns_and_a_reader_decided_it(self):
        golden = case("04")
        model = renamed(golden.model, "entity:calling-service", "Other teams backends")
        model = renamed(model, "entity:ml-engineer", "ML engineers")
        score = scored(golden, model)

        assert {pair.evidence for pair in score.aliased} == {"alias"}
        assert score.actor_recall == 1.0
        assert score.initiator_recall == 1.0
        assert set(score.extra_status.values()) == {"equivalent"}


class TestAnAliasKeepsItsType:
    """Same name, different scope: an alias on a zone never claims an entity."""

    def test_an_alias_never_crosses_its_elements_type(self):
        """Case 01 rules ``internet`` for ``boundary:public-internet``. A model
        writing an *entity* called internet gets no pair from that ruling."""
        golden = case("01")
        raw = golden.model.model_dump()
        raw["external_entities"].append(
            dict(raw["external_entities"][0], id="entity:internet", name="internet")
        )
        model = SystemModel.model_validate(raw)
        score = scored(golden, model)

        assert score.alignment.by_evidence("alias") == ()
        assert "entity:internet" in score.alignment.unaligned_produced
        assert score.extra_status["entity:internet"] == "unreviewed"


class TestInteractionsAlignOneToOne:
    """Between one pair of endpoints, a label or the discriminators decide."""

    @pytest.fixture(scope="class")
    def parallel(self):
        """Case 01 with a second flow between its first flow's endpoints."""
        golden = case("01")
        model = golden.model.model_copy(deep=True)
        second = model.data_flows[0].model_copy(deep=True)
        second.name = "WebSocket updates"
        second.protocol = "WebSocket"
        model.data_flows.append(second)
        return replace(golden, model=normalize_element_ids(model))

    def test_an_invented_replacement_is_a_miss_and_an_extra(self, parallel):
        """The audit's probe: the parallel flow's meaning and protocol replaced.

        The endpoint fold kept interaction recall at 1.0 because the pair
        still carried two flows. The alignment pairs the untouched flow by
        label and leaves the fabricated one, whose operation and protocol
        state agree with nothing, unpaired on both sides. Rule 6 does not
        reach it: it pairs the sole flow on each side, and this endpoint pair
        carries two.
        """
        model = parallel.model.model_copy(deep=True)
        pairs = Counter((f.source, f.destination) for f in model.data_flows)
        endpoints = next(p for p, n in pairs.items() if n > 1)
        flow = next(
            f for f in model.data_flows if (f.source, f.destination) == endpoints
        )
        flow.name = "Fabricated interaction"
        flow.protocol = "invented"
        flow.operations = "unknown"
        model = normalize_element_ids(model)
        score = scored(parallel, model)

        assert score.interaction_recall == 1.0, "the strict fold cannot see it"
        assert score.aligned_interaction_recall < 1.0
        assert score.aligned_interaction_precision < 1.0
        assert score.alignment.ambiguous == ()

    def test_a_relabelled_flow_the_discriminators_single_out_is_paired(self, parallel):
        """Relabel the WebSocket flow alone: still stated, still the same operation."""
        model = parallel.model.model_copy(deep=True)
        flow = next(f for f in model.data_flows if "websocket" in f.id)
        flow.name = "live updates"
        model = normalize_element_ids(model)
        score = scored(parallel, model)

        (pair,) = score.alignment.by_evidence("discriminated")
        assert flow_label(pair.reference) == "websocket-updates"
        assert flow_label(pair.produced) == "live-updates"
        assert score.aligned_interaction_recall == 1.0

    def test_two_flows_the_rules_cannot_tell_apart_are_listed_not_guessed(
        self, parallel
    ):
        """Relabel both flows and give them one signature: an explicit ambiguity."""
        model = parallel.model.model_copy(deep=True)
        pairs = Counter((f.source, f.destination) for f in model.data_flows)
        endpoints = next(p for p, n in pairs.items() if n > 1)
        for index, flow in enumerate(model.data_flows):
            if (flow.source, flow.destination) == endpoints:
                flow.name = f"interaction {index}"
                flow.protocol = "unknown"
        reference = parallel.model.model_copy(deep=True)
        for flow in reference.data_flows:
            if (flow.source, flow.destination) == endpoints:
                flow.protocol = "unknown"
        golden = replace(parallel, model=reference)
        score = scored(golden, normalize_element_ids(model))

        (ambiguity,) = score.alignment.ambiguous
        assert len(ambiguity.reference) == len(ambiguity.produced) == 2
        assert set(ambiguity.reference) <= set(score.alignment.unaligned_reference)
        assert set(ambiguity.produced) <= set(score.alignment.unaligned_produced)

    def test_a_flow_between_aliased_endpoints_is_paired_by_its_label(self):
        """Rule 3: the endpoint resolved through rule 2, and the label is its own."""
        golden = case("03")
        model = renamed(golden.model, "process:ingest-scheduler", "Airflow scheduler")
        score = scored(golden, model)

        labelled = score.alignment.by_evidence("label")
        assert labelled
        assert all("airflow-scheduler" in pair.produced for pair in labelled)
        assert all("ingest-scheduler" in pair.reference for pair in labelled)


class TestAFlowAliasIsReadBetweenItsEndpoints:
    """A ruled label for one flow pairs the produced flow that carries it, and no other."""

    def test_a_ruled_label_pairs_the_flow_as_an_alias(self):
        golden = case("04")
        flow_id = "flow:process:model-server>store:model-registry-bucket>load-artifact"
        alias = ElementAlias(
            element=flow_id,
            name="load artifacts",
            excerpt="loads model artifacts from a model registry bucket",
            ruling="plural of the same label",
        )
        ruled = replace(
            golden, meta=golden.meta.model_copy(update={"aliases": [alias]})
        )
        # Under another label and with a discriminator that differs, so
        # neither the label rule nor rule 4 pairs it: the alias is what does.
        model = relabelled(golden.model, flow_id, "load artifacts")
        raw = model.model_dump(mode="json")
        next(f for f in raw["data_flows"] if flow_label(f["id"]) == "load-artifacts")[
            "protocol"
        ] = "unknown"
        model = SystemModel.model_validate(raw)

        before = align(golden, model)
        after = align(ruled, model)

        assert [p.evidence for p in before.pairs if p.reference == flow_id] == ["sole"]
        (pair,) = after.by_evidence("alias")
        assert pair.reference == flow_id
        assert flow_label(pair.produced) == "load-artifacts"
        assert pair.excerpt == alias.excerpt

    def test_a_ruled_label_never_reaches_across_endpoints(self):
        golden = case("04")
        flow_id = "flow:process:model-server>store:model-registry-bucket>load-artifact"
        alias = ElementAlias(
            element=flow_id,
            name="load artifacts",
            excerpt="loads model artifacts from a model registry bucket",
        )
        ruled = replace(
            golden, meta=golden.meta.model_copy(update={"aliases": [alias]})
        )
        model = relabelled(golden.model, flow_id, "load artifacts")
        raw = model.model_dump(mode="json")
        moved = next(
            f for f in raw["data_flows"] if flow_label(f["id"]) == "load-artifacts"
        )
        moved["source"] = "process:inference-gateway"
        moved["id"] = (
            "flow:process:inference-gateway>store:model-registry-bucket>load-artifacts"
        )

        after = align(ruled, SystemModel.model_validate(raw))

        assert after.by_evidence("alias") == ()
        assert flow_id in after.unaligned_reference


class TestAZoneIsPairedByItsMembers:
    """Rule 3: a zone's name is coined; where its paired members sit is the fact."""

    def test_the_zone_holding_most_paired_members_pairs_as_membership(self):
        golden = case("04")
        model = renamed(golden.model, "boundary:serving-edge", "GKE")

        aligned = align(golden, model)

        (pair,) = aligned.by_evidence("membership")
        assert pair.reference == "boundary:serving-edge"
        assert pair.produced == "boundary:gke"

    def test_a_tie_is_listed_and_pairs_nothing(self):
        golden = case("04")
        raw = renamed(golden.model, "boundary:serving-edge", "GKE").model_dump(
            mode="json"
        )
        # The zone's two paired members split across two produced zones.
        raw["trust_boundaries"].append(
            {**raw["trust_boundaries"][0], "id": "boundary:edge", "name": "edge"}
        )
        next(s for s in raw["data_stores"] if s["id"] == "store:inference-log")[
            "trust_zone"
        ] = "boundary:edge"

        aligned = align(golden, SystemModel.model_validate(raw))

        assert aligned.by_evidence("membership") == ()
        assert any(a.reference == ("boundary:serving-edge",) for a in aligned.ambiguous)

    def test_a_produced_zone_two_reference_zones_claim_pairs_neither(self):
        golden = case("04")
        raw = golden.model.model_dump(mode="json")
        # Every zoned element into one produced zone named otherwise.
        raw["trust_boundaries"] = [
            {
                **raw["trust_boundaries"][0],
                "id": "boundary:everything",
                "name": "everything",
            }
        ]
        for key in ("processes", "external_entities", "data_stores"):
            for element in raw[key]:
                element["trust_zone"] = "boundary:everything"

        aligned = align(golden, SystemModel.model_validate(raw))

        assert aligned.by_evidence("membership") == ()
        assert any(a.produced == ("boundary:everything",) for a in aligned.ambiguous)


class TestTheSoleFlowBetweenFoundEndpoints:
    """Rule 6: one flow each side between two found elements is one interaction."""

    def test_a_lone_flow_under_another_label_with_other_facts_pairs_as_sole(self):
        golden = case("04")
        flow_id = "flow:process:model-server>store:model-registry-bucket>load-artifact"
        model = relabelled(golden.model, flow_id, "fetch model")
        raw = model.model_dump(mode="json")
        next(f for f in raw["data_flows"] if flow_label(f["id"]) == "fetch-model")[
            "protocol"
        ] = "unknown"

        aligned = align(golden, SystemModel.model_validate(raw))

        (pair,) = aligned.by_evidence("sole")
        assert pair.reference == flow_id
        assert flow_label(pair.produced) == "fetch-model"

    def test_two_produced_flows_beside_one_reference_flow_stay_unpaired(self):
        golden = case("04")
        flow_id = "flow:process:model-server>store:model-registry-bucket>load-artifact"
        model = relabelled(golden.model, flow_id, "fetch model")
        raw = model.model_dump(mode="json")
        one = next(f for f in raw["data_flows"] if flow_label(f["id"]) == "fetch-model")
        one["protocol"] = "unknown"
        raw["data_flows"].append(
            {
                **one,
                "id": one["id"].replace(":fetch-model", ":verify-model"),
                "name": "verify model",
                "operations": "read",
            }
        )

        aligned = align(golden, SystemModel.model_validate(raw))

        assert aligned.by_evidence("sole") == ()
        assert flow_id in aligned.unaligned_reference


class TestTheAlignmentIsTheOneReader:
    def test_a_missing_model_aligns_nothing_and_lists_every_reference(self):
        golden = case("01")
        aligned = align(golden, None)

        assert aligned.pairs == ()
        assert set(aligned.unaligned_reference) == {
            element.id for element in golden.model.elements()
        }

    def test_the_blessed_model_aligns_exactly_everywhere(self):
        for golden in load_corpus(CORPUS):
            aligned = align(golden, golden.model)
            assert {pair.evidence for pair in aligned.pairs} == {"exact"}, golden.id
            assert aligned.unaligned_reference == (), golden.id
            assert aligned.unaligned_produced == (), golden.id
            assert aligned.ambiguous == (), golden.id

    def test_every_pair_is_one_to_one(self):
        golden = case("09")
        model = renamed(golden.model, "process:catalogue-spreadsheet", "Spreadsheet")
        aligned = align(golden, model)

        references = [pair.reference for pair in aligned.pairs]
        produced = [pair.produced for pair in aligned.pairs]
        assert len(references) == len(set(references))
        assert len(produced) == len(set(produced))

    def test_the_scorer_and_the_discriminators_reduce_a_protocol_alike(self):
        """One reader of "is this protocol stated", held across the two tables."""
        assert modes._SCORED_ATTRIBUTES["protocol"].reduce is protocol_state
        assert FLOW_DISCRIMINATORS["protocol"] is protocol_state

    def test_every_discriminator_is_a_scored_attribute(self):
        """A fact that tells two flows apart is a fact the scorer compares."""
        assert set(FLOW_DISCRIMINATORS) <= set(modes._SCORED_ATTRIBUTES)

    def test_the_evidence_literal_and_its_tuple_agree(self):
        from typing import get_args

        assert set(EVIDENCE) == set(get_args(alignment.Evidence))

    def test_the_artifact_carries_the_pairs_and_the_leftovers(self):
        golden = case("09")
        model = renamed(golden.model, "process:catalogue-spreadsheet", "Spreadsheet")
        payload = scored(golden, model).to_json()["alignment"]

        assert {pair["evidence"] for pair in payload["pairs"]} == {
            "exact",
            "alias",
            "label",
        }
        assert payload["unaligned_reference"] == []
        assert payload["ambiguous"] == []

    def test_a_score_built_without_an_alignment_reads_as_unpaired(self):
        """The default is the empty alignment, which pairs nothing."""
        score = modes.ExtractionScore("x", ("process:a",), (), (), True, ())

        assert score.aligned_recall == 0.0
        assert score.alignment == Alignment.empty()
        assert Pair("a", "b", "exact").to_json()["excerpt"] == ""


class TestTheLossInstrumentReadsTheAlignment:
    """An end-to-end loss on a reference whose element the model renamed under a ruling."""

    CASE = "03-batch-data-pipeline"

    def test_an_aliased_element_is_carried_not_missing(
        self,
        tmp_path,
        sampling,  # noqa: F811
    ):
        golden = load_case(CORPUS / self.CASE)
        index = next(
            i
            for i, claim in enumerate(golden.stride_claims())
            if "process:ingest-scheduler" in claim.affected_element_ids
        )
        n = len(golden.stride_claims())
        end = write_run(
            tmp_path,
            "end.json",
            provenance(sampling),
            [score_block(self.CASE, n, [])],
            mode="end-to-end",
        )
        analysis = write_run(
            tmp_path,
            "analysis.json",
            provenance(sampling),
            [score_block(self.CASE, n, [index])],
        )
        model = renamed(golden.model, "process:ingest-scheduler", "Airflow scheduler")
        write_report_model(end, self.CASE, model)
        runs = load_runs([end, analysis])
        rows = attribute_handoff(
            runs[0], runs[1], {self.CASE: golden}, {self.CASE: model}
        )
        row = {e.reference: e for e in rows[0].fates}[str(index)]

        assert row.fate == "extraction"
        assert row.missing_elements == ()
        assert row.held_the_place
        assert rows[0].to_json()["missing_elements"] == []


class TestTwoRulingsForOneElement:
    """A blessed element with two aliases, both produced: listed, never paired twice."""

    def test_both_names_produced_is_an_ambiguity_on_the_reference(self):
        from dataclasses import replace as replace_case

        from evals.harness.reference import ElementAlias

        golden = case("03")
        second = ElementAlias(
            element="process:ingest-scheduler",
            name="scheduler job",
            excerpt="An Airflow scheduler running in the landing network",
        )
        meta = golden.meta.model_copy(
            update={"aliases": [*golden.meta.aliases, second]}
        )
        golden = replace_case(golden, meta=meta)
        raw = golden.model.model_dump()
        for process in raw["processes"]:
            if process["id"] == "process:ingest-scheduler":
                process["id"] = "process:airflow-scheduler"
        raw["processes"].append(
            dict(raw["processes"][0], id="process:scheduler-job", name="scheduler job")
        )
        for flow in raw["data_flows"]:
            for end in ("source", "destination"):
                if flow[end] == "process:ingest-scheduler":
                    flow[end] = "process:airflow-scheduler"
        aligned = align(golden, SystemModel.model_validate(raw))

        assert aligned.by_evidence("alias") == ()
        assert "process:ingest-scheduler" in aligned.unaligned_reference
        (ambiguity,) = aligned.ambiguous
        assert ambiguity.reference == ("process:ingest-scheduler",)
        assert ambiguity.produced == (
            "process:airflow-scheduler",
            "process:scheduler-job",
        )
