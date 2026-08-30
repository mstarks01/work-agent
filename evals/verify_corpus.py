"""Mechanical checks over the golden corpus and the calibration-label fixtures.

Everything here is deterministic and credential-free by construction;
``tests/test_corpus_lints.py`` runs the same checks in CI.

**This module is also the merge bar.** A deployment cannot read ``evals/`` —
``pyproject.toml`` packages ``src/analysis_service`` alone — so no load-time gate
can check that a framework was ever measured, and a package that *asserted* it
had been would be the shape Promotion already rejects. The floor sits here
instead, and it draws the same line the package gate does: the gate checks what
the code reads, CI checks what the budget allows. Three checks
(:func:`framework_issues`, :func:`lane_coverage_issues`) say a framework is
**gradeable**; none of them says it grades *well*, and no number stands behind
either claim until a live sweep runs.

Run ``python evals/verify_corpus.py`` to check, ``--write-sha`` to stamp each
case's ``source_sha256`` from its ``source.md``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any, get_args

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))
# The repository root too, so this runs both as a script and as an import from
# ``tests/test_corpus_lints.py``. The vocabulary below is the one thing this
# module takes from the harness: a frozenset of strings is data, not the loader
# the note on CLAIMS_DIR refuses to check the corpus through, and spelling
# twenty verbs twice would guarantee the two copies drift.
sys.path.insert(0, str(_REPO_ROOT))

from analysis_service.frameworks import PACKAGES, run_precondition
from analysis_service.frameworks.asvs.catalog import ASVS_LEVELS, requirements_for
from analysis_service.frameworks.stride.record import STRIDE_CATEGORIES
from analysis_service.grounding import verify_quote
from analysis_service.report import (
    FrameworkName,
    InputRef,
    Rating,
    SourceRef,
    derive_severity_level,
)
from analysis_service.sources import SourceKind
from analysis_service.system_model import SystemModel
from analysis_service.validation import parse_and_validate
from evals.harness.reference import AsvsDisposition
from evals.harness.verbs import unknown_verbs

SOURCE_KINDS = frozenset(get_args(SourceKind))

CORPUS_DIR = Path(__file__).resolve().parent / "corpus"
CALIBRATION_PATH = Path(__file__).resolve().parent / "calibration_labels" / "pairs.json"

# A reference set must be exhaustively enumerable by a person, which is only
# true of small systems.
MIN_ELEMENTS = 8
MAX_ELEMENTS = 20

# A floor, not a count: the fixtures hold 339. A set below this, or one that
# is all matches or all non-matches, cannot measure agreement.
MIN_CALIBRATION_PAIRS = 100
MIN_LABEL_SHARE = 0.3

RATINGS = frozenset(("low", "medium", "high"))
TIERS = frozenset(("must-find", "expected"))
EXEMPLAR_PROXIMITY = frozenset(("near", "far"))
CASE_FIELDS = frozenset(
    (
        "id",
        "title",
        "domain",
        "provenance",
        "bootstrap",
        "sources",
        "source_sha256",
        "frameworks",
        "notes",
    )
)
#: Allowed on a case, and empty until somebody holds a sitting: the
#: ``BLESSING.md`` step 6 sign-offs, append-only. ``tests/test_case_review.py``
#: is what makes their absence countable, and ``CaseSitting`` in
#: ``harness/reference.py`` is what checks each entry's shape — this set
#: decides only that the field may appear.
OPTIONAL_CASE_FIELDS = frozenset(("reviews",))
CASE_FRAMEWORK_FIELDS = frozenset(("name", "options", "exemplar_proximity"))
#: What every framework's reference record carries, whatever it grades with.
CLAIM_FIELDS = frozenset(("claim", "tier", "affected_element_ids", "notes"))
PAIR_FIELDS = frozenset(
    (
        "case",
        "category",
        "reference_claim",
        "reference_element_ids",
        "candidate_claim",
        "candidate_element_ids",
        "reference_verb",
        "candidate_verb",
        "label",
        "note",
    )
)

#: Where one framework's reference records live inside a case, relative to it.
#: Spelled here as well as in :mod:`evals.harness.reference` because this module
#: reads the corpus as raw JSON on purpose — a lint that went through the
#: harness's loader would be checking the loader.
CLAIMS_DIR = "claims"


def claims_file(case_dir: Path, framework: FrameworkName) -> Path:
    return case_dir / CLAIMS_DIR / f"{framework}.json"


def _stride_record_issues(
    where: str, record: dict, options: Mapping[str, Any]
) -> Iterator[str]:
    """What STRIDE's reference record carries beyond the neutral fields.

    A category, a graded severity, and at least one element: STRIDE-per-element
    is what the framework *means*, so a reference threat naming nothing in the
    graph is unscoreable rather than a legal record.
    """
    del options  # STRIDE declares none
    if record["category"] not in STRIDE_CATEGORIES:
        yield f"{where} category {record['category']!r} is not a STRIDE category"

    if not record["affected_element_ids"]:
        yield f"{where} cites no elements"

    severity = record["severity"]
    if severity.keys() != {"likelihood", "impact"}:
        yield f"{where} severity must carry likelihood and impact only"
        return
    for field, rating in severity.items():
        if rating not in RATINGS:
            yield f"{where} severity {field} {rating!r} is not a legal rating"


def _asvs_record_issues(
    where: str, record: dict, options: Mapping[str, Any]
) -> Iterator[str]:
    """What ASVS's reference record carries beyond the neutral fields.

    A chapter this package declares, and a requirement the published catalog
    holds in that chapter **at the level this case declares**. All three are
    checked against the catalog rather than against a pattern: a reference record
    naming a requirement outside the case's own level grades a run against a
    ruling no lane was asked to make.

    No severity and no element check. This package grades nothing, and most of
    its requirements address a coding practice with no position in the graph.

    The optional ``disposition`` is checked against the closed vocabulary rather
    than against a pattern, for the reason the requirement is checked against the
    catalog: a value nothing recognises scores as nothing at all.
    """
    chapter = record["chapter"]
    if chapter not in PACKAGES["asvs"].lanes:
        yield f"{where} chapter {chapter!r} is not an ASVS chapter"
        return
    level = options.get("level")
    if level not in ASVS_LEVELS:
        yield f"{where} sits in a case whose asvs options declare no legal level"
        return
    known = {req.id for req in requirements_for(level, chapter)}
    if record["requirement"] not in known:
        yield (
            f"{where} requirement {record['requirement']!r} is not in the"
            f" {chapter} chapter of ASVS {PACKAGES['asvs'].version} at level"
            f" {level}"
        )
    # A disposition outside the vocabulary would load as a validation error
    # rather than as a lint finding, which reports the whole corpus unreadable
    # instead of naming the one record. Caught here, where the record is.
    disposition = record.get("disposition")
    if disposition is not None and disposition not in ASVS_DISPOSITIONS:
        yield (
            f"{where} disposition {disposition!r} is not one of"
            f" {sorted(ASVS_DISPOSITIONS)}"
        )


#: Whether every lane a package declares must appear in *every* case's reference
#: set. True where a package's lanes are unconditional — all six STRIDE
#: categories apply to any system that can be drawn as a graph, so a case
#: grading five of them is a case quietly missing one.
#:
#: False for ASVS, and that is the standard's own position rather than a
#: concession: it tells an operator to filter out the chapters that do not apply,
#: naming OAuth and WebRTC by name. A corpus case with no OAuth has no OAuth
#: requirement to expect, and demanding a record there would make the merge bar
#: reward a fabricated expectation. What still holds for every package is
#: :func:`lane_coverage_issues` — every lane must be measured *somewhere*.
LANES_REQUIRED_PER_CASE: Mapping[FrameworkName, bool] = {
    "asvs": False,
    "stride": True,
}

#: The extra fields each framework's reference record carries, and the checks
#: over them. Harness data keyed off the closed ``FrameworkName``, beside
#: :data:`evals.harness.reference.REFERENCE_TYPES` and for the same reason: what
#: a reference set looks like is the eval's business, not a package member.
#: The expected dispositions a record may name, from the corpus vocabulary
#: rather than a second list here — a lint spelling its own would pass a value
#: the loader then refuses.
ASVS_DISPOSITIONS: frozenset[str] = frozenset(get_args(AsvsDisposition))

RECORD_FIELDS: Mapping[FrameworkName, frozenset[str]] = {
    "asvs": CLAIM_FIELDS | {"chapter", "requirement"},
    "stride": CLAIM_FIELDS | {"category", "severity"},
}
#: Fields a record **may** carry, keyed like the required set beside it. A field
#: here is permitted and never demanded, which is what lets the corpus fill in
#: ``verb`` one blessing pass at a time instead of in one unreviewable edit.
#: ``tests/test_verb_coverage.py`` is what stops that becoming permanent: it
#: counts the claims still missing one and fails when the count moves.
#:
#: ASVS needs no verb — a claim whose identity is a catalog requirement
#: identifier composes nothing. It carries ``disposition`` instead, on the same
#: footing and for the same reason: a framework that defers a requirement for
#: want of a kind of evidence the job does not hold has an expected routing
#: answer to record, and one whose claims rest on the system's own shape defers
#: nothing and has none. Both are properties of a framework rather than its
#: name, so both answer for a package nobody has written.
#: ``tests/test_asvs_disposition_coverage.py`` counts the records still missing
#: one, exactly as the verb count does.
OPTIONAL_RECORD_FIELDS: Mapping[FrameworkName, frozenset[str]] = {
    "asvs": frozenset({"disposition"}),
    "stride": frozenset({"verb"}),
}
RECORD_CHECKS: Mapping[
    FrameworkName, Callable[[str, dict, Mapping[str, Any]], Iterator[str]]
] = {
    "asvs": _asvs_record_issues,
    "stride": _stride_record_issues,
}
#: Which lane a reference record belongs to, or ``None`` for a framework whose
#: records carry no lane. Read by :func:`lane_coverage_issues`, which is the
#: check that an unmeasured lane — one ``strong`` call per lane, which is the
#: granularity that spends money — cannot reach a deployment.
RECORD_LANE: Mapping[FrameworkName, Callable[[dict], object]] = {
    "asvs": lambda record: record.get("chapter"),
    "stride": lambda record: record.get("category"),
}


def case_dirs() -> list[Path]:
    """Every case directory in the corpus, in stable numeric-prefix order."""
    return sorted(path for path in CORPUS_DIR.iterdir() if path.is_dir())


def _load_json(path: Path) -> object:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


# The two shape-asserting loaders below are for the readers that walk a file's
# contents directly. The lints that *report* on a shape -- `_check_citations`
# and `_check_threats` -- take the bare `object` instead and narrow it
# themselves, because "this file is not a list" is a finding they yield rather
# than a crash. Everything else in this module would fail on the same file with
# an AttributeError several frames from the cause; these say which file.
def _load_json_object(path: Path) -> dict[str, Any]:
    """A JSON file whose top level this module reads as an object."""
    value = _load_json(path)
    if not isinstance(value, dict):
        raise TypeError(f"{path}: expected a JSON object at the top level")
    return value


def _load_json_array(path: Path) -> list[Any]:
    """A JSON file whose top level this module reads as an array."""
    value = _load_json(path)
    if not isinstance(value, list):
        raise TypeError(f"{path}: expected a JSON array at the top level")
    return value


SOURCE_FIELDS = frozenset(("kind", "label", "file", "sha256"))


def source_sha256(source_path: Path) -> str:
    """One source's digest, over the exact submitted bytes."""
    return hashlib.sha256(source_path.read_bytes()).hexdigest()


def declared_sources(case_dir: Path, meta: dict) -> dict[str, str]:
    """This case's sources as label to text, for the citation lint.

    The text and not just the label, because a citation resolving is only half
    of what the service checks. A source whose file is missing or unreadable
    maps to the empty string rather than being dropped: its label still has to
    be citable, and :func:`_check_sources` is what reports the missing file.
    """
    sources = meta.get("sources")
    if not isinstance(sources, list):
        return {}
    declared: dict[str, str] = {}
    for source in sources:
        if not isinstance(source, dict) or not isinstance(source.get("label"), str):
            continue
        path = (
            case_dir / source["file"] if isinstance(source.get("file"), str) else None
        )
        text = path.read_text(encoding="utf-8") if path and path.is_file() else ""
        declared[source["label"]] = text
    return declared


def _check_sources(case_dir: Path, meta: dict) -> Iterator[str]:
    """Each declared source exists, digests as claimed, and is named once."""
    sources = meta.get("sources")
    if not isinstance(sources, list) or not sources:
        yield "case.json sources must be a non-empty list"
        return

    refs = []
    seen_labels = set()
    for index, source in enumerate(sources):
        where = f"sources[{index}]"
        if not isinstance(source, dict):
            yield f"{where} is not an object"
            continue
        missing = SOURCE_FIELDS - source.keys()
        unexpected = source.keys() - SOURCE_FIELDS
        if missing or unexpected:
            yield (
                f"{where} fields wrong: missing {sorted(missing)},"
                f" extra {sorted(unexpected)}"
            )
            continue
        if source["kind"] not in SOURCE_KINDS:
            yield (
                f"{where} kind {source['kind']!r} is not one of {sorted(SOURCE_KINDS)}"
            )
        if source["label"] in seen_labels:
            yield f"{where} label {source['label']!r} is used twice in one case"
        seen_labels.add(source["label"])

        path = case_dir / source["file"]
        if not path.is_file():
            yield f"{where} names {source['file']!r}, which does not exist"
            continue
        expected = source_sha256(path)
        if source["sha256"] != expected:
            yield (
                f"{where} sha256 does not match {source['file']} (expected {expected})"
            )
            continue
        refs.append(
            SourceRef(kind=source["kind"], label=source["label"], sha256=expected)
        )

    if len(refs) == len(sources):
        expected_aggregate = InputRef.aggregate_digest(refs)
        if meta.get("source_sha256") != expected_aggregate:
            yield (
                "source_sha256 is not the aggregate over the declared sources"
                f" (expected {expected_aggregate})"
            )


def _check_citations(model: object, sources: dict[str, str]) -> Iterator[str]:
    """Every citation in the model resolves to a case source, and is really in it.

    This is what ties the corpus to the gate rules it exists to exercise: the
    service rejects a model citing a label its job never carried *and* one
    whose excerpt cannot be found in the source it names, so a corpus that
    drifted from its own case.json on either count would be grading a shape the
    service would refuse.

    Re-asserted here rather than delegated to :func:`parse_and_validate`, for
    the reason ``evals/harness/structural.py`` gives about its own gates: a
    check that would silently weaken if someone relaxed the shipped validator
    is not a check. The corpus is the thing those validators are measured
    against, so it verifies itself.

    A source declared with no readable text skips the excerpt half alone — the
    label must still resolve, and the missing file is :func:`_check_sources`'
    to report rather than this function's to report twice.
    """
    if not isinstance(model, dict):
        return
    for elements in model.values():
        if not isinstance(elements, list):
            continue
        for element in elements:
            if not isinstance(element, dict):
                continue
            excerpt = element.get("source_excerpt")
            label = element.get("source_label")
            if not excerpt:
                continue
            if not label:
                yield (
                    f"model.json: {element.get('id')} has a source_excerpt with"
                    " no source_label"
                )
            elif label not in sources:
                yield (
                    f"model.json: {element.get('id')} cites source_label"
                    f" {label!r}, which this case does not declare"
                )
            elif sources[label] and not verify_quote(excerpt, sources[label]):
                yield (
                    f"model.json: {element.get('id')} quotes {excerpt!r}, which is"
                    f" not found in the source it cites, {label!r}"
                )


def _check_case_metadata(case_dir: Path, meta: dict) -> Iterator[str]:
    missing = CASE_FIELDS - meta.keys()
    unexpected = meta.keys() - CASE_FIELDS - OPTIONAL_CASE_FIELDS
    if missing:
        yield f"case.json is missing fields: {sorted(missing)}"
    if unexpected:
        yield f"case.json has unexpected fields: {sorted(unexpected)}"
    if meta.get("id") != case_dir.name:
        yield f"case.json id {meta.get('id')!r} does not match directory name"
    yield from _check_declared_frameworks(meta)
    yield from _check_sources(case_dir, meta)


def _check_declared_frameworks(meta: dict) -> Iterator[str]:
    """The ``frameworks`` block is well-formed and names each package once."""
    declared = meta.get("frameworks")
    if not isinstance(declared, list) or not declared:
        yield "case.json frameworks must be a non-empty list"
        return

    seen: set[str] = set()
    for index, entry in enumerate(declared):
        where = f"frameworks[{index}]"
        if not isinstance(entry, dict):
            yield f"{where} is not an object"
            continue
        missing = CASE_FRAMEWORK_FIELDS - entry.keys()
        unexpected = entry.keys() - CASE_FRAMEWORK_FIELDS
        if missing or unexpected:
            yield (
                f"{where} fields wrong: missing {sorted(missing)},"
                f" extra {sorted(unexpected)}"
            )
            continue
        if entry["name"] not in PACKAGES:
            yield f"{where} names {entry['name']!r}, which this build does not carry"
        elif entry["name"] in seen:
            yield f"{where} declares {entry['name']!r} twice"
        seen.add(entry["name"])
        if not isinstance(entry["options"], dict):
            yield f"{where} options must be an object"
        if entry["exemplar_proximity"] not in EXEMPLAR_PROXIMITY:
            yield (
                f"{where} exemplar_proximity"
                f" {entry['exemplar_proximity']!r} is not near/far"
            )


def _check_claims(
    framework: FrameworkName,
    records: object,
    element_ids: set[str],
    options: Mapping[str, Any],
) -> Iterator[str]:
    """One framework's reference file, on the neutral rules plus its own.

    The neutral half is every framework's: a legal tier, a one-sentence claim,
    and cited elements that exist in the blessed model. The rest comes from
    :data:`RECORD_CHECKS`, so a second framework's record shape is a table entry
    rather than a branch here.
    """
    where_file = f"{CLAIMS_DIR}/{framework}.json"
    if not isinstance(records, list) or not records:
        yield f"{where_file} must be a non-empty list"
        return

    fields = RECORD_FIELDS[framework]
    optional = OPTIONAL_RECORD_FIELDS[framework]
    for index, record in enumerate(records):
        where = f"{framework}[{index}]"
        if not isinstance(record, dict):
            yield f"{where} is not an object"
            continue
        missing = fields - record.keys()
        unexpected = record.keys() - fields - optional
        if missing:
            yield f"{where} is missing fields: {sorted(missing)}"
            continue
        if unexpected:
            yield f"{where} has unexpected fields: {sorted(unexpected)}"

        if record["tier"] not in TIERS:
            yield f"{where} tier {record['tier']!r} is not must-find/expected"

        # A verb outside the vocabulary would compare equal to nothing, which
        # reads downstream as a tool that found nothing rather than as a claim
        # written wrong. Caught here, where the claim is.
        for bad in unknown_verbs([record["verb"]] if "verb" in record else []):
            yield f"{where} verb {bad!r} is not in evals.harness.verbs"

        # Mirrors the exemplar lint: a reference record that cites an element
        # the blessed model does not have is unscoreable. Citing *none* is legal
        # here and refused by the frameworks whose records are about elements;
        # that split is :data:`RECORD_CHECKS`'.
        for element_id in record["affected_element_ids"]:
            if element_id not in element_ids:
                yield f"{where} cites {element_id!r}, absent from model.json"

        claim = record["claim"].strip()
        if not claim.endswith("."):
            yield f"{where} claim is not a single terminated sentence"
        if claim.count(".") != 1:
            yield f"{where} claim is not one sentence: {claim!r}"

        yield from RECORD_CHECKS[framework](where, record, options)

    # Every lane represented in every case, which is stricter than the merge bar
    # below and is what this corpus has always held for STRIDE. The bar asks that
    # a lane be measured *somewhere*; this asks that no case quietly grades five
    # of six. :data:`LANES_REQUIRED_PER_CASE` says why ASVS is exempt.
    lane_of = RECORD_LANE[framework]
    seen_lanes = {lane_of(record) for record in records if isinstance(record, dict)}
    stray = sorted(str(lane) for lane in seen_lanes - set(PACKAGES[framework].lanes))
    if stray:
        yield f"{where_file} names lanes {framework} does not declare: {stray}"
    if LANES_REQUIRED_PER_CASE[framework]:
        for lane in PACKAGES[framework].lanes:
            if lane not in seen_lanes:
                yield f"{where_file} carries no reference record in the {lane} lane"

    if not any(record.get("tier") == "must-find" for record in records):
        yield (
            f"{where_file} carries no must-find record: tier 2 recall would be"
            " vacuous for this case"
        )


def declared_names(meta: dict) -> list[str]:
    """The frameworks ``case.json`` declares, however malformed the block is."""
    declared = meta.get("frameworks")
    if not isinstance(declared, list):
        return []
    return [
        entry["name"]
        for entry in declared
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    ]


def declared_options(meta: dict) -> dict[str, Mapping[str, Any]]:
    """Each framework ``case.json`` declares, against the options it declares it with.

    The options are what a job would submit, so a reference set is checked
    against them: an ASVS level decides which requirements a run rules on, and a
    record outside it is unmeetable.
    """
    declared = meta.get("frameworks")
    if not isinstance(declared, list):
        return {}
    return {
        entry["name"]: entry.get("options") or {}
        for entry in declared
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    }


#: Cases whose model satisfies a framework's **Precondition** and which carry no
#: reference set for it yet, with what each one is still missing.
#:
#: **Missing work, never an exemption.** Every entry is a case the corpus should
#: grade and does not, so every ASVS number in the suite is computed over less of
#: the corpus than it could be. The list is meant to reach zero, and is there now.
#:
#: It held four cases between
#: [#234](https://github.com/mstarks01/work-agent/pull/234), which made the
#: precondition read what a **Process** presents, and
#: [#236](https://github.com/mstarks01/work-agent/pull/236), which wrote the four
#: reference sets. Kept rather than deleted because the next package whose
#: precondition can refuse will need it the same way: a case that starts
#: satisfying one and carries no records for it belongs here by name, not in
#: silence.
PENDING_REFERENCE_SETS: dict[str, frozenset[str]] = {}


def framework_issues(case_dir: Path, meta: dict, model: SystemModel) -> Iterator[str]:
    """The per-case half of the merge bar: two of #167's three checks.

    Every registered framework carries a reference file for every case its own
    **Precondition** does not refuse, and the ``frameworks`` declaration agrees
    with that precondition run over the blessed ``model.json``.

    The precondition is a pure function of the **Valid System Model**, so both
    are credential-free and run on every PR. A refused case and a missing file
    are different facts — the first scores nothing and is reported *unexercised*,
    the second is an unmeasured framework — and only the declaration can tell
    them apart, which is why the declaration is checked against the predicate
    rather than trusted.

    **Only ``satisfied`` earns a reference set.** The run-time gate runs a
    framework's lanes on that answer alone, so a case whose model leaves the
    question open produces no claims to grade — and an expectation nothing can
    meet is worse than no expectation. ``refuted`` and ``undecidable`` are still
    kept apart everywhere their remedy differs; here their consequence is the
    same and the check treats them alike.
    """
    declared = set(declared_names(meta))
    for name, package in PACKAGES.items():
        result = run_precondition(package, model)
        if result != "satisfied":
            if name in declared:
                yield (
                    f"case.json declares {name!r}, but its precondition answers"
                    f" {result} for this case; only a satisfied framework runs its"
                    " lanes, so only one carries a reference set"
                )
            continue
        if name not in declared:
            # A named gap is not silence: the case is listed, what it still
            # needs is written down, and an entry that no longer applies fails
            # below.
            if name not in PENDING_REFERENCE_SETS.get(case_dir.name, frozenset()):
                yield (
                    f"case.json does not declare {name!r}, whose precondition"
                    " satisfies this case; every framework a case runs must carry"
                    " a reference set. If it is not being written now, add it to"
                    " PENDING_REFERENCE_SETS with what the case is missing"
                )
            continue
        if name in PENDING_REFERENCE_SETS.get(case_dir.name, frozenset()):
            yield (
                f"case.json declares {name!r} and PENDING_REFERENCE_SETS still"
                f" lists {case_dir.name!r} as missing it; the reference set"
                " exists now, so remove the entry"
            )
        if not claims_file(case_dir, name).is_file():
            yield f"case.json declares {name!r}, but {CLAIMS_DIR}/{name}.json is absent"


def lane_coverage_issues(must_find_lanes: Mapping[str, set[object]]) -> Iterator[str]:
    """The corpus-wide half of the merge bar: #167's second check.

    Every lane a package declares carries at least one ``must-find`` record
    somewhere in the corpus. A lane is one **Model Tier** call, so an unmeasured
    lane is the granularity that spends money — which is why the bar sits at the
    lane rather than at the framework.
    """
    for name, package in PACKAGES.items():
        seen = must_find_lanes.get(name, set())
        for lane in package.lanes:
            if lane not in seen:
                yield (
                    f"{name}: the {lane} lane carries no must-find record anywhere"
                    " in the corpus, so a run of it is unmeasured"
                )


def check_case(case_dir: Path) -> list[str]:
    """Every mechanical failure in one case, empty if the case is sound."""
    problems: list[str] = []
    for name in ("source.md", "model.json", "case.json"):
        if not (case_dir / name).exists():
            problems.append(f"missing {name}")
    if problems:
        return problems

    meta = _load_json_object(case_dir / "case.json")
    problems.extend(_check_case_metadata(case_dir, meta))

    raw_model = _load_json(case_dir / "model.json")
    problems.extend(_check_citations(raw_model, declared_sources(case_dir, meta)))

    model, issues = parse_and_validate(raw_model)
    problems.extend(f"model.json: {issue.code}: {issue.message}" for issue in issues)
    if model is None:
        return problems

    element_count = len(model.elements())
    if not MIN_ELEMENTS <= element_count <= MAX_ELEMENTS:
        problems.append(
            f"model.json has {element_count} elements, outside the"
            f" {MIN_ELEMENTS}-{MAX_ELEMENTS} band"
        )
    # Derivation fails closed on a malformed model; a case with no crossing at
    # all is a modelling mistake, since crossings are the highest-signal input.
    if not model.boundary_crossings():
        problems.append("model.json derives no boundary crossing")

    problems.extend(framework_issues(case_dir, meta, model))

    # Only the packages this build carries: a declaration naming something else
    # has no record shape to check against, and :func:`framework_issues` has
    # already reported it.
    element_ids = {element.id for element in model.elements()}
    options = declared_options(meta)
    for name in PACKAGES:
        path = claims_file(case_dir, name)
        if name in options and path.is_file():
            problems.extend(
                _check_claims(name, _load_json(path), element_ids, options[name])
            )
    return problems


def calibration_inputs() -> tuple[dict[str, set[str]], dict[str, dict[str, list[str]]]]:
    """What :func:`check_calibration` compares fixtures against, per case.

    The blessed model's element IDs, and each STRIDE reference claim mapped to
    its own sorted element IDs. STRIDE's reference file only, because the
    composed identity is STRIDE's: a framework that matches by requirement ID
    reaches no claim-equivalence question and contributes no pair.
    """
    elements: dict[str, set[str]] = {}
    claims: dict[str, dict[str, list[str]]] = {}
    for case_dir in case_dirs():
        records = _load_json_array(claims_file(case_dir, "stride"))
        claims[case_dir.name] = {
            record["claim"]: sorted(record["affected_element_ids"])
            for record in records
            if isinstance(record, dict)
        }
        model, _ = parse_and_validate(_load_json(case_dir / "model.json"))
        elements[case_dir.name] = (
            {element.id for element in model.elements()} if model else set()
        )
    return elements, claims


def _check_pair_elements(
    where: str,
    pair: dict[str, Any],
    element_ids: set[str],
    reference_elements: list[str],
) -> list[str]:
    """One pair's two element-ID fields, against the corpus they came from."""
    problems = []
    if sorted(pair["reference_element_ids"]) != reference_elements:
        problems.append(
            f"{where} reference_element_ids {pair['reference_element_ids']} are"
            f" not the claim's own {reference_elements}; re-run build_pairs.py"
        )
    candidate = pair["candidate_element_ids"]
    if candidate is None:
        # Only ``match`` pairs are assigned so far, and the ones that are not
        # are excluded by the identity measurement rather than scored as a miss.
        if pair["label"] == "match":
            problems.append(
                f"{where} is labelled match and carries no candidate_element_ids;"
                " every match pair is assigned, so this one was missed"
            )
        return problems
    dangling = sorted(set(candidate) - element_ids)
    if dangling:
        problems.append(
            f"{where} candidate_element_ids name elements absent from"
            f" {pair['case']}'s model: {dangling}"
        )
    if sorted(candidate) != list(candidate):
        problems.append(f"{where} candidate_element_ids are not sorted: {candidate}")
    return problems


def check_calibration(
    elements_by_case: dict[str, set[str]],
    claims_by_case: dict[str, dict[str, list[str]]],
) -> list[str]:
    """Every mechanical failure in the calibration-label fixtures.

    The two element-ID fields are checked the way the claim strings are, and for
    the same reason. ``reference_element_ids`` is copied out of the corpus by
    ``build_pairs.py``, so a reference whose elements are re-cut and a fixture
    file nobody regenerated show up here rather than as a silent drop in the
    mechanical-identity number. ``candidate_element_ids`` is a hand assignment,
    so what is checkable is that every ID resolves in that case's blessed model
    — a candidate citing an element the model does not hold is unscoreable in
    exactly the way a dangling reference claim is.
    """
    pairs = _load_json_array(CALIBRATION_PATH)
    problems: list[str] = []
    if len(pairs) < MIN_CALIBRATION_PAIRS:
        problems.append(
            f"{len(pairs)} labelled pairs, under the {MIN_CALIBRATION_PAIRS} the"
            " agreement bar was sized against"
        )

    for index, pair in enumerate(pairs):
        where = f"pairs[{index}]"
        missing = PAIR_FIELDS - pair.keys()
        if missing:
            problems.append(f"{where} is missing fields: {sorted(missing)}")
            continue
        if pair["case"] not in claims_by_case:
            problems.append(f"{where} names unknown case {pair['case']!r}")
        elif pair["reference_claim"] not in claims_by_case[pair["case"]]:
            problems.append(f"{where} reference_claim is not a claim in {pair['case']}")
        else:
            problems.extend(
                _check_pair_elements(
                    where,
                    pair,
                    elements_by_case[pair["case"]],
                    claims_by_case[pair["case"]][pair["reference_claim"]],
                )
            )
        if pair["category"] not in STRIDE_CATEGORIES:
            problems.append(f"{where} category {pair['category']!r} is not a lane")
        if pair["label"] not in ("match", "no-match"):
            problems.append(f"{where} label {pair['label']!r} is not match/no-match")

    matches = sum(1 for pair in pairs if pair.get("label") == "match")
    for label, count in (("match", matches), ("no-match", len(pairs) - matches)):
        if count < MIN_LABEL_SHARE * len(pairs):
            problems.append(
                f"only {count} of {len(pairs)} pairs are labelled {label};"
                " agreement on a lopsided fixture set is not informative"
            )
    return problems


def _severity_bands() -> Iterator[str]:
    """Guard the one arithmetic the corpus shares with production.

    STRIDE's alone, because the matrix is: a framework that grades no harm
    carries no ``severity`` on its records and has nothing to derive.
    """
    for case_dir in case_dirs():
        path = claims_file(case_dir, "stride")
        if not path.is_file():
            continue
        for threat in _load_json_array(path):
            severity = threat.get("severity", {})
            likelihood: Rating = severity.get("likelihood")
            impact: Rating = severity.get("impact")
            if likelihood in RATINGS and impact in RATINGS:
                derive_severity_level(likelihood, impact)
                continue
            yield f"{case_dir.name}: severity {severity!r} has no derivable band"


def write_shas() -> None:
    """Restamp every declared source's digest, and the aggregate over them."""
    for case_dir in case_dirs():
        meta_path = case_dir / "case.json"
        meta = _load_json_object(meta_path)
        refs = []
        for source in meta.get("sources", []):
            source["sha256"] = source_sha256(case_dir / source["file"])
            refs.append(
                SourceRef(
                    kind=source["kind"],
                    label=source["label"],
                    sha256=source["sha256"],
                )
            )
        meta["source_sha256"] = InputRef.aggregate_digest(refs)
        meta_path.write_text(
            json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-sha",
        action="store_true",
        help="stamp source_sha256 into each case.json instead of checking",
    )
    args = parser.parse_args()
    if args.write_sha:
        write_shas()
        print("stamped source_sha256 for", len(case_dirs()), "cases")
        return 0

    failures = 0
    # Lane -> whether any case anywhere carries a must-find record for it. The
    # merge bar's second check is over the whole corpus, so it is accumulated
    # here rather than answered per case.
    must_find_lanes: dict[str, set[object]] = {}
    for case_dir in case_dirs():
        problems = check_case(case_dir)
        for name in PACKAGES:
            path = claims_file(case_dir, name)
            if not path.is_file():
                continue
            records = _load_json_array(path)
            must_find_lanes.setdefault(name, set()).update(
                RECORD_LANE[name](record)
                for record in records
                if isinstance(record, dict) and record.get("tier") == "must-find"
            )
        for problem in problems:
            print(f"{case_dir.name}: {problem}")
        failures += len(problems)

    for problem in lane_coverage_issues(must_find_lanes):
        print(f"merge bar: {problem}")
        failures += 1

    for problem in _severity_bands():
        print(problem)
        failures += 1

    if CALIBRATION_PATH.exists():
        for problem in check_calibration(*calibration_inputs()):
            print(f"calibration_labels: {problem}")
            failures += 1
    else:
        print(f"calibration_labels: {CALIBRATION_PATH.name} not authored yet")
        failures += 1

    print(f"{len(case_dirs())} cases checked, {failures} problems")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
