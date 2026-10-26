"""Refuse a pull request that changes a code path and states no five-class read.

The pre-merge review reads a diff against the five defect classes of
``docs/agents/code-review.md`` and puts the result in the pull request body,
under the heading :data:`HEADING`, with one entry for each class.
``.github/workflows/five-class.yml`` runs this over each pull request.

A change to a module that decides what a prompt says the code does also needs
a prompt read: the heading :data:`PROMPT_HEADING`, with one entry for each
prompt that :data:`PROMPT_READERS` names for the module.

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
    "uv.lock",
)

PROMPT_HEADING = "## Prompt read"

#: Each module that decides an outcome a prompt states, as an ``fnmatch``
#: pattern, against the prompts that state its outcomes. A prompt sentence
#: about a refusal, a repair, a drop or an open question is true only while the
#: module does that, so a change to the module needs a read of the prompts.
#: ``tests/test_five_class_read.py`` holds each pattern and each prompt to a
#: real file, and holds every prompt to some module.
PROMPT_READERS: dict[str, tuple[str, ...]] = {
    "src/analysis_service/actions.py": (
        "frameworks/stride/output.md",
        "frameworks/stride/lanes/*/skill.md",
    ),
    "src/analysis_service/assertions.py": ("prompts/assert.md", "prompts/analyze.md"),
    "src/analysis_service/bands.py": ("frameworks/stride/severity_rubric.md",),
    "src/analysis_service/capabilities.py": ("prompts/capabilities.md",),
    "src/analysis_service/capability_reader.py": ("prompts/capabilities.md",),
    "src/analysis_service/claims.py": (
        "prompts/analyze.md",
        "prompts/critic.md",
        "frameworks/*/output.md",
    ),
    "src/analysis_service/compact.py": ("prompts/extract-compact.md",),
    "src/analysis_service/critic.py": (
        "prompts/critic.md",
        "prompts/recritic.md",
        "frameworks/*/critic.md",
    ),
    "src/analysis_service/evidence.py": (
        "prompts/analyze.md",
        "frameworks/*/lanes/*/exemplars.md",
    ),
    "src/analysis_service/fan_in.py": (
        "prompts/analyze.md",
        "frameworks/*/output.md",
        "frameworks/*/lanes/*/exemplars.md",
    ),
    "src/analysis_service/frameworks/asvs/*": (
        "frameworks/asvs/output.md",
        "frameworks/asvs/critic.md",
        "frameworks/asvs/lanes/*/skill.md",
    ),
    "src/analysis_service/frameworks/stride/*": (
        "frameworks/stride/output.md",
        "frameworks/stride/critic.md",
        "frameworks/stride/lanes/*/skill.md",
    ),
    "src/analysis_service/graph.py": (
        "prompts/extract-compact.md",
        "prompts/repair.md",
        "prompts/recritic.md",
    ),
    "src/analysis_service/grounding.py": ("prompts/analyze.md", "prompts/extract.md"),
    "src/analysis_service/prompts.py": (
        "prompts/assert.md",
        "prompts/capabilities.md",
        "prompts/critic.md",
        "frameworks/stride/lane_closing.md",
    ),
    "src/analysis_service/question_kinds.py": (
        "prompts/critic.md",
        "prompts/recritic.md",
    ),
    "src/analysis_service/system_model.py": (
        "prompts/extract.md",
        "prompts/extract-compact.md",
        "prompts/analyze.md",
    ),
    "src/analysis_service/validation.py": (
        "prompts/extract.md",
        "prompts/extract-compact.md",
        "prompts/repair.md",
    ),
}

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


def prompts_to_read(paths: Iterable[str]) -> list[str]:
    """Each prompt that :data:`PROMPT_READERS` names for a path in ``paths``."""
    return sorted(
        {
            prompt
            for pattern, prompts in PROMPT_READERS.items()
            if any(fnmatchcase(path, pattern) for path in paths)
            for prompt in prompts
        }
    )


def section(body: str, heading: str = HEADING) -> str | None:
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
    Markdown spends them on bold text and a prompt path spends them on a glob.
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
    prompts = prompts_to_read(paths)
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
