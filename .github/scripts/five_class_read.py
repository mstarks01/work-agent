"""Refuse a pull request that changes a code path and states no five-class read.

The pre-merge review reads a diff against the five defect classes of
``docs/agents/code-review.md`` and puts the result in the pull request body,
under the heading :data:`HEADING`, with one entry for each class.
``.github/workflows/five-class.yml`` runs this over each pull request.

    five_class_read.py <base sha> <head sha>   reads the body from PR_BODY
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Iterable

HEADING = "## Five-class read"

#: The five defect classes, as the class table of ``docs/agents/code-review.md``
#: names them, in its order.
CLASSES = (
    "One rule, two readers",
    "A table with a hole",
    "A shape not listed",
    "A bound predicted, not measured",
    "A fact with no reader",
)

#: The paths that hold code. ``tests/`` is one: the lints and gates live there,
#: and a test can pass on broken code (#1824). A change to any other path, such
#: as a doc or a ledger row, needs no five-class read.
CODE_PATHS = (
    "src/",
    "webapp/",
    "evals/harness/",
    "prompts/",
    "frameworks/",
    "tests/",
    ".githooks/",
    ".github/workflows/",
    ".github/scripts/",
)


def changes_code(paths: Iterable[str]) -> bool:
    """Whether a path in ``paths`` sits under one of :data:`CODE_PATHS`."""
    return any(path.startswith(CODE_PATHS) for path in paths)


def section(body: str) -> str | None:
    """The text under :data:`HEADING` up to the next heading of its level, or None."""
    lines = body.replace("\r\n", "\n").split("\n")
    starts = [index for index, line in enumerate(lines) if line.rstrip() == HEADING]
    if not starts:
        return None
    rest = lines[starts[0] + 1 :]
    end = next(
        (index for index, line in enumerate(rest) if re.match(r"#{1,2} ", line)),
        len(rest),
    )
    return "\n".join(rest[:end])


def missing(body: str) -> list[str]:
    """What the body lacks: the heading, or each class its section does not name."""
    text = section(body)
    if text is None:
        return [HEADING]
    folded = " ".join(text.replace("*", "").casefold().split())
    return [name for name in CLASSES if name.casefold() not in folded]


def changed_paths(base: str, head: str) -> list[str]:
    """Each path the pull request changes, from the merge base of the two commits."""
    listed = subprocess.run(
        ["git", "diff", "--name-only", "-z", f"{base}...{head}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [path for path in listed.split("\0") if path]


def main(argv: list[str]) -> int:
    base, head = argv
    if not changes_code(changed_paths(base, head)):
        print("The pull request changes no code path, so it needs no five-class read.")
        return 0
    lacking = missing(os.environ.get("PR_BODY", ""))
    if lacking:
        print(
            "The pull request changes a code path, and its body lacks: "
            + "; ".join(lacking)
            + f". Add a {HEADING!r} section with one entry for each class of"
            " docs/agents/code-review.md.",
            file=sys.stderr,
        )
        return 1
    print("The body holds a five-class read.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
