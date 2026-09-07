"""The ASVS 5.0.0 requirement catalog, as this package's own private data.

A package is a catalog it does not own plus a profile it does. ASVS publishes a
machine-readable requirement set, so this package carries it. No contract member
names it, no service module reads it, and no other package sees it. That is the
rule :mod:`analysis_service.frameworks` states, and it is why the catalog is a
module here rather than a tenth member on
:class:`~analysis_service.frameworks.FrameworkPackage`.

The data sits beside this module in ``catalog.json``, copied from the flat JSON
the ASVS project publishes at tag ``v5.0.0``. There are five fields per
requirement, which is every field the standard publishes: chapter, section,
identifier, description and level. There is no applies-when field, no tag and no
technology list, so the applicability rules in
:mod:`analysis_service.frameworks.asvs.rules` are this repository's own.

The catalog checks itself at import. A truncated file, or a chapter with no
requirements, would otherwise reach a ``strong``-tier prompt, and the package
gate cannot see a catalog no contract member names. The check therefore runs
where the data is read, and it raises the same error a malformed package raises.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal, get_args

from analysis_service.frameworks import FrameworkPackageError

__all__ = [
    "ASVS_LEVELS",
    "ASVS_VERSION",
    "CATALOG_SHA256",
    "CHAPTERS",
    "CHAPTER_NUMBERS",
    "LANES",
    "REQUIREMENTS",
    "AsvsLevel",
    "Chapter",
    "Requirement",
    "is_published_requirement",
    "provenance_issues",
    "requirement_id",
    "requirements_for",
]

#: The release of the standard this catalog carries. It names **the standard's
#: own version**, unlike STRIDE's, which names this repo's ruleset: ASVS is a
#: published standard and STRIDE is a method. Every ASVS claim carries it, and
#: the version-safe reference format the standard publishes carries it too.
ASVS_VERSION = "5.0.0"

#: The three levels an organization chooses between. A requirement belongs to
#: exactly one. The levels are cumulative in use: a run at level 2 rules on the
#: level 1 and level 2 sets together.
#:
#: ASVS 5.0 broke the tie between application risk and level, and tells the
#: organization to choose. So nothing in a **Valid System Model** picks one, and
#: this is a job option rather than a derived fact.
AsvsLevel = Literal[1, 2, 3]

#: The levels, as a value the catalog's own checks can test a requirement against.
ASVS_LEVELS: tuple[AsvsLevel, ...] = get_args(AsvsLevel)

_CATALOG_PATH = Path(__file__).with_name("catalog.json")

#: The SHA-256 of the catalog file this build was reviewed against. A catalog
#: edit that does not move this constant fails the import, so the file cannot
#: drift from the release it claims to reproduce without a reviewer saying
#: so here (#659). Update it in the same change as the file, with the upstream
#: tag the new bytes were compared to.
CATALOG_SHA256 = "6cc52a42534e234d09cd16d5f389fd466ac254ec4bb9ce851954c3ed1b11fb21"


@dataclass(frozen=True)
class Chapter:
    """One chapter of the standard, and the lane that runs it.

    ``id`` is the standard's own ``V1``..``V17``. ``lane`` is the slug this
    package declares, taken from the chapter name. The standard's own
    applicability guidance operates on a chapter, so one lane is one chapter.
    """

    id: str
    name: str
    lane: str

    @property
    def number(self) -> str:
        """The chapter number alone, which is an ASVS identifier's first part."""
        return self.id[1:]


@dataclass(frozen=True)
class Requirement:
    """One ASVS requirement, with the lane that rules on it.

    ``key`` is the ``<section>.<requirement>`` pair a lane agent supplies. The
    service composes the whole identifier from it and the lane's chapter number,
    so an agent never spells a chapter it was told to work in.
    """

    id: str
    lane: str
    section: str
    section_name: str
    key: str
    level: AsvsLevel
    text: str


def _load(
    path: Path,
) -> tuple[tuple[Chapter, ...], tuple[Requirement, ...], str, str]:
    """Read the published catalog off disk: chapters, requirements, version, digest."""
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    chapters = tuple(Chapter(**entry) for entry in payload["chapters"])
    requirements = tuple(Requirement(**entry) for entry in payload["requirements"])
    return (
        chapters,
        requirements,
        str(payload.get("version", "")),
        hashlib.sha256(raw).hexdigest(),
    )


def provenance_issues(version: str, digest: str) -> list[str]:
    """Everything that says the catalog on disk is not the one this build claims.

    Two checks, and they catch different edits. The version check catches a
    payload regenerated from another release under the same loader; the digest
    catches any edit at all, including one that keeps the version string. The
    loader's own version was a constant nothing compared to the payload, so a
    stale or hand-edited file loaded silently.
    """
    issues = []
    if version != ASVS_VERSION:
        issues.append(
            f"the catalog payload says ASVS {version or '<no version>'} and this"
            f" package declares {ASVS_VERSION}"
        )
    if digest != CATALOG_SHA256:
        issues.append(
            f"the catalog file's SHA-256 is {digest}, not the reviewed"
            f" {CATALOG_SHA256}; if the edit is intended, update CATALOG_SHA256"
            " in the same change"
        )
    return issues


CHAPTERS, REQUIREMENTS, _PAYLOAD_VERSION, _PAYLOAD_SHA256 = _load(_CATALOG_PATH)

#: This package's lanes, in chapter order. The chapter order is the standard's,
#: and it is not alphabetical: V1 through V17 is how the standard numbers its
#: own chapters, and a reader who infers a ranking from it reads the standard's.
LANES: tuple[str, ...] = tuple(chapter.lane for chapter in CHAPTERS)

#: One lane against the chapter number its identifiers carry, which is the
#: package's ``id_rule`` prefix table.
CHAPTER_NUMBERS: Mapping[str, str] = MappingProxyType(
    {chapter.lane: chapter.number for chapter in CHAPTERS}
)


def requirements_for(level: int, lane: str | None = None) -> tuple[Requirement, ...]:
    """Every requirement a run at ``level`` rules on, in catalog order.

    The levels are cumulative, so a run at level 2 gets the level 1 and level 2
    sets together. ``lane`` narrows the answer to one chapter.

    ``level`` is a plain ``int`` rather than :data:`AsvsLevel`, because this is a
    comparison rather than a lookup: a level outside the three simply selects
    everything at or below it. What refuses a level nobody defined is
    ``AsvsOptions``, on the input ladder, where a caller can be told.
    """
    return tuple(
        requirement
        for requirement in REQUIREMENTS
        if requirement.level <= level and (lane is None or requirement.lane == lane)
    )


def requirement_id(lane: str, key: object) -> str:
    """The standard's own identifier for one requirement, as ``V1.2.5``.

    The inverse of what an agent supplies: the lane gives the chapter and the
    agent gives the rest. :func:`~analysis_service.frameworks.asvs.record.
    requirement_of` reads the same pair back off a composed claim ID.
    """
    return f"V{CHAPTER_NUMBERS[lane]}.{key}"


_REQUIREMENT_IDS: frozenset[str] = frozenset(req.id for req in REQUIREMENTS)


def is_published_requirement(lane: str, key: object) -> bool:
    """Whether this lane and key name one of the 345 requirements 5.0.0 publishes.

    **The check the shape of a key cannot make.** A lane agent supplies a
    ``<section>.<requirement>`` pair, and ``99.99`` is as well-formed as
    ``2.1``: both match the pattern, and the service composes an identifier from
    either without noticing. Only the catalog knows which of them the standard
    contains.

    What rests on it is the citation. This package composes the standard's own
    version-safe reference, so an unchecked key puts ``v5.0.0-6.99.99`` in a
    report — a citation that reads as verifiable and resolves to nothing. A
    missing finding is a gap; an invented requirement is a false statement about
    the standard, and the second is worse.

    Reads the same ``lane``-to-chapter table :func:`requirement_id` composes
    from, so what counts as published here and what gets composed there cannot
    disagree. An unknown lane answers ``False`` rather than raising: this is
    reached with an agent's value on one side, and the caller's remedy is to
    drop the claim either way.
    """
    if lane not in CHAPTER_NUMBERS:
        return False
    return requirement_id(lane, key) in _REQUIREMENT_IDS


def _catalog_issues() -> list[str]:
    """Everything wrong with the catalog on disk, as messages."""
    issues: list[str] = provenance_issues(_PAYLOAD_VERSION, _PAYLOAD_SHA256)
    if not CHAPTERS:
        issues.append("the catalog declares no chapters")
    if not REQUIREMENTS:
        issues.append("the catalog declares no requirements")

    duplicate_lanes = sorted(
        {lane for lane in LANES if LANES.count(lane) > 1},
    )
    if duplicate_lanes:
        issues.append(f"two chapters share a lane slug: {', '.join(duplicate_lanes)}")

    stray = sorted({req.lane for req in REQUIREMENTS} - set(LANES))
    if stray:
        issues.append(f"requirements name chapters the catalog omits: {stray}")

    empty = sorted(lane for lane in LANES if not requirements_for(3, lane))
    if empty:
        issues.append(f"chapters carry no requirement at any level: {empty}")

    counts = Counter(req.id for req in REQUIREMENTS)
    duplicate_ids = sorted(req_id for req_id, count in counts.items() if count > 1)
    if duplicate_ids:
        issues.append(f"two requirements share an identifier: {duplicate_ids}")

    malformed = sorted(
        req.id
        for req in REQUIREMENTS
        if req.id != requirement_id(req.lane, req.key) or req.level not in ASVS_LEVELS
    )
    if malformed:
        issues.append(f"requirements are ill-formed: {malformed}")

    textless = sorted(req.id for req in REQUIREMENTS if not req.text.strip())
    if textless:
        issues.append(f"requirements carry no description: {textless}")
    return issues


_ISSUES = _catalog_issues()
if _ISSUES:
    raise FrameworkPackageError(
        "the asvs requirement catalog is not well-formed: " + "; ".join(_ISSUES)
    )
