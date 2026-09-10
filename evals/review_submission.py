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
from typing import Literal
from urllib.parse import quote, urlencode

from evals.harness import envelope as envelopes
from evals.harness import sitting as sittings
from evals.harness import submit as submit_spine
from evals.harness.envelope import relative_path, serialize, submission_name
from evals.harness.fingerprint import (
    SUPPORTED_VERSIONS,
    FingerprintError,
    components_for,
    fingerprint,
    version_for,
)
from evals.harness.reference import CorpusError, load_case

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

    @property
    def generated(self) -> str:
        return self.envelope.generated

    @property
    def order(self) -> tuple[str, str]:
        """What "later" means for a merged sitting, spelled once.

        The ``generated`` date is the only date a file carries, so it decides.
        The name breaks a tie between two signatures on one date, and both of
        those stay live; a tie inside one signature is refused by
        :func:`_live`, because nothing in the files says which the reader
        wrote last.
        """
        return (self.generated, self.path.name)


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
    return [*problems, *_ties_with(root, envelope)]


def _ties_with(root: Path, envelope: envelopes.Envelope) -> list[str]:
    """The ties this envelope would make against what is already merged.

    Read before the file is in the tree, so the app and CI refuse the same
    thing. A merged file with this envelope's own name is this envelope, and
    is not a tie with itself.
    """
    name = submission_name(envelope)
    try:
        merged = [
            (path, held) for path, held in iter_submissions(root) if path.name != name
        ]
    except ReviewSubmissionError:
        return []
    _, ties = _live(_reviews([*merged, (root / relative_path(envelope), envelope)]))
    return ties


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
    _, ties = _live(_reviews(submissions))
    return [*problems, *ties]


def _live(reviews: list[MergedReview]) -> tuple[list[MergedReview], list[str]]:
    """The live sitting per case and signature, and every tie nothing breaks.

    A reader who sits one case twice replaces their earlier sitting, and
    "later" is the ``generated`` date. Two sittings by one signature of one
    case on one date carry nothing that says which the reader wrote last, so
    neither is live and both are named. A merged file is never removed, so
    the reader's remedy is a sitting dated later, and a maintainer's is to
    drop one of the two.
    """
    grouped: dict[tuple[str, str], list[MergedReview]] = {}
    for review in reviews:
        grouped.setdefault((review.case_id, review.signature), []).append(review)
    live: list[MergedReview] = []
    ties: list[str] = []
    for (case_id, signature), held in grouped.items():
        newest = max(review.generated for review in held)
        tied = sorted(review.path.name for review in held if review.generated == newest)
        if len(tied) == 1:
            live.append(max(held, key=lambda review: review.order))
            continue
        ties.append(
            f"{case_id}: {' and '.join(tied)} are both {signature}'s sitting dated"
            f" {newest}, and nothing says which is later; keep one"
        )
    return live, ties


def _reviews(submissions) -> list[MergedReview]:
    return [
        MergedReview(path, envelope, case_id, answers)
        for path, envelope in submissions
        for case_id, answers in envelope.cases.items()
    ]


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
    :func:`_live` says what later is, and drops the pair it cannot order.
    """
    covered: dict[str, dict[str, MergedReview]] = {}
    try:
        submissions = list(iter_submissions(root))
    except ReviewSubmissionError:
        return covered
    live, _ = _live(_reviews(submissions))
    for review in sorted(live, key=lambda review: review.order):
        if _case_problems(root, review.case_id, review.answers):
            continue
        for framework in covers(root, review.case_id, review.answers):
            covered.setdefault(review.case_id, {})[framework] = review
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
    return max(covered.values(), key=lambda review: review.order, default=None)


def latest_for_case(root: Path, case_id: str) -> MergedReview | None:
    """The newest live sitting that names this case, whether or not it still stands.

    A different question from :func:`current_for_case`. That one answers what
    covers the case now, and drops a sitting the moment a file it read moves,
    which is the fail-closed answer a gate needs. A reader who comes back after
    that edit needs the other answer: what did the last sitting say? Its marks
    are keyed by fingerprint, so every one that still names a finding is still
    an answer, and the draft the surface seeds from it pins the files as they
    are now. Without this reader, one added claim cost a whole case's read.
    """
    try:
        submissions = list(iter_submissions(root))
    except ReviewSubmissionError:
        return None
    live, _ = _live(_reviews(submissions))
    named = [review for review in live if review.case_id == case_id]
    return max(named, key=lambda review: review.order, default=None)


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
            newest = max(reviews.values(), key=lambda review: review.order)
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


#: The longest editor link this repository hands a reader. GitHub refuses a
#: request URL past its own limit with "Whoa there! Your request URL is too
#: long", and does not publish the number; a case 01 sitting at 5,073
#: characters opened, and a case 02 sitting was refused on 2026-09-09. The
#: widely reported bound is 8,192, and this sits under it. A sitting that will
#: not fit takes the upload route instead, which carries nothing in the URL.
EDITOR_URL_LIMIT = 8000


@dataclass(frozen=True)
class ContributionRoute:
    """How a reader with no clone lands their one file on GitHub.

    ``editor`` opens GitHub's new-file form with the file already typed.
    ``upload`` opens GitHub's upload page at the submissions directory, for a
    file the page has already downloaded; the reader drops it in. Both end in
    the same two buttons and the same contribution CI.
    """

    kind: Literal["editor", "upload"]
    url: str


def upload_url(slug: str) -> str:
    """GitHub's upload page at the submissions directory. Nothing rides in the URL."""
    return (
        f"https://github.com/{slug}/upload/{submit_spine.BASE_BRANCH}/"
        f"{envelopes.SUBMISSIONS_DIR.as_posix()}"
    )


def contribution_route(envelope: envelopes.Envelope, slug: str) -> ContributionRoute:
    """The editor link when it fits GitHub's limit, and the upload page when it does not.

    One reader for the choice. The offline page makes the same choice in its
    own script from the same limit, and ``tests/test_offline_sitting.py``
    drives that script against this function.
    """
    editor = contribution_url(envelope, slug)
    if len(editor) <= EDITOR_URL_LIMIT:
        return ContributionRoute("editor", editor)
    return ContributionRoute("upload", upload_url(slug))


def contribution_url(envelope: envelopes.Envelope, slug: str) -> str:
    """A link that opens GitHub's editor with this submission already typed.

    **The way in for a reader with no clone.** They open the standalone sitting
    page, read a case, press this, and land on GitHub's new-file form holding
    the file and its name. An account without write access presses **Propose
    changes**, and GitHub opens the pull request from a fork. An account with
    write access presses **Commit changes** and chooses a new branch there,
    because that dialog's default commits straight to main. Contribution CI
    validates the pull request exactly as it validates one the app opened.
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


def _current_keys(case_dir: Path) -> dict[str, str]:
    """Every key one case's reference claims ever carried, against the key they carry now.

    A mark is keyed by a finding's fingerprint, and a fingerprint is a pure
    function of the reference claim under one version. So the map from an old
    key to the current one is computed from the corpus alone: each claim is
    keyed under every version this build computes, and every one of those
    spellings points at the version :data:`~evals.harness.fingerprint.VERSION_FOR`
    names today. A version a claim cannot satisfy — one that reads a catalog
    identifier of a claim that composes its identity — is skipped, because no
    mark was ever keyed under it.
    """
    case = load_case(case_dir)
    flows = {flow.id: (flow.source, flow.destination) for flow in case.model.data_flows}
    keys: dict[str, str] = {}
    for framework in case.frameworks:
        for claim in case.claims_for(framework):
            full = components_for(
                framework,
                claim.lane,
                claim.affected_element_ids,
                flows,
                verb=claim.verb,
                identifier=claim.identifier,
                scope=case.id,
            )
            now = fingerprint(full, version=version_for(framework))
            for version in SUPPORTED_VERSIONS:
                try:
                    keys[fingerprint(full, version=version)] = now
                except FingerprintError:
                    continue
    return keys


def rekey_submissions(root: Path, corpus_dir: Path) -> list[tuple[str, str]]:
    """Move every merged sitting's marks to the keys the current rule computes.

    The sitting's counterpart of :func:`evals.harness.ledger.rekey`. A vote
    stores its components and re-keys from them; a mark stores only the key,
    but the finding it names is a reference claim in the corpus, so the key
    recomputes from the case (:func:`_current_keys`). No re-read, no provider.

    A file whose marks move is written again under the name its new bytes
    take, and the old file is removed, because a submission is named by its
    own digest. Two marks that the new rule folds into one finding must agree,
    or the file is refused with both claims named: that is a reading question,
    and a re-key answers none. Returns the ``(old name, new name)`` pairs.
    """
    moves: list[tuple[str, str]] = []
    for path, envelope in list(iter_submissions(root)):
        cases = {}
        changed = False
        for case_id, answers in envelope.cases.items():
            keys = _current_keys(corpus_dir / case_id)
            marks: dict[str, str] = {}
            for old, mark in answers.marks.items():
                new = keys.get(old, old)
                if new in marks and marks[new] != mark:
                    raise ReviewSubmissionError(
                        f"{path.name}: {case_id} marks two findings the current"
                        f" rule calls one, and the marks disagree ({marks[new]!r}"
                        f" against {mark!r} at {new}); a person settles that"
                    )
                marks[new] = mark
                changed = changed or new != old
            cases[case_id] = answers.model_copy(update={"marks": marks})
        if not changed:
            continue
        moved = envelope.model_copy(update={"cases": cases})
        target = root / relative_path(moved)
        target.write_bytes(serialize(moved))
        if target != path:
            path.unlink()
        moves.append((path.name, target.name))
    return moves
