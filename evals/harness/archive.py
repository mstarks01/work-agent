"""The bytes each archived file's producer writes, in one table.

**The archive is not one encoding.** Five writers lay files into a **Baseline**
directory and they do not agree, because each one reaches for whatever its own
call makes convenient:

===================  ====================================  ======================
file                 producer                              spelling
===================  ====================================  ======================
``*.report.json``    ``bundle.write_reports``              UTF-8
``*.drafts.json``    ``bundle.write_reports``              escaped ASCII
``*.proposals.json`` ``bundle.write_reports``              escaped ASCII
the sweep artifact   ``run.py`` sweep, and ``run.py``      escaped ASCII
                     ``score``, which rewrites it
``baseline.json``    ``baseline.assemble``                 UTF-8, keys sorted
===================  ====================================  ======================

Two of those sit in one directory, and the report and the drafts beside it are
written three lines apart in one function. So "the archive is UTF-8" is a rule a
reader learns from whichever writer they happened to open, and it is wrong for
three of the five files.

**It matters because a Baseline seals these bytes.** ``_file_digests`` digests
every file a sweep owns, the artifact's own filename is a digest of its
contents, and ``verify`` recomputes whatever is on disk — so a rewrite in the
wrong spelling parses to the same value, re-stamps the seal, and reports
nothing.

Every writer here calls :func:`archive_bytes`, so the table is the rule and no
site carries a second copy of it. A script that walks the archive reads
:func:`kind_of` to ask what it is holding, and refuses a file whose producer
nobody has declared rather than guessing one.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

__all__ = [
    "ARCHIVE_SPELLINGS",
    "KIND_NAMES",
    "KIND_SUFFIXES",
    "UnknownArchiveKind",
    "archive_bytes",
    "kind_of",
]


def _utf8(value: Any) -> str:
    """What ``Report.model_dump_json(indent=2)`` emits, spelled through :mod:`json`.

    The equality is asserted rather than assumed —
    ``tests/test_archive_encoding_lints.py`` compares this call against pydantic's
    own output on a real archived report, so a pydantic release that changed its
    indent or its escaping fails there instead of quietly re-encoding the archive.
    """
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _escaped(value: Any) -> str:
    """``json.dumps`` at its defaults, which escape every non-ASCII character."""
    return json.dumps(value, indent=2) + "\n"


def _sorted_utf8(value: Any) -> str:
    """UTF-8 with keys sorted, which is the manifest alone.

    ``sort_keys`` is the fact a re-encoding loses most visibly: a manifest
    rewritten without it keeps every byte of its values and reorders every key
    in the file.
    """
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


#: One entry per producer, keyed by the kind of file it writes. A writer names
#: its own kind, so nothing infers a spelling from a path.
ARCHIVE_SPELLINGS: dict[str, Callable[[Any], str]] = {
    "report": _utf8,
    "drafts": _escaped,
    "proposals": _escaped,
    "artifact": _escaped,
    "manifest": _sorted_utf8,
}

#: The kinds a **reader** recognises by a filename's whole name. A manifest is
#: named ``baseline.json`` exactly, and matching it as a suffix would read any
#: stray file whose name merely ends that way as one, and sort every key in it.
KIND_NAMES: dict[str, str] = {"baseline.json": "manifest"}

#: The kinds a reader recognises by a suffix, which is how ``bundle`` names the
#: three files it writes per case. The artifact is absent on purpose: it is
#: named ``<stem>.json`` and no suffix separates it from any other JSON file, so
#: the only honest answer for one is the name a Baseline manifest records in its
#: ``artifact`` field. A reader that guessed instead would hand the artifact's
#: spelling to every unrelated file it walked past.
KIND_SUFFIXES: dict[str, str] = {
    ".report.json": "report",
    ".drafts.json": "drafts",
    ".proposals.json": "proposals",
}


class UnknownArchiveKind(LookupError):
    """A file whose producer this table does not declare."""


def kind_of(name: str) -> str | None:
    """Which kind ``name`` is, or ``None`` where the filename cannot say.

    ``None`` is a real answer and not a failure: a sweep artifact and a file
    that belongs to no producer here are both spelled ``<something>.json``, and
    telling them apart needs a manifest rather than a name. A caller that gets
    ``None`` asks its registry or refuses — see :func:`archive_bytes`.

    ``name`` is a bare filename. A caller holding a path passes ``path.name``:
    a suffix match against a whole path would read a directory component, and
    ``.reports`` directories are named after the artifact they sit beside.
    """
    if name in KIND_NAMES:
        return KIND_NAMES[name]
    for suffix, kind in KIND_SUFFIXES.items():
        if name.endswith(suffix):
            return kind
    return None


def archive_bytes(kind: str, value: Any) -> str:
    """``value`` in the bytes the producer of ``kind`` writes.

    Raises :class:`UnknownArchiveKind` for a kind the table does not carry, so a
    file kind added to the archive tomorrow stops the writer that added it
    rather than inheriting whichever spelling sat nearest.
    """
    if kind not in ARCHIVE_SPELLINGS:
        raise UnknownArchiveKind(
            f"no producer is declared for archive kind {kind!r};"
            f" declare it in evals/harness/archive.py, which is where the"
            f" archive's encodings are decided. Known kinds:"
            f" {sorted(ARCHIVE_SPELLINGS)}"
        )
    return ARCHIVE_SPELLINGS[kind](value)
