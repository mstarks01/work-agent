"""One command opens every contribution PR: ``submit <kind>``.

The spine is #325's, and it is the same four steps for every kind: bind the
authenticated ``gh`` login, run this kind's CI checks locally as a checklist,
package the kind's allowlist on a fresh branch cut from ``origin/main``, and
open the PR through ``gh``. ``--dry-run`` stops after the checklist, which is
the contributor's local CI — a red PR should be rare because the same checks
already ran on their machine.

**Which kinds exist is a table**, :data:`KINDS`, never a branch: the baseline
kind arrives by adding an entry (#337), and the CLI offers exactly the
table's keys. A kind that is not in the table is not a choice, rather than a
stub that refuses.

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
import hashlib
import json
import subprocess
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import ValidationError

from evals.harness import baseline, ledger, roster
from evals.harness.artifact import ProvenanceError
from evals.harness.reference import CaseSitting

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
    #: table the CLI offers rather than by a second list somebody maintains.
    prefix: str = ""
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


# --- the sitting kind ---------------------------------------------------------

#: Where the debt list lives; a sitting PR deletes its case's line from it.
CASE_REVIEW_TEST = "tests/test_case_review.py"


def _sitting_case(root: Path) -> str | None:
    """The one case this submission touches, or None when it is not one."""
    cases = {
        rel.split("/")[2]
        for rel in _changed_paths(root)
        if rel.startswith("evals/corpus/") and rel.count("/") >= 3
    }
    return cases.pop() if len(cases) == 1 else None


def _sitting_allowlist(root: Path, author: str) -> list[str]:
    case = _sitting_case(root)
    changed = [
        rel
        for rel in _changed_paths(root)
        if case is not None and rel.startswith(f"evals/corpus/{case}/")
    ]
    return [*changed, CASE_REVIEW_TEST, "evals/review/voters.toml"]


def _new_sittings(root: Path, case: str) -> tuple[list[dict], list[str]]:
    """The entries this PR appends, and what is wrong with the append.

    ``reviews`` is append-only (#327): the base ref's entries must survive as
    an exact prefix, and only what follows them is this submission's.
    """
    raw = json.loads(
        (root / "evals" / "corpus" / case / "case.json").read_text(encoding="utf-8")
    )
    reviews = raw.get("reviews", [])
    base_raw = _base_text(root, f"evals/corpus/{case}/case.json")
    base_reviews = json.loads(base_raw).get("reviews", []) if base_raw else []
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


def _check_one_case(root: Path, author: str) -> Check:
    case = _sitting_case(root)
    return _check(
        "the change is one case directory",
        [] if case else ["a sitting PR touches exactly one case under evals/corpus/"],
    )


def _check_sitting_scope(root: Path, author: str) -> Check:
    allowed = set(_sitting_allowlist(root, author))
    strays = [rel for rel in _changed_paths(root) if rel not in allowed]
    return _check(
        "nothing outside this kind's allowlist changed",
        [f"{rel} is changed but not part of a sitting" for rel in strays],
    )


def _check_sitting_is_yours(root: Path, author: str) -> Check:
    """#327 check 1, failed early: every appended entry names the author."""
    case = _sitting_case(root)
    if case is None:
        return _check("every appended sitting names you", [])
    new, problems = _new_sittings(root, case)
    for entry in new:
        try:
            sitting = CaseSitting.model_validate(entry)
        except ValidationError as exc:
            problems.append(f"{case}/case.json: an appended entry is malformed: {exc}")
            continue
        if sitting.reviewer != author:
            problems.append(
                f"an appended entry names {sitting.reviewer!r}; you are"
                f" {author!r}, and a sitting only enters through its own"
                " reviewer's PR (#320)"
            )
    return _check("every appended sitting names you", problems)


def _check_sitting_evidence(root: Path, author: str) -> Check:
    """The digests match the tree, and the filled document is committed."""
    case = _sitting_case(root)
    if case is None:
        return _check("the digests and the document hold", [])
    case_dir = root / "evals" / "corpus" / case
    new, problems = _new_sittings(root, case)
    for entry in new:
        try:
            sitting = CaseSitting.model_validate(entry)
        except ValidationError:
            continue  # named by the naming check; one problem, one message
        for record in sitting.read:
            target = case_dir / record.file
            if not target.is_file():
                problems.append(f"{record.file}: read but not in the case directory")
            elif hashlib.sha256(target.read_bytes()).hexdigest() != record.sha256:
                problems.append(
                    f"{record.file}: the digest does not match the file; the"
                    " entry signs the bytes that merge, so recompute it"
                )
        if not (case_dir / sitting.document).is_file():
            problems.append(
                f"{sitting.document}: the filled document is the evidence,"
                " and it is not committed beside the case"
            )
    return _check("the digests and the document hold", problems)


def _check_sitting_covers(root: Path, author: str) -> Check:
    """#327's derived rule: the read covers every framework the case declares."""
    case = _sitting_case(root)
    if case is None:
        return _check("the sitting covers every declared framework", [])
    raw = json.loads(
        (root / "evals" / "corpus" / case / "case.json").read_text(encoding="utf-8")
    )
    required = {"source.md", "model.json"} | {
        f"claims/{declared['name']}.json" for declared in raw.get("frameworks", [])
    }
    new, problems = _new_sittings(root, case)
    read = {
        record.get("file")
        for entry in new
        if isinstance(entry, dict)
        for record in entry.get("read", [])
        if isinstance(record, dict)
    }
    if new and (gap := sorted(required - read)):
        problems.append(
            f"the appended sitting leaves {gap} unread; one sitting signs the"
            " model and every declared framework's reference set together"
        )
    return _check("the sitting covers every declared framework", problems)


def _check_sitting_clears_debt(root: Path, author: str) -> Check:
    case = _sitting_case(root)
    if case is None:
        return _check("the case's UNREVIEWED line is gone", [])
    text = (root / CASE_REVIEW_TEST).read_text(encoding="utf-8")
    return _check(
        "the case's UNREVIEWED line is gone",
        [f'delete the "{case}" line from UNREVIEWED in {CASE_REVIEW_TEST}']
        if f'"{case}":' in text
        else [],
    )


def _check_author_rostered(root: Path, author: str) -> Check:
    problems = []
    try:
        if author not in roster.load(root / "evals" / "review" / "voters.toml"):
            problems.append(
                f"{author!r} has no roster line; add yourself to"
                ' evals/review/voters.toml with standing "contributor"'
            )
    except roster.RosterError as exc:
        problems.append(str(exc))
    return _check("you have a roster line", problems)


def _sitting_preflight(root: Path, author: str) -> list[Check]:
    return [
        check(root, author)
        for check in (
            _check_one_case,
            _check_sitting_scope,
            _check_sitting_is_yours,
            _check_sitting_evidence,
            _check_sitting_covers,
            _check_sitting_clears_debt,
            _check_author_rostered,
            _check_no_self_raise,
        )
    ]


def _sitting_title(root: Path, author: str) -> str:
    return f"Sitting: {_sitting_case(root)} by {author}"


def _sitting_closing(root: Path, author: str) -> str:
    standing = roster.load(root / "evals" / "review" / "voters.toml").standing_of(
        author
    )
    return (
        "A maintainer reviews every line before this merges. This sitting"
        f" clears the case from UNREVIEWED, and standing {standing!r} labels"
        " the read — the debt means nobody read it, and a labelled read"
        " answers that."
    )


# --- the baseline kind ---------------------------------------------------------


def _baseline_prepare(root: Path, author: str, args: argparse.Namespace) -> None:
    """Assemble ``--artifact`` sweeps into their Baseline directory first."""
    paths = [Path(raw) for raw in getattr(args, "artifact", None) or []]
    if paths:
        baseline.assemble(root, author, paths)


def _baseline_dir(root: Path) -> str | None:
    """The one Baseline this submission touches, or None when it is not one."""
    names = {
        rel.split("/")[2]
        for rel in _changed_paths(root)
        if rel.startswith("evals/baselines/") and rel.count("/") >= 3
    }
    return names.pop() if len(names) == 1 else None


def _baseline_allowlist(root: Path, author: str) -> list[str]:
    name = _baseline_dir(root)
    changed = [
        rel
        for rel in _changed_paths(root)
        if name is not None and rel.startswith(f"evals/baselines/{name}/")
    ]
    return [*changed, "evals/review/voters.toml"]


def _check_one_baseline(root: Path, author: str) -> Check:
    return _check(
        "the change is one Baseline directory",
        []
        if _baseline_dir(root)
        else ["a baseline PR touches exactly one directory under evals/baselines/"],
    )


def _check_baseline_scope(root: Path, author: str) -> Check:
    allowed = set(_baseline_allowlist(root, author))
    strays = [rel for rel in _changed_paths(root) if rel not in allowed]
    return _check(
        "nothing outside this kind's allowlist changed",
        [f"{rel} is changed but not part of a baseline submission" for rel in strays],
    )


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
            _check_one_baseline,
            _check_baseline_scope,
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
    models = ", ".join(
        f"{tier}: {model}" for tier, model in sorted(identity.get("models", {}).items())
    )
    costs = [entry.get("cost", {}) for entry in sweeps]
    total = sum(float(cost.get("actual_usd", 0.0)) for cost in costs)
    unpriced = sorted({model for cost in costs for model in cost.get("unpriced", ())})
    standing = roster.load(root / "evals" / "review" / "voters.toml").standing_of(
        author
    )
    lines = [
        (
            f"{len(sweeps)} sweep(s) at commit {identity.get('repo_commit', '')[:12]},"
            f" frameworks {', '.join(identity.get('frameworks', ()))}; {models}."
        ),
        f"Recorded actual cost across the directory: ${total:.2f}"
        + (f"; unpriced models: {', '.join(unpriced)}" if unpriced else "")
        + ".",
        (
            "A maintainer reviews every line before this merges. Standing"
            f" {standing!r} labels the submission; every published number"
            " states the standings behind its Baseline."
        ),
    ]
    return "\n".join(lines)


#: Every submission kind the CLI offers. A new kind arrives as an entry here,
#: never as a branch in the spine.
KINDS: dict[str, Kind] = {
    "vote": Kind(
        prefix="evals/review/votes/",
        preflight=_vote_preflight,
        allowlist=_vote_allowlist,
        title=_vote_title,
        closing=_vote_closing,
    ),
    "sitting": Kind(
        prefix="evals/corpus/",
        preflight=_sitting_preflight,
        allowlist=_sitting_allowlist,
        title=_sitting_title,
        closing=_sitting_closing,
    ),
    "baseline": Kind(
        prefix="evals/baselines/",
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

    checks = list(KINDS[kind].preflight(root, author)) if kind else []
    if kind is None:
        # A roster line with no submission behind it still may not raise its
        # own author's standing — the one edit that is dangerous alone.
        if "evals/review/voters.toml" in _changed_paths(root):
            checks = [_check_no_self_raise(root, author)]
        else:
            print("no contribution in this diff; nothing to check")
            return 0

    print(f"checking this {kind or 'roster'} PR as {author}\n")
    for check in checks:
        print(f"  {'ok  ' if check.passed else 'FAIL'}  {check.name}")
        for problem in check.problems:
            print(f"        {problem}")
    if all(check.passed for check in checks):
        return 0
    print(
        "\nThe contributor's own `submit --dry-run` runs these same checks,"
        " so a red result here usually means the branch moved under them."
    )
    return 1


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
    kind = KINDS[args.kind]
    if kind.prepare is not None:
        try:
            kind.prepare(root, author, args)
        except (SubmitError, ProvenanceError, baseline.BaselineError) as exc:
            print(f"cannot assemble the submission: {exc}")
            return 1
    checks = kind.preflight(root, author)
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
