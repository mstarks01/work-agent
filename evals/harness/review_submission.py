"""One contributed review is one JSON file.

The browser review keeps drafts local until a reviewer explicitly contributes.
At that point the structured envelope is the evidence: the independent list,
marks, missed findings, notes, and the digests of every corpus file the reviewer
saw.  A pull request therefore needs one file under
``evals/review/submissions/`` rather than rewritten case metadata, a generated
Markdown copy, the unreviewed test module, and a roster edit.

The file is intentionally useful without the web app.  CI validates it against
the pull-request author and the corpus bytes in that PR.  Once merged, readers
can derive whether it still clears a case by comparing the recorded digests with
the corpus as it exists now.  Old submissions remain historical evidence when a
case later changes; they simply stop clearing the changed case.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from evals.harness import envelope as envelopes
from evals.harness import roster as rosters
from evals.harness import sitting as sittings
from evals.harness import submit as submit_spine

REPO_ROOT = Path(__file__).resolve().parents[2]
SUBMISSIONS_DIR = Path("evals/review/submissions")
SUBMISSIONS_PREFIX = f"{SUBMISSIONS_DIR.as_posix()}/"


class ReviewSubmissionError(RuntimeError):
    """A contributed review cannot be read, validated, or submitted."""


@dataclass(frozen=True)
class MergedReview:
    """One case inside one merged review envelope."""

    path: Path
    envelope: envelopes.Envelope
    case_id: str
    answers: envelopes.CaseAnswers

    @property
    def signature(self) -> str:
        return sittings.naming(self.envelope.submitted_by, self.envelope.submitted_for)


def serialize(envelope: envelopes.Envelope) -> bytes:
    """The canonical bytes committed for one review."""
    return (envelope.model_dump_json(indent=2) + "\n").encode("utf-8")


def submission_name(envelope: envelopes.Envelope) -> str:
    """A stable, collision-resistant name derived from the review itself."""
    digest = hashlib.sha256(serialize(envelope)).hexdigest()[:12]
    return f"review-{envelope.generated}-{envelope.submitted_by}-{digest}.json"


def relative_path(envelope: envelopes.Envelope) -> str:
    return (SUBMISSIONS_DIR / submission_name(envelope)).as_posix()


def _case_problems(
    root: Path, case_id: str, answers: envelopes.CaseAnswers
) -> list[str]:
    corpus_dir = root / "evals" / "corpus"
    case_dir = corpus_dir / case_id
    problems: list[str] = []
    try:
        prepared = sittings.prepare(case_dir)
    except sittings.SittingError as exc:
        return [f"{case_id}: {exc}"]

    if not sittings.own_list_is_written(answers.own_list):
        problems.append(
            f"{case_id}: the independent list is shorter than "
            f"{sittings.MIN_OWN_LIST} characters"
        )

    expected = set(prepared.files)
    recorded = set(answers.opened_digests)
    if recorded != expected:
        missing = sorted(expected - recorded)
        extra = sorted(recorded - expected)
        if missing:
            problems.append(
                f"{case_id}: the review does not carry digests for {missing}"
            )
        if extra:
            problems.append(
                f"{case_id}: the review carries unexpected digests for {extra}"
            )

    stale = sittings.moved(
        case_dir,
        {name: answers.opened_digests.get(name, "") for name in prepared.files},
    )
    if stale:
        problems.append(
            f"{case_id}: {', '.join(stale)} changed since the reviewer opened the case"
        )

    known = {target.fingerprint for target in prepared.mark_targets}
    unknown = sorted(set(answers.marks) - known)
    if unknown:
        problems.append(
            f"{case_id}: {', '.join(unknown)} names no recorded finding in this case"
        )
    return problems


def validate(
    envelope: envelopes.Envelope, root: Path, *, author: str | None = None
) -> list[str]:
    """Everything that must hold when a review enters through a pull request."""
    problems: list[str] = []
    if author is not None and envelope.submitted_by != author:
        problems.append(
            f"the review is submitted by {envelope.submitted_by!r}, but the pull request "
            f"was opened by {author!r}"
        )
    if not envelope.cases:
        problems.append("the review contains no completed cases")
        return problems

    corpus_dir = root / "evals" / "corpus"
    try:
        offered = {case.meta.id for case in sittings.load_corpus(corpus_dir)}
    except Exception as exc:  # corpus loaders already provide the useful message
        return [f"the corpus cannot be read: {exc}"]

    unknown_cases = sorted(set(envelope.cases) - offered)
    if unknown_cases:
        problems.append(f"the review names unknown cases: {unknown_cases}")
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
    """Every merged review envelope, in stable filename order."""
    directory = root / SUBMISSIONS_DIR
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.json")):
        yield path, _read(path)


def repository_problems(root: Path) -> list[str]:
    """Structural problems in already-merged review files.

    Current corpus digests are deliberately not checked here: a later corpus
    edit makes an old review historical, not malformed.  Currentness is derived
    by :func:`current_reviews`.
    """
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
    """The newest merged review that still matches each case's current bytes."""
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
    return {case_id: review.signature for case_id, review in current_reviews(root).items()}


def unreviewed_cases(root: Path) -> list[str]:
    """Cases with neither a current legacy sitting nor a current review file."""
    corpus_dir = root / "evals" / "corpus"
    corpus = sittings.load_corpus(corpus_dir)
    central = current_reviews(root)
    try:
        roster = rosters.load(root / submit_spine.ROSTER_FILE)
    except rosters.RosterError:
        roster = rosters.Roster(version=1, voters={})
    return [
        case.meta.id
        for case in corpus
        if case.meta.id not in central
        and not any(
            sittings.clears(case, recorded, roster, corpus_dir)
            for recorded in case.meta.reviews
        )
    ]


def _changed_paths(root: Path) -> list[str]:
    """Reuse the contribution spine's one definition of the pull-request delta."""
    return submit_spine._changed_paths(root)  # noqa: SLF001 - same harness package


def verify_pull_request(root: Path, author: str) -> list[str]:
    """Validate a review-only pull request against the GitHub PR author."""
    changed = _changed_paths(root)
    under_prefix = [rel for rel in changed if rel.startswith(SUBMISSIONS_PREFIX)]
    if not under_prefix:
        return []

    problems: list[str] = []
    review_files = [rel for rel in under_prefix if rel.endswith(".json")]
    unexpected = sorted(set(under_prefix) - set(review_files))
    if unexpected:
        problems.append(f"review submissions only add JSON files: {unexpected}")
    if len(review_files) != 1:
        problems.append(
            f"one review pull request carries exactly one JSON file; found {len(review_files)}"
        )
    strays = sorted(set(changed) - set(review_files))
    if strays:
        problems.append(f"a review pull request changes nothing else: {strays}")
    if len(review_files) != 1:
        return problems

    rel = review_files[0]
    if submit_spine._base_text(root, rel) is not None:  # noqa: SLF001
        problems.append(f"{rel}: a contributed review is append-only; add a new file")
        return problems
    path = root / rel
    try:
        envelope = _read(path)
    except ReviewSubmissionError as exc:
        problems.append(str(exc))
        return problems
    if path.name != submission_name(envelope):
        problems.append(
            f"{rel}: expected canonical filename {submission_name(envelope)!r}"
        )
    problems.extend(validate(envelope, root, author=author))
    return problems


def _title(envelope: envelopes.Envelope) -> str:
    count = len(envelope.cases)
    noun = "case" if count == 1 else "cases"
    return f"Review: {envelope.submitted_by}, {count} {noun}"


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
    """Open a PR containing only the canonical review JSON.

    Git authentication, branch creation, and a fork when one is actually needed
    stay behind the existing contribution spine.  The reviewer-facing app never
    asks the user to understand those transport details.
    """
    problems = validate(envelope, root, author=envelope.submitted_by)
    if problems:
        raise ReviewSubmissionError("; ".join(problems))

    author = envelope.submitted_by
    rel = relative_path(envelope)
    data = serialize(envelope)
    try:
        submit_spine._run(["git", "fetch", "origin"], root)  # noqa: SLF001
        remote = submit_spine._push_remote(root, author)  # noqa: SLF001
        branch = submit_spine._branch_name(root, "review", author, remote)  # noqa: SLF001
        with TemporaryDirectory(prefix="review-submit-") as scratch:
            worktree = Path(scratch) / "worktree"
            submit_spine._run(  # noqa: SLF001
                ["git", "worktree", "add", "--detach", str(worktree), submit_spine.BASE_REF],
                root,
            )
            try:
                target = worktree / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                submit_spine._run(["git", "checkout", "-b", branch], worktree)  # noqa: SLF001
                submit_spine._run(["git", "add", "--", rel], worktree)  # noqa: SLF001
                submit_spine._run(["git", "commit", "-m", _title(envelope)], worktree)  # noqa: SLF001
                submit_spine._run(  # noqa: SLF001
                    ["git", "push", remote, f"HEAD:refs/heads/{branch}"], worktree
                )
            finally:
                submit_spine._run(  # noqa: SLF001
                    ["git", "worktree", "remove", "--force", str(worktree)], root
                )
        return submit_spine._run(  # noqa: SLF001
            [
                "gh",
                "pr",
                "create",
                "--head",
                submit_spine._pr_head(remote, author, branch),  # noqa: SLF001
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
    """CI entry point: validate a review submission in the current PR."""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--author", required=True, help="GitHub pull-request author")
    args = parser.parse_args()
    problems = verify_pull_request(REPO_ROOT, args.author.strip())
    if problems:
        for problem in problems:
            print(f"FAIL  {problem}")
        return 1
    print("review submission: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
