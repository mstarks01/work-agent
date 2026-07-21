"""``ReferenceThreat`` and the corpus loader (ticket 023).

The loader is checked against the shipped corpus, not a synthetic fixture:
"fits the ticket-022 layout" is the requirement, and only the real files test
it. Fail-closed behaviour gets its own tempdir cases.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.harness.reference import (
    CorpusError,
    ReferenceThreat,
    load_case,
    load_corpus,
)
from stride_service.report import STRIDE_CATEGORIES, derive_severity_level

CORPUS_DIR = Path(__file__).resolve().parents[1] / "evals" / "corpus"


@pytest.fixture(scope="module")
def corpus():
    return load_corpus(CORPUS_DIR)


def test_loads_every_shipped_case(corpus):
    assert len(corpus) == 12
    assert [case.id for case in corpus] == sorted(case.id for case in corpus)


def test_exactly_one_near_exemplar_control(corpus):
    # Without the payments control there is nothing to subtract from, and the
    # exemplar-domain-bias delta is unmeasurable (ticket 009 decision 6).
    near = [case.id for case in corpus if case.meta.exemplar_proximity == "near"]
    assert near == ["01-payments-checkout"]


def test_every_case_carries_must_find_references(corpus):
    for case in corpus:
        assert case.must_find, f"{case.id} would make tier 2 recall vacuous"
        assert len(case.references) >= len(case.must_find)


def test_references_span_every_lane(corpus):
    for case in corpus:
        lanes = {reference.category for reference in case.references}
        assert lanes == set(STRIDE_CATEGORIES), f"{case.id} misses lanes"


def test_reference_severity_band_uses_shipped_arithmetic(corpus):
    for case in corpus:
        for reference in case.references:
            assert reference.severity.level == derive_severity_level(
                reference.severity.likelihood, reference.severity.impact
            )


def test_blessed_models_are_small_enough_to_enumerate(corpus):
    # Ground truth is only exhaustively enumerable by a human on small systems
    # (decision 5); non-exhaustive references silently corrupt precision.
    for case in corpus:
        assert 8 <= len(case.model.elements()) <= 20


def test_reference_threat_rejects_unknown_fields():
    with pytest.raises(ValueError):
        ReferenceThreat.model_validate(
            {
                "category": "spoofing",
                "affected_element_ids": ["entity:x"],
                "claim": "An attacker does a thing.",
                "tier": "must-find",
                "severity": {"likelihood": "high", "impact": "high"},
                "mitigations": [],  # a DraftThreat field, never graded here
            }
        )


def test_reference_threat_rejects_unknown_tier():
    with pytest.raises(ValueError):
        ReferenceThreat.model_validate(
            {
                "category": "spoofing",
                "affected_element_ids": ["entity:x"],
                "claim": "An attacker does a thing.",
                "tier": "nice-to-have",
                "severity": {"likelihood": "high", "impact": "high"},
            }
        )


def _copy_case(source: Path, destination: Path) -> Path:
    case_dir = destination / source.name
    case_dir.mkdir(parents=True)
    for name in ("source.md", "model.json", "threats.json", "case.json"):
        (case_dir / name).write_bytes((source / name).read_bytes())
    return case_dir


def test_dangling_element_reference_fails_closed(tmp_path):
    # Mirrors ticket 020's exemplar guard: a reference citing an element the
    # blessed model lacks is unscoreable, and dropping it silently would lower
    # the recall denominator without anyone noticing.
    case_dir = _copy_case(CORPUS_DIR / "01-payments-checkout", tmp_path)
    threats = json.loads((case_dir / "threats.json").read_text())
    threats[0]["affected_element_ids"] = ["process:does-not-exist"]
    (case_dir / "threats.json").write_text(json.dumps(threats))

    with pytest.raises(CorpusError, match="absent from model.json"):
        load_case(case_dir)


def test_invalid_blessed_model_fails_closed(tmp_path):
    case_dir = _copy_case(CORPUS_DIR / "02-iot-fleet-telemetry", tmp_path)
    model = json.loads((case_dir / "model.json").read_text())
    model["processes"][0]["trust_zone"] = "boundary:not-declared"
    (case_dir / "model.json").write_text(json.dumps(model))

    with pytest.raises(CorpusError, match="model.json is not valid"):
        load_case(case_dir)


def test_missing_file_fails_closed(tmp_path):
    case_dir = _copy_case(CORPUS_DIR / "03-batch-data-pipeline", tmp_path)
    (case_dir / "threats.json").unlink()

    with pytest.raises(CorpusError, match="missing threats.json"):
        load_case(case_dir)


def test_case_id_must_match_directory(tmp_path):
    case_dir = _copy_case(CORPUS_DIR / "04-ml-inference-service", tmp_path)
    meta = json.loads((case_dir / "case.json").read_text())
    meta["id"] = "renamed"
    (case_dir / "case.json").write_text(json.dumps(meta))

    with pytest.raises(CorpusError, match="does not match the directory name"):
        load_case(case_dir)


def test_empty_corpus_fails_closed(tmp_path):
    with pytest.raises(CorpusError, match="no cases"):
        load_corpus(tmp_path)
