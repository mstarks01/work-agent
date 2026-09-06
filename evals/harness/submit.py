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

A **Case Sitting** is not a kind here. It merges as one JSON file under
``evals/review/submissions``, which :mod:`evals.review_submission` opens and
validates on its own terms — one file, no allowlist to widen, and nothing in
the working tree to package.

On security: everything this module runs is an argument list with
``shell=False`` (A05), and every path it stages comes from the kind's allowlist
rather than from the command line (A01). The packaging happens in a throwaway
git worktree cut from ``origin/main``, so nothing the contributor's checkout
carries beyond the allowlist can ride into the pull request.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tomllib
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from evals.harness import baseline, comparison, ledger, roster
from evals.harness.artifact import ProvenanceError
from evals.harness.fingerprint import (
    FingerprintError,
    version_for,
    version_of,
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


#: The repository a contribution goes to when the remote does not say. A
#: fallback rather than the answer: a fork's clone names its own remote, and a
#: contributor who reads a case in a fork should still be sent to the upstream
#: this constant names.
DEFAULT_REPO = "mstarks01/work-agent"

#: The branch a contribution branches from, read off :data:`BASE_REF` so the
#: two cannot name different branches.
BASE_BRANCH = BASE_REF.split("/", 1)[1]

_REMOTE = re.compile(r"(?:[:/])(?P<owner>[^/:]+)/(?P<name>[^/]+?)(?:\.git)?$")


def repo_slug(root: Path) -> str:
    """``owner/name`` for this clone's origin, or :data:`DEFAULT_REPO`.

    Read from git rather than written down, so a fork's own page links to the
    fork it was built in. It falls back rather than raising, because the only
    caller is a page offering somebody a link: no link is worse than a link to
    the upstream, and neither is worth refusing a sitting over.
    """
    try:
        url = _run(["git", "remote", "get-url", "origin"], root).strip()
    except SubmitError:
        return DEFAULT_REPO
    found = _REMOTE.search(url)
    return f"{found['owner']}/{found['name']}" if found else DEFAULT_REPO


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


def _stale_keys(rel: str, rows: list[ledger.Vote]) -> list[str]:
    """Rows this PR adds that are keyed under a version their framework left.

    The loader asks only that a row be self-consistent at the version it names,
    because a ledger written before a rule change still has to load -- `rekey`
    is the command that moves it, and it reads the file first. That leaves one
    gap, and it is here: a row written *today* under a version the table has
    moved past. It is self-consistent, so it loads and scores under the old
    rule, and `rekey` then refuses to move it and takes the whole ledger with
    it. An added row is the only row this repository can insist is current.
    """
    problems = []
    for vote in rows:
        want = version_for(vote.components.framework)
        got = version_of(vote.fingerprint)
        if got != want:
            problems.append(
                f"{rel}: a vote on {vote.case} is keyed at version {got}, and"
                f" {vote.components.framework} keys at {want}; re-key before you"
                " submit"
            )
    return problems


def _check_your_file_appends(root: Path, author: str) -> Check:
    """#322's append shape: the base content is a byte prefix of the new."""
    rel = f"evals/review/votes/{author}.jsonl"
    problems = []
    try:
        new = (root / rel).read_text(encoding="utf-8") if (root / rel).exists() else ""
    except UnicodeDecodeError as exc:
        # The same pull request commits this file, so its bytes are a
        # contributor's and need not be UTF-8. This read sits ahead of every
        # handler below, so the checklist ended in a traceback rather than in
        # the sentence that names the file.
        return _check(
            "your file only appends, and adds something",
            [f"{rel}: is not UTF-8 text: {exc}"],
        )
    base = _base_text(root, rel)
    if base is not None and not new.startswith(base):
        problems.append(
            f"{rel} rewrites history; a vote submission only appends, and a"
            " re-key is a maintainer's own PR"
        )
    else:
        try:
            rows = _vote_rows(root, author)
            if not rows:
                problems.append(f"{rel} adds no votes; there is nothing to submit")
            problems.extend(_stale_keys(rel, rows))
        except (ledger.LedgerError, json.JSONDecodeError) as exc:
            problems.append(f"{rel}: an added row will not parse: {exc}")
        except UnicodeDecodeError as exc:
            # The base revision's bytes, which `_vote_rows` reads separately
            # from the read above.
            problems.append(f"{rel}: is not UTF-8 text: {exc}")
        except FingerprintError as exc:
            # `version_for` raises this, and it is neither of the two above. A
            # row naming a framework outside VERSION_FOR loads fine, because
            # `fingerprint()` consults no table, and then aborted the whole
            # preflight with a traceback instead of naming the row.
            problems.append(f"{rel}: an added row names no known rule: {exc}")
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
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        return []  # the check below reports an unreadable roster
    # The table itself, not only its entries. `voters = "abc"` is legal TOML and
    # is the same defect one level up from the entry guard below: `set(was)`
    # would iterate its characters and `was.get` does not exist. The loader
    # reports it; this note only has to survive it.
    if not isinstance(was, dict) or not isinstance(now, dict):
        return ["voters: the roster's own table is not a table"]
    notes = []
    for login in sorted(set(was) | set(now)):
        before, after = was.get(login), now.get(login)
        if before == after:
            continue
        # A scalar here is `ada = "contributor"` where `[voters.ada]` was meant,
        # which is the line a first-timer writes. It is the roster loader's to
        # report; this one only has to not raise, because it runs after that
        # check's own handler and is the sole check a roster-only PR runs.
        if not isinstance(before, dict | None) or not isinstance(after, dict | None):
            notes.append(f"{login}: changed, and its entry is not a table")
            continue
        if before is None and after is not None:
            notes.append(f"{login}: added as {after.get('standing', '?')!r}")
        elif after is None and before is not None:
            notes.append(f"{login}: removed (was {before.get('standing', '?')!r})")
        elif before is not None and after is not None:
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
    # A cost that is not a table is not money and names nothing unpriced; the
    # baseline re-check lists it as a problem, and this summary reads past it.
    costs = [
        c for c in (entry.get("cost") for entry in sweeps) if isinstance(c, Mapping)
    ]
    total = sum(baseline.recorded_usd(cost) or 0.0 for cost in costs)
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
