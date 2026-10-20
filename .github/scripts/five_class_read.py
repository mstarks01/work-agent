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
from fnmatch import fnmatchcase

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

#: The paths that hold code or model content, as ``fnmatch`` patterns, where
#: ``*`` also matches ``/``. ``tests/`` is one: the lints and gates live there,
#: and a test can pass on broken code (#1824). ``config/`` and ``domains/`` are
#: read by the analysis, and each path that a live workflow sweeps must be one
#: (``tests/test_five_class_read.py`` holds them together). A change to any
#: other path, such as a doc or a ledger row, needs no five-class read.
CODE_PATHS = (
    "src/*",
    "webapp/*",
    "evals/harness/*",
    "evals/*.py",
    "prompts/*",
    "frameworks/*",
    "domains/*",
    "config/*",
    "tests/*",
    ".githooks/*",
    ".github/workflows/*",
    ".github/scripts/*",
    "pyproject.toml",
)

#: An HTML comment, closed or open to the end of the body. GitHub does not
#: render it.
_COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)

#: A line that opens or closes a fenced code block.
_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})")

#: A list marker at the end of an entry, which belongs to the next entry.
_NEXT_MARKER = re.compile(r"(?:\d+[.)]|[-+])\s*$")


def changes_code(paths: Iterable[str]) -> bool:
    """Whether a path in ``paths`` matches one of :data:`CODE_PATHS`."""
    return any(fnmatchcase(path, pattern) for path in paths for pattern in CODE_PATHS)


def rendered(body: str) -> list[str]:
    """The lines of ``body`` that GitHub renders as prose: no HTML comment and no
    fenced code block."""
    lines = []
    fence = None
    for line in _COMMENT.sub("", body.replace("\r\n", "\n")).split("\n"):
        opener = _FENCE.match(line)
        if fence is None and opener:
            fence = opener.group(1)
        elif fence is not None:
            closer = line.strip()
            if closer.startswith(fence) and not closer.strip(fence[0]):
                fence = None
        else:
            lines.append(line)
    return lines


def section(body: str) -> str | None:
    """The rendered text under :data:`HEADING` up to the next heading of its
    level, or None."""
    lines = rendered(body)
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
    """What the body lacks: the heading, each class its section does not name,
    and each class with no text between its name and the next entry."""
    text = section(body)
    if text is None:
        return [HEADING]
    folded = " ".join(text.replace("*", "").casefold().split())
    found = sorted(
        (folded.find(name.casefold()), name)
        for name in CLASSES
        if name.casefold() in folded
    )
    ends = [start for start, _ in found[1:]] + [len(folded)]
    empty = {
        name
        for (start, name), end in zip(found, ends)
        if not re.search(r"\w", _NEXT_MARKER.sub("", folded[start + len(name) : end]))
    }
    named = {name for _, name in found}
    return [
        name if name not in named else f"text after {name}"
        for name in CLASSES
        if name not in named or name in empty
    ]


def changed_paths(base: str, head: str) -> list[str]:
    """Each path the pull request changes, from the merge base of the two
    commits. A rename lists both its old and its new path."""
    listed = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", "-z", f"{base}...{head}"],
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
