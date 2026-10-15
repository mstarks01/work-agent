"""Refuse a pull request that changes the experiment ledger and code together.

Ledger rows and grants go to ``main`` through their own pull request, so a
reverted candidate does not take its record with it (the "Isolation" section of
``.claude/skills/quality-audit/references/experiment-protocol.md``). This
script reads the paths a pull request changes from standard input, each ended
by a NUL byte as ``git diff -z`` writes them, and fails when they hold a ledger
file and a code path. A NUL byte ends a path whatever characters it holds,
where a list of lines quotes some paths, and a quoted path matches no pattern.

A record pull request may also change a guide, an ADR, a test or an archived
run, so only the paths in :data:`CODE` count as code: the paths a candidate
change touches, and that a revert takes away.

Standard library only: ``.github/workflows/ledger.yml`` runs the base branch's
copy with the runner's own Python, before any dependency is installed::

    git diff -z --name-only --no-renames BASE...HEAD \
        | python3 evals/harness/ledger_isolation.py
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from fnmatch import fnmatchcase
from pathlib import PurePosixPath

#: The folders that hold the ledger: one JSONL file of rows for each audit,
#: and one JSONL file of grants for each audit.
LEDGER_DIRS = ("evals/experiments", "evals/experiments/grants")

#: The record of each audit: a code path in it is part of the record.
RECORDS = "evals/experiments/"

#: The code paths, as ``fnmatch`` patterns, in which ``*`` also matches ``/``.
CODE = (
    "src/*",
    "webapp/*",
    "prompts/*",
    "frameworks/*",
    "domains/*",
    "config/*",
    "evals/harness/*",
    "evals/*.py",
    ".github/*",
    ".githooks/*",
    "pyproject.toml",
    "uv.lock",
)


def is_ledger(path: str) -> bool:
    """Whether ``path`` is a file of ledger rows or of grants."""
    posix = PurePosixPath(path)
    return posix.suffix == ".jsonl" and posix.parent.as_posix() in LEDGER_DIRS


def is_code(path: str) -> bool:
    """Whether ``path`` is code outside the audit records."""
    outside = not path.startswith(RECORDS)
    return outside and any(fnmatchcase(path, pattern) for pattern in CODE)


def code_beside_ledger(paths: Iterable[str]) -> tuple[str, ...]:
    """The code paths in ``paths``, where ``paths`` also change the ledger."""
    changed = tuple(paths)
    if not any(is_ledger(path) for path in changed):
        return ()
    return tuple(path for path in changed if is_code(path))


def main() -> int:
    paths = [path for path in sys.stdin.read().split("\0") if path]
    code = code_beside_ledger(paths)
    if code:
        ledger = [path for path in paths if is_ledger(path)]
        print(
            "This pull request changes the experiment ledger and code. Ledger"
            " rows and grants go to main in their own pull request, so a"
            " reverted candidate keeps its record.",
            file=sys.stderr,
        )
        print("Ledger:", *ledger, sep="\n  ", file=sys.stderr)
        print("Code:", *code, sep="\n  ", file=sys.stderr)
    return 1 if code else 0


if __name__ == "__main__":
    sys.exit(main())
