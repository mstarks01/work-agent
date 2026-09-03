"""One command opens every contribution PR: ``submit <kind>``.

The spine is #325's, and it is the same four steps for every kind. Bind the
authenticated ``gh`` login. Run this kind's CI checks locally as a checklist.
Package the kind's allowlist on a fresh branch cut from ``origin/main``. Open
the pull request through ``gh``. ``--dry-run`` stops after the checklist, which
is the contributor's local CI, so a red pull request should be rare: the same
checks already ran on their machine.

Which kinds exist is a table, :data:`KINDS`, and never a branch. The baseline
kind arrives by adding an entry (#337), and the CLI offers exactly the table's
keys. A kind that is not in the table is not a choice, rather than a stub that
refuses.

The binding is strict, and there is no proxy (#320). The vote kind refuses to
open a pull request whose ledger delta names any voter other than the
authenticated login, and it refuses a roster edit that raises the author's own
standing. CI re-checks both. Running them here fails them early, at the machine
where the fix is.

On security: everything this module runs is an argument list with
``shell=False`` (A05), and every path it stages comes from the kind's allowlist
rather than from the command line (A01). The packaging happens in a throwaway
git worktree cut from ``origin/main``, so nothing the contributor's checkout
carries beyond the allowlist can ride into the pull request.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tomllib
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from pydantic import ValidationError

from evals.build_review_docs import GENERATED_DOCUMENT
from evals.harness import baseline, comparison, ledger, roster
from evals.harness.artifact import ProvenanceError
from evals.harness.reference import CaseSitting
from evals.harness.sitting import (
    MIN_OWN_LIST,
    SittingError,
    claim_files,
    document_name,
    required_files,
    unreviewed_cases,
    without_unreviewed,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The fixed point every submission diffs against and branches from. The PR
#: targets the default branch, so the delta that matters is the delta to it.
BASE_REF = "origin/main"

#: How much a standing may claim for its own author: nothing above what the
#: base ref already grants. Promotion exists — as a maintainer's edit (#326).
_RANK = {"contributor": 0, "maintainer": 1}

#: The roster, which every kind may carry so a first-timer registers in the
#: same PR as their first submission.
ROSTER_FILE = "evals/review/voters.toml"


class SubmitError(RuntimeError):
    """The submission cannot proceed; the message says what stops it."""


@dataclass(frozen=True)
class Check:
    """One checklist line: a name, and the problems that fail it."""

    name: str
    problems: tuple[str, ...] = ()
    #: Lines printed beside the result that do not fail it. For a fact the
    #: reviewer -- not the checklist -- is the one who can judge.
    notes: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.problems


@dataclass(frozen=True)
class Kind:
    """One submission kind: what it proves, stages, and says on the PR.

    ``prepare`` is the one pre-checklist step a kind may own — the baseline
    kind assembles its directory from ``--artifact`` paths there, so the
    checks then read an ordinary tree. It is a convenience, never a trust
    point: everything it writes recomputes in the checks and again in CI.
    """

    preflight: Callable[[Path, str], list[Check]]
    allowlist: Callable[[Path, str], list[str]]
    title: Callable[[Path, str], str]
    closing: Callable[[Path, str], str]
    #: The path prefix that identifies this kind in a diff. What
    #: :func:`detect_kind` reads, so CI recognises a submission by the same
    #: table the CLI offers rather than by a second list somebody maintains —
    #: and what the shared scope checks read, so the prefix is spelled once
    #: for the whole module.
    prefix: str = ""
    #: File names under this kind's tree that a generator writes from the
    #: material beside them. A submission never carries one: regenerating a
    #: derived file claims nothing, so it names no author and appends no
    #: entry. Declared beside the prefix rather than listed elsewhere, so a
    #: kind that grows a generated file answers for it here.
    derived: frozenset[str] = frozenset()
    #: What one submission of this kind is called, in a checklist sentence.
    #: Beside the prefix because the checks that read one read the other.
    noun: str = ""
    #: What one directory under :attr:`prefix` holds. Separate from ``noun``
    #: because the two answer different questions: a stray file is not part of
    #: a *baseline submission*, and the directory it strayed from holds a
    #: *Baseline*. One word doing both jobs reads wrong at one of them.
    subject: str = ""
    #: How many :attr:`subject` directories one submission of this kind
    #: holds — a key of :data:`_SUBJECTS`. The sitting kind carries a
    #: reader's whole session, so it says ``"many"`` (ADR 0020); every other
    #: kind keeps the default. A field rather than a check that reads the
    #: kind's name, so a fourth kind still arrives as a table row.
    subjects: str = "one"
    prepare: Callable[[Path, str, argparse.Namespace], None] | None = None


def _run(args: Sequence[str], cwd: Path) -> str:
    """One subprocess, list-form and captured; the error names the command."""
    try:
        done = subprocess.run(
            list(args), cwd=cwd, capture_output=True, text=True, check=True
        )
    except FileNotFoundError as exc:
        raise SubmitError(f"{args[0]}: not installed: {exc}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise SubmitError(f"{' '.join(args)}: {detail}") from exc
    return done.stdout


def gh_login(root: Path) -> str:
    """The authenticated ``gh`` account — the one name a submission may use."""
    return _run(["gh", "api", "user", "--jq", ".login"], root).strip()


#: The delta, held for the length of one checklist pass. A key present means
#: a pass is open for that root; its value is the delta once something has
#: read it. Empty outside a pass, so no read here can answer from a tree that
#: has since moved.
_DELTA: dict[Path, list[str] | None] = {}


@contextmanager
def _delta_cache(root: Path) -> Iterator[None]:
    """Read the working-tree delta once for one pass of the checks.

    A checklist runs six or seven checks and most of them read the delta, so
    an uncached pass spends a dozen ``git`` subprocesses answering the same
    question. The cache is **scoped rather than global** on purpose: this
    module writes to the tree before the checks run — ``_register`` adds a
    roster line and the baseline kind assembles a whole directory — and a
    memo that outlived those writes would let a check pass against a tree
    that no longer exists. Entering after the writes and leaving before the
    branch is packaged is what makes that unspellable.
    """
    _DELTA[root] = None
    try:
        yield
    finally:
        _DELTA.pop(root, None)


def _changed_paths(root: Path) -> list[str]:
    """Everything different from BASE_REF, tracked or not, repo-relative.

    The caller fetched once already; fetching here would make every check
    that reads the delta pay a network round trip.
    """
    cached = _DELTA.get(root)
    if cached is not None:
        return cached
    tracked = _run(["git", "diff", "--name-only", BASE_REF], root).splitlines()
    porcelain = _run(
        ["git", "status", "--porcelain", "--untracked-files=all"], root
    ).splitlines()
    untracked = [line[3:] for line in porcelain if line.startswith("?? ")]
    delta = sorted(rel for rel in set(tracked) | set(untracked) if not _is_derived(rel))
    if root in _DELTA:
        _DELTA[root] = delta
    return delta


def _is_derived(rel: str) -> bool:
    """Whether a generator writes this path from the material beside it.

    Read off :data:`KINDS` rather than a list here, so a kind that grows a
    generated file declares it beside its own prefix.

    Derived files leave the delta before any check reads it, which is what
    makes a regeneration an ordinary code change: it selects no kind, so it
    names no submitter and appends no entry. Before this, refreshing the
    reading documents was classified as twelve sittings by twelve people who
    had not read anything, and there was no diff that could pass.
    """
    name = rel.rsplit("/", 1)[-1]
    return any(
        rel.startswith(kind.prefix) and name in kind.derived for kind in KINDS.values()
    )


def _base_text(root: Path, rel: str) -> str | None:
    """The file's content at BASE_REF, or None where the base has no file."""
    try:
        return _run(["git", "show", f"{BASE_REF}:{rel}"], root)
    except SubmitError:
        return None


# --- the vote kind -----------------------------------------------------------


def _vote_allowlist(root: Path, author: str) -> list[str]:
    return [f"{KINDS['vote'].prefix}{author}.jsonl", ROSTER_FILE]


def _vote_rows(root: Path, author: str) -> list[ledger.Vote]:
    """The rows this submission adds: the lines past the base-ref prefix."""
    rel = f"evals/review/votes/{author}.jsonl"
    new = (root / rel).read_text(encoding="utf-8") if (root / rel).exists() else ""
    base = _base_text(root, rel) or ""
    if not new.startswith(base):
        return []
    added = new[len(base) :]
    return [
        ledger.Vote.from_json(json.loads(line))
        for line in added.splitlines()
        if line.strip()
    ]


def _check(name: str, problems: list[str], notes: list[str] | None = None) -> Check:
    return Check(name=name, problems=tuple(problems), notes=tuple(notes or ()))


# --- the checks every kind shares --------------------------------------------
#
# Each of these reads its kind out of :data:`KINDS` and is bound to one by
# ``partial`` in that kind's preflight. Written once because the three kinds
# differ only in a path prefix, a noun and a count, and all three of those
# already live on the :class:`Kind` — which is what ``CLAUDE.md`` means by
# keying machinery that grows an entry per anything. A fourth kind adds a
# table row and no check.


def _subdirs(root: Path, prefix: str) -> list[str]:
    """Every directory under ``prefix`` this submission touches, sorted."""
    return sorted(
        {
            rest.split("/")[0]
            for rel in _changed_paths(root)
            if rel.startswith(prefix) and "/" in (rest := rel[len(prefix) :])
        }
    )


def _one_subdir(root: Path, prefix: str) -> str | None:
    """The one directory under ``prefix`` this submission touches, or None.

    ``None`` covers both "no directory" and "more than one", because a kind
    that carries exactly one treats either miss as the same refusal.
    """
    names = _subdirs(root, prefix)
    return names[0] if len(names) == 1 else None


@dataclass(frozen=True)
class _Cardinality:
    """One answer to "how many subject directories", and what it refuses."""

    #: The checklist line and the problem, each formatted with the kind's
    #: name, subject noun and prefix.
    line: str
    problem: str
    holds: Callable[[int], bool]


#: What :attr:`Kind.subjects` may say. A table keyed by the field's value, so
#: the count a kind carries is data rather than an ``if`` on a kind name.
#: ``tests/test_evals_submit.py`` checks it against :data:`KINDS`.
_SUBJECTS: dict[str, _Cardinality] = {
    "one": _Cardinality(
        line="the change is one {subject} directory",
        problem="a {kind} PR touches exactly one {subject} directory under {prefix}",
        holds=lambda count: count == 1,
    ),
    "many": _Cardinality(
        line="the change is at least one {subject} directory",
        problem="a {kind} PR touches at least one {subject} directory under {prefix}",
        holds=lambda count: count > 0,
    ),
}


def _check_subject_count(root: Path, author: str, kind_name: str) -> Check:
    kind = KINDS[kind_name]
    rule = _SUBJECTS[kind.subjects]
    words = {"kind": kind_name, "subject": kind.subject, "prefix": kind.prefix}
    return _check(
        rule.line.format(**words),
        []
        if rule.holds(len(_subdirs(root, kind.prefix)))
        else [rule.problem.format(**words)],
    )


def _check_scope(root: Path, author: str, kind_name: str) -> Check:
    """Nothing outside the kind's allowlist changed — a dirty tree included.

    The delta covers untracked files too, so a scratch file anywhere in the
    checkout fails this rather than riding into the pull request.
    """
    kind = KINDS[kind_name]
    allowed = set(kind.allowlist(root, author))
    strays = [rel for rel in _changed_paths(root) if rel not in allowed]
    return _check(
        "nothing outside this kind's allowlist changed",
        [f"{rel} is changed but not part of a {kind.noun}" for rel in strays],
    )


def _standing_of(root: Path, author: str) -> str:
    """The author's standing, for the sentence each kind closes its PR with."""
    return roster.load(root / ROSTER_FILE).standing_of(author)


#: The one sentence every kind's PR body carries. Spelled once because it is
#: a fact about how this repository merges, not about any one kind.
REVIEW_SENTENCE = "A maintainer reviews every line before this merges."


def _check_ledger_loads(root: Path, author: str) -> Check:
    problems = []
    try:
        ledger.load(root / "evals" / "review" / "votes")
    except ledger.LedgerError as exc:
        problems.append(str(exc))
    return _check("the whole ledger loads, fail-closed", problems)


def _check_roster_covers(root: Path, author: str) -> Check:
    problems = []
    try:
        table = roster.load(root / ROSTER_FILE)
        votes = ledger.load(root / "evals" / "review" / "votes")
        problems += [
            f"{voter!r} has no roster line"
            for voter in sorted({vote.voter for vote in votes})
            if voter not in table
        ]
        if author not in table:
            problems.append(
                f"{author!r} has no roster line; add yourself to"
                f' {ROSTER_FILE} with standing "contributor"'
            )
    # LedgerError too: this reads the ledger as well as the roster, and a
    # malformed row made the checklist exit on a traceback instead of a line a
    # contributor can act on. Fail-closed either way; only one of them is
    # readable.
    except (roster.RosterError, ledger.LedgerError) as exc:
        problems.append(str(exc))
    return _check("every voter has a roster line, including you", problems)


def _check_the_delta_is_yours(root: Path, author: str) -> Check:
    """#320's binding, failed early: the ledger delta names only the login."""
    problems = []
    votes_dir = "evals/review/votes/"
    own = f"{votes_dir}{author}.jsonl"
    for rel in _changed_paths(root):
        if rel.startswith(votes_dir) and rel != own:
            problems.append(f"{rel} is not your file; you are {author!r}")
    return _check("the ledger delta is yours alone", problems)


def _check_your_file_appends(root: Path, author: str) -> Check:
    """#322's append shape: the base content is a byte prefix of the new."""
    rel = f"evals/review/votes/{author}.jsonl"
    new = (root / rel).read_text(encoding="utf-8") if (root / rel).exists() else ""
    base = _base_text(root, rel)
    problems = []
    if base is not None and not new.startswith(base):
        problems.append(
            f"{rel} rewrites history; a vote submission only appends, and a"
            " re-key is a maintainer's own PR"
        )
    else:
        try:
            if not _vote_rows(root, author):
                problems.append(f"{rel} adds no votes; there is nothing to submit")
        except (ledger.LedgerError, json.JSONDecodeError) as exc:
            problems.append(f"{rel}: an added row will not parse: {exc}")
    return _check("your file only appends, and adds something", problems)


def _roster_delta(base_raw: str | None, live: Path) -> list[str]:
    """Every roster line this PR changes, for the reviewer to read.

    Informational, and deliberately not a refusal. What a roster edit is
    entitled to do is a judgement about people, and the merge is where this
    repository makes it -- ``voters.toml`` says a maintainer line is legitimate
    only because a maintainer merged it. A check cannot make that call.

    What it can do is put the edit where the person making the call will see it.
    :func:`_check_no_self_raise` looks at one line, the author's, which is the
    contract ``contribution.yml`` states; an audit twice observed that the other
    lines go past unremarked, and twice concluded the merge is the control.
    This is the note that observation asked for both times: the control works
    better when it is shown the diff.
    """
    if base_raw is None:
        return []
    try:
        was = tomllib.loads(base_raw).get("voters", {})
        now = tomllib.loads(live.read_text(encoding="utf-8")).get("voters", {})
    except (OSError, tomllib.TOMLDecodeError):
        return []  # the check below reports an unreadable roster
    notes = []
    for login in sorted(set(was) | set(now)):
        before, after = was.get(login), now.get(login)
        if before == after:
            continue
        if before is None:
            notes.append(f"{login}: added as {after.get('standing', '?')!r}")
        elif after is None:
            notes.append(f"{login}: removed (was {before.get('standing', '?')!r})")
        else:
            for key in sorted(set(before) | set(after)):
                if before.get(key) != after.get(key):
                    notes.append(
                        f"{login}.{key}: {before.get(key)!r} -> {after.get(key)!r}"
                    )
    return notes


def _check_no_self_raise(root: Path, author: str) -> Check:
    """#320's one dangerous edit: nobody raises their own standing.

    One line, the author's, which is what ``contribution.yml`` states this job
    proves. Every other roster line is reported beside the result rather than
    refused -- see :func:`_roster_delta`.
    """
    problems = []
    base_raw = _base_text(root, ROSTER_FILE)
    live = root / ROSTER_FILE
    try:
        now = roster.load(live).standing_of(author) if live.exists() else None
        was = None
        if base_raw is not None:
            table = tomllib.loads(base_raw).get("voters", {})
            entry = table.get(author, {})
            was = entry.get("standing") if isinstance(entry, dict) else None
        if now is not None and _RANK[now] > _RANK.get(was or "contributor", 0):
            problems.append(
                f"this PR raises your own standing to {now!r}; a promotion is"
                " a maintainer's edit (#326)"
            )
        if was is None and now == "maintainer":
            problems.append(
                'a first roster line carries standing "contributor";'
                " a maintainer line is legitimate only because a maintainer"
                " merged it"
            )
    except (roster.RosterError, tomllib.TOMLDecodeError) as exc:
        problems.append(str(exc))
    return _check(
        "your roster line does not raise itself",
        problems,
        notes=_roster_delta(base_raw, live) if live.exists() else [],
    )


def _vote_preflight(root: Path, author: str) -> list[Check]:
    return [
        check(root, author)
        for check in (
            _check_ledger_loads,
            _check_roster_covers,
            partial(_check_scope, kind_name="vote"),
            _check_the_delta_is_yours,
            _check_your_file_appends,
            _check_no_self_raise,
        )
    ]


def _vote_title(root: Path, author: str) -> str:
    rows = _vote_rows(root, author)
    cases = {vote.case for vote in rows}
    return f"Vote: {author}, {len(rows)} votes over {len(cases)} cases"


def _vote_closing(root: Path, author: str) -> str:
    standing = _standing_of(root, author)
    return (
        f"{REVIEW_SENTENCE} These votes join the {standing} series; every"
        " published number states the standings it includes."
    )


# --- the sitting kind ---------------------------------------------------------

#: Where the unreviewed list lives; a sitting PR deletes its case's line
#: from it.
CASE_REVIEW_TEST = "tests/test_case_review.py"


def _sitting_cases(root: Path) -> list[str]:
    """Every case this submission touches, sorted. One session, one PR."""
    return _subdirs(root, KINDS["sitting"].prefix)


#: The shape a declared framework name holds before it reaches a path. The
#: allowlist derives a claim file from the case's own metadata, so a name
#: carrying a separator would name a file outside the case's ``claims``
#: directory (A01). A package nobody wrote yet still passes it: every package
#: this repository ships is a lowercase slug.
_FRAMEWORK_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def _declared_frameworks(root: Path, case: str) -> list[str]:
    """Every framework one case declares, in the order it declares them.

    Fail-closed and quiet: a case whose metadata cannot be read, or whose
    declaration is the wrong shape, declares nothing here. The checks that
    read the metadata report that, and an allowlist which raised would answer
    a scope question with a parse error.
    """
    path = root / KINDS["sitting"].prefix / case / "case.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    declared = raw.get("frameworks") if isinstance(raw, dict) else None
    if not isinstance(declared, list):
        return []
    names = [entry.get("name") for entry in declared if isinstance(entry, dict)]
    return [
        name
        for name in names
        if isinstance(name, str) and _FRAMEWORK_NAME.fullmatch(name)
    ]


def _sitting_allowlist(root: Path, author: str) -> list[str]:
    """A sitting may change an answer. It may never change the question.

    Under each touched case: the case metadata, this reader's own filled
    reading document, and one claim file per framework the case declares
    (#388). The reference sets are the answer under review, so a reader who
    corrects one does what #327 asks for. The sources and the blessed
    **System Model** are the question, so an edit to either falls outside
    and fails scope — a reader who rewrites the question makes their own
    read unfalsifiable.

    The claim files come from the case's own declaration rather than a list
    here, so a **Framework Package** nobody wrote yet is covered the moment a
    case declares it. The document name comes from the authenticated login
    rather than from the appended entry, so no name the diff supplies can
    widen the list.
    """
    allowed = [
        f"{KINDS['sitting'].prefix}{case}/{name}"
        for case in _sitting_cases(root)
        for name in [
            "case.json",
            document_name(author),
            *claim_files(_declared_frameworks(root, case)),
        ]
    ]
    return [*allowed, CASE_REVIEW_TEST, ROSTER_FILE]


def _new_sittings(root: Path, case: str) -> tuple[list[dict], list[str]]:
    """The entries this PR appends, and what is wrong with the append.

    ``reviews`` is append-only (#327): the base ref's entries must survive as
    an exact prefix, and only what follows them is this submission's.
    """
    rel = f"evals/corpus/{case}/case.json"
    try:
        raw = json.loads((root / rel).read_text(encoding="utf-8"))
        base_raw = _base_text(root, rel)
        base = json.loads(base_raw) if base_raw else {}
    except (OSError, json.JSONDecodeError) as exc:
        return [], [f"{case}/case.json: cannot be read: {exc}"]
    reviews = raw.get("reviews", []) if isinstance(raw, dict) else None
    base_reviews = base.get("reviews", []) if isinstance(base, dict) else None
    # Shape first: one malformed case refuses its own case, and every other
    # one still reports. A slice of the wrong type raises out of the pass.
    if not isinstance(reviews, list) or not isinstance(base_reviews, list):
        return [], [f"{case}/case.json: no `reviews` list to append to"]
    if reviews[: len(base_reviews)] != base_reviews:
        return [], [
            (
                f"{case}/case.json rewrites recorded sittings; `reviews` is"
                " append-only, and a correction is a new entry"
            )
        ]
    new = reviews[len(base_reviews) :]
    if not new:
        return [], [f"{case}/case.json appends no sitting entry"]
    return new, []


def _appended(root: Path, case: str) -> list[dict]:
    """The entries this PR appends to one case, without what refuses them.

    :func:`_check_sitting_is_yours` prints every problem :func:`_new_sittings`
    finds, so the checks that only read the entries stay quiet about them:
    one problem, one message, whatever N is.
    """
    new, _ = _new_sittings(root, case)
    return new


def _check_sitting_is_yours(root: Path, author: str) -> Check:
    """#327 check 1, failed early: every appended entry is submitted by the author.

    This also carries the gate ADR 0020 took off the cardinality check: a case
    directory in the diff whose metadata appends nothing refuses the whole
    submission, so a submission cannot carry a case it did not sit.

    **It reads ``submitted_by`` and never ``submitted_for``.** The submitting
    account is what `gh` proves, and it stays bound to the author exactly as
    #320 rules. ``submitted_for`` names who read the case, is free to be
    :data:`~evals.harness.reference.ANONYMOUS` or another person, and is
    checked by nothing here — because it grants nothing anywhere.
    """
    problems: list[str] = []
    for case in _sitting_cases(root):
        new, refusals = _new_sittings(root, case)
        problems += refusals
        for entry in new:
            try:
                sitting = CaseSitting.model_validate(entry)
            except ValidationError as exc:
                problems.append(
                    f"{case}/case.json: an appended entry is malformed: {exc}"
                )
                continue
            if sitting.submitted_by != author:
                problems.append(
                    f"{case}: an appended entry is submitted by"
                    f" {sitting.submitted_by!r}; you are {author!r}, and a"
                    " sitting only enters through its own submitter's PR"
                    " (#320)"
                )
    return _check("every appended sitting is submitted by you", problems)


def _check_sitting_evidence(root: Path, author: str) -> Check:
    """The digests match the tree, and the filled document is committed."""
    problems: list[str] = []
    for case in _sitting_cases(root):
        case_dir = root / "evals" / "corpus" / case
        for entry in _appended(root, case):
            try:
                sitting = CaseSitting.model_validate(entry)
            except ValidationError:
                continue  # named by the naming check; one problem, one message
            for record in sitting.read:
                target = case_dir / record.file
                if not target.is_file():
                    problems.append(
                        f"{case}/{record.file}: read but not in the case directory"
                    )
                elif hashlib.sha256(target.read_bytes()).hexdigest() != record.sha256:
                    problems.append(
                        f"{case}/{record.file}: the digest does not match the"
                        " file; the entry signs the bytes that merge, so"
                        " recompute it"
                    )
            problems += _document_problems(case, case_dir, sitting, author)
    return _check("the digests and the document hold", problems)


def _document_problems(
    case: str, case_dir: Path, sitting: CaseSitting, author: str
) -> list[str]:
    """Whether the entry names the document this submission may write, and
    whether that document says anything.

    The old check was one `.is_file()` probe on whatever name the entry gave.
    A name is not a path this repository resolves, but it is a claim, and the
    probe let the claim be any file that happens to exist: `"source.md"`
    satisfied it in every case directory, so an entry could clear a case while
    committing no reading document at all.

    Naming it is half. `document()` is the evidence that the method ran, and
    `MIN_OWN_LIST` is the floor that makes a filled copy different from an empty
    one -- enforced in the app and in the offline envelope, and until now on
    neither side of a pull request. So `touch REVIEW-<login>.md` passed every
    gate under the correct name, and constraining only the name would have moved
    the same hole one step.
    """
    expected = document_name(author)
    if sitting.document != expected:
        return [
            (
                f"{case}: the entry names {sitting.document!r}, and a submission"
                f" writes {expected!r}. The document carries the submitting"
                " login, so an entry naming another is another submission's."
            )
        ]
    path = case_dir / expected
    if not path.is_file():
        return [
            (
                f"{case}/{expected}: the filled document is the evidence, and"
                " it is not committed beside the case"
            )
        ]
    if len(path.read_text(encoding="utf-8").strip()) < MIN_OWN_LIST:
        return [
            (
                f"{case}/{expected}: the document is empty. It is the evidence"
                " the method ran, and the app and the offline page both hold a"
                " filled one to the same floor."
            )
        ]
    return []


def _check_sitting_covers(root: Path, author: str) -> Check:
    """#327's derived rule: the read covers every framework the case declares."""
    problems: list[str] = []
    for case in _sitting_cases(root):
        new = _appended(root, case)
        if not new:
            continue  # the naming check refuses a case that appends nothing
        raw = json.loads(
            (root / "evals" / "corpus" / case / "case.json").read_text(encoding="utf-8")
        )
        required = set(
            required_files(declared["name"] for declared in raw.get("frameworks", []))
        )
        read = {
            record.get("file")
            for entry in new
            if isinstance(entry, dict)
            for record in entry.get("read", [])
            if isinstance(record, dict)
        }
        if gap := sorted(required - read):
            problems.append(
                f"{case}: the appended sitting leaves {gap} unread; one sitting"
                " signs the model and every declared framework's reference set"
                " together"
            )
    return _check("the sitting covers every declared framework", problems)


def _check_sitting_clears_unreviewed(root: Path, author: str) -> Check:
    """Every carried case is off the unreviewed list.

    Asked of the parsed table rather than of the file's text. A substring test
    answers a slightly different question -- is this spelling present -- and the
    difference between the two questions is a gap: a key respelled so the text
    does not hold it, while :mod:`ast` still reads it as the same case, passes
    the text reader and is removed by the parsed one, so the entry survives with
    whatever it carries. One reader, so there is no second opinion to disagree
    with.
    """
    cases = _sitting_cases(root)
    if not cases:
        return _check("every case's UNREVIEWED line is gone", [])
    try:
        listed = set(unreviewed_cases(root))
    except SittingError as exc:
        return _check("every case's UNREVIEWED line is gone", [str(exc)])
    return _check(
        "every case's UNREVIEWED line is gone",
        [
            f"{case}: delete its line from UNREVIEWED in {CASE_REVIEW_TEST}"
            for case in cases
            if case in listed
        ],
    )


def _check_sitting_edits_only_the_list(root: Path, author: str) -> Check:
    """The unreviewed list changed by the carried cases' lines and nothing else.

    ``CASE_REVIEW_TEST`` is the one file outside a case directory that a
    sitting may change, and it is a module ``pytest`` imports — so an
    allowlist that admits it by path admits arbitrary Python running in
    everybody's checkout. The scope check compares paths and cannot see that;
    this compares content.

    Both sides have the carried cases' entries taken out before they are
    compared, so a reader who has not cleared a line yet fails
    :func:`_check_sitting_clears_unreviewed` and not this. What is left is
    the module's prose, its code, and every case this submission does not
    carry.
    """
    cases = _sitting_cases(root)
    if not cases:
        return _check(_ONLY_THE_LIST, [])
    base = _base_text(root, CASE_REVIEW_TEST)
    if base is None:
        return _check(
            _ONLY_THE_LIST,
            [f"{CASE_REVIEW_TEST}: the base ref has no such file to compare against"],
        )
    live = (root / CASE_REVIEW_TEST).read_text(encoding="utf-8")
    try:
        moved = without_unreviewed(live, cases) != without_unreviewed(base, cases)
    except SittingError as exc:
        # The message names the file already, so it goes out as it stands.
        return _check(_ONLY_THE_LIST, [str(exc)])
    refusal = (
        f"{CASE_REVIEW_TEST} changed outside the lines this sitting clears;"
        " a sitting deletes its cases' UNREVIEWED entries and nothing else"
    )
    return _check(_ONLY_THE_LIST, [refusal] if moved else [])


#: Spelled once because the check names it twice and a checklist line is the
#: contract a contributor reads.
_ONLY_THE_LIST = "the unreviewed list changed only by those lines"


def _check_author_rostered(root: Path, author: str) -> Check:
    problems = []
    try:
        if author not in roster.load(root / ROSTER_FILE):
            problems.append(
                f"{author!r} has no roster line; add yourself to"
                f' {ROSTER_FILE} with standing "contributor"'
            )
    except roster.RosterError as exc:
        problems.append(str(exc))
    return _check("you have a roster line", problems)


def _sitting_preflight(root: Path, author: str) -> list[Check]:
    return [
        check(root, author)
        for check in (
            partial(_check_subject_count, kind_name="sitting"),
            partial(_check_scope, kind_name="sitting"),
            _check_sitting_is_yours,
            _check_sitting_evidence,
            _check_sitting_covers,
            _check_sitting_clears_unreviewed,
            _check_sitting_edits_only_the_list,
            _check_author_rostered,
            _check_no_self_raise,
        )
    ]


def _sitting_title(root: Path, author: str) -> str:
    """``Sitting: <author>, <n> cases``, the vote kind's shape.

    Its plural agreement too: the count reads for itself, and a branch on a
    count is the thing this repository does not write.
    """
    return f"Sitting: {author}, {len(_sitting_cases(root))} cases"


def _sitting_closing(root: Path, author: str) -> str:
    standing = _standing_of(root, author)
    cases = "\n".join(f"- {case}" for case in _sitting_cases(root))
    return (
        f"{cases}\n\n"
        f"{REVIEW_SENTENCE} Each sitting above clears its case from"
        f" UNREVIEWED, and standing {standing!r} labels the read — the"
        " UNREVIEWED line means nobody read it, and a labelled read answers"
        " that."
    )


# --- the baseline kind ---------------------------------------------------------


def _baseline_prepare(root: Path, author: str, args: argparse.Namespace) -> None:
    """Assemble the sweeps, then rebuild the published comparison.

    The rebuild is here so a contributor never learns the step exists (#330):
    the table is generated from the Baselines they just laid down, and the
    staleness test would otherwise fail their PR for a file they had never
    heard of.
    """
    paths = [Path(raw) for raw in getattr(args, "artifact", None) or []]
    if paths:
        baseline.assemble(root, author, paths)
    comparison.write(root)


def _baseline_dir(root: Path) -> str | None:
    """The one Baseline this submission touches, or None when it is not one."""
    return _one_subdir(root, KINDS["baseline"].prefix)


def _baseline_allowlist(root: Path, author: str) -> list[str]:
    name = _baseline_dir(root)
    prefix = f"{KINDS['baseline'].prefix}{name}/"
    changed = [
        rel
        for rel in _changed_paths(root)
        if name is not None and rel.startswith(prefix)
    ]
    # The generated comparison moves whenever a Baseline lands, and
    # ``_baseline_prepare`` has already rebuilt it (#330), so it travels with
    # the submission rather than failing the PR as a stray.
    return [*changed, "evals/baselines/README.md", ROSTER_FILE]


def _check_baseline_verifies(root: Path, author: str) -> Check:
    """#323's artifact-consistency checks, failed at the contributor's machine."""
    name = _baseline_dir(root)
    if name is None:
        return _check("the Baseline recomputes: identity, name, digests, cost", [])
    problems = baseline.verify(
        root / "evals" / "baselines" / name, root=root, base_ref=BASE_REF
    )
    return _check("the Baseline recomputes: identity, name, digests, cost", problems)


def _check_baseline_sweeps_are_yours(root: Path, author: str) -> Check:
    """#323's label: every sweep this PR adds is stamped with your login."""
    name = _baseline_dir(root)
    if name is None:
        return _check("every added sweep is stamped with your login", [])
    manifest_rel = f"evals/baselines/{name}/baseline.json"
    problems: list[str] = []
    try:
        manifest = json.loads((root / manifest_rel).read_text(encoding="utf-8"))
        base_raw = _base_text(root, manifest_rel)
        known = {
            str(entry.get("artifact"))
            for entry in (json.loads(base_raw).get("sweeps", []) if base_raw else [])
        }
        for entry in manifest.get("sweeps", []):
            if str(entry.get("artifact")) in known:
                continue
            if entry.get("submitted_by") != author:
                problems.append(
                    f"{entry.get('artifact')}: stamped"
                    f" {entry.get('submitted_by')!r}; you are {author!r}, and"
                    " the label is the disclosure (#323)"
                )
    except (OSError, json.JSONDecodeError) as exc:
        problems.append(f"{manifest_rel}: cannot be read: {exc}")
    return _check("every added sweep is stamped with your login", problems)


def _baseline_preflight(root: Path, author: str) -> list[Check]:
    return [
        check(root, author)
        for check in (
            partial(_check_subject_count, kind_name="baseline"),
            partial(_check_scope, kind_name="baseline"),
            _check_baseline_verifies,
            _check_baseline_sweeps_are_yours,
            _check_author_rostered,
            _check_no_self_raise,
        )
    ]


def _baseline_manifest(root: Path) -> dict:
    name = _baseline_dir(root)
    return json.loads(
        (root / "evals" / "baselines" / str(name) / "baseline.json").read_text(
            encoding="utf-8"
        )
    )


def _baseline_title(root: Path, author: str) -> str:
    return f"Baseline: {_baseline_dir(root)}"


def _baseline_closing(root: Path, author: str) -> str:
    """The review aid #325 asks for; every checked fact stays in the files."""
    manifest = _baseline_manifest(root)
    identity = manifest.get("identity", {})
    sweeps = manifest.get("sweeps", [])
    # Escaped for the reason `comparison._inline` gives: these come out of the
    # contributor's own artifact, and this text is the review aid a maintainer
    # reads before merging it.
    models = ", ".join(
        f"{comparison._inline(tier)}: {comparison._inline(model)}"
        for tier, model in sorted(identity.get("models", {}).items())
    )
    costs = [entry.get("cost", {}) for entry in sweeps]
    total = sum(float(cost.get("actual_usd", 0.0)) for cost in costs)
    unpriced = sorted({model for cost in costs for model in cost.get("unpriced", ())})
    standing = _standing_of(root, author)
    lines = [
        (
            f"{len(sweeps)} sweep(s) at commit {identity.get('repo_commit', '')[:12]},"
            f" frameworks {', '.join(identity.get('frameworks', ()))}; {models}."
        ),
        f"Recorded actual cost across the directory: ${total:.2f}"
        + (f"; unpriced models: {', '.join(unpriced)}" if unpriced else "")
        + ".",
        (
            f"{REVIEW_SENTENCE} Standing {standing!r} labels the submission;"
            " every published number states the standings behind its Baseline."
        ),
    ]
    return "\n".join(lines)


#: Every submission kind the CLI offers. A new kind arrives as an entry here,
#: never as a branch in the spine.
KINDS: dict[str, Kind] = {
    "vote": Kind(
        prefix="evals/review/votes/",
        noun="vote submission",
        preflight=_vote_preflight,
        allowlist=_vote_allowlist,
        title=_vote_title,
        closing=_vote_closing,
    ),
    "sitting": Kind(
        prefix="evals/corpus/",
        # `REVIEW.md` is written by `evals/build_review_docs.py` from the case
        # it sits beside. The filled `REVIEW-<login>.md` is evidence and is
        # not derived, so the two names part company here.
        derived=frozenset({GENERATED_DOCUMENT}),
        noun="sitting",
        subject="case",
        subjects="many",
        preflight=_sitting_preflight,
        allowlist=_sitting_allowlist,
        title=_sitting_title,
        closing=_sitting_closing,
    ),
    "baseline": Kind(
        prefix="evals/baselines/",
        noun="baseline submission",
        subject="Baseline",
        preflight=_baseline_preflight,
        allowlist=_baseline_allowlist,
        title=_baseline_title,
        closing=_baseline_closing,
        prepare=_baseline_prepare,
    ),
}


# --- the spine ----------------------------------------------------------------


def _branch_name(root: Path, kind: str, author: str, remote: str) -> str:
    """``submit/<kind>/<login>-<date>``, suffixed numerically on collision."""
    stem = f"submit/{kind}/{author}-{datetime.now(UTC).date().isoformat()}"
    name = stem
    suffix = 2
    while _run(["git", "ls-remote", remote, f"refs/heads/{name}"], root).strip():
        name = f"{stem}-{suffix}"
        suffix += 1
    return name


def _push_remote(root: Path, author: str) -> str:
    """Where the branch goes: origin for the repo's owner, the fork otherwise.

    ``gh repo fork`` is idempotent — it creates the fork when missing and
    answers with the existing one when not.
    """
    owner = _run(
        ["gh", "repo", "view", "--json", "owner", "--jq", ".owner.login"], root
    ).strip()
    if owner == author:
        return "origin"
    name = _run(["gh", "repo", "view", "--json", "name", "--jq", ".name"], root).strip()
    _run(["gh", "repo", "fork", "--clone=false"], root)
    return f"https://github.com/{author}/{name}.git"


def _pr_head(remote: str, author: str, branch: str) -> str:
    return branch if remote == "origin" else f"{author}:{branch}"


def open_pr(root: Path, kind_name: str, author: str) -> str:
    """Package the allowlist on a fresh branch and open the PR. Returns the URL.

    The branch is built in a throwaway worktree cut from BASE_REF, so the
    contributor's own checkout is never switched, and nothing outside the
    allowlist can ride along — the preflight already refused a dirty tree,
    and the worktree makes that refusal structural.
    """
    kind = KINDS[kind_name]
    _run(["git", "fetch", "origin"], root)
    remote = _push_remote(root, author)
    branch = _branch_name(root, kind_name, author, remote)
    title = kind.title(root, author)
    body = (
        f"{kind.closing(root, author)}\n\n"
        "This PR was opened by `submit`; the checked facts live in the files"
        " the code reads, never in this body."
    )
    with TemporaryDirectory(prefix="submit-") as scratch:
        worktree = Path(scratch) / "worktree"
        _run(["git", "worktree", "add", "--detach", str(worktree), BASE_REF], root)
        try:
            staged = []
            for rel in kind.allowlist(root, author):
                source = root / rel
                if not source.exists():
                    continue
                target = worktree / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
                staged.append(rel)
            _run(["git", "checkout", "-b", branch], worktree)
            _run(["git", "add", "--", *staged], worktree)
            _run(["git", "commit", "-m", title], worktree)
            _run(["git", "push", remote, f"HEAD:refs/heads/{branch}"], worktree)
        finally:
            _run(["git", "worktree", "remove", "--force", str(worktree)], root)
    return _run(
        [
            "gh",
            "pr",
            "create",
            "--head",
            _pr_head(remote, author, branch),
            "--title",
            title,
            "--body",
            body,
        ],
        root,
    ).strip()


# --- the CI side --------------------------------------------------------------


def detect_kind(root: Path) -> str | None:
    """Which kind this diff carries, read off the :data:`KINDS` table.

    ``None`` when the diff touches no kind's tree — an ordinary code PR, or a
    roster line on its own. Raises when it touches two, because one kind per
    PR is the rule the forced merge order rests on (#325).
    """
    changed = _changed_paths(root)
    found = sorted(
        name
        for name, kind in KINDS.items()
        if any(rel.startswith(kind.prefix) for rel in changed)
    )
    if len(found) > 1:
        raise SubmitError(
            f"this PR carries {' and '.join(found)} changes; one kind per PR,"
            " so split it — and the order matters, because a vote over a"
            " merged Baseline stamps that Baseline's identity"
        )
    return found[0] if found else None


def command_verify(args: argparse.Namespace) -> int:
    """Run a contribution's checks against the PR author. What CI calls.

    The same functions the contributor already ran through ``submit
    --dry-run``, against the login GitHub says opened the PR rather than the
    one ``gh`` is signed in as. That equality is the whole binding (#320): a
    submission enters only through its own author's PR, and nothing here
    trusts a name the diff supplies.
    """
    root = REPO_ROOT
    author = args.author.strip()
    if not author:
        print("no PR author given; the binding cannot be checked")
        return 1

    try:
        kind = detect_kind(root)
    except SubmitError as exc:
        print(f"FAIL  {exc}")
        return 1

    with _delta_cache(root):
        checks = list(KINDS[kind].preflight(root, author)) if kind else []
    if kind is None:
        # A roster line with no submission behind it still may not raise its
        # own author's standing — the one edit that is dangerous alone.
        if ROSTER_FILE in _changed_paths(root):
            checks = [_check_no_self_raise(root, author)]
        else:
            print("no contribution in this diff; nothing to check")
            return 0

    print(f"checking this {kind or 'roster'} PR as {author}\n")
    for check in checks:
        print(f"  {'ok  ' if check.passed else 'FAIL'}  {check.name}")
        for problem in check.problems:
            print(f"        {problem}")
        for note in check.notes:
            print(f"        note  {note}")
    if all(check.passed for check in checks):
        return 0
    print(
        "\nThe contributor's own `submit --dry-run` runs these same checks,"
        " so a red result here usually means the branch moved under them."
    )
    return 1


def _register(root: Path, author: str) -> bool:
    """Add the author's roster line when they have none. Returns whether it wrote.

    Self-registration is the decision (#320): a first-time contributor's own
    PR carries their line, standing ``contributor``, and nobody waits for
    provisioning. The command knows the login and knows the line is missing,
    so failing a checklist over it would be the tool asking a person to do
    the one thing it could do itself.

    Never an upgrade: this only ever appends a ``contributor`` line for
    somebody the roster does not name. Raising a standing stays a
    maintainer's edit, and :func:`_check_no_self_raise` still refuses one.
    """
    path = root / ROSTER_FILE
    if author in roster.load(path):
        return False
    text = path.read_text(encoding="utf-8").rstrip("\n")
    path.write_text(
        f'{text}\n\n[voters.{author}]\nstanding = "contributor"\n', encoding="utf-8"
    )
    print(
        f"added your roster line to {ROSTER_FILE} as a contributor."
        " It rides along with this submission, and a maintainer reviews it"
        " with the rest.\n"
    )
    return True


@dataclass(frozen=True)
class Outcome:
    """What a submission attempt did. One shape for every caller.

    The CLI prints it and the sitting app returns it as JSON, so a rule can
    never hold on one surface and not the other — the reason this is a value
    rather than a pile of prints.
    """

    author: str
    checks: tuple[Check, ...] = ()
    url: str = ""
    closing: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and all(check.passed for check in self.checks)

    def to_json(self) -> dict[str, Any]:
        return {
            "author": self.author,
            "checks": [
                {
                    "name": check.name,
                    "passed": check.passed,
                    "problems": list(check.problems),
                }
                for check in self.checks
            ],
            "url": self.url,
            "closing": self.closing,
            "error": self.error,
            "ok": self.ok,
        }


def submission(
    root: Path,
    kind_name: str,
    *,
    dry_run: bool = False,
    args: argparse.Namespace | None = None,
) -> Outcome:
    """The spine: bind, register, prepare, check, and open unless asked not to.

    Every caller runs exactly this, so the checklist a contributor sees
    locally, the one the sitting app shows, and the rules CI re-runs are one
    implementation rather than three that agree today.
    """
    try:
        author = gh_login(root)
    except SubmitError as exc:
        return Outcome(author="", error=f"cannot read the gh login: {exc}")

    try:
        _run(["git", "fetch", "origin"], root)
        _register(root, author)
    except (OSError, SubmitError, roster.RosterError) as exc:
        return Outcome(author=author, error=str(exc))

    kind = KINDS[kind_name]
    if kind.prepare is not None:
        try:
            kind.prepare(root, author, args or argparse.Namespace())
        except (SubmitError, ProvenanceError, baseline.BaselineError) as exc:
            return Outcome(
                author=author, error=f"cannot assemble the submission: {exc}"
            )

    # Opened here, after every write this function makes: the checks read one
    # snapshot of the tree, and the packaging below reads a fresh one.
    with _delta_cache(root):
        checks = tuple(kind.preflight(root, author))
    if not all(check.passed for check in checks) or dry_run:
        return Outcome(author=author, checks=checks)

    try:
        url = open_pr(root, kind_name, author)
    except SubmitError as exc:
        return Outcome(author=author, checks=checks, error=f"cannot open the PR: {exc}")
    return Outcome(
        author=author, checks=checks, url=url, closing=kind.closing(root, author)
    )


def command_submit(args: argparse.Namespace) -> int:
    """The four steps, in order, stopping at the first that fails."""
    outcome = submission(REPO_ROOT, args.kind, dry_run=args.dry_run, args=args)
    if outcome.author:
        print(f"submitting as {outcome.author}\n")
    for check in outcome.checks:
        print(f"  {'ok  ' if check.passed else 'FAIL'}  {check.name}")
        for problem in check.problems:
            print(f"        {problem}")
    if outcome.error:
        print(outcome.error)
        return 1
    if not outcome.ok:
        print("\nnothing opened; fix the failures above and re-run.")
        return 1
    if not outcome.url:
        print("\ndry run: the checklist passed. No branch, no PR.")
        return 0
    print(f"\n{outcome.url}")
    print(outcome.closing)
    return 0
