"""Which ASVS requirements apply to an application, as one rule per requirement.

ASVS publishes no applies-when field, so this table is this repository's own.
``applicability.json`` holds one row for each of the 345 requirements. A row
states the capabilities the requirement's subject needs, as an expression over
:data:`~analysis_service.capabilities.CAPABILITIES`, and a rationale in this
repository's own words. ``always`` marks a requirement whose subject every web
application or service has.

**A row answers "is there a subject", never "is it met".** A password rule
applies because users sign in with a password, and its minimum length is the
conformance question a lane asks later. A row may not read the control its
requirement asks for: an authentication logging rule applies because the
application authenticates, not because it logs.

**Two rules decide between a capability and ``always``.** A subject that every
web application or service has, such as TLS or third-party components, is
``always``: a question about it has only one honest answer. A subject that a
real application can lack is a capability, even where the description rarely
says: an unknown is a truthful answer, and ``always`` would include the
requirement for an application that has no such subject.

The catalog and this table are checked against each other at import, so a
requirement with no row, or a row the catalog does not hold, raises before any
job runs.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from analysis_service.capabilities import (
    CapabilityFact,
    Decision,
    Expression,
    evaluate,
    expression_issues,
    resolve,
)
from analysis_service.frameworks import FrameworkPackageError
from analysis_service.frameworks.asvs.catalog import (
    ASVS_VERSION,
    REQUIREMENTS,
    requirements_for,
)

__all__ = ["APPLICABILITY", "RATIONALES", "REVIEWED_BY", "applicability_for"]

_PATH = Path(__file__).with_name("applicability.json")


def _load(path: Path) -> tuple[str, list[dict]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return str(payload.get("version", "")), list(payload["requirements"])


_VERSION, _ROWS = _load(_PATH)

#: Each requirement's applicability expression, keyed by the standard's ID.
APPLICABILITY: Mapping[str, Expression] = MappingProxyType(
    {row["id"]: row["applies_if"] for row in _ROWS}
)

#: Why each row reads what it reads, in this repository's words.
RATIONALES: Mapping[str, str] = MappingProxyType(
    {row["id"]: row["rationale"] for row in _ROWS}
)

#: Who reviewed each row, or ``None`` where nobody has. An agent drafted every
#: row, and a row is not reviewed until the maintainer reads it.
REVIEWED_BY: Mapping[str, str | None] = MappingProxyType(
    {row["id"]: row["reviewed_by"] for row in _ROWS}
)


def applicability_for(
    level: int, known: Mapping[str, CapabilityFact]
) -> dict[str, Decision]:
    """Every requirement a run at ``level`` rules on, with its decision.

    ``known`` is what the job knows about each capability; a capability it
    leaves out is unknown.
    """
    facts = resolve(known)
    return {
        requirement.id: evaluate(APPLICABILITY[requirement.id], facts)
        for requirement in requirements_for(level)
    }


def _issues() -> list[str]:
    issues = []
    if _VERSION != ASVS_VERSION:
        issues.append(f"the table is for {_VERSION!r}, the catalog is {ASVS_VERSION!r}")
    ids = [row["id"] for row in _ROWS]
    if len(ids) != len(set(ids)):
        issues.append("a requirement has more than one row")
    catalog = {requirement.id for requirement in REQUIREMENTS}
    if missing := sorted(catalog - set(ids)):
        issues.append(f"requirements with no row: {missing}")
    if stray := sorted(set(ids) - catalog):
        issues.append(f"rows the catalog does not hold: {stray}")
    for row in _ROWS:
        issues += [
            f"{row['id']}: {issue}" for issue in expression_issues(row["applies_if"])
        ]
        if not str(row.get("rationale", "")).strip():
            issues.append(f"{row['id']}: no rationale")
    return issues


if _ISSUES := _issues():
    raise FrameworkPackageError(
        "the asvs applicability table is not well-formed: " + "; ".join(_ISSUES)
    )
