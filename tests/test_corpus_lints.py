"""The corpus checks, run as part of the offline test job.

``evals/verify_corpus.py`` stays runnable by hand for corpus authors, and CI
runs it here so a corpus edit cannot land without it. Everything is
deterministic and credential-free, which is what lets it run on every PR.
"""

from __future__ import annotations

import json
import re

import pytest

from analysis_service.report import InputRef, SourceRef
from analysis_service.validation import parse_and_validate
from evals import verify_corpus


@pytest.mark.parametrize(
    "case_dir", verify_corpus.case_dirs(), ids=lambda path: path.name
)
def test_case_passes_every_mechanical_check(case_dir):
    assert verify_corpus.check_case(case_dir) == []


def test_severity_bands_are_derivable_everywhere():
    assert list(verify_corpus._severity_bands()) == []


def test_calibration_fixtures_pass_their_checks():
    problems = verify_corpus.check_calibration(*verify_corpus.calibration_inputs())

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
        " case; every framework a case runs must carry a reference set. If it is"
        " not being written now, add it to PENDING_REFERENCE_SETS with what the"
        " case is missing"
        for name in verify_corpus.PACKAGES
    )


def test_a_case_declaring_a_framework_its_model_does_not_satisfy_is_caught():
    """The other direction, and ASVS is the first framework that can fire it.

    STRIDE's precondition is total, so no case can over-declare it. The batch
    pipeline answers ``refuted``: every process in it states a non-web interface,
    which is the model saying what the system is rather than failing to say. A
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
        "declares 'asvs', but its precondition answers refuted" in problem
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


#: The corpus table in ``evals/README.md``: one row per case, and a single
#: proximity column. ``exemplar_proximity`` is declared per (case, framework),
#: so the column is only writable while a case's frameworks agree.
README = verify_corpus.CORPUS_DIR.parent / "README.md"
TABLE_ROW = re.compile(
    r"^\| `(?P<case>[0-9]{2}-[a-z0-9-]+)` \| [^|]+ \| \*{0,2}(?P<proximity>near|far)\*{0,2} \|",
    re.MULTILINE,
)


def _documented_proximity() -> dict[str, str]:
    return {
        match["case"]: match["proximity"]
        for match in TABLE_ROW.finditer(README.read_text(encoding="utf-8"))
    }


def test_the_readme_table_names_every_case():
    """A case absent from the table is one the table silently stops describing."""
    documented = set(_documented_proximity())
    actual = {case_dir.name for case_dir in verify_corpus.case_dirs()}

    assert documented == actual, (
        f"evals/README.md's corpus table and evals/corpus/ disagree: only in the"
        f" table {sorted(documented - actual)}, only on disk {sorted(actual - documented)}"
    )


@pytest.mark.parametrize(
    "case_dir", verify_corpus.case_dirs(), ids=lambda path: path.name
)
def test_the_readme_table_agrees_with_the_case(case_dir):
    """The one column is prose; ``case.json`` is what ``exemplar_delta`` reads.

    The failure this exists for: the table called ``02-iot-fleet-telemetry``
    far while its ``case.json`` declared it near for both frameworks, so the
    document described a one-system near side and the code computed a
    two-system one.
    """
    meta = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    declared = {
        framework["name"]: framework["exemplar_proximity"]
        for framework in meta["frameworks"]
    }
    documented = _documented_proximity()[case_dir.name]

    assert set(declared.values()) == {documented}, (
        f"evals/README.md's corpus table says {case_dir.name} is {documented!r},"
        f" and its case.json declares {declared}. A case whose frameworks"
        " disagree cannot be described by one column — give the table a column"
        " per framework."
    )


#: ``evals/README.md``'s harness table: one row per module, keyed by path.
HARNESS_ROW = re.compile(r"^\| `harness/(?P<module>[a-z_]+\.py)` \|", re.MULTILINE)
HARNESS_DIR = verify_corpus.CORPUS_DIR.parent / "harness"


def test_the_harness_table_names_every_module():
    """The table grew an entry per module, so it is a table and must be keyed.

    The failure this exists for: six modules — ``applicability``, ``artifact``,
    ``instruction``, ``instruction_delta``, ``instruments`` and ``triggers`` —
    were on disk and in no row. A reference check cannot see that, because
    every path the table *did* name resolved. An incomplete table is invisible
    to anything that only asks whether named things exist.
    """
    documented = set(HARNESS_ROW.findall(README.read_text(encoding="utf-8")))
    actual = {path.name for path in HARNESS_DIR.glob("*.py")} - {"__init__.py"}

    assert documented == actual, (
        "evals/README.md's harness table and evals/harness/ disagree: undocumented"
        f" {sorted(actual - documented)}, named but absent"
        f" {sorted(documented - actual)}. A module added to the harness needs a"
        " row saying what it owns."
    )


#: The corpus table's fourth column, beside the proximity one.
SOURCE_ROW = re.compile(
    r"^\| `(?P<case>[0-9]{2}-[a-z0-9-]+)` \| [^|]+ \| [^|]+ \| (?P<source>[^|]+?) \|",
    re.MULTILINE,
)

#: The upstream corpus this repository borrows cases from, and the licence
#: those cases carry. A case whose ``provenance`` opens with this attribution
#: is borrowed; everything else is written here.
COOKBOOK = "OWASP Threat Model Cookbook"
COOKBOOK_LICENCE = "CC-BY 4.0"


def _documented_source() -> dict[str, str]:
    return {
        match["case"]: match["source"]
        for match in SOURCE_ROW.finditer(README.read_text(encoding="utf-8"))
    }


@pytest.mark.parametrize(
    "case_dir", verify_corpus.case_dirs(), ids=lambda path: path.name
)
def test_the_readme_attributes_a_borrowed_case_to_its_source(case_dir):
    """Whether a case is borrowed is a licence fact, so the table must not drift.

    The other columns describe a case. This one attributes it. A borrowed case
    whose row reads ``synthetic`` is a missing attribution in a published
    document, not a stale label, which is why this is checked where the
    ``Domain`` column beside it is not.

    A rule rather than a mapping: the provenance decides, so a fifth borrowed
    case is covered on the day it lands.
    """
    provenance = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))[
        "provenance"
    ]
    documented = _documented_source()[case_dir.name]
    borrowed = provenance.startswith(COOKBOOK)

    if borrowed:
        assert documented == COOKBOOK, (
            f"{case_dir.name} is borrowed from the {COOKBOOK} and its README row"
            f" says {documented!r}. The row is the attribution."
        )
    else:
        assert documented.startswith("synthetic"), (
            f"{case_dir.name} was written for this corpus and its README row says"
            f" {documented!r}, which claims a source it does not have."
        )


@pytest.mark.parametrize(
    "case_dir", verify_corpus.case_dirs(), ids=lambda path: path.name
)
def test_a_borrowed_case_names_the_licence_it_carries(case_dir):
    """Attribution without a licence is half a citation."""
    provenance = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))[
        "provenance"
    ]
    if not provenance.startswith(COOKBOOK):
        pytest.skip("written for this corpus, so it carries no upstream licence")

    assert COOKBOOK_LICENCE in provenance, (
        f"{case_dir.name} cites the {COOKBOOK} and never names its licence."
        f" Record {COOKBOOK_LICENCE} in case.json's provenance."
    )


#: The corpus table's second column, beside the case it names.
DOMAIN_ROW = re.compile(
    r"^\| `(?P<case>[0-9]{2}-[a-z0-9-]+)` \| (?P<domain>[^|]+?) \| ", re.MULTILINE
)


def _same_domain(slug: str, prose: str) -> bool:
    """Whether a slug and a table cell name the same domain.

    Not string equality. ``case.json`` holds a slug because
    ``build_review_docs.py`` renders it as code at the top of a sitting
    document; the table holds prose because a reader scans the whole corpus
    there. Both are the domain, written for their own reader, so the comparison
    normalises the three ways they differ: letter case, word separators, and
    the conjunction symbols a table uses where a slug must spell the word.

    Nothing else is folded. A normaliser that did more would start hiding the
    disagreements this exists to find.
    """

    def normalise(text: str) -> list[str]:
        for symbol in ("&", "+"):
            text = text.replace(symbol, " and ")
        return text.lower().replace("-", " ").replace("/", " ").split()

    return normalise(slug) == normalise(prose)


@pytest.mark.parametrize(
    "case_dir", verify_corpus.case_dirs(), ids=lambda path: path.name
)
def test_the_readme_table_names_the_case_s_own_domain(case_dir):
    """One domain per case, however the two readers see it written.

    A reviewer meets ``domain `iot-fleet``` at the top of ``REVIEW.md`` and
    "IoT fleet" in this table. Those must be one fact. Four cases said
    otherwise — ``iot`` against "IoT fleet", ``data-pipeline`` against "batch
    data" — and the slugs were corrected to the prose rather than the reverse,
    because both places are read by people.
    """
    slug = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))["domain"]
    documented = {
        match["case"]: match["domain"]
        for match in DOMAIN_ROW.finditer(README.read_text(encoding="utf-8"))
    }[case_dir.name]

    assert _same_domain(slug, documented), (
        f"{case_dir.name} declares domain {slug!r} and its README row says"
        f" {documented!r}. The two are read by different people and must still"
        " name one domain — correct whichever is wrong, then re-run"
        " 'python evals/build_review_docs.py'."
    )
