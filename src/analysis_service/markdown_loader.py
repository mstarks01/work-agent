"""Loading Markdown content — skills and prompts — from a directory of files.

One implementation serves both content roots. The loader takes a directory in
and hands named Markdown text out, with no templating and no caching.

Loading fails closed. Repo Markdown is trusted content baked into the image, but
a missing file, a heading that deviates from the fixed set, or a name that
escapes the root raises rather than degrading silently. Content that drops out
of an agent's context silently is a recall loss nobody would notice. The CI
lints over ``skills/**/*.md`` and ``prompts/**/*.md`` enforce section structure
and token caps.
"""

from __future__ import annotations

import math
from pathlib import Path


class MarkdownNotFoundError(LookupError):
    """No Markdown file exists for the requested name."""


class MarkdownFormatError(ValueError):
    """A Markdown file deviates from the section structure required of it."""


def estimate_tokens(text: str) -> int:
    """Coarse token estimate (words x 4/3), the convention the caps assume."""
    return math.ceil(len(text.split()) * 4 / 3)


#: Every way :meth:`pathlib.Path.resolve` fails on Python 3.12: ``OSError``
#: for a path the process cannot walk, ``RuntimeError`` for a symlink loop,
#: and ``ValueError`` for an embedded NUL. A handler that lists the first alone
#: let a committed ``source.md -> source.md`` raise a traceback through the
#: startup gate, the loader, and the lint that runs over a stranger's pull
#: request. Every reader that resolves and contains catches this set.
RESOLVE_ERRORS = (OSError, RuntimeError, ValueError)


def _inside(root: Path, path: Path) -> bool:
    """Whether ``path`` resolves to a readable file under ``root``.

    One reader for "is this file mine to read". A name resolving outside the
    root -- by traversal or by symlink -- is treated the same as absent: deny,
    and reveal nothing about what lies outside. A name that cannot be resolved
    at all is absent too.
    """
    try:
        resolved = path.resolve()
        return resolved.is_relative_to(root.resolve()) and resolved.is_file()
    except RESOLVE_ERRORS:
        return False


def split_sections(text: str) -> dict[str, str]:
    """Split a Markdown file into its H2 sections, preserving order.

    Headings are everything after ``## ``, with surrounding whitespace removed,
    which is the same reading the package gate's own heading lint applies. Two
    readings of one heading is a file that passes the gate and fails the load. Duplicate or empty headings raise
    :class:`MarkdownFormatError`.
    """
    sections: dict[str, str] = {}
    heading: str | None = None
    body: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if heading is not None:
                sections[heading] = "\n".join(body).strip()
            # Stripped, because the lint that gates these files strips.
            # `_heading_issues` compared `line[3:].strip()` and this stored
            # `line[3:]`, so one trailing space after `## Scope` passed the gate
            # and then raised here -- the critic's lane digest is read out of
            # that section, so the package started and its first job died.
            heading = line[3:].strip()
            if not heading.strip():
                raise MarkdownFormatError("empty H2 heading")
            if heading in sections:
                raise MarkdownFormatError(f"duplicate section '## {heading}'")
            body = []
        elif heading is not None:
            body.append(line)
    if heading is not None:
        sections[heading] = "\n".join(body).strip()
    return sections


def extract_section(text: str, heading: str) -> str:
    """The body of one named H2 section; missing or empty is a format error."""
    sections = split_sections(text)
    if heading not in sections:
        raise MarkdownFormatError(f"missing section '## {heading}'")
    if not sections[heading]:
        raise MarkdownFormatError(f"section '## {heading}' is empty")
    return sections[heading]


class MarkdownLoader:
    """Loads Markdown from a directory of files.

    Names are root-relative POSIX paths without the ``.md`` suffix, e.g.
    ``"<framework>/<lane>"``, ``"shared/severity_rubric"``, ``"analyze"`` or
    ``"exemplars/<lane>"``. This is the canonical directory-in,
    named-items-out interface for the service, used for both skills and
    prompts.
    """

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root).resolve()
        if not self._root.is_dir():
            raise FileNotFoundError(f"markdown root is not a directory: {root}")

    @property
    def root(self) -> Path:
        return self._root

    def names(self) -> list[str]:
        """All loadable names, sorted."""
        return sorted(
            path.relative_to(self._root).with_suffix("").as_posix()
            for path in self._root.rglob("*.md")
        )

    def readable(self, name: str) -> bool:
        """Whether :meth:`load` would return this name's text.

        Exported so a caller checking a file exists asks the same question the
        loader will ask. The framework package gate asked ``is_file()``, which
        follows a symlink out of the root that :meth:`load` refuses -- so a
        package passed startup validation and failed on its first job.
        """
        return _inside(self._root, self._root / f"{name}.md")

    def load(self, name: str) -> str:
        path = self._root / f"{name}.md"
        # A name resolving outside the root (traversal) is treated the same
        # as absent — deny, don't reveal what lies outside. `_inside` does the
        # resolving, so a name that cannot be resolved is refused the same way.
        if not _inside(self._root, path):
            raise MarkdownNotFoundError(
                f"no markdown named {name!r} under {self._root}"
            )
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise MarkdownFormatError(f"{name}.md is not UTF-8: {exc}") from exc
