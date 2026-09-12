"""What an end-to-end run lost before its lanes ran, driven over synthetic pairs and case 01.

The instrument joins two artifacts that were never joined: the analysis-mode
run's matched set and the end-to-end run's, on one corpus. The tests here
build both from the same factory ``stability`` is tested with, and put a real
extraction of case 01 — the blessed model with one thing changed — behind the
end-to-end side, so the "what did the model lack" half runs the real
extraction scorer rather than a stand-in.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from analysis_service.analysis import control_state
from analysis_service.system_model import SystemModel
from evals.harness import extraction_losses
from evals.harness.bundle import reports_dir
from evals.harness.extraction_losses import (
    FATES,
    attribute_handoff,
    extracted_model,
    pooled,
    refuse_unpaired,
)
from evals.harness.provenance import ProvenanceError
from evals.harness.reference import load_case
from evals.harness.stability import load_runs
from tests.test_evals_provenance import provenance, sampling  # noqa: F401
from tests.test_evals_stability import score, write_run

CASE_DIR = (
    Path(__file__).resolve().parents[1] / "evals" / "corpus" / "01-payments-checkout"
)
CASE = "01-payments-checkout"


@pytest.fixture(scope="module")
def case():
    return load_case(CASE_DIR)


def write_report_model(artifact: Path, case_id: str, model: SystemModel) -> None:
    """The one block the instrument reads off an end-to-end report."""
    directory = reports_dir(artifact)
    directory.mkdir(exist_ok=True)
    (directory / f"{case_id}.report.json").write_text(
        json.dumps({"system_model": model.model_dump(mode="json")}), encoding="utf-8"
    )


def pair(tmp_path, record, *, end_matched, analysis_matched, references):
    """One end-to-end run and one analysis run over case 01, scored as given."""
    end = write_run(
        tmp_path,
        "end.json",
        record,
        [score(CASE, references, end_matched)],
        mode="end-to-end",
    )
    analysis = write_run(
        tmp_path, "analysis.json", record, [score(CASE, references, analysis_matched)]
    )
    return end, analysis


def flow_reference(case):
    """A reference whose elements name a flow, so dropping that flow loses it."""
    for index, claim in enumerate(case.stride_claims()):
        flows = [e for e in claim.affected_element_ids if e.startswith("flow:")]
        if flows:
            return index, flows[0]
    raise AssertionError("case 01 has no reference citing a flow")


def without_flow(model: SystemModel, flow_id: str) -> SystemModel:
    raw = model.model_dump(mode="json")
    raw["data_flows"] = [flow for flow in raw["data_flows"] if flow["id"] != flow_id]
    return SystemModel.model_validate(raw)


def with_invented_control(model: SystemModel) -> tuple[SystemModel, str, str]:
    """The corpus's most repeated extraction failure: a control stated where the blessed model says unknown."""
    raw = model.model_dump(mode="json")
    for flow in raw["data_flows"]:
        if control_state(flow.get("encryption_in_transit")) == "unverified":
            flow["encryption_in_transit"] = "TLS 1.3 on every hop"
            return SystemModel.model_validate(raw), flow["id"], "encryption_in_transit"
    raise AssertionError("case 01 has no flow with an unverified transit control")


class TestTheFourFates:
    def test_every_reference_gets_exactly_one_fate(self, tmp_path, sampling, case):  # noqa: F811
        n = len(case.stride_claims())
        end, analysis = pair(
            tmp_path,
            provenance(sampling),
            end_matched=[0, 2],
            analysis_matched=[0, 1],
            references=n,
        )
        write_report_model(end, CASE, case.model)
        runs = load_runs([end, analysis])
        rows = attribute_handoff(
            runs[0], runs[1], {CASE: case}, {CASE: extracted_model(end, CASE)}
        )

        assert len(rows) == 1
        fates = {entry.reference: entry.fate for entry in rows[0].fates}
        assert fates["0"] == "both"
        assert fates["1"] == "extraction"
        assert fates["2"] == "recovered"
        assert fates["3"] == "downstream"
        assert sum(rows[0].by_fate.values()) == n
        assert set(rows[0].by_fate) == set(FATES)

    def test_must_find_rides_on_the_row_from_the_corpus(self, tmp_path, sampling, case):  # noqa: F811
        n = len(case.stride_claims())
        end, analysis = pair(
            tmp_path,
            provenance(sampling),
            end_matched=[],
            analysis_matched=[],
            references=n,
        )
        write_report_model(end, CASE, case.model)
        runs = load_runs([end, analysis])
        rows = attribute_handoff(runs[0], runs[1], {CASE: case}, {CASE: case.model})

        expected = {
            str(i): claim.tier == "must-find"
            for i, claim in enumerate(case.stride_claims())
        }
        assert {e.reference: e.must_find for e in rows[0].fates} == expected


class TestWhatTheModelLacked:
    def test_a_dropped_flow_is_named_on_the_reference_that_cites_it(
        self,
        tmp_path,
        sampling,  # noqa: F811
        case,
    ):
        index, flow_id = flow_reference(case)
        n = len(case.stride_claims())
        end, analysis = pair(
            tmp_path,
            provenance(sampling),
            end_matched=[],
            analysis_matched=[index],
            references=n,
        )
        write_report_model(end, CASE, without_flow(case.model, flow_id))
        runs = load_runs([end, analysis])
        rows = attribute_handoff(
            runs[0], runs[1], {CASE: case}, {CASE: extracted_model(end, CASE)}
        )
        row = {e.reference: e for e in rows[0].fates}[str(index)]

        assert row.fate == "extraction"
        assert row.missing_elements == (flow_id,)
        assert not row.held_the_place
        assert rows[0].extraction is not None
        assert flow_id in rows[0].extraction.missing
        assert pooled(rows)["extraction_lacked"]["element_missing"] == 1

    def test_an_invented_control_is_named_where_the_reference_cites_the_flow(
        self,
        tmp_path,
        sampling,  # noqa: F811
        case,
    ):
        model, flow_id, attribute = with_invented_control(case.model)
        index = next(
            i
            for i, claim in enumerate(case.stride_claims())
            if flow_id in claim.affected_element_ids
        )
        n = len(case.stride_claims())
        end, analysis = pair(
            tmp_path,
            provenance(sampling),
            end_matched=[],
            analysis_matched=[index],
            references=n,
        )
        write_report_model(end, CASE, model)
        runs = load_runs([end, analysis])
        rows = attribute_handoff(
            runs[0], runs[1], {CASE: case}, {CASE: extracted_model(end, CASE)}
        )
        row = {e.reference: e for e in rows[0].fates}[str(index)]

        assert row.fate == "extraction"
        assert row.missing_elements == ()
        assert row.differing_attributes == (
            f"{flow_id}.{attribute}: unverified -> stated",
        )
        assert pooled(rows)["extraction_lacked"]["attribute_differs"] == 1

    def test_a_loss_on_a_model_that_held_the_place_is_its_own_kind(
        self,
        tmp_path,
        sampling,  # noqa: F811
        case,
    ):
        """The sharper finding: the lanes lost it on a model with nothing missing there."""
        n = len(case.stride_claims())
        end, analysis = pair(
            tmp_path,
            provenance(sampling),
            end_matched=[],
            analysis_matched=[0],
            references=n,
        )
        write_report_model(end, CASE, case.model)
        runs = load_runs([end, analysis])
        rows = attribute_handoff(runs[0], runs[1], {CASE: case}, {CASE: case.model})
        row = {e.reference: e for e in rows[0].fates}["0"]

        assert row.fate == "extraction"
        assert row.held_the_place
        assert pooled(rows)["extraction_lacked"]["held_the_place"] == 1

    def test_a_case_with_no_model_handed_in_keeps_its_fates_and_says_so(
        self,
        tmp_path,
        sampling,  # noqa: F811
        case,
    ):
        n = len(case.stride_claims())
        end, analysis = pair(
            tmp_path,
            provenance(sampling),
            end_matched=[],
            analysis_matched=[0],
            references=n,
        )
        runs = load_runs([end, analysis])
        rows = attribute_handoff(runs[0], runs[1], {CASE: case}, {})

        assert rows[0].extraction is None
        assert {e.reference: e.fate for e in rows[0].fates}["0"] == "extraction"
        assert any("no extracted model" in note for note in rows[0].warnings)
        assert pooled(rows)["extraction_lacked"]["unread"] == 1


class TestWhatIsRefused:
    def test_two_analysis_runs_are_not_a_pair(self, tmp_path, sampling):  # noqa: F811
        record = provenance(sampling)
        a = write_run(tmp_path, "a.json", record, [score(CASE, 4, [0])])
        b = write_run(tmp_path, "b.json", record, [score(CASE, 4, [0])])
        runs = load_runs([a, b])
        with pytest.raises(
            ValueError, match="one 'end-to-end' run and one 'analysis' run"
        ):
            refuse_unpaired(runs[0], runs[1])

    def test_the_order_is_end_to_end_then_analysis(self, tmp_path, sampling):  # noqa: F811
        end, analysis = pair(
            tmp_path,
            provenance(sampling),
            end_matched=[0],
            analysis_matched=[0],
            references=4,
        )
        runs = load_runs([analysis, end])
        with pytest.raises(ValueError, match="in that order"):
            refuse_unpaired(runs[0], runs[1])

    def test_two_corpora_are_refused(self, tmp_path, sampling):  # noqa: F811
        record = provenance(sampling)
        end = write_run(
            tmp_path, "end.json", record, [score(CASE, 4, [0])], mode="end-to-end"
        )
        analysis = write_run(
            tmp_path,
            "analysis.json",
            record,
            [score(CASE, 4, [0])],
            corpus_digest="1" * 64,
        )
        runs = load_runs([end, analysis])
        with pytest.raises(ValueError, match="different corpora"):
            refuse_unpaired(runs[0], runs[1])

    def test_a_missing_report_is_refused_by_name(self, tmp_path, sampling):  # noqa: F811
        end, _ = pair(
            tmp_path,
            provenance(sampling),
            end_matched=[0],
            analysis_matched=[0],
            references=4,
        )
        with pytest.raises(ProvenanceError, match="no report for"):
            extracted_model(end, CASE)

    def test_a_reference_count_the_corpus_does_not_hold_is_refused(
        self,
        tmp_path,
        sampling,  # noqa: F811
        case,
    ):
        end, analysis = pair(
            tmp_path,
            provenance(sampling),
            end_matched=[0],
            analysis_matched=[0],
            references=4,
        )
        runs = load_runs([end, analysis])
        with pytest.raises(
            ValueError, match="not the one the runs were scored against"
        ):
            attribute_handoff(runs[0], runs[1], {CASE: case}, {CASE: case.model})

    def test_the_mode_warning_is_consumed_and_the_others_kept(self, tmp_path, sampling):  # noqa: F811
        record = provenance(sampling)
        end = write_run(
            tmp_path,
            "end.json",
            record,
            [score(CASE, 4, [0])],
            mode="end-to-end",
            models={"tiers": {"strong": "openai/other"}, "tiers_config_version": "3"},
        )
        analysis = write_run(tmp_path, "analysis.json", record, [score(CASE, 4, [0])])
        runs = load_runs([end, analysis])
        warnings = extraction_losses.warnings_for(runs[0], runs[1])

        assert not any(w.startswith("runs are of different modes") for w in warnings)
        assert any("disagree on tiers" in w for w in warnings)
