"""How much one job used its assertion catalog, read from its report.

**Use, not impact.** A lane that cites a catalog row can raise the same claim
from a quote, so a citation is an upper bound on what the pass added. The
figure nearest to impact is ``catalog_only``: a claim whose every ground is a
catalog row has no support on a job that ran no pass. ``catalog_leads`` is the
same bound for the rules, measured by ``prepare`` on the model before the
catalog's projection.

The one reader of these figures. Each finished job logs them, and ``run.py
assertion-use`` reads them out of archived reports.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from analysis_service.claims import ASSERTION_GROUNDS, FrameworkName
from analysis_service.graph import ASSERT_NODE
from analysis_service.links import link_questions
from analysis_service.report import Report


@dataclass(frozen=True)
class BlockUse:
    """One framework's use of the catalog."""

    framework: FrameworkName
    #: The block's accepted claims, confirmed and conditional.
    claims: int
    #: The claims with at least one ground on a catalog row.
    citing: int
    #: The claims whose every ground is on a catalog row.
    catalog_only: int
    #: See :attr:`~analysis_service.claims.FrameworkAnalysis.catalog_leads`.
    catalog_leads: int | None


@dataclass(frozen=True)
class AssertionUse:
    """One job's use of the catalog, and what the pass cost it."""

    rows: int
    refused: int
    #: The principals the catalog states facts about and places on no element.
    open_links: int
    #: The ``assert`` node's wall-clock; 0 where a facts-first reading wrote
    #: the catalog and no such node ran.
    assert_ms: int
    #: What the provider reported it charged for the ``assert`` node, or
    #: ``None`` where it reported nothing.
    assert_charge_usd: float | None
    blocks: tuple[BlockUse, ...]

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def assertion_use(report: Report) -> AssertionUse | None:
    """The job's use of its catalog, or ``None`` for a job that ran no pass."""
    if report.assertions is None:
        return None
    runs = [run for run in report.nodes if run.node == ASSERT_NODE]
    charges = [
        run.reported_charge_usd for run in runs if run.reported_charge_usd is not None
    ]
    return AssertionUse(
        rows=len(report.assertions.catalog.entries),
        refused=report.assertions.refused_rows(),
        open_links=len(link_questions(report.assertions.catalog, report.system_model)),
        assert_ms=sum(run.duration_ms for run in runs),
        assert_charge_usd=sum(charges) if charges else None,
        blocks=tuple(
            BlockUse(
                framework=block.framework,
                claims=len(block.claims),
                citing=sum(
                    any(ground.kind in ASSERTION_GROUNDS for ground in claim.grounds)
                    for claim in block.claims
                ),
                catalog_only=sum(
                    all(ground.kind in ASSERTION_GROUNDS for ground in claim.grounds)
                    for claim in block.claims
                ),
                catalog_leads=block.catalog_leads,
            )
            for block in report.analyses
        ),
    )
