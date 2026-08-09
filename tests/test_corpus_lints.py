"""The corpus checks, run as part of the offline test job.

``evals/verify_corpus.py`` stays runnable by hand for corpus authors, and CI
runs it here so a corpus edit cannot land without it. Everything is
deterministic and credential-free, which is what lets it run on every PR.
"""

from __future__ import annotations

import pytest

from evals import verify_corpus
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
            for threat in verify_corpus._load_json_array(case_dir / "threats.json")
        }
        for case_dir in verify_corpus.case_dirs()
    }

    problems = verify_corpus.check_calibration(set(claims_by_case), claims_by_case)

    assert problems == []


def test_each_declared_source_digests_to_what_it_claims():
    for case_dir in verify_corpus.case_dirs():
        meta = verify_corpus._load_json_object(case_dir / "case.json")
        for source in meta["sources"]:
            assert source["sha256"] == verify_corpus.source_sha256(
                case_dir / source["file"]
            )


def test_the_recorded_aggregate_is_taken_over_the_refs():
    # The same arithmetic a report's InputRef uses, so a case and a run of it
    # cannot disagree about what was submitted.
    for case_dir in verify_corpus.case_dirs():
        meta = verify_corpus._load_json_object(case_dir / "case.json")
        refs = [
            SourceRef(kind=s["kind"], label=s["label"], sha256=s["sha256"])
            for s in meta["sources"]
        ]
        assert meta["source_sha256"] == InputRef.aggregate_digest(refs)


def test_every_citation_resolves_and_its_excerpt_verifies():
    # The corpus exercises the gate rules it is graded through: the service
    # rejects a model citing a label its job never carried, and one whose
    # excerpt is not in the source it names.
    for case_dir in verify_corpus.case_dirs():
        meta = verify_corpus._load_json_object(case_dir / "case.json")
        model = verify_corpus._load_json(case_dir / "model.json")
        problems = list(
            verify_corpus._check_citations(
                model, verify_corpus.declared_sources(case_dir, meta)
            )
        )
        assert problems == [], f"{case_dir.name}: {problems}"


def test_a_case_that_cites_an_undeclared_label_is_caught():
    model = {
        "processes": [
            {"id": "process:x", "source_excerpt": "q", "source_label": "Nowhere"}
        ]
    }
    problems = list(verify_corpus._check_citations(model, {"System description": "q"}))
    assert len(problems) == 1
    assert "does not declare" in problems[0]


def test_an_excerpt_that_stitches_across_an_unmarked_cut_is_caught():
    """The defect this check found in case 03, pinned so it cannot return.

    The source reads "They are on the warehouse network and authenticate with
    SSO"; excising the middle and joining subject to predicate makes a sentence
    the source never contains. Marking the cut with ``…`` makes it verbatim
    again, which is the fix the corpus took.
    """
    source = {"Doc": "They are on the warehouse network and authenticate with SSO."}
    stitched = {
        "processes": [
            {
                "id": "process:x",
                "source_excerpt": "They authenticate with SSO",
                "source_label": "Doc",
            }
        ]
    }
    problems = list(verify_corpus._check_citations(stitched, source))
    assert len(problems) == 1
    assert "not found in the source it cites" in problems[0]

    marked = {
        "processes": [
            {
                "id": "process:x",
                "source_excerpt": "They…authenticate with SSO",
                "source_label": "Doc",
            }
        ]
    }
    assert list(verify_corpus._check_citations(marked, source)) == []


def test_a_source_declared_with_no_readable_text_skips_the_excerpt_half():
    # The missing file is _check_sources' to report, not this function's to
    # report a second time as an unverifiable quote.
    model = {
        "processes": [{"id": "process:x", "source_excerpt": "q", "source_label": "Doc"}]
    }
    assert list(verify_corpus._check_citations(model, {"Doc": ""})) == []
