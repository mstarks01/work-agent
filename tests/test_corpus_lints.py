"""The corpus checks, run as part of the offline test job.

``evals/verify_corpus.py`` stays runnable by hand for corpus authors, and CI
runs it here so a corpus edit cannot land without it. Everything is
deterministic and credential-free, which is what lets it run on every PR.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

EVALS_ROOT = Path(__file__).resolve().parents[1] / "evals"
sys.path.insert(0, str(EVALS_ROOT))

import verify_corpus

from stride_service.report import InputRef, SourceRef


@pytest.mark.parametrize(
    "case_dir", verify_corpus.case_dirs(), ids=lambda path: path.name
)
def test_case_passes_every_mechanical_check(case_dir):
    assert verify_corpus.check_case(case_dir) == []


def test_severity_bands_are_derivable_everywhere():
    assert list(verify_corpus._severity_bands()) == []


def test_calibration_fixtures_pass_their_checks():
    claims_by_case = {
        case_dir.name: {
            threat["claim"]
            for threat in verify_corpus._load_json(case_dir / "threats.json")
        }
        for case_dir in verify_corpus.case_dirs()
    }

    problems = verify_corpus.check_calibration(set(claims_by_case), claims_by_case)

    assert problems == []


def test_each_declared_source_digests_to_what_it_claims():
    for case_dir in verify_corpus.case_dirs():
        meta = verify_corpus._load_json(case_dir / "case.json")
        for source in meta["sources"]:
            assert source["sha256"] == verify_corpus.source_sha256(
                case_dir / source["file"]
            )


def test_the_recorded_aggregate_is_taken_over_the_refs():
    # The same arithmetic a report's InputRef uses, so a case and a run of it
    # cannot disagree about what was submitted.
    for case_dir in verify_corpus.case_dirs():
        meta = verify_corpus._load_json(case_dir / "case.json")
        refs = [
            SourceRef(kind=s["kind"], label=s["label"], sha256=s["sha256"])
            for s in meta["sources"]
        ]
        assert meta["source_sha256"] == InputRef.aggregate_digest(refs)


def test_every_citation_names_a_source_the_case_declares():
    # The corpus exercises the gate rule it is graded through: the service
    # rejects a model citing a label its job never carried.
    for case_dir in verify_corpus.case_dirs():
        meta = verify_corpus._load_json(case_dir / "case.json")
        model = verify_corpus._load_json(case_dir / "model.json")
        problems = list(
            verify_corpus._check_citations(model, verify_corpus.declared_labels(meta))
        )
        assert problems == [], f"{case_dir.name}: {problems}"


def test_a_case_that_cites_an_undeclared_label_is_caught():
    model = {
        "processes": [
            {"id": "process:x", "source_excerpt": "q", "source_label": "Nowhere"}
        ]
    }
    problems = list(verify_corpus._check_citations(model, {"System description"}))
    assert len(problems) == 1
    assert "does not declare" in problems[0]
