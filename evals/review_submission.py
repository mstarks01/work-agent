"""Canonical JSON contributions for completed corpus reviews.

New review contributions are one structured JSON file under
``evals/review/submissions``. The file carries the independent list, marks,
missed issues, notes, and digests of the case material the reviewer saw. CI
binds ``submitted_by`` to the pull-request author and validates the file against
the corpus before merge.

A submission rewrites nothing else: not case metadata, not generated
Markdown, not the voter roster. A later corpus edit does not rewrite old review
evidence either; it simply makes that review no longer current for the changed
case until somebody reviews the new bytes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote, urlencode

from evals.harness import envelope as envelopes
from evals.harness import sitting as sittings
from evals.harness import submit as submit_spine
from evals.harness.envelope import relative_path, serialize, submission_name
from evals.harness.reference import CorpusError

REPO_ROOT = Path(__file__).resolve().parents[1]
SUBMISSIONS_DIR = envelopes.SUBMISSIONS_DIR
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


def covers(root: Path, case_id: str, answers: envelopes.CaseAnswers) -> list[str]:
    """Which **Framework**s this sitting read. See
    :func:`~evals.harness.sitting.covered_frameworks`."""
    try:
        return sittings.covered_frameworks(
            root / "evals" / "corpus" / case_id, answers.opened_digests
        )
    except (CorpusError, sittings.SittingError, OSError, ValueError):
        return []


def _case_problems(
    root: Path, case_id: str, answers: envelopes.CaseAnswers
) -> list[str]:
    """This case's answers, against the one reader every surface asks."""
    try:
        return sittings.sitting_problems(
            root / "evals" / "corpus" / case_id,
            own_list=answers.own_list,
            opened_digests=answers.opened_digests,
            marks=answers.marks,
        )
    except (CorpusError, sittings.SittingError, OSError, ValueError) as exc:
        return [f"{case_id}: {exc}"]


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


def current_reviews(root: Path) -> dict[str, dict[str, MergedReview]]:
    """Which sitting currently covers each **Framework** of each case.

    Keyed ``case -> framework -> review``, because coverage is per framework
    and always was: ``tests/test_case_review.py`` says a case reviewed for one
    framework stays unread for the other. A case reads as read when every
    framework it declares has an entry here.

    **Fail-closed against a later edit.** A submission drops out the moment any
    file it read changes, so a corpus edit puts back exactly the frameworks
    whose evidence moved and leaves the rest standing.

    Later submissions win, which is how a second reader replaces a first.
    """
    covered: dict[str, dict[str, MergedReview]] = {}
    try:
        submissions = list(iter_submissions(root))
    except ReviewSubmissionError:
        return covered
    for path, envelope in submissions:
        for case_id, answers in envelope.cases.items():
            if _case_problems(root, case_id, answers):
                continue
            review = MergedReview(path, envelope, case_id, answers)
            for framework in covers(root, case_id, answers):
                covered.setdefault(case_id, {})[framework] = review
    return covered


def declared(root: Path, case_id: str) -> list[str]:
    """Every **Framework** this case declares, in its own order."""
    try:
        prepared = sittings.prepare(root / "evals" / "corpus" / case_id)
    except (CorpusError, sittings.SittingError, OSError, ValueError):
        return []
    return list(prepared.part_two_blocks)


def waiting(
    root: Path,
    case_id: str,
    covered: Mapping[str, Mapping[str, MergedReview]] | None = None,
) -> list[str]:
    """The frameworks of one case that no current sitting covers.

    ``covered`` is :func:`current_reviews` already read, for a caller that
    walks every case; a caller asking about one case leaves it out and the
    read happens here.
    """
    if covered is None:
        covered = current_reviews(root)
    have = covered.get(case_id, {})
    return [name for name in declared(root, case_id) if name not in have]


def current_for_case(root: Path, case_id: str) -> MergedReview | None:
    """The newest sitting covering any part of this case, for its document.

    A case can be covered by two readers, and the read-only view shows one
    document. It shows the newest, which is the one whose words answer the most
    recent state of the case.
    """
    covered = current_reviews(root).get(case_id, {})
    return max(covered.values(), key=lambda review: review.path.name, default=None)


def rail_signatures(root: Path) -> tuple[dict[str, str], dict[str, str]]:
    """What a rail says about every case a merged sitting touches.

    Two maps from one read of the submissions. The first names who cleared
    each **fully** covered case, for a rail that greys it; a case with a
    framework still waiting carries no signature, because work remains and a
    greyed row would put it out of a reader's reach. The second says what each
    partly covered case still waits for, spelled here rather than at the
    surface, so the rail, the printed count and a future reader agree about
    what "partly read" is.

    One :func:`current_reviews` serves both. A rail refresh used to read every
    merged submission once per case per map.
    """
    covered = current_reviews(root)
    signed: dict[str, str] = {}
    partial: dict[str, str] = {}
    for case_id, reviews in covered.items():
        left = waiting(root, case_id, covered)
        if left:
            read = ", ".join(sorted(reviews))
            partial[case_id] = f"{', '.join(left)} waiting; {read} read"
        else:
            newest = max(reviews.values(), key=lambda review: review.path.name)
            signed[case_id] = newest.signature
    return signed, partial


def unreviewed_cases(root: Path) -> list[str]:
    """Every corpus case no merged submission currently clears, in corpus order.

    **The one reader of "is this case read".** It is derived from the corpus
    and the merged submissions rather than from a list somebody maintains, so
    the app, the CI gate and the printed count cannot answer it differently.

    A case leaves this list when every **Framework** it declares is covered,
    and comes back the moment any file a covering sitting read changes —
    :func:`current_reviews` drops a review whose digests no longer match, so the
    return is fail-closed against a later corpus edit. A case that gains a
    framework comes back too, carrying only that framework's work.
    """
    covered = current_reviews(root)
    return [
        case.meta.id
        for case in sittings.load_corpus(root / "evals" / "corpus")
        if any(
            framework not in covered.get(case.meta.id, {})
            for framework in declared(root, case.meta.id)
        )
    ]


def contribution_url(envelope: envelopes.Envelope, slug: str) -> str:
    """A link that opens GitHub's editor with this submission already typed.

    **The way in for a reader with no clone.** They open the standalone sitting
    page, read a case, press this, and land on GitHub's new-file form holding
    the file and its name. **Propose changes** opens the pull request, and
    contribution CI validates it exactly as it validates one the app opened.
    Nothing is installed and no credential is held anywhere but GitHub.

    The name is the digest of the canonical bytes, so a reader who edits the
    prefilled content before proposing it lands a file whose name no longer
    matches — which :func:`verify_pull_request` refuses by name, in their own
    pull request, rather than merging words nobody read.

    Both values are escaped as query components, so a case id or a reader's own
    words cannot add a parameter of their own (OWASP A05). ``slug`` comes from
    the operator's own git remote through
    :func:`~evals.harness.submit.repo_slug`, never from a request.
    """
    query = urlencode(
        {
            "filename": relative_path(envelope),
            "value": serialize(envelope).decode("utf-8"),
        },
        quote_via=quote,
    )
    return f"https://github.com/{slug}/new/{submit_spine.BASE_BRANCH}?{query}"


def verify_pull_request(root: Path, author: str) -> list[str]:
    """Validate a review-only pull request against its GitHub author."""
    changed = submit_spine.changed_paths(root)
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
    if submit_spine.base_text(root, rel) is not None:
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
        submit_spine.run_command(["git", "fetch", "origin"], root)
        remote = submit_spine.push_remote(root, author)
        branch = submit_spine.branch_name(root, "review", author, remote)
        with TemporaryDirectory(prefix="review-submit-") as scratch:
            worktree = Path(scratch) / "worktree"
            submit_spine.run_command(
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
                submit_spine.run_command(["git", "checkout", "-b", branch], worktree)
                submit_spine.run_command(["git", "add", "--", rel], worktree)
                submit_spine.run_command(
                    ["git", "commit", "-m", _title(envelope)], worktree
                )
                submit_spine.run_command(
                    ["git", "push", remote, f"HEAD:refs/heads/{branch}"], worktree
                )
            finally:
                submit_spine.run_command(
                    ["git", "worktree", "remove", "--force", str(worktree)], root
                )
        return submit_spine.run_command(
            [
                "gh",
                "pr",
                "create",
                "--head",
                submit_spine.pr_head(remote, author, branch),
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
