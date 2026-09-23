"""What a finished report becomes once a reviewer has assessed its assertions.

#926's reviewed guarantee, in the owner's words of 2026-09-23: assertions
marked ``unsupported`` or ``unresolved`` must not support definite
conclusions, and the projections, evidence and findings they touched are
recomputed after review. The promise is **no reviewer-rejected fact reaches
analysis** — not "no wrong fact", because a review can miss one.

A review is a set of verdicts, one per assertion identity. This module applies
them to the report's catalog and recomputes, through the same reader analysis
preparation calls (:func:`~analysis_service.evidence.prepared_view`):

* the **model** — a rejected row's projection is set aside, so an attribute it
  wrote reads as a qualified ``unknown`` rather than the rejected value;
* the **evidence** a lane may cite — a rejected row is no longer in it;
* the **findings** — every claim whose grounds no longer resolve in the
  recomputed evidence is withdrawn, with the reason the ground check gives;
* the **leads** the review reopened — questions and absences the recomputed
  evidence offers that the original did not. A finding a rejected row
  suppressed was never written, so no recomputation can recover it; these are
  where a re-analysis has to look.

It writes no report. A report checks every ground on load, so a report that
kept a withdrawn claim beside the reviewed catalog would not load, and one
that dropped it silently would hide the withdrawal. The result is a record
beside the report, and the report stays the immutable artifact it was.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import get_args

from analysis_service.assertions import (
    REJECTING,
    AssertionCatalog,
    AssertionRecord,
    Assessment,
    Projection,
    assertion_id,
    settled,
)
from analysis_service.evidence import (
    EvidenceCatalog,
    evidence_catalog,
    ground_issues,
    lead_topics,
    prepared_view,
)
from analysis_service.report import Report
from analysis_service.system_model import SystemModel


class ReviewError(ValueError):
    """A set of verdicts that cannot be applied honestly."""


@dataclass(frozen=True)
class Verdict:
    """One reviewer's assessment of one assertion."""

    assessment: Assessment
    assessor: str


@dataclass(frozen=True)
class Withdrawn:
    """A finding whose grounds the review took away, and why."""

    claim: str
    framework: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Reassessment:
    """A report's assertion layer, recomputed under a review."""

    record: AssertionRecord
    model: SystemModel
    projections: tuple[Projection, ...]
    evidence: EvidenceCatalog
    #: The identities the review rejected.
    rejected: tuple[str, ...]
    withdrawn: tuple[Withdrawn, ...]
    #: Lead topics the recomputed evidence offers and the original did not.
    reopened: tuple[str, ...]


def reviewed(
    catalog: AssertionCatalog, verdicts: Mapping[str, Verdict]
) -> AssertionCatalog:
    """``catalog`` with each verdict written onto the row it names.

    **Refused rather than guessed.** A verdict naming a row the catalog does
    not hold was written against another catalog; a verdict of ``unchecked``
    is no verdict; one with no assessor cannot be told apart from an
    extractor's own opinion, which ADR 0034 rules is never an assessment.
    """
    held = {assertion_id(entry) for entry in catalog.entries}
    stray = sorted(set(verdicts) - held)
    if stray:
        raise ReviewError(f"verdicts name rows this catalog does not hold: {stray}")
    for identity, verdict in verdicts.items():
        if verdict.assessment not in get_args(Assessment):
            raise ReviewError(f"{identity}: {verdict.assessment!r} is no assessment")
        if verdict.assessment == "unchecked":
            raise ReviewError(f"{identity}: `unchecked` is not a verdict")
        if not verdict.assessor.strip():
            raise ReviewError(f"{identity}: a verdict names who made it")
    return catalog.model_copy(
        update={
            "entries": [
                entry
                if assertion_id(entry) not in verdicts
                else entry.model_copy(
                    update={
                        "assessment": verdicts[assertion_id(entry)].assessment,
                        "assessor": verdicts[assertion_id(entry)].assessor,
                    }
                )
                for entry in catalog.entries
            ]
        }
    )


def reassess(report: Report, verdicts: Mapping[str, Verdict]) -> Reassessment:
    """Recompute what ``report``'s analysis may rest on, under ``verdicts``."""
    if report.assertions is None:
        raise ReviewError("the report ran no assertion pass, so nothing is reviewed")
    before = report.assertions.catalog
    after = reviewed(before, verdicts)
    model, projections, evidence = prepared_view(report.system_model, after)
    # A row the review settles may leave the evidence because the attribute
    # now carries it, and a finding citing it still rests on a fact.
    standing = {assertion_id(entry) for entry in settled(after)}
    withdrawn = []
    for block in report.analyses:
        for claim in block.all_claims():
            open_grounds = [
                ground
                for ground in claim.grounds
                if not (ground.kind == "assertion" and ground.assertion in standing)
            ]
            reasons = ground_issues(
                [claim.model_copy(update={"grounds": open_grounds})], model, after
            )
            if reasons:
                withdrawn.append(
                    Withdrawn(claim.id, str(block.framework), tuple(reasons))
                )
    original = lead_topics(evidence_catalog(report.system_model, before), before)
    return Reassessment(
        record=report.assertions.model_copy(update={"catalog": after}),
        model=model,
        projections=projections,
        evidence=evidence,
        rejected=tuple(
            sorted(
                identity
                for identity, verdict in verdicts.items()
                if verdict.assessment in REJECTING
            )
        ),
        withdrawn=tuple(withdrawn),
        reopened=tuple(sorted(lead_topics(evidence, after) - original)),
    )
