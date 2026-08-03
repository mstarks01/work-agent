"""Mechanical checks at the join and assemble seams around the critic.

The deterministic half of the critic step: mechanical checks belong in code,
prompts carry only judgement. Everything here is a check no model should be
asked to perform — that the six analysts' drafts cite elements the System Model
actually contains, that threat IDs are unique, and that the critic ruled on
exactly the drafts it was given, each ruling carrying a well-formed verdict.
The critic prompt names these as already done so its judgement is spent on
evidence, lanes, duplicates, severity and confidence instead.

The assemble seam is also where a ruling becomes a threat. The critic emits
judgements keyed by draft ID rather than the drafts themselves
(:class:`~stride_service.report.ThreatRuling`), so the analyst's own fields
reach the report from the copy this service already holds — not round-tripped
through a model that was never asked to change them.

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
    ThreatRuling,
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
    rulings: Iterable[ThreatRuling], system_model: SystemModel
) -> list[str]:
    known_ids = {element.id for element in system_model.elements()}
    return [
        f"threat {ruling.id!r} hangs its needs-info verdict on element"
        f" {ref.element_id!r}, which is not in the system model"
        for ruling in rulings
        for ref in ruling.verdict.related_unknowns
        if ref.element_id not in known_ids
    ]


def _duplicate_id_issues(entries: Iterable[DraftThreat | ThreatRuling]) -> list[str]:
    counts = Counter(entry.id for entry in entries)
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
    rulings: Sequence[ThreatRuling],
    system_model: SystemModel,
) -> list[str]:
    """Every way the critic's rulings fail to account for the drafts it saw.

    The mechanical check, returned as a list rather than raised, so the graph
    can *route* on it: an empty list means the rulings are assemblable, a
    non-empty one is what the bounded re-ask is asked to fix. The critic must
    rule on exactly the drafted set — no threat invented, none dropped — with
    unique IDs, and each ``needs-info`` verdict naming only unknowns the model
    actually contains. Verdict shape and the ID's category letter are enforced
    by :class:`~stride_service.report.ThreatRuling` itself and never re-checked
    here.

    Element references are deliberately **not** checked: a ruling carries none.
    They are the join seam's business (:func:`join_drafts` fails closed on a
    draft citing an element the model does not contain), and since the critic
    no longer re-emits them there is no second place they can break. An issue
    listed here has to be one the re-ask can actually fix, and a draft's bad
    reference never was.
    """
    drafted_ids = {draft.id for draft in drafts}
    ruled_ids = {ruling.id for ruling in rulings}
    issues = [
        f"critic dropped draft {threat_id!r}"
        for threat_id in sorted(drafted_ids - ruled_ids)
    ]
    issues += [
        f"critic returned threat {threat_id!r}, which no analyst drafted"
        for threat_id in sorted(ruled_ids - drafted_ids)
    ]
    issues += _duplicate_id_issues(rulings)
    issues += _unresolved_unknown_ref_issues(rulings, system_model)
    return issues


def _ruled(draft: DraftThreat, ruling: ThreatRuling) -> Threat:
    """One draft plus the critic's ruling on it, as the report's :class:`Threat`.

    The draft's own fields are carried across from the copy the service already
    held rather than from anything the critic emitted, so a review cannot alter
    a description or an element reference. ``severity`` is the single exception
    and the single overridable field: the calibration step is allowed to replace
    a rating, and does so together with the justification that argues for it.
    """
    return Threat(
        **draft.model_dump(exclude={"severity"}),
        severity=ruling.severity or draft.severity,
        confidence=ruling.confidence,
        verdict=ruling.verdict,
    )


def assemble_threats(
    drafts: Sequence[DraftThreat],
    rulings: Sequence[ThreatRuling],
    system_model: SystemModel,
) -> AssembledThreats:
    """Merge the critic's rulings onto the drafts, split into the report's arrays.

    :func:`review_issues` is the gate — one definition of what "well-formed
    critic output" means, shared with the router that decides whether to
    re-ask. Assembly runs only after that gate has passed, but re-checks here
    and fails closed regardless: nothing reaches the report on output that did
    not survive the check. Rejected threats ride in their own audit array; the
    rest are sorted most-severe-first, as the report expects.

    Threats are built in ``drafts`` order — canonical STRIDE order, as
    :func:`join_drafts` left them — so the audit array does not inherit
    whatever order the critic happened to emit its rulings in.
    """
    issues = review_issues(drafts, rulings, system_model)
    if issues:
        raise CriticOutputError("; ".join(issues))

    ruling_by_id = {ruling.id: ruling for ruling in rulings}
    reviewed = [_ruled(draft, ruling_by_id[draft.id]) for draft in drafts]
    actionable = [t for t in reviewed if t.verdict.status != "rejected"]
    rejected = [t for t in reviewed if t.verdict.status == "rejected"]
    return AssembledThreats(sorted(actionable, key=_severity_key), rejected)


def _severity_key(threat: Threat) -> tuple[int, str]:
    return SEVERITY_ORDER.index(threat.severity.level), threat.id
