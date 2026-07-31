"""Mechanical checks at the join and assemble seams around the critic.

The deterministic half of the critic step: mechanical checks belong in code,
prompts carry only judgement. Everything here is a check no model should be
asked to perform — that the six analysts' drafts cite elements the System Model
actually contains, that threat IDs are unique, and that the critic returned
exactly the drafts it was given, each carrying a well-formed verdict. The
critic prompt names these as already done so its judgement is spent on
evidence, lanes, duplicates, severity and confidence instead.

Model output is untrusted input (OWASP LLM05): it is validated here, before
anything reaches the report. Both seams fail closed with every issue listed
at once — an analyst that hallucinates an element ID or a critic that drops
threats is a defect to surface loudly, never to paper over by discarding the
offending entries.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import NamedTuple

from stride_service.report import (
    STRIDE_CATEGORIES,
    DraftThreat,
    SeverityLevel,
    StrideCategory,
    Threat,
)
from stride_service.system_model import SystemModel

# Most severe first — the order the report's ``threats`` array carries.
SEVERITY_ORDER: tuple[SeverityLevel, ...] = ("critical", "high", "medium", "low")


class DraftJoinError(ValueError):
    """The merged analyst drafts fail a mechanical check."""


class CriticOutputError(ValueError):
    """The critic's output does not account for exactly the drafts it saw."""


class AssembledThreats(NamedTuple):
    """The critic's ruled threats, split into the report's two arrays."""

    threats: list[Threat]
    rejected_threats: list[Threat]


def _unresolved_reference_issues(
    threats: Iterable[DraftThreat], system_model: SystemModel
) -> list[str]:
    known_ids = {element.id for element in system_model.elements()}
    return [
        f"threat {threat.id!r} references element {ref!r}, which is not in the"
        " system model"
        for threat in threats
        for ref in threat.affected_element_ids
        if ref not in known_ids
    ]


def _unresolved_unknown_ref_issues(
    threats: Iterable[Threat], system_model: SystemModel
) -> list[str]:
    known_ids = {element.id for element in system_model.elements()}
    return [
        f"threat {threat.id!r} hangs its needs-info verdict on element"
        f" {ref.element_id!r}, which is not in the system model"
        for threat in threats
        for ref in threat.verdict.related_unknowns
        if ref.element_id not in known_ids
    ]


def _duplicate_id_issues(threats: Iterable[DraftThreat]) -> list[str]:
    counts = Counter(threat.id for threat in threats)
    return [
        f"threat ID {threat_id!r} is used by {count} drafts"
        for threat_id, count in counts.items()
        if count > 1
    ]


def join_drafts(
    drafts_by_category: Mapping[StrideCategory, Sequence[DraftThreat]],
    system_model: SystemModel,
) -> list[DraftThreat]:
    """Merge the six analysts' drafts into the single list the critic sees.

    Canonical STRIDE order, so the critic reads the lanes in the same order
    every run. Category letters are enforced by :class:`DraftThreat` itself;
    what this seam adds is the two checks that need the whole set: element
    references resolving against the System Model, and IDs unique across it.
    """
    merged = [
        draft
        for category in STRIDE_CATEGORIES
        for draft in drafts_by_category.get(category, ())
    ]
    misfiled = [
        f"draft {draft.id!r} is filed under {category!r} but its category is"
        f" {draft.category!r}"
        for category, drafts in drafts_by_category.items()
        for draft in drafts
        if draft.category != category
    ]
    issues = (
        misfiled
        + _duplicate_id_issues(merged)
        + _unresolved_reference_issues(merged, system_model)
    )
    if issues:
        raise DraftJoinError("; ".join(issues))
    return merged


def review_issues(
    drafts: Sequence[DraftThreat],
    reviewed: Sequence[Threat],
    system_model: SystemModel,
) -> list[str]:
    """Every way the critic's output fails to account for the drafts it saw.

    The mechanical check, returned as a list rather than raised, so the graph
    can *route* on it: an empty list means the critic output is assemblable, a
    non-empty one is what the bounded re-ask is asked to fix. The critic must
    return exactly the drafted set — no threat invented, none dropped — with
    unique IDs and references that still resolve after any edit it made.
    Verdict shape and the category letter are enforced by the models and never
    re-checked here.
    """
    drafted_ids = {draft.id for draft in drafts}
    reviewed_ids = {threat.id for threat in reviewed}
    issues = [
        f"critic dropped draft {threat_id!r}"
        for threat_id in sorted(drafted_ids - reviewed_ids)
    ]
    issues += [
        f"critic returned threat {threat_id!r}, which no analyst drafted"
        for threat_id in sorted(reviewed_ids - drafted_ids)
    ]
    issues += _duplicate_id_issues(reviewed)
    issues += _unresolved_reference_issues(reviewed, system_model)
    issues += _unresolved_unknown_ref_issues(reviewed, system_model)
    return issues


def assemble_threats(
    drafts: Sequence[DraftThreat],
    reviewed: Sequence[Threat],
    system_model: SystemModel,
) -> AssembledThreats:
    """Split the critic's ruled threats into the report's two arrays.

    :func:`review_issues` is the gate — one definition of what "well-formed
    critic output" means, shared with the router that decides whether to
    re-ask. Assembly runs only after that gate has passed, but re-checks here
    and fails closed regardless: nothing reaches the report on output that did
    not survive the check. Rejected threats ride in their own audit array; the
    rest are sorted most-severe-first, as the report expects.
    """
    issues = review_issues(drafts, reviewed, system_model)
    if issues:
        raise CriticOutputError("; ".join(issues))

    actionable = [t for t in reviewed if t.verdict.status != "rejected"]
    rejected = [t for t in reviewed if t.verdict.status == "rejected"]
    return AssembledThreats(sorted(actionable, key=_severity_key), rejected)


def _severity_key(threat: Threat) -> tuple[int, str]:
    return SEVERITY_ORDER.index(threat.severity.level), threat.id
