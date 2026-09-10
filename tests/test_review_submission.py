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


def test_a_sitting_keyed_under_an_older_version_still_covers_its_case(tmp_path):
    """A mark stores only its key, so a version that reads more would orphan
    every merged sitting: the case reads as unread and a person's answers are
    lost. The finding a mark names is a reference claim in the corpus, so the
    key recomputes from the case, and the reader maps an older key to the
    current one at read time. No record moves."""
    from evals.harness.fingerprint import components_for, fingerprint, version_for

    tree = tree_for(tmp_path)
    corpus = tree / "evals" / "corpus"
    envelope = envelope_for(tree)
    case = load_case(corpus / CASE)
    flows = {flow.id: (flow.source, flow.destination) for flow in case.model.data_flows}
    current = version_for("stride")
    older = {}
    for claim in case.claims_for("stride"):
        full = components_for(
            "stride",
            claim.lane,
            claim.affected_element_ids,
            flows,
            verb=claim.verb,
            scope=case.id,
        )
        now = fingerprint(full, version=current)
        if now in envelope.cases[CASE].marks:
            older[fingerprint(full, version=current - 2)] = envelope.cases[CASE].marks[
                now
            ]
    assert older, "the case carries STRIDE targets"
    stale = envelope.model_copy(
        update={
            "cases": {CASE: envelope.cases[CASE].model_copy(update={"marks": older})}
        }
    )
    write_review(tree, stale)

    covering = reviews.current_for_case(tree, CASE)

    assert covering is not None, "an older key still names its finding"
    prepared = sittings.prepare(corpus / CASE)
    read = sittings.current_marks(prepared, covering.answers.marks)
    assert set(read) == set(envelope.cases[CASE].marks)


def test_two_marks_the_current_rule_folds_into_one_finding_must_agree(tmp_path):
    from evals.harness.sitting import MarkTarget, SittingError

    tree = tree_for(tmp_path)
    prepared = sittings.prepare(tree / "evals" / "corpus" / CASE)
    first = prepared.mark_targets[0]
    from dataclasses import replace

    aliased = replace(
        prepared,
        mark_targets=(
            MarkTarget(
                fingerprint=first.fingerprint,
                framework=first.framework,
                claims=first.claims,
                aliases=("v0:old",),
            ),
            *prepared.mark_targets[1:],
        ),
    )

    assert sittings.current_marks(aliased, {"v0:old": "agree"}) == {
        first.fingerprint: "agree"
    }
    with __import__("pytest").raises(SittingError, match="disagree"):
        sittings.current_marks(
            aliased, {"v0:old": "agree", first.fingerprint: "reject"}
        )
