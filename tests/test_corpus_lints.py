"""The corpus checks, run as part of the offline test job.

``evals/verify_corpus.py`` stays runnable by hand for corpus authors, and CI
runs it here so a corpus edit cannot land without it. Everything is
deterministic and credential-free, which is what lets it run on every PR.
"""

from __future__ import annotations

import pytest

from evals import verify_corpus
from stride_service.report import InputRef, SourceRef
from stride_service.validation import parse_and_validate


@pytest.mark.parametrize(
    "case_dir", verify_corpus.case_dirs(), ids=lambda path: path.name
)
def test_case_passes_every_mechanical_check(case_dir):
    assert verify_corpus.check_case(case_dir) == []


def test_severity_bands_are_derivable_everywhere():
    assert list(verify_corpus._severity_bands()) == []


def test_calibration_fixtures_pass_their_checks():
    # STRIDE's reference file, because the judge is STRIDE's: a framework that
    # matches by requirement ID reaches no claim-equivalence judgement and so
    # contributes no pair here.
    claims_by_case = {
        case_dir.name: {
            record["claim"]
            for record in verify_corpus._load_json_array(
                verify_corpus.claims_file(case_dir, "stride")
            )
        }
        for case_dir in verify_corpus.case_dirs()
    }

    problems = verify_corpus.check_calibration(set(claims_by_case), claims_by_case)

    assert problems == []


def test_every_lane_of_every_carried_package_is_measured():
    """The merge bar's corpus-wide half, over the corpus this repo ships.

    A lane is one **Model Tier** call, so a lane with no ``must-find`` record
    anywhere is a run nobody ever measured — and the deployment cannot check
    that for itself, because it cannot read ``evals/``.
    """
    must_find_lanes: dict[str, set[object]] = {}
    for case_dir in verify_corpus.case_dirs():
        for name in verify_corpus.PACKAGES:
            path = verify_corpus.claims_file(case_dir, name)
            if not path.is_file():
                continue
            must_find_lanes.setdefault(name, set()).update(
                verify_corpus.RECORD_LANE[name](record)
                for record in verify_corpus._load_json_array(path)
                if record.get("tier") == "must-find"
            )

    assert list(verify_corpus.lane_coverage_issues(must_find_lanes)) == []


def test_an_unmeasured_lane_fails_the_merge_bar():
    """The bar bites: drop one lane's must-find records and it says so.

    Per package, because the bar runs over every carried one: a corpus that
    measured every STRIDE category and no ASVS chapter would be a corpus paying
    for 17 ``strong``-tier calls it never graded.
    """
    measured = {
        name: set(package.lanes) for name, package in verify_corpus.PACKAGES.items()
    }
    measured["stride"] = {"spoofing"}
    problems = list(verify_corpus.lane_coverage_issues(measured))

    assert len(problems) == len(verify_corpus.PACKAGES["stride"].lanes) - 1
    assert all(
        "carries no must-find record anywhere" in problem for problem in problems
    )
    assert not any("spoofing" in problem for problem in problems)


def test_a_case_that_declares_no_framework_it_runs_is_caught():
    """The declaration is checked against the precondition, not trusted.

    A case that declared nothing would carry no reference set and score nothing,
    which no recall denominator can show. The first case satisfies both
    preconditions — STRIDE's because it is total, ASVS's because the case is a
    web system — so dropping the declaration is caught once per framework.
    """
    case_dir = verify_corpus.case_dirs()[0]
    model, _ = parse_and_validate(verify_corpus._load_json(case_dir / "model.json"))
    assert model is not None

    problems = list(verify_corpus.framework_issues(case_dir, {"frameworks": []}, model))

    assert sorted(problems) == sorted(
        f"case.json does not declare {name!r}, whose precondition satisfies this"
        " case; every framework a case runs must carry a reference set"
        for name in verify_corpus.PACKAGES
    )


def test_a_case_declaring_a_framework_its_model_does_not_satisfy_is_caught():
    """The other direction, and ASVS is the first framework that can fire it.

    STRIDE's precondition is total, so no case can over-declare it. A case whose
    flows never say they carry the web answers ``undecidable`` for ASVS, and a
    reference set there would grade a run whose lanes never ran.
    """
    case_dir = next(
        path for path in verify_corpus.case_dirs() if path.name.endswith("pipeline")
    )
    model, _ = parse_and_validate(verify_corpus._load_json(case_dir / "model.json"))
    assert model is not None

    meta = {"frameworks": [{"name": "asvs", "options": {"level": 1}}]}
    problems = list(verify_corpus.framework_issues(case_dir, meta, model))

    assert any(
        "declares 'asvs', but its precondition answers undecidable" in problem
        for problem in problems
    )


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
