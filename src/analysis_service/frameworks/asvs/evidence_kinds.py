"""What kind of evidence settles each ASVS requirement.

**Not a property of the requirement. A relation between a requirement and a
job.** "This cannot be answered" would go stale the day the service accepts a
new kind of input. "This needs source code, and this job carries prose" stays
true and reverses on its own.

The four kinds are the standard's own, quoted by this package's disclaimer:
ASVS verification "needs source code, configuration and the people who built
the system", and a job here carries **prose**. Nothing here invents a
vocabulary.

**What each kind means, as the question a verifier would ask.**

``prose``
    A **Source** settles it. The substance is a fact about the system's shape
    or composition — what exists, what it presents, what connects to what,
    where data rests — or it is *whether a named control exists at all*, where
    a description that omits the control is itself the finding.
``code``
    The implementation settles it: how a query is built, how output is encoded,
    which library is called.
``config``
    A deployed setting settles it: a protocol version, a cipher suite, a
    lifetime, a header value.
``people``
    A document or a person settles it: a policy, an inventory, a defined
    process, an agreed timeline.

A requirement carries every kind that could settle it. A job settles it if it
carries any one of them.

**The line, and how it was drawn.** Prose settles *"does control X exist"*; it
does not settle *"is X implemented correctly"*. That line is not a guess. A
first pass allowed prose only for structural facts, and four requirements that
live runs had ruled ``confirmed`` — which means a Source *did* settle them —
came out unsettleable. All four asked whether a control existed. The rule is
the correction, and ``tests/test_asvs_evidence_kinds.py`` holds the eleven
requirements that ground it.

**This module changes no analysis.** It is the table and its checks. Whether a
requirement no available kind settles becomes a **Scope Entry** rather than a
**Claim** is [#415](https://github.com/mstarks01/work-agent/issues/415), and
that decision is not taken here.

Licensing: this file and ``evidence_kinds.json`` carry requirement identifiers
and this project's own judgement. They reproduce none of the standard's text,
so neither is governed by the ASVS package's CC BY-SA content licence. Read a
requirement's words from ``catalog.json``, which is.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Literal, get_args

from analysis_service.frameworks.asvs.catalog import REQUIREMENTS, FrameworkPackageError

__all__ = [
    "EVIDENCE_KINDS",
    "KINDS",
    "EvidenceKind",
    "settles",
    "unsettled_by",
]

EvidenceKind = Literal["code", "config", "people", "prose"]

#: The closed set, in one place, so a table entry cannot invent a fifth.
KINDS: tuple[EvidenceKind, ...] = get_args(EvidenceKind)

_TABLE = Path(__file__).with_name("evidence_kinds.json")


def _load() -> Mapping[str, tuple[EvidenceKind, ...]]:
    """The table, checked against the catalog it describes.

    Checked at import rather than at first use, on the rule the package gate
    already follows: a table that does not cover its registry fails as quietly
    as the branch it replaced. A requirement added to the catalog with no entry
    here would otherwise be settled by nothing and scoped out of every job.
    """
    raw = json.loads(_TABLE.read_text(encoding="utf-8"))
    if raw.get("version") != 1:
        raise FrameworkPackageError(f"{_TABLE.name}: unsupported version")
    table = {name: tuple(kinds) for name, kinds in raw["kinds"].items()}

    catalog = {requirement.id for requirement in REQUIREMENTS}
    missing = sorted(catalog - set(table))
    if missing:
        raise FrameworkPackageError(
            f"{_TABLE.name} covers no evidence kind for {missing}. Every"
            " requirement needs one, or a job settles it by nothing."
        )
    stray = sorted(set(table) - catalog)
    if stray:
        raise FrameworkPackageError(
            f"{_TABLE.name} names requirements the catalog does not hold: {stray}"
        )
    for name, kinds in table.items():
        unknown = sorted(set(kinds) - set(KINDS))
        if unknown or not kinds:
            raise FrameworkPackageError(
                f"{_TABLE.name}: {name} names {unknown or 'no'} evidence kind"
            )
    return MappingProxyType(table)


#: Requirement identifier -> every kind of evidence that could settle it.
EVIDENCE_KINDS: Mapping[str, tuple[EvidenceKind, ...]] = _load()


def settles(requirement_id: str, available: Collection[str]) -> bool:
    """Can a job carrying ``available`` kinds settle this requirement?

    True on any overlap: one sufficient kind is enough, and a job carrying more
    settles a superset. A requirement the catalog does not hold raises rather
    than answering, because a silent ``False`` would scope it out of every job.
    """
    return bool(set(EVIDENCE_KINDS[requirement_id]) & set(available))


def unsettled_by(available: Collection[str], level: int) -> tuple[str, ...]:
    """Every requirement at or below ``level`` that ``available`` cannot settle.

    The set a job would record as **Scope Entries** rather than rule on, under
    the design in #415. Returned in catalog order so two calls agree.
    """
    return tuple(
        requirement.id
        for requirement in REQUIREMENTS
        if requirement.level <= level and not settles(requirement.id, available)
    )


def _review() -> None:
    """Print the table beside the requirement text, for a person to check.

    The text is read from ``catalog.json`` at render time rather than copied
    into a file beside the judgement, which is what keeps the governed words in
    the one governed place. ``roster.py`` composes skill text the same way and
    for the same reason.

    Run it as::

        uv run python -m analysis_service.frameworks.asvs.evidence_kinds
    """
    from collections import Counter

    by_kind: Counter[str] = Counter()
    print("# ASVS evidence kinds, for review\n")
    print(
        "One line per requirement: its level, the kinds of evidence that could"
        " settle it, and its published text. `prose` means a Source can settle"
        " it. Read the rule in this module's docstring before the rows: the"
        " line is *does control X exist* against *is X implemented correctly*.\n"
    )
    seen_lane = ""
    for requirement in REQUIREMENTS:
        if requirement.lane != seen_lane:
            seen_lane = requirement.lane
            print(f"\n## {seen_lane}\n")
        kinds = EVIDENCE_KINDS[requirement.id]
        by_kind[", ".join(kinds)] += 1
        print(f"- **{requirement.id}** (L{requirement.level}) `{', '.join(kinds)}`")
        print(f"  {requirement.text}")

    print("\n## Totals\n")
    for combination, count in by_kind.most_common():
        print(f"- `{combination}` — {count}")
    for level in (1, 2, 3):
        total = sum(1 for r in REQUIREMENTS if r.level <= level)
        blind = len(unsettled_by(["prose"], level))
        print(
            f"- level {level}: a prose-only job settles {total - blind} of"
            f" {total}; {blind} would be Scope Entries"
        )


if __name__ == "__main__":  # pragma: no cover - a reading aid, not a code path
    _review()
