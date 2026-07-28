"""Ticket 022's corpus checks, ported into the offline test job.

The same move ticket 019's verification script made into ticket 020's lints:
the script stays runnable by hand for corpus authors, and CI runs it here so a
corpus edit cannot land without it. Everything is deterministic and
credential-free, which is what lets it run on every PR (ticket 009 decision
17).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

EVALS_ROOT = Path(__file__).resolve().parents[1] / "evals"
sys.path.insert(0, str(EVALS_ROOT))

import verify_corpus


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


def test_recorded_source_digests_match_the_source_text():
    for case_dir in verify_corpus.case_dirs():
        meta = verify_corpus._load_json(case_dir / "case.json")
        assert meta["source_sha256"] == verify_corpus.source_sha256(
            case_dir / "source.md"
        )
