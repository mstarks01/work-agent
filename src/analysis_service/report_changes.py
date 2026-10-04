"""What a follow-up report changed, finding by finding, against the report before it.

A report's follow-up runs again on the owner's answers (ADR 0054). Its reader
needs to know what those answers did: which findings the run confirmed,
which it ruled out, which are new and which it no longer raised (#561).

A finding is matched by :func:`~analysis_service.critic.finding_key` and,
under one key, by :func:`~analysis_service.critic.distinct_mechanisms`: two
claims are one finding where their keys agree and their grounded controls are
not disjoint. The comparison reads no prose and makes no model call. Rejected drafts take part on both sides, because a draft the
earlier report rejected and the follow-up confirmed is a change a reader has to
see.

The follow-up drafts again, so a finding it writes at another place or with
another verb reads as one new finding and one that is gone. On the
withheld-sentence runs of 2026-09-28, each pair of reports held 4 to 7 such
lost findings beside 12 to 16 that stayed. The match says which identities
moved, never that two differently keyed claims are the same finding.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

from analysis_service.claims import RuledClaim
from analysis_service.critic import (
    FindingKey,
    distinct_mechanisms,
    finding_key,
    mechanism_of,
    row_controls,
)
from analysis_service.report import Report
from analysis_service.system_model import ModelIndex

#: How one finding moved between the two reports.
Change = Literal["new", "unchanged", "changed", "gone"]


@dataclass(frozen=True)
class FindingChange:
    """One finding of either report, and how it moved."""

    framework: str
    #: The follow-up's claim ID, or the earlier report's for a finding that is
    #: gone.
    claim_id: str
    title: str
    change: Change
    #: The verdict in the earlier report, or ``None`` for a new finding.
    before: str | None
    #: The verdict in the follow-up, or ``None`` for a finding that is gone.
    after: str | None

    def to_json(self) -> dict[str, str | None]:
        return {
            "framework": self.framework,
            "claim_id": self.claim_id,
            "title": self.title,
            "change": self.change,
            "before": self.before,
            "after": self.after,
        }


def _findings(
    report: Report,
) -> Iterator[tuple[FindingKey, frozenset[str], RuledClaim]]:
    flows = ModelIndex.of(report.system_model).flow_endpoints
    rows = row_controls(report.assertions.catalog if report.assertions else None)
    for block in report.analyses:
        for claim in (*block.claims, *block.rejected_claims):
            yield finding_key(claim, flows), mechanism_of(claim, flows, rows), claim


def report_changes(before: Report, after: Report) -> tuple[FindingChange, ...]:
    """Every finding of ``after`` against ``before``, then every one ``after`` lost.

    Two claims of one report that share a key are matched in report order, so
    each earlier claim answers for at most one later one.
    """
    earlier: dict[FindingKey, list[tuple[frozenset[str], RuledClaim]]] = {}
    for key, mechanism, claim in _findings(before):
        earlier.setdefault(key, []).append((mechanism, claim))
    changes = []
    for key, mechanism, claim in _findings(after):
        status = claim.verdict.status
        candidates = earlier.get(key, [])
        matched = [
            held for held in candidates if not distinct_mechanisms(mechanism, held[0])
        ]
        if not matched:
            changes.append(
                FindingChange(
                    claim.framework, claim.id, claim.title, "new", None, status
                )
            )
            continue
        first = matched[0]
        candidates.remove(first)
        was = first[1].verdict.status
        change: Change = "unchanged" if was == status else "changed"
        changes.append(
            FindingChange(claim.framework, claim.id, claim.title, change, was, status)
        )
    for held_claims in earlier.values():
        for _, claim in held_claims:
            if claim.verdict.status != "rejected":
                changes.append(
                    FindingChange(
                        claim.framework,
                        claim.id,
                        claim.title,
                        "gone",
                        claim.verdict.status,
                        None,
                    )
                )
    return tuple(changes)
