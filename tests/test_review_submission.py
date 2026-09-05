"""The canonical one-file human-review contribution."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from evals.harness import envelope as envelopes
from evals.harness import review_submission as reviews
from evals.harness import sitting as sittings

CASE = "03-batch-data-pipeline"
OWN = ["a malicious batch row"]


def tree_for(tmp_path: Path) -> Path:
    source_root = Path(__file__).resolve().parents[1]
    tree = tmp_path / "tree"
    shutil.copytree(
        source_root / "evals" / "corpus" / CASE,
        tree / "evals" / "corpus" / CASE,
    )
    (tree / "evals" / "review").mkdir(parents=True)
    (tree / "evals" / "review" / "voters.toml").write_text(
        'version = 1\n\n[voters.ada]\nstanding = "contributor"\n',
        encoding="utf-8",
    )
    return tree


def envelope_for(tree: Path, author: str = "ada") -> envelopes.Envelope:
    prepared = sittings.prepare(tree / "evals" / "corpus" / CASE)
    return envelopes.Envelope(
        envelope=envelopes.VERSION,
        submitted_by=author,
        submitted_for="anonymous",
        generated="2026-09-05",
        cases={
            CASE: envelopes.CaseAnswers(
                own_list=OWN,
                marks={},
                missing=["missing authorization context"],
                notes="human note",
                opened_digests=sittings.digests(
                    tree / "evals" / "corpus" / CASE, prepared.files
                ),
            )
        },
    )


def write_review(tree: Path, envelope: envelopes.Envelope) -> Path:
    path = tree / reviews.relative_path(envelope)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(reviews.serialize(envelope))
    return path


def test_name_is_stable_and_content_addressed(tmp_path: Path):
    tree = tree_for(tmp_path)
    envelope = envelope_for(tree)
    first = reviews.submission_name(envelope)
    second = reviews.submission_name(envelope)
    assert first == second
    assert first.startswith("review-2026-09-05-ada-")
    assert first.endswith(".json")
    changed = envelope.model_copy(
        update={
            "cases": {
                CASE: envelope.cases[CASE].model_copy(update={"notes": "different"})
            }
        }
    )
    assert reviews.submission_name(changed) != first


def test_validation_binds_the_pr_author(tmp_path: Path):
    tree = tree_for(tmp_path)
    envelope = envelope_for(tree)
    assert reviews.validate(envelope, tree, author="ada") == []
    problems = reviews.validate(envelope, tree, author="mallory")
    assert any("pull request was opened by 'mallory'" in problem for problem in problems)


def test_a_merged_review_clears_until_the_case_changes(tmp_path: Path):
    tree = tree_for(tmp_path)
    envelope = envelope_for(tree)
    write_review(tree, envelope)
    assert CASE in reviews.current_reviews(tree)
    assert CASE not in reviews.unreviewed_cases(tree)

    source = tree / "evals" / "corpus" / CASE / "source.md"
    source.write_text(source.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
    assert CASE not in reviews.current_reviews(tree)
    assert CASE in reviews.unreviewed_cases(tree)


def test_repository_check_does_not_call_old_review_stale_malformed(tmp_path: Path):
    tree = tree_for(tmp_path)
    envelope = envelope_for(tree)
    write_review(tree, envelope)
    source = tree / "evals" / "corpus" / CASE / "source.md"
    source.write_text(source.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
    assert reviews.repository_problems(tree) == []


def test_the_json_contains_the_human_evidence(tmp_path: Path):
    tree = tree_for(tmp_path)
    envelope = envelope_for(tree)
    body = json.loads(reviews.serialize(envelope))
    answers = body["cases"][CASE]
    assert answers["own_list"] == OWN
    assert answers["missing"] == ["missing authorization context"]
    assert answers["notes"] == "human note"
    assert set(answers["opened_digests"]) == {
        "source.md",
        "model.json",
        "claims/stride.json",
    }
