"""Canonical JSON contributions for completed corpus reviews.

New review contributions are one structured JSON file under
``evals/review/submissions``. The file carries the independent list, marks,
missed issues, notes, and digests of the case material the reviewer saw. CI
binds ``submitted_by`` to the pull-request author and validates the file against
the corpus before merge.

Existing case-local sittings remain readable for compatibility. New JSON
submissions do not rewrite case metadata, generated Markdown, the bootstrap
unreviewed list, or the voter roster. A later corpus edit does not rewrite old
review evidence; it simply makes that review no longer current for the changed
case until somebody reviews the new bytes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from evals.harness import envelope as envelopes
from evals.harness import sitting as sittings
from evals.harness import submit as submit_spine
from evals.harness.reference import CorpusError

REPO_ROOT = Path(__file__).resolve().parents[1]
SUBMISSIONS_DIR = Path("evals/review/submissions")
SUBMISSIONS_PREFIX = f"{SUBMISSIONS_DIR.as_posix()}/"


class ReviewSubmissionError(RuntimeError):
    """A contributed review cannot be read, validated, or submitted."""


@dataclass(frozen=True)
class MergedReview:
    path: Path
    envelope: envelopes.Envelope
    case_id: str
    answers: envelopes.CaseAnswers

    @property
    def signature(self) -> str:
        return sittings.naming(self.envelope.submitted_by, self.envelope.submitted_for)


def serialize(envelope: envelopes.Envelope) -> bytes:
    """Canonical bytes for the committed review file."""
    return (envelope.model_dump_json(indent=2) + "\n").encode("utf-8")


def submission_name(envelope: envelopes.Envelope) -> str:
    digest = hashlib.sha256(serialize(envelope)).hexdigest()[:12]
    return f"review-{envelope.generated}-{envelope.submitted_by}-{digest}.json"


def relative_path(envelope: envelopes.Envelope) -> str:
    return (SUBMISSIONS_DIR / submission_name(envelope)).as_posix()


def _case_problems(
    root: Path, case_id: str, answers: envelopes.CaseAnswers
) -> list[str]:
    case_dir = root / "evals" / "corpus" / case_id
    try:
        prepared = sittings.prepare(case_dir)
    except (CorpusError, sittings.SittingError, OSError, ValueError) as exc:
        return [f"{case_id}: {exc}"]

    problems: list[str] = []
    if not sittings.own_list_is_written(answers.own_list):
        problems.append(
            f"{case_id}: the independent list is shorter than "
            f"{sittings.MIN_OWN_LIST} characters"
        )

    expected = set(prepared.files)
    recorded = set(answers.opened_digests)
    if missing := sorted(expected - recorded):
        problems.append(f"{case_id}: the review carries no digest for {missing}")
    if extra := sorted(recorded - expected):
        problems.append(f"{case_id}: the review carries unexpected digests for {extra}")

    stale = sittings.moved(
        case_dir,
        {name: answers.opened_digests.get(name, "") for name in prepared.files},
    )
    if stale:
        problems.append(
            f"{case_id}: {', '.join(stale)} changed since the reviewer opened the case"
        )

    known = {target.fingerprint for target in prepared.mark_targets}
    if unknown := sorted(set(answers.marks) - known):
        problems.append(
            f"{case_id}: {', '.join(unknown)} names no recorded finding in this case"
        )
    return problems


def validate(
    envelope: envelopes.Envelope, root: Path, *, author: str | None = None
) -> list[str]:
    """Validate a contribution against the corpus and, in CI, the PR author."""
    problems: list[str] = []
    if author is not None and envelope.submitted_by != author:
        problems.append(
            f"the review is submitted by {envelope.submitted_by!r}, but the pull "
            f"request was opened by {author!r}"
        )
    if not envelope.cases:
        return [*problems, "the review contains no completed cases"]

    try:
        offered = {
            case.meta.id for case in sittings.load_corpus(root / "evals" / "corpus")
        }
    except (CorpusError, sittings.SittingError, OSError, ValueError) as exc:
        return [*problems, f"the corpus cannot be read: {exc}"]

    if unknown := sorted(set(envelope.cases) - offered):
        problems.append(f"the review names unknown cases: {unknown}")
    for case_id, answers in envelope.cases.items():
        if case_id in offered:
            problems.extend(_case_problems(root, case_id, answers))
    return problems


def _read(path: Path) -> envelopes.Envelope:
    try:
        return envelopes.read(path)
    except envelopes.EnvelopeError as exc:
        raise ReviewSubmissionError(str(exc)) from exc


def iter_submissions(root: Path):
    """Yield every merged review file in stable filename order."""
    directory = root / SUBMISSIONS_DIR
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.json")):
        yield path, _read(path)


def repository_problems(root: Path) -> list[str]:
    """Structural errors in merged files, independent of later corpus drift."""
    problems: list[str] = []
    try:
        submissions = list(iter_submissions(root))
    except ReviewSubmissionError as exc:
        return [str(exc)]
    for path, envelope in submissions:
        if path.name != submission_name(envelope):
            problems.append(
                f"{path.relative_to(root)}: expected filename {submission_name(envelope)!r}"
            )
        if not envelope.cases:
            problems.append(f"{path.relative_to(root)}: contains no completed cases")
    return problems


def current_reviews(root: Path) -> dict[str, MergedReview]:
    """Newest merged review that still matches each case's current bytes."""
    current: dict[str, MergedReview] = {}
    try:
        submissions = list(iter_submissions(root))
    except ReviewSubmissionError:
        return current
    for path, envelope in submissions:
        for case_id, answers in envelope.cases.items():
            if not _case_problems(root, case_id, answers):
                current[case_id] = MergedReview(path, envelope, case_id, answers)
    return current


def current_for_case(root: Path, case_id: str) -> MergedReview | None:
    return current_reviews(root).get(case_id)


def clearing_signatures(root: Path) -> dict[str, str]:
    return {case: review.signature for case, review in current_reviews(root).items()}


def unreviewed_cases(root: Path) -> list[str]:
    """Cases needing review, preserving the canonical list when it is present."""
    current = current_reviews(root)
    if (root / sittings.UNREVIEWED_FILE).is_file():
        candidates = sittings.unreviewed_cases(root)
    else:
        candidates = [
            case.meta.id for case in sittings.load_corpus(root / "evals" / "corpus")
        ]
    return [case_id for case_id in candidates if case_id not in current]


def verify_pull_request(root: Path, author: str) -> list[str]:
    """Validate a review-only pull request against its GitHub author."""
    changed = submit_spine._changed_paths(root)
    review_files = [
        rel
        for rel in changed
        if rel.startswith(SUBMISSIONS_PREFIX) and rel.endswith(".json")
    ]
    if not review_files:
        return []

    under_prefix = [rel for rel in changed if rel.startswith(SUBMISSIONS_PREFIX)]
    problems: list[str] = []
    if unexpected := sorted(set(under_prefix) - set(review_files)):
        problems.append(f"review submissions only add JSON files: {unexpected}")
    if len(review_files) != 1:
        problems.append(
            f"one review pull request carries exactly one JSON file; found {len(review_files)}"
        )
    if strays := sorted(set(changed) - set(review_files)):
        problems.append(f"a review pull request changes nothing else: {strays}")
    if len(review_files) != 1:
        return problems

    rel = review_files[0]
    if submit_spine._base_text(root, rel) is not None:
        return [
            *problems,
            f"{rel}: a contributed review is append-only; add a new file",
        ]
    path = root / rel
    try:
        envelope = _read(path)
    except ReviewSubmissionError as exc:
        return [*problems, str(exc)]
    if path.name != submission_name(envelope):
        problems.append(f"{rel}: expected filename {submission_name(envelope)!r}")
    problems.extend(validate(envelope, root, author=author))
    return problems


def _title(envelope: envelopes.Envelope) -> str:
    count = len(envelope.cases)
    return (
        f"Review: {envelope.submitted_by}, {count} {'case' if count == 1 else 'cases'}"
    )


def _body(envelope: envelopes.Envelope) -> str:
    cases = "\n".join(f"- {case_id}" for case_id in envelope.cases)
    return (
        f"{cases}\n\n"
        "This pull request contributes one structured human review. The JSON "
        "contains the independent list, finding marks, missed issues, notes, "
        "and digests of the corpus material the reviewer saw. Contribution CI "
        "binds `submitted_by` to this pull request's author and validates the "
        "review against the corpus before merge."
    )


def open_pull_request(root: Path, envelope: envelopes.Envelope) -> str:
    """Open a PR containing only the canonical JSON review file."""
    if problems := validate(envelope, root, author=envelope.submitted_by):
        raise ReviewSubmissionError("; ".join(problems))

    author = envelope.submitted_by
    rel = relative_path(envelope)
    try:
        submit_spine._run(["git", "fetch", "origin"], root)
        remote = submit_spine._push_remote(root, author)
        branch = submit_spine._branch_name(root, "review", author, remote)
        with TemporaryDirectory(prefix="review-submit-") as scratch:
            worktree = Path(scratch) / "worktree"
            submit_spine._run(
                [
                    "git",
                    "worktree",
                    "add",
                    "--detach",
                    str(worktree),
                    submit_spine.BASE_REF,
                ],
                root,
            )
            try:
                target = worktree / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(serialize(envelope))
                submit_spine._run(["git", "checkout", "-b", branch], worktree)
                submit_spine._run(["git", "add", "--", rel], worktree)
                submit_spine._run(["git", "commit", "-m", _title(envelope)], worktree)
                submit_spine._run(
                    ["git", "push", remote, f"HEAD:refs/heads/{branch}"], worktree
                )
            finally:
                submit_spine._run(
                    ["git", "worktree", "remove", "--force", str(worktree)], root
                )
        return submit_spine._run(
            [
                "gh",
                "pr",
                "create",
                "--head",
                submit_spine._pr_head(remote, author, branch),
                "--title",
                _title(envelope),
                "--body",
                _body(envelope),
            ],
            root,
        ).strip()
    except submit_spine.SubmitError as exc:
        raise ReviewSubmissionError(str(exc)) from exc


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--author", required=True, help="GitHub pull-request author")
    args = parser.parse_args()
    if problems := verify_pull_request(REPO_ROOT, args.author.strip()):
        for problem in problems:
            print(f"FAIL  {problem}")
        return 1
    print("review submission: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
