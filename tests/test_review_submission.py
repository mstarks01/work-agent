"""The canonical one-file human-review contribution."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from evals import review_submission as reviews
from evals.harness import envelope as envelopes
from evals.harness import sitting as sittings
from evals.harness.reference import load_case

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
                marks={target.fingerprint: "agree" for target in prepared.mark_targets},
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
    assert any(
        "pull request was opened by 'mallory'" in problem for problem in problems
    )


def test_a_merged_review_clears_until_the_case_changes(tmp_path: Path):
    tree = tree_for(tmp_path)
    envelope = envelope_for(tree)
    write_review(tree, envelope)
    assert CASE in reviews.current_reviews(tree)
    assert CASE not in reviews.unreviewed_cases(tree)

    source = tree / "evals" / "corpus" / CASE / "source.md"
    source.write_text(
        source.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8"
    )
    assert CASE not in reviews.current_reviews(tree)
    assert CASE in reviews.unreviewed_cases(tree)


def test_repository_check_does_not_call_old_review_stale_malformed(tmp_path: Path):
    tree = tree_for(tmp_path)
    envelope = envelope_for(tree)
    write_review(tree, envelope)
    source = tree / "evals" / "corpus" / CASE / "source.md"
    source.write_text(
        source.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8"
    )
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


def dated(
    envelope: envelopes.Envelope, generated: str, notes: str
) -> envelopes.Envelope:
    """The same sitting on another date, with a note that says which it is."""
    answers = envelope.cases[CASE].model_copy(update={"notes": notes})
    return envelope.model_copy(
        update={"generated": generated, "cases": {CASE: answers}}
    )


def test_a_later_dated_sitting_by_one_reader_replaces_the_first(tmp_path: Path):
    tree = tree_for(tmp_path)
    write_review(tree, dated(envelope_for(tree), "2026-09-05", "first"))
    write_review(tree, dated(envelope_for(tree), "2026-09-06", "second"))
    assert reviews.repository_problems(tree) == []
    current = reviews.current_for_case(tree, CASE)
    assert current is not None and current.answers.notes == "second"
    assert reviews.unreviewed_cases(tree) == []


def test_two_sittings_by_one_reader_on_one_date_are_refused(tmp_path: Path):
    """Nothing in the files says which one the reader wrote last.

    The name is a digest, so on one date two files sort by a random hex
    string. The pull request that would make the pair is refused, and a pair
    that reaches the tree covers nothing rather than whichever sorts last.
    """
    tree = tree_for(tmp_path)
    first = dated(envelope_for(tree), "2026-09-05", "first")
    second = dated(envelope_for(tree), "2026-09-05", "second")
    first_path = write_review(tree, first)
    refused = reviews.validate(second, tree, author="ada")
    assert any("nothing says which is later" in problem for problem in refused)
    # The merged file is this envelope, and a file is not a tie with itself.
    assert reviews.validate(first, tree, author="ada") == []

    second_path = write_review(tree, second)
    problems = reviews.repository_problems(tree)
    assert len(problems) == 1
    assert first_path.name in problems[0] and second_path.name in problems[0]
    assert reviews.current_reviews(tree) == {}
    assert reviews.unreviewed_cases(tree) == [CASE]


def test_two_readers_on_one_date_both_cover(tmp_path: Path):
    tree = tree_for(tmp_path)
    write_review(tree, envelope_for(tree, "ada"))
    write_review(tree, envelope_for(tree, "bob"))
    assert reviews.repository_problems(tree) == []
    assert set(reviews.current_reviews(tree)[CASE]) == set(reviews.declared(tree, CASE))
    assert reviews.unreviewed_cases(tree) == []


def test_a_merged_sitting_re_keys_to_the_current_rule_without_a_re_read(tmp_path):
    """A mark stores only its key, so a version that reads more would orphan
    every merged sitting: the case reads as unread and the reader's answers
    are lost. The finding a mark names is a reference claim in the corpus,
    so the key recomputes from the case, as a vote re-keys from its
    components. The file is written again under its new digest name."""
    from evals.harness.fingerprint import (
        components_for,
        fingerprint,
        version_for,
    )

    tree = tree_for(tmp_path)
    corpus = tree / "evals" / "corpus"
    envelope = envelope_for(tree)
    case = load_case(corpus / CASE)
    flows = {flow.id: (flow.source, flow.destination) for flow in case.model.data_flows}
    old_version = version_for("stride") - 2
    old_keys = {}
    for target in envelope.cases[CASE].marks:
        for claim in case.claims_for("stride"):
            full = components_for(
                "stride",
                claim.lane,
                claim.affected_element_ids,
                flows,
                verb=claim.verb,
                scope=case.id,
            )
            if fingerprint(full, version=version_for("stride")) == target:
                old_keys[fingerprint(full, version=old_version)] = envelope.cases[
                    CASE
                ].marks[target]
    assert old_keys, "the case carries STRIDE targets"
    stale = envelope.model_copy(
        update={
            "cases": {CASE: envelope.cases[CASE].model_copy(update={"marks": old_keys})}
        }
    )
    old_path = write_review(tree, stale)
    assert reviews.current_for_case(tree, CASE) is None, "old keys cover nothing"

    moves = reviews.rekey_submissions(tree, corpus)

    assert moves == [(old_path.name, reviews.relative_path(envelope).split("/")[-1])]
    assert not old_path.exists()
    covering = reviews.current_for_case(tree, CASE)
    assert covering is not None
    assert set(covering.answers.marks) == set(envelope.cases[CASE].marks)
    assert reviews.rekey_submissions(tree, corpus) == [], "a second pass moves nothing"
