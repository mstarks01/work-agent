"""Refuse a pull request that changes a code path and states no five-class read.

The pre-merge review reads a diff against the five defect classes of
``docs/agents/code-review.md`` and puts the result in the pull request body,
under the heading :data:`HEADING`, with one entry for each class.
``.github/workflows/five-class.yml`` runs this over each pull request.

A change to a module that decides what a prompt says the code does also needs
a prompt read: the heading :data:`PROMPT_HEADING`, with one entry for each
prompt that the table :data:`TABLE` pairs with the module.

    five_class_read.py <base sha> <head sha>   reads the body from PR_BODY
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from collections import defaultdict
from collections.abc import Iterable, Mapping
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

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
#: and a test can pass on broken code (#1824). ``config/``, ``domains/`` and
#: ``diagram_reader/`` are read by the analysis, and each path that a live workflow sweeps must be one
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
    "diagram_reader/*",
    "tests/*",
    ".githooks/*",
    ".github/workflows/*",
    ".github/scripts/*",
    "pyproject.toml",
    "uv.lock",
)

PROMPT_HEADING = "## Prompt read"

#: The table that pairs each prompt with the modules that decide what it says,
#: beside this script. :func:`main` reads the base branch's copy, as the
#: workflow runs the base branch's copy of this script, and falls back to this
#: one where the base holds none.
TABLE = Path(__file__).with_name("prompt_outcomes.toml")

#: The placeholder in a module pattern or a prompt path that stands for a
#: framework package's directory name. A changed path fills it, so a package
#: that lands later is paired with its own text with no edit to the table.
PACKAGE = "{package}"


def readers(table: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    """Each module pattern against the prompts it decides, from the table.

    An ``outcome`` row pairs each of its modules with its prompt; a ``reader``
    row pairs its module with each of its prompts.
    """
    pairs: dict[str, set[str]] = defaultdict(set)
    for outcome in table.get("outcome", ()):
        for module in outcome["modules"]:
            pairs[module].add(outcome["prompt"])
    for reader in table.get("reader", ()):
        pairs[reader["module"]].update(reader["prompts"])
    return {module: tuple(sorted(prompts)) for module, prompts in sorted(pairs.items())}


def package_of(path: str, pattern: str) -> str | None:
    """The package name ``path`` fills ``pattern``'s placeholder with: the empty
    string for a pattern with none, or None where ``path`` does not match."""
    head, placeholder, _ = pattern.partition(PACKAGE)
    if not placeholder:
        return "" if fnmatchcase(path, pattern) else None
    name = path.removeprefix(head).split("/", 1)[0]
    filled = pattern.replace(PACKAGE, name)
    return name if path.startswith(head) and fnmatchcase(path, filled) else None


def prompts_to_read(
    paths: Iterable[str], pairs: Mapping[str, Iterable[str]], root: Path = Path()
) -> list[str]:
    """Each prompt that ``pairs`` names for a path in ``paths`` and that is on
    disk under ``root``. A package text the package does not carry, such as a
    severity rubric for a record that grades nothing, is not asked for, and a
    prompt that another named glob covers is asked for once, under the glob."""
    paths = list(paths)
    named = {
        prompt.replace(PACKAGE, name)
        for pattern, prompts in pairs.items()
        for name in {package_of(path, pattern) for path in paths}
        if name is not None
        for prompt in prompts
    }
    return sorted(
        prompt
        for prompt in named
        if any(root.glob(prompt))
        and not any(other != prompt and fnmatchcase(prompt, other) for other in named)
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


def section(body: str, heading: str) -> str | None:
    """The rendered text under ``heading`` up to the next heading of its
    level, or None."""
    lines = rendered(body)
    starts = [index for index, line in enumerate(lines) if line.rstrip() == heading]
    if not starts:
        return None
    rest = lines[starts[0] + 1 :]
    end = next(
        (index for index, line in enumerate(rest) if re.match(r"#{1,2} ", line)),
        len(rest),
    )
    return "\n".join(rest[:end])


def missing(
    body: str, heading: str = HEADING, names: Iterable[str] = CLASSES
) -> list[str]:
    """What the body lacks: the heading, each name its section does not name,
    and each name with no text between it and the next entry.

    Asterisks are dropped from the section and from each name, because
    Markdown uses them for bold text and a prompt path uses them for a glob.
    """
    names = tuple(names)
    text = section(body, heading)
    if text is None:
        return [heading]
    folded = " ".join(text.replace("*", "").casefold().split())
    found = sorted(
        (folded.find(_folded(name)), name) for name in names if _folded(name) in folded
    )
    ends = [start for start, _ in found[1:]] + [len(folded)]
    empty = {
        name
        for (start, name), end in zip(found, ends)
        if not re.search(
            r"\w", _NEXT_MARKER.sub("", folded[start + len(_folded(name)) : end])
        )
    }
    named = {name for _, name in found}
    return [
        name if name not in named else f"text after {name}"
        for name in names
        if name not in named or name in empty
    ]


def _folded(name: str) -> str:
    """``name`` as :func:`missing` searches the section for it."""
    return name.replace("*", "").casefold()


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


def table_text(base: str) -> str:
    """The text of :data:`TABLE` at ``base``, or of this script's own copy where
    ``base`` holds none."""
    shown = subprocess.run(
        ["git", "show", f"{base}:.github/scripts/{TABLE.name}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return shown.stdout if shown.returncode == 0 else TABLE.read_text(encoding="utf-8")


def main(argv: list[str]) -> int:
    base, head = argv
    paths = changed_paths(base, head)
    if not changes_code(paths):
        print("The pull request changes no code path, so it needs no five-class read.")
        return 0
    body = os.environ.get("PR_BODY", "")
    status = 0
    lacking = missing(body)
    if lacking:
        print(
            "The pull request changes a code path, and its body lacks: "
            + "; ".join(lacking)
            + f". Add a {HEADING!r} section with one entry for each class of"
            " docs/agents/code-review.md.",
            file=sys.stderr,
        )
        status = 1
    else:
        print("The body holds a five-class read.")
    prompts = prompts_to_read(paths, readers(tomllib.loads(table_text(base))))
    unread = missing(body, PROMPT_HEADING, prompts) if prompts else []
    if unread:
        print(
            "The pull request changes a module that decides what a prompt says,"
            " and its body lacks: "
            + "; ".join(unread)
            + f". Add a {PROMPT_HEADING!r} section with one entry for each of"
            f" {', '.join(prompts)}.",
            file=sys.stderr,
        )
        status = 1
    elif prompts:
        print("The body holds a prompt read.")
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
