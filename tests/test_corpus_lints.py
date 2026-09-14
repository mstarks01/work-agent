"""The corpus checks, run as part of the offline test job.

``evals/verify_corpus.py`` stays runnable by hand for corpus authors, and CI
runs it here so a corpus edit cannot land without it. Everything is
deterministic and credential-free, which is what lets it run on every PR.
"""

from __future__ import annotations

import json
import re
import shutil

import pytest

from analysis_service.grounding import verify_quote
from analysis_service.report import (
    InputRef,
    SourceRef,
)
from analysis_service.system_model import ELEMENT_GROUPS
from analysis_service.validation import parse_and_validate, validate
from evals import build_review_docs, verify_corpus
from tests.factories import valid_model


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


def _must_find_lanes() -> dict[str, set[object]]:
    """Every lane carrying a ``must-find`` record, per package, over the corpus."""
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
    return must_find_lanes


def test_every_lane_of_every_carried_package_is_measured():
    """The merge bar's corpus-wide half, over the corpus this repo ships.

    A lane is one **Model Tier** call, so a lane with no ``must-find`` record
    anywhere is a run nobody ever measured — and the deployment cannot check
    that for itself, because it cannot read ``evals/``.
    """
    assert list(verify_corpus.lane_coverage_issues(_must_find_lanes())) == []


def test_every_declared_hole_names_a_lane_some_package_declares():
    """A table nobody compares to its registry fails as quietly as a branch.

    A renamed or deleted lane would leave an entry exempting nothing, and the
    bar would still read as satisfied because the real lane's own name is what
    it checks.
    """
    for name, holes in verify_corpus.UNMEASURED_LANES.items():
        assert name in verify_corpus.PACKAGES, f"{name} is not a package"
        declared = set(verify_corpus.PACKAGES[name].lanes)
        assert set(holes) <= declared, f"{name}: {set(holes) - declared}"


def test_no_declared_hole_outlives_the_lane_it_describes():
    """An entry is deleted by writing the case, so a stale one must fail.

    Without this the table is a ratchet: a lane that gains a ``must-find``
    keeps its exemption, and the next lane to lose one inherits a line that
    says the corpus cannot reach it when the corpus can.
    """
    measured = _must_find_lanes()
    for name, holes in verify_corpus.UNMEASURED_LANES.items():
        covered = set(holes) & measured.get(name, set())
        assert not covered, (
            f"{name}: {sorted(covered)} now carry a must-find record, so the"
            " UNMEASURED_LANES entry is stale — delete it"
        )


def test_every_declared_hole_gives_a_reason_that_is_not_the_lanes_name():
    """The reason answers for a lane nobody has written yet, or it is not one.

    "OAuth is not in the corpus" restates the key. What the bar needs is the
    property that makes the lane unreachable, so the next conditional chapter
    is judged by the same sentence rather than by whether somebody recognises
    its name.
    """
    for name, holes in verify_corpus.UNMEASURED_LANES.items():
        for lane, reason in holes.items():
            assert len(reason.split()) >= 12, f"{name}/{lane}: say why"
            assert reason.strip() != lane, f"{name}/{lane}: the reason is the name"


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


#: Every shape a citation can arrive in, and whether a reader must refuse it.
#: Read by the two readers below, which is the whole point: the corpus lint
#: re-asserts the gate's rule rather than calling it, so the only thing that
#: keeps them one rule is a table they both answer.
CITATION_SHAPES: tuple[tuple[str, str, str, bool], ...] = (
    ("quoted and resolvable", "a quote", "Doc", False),
    ("no excerpt at all", "", "", True),
    ("no excerpt, label given", "", "Doc", True),
    ("excerpt with no label", "a quote", "", True),
    ("label naming no source", "a quote", "Elsewhere", True),
    ("excerpt absent from its source", "never written", "Doc", True),
)


@pytest.mark.parametrize(
    "shape,excerpt,label,refused",
    CITATION_SHAPES,
    ids=[row[0] for row in CITATION_SHAPES],
)
def test_the_gate_and_the_corpus_lint_refuse_the_same_citation_shapes(
    shape, excerpt, label, refused
):
    """Two readers of one rule, held to each other rather than each to itself.

    ``verify_corpus._check_citations`` deliberately does not call
    :func:`~analysis_service.validation.validate`: a check that would weaken
    the moment somebody relaxed the shipped gate is not a check. That decision
    buys independence and costs agreement, and agreement is what this pays for.
    Both readers once passed an element carrying no excerpt at all, each with a
    test that agreed with it (#925).
    """
    sources = {"Doc": "a quote lives here"}
    model = valid_model()
    for element in model.elements():
        element.source_excerpt = "a quote"
        element.source_label = "Doc"
    model.processes[0].source_excerpt = excerpt
    model.processes[0].source_label = label

    gate = [issue for issue in validate(model, sources=sources) if issue.is_citation]
    lint = list(verify_corpus._check_citations(model.model_dump(mode="json"), sources))

    assert bool(gate) is refused, f"the service gate disagrees about {shape}"
    assert bool(lint) is refused, f"the corpus lint disagrees about {shape}"


def test_no_corpus_element_asserts_a_fact_on_its_own_authority():
    """Every element of every case cites a span of that case's own sources.

    The reference is what extraction is graded against, so an uncited element
    in it would grade a model through a rule the yardstick itself breaks.
    """
    for case_dir in verify_corpus.case_dirs():
        model = verify_corpus._load_json(case_dir / "model.json")
        assert isinstance(model, dict)
        uncited = [
            element.get("id")
            for group in ELEMENT_GROUPS
            for element in model.get(group, [])
            if not element.get("source_excerpt") or not element.get("source_label")
        ]
        assert uncited == [], f"{case_dir.name}: {uncited}"


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


@pytest.mark.parametrize(
    "case_dir", verify_corpus.case_dirs(), ids=lambda path: path.name
)
def test_the_reading_document_is_what_its_generator_writes(case_dir):
    """The committed ``REVIEW.md`` is what ``build_review_docs.py`` writes now.

    Nothing compared the two before, and both halves of the document drifted
    away from the code that writes them: the mark vocabulary moved underneath
    it, and every ``sha256`` in the pasteable ``reviews`` entry went stale
    against the bytes it signs. Neither was visible in a diff, because a
    derived file nobody re-derives looks exactly like a current one.

    It matters because the document is what a reader takes away. A person
    holding a sitting offline works from this file alone, so a stale copy is
    wrong in their hands rather than merely untidy in the tree — they mark
    with words the app refuses, and follow steps the contribution path no
    longer takes.

    **A case that records a sitting is covered too.** A sitting does not retire
    a case, and the generator no longer skips one — so this covers every case
    it writes. The skip that stood here was the one hole left in the guard: a
    derived file leaves the submission delta, so a case neither the generator
    nor this check reached would be pinned by nothing at all.
    """
    if case_dir.name in build_review_docs.HAND_WRITTEN:
        pytest.skip("hand-written; regenerating it would overwrite the record")

    document = case_dir / build_review_docs.GENERATED_DOCUMENT
    assert document.is_file(), (
        f"{case_dir.name} has no {build_review_docs.GENERATED_DOCUMENT} and no"
        " sitting that would explain the absence. Run"
        " 'python evals/build_review_docs.py'."
    )
    assert document.read_text(encoding="utf-8") == build_review_docs.build_doc(
        case_dir
    ), (
        f"{case_dir.name}/{build_review_docs.GENERATED_DOCUMENT} is not what the"
        " generator writes. Run 'python evals/build_review_docs.py' and commit"
        " the result — a sitting PR no longer has to carry it."
    )


def test_the_check_covers_every_document_the_generator_writes():
    """The guard is two halves, and they have to meet.

    A reading document is derived, so it leaves the submission delta and the
    scope check never sees it. That leaves
    ``test_the_reading_document_is_what_its_generator_writes`` as the only
    thing pinning one, and a case the generator writes but that check skips
    would be pinned by nothing at all. So the two selections are compared
    rather than kept level by hand.
    """
    covered = {
        case_dir
        for case_dir in verify_corpus.case_dirs()
        if case_dir.name not in build_review_docs.HAND_WRITTEN
    }
    assert covered == set(build_review_docs.documents())


def test_a_case_a_merged_sitting_covers_still_gets_a_document(tmp_path):
    """A sitting does not retire a case, so its document stays current.

    A second reader may sit the same case, and a change to any file a sitting
    read puts that case back on the unreviewed list. A generator that stopped
    here would let the document go stale exactly while somebody needed it.
    A merged sitting lives under ``evals/review/submissions`` and writes
    nothing into the case, so the generator has nothing to read it from and
    documents every case alike.
    """
    source = verify_corpus.case_dirs()[-1]
    corpus = tmp_path / "evals" / "corpus"
    case_dir = corpus / source.name
    shutil.copytree(source, case_dir)
    submissions = tmp_path / "evals" / "review" / "submissions"
    submissions.mkdir(parents=True)
    (submissions / "review-2026-09-01-ada-000000000000.json").write_text(
        json.dumps({"submitted_by": "ada", "cases": {source.name: {}}}),
        encoding="utf-8",
    )

    assert case_dir in build_review_docs.documents(corpus)


class TestTheReviewedAliases:
    """The reader's ruling of 2026-09-14, as the corpus carries it.

    Eleven corpus element names never appear in the source they came from. A
    reader ruled all eleven defensible: their absence as exact phrases does not
    establish an extraction error, so a name the source supports is a naming
    difference rather than a missed component (#882). These pin what that
    ruling put in the files.
    """

    def aliases(self):
        from evals.harness.reference import load_corpus

        return {
            (case.id, alias.element): alias
            for case in load_corpus(verify_corpus.CORPUS_DIR)
            for alias in case.meta.aliases
        }

    def test_every_alias_names_an_element_its_case_holds(self):
        from evals.harness.reference import load_corpus

        for case in load_corpus(verify_corpus.CORPUS_DIR):
            held = {element.id for element in case.model.elements()}
            for alias in case.meta.aliases:
                assert alias.element in held, f"{case.id}: {alias.element}"

    def test_every_alias_excerpt_is_in_its_own_case_s_sources(self):
        """The load-bearing check: an alias is supported by the case's own words
        or it is somebody's preference. `verify_corpus` refuses one that is not,
        and this says the shipped corpus satisfies it. It asks through
        `verify_quote` rather than through a substring test of its own, so the
        test cannot agree with an expectation the lint does not hold."""
        from evals.harness.reference import load_corpus

        for case in load_corpus(verify_corpus.CORPUS_DIR):
            text = "\n".join(source.text for source in case.sources)
            for alias in case.meta.aliases:
                assert verify_quote(alias.excerpt, text), f"{case.id}: {alias.name}"

    def test_the_document_store_carries_no_alias_and_says_why(self):
        """The one qualification the reader made. Naming it after the platform
        would collapse the store into `boundary:vendor-platform`, the zone that
        contains it, and the model wrote exactly that in four runs of six."""
        from evals.harness.reference import load_case

        case = load_case(verify_corpus.CORPUS_DIR / "12-overclaiming-supplier-portal")
        store = next(
            element
            for element in case.model.elements()
            if element.id == "store:document-store"
        )

        assert not [a for a in case.meta.aliases if a.element == store.id]
        assert "logical store" in store.notes
        assert "not a separately identified" in store.notes
        assert "boundary:vendor-platform" in {
            zone.id for zone in case.model.trust_boundaries
        }

    def test_an_alias_may_quote_a_sentence_the_source_wraps(self, tmp_path):
        """A source hard-wraps its lines, so a quoted sentence carries a newline.

        `model.json` quotes such a sentence today and `verify_quote` accepts it,
        because the gate folds whitespace before it looks. An alias excerpt asks
        the same question of the same bytes and must get the same answer.
        """
        source = verify_corpus.CORPUS_DIR / "01-payments-checkout"
        case_dir = tmp_path / source.name
        shutil.copytree(source, case_dir)
        meta = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
        meta["aliases"] = [
            {
                "element": "boundary:public-internet",
                "name": "internet",
                "excerpt": "It is the only thing we expose to the internet.",
                "ruling": "Supported; the source names the zone this way.",
            }
        ]
        (case_dir / "case.json").write_text(json.dumps(meta), encoding="utf-8")

        wrapped = (case_dir / "source.md").read_text(encoding="utf-8")
        assert "expose to the\ninternet." in wrapped
        assert not list(verify_corpus._check_aliases(case_dir, meta))

    def test_an_alias_keeps_its_element_s_type(self):
        """An extraction that files the catalogue spreadsheet as a store has made
        a different judgement about what the thing is. Crediting that as a
        naming difference would erase the disagreement."""
        for (case_id, element), alias in self.aliases().items():
            from analysis_service.system_model import make_element_id

            derived = make_element_id(element.split(":", 1)[0], alias.name)
            assert derived.split(":", 1)[0] == element.split(":", 1)[0], case_id
