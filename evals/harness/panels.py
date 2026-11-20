"""The pre-generation phase panels: one panel for each node an archive can read.

Extraction, capability reading, assertions and preparation produce no finding,
so the scorecard's must-find rows cannot measure them. Each panel here reads
one node's output in that node's own unit, over one source, and keeps its own
counts, denominators and unread counts. No code path adds two panels together
or averages them.

**Phases.** A panel's phase is :func:`~evals.harness.audit.phase_of` its node,
so ``capabilities`` and ``assert`` are sub-panels of ``extraction``.

**Routes.** An assertion panel names its route by the nodes that wrote the
catalog: ``assert`` reads the signed model, and ``extract`` is the combined
extraction and assertion head. A combined head's recall is never an
assertion-only or an extraction-only figure.

**Production path.** Each panel says whether its reading reproduces what a
job runs today: ``equivalent``, ``known-different`` or ``unverified``, with
the evidence. A model's archived output is never read as ``equivalent``,
because the archive does not record every setting a job sends.
"""

from __future__ import annotations

import csv
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, get_args

from analysis_service.graph import LANE_SHARED_KEYS
from analysis_service.prompts import EXTRACT_PROMPT_NAME, EXTRACTION_DELTAS
from evals.harness.audit import Experiment, Phase, changed_since, phase_of
from evals.harness.delivery import Delivery
from evals.harness.losses import SILENT
from evals.harness.provenance import REPO_ROOT
from evals.harness.rates import mean, shown_figure
from evals.harness.replay import (
    FATES,
    PRODUCED_FATES,
    ROW_FATES,
    Kept,
    SweepReplay,
    pooled_assertions,
    pooled_extraction,
)
from evals.harness.scorecard import Rate

Fidelity = Literal["equivalent", "known-different", "unverified"]

#: The node each panel reads.
PanelNode = Literal["extract", "capabilities", "assert", "prepare"]
PANEL_NODES: tuple[PanelNode, ...] = get_args(PanelNode)

#: A verdict the capability ballot records, as its ``verdict`` column spells it.
BallotVerdict = Literal["right", "wrong"]
BALLOT_VERDICTS: tuple[BallotVerdict, ...] = get_args(BallotVerdict)


@dataclass(frozen=True)
class ProductionPath:
    """Whether a panel's reading reproduces what a job runs, and why."""

    fidelity: Fidelity
    evidence: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {"fidelity": self.fidelity, "evidence": list(self.evidence)}


@dataclass(frozen=True)
class Panel:
    """One node's output, read in its own unit over one source.

    ``counts`` holds the counts the panel reads, and ``rates`` the shares it
    states, each with its numerator and denominator. ``means`` holds a mean
    with the number of values it is over. ``unread`` counts what the source
    holds no reading for, in the panel's own terms: the produced rows nothing
    signed rules on (assertions), the verdicts outside :data:`BALLOT_VERDICTS`
    (capabilities) and the lanes with no capture (preparation). It is ``None``
    for extraction, where every signed element takes a fate. A panel the
    source cannot fill holds no count and says why in ``uncaptured``.
    """

    node: PanelNode
    source: str
    unit: str
    counts: Mapping[str, int]
    rates: Mapping[str, Rate]
    means: Mapping[str, tuple[float | None, int]]
    unread: int | None
    path: ProductionPath | None
    uncaptured: str = ""

    @property
    def phase(self) -> Phase:
        return phase_of(self.node)

    def to_json(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "node": self.node,
            "source": self.source,
            "unit": self.unit,
            "counts": dict(self.counts),
            "rates": {name: rate.to_json() for name, rate in self.rates.items()},
            "means": {
                name: {"mean": mean, "over": over}
                for name, (mean, over) in self.means.items()
            },
            "unread": self.unread,
            "path": None if self.path is None else self.path.to_json(),
            "uncaptured": self.uncaptured,
        }


def uncaptured(node: PanelNode, source: str, reason: str) -> Panel:
    """The panel of a node that ``source`` holds no output of."""
    return Panel(node, source, "", {}, {}, {}, None, None, reason)


def route_path(
    commit: str, nodes: Sequence[str], changed: Sequence[str] | None
) -> ProductionPath:
    """The production path of an archived reading route, from the paths changed since.

    ``changed`` is :func:`~evals.harness.audit.changed_since` the archive's
    commit. A route whose prompt body changed is ``known-different``. A
    change to a delta alone is ``unverified``, because the archive does not
    record which transport the route used. A route whose files are the same is
    still ``unverified``: the tables a prompt renders, the model, the settings
    and the message placement of the archived call are not compared.
    """
    if changed is None:
        return ProductionPath(
            "unverified", (f"git cannot compare {commit[:8]} with the tree",)
        )
    bodies = sorted(set(changed) & {_file(node) for node in nodes})
    if bodies:
        return ProductionPath(
            "known-different",
            tuple(f"{path} changed since {commit[:8]}" for path in bodies),
        )
    deltas = sorted(
        set(changed) & {path for node in nodes for path in prompt_files(node)}
    )
    return ProductionPath(
        "unverified",
        (
            *(
                f"{path} changed since {commit[:8]}, and the archive does not"
                " record the transport that reads it"
                for path in deltas
            ),
            f"the route's prompt bodies are unchanged since {commit[:8]}",
            (
                "the rendered tables, model, settings and message placement are"
                " not compared"
            ),
        ),
    )


def prompt_files(node: str) -> frozenset[str]:
    """The prompt files ``node`` reads: its body, and for ``extract`` each delta."""
    deltas = (
        [delta for names in EXTRACTION_DELTAS.values() for delta in names]
        if node == EXTRACT_PROMPT_NAME
        else []
    )
    return frozenset(_file(name) for name in (node, *deltas))


def _file(name: str) -> str:
    return f"prompts/{name}.md"


def _source(sweep: SweepReplay) -> str:
    return f"{sweep.artifact} ({sweep.arm.label})"


def _sweep_path(sweep: SweepReplay) -> ProductionPath:
    return route_path(sweep.commit, sweep.arm.nodes, changed_since(sweep.commit, None))


def extraction_panel(sweep: SweepReplay) -> Panel:
    """The extraction panel of one replayed extraction sweep.

    Every signed-model element of every emission takes one fate. The wrong
    facts on paired elements are read per attribute over the checks compared,
    as :func:`~evals.harness.modes.attribute_agreement` counts them. An extra
    element the signed model does not hold is unreviewed: it is counted, and it
    is not a false positive.
    """
    pool = pooled_extraction([sweep])
    agreement = pool["attribute_aggregate"]
    rates = {
        f"wrong {key}": Rate(row["compared"] - row["agreed"], row["compared"])
        for key, row in agreement["by_attribute"].items()
    }
    return Panel(
        node="extract",
        source=_source(sweep),
        unit="signed-model element, once per emission",
        counts={
            **{fate: pool["fates"][fate] for fate in FATES},
            "emissions": pool["emissions"],
            "unparsed emissions": pool["unparsed"],
            "unreviewed extra elements": pool["extra"],
            "attribute checks compared": agreement["compared"],
            "attribute checks agreed": agreement["agreed"],
        },
        rates=rates,
        means={},
        unread=None,
        path=_sweep_path(sweep),
    )


def assertion_panel(sweep: SweepReplay) -> Panel:
    """The assertion panel of one replayed catalog sweep, named by its route.

    Required-fact recall pooled over every emission, and beside it the mean of
    each emission's own recall: two figures, and neither replaces the other.
    A produced row nothing signed rules on is ``unread``: it is not an error.
    """
    if not sweep.assertions:
        return uncaptured(
            "assert",
            _source(sweep),
            "the sweep holds no emission of a development case",
        )
    pool = pooled_assertions([sweep])
    route = "+".join(sweep.arm.nodes)
    return Panel(
        node="assert",
        source=f"{_source(sweep)}, catalog written by {route}",
        unit="signed reference row, once per emission",
        counts={
            **{fate: pool["fates"][fate] for fate in ROW_FATES},
            **{f"produced {fate}": pool["produced"][fate] for fate in PRODUCED_FATES},
            "emissions": pool["emissions"],
            "required rows": pool["required"],
            "required rows recovered": pool["recovered"],
            "span refusals": pool["span_refusals"],
        },
        rates={
            "pooled required-fact recall": Rate(pool["recovered"], pool["required"]),
            "produced rows assessed": Rate(pool["assessed"], pool["rows"]),
        },
        means={
            "mean of each emission's required-fact recall": (
                mean(replay.recall for replay in sweep.assertions),
                sum(replay.recall is not None for replay in sweep.assertions),
            )
        },
        unread=pool["produced"]["unreviewed"],
        path=_sweep_path(sweep),
    )


def ballot_row(ballot: Path, ledger: Sequence[Experiment]) -> Experiment | None:
    """The ledger row that lists ``ballot`` among its artifacts, if one does."""
    try:
        named = str(ballot.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return None
    return next((row for row in ledger if named in row.artifacts), None)


def capability_panel(ballot: Path, row: Experiment | None) -> Panel:
    """The capability panel of one returned ballot of the reader's statements.

    Each row is one statement a person ruled. A verdict other than
    :data:`BALLOT_VERDICTS` is ``unread``. The ballot carries no reference of
    every capability a source states, so the panel states no recall. ``row``
    is the ledger row that records the ballot; its revision is the tree the
    statements were read on.
    """
    with ballot.open(encoding="utf-8", newline="") as handle:
        verdicts = Counter(row["verdict"] for row in csv.DictReader(handle))
    right = verdicts["right"]
    ruled = sum(verdicts[verdict] for verdict in BALLOT_VERDICTS)
    path = (
        ProductionPath("unverified", ("no ledger row lists the ballot",))
        if row is None
        else route_path(
            row.revision, ("capabilities",), changed_since(row.revision, None)
        )
    )
    if row is not None:
        path = ProductionPath(
            path.fidelity, (f"ledger row {row.experiment_id}", *path.evidence)
        )
    return Panel(
        node="capabilities",
        source=str(ballot),
        unit="capability statement a person ruled",
        counts={verdict: verdicts[verdict] for verdict in BALLOT_VERDICTS},
        rates={"precision": Rate(right, ruled)},
        means={},
        unread=sum(verdicts.values()) - ruled,
        path=path,
    )


def preparation_panel(result: Delivery) -> Panel:
    """The preparation panel of one sweep's delivery trace.

    One unit is a charged must-find miss of a cause in
    :data:`~evals.harness.losses.SILENT`, which :mod:`evals.harness.delivery`
    traces. ``delivered`` reads the candidates the archived lane was sent, and
    ``rebuilt`` the candidates today's rules write for the archived model. A
    shared element is a structural overlap and not mechanism relevance.

    The path is ``known-different`` where a traced lane's captured artifacts
    differ from the rebuild. The candidates artifact holds the leads, so
    ``same_leads`` differs only where ``same_lane_artifacts`` differs too.
    Otherwise it is ``unverified`` and never ``equivalent``: the shared keys
    every lane templates, the prepared user turn and the case options have no
    rebuild from the archive, and the evidence names each of them.
    """
    lanes = [row.lane for row in result.rows if row.lane is not None]
    captured = [lane.captured for lane in lanes if lane.captured is not None]
    uncaptured = len(lanes) - len(captured)
    same_leads = Counter(each.same_leads for each in captured)
    same_artifacts = Counter(each.same_lane_artifacts for each in captured)
    delivered = Counter(each.delivered for each in captured)
    rebuilt = Counter(lane.rebuilt for lane in lanes)
    fidelity: Fidelity = (
        "known-different" if same_artifacts["differs"] else "unverified"
    )
    evidence = [
        "the delivered candidates are the lane input the archived run sent",
        "the rebuilt lane artifacts read today's corpus options",
        "not compared: the case options, which the archive does not record",
        "not compared: the prepared user turn",
        *(f"not compared: the shared key {key}" for key in LANE_SHARED_KEYS),
        *(f"{case}: lane material uncaptured" for case in sorted(result.uncaptured)),
    ]
    return Panel(
        node="prepare",
        source=result.run.artifact,
        unit=f"charged must-find miss of cause {' or '.join(sorted(SILENT))}",
        counts={
            **{f"same leads {key}": n for key, n in sorted(same_leads.items())},
            **{
                f"same lane artifacts {key}": n
                for key, n in sorted(same_artifacts.items())
            },
            "lane material uncaptured": uncaptured,
            **{f"delivered {key}": count for key, count in sorted(delivered.items())},
            **{f"rebuilt {key}": count for key, count in sorted(rebuilt.items())},
            "with an archived lead rule": sum(
                1 for lane in lanes if lane.archived_lead_rules
            ),
            "re-keyed must-finds outside the population": result.re_keyed,
        },
        rates={"lane material captured": Rate(len(captured), len(lanes))},
        means={},
        unread=uncaptured,
        path=ProductionPath(fidelity, tuple(evidence)),
    )


#: The panel each kind of kept emission feeds.
PANEL_OF_KEPT: Mapping[Kept, Callable[[SweepReplay], Panel]] = {
    "extraction": extraction_panel,
    "catalog": assertion_panel,
}


def render(panels: Sequence[Panel]) -> None:
    """Each panel on its own, in its own unit."""
    for panel in panels:
        print(f"{panel.phase} / {panel.node}: {panel.source}")
        if panel.uncaptured:
            print(f"  uncaptured: {panel.uncaptured}")
            continue
        print(f"  unit: {panel.unit}")
        for name, count in panel.counts.items():
            print(f"  {name}: {count}")
        for name, rate in panel.rates.items():
            shown = shown_figure(rate.value, ".3f")
            print(f"  {name}: {rate.numerator}/{rate.denominator} ({shown})")
        for name, (average, over) in panel.means.items():
            print(f"  {name}: {shown_figure(average, '.3f')} over {over}")
        print(f"  unread: {panel.unread}")
        if panel.path is not None:
            print(f"  production path: {panel.path.fidelity}")
            for line in panel.path.evidence:
                print(f"    {line}")
