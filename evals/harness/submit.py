"""One command opens every contribution PR: ``submit <kind>``.

The spine is #325's, and it is the same four steps for every kind: bind the
authenticated ``gh`` login, run this kind's CI checks locally as a checklist,
package the kind's allowlist on a fresh branch cut from ``origin/main``, and
open the PR through ``gh``. ``--dry-run`` stops after the checklist, which is
the contributor's local CI — a red PR should be rare because the same checks
already ran on their machine.

**Which kinds exist is a table**, :data:`KINDS`, never a branch: the sitting
and baseline kinds arrive by adding an entry (#337), and the CLI offers
exactly the table's keys. A kind that is not in the table is not a choice,
rather than a stub that refuses.

**The binding is strict — no proxy** (#320). The vote kind refuses to open a
PR whose ledger delta names any voter other than the authenticated login, and
it refuses a roster edit that raises the author's own standing. CI re-checks
both; running them here just fails them early, at the machine where the fix
is.

Security: everything this module runs is an argument list with
``shell=False`` (A05), and every path it stages comes from the kind's
allowlist rather than from the command line (A01). The packaging happens in a
throwaway git worktree cut from ``origin/main``, so nothing the contributor's
checkout carries beyond the allowlist can ride into the PR.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from evals.harness import ledger, roster

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The fixed point every submission diffs against and branches from. The PR
#: targets the default branch, so the delta that matters is the delta to it.
BASE_REF = "origin/main"

#: How much a standing may claim for its own author: nothing above what the
#: base ref already grants. Promotion exists — as a maintainer's edit (#326).
_RANK = {"contributor": 0, "maintainer": 1}


class SubmitError(RuntimeError):
    """The submission cannot proceed; the message says what stops it."""


@dataclass(frozen=True)
class Check:
    """One checklist line: a name, and the problems that fail it."""

    name: str
    problems: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.problems


@dataclass(frozen=True)
class Kind:
    """One submission kind: what it proves, stages, and says on the PR."""

    preflight: Callable[[Path, str], list[Check]]
    allowlist: Callable[[Path, str], list[str]]
    title: Callable[[Path, str], str]
    closing: Callable[[Path, str], str]


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


def _changed_paths(root: Path) -> list[str]:
    """Everything different from BASE_REF, tracked or not, repo-relative.

    The caller fetched once already; fetching here would make every check
    that reads the delta pay a network round trip.
    """
    tracked = _run(["git", "diff", "--name-only", BASE_REF], root).splitlines()
    porcelain = _run(
        ["git", "status", "--porcelain", "--untracked-files=all"], root
    ).splitlines()
    untracked = [line[3:] for line in porcelain if line.startswith("?? ")]
    return sorted(set(tracked) | set(untracked))


def _base_text(root: Path, rel: str) -> str | None:
    """The file's content at BASE_REF, or None where the base has no file."""
    try:
        return _run(["git", "show", f"{BASE_REF}:{rel}"], root)
    except SubmitError:
        return None


# --- the vote kind -----------------------------------------------------------


def _vote_allowlist(root: Path, author: str) -> list[str]:
    return [f"evals/review/votes/{author}.jsonl", "evals/review/voters.toml"]


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


def _check(name: str, problems: list[str]) -> Check:
    return Check(name=name, problems=tuple(problems))


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
        table = roster.load(root / "evals" / "review" / "voters.toml")
        votes = ledger.load(root / "evals" / "review" / "votes")
        problems += [
            f"{voter!r} has no roster line"
            for voter in sorted({vote.voter for vote in votes})
            if voter not in table
        ]
        if author not in table:
            problems.append(
                f"{author!r} has no roster line; add yourself to"
                ' evals/review/voters.toml with standing "contributor"'
            )
    except roster.RosterError as exc:
        problems.append(str(exc))
    return _check("every voter has a roster line, including you", problems)


def _check_only_the_allowlist_changed(root: Path, author: str) -> Check:
    allowed = set(_vote_allowlist(root, author))
    strays = [rel for rel in _changed_paths(root) if rel not in allowed]
    return _check(
        "nothing outside this kind's allowlist changed",
        [f"{rel} is changed but not part of a vote submission" for rel in strays],
    )


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


def _check_no_self_raise(root: Path, author: str) -> Check:
    """#320's one dangerous edit: nobody raises their own standing."""
    problems = []
    base_raw = _base_text(root, "evals/review/voters.toml")
    live = root / "evals" / "review" / "voters.toml"
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
    return _check("your roster line does not raise itself", problems)


def _vote_preflight(root: Path, author: str) -> list[Check]:
    return [
        check(root, author)
        for check in (
            _check_ledger_loads,
            _check_roster_covers,
            _check_only_the_allowlist_changed,
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
    standing = roster.load(root / "evals" / "review" / "voters.toml").standing_of(
        author
    )
    return (
        "A maintainer reviews every line before this merges. These votes join"
        f" the {standing} series; every published number states the standings"
        " it includes."
    )


#: Every submission kind the CLI offers. The sitting and baseline kinds are
#: #337's later steps; they arrive as entries here, never as branches below.
KINDS: dict[str, Kind] = {
    "vote": Kind(
        preflight=_vote_preflight,
        allowlist=_vote_allowlist,
        title=_vote_title,
        closing=_vote_closing,
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


def command_submit(args: argparse.Namespace) -> int:
    """The four steps, in order, stopping at the first that fails."""
    root = REPO_ROOT
    try:
        author = gh_login(root)
    except SubmitError as exc:
        print(f"cannot read the gh login: {exc}")
        return 1

    print(f"submitting as {author}\n")
    _run(["git", "fetch", "origin"], root)
    checks = KINDS[args.kind].preflight(root, author)
    for check in checks:
        mark = "ok  " if check.passed else "FAIL"
        print(f"  {mark}  {check.name}")
        for problem in check.problems:
            print(f"        {problem}")
    if not all(check.passed for check in checks):
        print("\nnothing opened; fix the failures above and re-run.")
        return 1
    if args.dry_run:
        print("\ndry run: the checklist passed and nothing was staged.")
        return 0

    try:
        url = open_pr(root, args.kind, author)
    except SubmitError as exc:
        print(f"cannot open the PR: {exc}")
        return 1
    print(f"\n{url}")
    print(KINDS[args.kind].closing(root, author))
    return 0
