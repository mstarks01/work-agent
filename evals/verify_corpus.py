"""Mechanical checks over the golden corpus and the judge-calibration fixtures.

Everything here is deterministic and credential-free by construction;
``tests/test_corpus_lints.py`` runs the same checks in CI.

Run ``python evals/verify_corpus.py`` to check, ``--write-sha`` to stamp each
case's ``source_sha256`` from its ``source.md``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Iterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stride_service.report import (
    STRIDE_CATEGORIES,
    Rating,
    derive_severity_level,
)
from stride_service.validation import parse_and_validate

CORPUS_DIR = Path(__file__).resolve().parent / "corpus"
CALIBRATION_PATH = Path(__file__).resolve().parent / "judge_calibration" / "pairs.json"

# Ground truth must be exhaustively enumerable by a human, which is only true
# of small systems.
MIN_ELEMENTS = 8
MAX_ELEMENTS = 20

# ~100 hand-labelled pairs, and a fixture set that is all matches or all
# non-matches cannot measure agreement.
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
        "exemplar_proximity",
        "provenance",
        "bootstrap",
        "source_sha256",
        "notes",
    )
)
THREAT_FIELDS = frozenset(
    ("category", "affected_element_ids", "claim", "tier", "severity", "notes")
)
PAIR_FIELDS = frozenset(
    ("case", "category", "reference_claim", "candidate_claim", "label", "note")
)


def case_dirs() -> list[Path]:
    """Every case directory in the corpus, in stable numeric-prefix order."""
    return sorted(path for path in CORPUS_DIR.iterdir() if path.is_dir())


def _load_json(path: Path) -> object:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def source_sha256(source_path: Path) -> str:
    """The digest recorded in ``case.json``, over the exact submitted bytes."""
    return hashlib.sha256(source_path.read_bytes()).hexdigest()


def _check_case_metadata(case_dir: Path, meta: dict) -> Iterator[str]:
    missing = CASE_FIELDS - meta.keys()
    unexpected = meta.keys() - CASE_FIELDS
    if missing:
        yield f"case.json is missing fields: {sorted(missing)}"
    if unexpected:
        yield f"case.json has unexpected fields: {sorted(unexpected)}"
    if meta.get("id") != case_dir.name:
        yield f"case.json id {meta.get('id')!r} does not match directory name"
    if meta.get("exemplar_proximity") not in EXEMPLAR_PROXIMITY:
        yield f"exemplar_proximity {meta.get('exemplar_proximity')!r} is not near/far"
    expected_sha = source_sha256(case_dir / "source.md")
    if meta.get("source_sha256") != expected_sha:
        yield f"source_sha256 does not match source.md (expected {expected_sha})"


def _check_threats(threats: object, element_ids: set[str]) -> Iterator[str]:
    if not isinstance(threats, list) or not threats:
        yield "threats.json must be a non-empty list"
        return

    seen_categories = set()
    for index, threat in enumerate(threats):
        where = f"threats[{index}]"
        missing = THREAT_FIELDS - threat.keys()
        unexpected = threat.keys() - THREAT_FIELDS
        if missing:
            yield f"{where} is missing fields: {sorted(missing)}"
            continue
        if unexpected:
            yield f"{where} has unexpected fields: {sorted(unexpected)}"

        category = threat["category"]
        if category not in STRIDE_CATEGORIES:
            yield f"{where} category {category!r} is not a STRIDE category"
        else:
            seen_categories.add(category)

        if threat["tier"] not in TIERS:
            yield f"{where} tier {threat['tier']!r} is not must-find/expected"

        # Mirrors the exemplar lint: a reference threat that cites an element
        # the blessed model does not have is unscoreable.
        for element_id in threat["affected_element_ids"]:
            if element_id not in element_ids:
                yield f"{where} cites {element_id!r}, absent from model.json"
        if not threat["affected_element_ids"]:
            yield f"{where} cites no elements"

        claim = threat["claim"].strip()
        if not claim.endswith("."):
            yield f"{where} claim is not a single terminated sentence"
        if claim.count(".") != 1:
            yield f"{where} claim is not one sentence: {claim!r}"

        severity = threat["severity"]
        if severity.keys() != {"likelihood", "impact"}:
            yield f"{where} severity must carry likelihood and impact only"
            continue
        for field, rating in severity.items():
            if rating not in RATINGS:
                yield f"{where} severity {field} {rating!r} is not a legal rating"

    for category in STRIDE_CATEGORIES:
        if category not in seen_categories:
            yield f"no reference threat in the {category} lane"
    if not any(threat.get("tier") == "must-find" for threat in threats):
        yield "no must-find threat: tier 2 recall would be vacuous for this case"


def check_case(case_dir: Path) -> list[str]:
    """Every mechanical failure in one case, empty if the case is sound."""
    problems: list[str] = []
    for name in ("source.md", "model.json", "threats.json", "case.json"):
        if not (case_dir / name).exists():
            problems.append(f"missing {name}")
    if problems:
        return problems

    problems.extend(_check_case_metadata(case_dir, _load_json(case_dir / "case.json")))

    model, issues = parse_and_validate(_load_json(case_dir / "model.json"))
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

    element_ids = {element.id for element in model.elements()}
    problems.extend(_check_threats(_load_json(case_dir / "threats.json"), element_ids))
    return problems


def check_calibration(
    case_ids: set[str], claims_by_case: dict[str, set[str]]
) -> list[str]:
    """Every mechanical failure in the judge-calibration fixtures."""
    pairs = _load_json(CALIBRATION_PATH)
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
        if pair["case"] not in case_ids:
            problems.append(f"{where} names unknown case {pair['case']!r}")
        elif pair["reference_claim"] not in claims_by_case[pair["case"]]:
            problems.append(
                f"{where} reference_claim is not a claim in {pair['case']}"
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
    """Guard the one arithmetic the corpus shares with production."""
    for case_dir in case_dirs():
        for threat in _load_json(case_dir / "threats.json"):
            severity = threat.get("severity", {})
            likelihood: Rating = severity.get("likelihood")
            impact: Rating = severity.get("impact")
            if likelihood in RATINGS and impact in RATINGS:
                derive_severity_level(likelihood, impact)
                continue
            yield f"{case_dir.name}: severity {severity!r} has no derivable band"


def write_shas() -> None:
    """Stamp each case's ``source_sha256`` from its current ``source.md``."""
    for case_dir in case_dirs():
        meta_path = case_dir / "case.json"
        meta = _load_json(meta_path)
        meta["source_sha256"] = source_sha256(case_dir / "source.md")
        meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


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
    claims_by_case: dict[str, set[str]] = {}
    for case_dir in case_dirs():
        problems = check_case(case_dir)
        threats_path = case_dir / "threats.json"
        if threats_path.exists():
            claims_by_case[case_dir.name] = {
                threat["claim"] for threat in _load_json(threats_path)
            }
        for problem in problems:
            print(f"{case_dir.name}: {problem}")
        failures += len(problems)

    for problem in _severity_bands():
        print(problem)
        failures += 1

    if CALIBRATION_PATH.exists():
        for problem in check_calibration(set(claims_by_case), claims_by_case):
            print(f"judge_calibration: {problem}")
            failures += 1
    else:
        print(f"judge_calibration: {CALIBRATION_PATH.name} not authored yet")
        failures += 1

    print(f"{len(case_dirs())} cases checked, {failures} problems")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
