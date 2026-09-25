"""A holdout case is scored and reported apart, and no diagnosis reads it (#744).

Two halves. The rule has one reader, :func:`~evals.harness.reference.diagnosable`,
and these tests hold what calls it. The lint below finds every site that loads
the corpus by reading the modules, so a diagnosis command added tomorrow either
filters through :func:`~evals.harness.reference.tuning_cases` or says here why
it reads a holdout case.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import json
import shutil
from pathlib import Path

import pytest

from evals.harness import applicability, scorer
from evals.harness.provenance import REPO_ROOT
from evals.harness.reference import (
    CorpusError,
    diagnosable,
    load_case,
    load_corpus,
    refuse_holdout,
    tuning_cases,
)

CORPUS = REPO_ROOT / "evals" / "corpus"

#: The functions that load the corpus and read holdout cases, and why. Every
#: other caller of ``load_corpus`` outside the tests filters through
#: ``tuning_cases``.
HOLDOUT_READERS = {
    "evals/harness/run.py:command_run": "a sweep scores every case",
    "evals/harness/run.py:command_score": "scoring reports holdout figures apart",
    "evals/harness/run.py:command_replay": (
        "a rule-change replay re-scores the archive, and the holdout is scored"
    ),
    "evals/harness/run.py:command_calibrate": (
        "calibration pairs measure the identity rule, not a case's losses"
    ),
    "evals/harness/promotion.py": "a promotion gate is what the holdout serves",
    "evals/harness/sitting.py": "a person holds a sitting on a holdout case too",
    "evals/harness/envelope.py": "the offline sitting reads every case",
    "evals/review_submission.py": "a sitting on a holdout case is checked too",
}


def _loaders() -> set[str]:
    """Every call that reaches the golden ``load_corpus`` unfiltered, by site."""
    found = set()
    for path in sorted((REPO_ROOT / "evals").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        relative = path.relative_to(REPO_ROOT).as_posix()
        module = None
        for function in ast.walk(tree):
            if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            wrapped = {
                id(argument)
                for call in ast.walk(function)
                if isinstance(call, ast.Call) and _name(call) == "tuning_cases"
                for argument in call.args
            }
            for call in ast.walk(function):
                if not (
                    isinstance(call, ast.Call)
                    and _name(call) == "load_corpus"
                    and id(call) not in wrapped
                ):
                    continue
                module = module or importlib.import_module(
                    relative.removesuffix(".py").replace("/", ".")
                )
                if _resolves_to_the_loader(module, call.func):
                    found.add(f"{relative}:{function.name}")
    return found


def _resolves_to_the_loader(module: object, func: ast.expr) -> bool:
    """Whether the called name is the golden corpus's loader, through any alias.

    Resolved on the imported module, not read off the import line: the sitting
    modules call a re-export, and the adversarial corpus has a ``load_corpus``
    of its own.
    """
    target = module
    for part in ast.unparse(func).split("."):
        target = getattr(target, part, None)
    return target is load_corpus


def _name(call: ast.Call) -> str:
    func = call.func
    return func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")


def _declared(site: str) -> bool:
    module = site.split(":")[0]
    return site in HOLDOUT_READERS or module in HOLDOUT_READERS


def test_every_corpus_loader_filters_the_holdout_or_says_why_not():
    unfiltered = {site for site in _loaders() if not _declared(site)}

    assert not unfiltered, (
        "these load the corpus and read holdout cases; wrap the call in"
        f" tuning_cases or add the site to HOLDOUT_READERS: {sorted(unfiltered)}"
    )


def test_every_declared_holdout_reader_still_loads_the_corpus():
    sites = _loaders()
    stale = {
        entry
        for entry in HOLDOUT_READERS
        if not any(site == entry or site.startswith(entry + ":") for site in sites)
    }

    assert not stale, f"no longer load the corpus: {sorted(stale)}"


def test_the_lint_sees_an_unfiltered_call_and_passes_a_filtered_one():
    sites = _loaders()

    # Positive controls: a sweep loads every case, and the sitting modules
    # reach the loader through a re-export.
    assert "evals/harness/run.py:command_run" in sites
    assert any(site.startswith("evals/review_submission.py:") for site in sites)
    # A diagnosis command that filters is not reported.
    assert "evals/harness/run.py:command_near_misses" not in sites


@pytest.fixture
def holdout_corpus(tmp_path):
    """The real corpus, with case 01 marked as a holdout."""
    root = tmp_path / "corpus"
    shutil.copytree(CORPUS, root)
    path = root / "01-payments-checkout" / "case.json"
    case = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**case, "holdout": True}), encoding="utf-8")
    return root


def test_the_corpus_carries_no_holdout_case_yet():
    assert all(diagnosable(case) for case in load_corpus(CORPUS))


def test_a_holdout_case_is_loaded_and_left_out_of_tuning(holdout_corpus):
    cases = load_corpus(holdout_corpus)
    tuned = tuning_cases(cases)

    assert "01-payments-checkout" in {case.id for case in cases}
    assert "01-payments-checkout" not in {case.id for case in tuned}
    assert len(tuned) == len(cases) - 1


def test_a_replay_of_a_holdout_case_is_refused_by_name(holdout_corpus):
    with pytest.raises(CorpusError, match="01-payments-checkout is a holdout"):
        refuse_holdout("01-payments-checkout", holdout_corpus)
    refuse_holdout("02-iot-fleet-telemetry", holdout_corpus)


def test_a_case_without_the_field_fails_to_load(tmp_path):
    root = tmp_path / "case"
    shutil.copytree(CORPUS / "01-payments-checkout", root)
    path = root / "case.json"
    case = json.loads(path.read_text(encoding="utf-8"))
    del case["holdout"]
    path.write_text(json.dumps(case), encoding="utf-8")

    with pytest.raises(CorpusError, match="holdout"):
        load_case(root)


def _stride(case_id: str, holdout: bool, coverage: float) -> scorer.CaseScore:
    template = scorer.CaseScore(
        case_id=case_id,
        exemplar_proximity="far",
        holdout=holdout,
        produced_ids=(),
        produced_count=0,
        reference_count=4,
        must_find_total=2,
        matched=(),
        missed=(),
        lane_errors=(),
        unlisted=(),
        foreign=(),
        rulings=(),
    )
    return _with_coverage(template, coverage)


def _with_coverage(score, coverage):
    """A score whose coverage figures read ``coverage``, whatever built it."""

    class Fixed(type(score)):
        reference_coverage = coverage
        must_find_coverage = coverage

    return Fixed(**{f.name: getattr(score, f.name) for f in dataclasses.fields(score)})


def test_the_stride_split_reports_holdout_coverage_apart():
    scores = [
        _stride("a", False, 0.8),
        _stride("b", False, 0.6),
        _stride("c", True, 0.3),
    ]

    split = scorer.holdout_split(scores)

    assert split == {
        "tuned_coverage": 0.7,
        "holdout_coverage": 0.3,
        "tuned_must_find_coverage": 0.7,
        "holdout_must_find_coverage": 0.3,
    }
    assert scorer.holdout_split(scores[:2]) is None


def test_the_asvs_split_reports_holdout_recall_apart():
    class Score:
        def __init__(self, holdout, recall):
            self.holdout, self.recall = holdout, recall

    scores = [Score(False, 0.5), Score(False, 1.0), Score(True, 0.25)]

    assert applicability.holdout_split(scores) == {
        "tuned_recall": 0.75,
        "holdout_recall": 0.25,
    }
    assert applicability.holdout_split(scores[:2]) is None


def test_both_packages_carry_the_split_in_their_artifact():
    assert "holdout_split" in scorer.artifact([])
    assert "applicability_holdout_split" in applicability.artifact([])


def test_the_readers_path_is_repo_relative():
    assert all(
        Path(REPO_ROOT / site.split(":")[0]).is_file() for site in HOLDOUT_READERS
    )
