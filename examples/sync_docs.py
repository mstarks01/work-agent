"""Generate the docs' code blocks from ``examples/``, and check they stayed generated.

``examples/`` is the single source of truth for every code block in the prose.
No Markdown file in this repo carries hand-written engine code: a block is
declared as an include, and this tool fills it from a named region of a real,
runnable file.

The mechanism is a ``--write`` / ``--check`` pair rather than render-time
injection because there is **no docs build** here — no mkdocs, no Sphinx; the
``.md`` files are read directly on GitHub — so the generated block has to be
committed. That is the same shape as ``evals/verify_corpus.py``: hand-runnable
for authors, unbypassable in CI via ``tests/test_docs_lints.py``.

Declare an include in Markdown like this::

    <!-- docs-include: examples/embed.py#embed -->
    ```python
    ...generated, do not edit...
    ```
    <!-- /docs-include -->

and mark the region in the source::

    # docs-region: embed
    ...
    # docs-region-end: embed

Drop the ``#region`` suffix to include a whole file — that is how the sample
description at ``docs/Integration-Guide.md`` stays byte-identical to the
``examples/orders.md`` the web app actually loads.

Usage::

    uv run python examples/sync_docs.py --write    # regenerate the blocks
    uv run python examples/sync_docs.py --check    # fail if any has drifted
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Every Markdown file that may carry an include. Scanned wholesale rather than
# listed file by file, so a new page cannot quietly opt out of the lint.
DOC_GLOBS = ("README.md", "docs/*.md", "examples/*.md")

# The fence language each source kind renders as. An unlisted extension is an
# error rather than a guess: a wrong language tag is silent on GitHub.
FENCE_LANGUAGES = {".py": "python", ".md": "text", ".toml": "toml"}

_INCLUDE = re.compile(
    r"<!-- docs-include: (?P<source>[^\s#]+)(?:#(?P<region>[\w./-]+))? -->\n"
    r"```(?P<lang>[\w-]*)\n"
    r"(?P<body>.*?)"
    r"```\n"
    r"<!-- /docs-include -->",
    re.DOTALL,
)


class IncludeError(Exception):
    """An include names a source or region that cannot be resolved."""


def _region_markers(name: str) -> tuple[str, str]:
    return f"# docs-region: {name}", f"# docs-region-end: {name}"


def extract(source: str, region: str | None) -> str:
    """The text an include pulls in: a whole file, or one marked region of it."""
    path = REPO_ROOT / source
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise IncludeError(f"{source}: cannot be read ({exc})") from exc

    if region is None:
        return text

    start_marker, end_marker = _region_markers(region)
    lines = text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.strip() == start_marker]
    ends = [i for i, line in enumerate(lines) if line.strip() == end_marker]
    if len(starts) != 1 or len(ends) != 1:
        raise IncludeError(
            f"{source}: expected exactly one '{start_marker}' and one "
            f"'{end_marker}', found {len(starts)} and {len(ends)}"
        )
    if ends[0] < starts[0]:
        raise IncludeError(f"{source}: region {region!r} ends before it starts")
    body = lines[starts[0] + 1 : ends[0]]
    # An end marker sitting just above a top-level definition carries the two
    # blank lines the formatter puts there. That padding is the file's layout,
    # not the snippet's, so it must not reach the fence.
    while body and not body[-1].strip():
        body.pop()
    return "".join(body)


def _fence_language(source: str) -> str:
    suffix = Path(source).suffix
    try:
        return FENCE_LANGUAGES[suffix]
    except KeyError:
        raise IncludeError(
            f"{source}: no fence language known for {suffix!r} "
            f"(known: {', '.join(sorted(FENCE_LANGUAGES))})"
        ) from None


def render(doc_text: str) -> str:
    """The document with every include's fenced block filled from its source."""

    def replace(match: re.Match[str]) -> str:
        source = match.group("source")
        region = match.group("region")
        body = extract(source, region)
        if not body.endswith("\n"):
            body += "\n"
        anchor = f"{source}#{region}" if region else source
        return (
            f"<!-- docs-include: {anchor} -->\n"
            f"```{_fence_language(source)}\n"
            f"{body}"
            f"```\n"
            f"<!-- /docs-include -->"
        )

    return _INCLUDE.sub(replace, doc_text)


def doc_paths() -> list[Path]:
    """Every Markdown file in scope, deduplicated and stably ordered."""
    found = {path for glob in DOC_GLOBS for path in REPO_ROOT.glob(glob)}
    return sorted(found)


def sync(*, write: bool) -> list[str]:
    """Regenerate or verify every include. Returns the paths that were stale."""
    stale = []
    for path in doc_paths():
        current = path.read_text(encoding="utf-8")
        updated = render(current)
        if updated == current:
            continue
        stale.append(str(path.relative_to(REPO_ROOT)))
        if write:
            path.write_text(updated, encoding="utf-8")
    return stale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--write", action="store_true", help="regenerate every included block"
    )
    mode.add_argument(
        "--check", action="store_true", help="fail if any block has drifted"
    )
    args = parser.parse_args(argv)

    try:
        stale = sync(write=args.write)
    except IncludeError as exc:
        print(f"docs include error: {exc}", file=sys.stderr)
        return 2

    if not stale:
        print("docs includes are in sync")
        return 0

    if args.write:
        print("rewrote: " + ", ".join(stale))
        return 0

    print(
        "these documents have drifted from examples/: "
        + ", ".join(stale)
        + "\nRun: uv run python examples/sync_docs.py --write",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
