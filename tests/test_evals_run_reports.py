"""A finished sweep keeps its reports, not only the numbers it thought to take.

A harness that built a full :class:`~analysis_service.report.Report` per
case, read four things off it and dropped it would leave a plain question — show
me one of the reports — unanswerable on a sweep the provider billed, and
nothing about a sweep is deterministic enough for a re-run to answer it
([#180](https://github.com/mstarks01/work-agent/issues/180)).

Driven offline against the scripted sweep in
:mod:`tests.test_evals_run_grounds`, which produces real reports through the
shipped graph without a provider call.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from analysis_service import graph
from analysis_service.assertions import (
    ABSENT,
    MAX_SUBJECTS,
    AssertionCatalog,
    AssertionProposal,
    AssertionRecord,
    CatalogIssue,
    CatalogProposal,
    Quarantined,
    QuoteProposal,
    resolve_catalog,
)
from analysis_service.deployment import Deployment
from analysis_service.graph import ENTRY_EXTRACT
from analysis_service.report import Report
from analysis_service.system_model import SystemModel
from analysis_service.validation import ValidationIssue, parse_and_validate
from evals.harness import modes
from evals.harness.bundle import (
    heads_from_reports,
    reports_dir,
    write_assertions,
    write_extractions,
    write_reports,
)
from evals.harness.modes import (
    AssertionResult,
    ExtractionResult,
    score_assertions,
    score_extraction,
)
from evals.harness.reference import load_case
from evals.harness.run import _run_mode
from tests.test_evals_run_grounds import CASE_DIR, DEAD, TEST_TIER_ENV, sweep


@pytest.fixture(scope="module")
def case():
    return load_case(CASE_DIR)


def test_the_report_directory_pairs_with_its_own_artifact():
    """One sweep, one name: reports cannot land beside another run's artifact."""
    assert reports_dir("/tmp/run-a.json") == Path("/tmp/run-a.reports")


def test_every_finished_case_keeps_a_report(monkeypatch, case, tmp_path):
    run = sweep(monkeypatch, case, None)
    out = tmp_path / "artifact.json"

    write_reports(str(out), "analysis", run.runs)

    written = sorted(path.name for path in reports_dir(out).iterdir())
    assert written == [
        f"{case.id}.drafts.json",
        f"{case.id}.lanes.json",
        f"{case.id}.proposals.json",
        f"{case.id}.report.json",
        "case-second.drafts.json",
        "case-second.lanes.json",
        "case-second.proposals.json",
        "case-second.report.json",
    ], "the drafts ride beside the report, because `score` reads both"


def test_the_lanes_file_keeps_everything_a_lane_read(monkeypatch, case, tmp_path):
    """Every key ``analyze.md`` templates, once per job and once per lane (#1091)."""
    run = sweep(monkeypatch, case, None)
    out = tmp_path / "artifact.json"

    write_reports(str(out), "analysis", run.runs)

    held = json.loads((reports_dir(out) / f"{case.id}.lanes.json").read_text("utf-8"))
    assert set(held["shared"]) == set(graph.LANE_SHARED_KEYS)
    lanes = held["lanes"]["stride"]
    assert set(lanes) == {lane.lane for lane in graph.FrameworkNodes("stride").lanes}
    for lane, artifacts in lanes.items():
        assert set(artifacts) == set(graph.LANE_ARTIFACTS), lane


def test_a_persisted_report_carries_what_the_artifact_cannot(
    monkeypatch, case, tmp_path
):
    """The claim text, its grounds and the model — none of which reach the
    artifact, and all of which the open questions on #180 ask for."""
    run = sweep(monkeypatch, case, None)
    out = tmp_path / "artifact.json"

    write_reports(str(out), "analysis", run.runs)

    path = reports_dir(out) / f"{case.id}.report.json"
    report = Report.model_validate_json(path.read_text("utf-8"))
    block = report.analyses[0]
    assert block.framework == "stride"
    assert all(claim.grounds for claim in block.claims)
    assert report.system_model.elements()


def test_a_case_that_died_leaves_no_report(monkeypatch, case, tmp_path):
    """No report, nothing to persist — an empty file would read as a finished
    case that found nothing."""
    run = sweep(monkeypatch, case, DEAD)
    out = tmp_path / "artifact.json"

    write_reports(str(out), "analysis", run.runs)

    written = sorted(path.name for path in reports_dir(out).iterdir())
    assert written == [
        "case-second.drafts.json",
        "case-second.lanes.json",
        "case-second.proposals.json",
        "case-second.report.json",
    ]


def test_extraction_says_it_has_no_reports(tmp_path, capsys):
    """That mode stops at the validity gate, so it writes nothing and says so."""
    out = tmp_path / "artifact.json"

    write_reports(str(out), "extraction", {})

    assert "produces none" in capsys.readouterr().out
    assert not reports_dir(out).exists()


class TestAnExtractionSweepKeepsItsModels:
    """The same question of the mode that produces no report.

    An extraction sweep's figures are computed offline from two models and the
    case's own sources, so a scorer change can be answered from a finished run
    — but only by a run that kept the model it scored. One that kept its scores
    alone has to be paid for again to answer a figure invented after it ran
    (#925).
    """

    def result(self, case):
        """One extraction, as ``run_extraction`` returns it, with no provider."""
        raw = case.model.model_dump(mode="json")
        model, issues = parse_and_validate(
            raw,
            normalize_ids=True,
            sources={source.label: source.text for source in case.sources},
        )
        return ExtractionResult(
            case_id=case.id, extracted=model, issues=tuple(issues), raw=raw
        )

    def test_the_written_model_re_scores_to_what_the_sweep_reported(
        self, case, tmp_path
    ):
        """The acceptance test for the whole file: read it back and score it.

        Not "a file exists" — a file whose contents cannot reproduce the run's
        own numbers keeps nothing worth keeping.
        """
        out = tmp_path / "artifact.json"
        result = self.result(case)
        live = score_extraction(case, result)

        write_extractions(str(out), "extraction", {case.id: result})

        written = json.loads(
            (reports_dir(out) / f"{case.id}.extraction.json").read_text("utf-8")
        )
        offline = score_extraction(
            case,
            ExtractionResult(
                case_id=case.id,
                extracted=SystemModel.model_validate(written["normalized"]),
                issues=tuple(
                    ValidationIssue.model_validate(issue) for issue in written["issues"]
                ),
            ),
        )

        assert offline.to_json() == live.to_json()

    def test_the_raw_output_is_kept_beside_the_normalized_one(self, case, tmp_path):
        """Normalizing makes a slug decision, and a re-score may want to remake it.

        It is the one part of a run that cannot be recomputed from what else is
        written: the normalized model has already had its IDs derived.
        """
        out = tmp_path / "artifact.json"
        result = self.result(case)
        write_extractions(str(out), "extraction", {case.id: result})

        written = json.loads(
            (reports_dir(out) / f"{case.id}.extraction.json").read_text("utf-8")
        )

        assert written["raw"] == case.model.model_dump(mode="json")

    def test_a_model_the_gate_refused_is_kept_with_its_issues(self, case, tmp_path):
        """A refused extraction is the one most worth reading back."""
        out = tmp_path / "artifact.json"
        raw = case.model.model_dump(mode="json")
        for element in raw["processes"]:
            element["source_excerpt"] = ""
            element["source_label"] = ""
        model, issues = parse_and_validate(
            raw,
            normalize_ids=True,
            sources={source.label: source.text for source in case.sources},
        )
        result = ExtractionResult(
            case_id=case.id, extracted=model, issues=tuple(issues), raw=raw
        )

        write_extractions(str(out), "extraction", {case.id: result})

        written = json.loads(
            (reports_dir(out) / f"{case.id}.extraction.json").read_text("utf-8")
        )
        assert [issue["code"] for issue in written["issues"]] == [
            "missing-citation"
        ] * (len(raw["processes"]))

    def test_a_mode_that_ran_no_extraction_writes_none_and_says_so(
        self, case, tmp_path, capsys
    ):
        """An analysis sweep seeds the blessed model and has no emission to keep."""
        out = tmp_path / "artifact.json"

        write_extractions(str(out), "analysis", {})

        assert not reports_dir(out).exists()
        assert "no extractions written" in capsys.readouterr().out

    def test_an_end_to_end_sweep_keeps_the_first_pass_and_the_repair(
        self, case, tmp_path
    ):
        """#961 finding 14: the report's model is the one after the overlay."""
        out = tmp_path / "artifact.json"
        broken = case.model.model_dump(mode="json")
        broken["data_flows"][0]["destination"] = "process:does-not-exist"
        repaired = case.model.model_dump(mode="json")
        result = ExtractionResult(
            case_id=case.id, extracted=None, issues=(), raw=broken, repair=repaired
        )

        write_extractions(str(out), "end-to-end", {case.id: result})

        written = json.loads(
            (reports_dir(out) / f"{case.id}.extraction.json").read_text("utf-8")
        )
        assert written["raw"] == broken
        assert written["repair"] == repaired

    def test_a_refused_case_is_written_beside_the_finished_ones(
        self, monkeypatch, case, tmp_path
    ):
        """The sweep keeps a first pass whichever way its case ended."""
        from tests.test_evals_modes import build

        models = {}
        pipeline = build(case, ENTRY_EXTRACT, models)
        broken = case.model.model_dump(mode="json")
        broken["data_flows"][0]["destination"] = "process:does-not-exist"
        models["extract"].reply = json.dumps(broken)
        models["repair"].reply = json.dumps(broken)
        monkeypatch.setattr(modes, "build_eval_pipeline", lambda *a, **k: pipeline)
        deployment = Deployment.from_env(env=TEST_TIER_ENV)

        run = asyncio.run(_run_mode([case], "end-to-end", deployment))

        assert run.runs == {}
        assert any("rejected the model" in failure for failure in run.failures)
        assert run.extracted[case.id].raw == broken
        assert run.extracted[case.id].repair == broken


class TestTheAssertionsBesideTheArtifact:
    """The assertion mode's sidecar, held to the same acceptance test.

    Not "a file exists" — a file whose contents cannot reproduce the run's own
    numbers keeps nothing worth keeping.
    """

    def result(self, case):
        flow = next(flow for flow in case.model.data_flows if flow.source_excerpt)
        sources = {source.label: source.text for source in case.sources}
        proposal = CatalogProposal(
            assertions=[
                AssertionProposal(
                    subject_type="interaction",
                    subject=flow.id,
                    predicate="mfa-requirement",
                    value=ABSENT,
                    basis="stated",
                    quotes=[
                        QuoteProposal(
                            source_label=flow.source_label, quote=flow.source_excerpt
                        )
                    ],
                ),
                AssertionProposal(
                    subject_type="interaction",
                    subject="flow:nowhere:at-all",
                    predicate="mfa-requirement",
                    value="required",
                    basis="stated",
                    quotes=[],
                ),
            ]
        )
        catalog, issues = resolve_catalog(proposal, case.model, sources)
        return AssertionResult(
            case_id=case.id,
            proposal=proposal.model_dump(mode="json"),
            record=AssertionRecord(
                proposed=len(proposal.assertions), catalog=catalog, issues=issues
            ),
        )

    def written(self, case, tmp_path):
        out = tmp_path / "artifact.json"
        write_assertions(str(out), "assertions", {case.id: self.result(case)})
        return json.loads(
            (reports_dir(out) / f"{case.id}.assertions.json").read_text("utf-8")
        )

    def test_the_written_rows_re_score_to_what_the_sweep_reported(self, case, tmp_path):
        live = score_assertions(case, self.result(case))
        written = self.written(case, tmp_path)

        offline = score_assertions(
            case,
            AssertionResult(
                case_id=case.id,
                proposal=written["proposal"],
                record=AssertionRecord(
                    proposed=written["proposed"],
                    catalog=AssertionCatalog.model_validate(written["catalog"]),
                    issues=[
                        CatalogIssue.model_validate(issue)
                        for issue in written["issues"]
                    ],
                    quarantined=[
                        Quarantined.model_validate(row)
                        for row in written["quarantined"]
                    ],
                ),
            ),
        )

        assert offline.to_json() == live.to_json()

    def test_the_proposal_is_kept_beside_the_rows_code_built(self, case, tmp_path):
        """Resolving drops rows and locates spans, so what arrived cannot be recomputed."""
        written = self.written(case, tmp_path)

        assert len(written["proposal"]["assertions"]) == 2
        assert len(written["catalog"]["entries"]) == 1
        assert [issue["code"] for issue in written["issues"]] == ["dangling-subject"]

    def test_the_projection_is_kept_with_the_rows_behind_it(self, case, tmp_path):
        """A degraded value is only explainable with the rows that would not fit.

        This case's one row is an MFA absence, which projects into no graph
        field at all — so the projection is empty, and that emptiness is the
        record.
        """
        written = self.written(case, tmp_path)

        assert written["projection"] == []

    def test_no_file_is_written_in_another_mode(self, case, tmp_path):
        out = tmp_path / "artifact.json"
        write_assertions(str(out), "extraction", {case.id: self.result(case)})

        assert not reports_dir(out).exists()


def _refused_catalog(case) -> AssertionResult:
    """A run the gate refused whole: more subjects than it reads, and no row
    number on the refusal, because it is about the catalog."""
    proposal = CatalogProposal(
        assertions=[
            AssertionProposal(
                subject_type="principal",
                subject=f"team {index}",
                predicate="mfa-requirement",
                value="unknown",
                reason="silent",
                basis="stated",
            )
            for index in range(MAX_SUBJECTS + 1)
        ]
    )
    sources = {source.label: source.text for source in case.sources}
    return AssertionResult(
        case_id=case.id,
        proposal=proposal.model_dump(mode="json"),
        record=AssertionRecord.of(proposal, case.model, sources),
    )


def test_a_refused_catalog_counts_every_row_it_lost(case):
    """The score counts what the report counts, not only the resolver's rows."""
    result = _refused_catalog(case)

    score = score_assertions(case, result)

    assert score.kept == 0
    assert score.rejected == result.record.refused_rows() == MAX_SUBJECTS + 1


def test_a_refused_catalog_keeps_its_count_through_the_archive(case, tmp_path):
    """A replay of the archived run counts the loss the live run counted."""
    result = _refused_catalog(case)
    out = tmp_path / "artifact.json"
    write_assertions(str(out), "assertions", {case.id: result})

    (read,) = heads_from_reports(out, [case]).values()

    assert score_assertions(case, read).rejected == MAX_SUBJECTS + 1
