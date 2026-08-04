"""Loading Markdown content — skills and prompts — from a directory of files.

One implementation serves both content roots: the loader takes a directory in
and hands named Markdown text out, with no templating and no caching.

Loading fails closed. Repo Markdown is trusted content baked into the image,
but a missing file, a heading that deviates from the fixed set, or a name
escaping the root raises instead of degrading silently — content that
silently drops out of an agent's context is a recall loss no one would
notice. Section structure and token caps are enforced by the CI lints over
``skills/**/*.md`` and ``prompts/**/*.md``.
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


def split_sections(text: str) -> dict[str, str]:
    """Split a Markdown file into its H2 sections, preserving order.

    Headings are taken verbatim (everything after ``## ``) so the lints can
    enforce exact strings. Duplicate or empty headings raise
    :class:`MarkdownFormatError`.
    """
    sections: dict[str, str] = {}
    heading: str | None = None
    body: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if heading is not None:
                sections[heading] = "\n".join(body).strip()
            heading = line[3:]
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
    ``"stride/spoofing"``, ``"shared/severity_rubric"``, ``"analyze"`` or
    ``"exemplars/tampering"``. This is the canonical directory-in,
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

    def load(self, name: str) -> str:
        path = (self._root / f"{name}.md").resolve()
        # A name resolving outside the root (traversal) is treated the same
        # as absent — deny, don't reveal what lies outside.
        if not path.is_relative_to(self._root) or not path.is_file():
            raise MarkdownNotFoundError(
                f"no markdown named {name!r} under {self._root}"
            )
        return path.read_text(encoding="utf-8")
