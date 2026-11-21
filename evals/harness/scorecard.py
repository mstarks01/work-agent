"""The must-find scorecard: one row per STRIDE must-find, by lane and stage.

A re-score of an archived sweep on today's corpus mixes two facts. Some
references the run could not answer under today's contract, and some it
answered or missed. This module keeps them apart. Each must-find of each
development case the sweep lists gets one row with one exclusive
:data:`Status`, so the counts reconcile to the reference population and no
incompatible row is dropped. :attr:`Scorecard.left_out` names the sweep's
other cases.

**Compatibility.** A row is ``compatible`` when today's scorer can read the
run's answer to it. The other values say why it cannot:

* ``uncaptured``: the sweep wrote no STRIDE block for the case.
* ``re-keyed``: the reference resolves to an element the run's analysed
  model does not hold, because the corpus renamed or re-keyed it after the
  run. A cited flow resolves to its two endpoints first, as the scorer
  resolves it. A reference the matcher matched at a stage is never
  ``re-keyed``, because the scorer read it.
* ``position-unstated``: a person ruled the reference an attacker position,
  and :func:`~evals.harness.scorer.none_stated` reads every draft of the
  case as ``unstated`` against it. The archive holds no ``attacker_position``,
  so the row is not a lane's choice of attacker. A missing position is never
  inferred from prose.

**Stages.** A reference is *represented* at a stage when the structural
matcher matches it to a claim of that stage. ``drafted`` reads the drafts the
critic was handed and ``reported`` reads the report's claims, through one
matcher and :func:`~evals.harness.scorer.score_case`. ``proposed`` is the
lanes' proposals before the fan-in. The structural matcher does not read a
proposal, so that stage is uncaptured on every row. An uncaptured stage is
never a zero.

**Replay checks.** Beside the rows, :class:`AssemblyCheck` and
:class:`FanInCheck` run assembly and the fan-in again on the archive. The
fan-in check counts proposals by the ID the fan-in gives them, not
references, so it does not fill the ``proposed`` stage.

**Rates.** Each rate is a :class:`Rate` with its numerator and denominator.
An empty denominator gives ``None``, which prints as unavailable
(:func:`~evals.harness.rates.ratio`). A rate here is a share of matched
must-finds, not the per-lane **Coverage** of ``GLOSSARY.md``.

**One run.** :func:`summarize` refuses rows of more than one
:class:`RunIdentity`, so two sweeps, two corpus revisions or two scorer trees
never render as one funnel.

Scored by the STRIDE scorer only. The rows read no vote status. The critic
audit reads the vote ledger, because a vote is one of the labels its
populations rest on.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, get_args

from evals.harness.artifact import EvalArtifact, RepoCommit, ScoredAgainst
from evals.harness.assembly import AssemblySweep
from evals.harness.audit import Phase, phase_of
from evals.harness.critic_yield import CriticAudit
from evals.harness.fan_in_accounting import FanInSweep
from evals.harness.identity import CaseFlows, endpoint_form
from evals.harness.losses import UNREAD_CAUSE, CaseLosses, Cause
from evals.harness.rates import Rate
from evals.harness.reference import GoldenCase
from evals.harness.scorer import none_stated
from evals.harness.triggers import case_trigger_recall
from katra.frameworks.stride.record import (
    STRIDE_CATEGORIES,
    AttackerPosition,
    DraftThreat,
    StrideCategory,
)
from katra.system_model import SystemModel

Compatibility = Literal["compatible", "uncaptured", "re-keyed", "position-unstated"]

#: One exclusive accounting status per row. ``incompatible`` is every
#: compatibility other than ``compatible`` and ``uncaptured``.
Status = Literal["matched", "missed", "incompatible", "uncaptured"]
STATUSES: tuple[Status, ...] = get_args(Status)

Stage = Literal["proposed", "drafted", "reported"]
STAGES: tuple[Stage, ...] = get_args(Stage)

#: Each stage change, as (before, after, the graph node between them). Its
#: phase is :func:`~evals.harness.audit.phase_of` that node, which reads
#: :data:`~evals.harness.audit.PHASES`.
CHANGES: tuple[tuple[Stage, Stage, str], ...] = (
    ("proposed", "drafted", "merge"),
    ("drafted", "reported", "critic"),
)


@dataclass(frozen=True)
class RunIdentity:
    """Which sweep a row reads, and which corpus and tree scored it."""

    artifact: str
    run_commit: RepoCommit
    run_corpus: str
    scored: ScoredAgainst

    def to_json(self) -> dict[str, Any]:
        return {
            "artifact": self.artifact,
            "run_commit": self.run_commit.model_dump(),
            "run_corpus": self.run_corpus,
            "scored": self.scored.model_dump(),
        }


def run_identity(artifact: EvalArtifact, scored: ScoredAgainst) -> RunIdentity:
    """The identity every row of one scorecard carries."""
    return RunIdentity(
        artifact=artifact.path.name,
        run_commit=artifact.commit,
        run_corpus=artifact.corpus_digest,
        scored=scored,
    )


@dataclass(frozen=True)
class ReferenceRow:
    """One must-find reference of one case, as one run answered it."""

    run: RunIdentity
    case: str
    lane: StrideCategory
    reference_index: int
    verb: str | None
    elements: tuple[str, ...]
    ruled_position: AttackerPosition | None
    compatibility: Compatibility
    #: Whether the matcher matches the reference at each stage. ``None`` where
    #: the stage is uncaptured.
    stages: Mapping[Stage, bool | None]
    #: The cause :mod:`evals.harness.losses` charged a miss to. ``None`` for a
    #: match and an uncaptured case.
    cause: Cause | None
    #: The STRIDE rules that fire on the reference's elements in today's
    #: corpus ``model.json``, read through
    #: :func:`~evals.harness.triggers.case_trigger_recall`. Empty means no rule
    #: leads there today. Whether a rule led the run's lane there is the
    #: ``place`` against ``unled`` split of :attr:`cause`, which reads the
    #: run's own model and its archived ``fired_rules``.
    lead_rules: tuple[str, ...]

    @property
    def status(self) -> Status:
        if self.compatibility == "uncaptured":
            return "uncaptured"
        if self.compatibility != "compatible":
            return "incompatible"
        return "matched" if self.stages["reported"] else "missed"

    def to_json(self) -> dict[str, Any]:
        return {
            "run": self.run.to_json(),
            "case": self.case,
            "lane": self.lane,
            "reference_index": self.reference_index,
            "verb": self.verb,
            "elements": list(self.elements),
            "ruled_position": self.ruled_position,
            "compatibility": self.compatibility,
            "status": self.status,
            "stages": dict(self.stages),
            "cause": self.cause,
            "lead_rules": list(self.lead_rules),
        }


@dataclass(frozen=True)
class CaseCapture:
    """What one finished case of a run holds for the scorecard.

    ``drafted`` and ``reported`` are the reference indices the matcher matches
    over the drafts the critic was handed and over the report's claims.
    """

    analysed: SystemModel
    flows: CaseFlows
    drafted: frozenset[int]
    reported: frozenset[int]
    #: The attacker positions the drafts state, ``None`` for a draft that
    #: states none. The lane's proposal schema requires a position, so only an
    #: archive whose drafts carry no ``attacker_position`` holds ``None``.
    stated_positions: frozenset[AttackerPosition | None]


def capture(
    analysed: SystemModel,
    flows: CaseFlows,
    drafts: Sequence[DraftThreat],
    drafted: Iterable[int],
    reported: Iterable[int],
) -> CaseCapture:
    """One finished case's :class:`CaseCapture`."""
    return CaseCapture(
        analysed=analysed,
        flows=flows,
        drafted=frozenset(drafted),
        reported=frozenset(reported),
        stated_positions=frozenset(draft.attacker_position for draft in drafts),
    )


def _compatibility(
    held: CaseCapture | None,
    index: int,
    elements: Sequence[str],
    position: AttackerPosition | None,
) -> Compatibility:
    if held is None:
        return "uncaptured"
    # The scorer's own fold: a cited flow resolves to its two endpoints
    # through the flow map, so a flow the corpus renamed is not re-keyed. The
    # identity rule accepts a draft whose place the reference's contains, so a
    # reference the matcher matched at some stage was readable.
    present = {element.id for element in held.analysed.elements()}
    matched = index in held.drafted or index in held.reported
    if not matched and not endpoint_form(elements, held.flows) <= present:
        return "re-keyed"
    if none_stated(position, held.stated_positions):
        return "position-unstated"
    return "compatible"


def case_rows(
    run: RunIdentity,
    case: GoldenCase,
    held: CaseCapture | None,
    losses: CaseLosses | None,
) -> tuple[ReferenceRow, ...]:
    """One row for each STRIDE must-find of ``case``.

    ``held`` is ``None`` where the sweep wrote no STRIDE block for the case.
    """
    cause_of = (
        {loss.reference_index: loss.cause for loss in losses.losses} if losses else {}
    )
    hits = case_trigger_recall(case, "stride").hits
    rows = []
    for index, reference in enumerate(case.stride_claims()):
        if not reference.must_find:
            continue
        position = case.ruled_positions.get(index)
        elements = tuple(reference.affected_element_ids)
        rows.append(
            ReferenceRow(
                run=run,
                case=case.id,
                lane=reference.category,
                reference_index=index,
                verb=reference.verb,
                elements=elements,
                ruled_position=position,
                compatibility=_compatibility(held, index, elements, position),
                stages={
                    "proposed": None,
                    "drafted": index in held.drafted if held else None,
                    "reported": index in held.reported if held else None,
                },
                cause=cause_of.get(index),
                lead_rules=hits[index].rule_ids,
            )
        )
    return tuple(rows)


@dataclass(frozen=True)
class StageChange:
    """What one stage change did to the compatible references it can observe.

    ``observed`` counts the compatible rows that captured both stages.
    ``lost`` are represented before and not after, ``gained`` the reverse.
    Where no row captured both stages, ``observed`` is 0 and
    ``represented_before``, ``lost`` and ``gained`` are ``None``.
    """

    before: Stage
    after: Stage
    phase: Phase
    observed: int
    represented_before: int | None
    lost: int | None
    gained: int | None

    @property
    def loss(self) -> Rate | None:
        if self.lost is None or self.represented_before is None:
            return None
        return Rate(self.lost, self.represented_before)

    @property
    def net(self) -> int | None:
        if self.lost is None or self.gained is None:
            return None
        return self.gained - self.lost

    def to_json(self) -> dict[str, Any]:
        loss = self.loss
        return {
            "before": self.before,
            "after": self.after,
            "phase": self.phase,
            "observed": self.observed,
            "represented_before": self.represented_before,
            "lost": self.lost,
            "gained": self.gained,
            "net": self.net,
            "loss": loss.to_json() if loss else None,
        }


@dataclass(frozen=True)
class LaneSummary:
    """One lane's rows, or every lane's under the lane ``all``."""

    lane: StrideCategory | Literal["all"]
    by_status: Mapping[Status, int]
    #: Matched over the compatible rows: the comparable subset.
    compatible_matched: Rate
    #: Matched over every row: the full population, beside the subset.
    population_matched: Rate
    #: Per stage, the compatible rows represented there over the compatible
    #: rows that captured the stage.
    #: ``None`` where no compatible row captured it.
    represented: Mapping[Stage, Rate | None]
    changes: tuple[StageChange, ...]

    @property
    def total(self) -> int:
        return sum(self.by_status.values())

    def to_json(self) -> dict[str, Any]:
        return {
            "lane": self.lane,
            "total": self.total,
            "by_status": dict(self.by_status),
            "compatible_matched": self.compatible_matched.to_json(),
            "population_matched": self.population_matched.to_json(),
            "represented": {
                stage: rate.to_json() if rate else None
                for stage, rate in self.represented.items()
            },
            "changes": [change.to_json() for change in self.changes],
        }


class MixedRunsError(ValueError):
    """Rows of more than one run, which no scorecard pools."""


def _change(
    rows: Sequence[ReferenceRow], before: Stage, after: Stage, node: str
) -> StageChange:
    pairs = [
        (row.stages[before], row.stages[after])
        for row in rows
        if row.stages[before] is not None and row.stages[after] is not None
    ]
    phase = phase_of(node)
    if not pairs:
        return StageChange(before, after, phase, 0, None, None, None)
    return StageChange(
        before=before,
        after=after,
        phase=phase,
        observed=len(pairs),
        represented_before=sum(1 for was, _ in pairs if was),
        lost=sum(1 for was, now in pairs if was and not now),
        gained=sum(1 for was, now in pairs if now and not was),
    )


def _represented(rows: Sequence[ReferenceRow], stage: Stage) -> Rate | None:
    captured = [row.stages[stage] for row in rows if row.stages[stage] is not None]
    return (
        Rate(sum(1 for here in captured if here), len(captured)) if captured else None
    )


def _lane(
    lane: StrideCategory | Literal["all"], rows: Sequence[ReferenceRow]
) -> LaneSummary:
    counts = Counter(row.status for row in rows)
    compatible = [row for row in rows if row.compatibility == "compatible"]
    matched = counts["matched"]
    return LaneSummary(
        lane=lane,
        by_status={status: counts[status] for status in STATUSES},
        compatible_matched=Rate(matched, len(compatible)),
        population_matched=Rate(matched, len(rows)),
        represented={stage: _represented(compatible, stage) for stage in STAGES},
        changes=tuple(
            _change(compatible, before, after, node) for before, after, node in CHANGES
        ),
    )


@dataclass(frozen=True)
class AssemblyCheck:
    """Whether assembly, run again, rebuilds each archived STRIDE block."""

    reproduced: int
    differ: tuple[str, ...]
    refused: tuple[str, ...]
    #: Finished cases whose report today's ``Report`` refuses.
    unreadable: int
    #: Cases whose report or drafts file the archive lacks, or holds for a
    #: case it records as failed: a missing or a stray capture.
    capture_faults: int

    def to_json(self) -> dict[str, Any]:
        return {
            "reproduced": self.reproduced,
            "differ": list(self.differ),
            "refused": list(self.refused),
            "unreadable": self.unreadable,
            "capture_faults": self.capture_faults,
        }


def assembly_check(sweep: AssemblySweep) -> AssemblyCheck:
    """The STRIDE half of an :func:`~evals.harness.assembly.replay_artifact` sweep."""
    stride = [replay for replay in sweep.rows if replay.framework == "stride"]
    return AssemblyCheck(
        reproduced=sum(
            1 for replay in stride if not replay.refused and not replay.unexplained
        ),
        differ=tuple(
            replay.case
            for replay in stride
            if not replay.refused and replay.unexplained
        ),
        refused=tuple(replay.case for replay in stride if replay.refused),
        unreadable=len(sweep.unreadable),
        capture_faults=len(sweep.capture_faults),
    )


@dataclass(frozen=True)
class FanInCheck:
    """Where the fan-in, run again, puts each proposal of each archived STRIDE block.

    ``inspected`` counts every proposal of every block, as
    :func:`~evals.harness.fan_in_accounting.command_fan_in` does. A block the
    replay cannot stand for is incompatible, with its reason, and its
    proposals are ``unavailable``: never accounted or unexplained. The
    compatible blocks' proposals are ``accounted`` or ``unexplained``, and
    ``extra`` counts the output IDs no proposal carries. An incompatible block
    whose proposals the replay could not read adds nothing to ``unavailable``,
    and ``unread_blocks`` counts it, so its proposals never read as zero.
    """

    compatible_blocks: int
    incompatible: tuple[tuple[str, str], ...]
    unread_blocks: int
    inspected: int
    accounted: int
    unexplained: int
    extra: int
    unavailable: int
    #: Finished cases whose report today's ``Report`` refuses.
    unreadable: int
    #: Capture faults, one line each: a report, drafts or proposals file the
    #: archive lacks, holds for a case it records as failed, or holds unparsed,
    #: and a capture that holds a framework the report has no block for.
    capture_faults: int

    @property
    def accounted_rate(self) -> Rate:
        """Accounted over the proposals of the compatible blocks."""
        return Rate(self.accounted, self.inspected - self.unavailable)

    def to_json(self) -> dict[str, Any]:
        return {
            "compatible_blocks": self.compatible_blocks,
            "incompatible": [
                {"case": case, "reason": reason} for case, reason in self.incompatible
            ],
            "unread_blocks": self.unread_blocks,
            "inspected": self.inspected,
            "accounted": self.accounted,
            "accounted_rate": self.accounted_rate.to_json(),
            "unexplained": self.unexplained,
            "extra": self.extra,
            "unavailable": self.unavailable,
            "unreadable": self.unreadable,
            "capture_faults": self.capture_faults,
        }


def fan_in_check(sweep: FanInSweep) -> FanInCheck:
    """The STRIDE half of a :func:`~evals.harness.fan_in_accounting.replay_artifact` sweep."""
    stride = [row for row in sweep.rows if row.framework == "stride"]
    compatible = [row for row in stride if not row.incompatible]
    incompatible = [row for row in stride if row.incompatible]
    return FanInCheck(
        compatible_blocks=len(compatible),
        incompatible=tuple((row.case, row.incompatible) for row in incompatible),
        unread_blocks=sum(1 for row in incompatible if not row.proposals),
        inspected=sum(len(row.proposals) for row in stride),
        accounted=sum(row.accounted for row in compatible),
        unexplained=sum(len(row.unexplained) for row in compatible),
        extra=sum(len(row.extra) for row in compatible),
        unavailable=sum(len(row.proposals) for row in incompatible),
        unreadable=len(sweep.unreadable),
        capture_faults=len(sweep.capture_faults),
    )


@dataclass(frozen=True)
class Scorecard:
    """One run's rows, its six lanes and their total, and the replay checks."""

    run: RunIdentity
    rows: tuple[ReferenceRow, ...]
    lanes: tuple[LaneSummary, ...]
    assembly: AssemblyCheck
    fan_in: FanInCheck
    #: The sweep's cases that have no row: holdout cases, which no diagnosis
    #: reads, and cases the corpus no longer holds.
    left_out: tuple[str, ...]
    critic: CriticAudit

    def to_json(self) -> dict[str, Any]:
        return {
            "run": self.run.to_json(),
            "left_out": list(self.left_out),
            "lanes": [lane.to_json() for lane in self.lanes],
            "assembly": self.assembly.to_json(),
            "fan_in": self.fan_in.to_json(),
            "critic": self.critic.to_json(),
            "rows": [row.to_json() for row in self.rows],
        }


def summarize(
    rows: Sequence[ReferenceRow],
    assembly: AssemblyCheck,
    fan_in: FanInCheck,
    left_out: Sequence[str],
    critic: CriticAudit,
) -> Scorecard:
    """The scorecard of one run's rows. Raises :class:`MixedRunsError` on two runs."""
    runs = {row.run for row in rows}
    if len(runs) > 1:
        raise MixedRunsError(
            f"a scorecard reads one run, and these rows carry {len(runs)}"
        )
    if not runs:
        raise ValueError("a scorecard needs at least one row")
    (run,) = runs
    lanes = [
        _lane(lane, [row for row in rows if row.lane == lane])
        for lane in STRIDE_CATEGORIES
    ]
    return Scorecard(
        run=run,
        rows=tuple(rows),
        lanes=(*lanes, _lane("all", rows)),
        assembly=assembly,
        fan_in=fan_in,
        left_out=tuple(left_out),
        critic=critic,
    )


def charged_misses(rows: Iterable[ReferenceRow]) -> tuple[ReferenceRow, ...]:
    """The misses :mod:`evals.harness.losses` charged to a cause the run can read.

    Every row with a cause other than
    :data:`~evals.harness.losses.UNREAD_CAUSE`, except a ``re-keyed`` row,
    whose cause rests on an element the run's model did not hold. An
    ``unstated`` row is the archive's missing position, not a
    lane's miss. A ``position-unstated`` row stays: its action and place were
    read, and only its position was not. Each row carries its run identity
    and ``lead_rules``.
    """
    return tuple(
        row
        for row in rows
        if row.cause not in (None, UNREAD_CAUSE) and row.compatibility != "re-keyed"
    )


def _rate(rate: Rate | None) -> str:
    return "uncaptured" if rate is None else rate.shown()


def render(card: Scorecard) -> None:
    """The lane table, the stage table and the panels no reader fills yet."""
    run = card.run
    print(
        f"{run.artifact}: run {run.run_commit.commit[:8]} on corpus"
        f" {run.run_corpus[:12]}, scored on corpus {run.scored.corpus_digest[:12]}"
        f" at {run.scored.repo_commit.commit[:8]}"
    )
    print(
        f"{'lane':<24}{'total':>6}{'match':>6}{'miss':>6}{'incomp':>7}{'uncapt':>8}"
        "  matched of compatible matched of all"
    )
    for lane in card.lanes:
        status = lane.by_status
        print(
            f"{lane.lane:<24}{lane.total:>6}{status['matched']:>6}{status['missed']:>6}"
            f"{status['incompatible']:>7}{status['uncaptured']:>8}"
            f"  {_rate(lane.compatible_matched):<21} {_rate(lane.population_matched)}"
        )
    every = card.lanes[-1]
    print("compatible rows represented at each stage:")
    for stage, rate in every.represented.items():
        print(f"  {stage:<10} {_rate(rate)}")
    print("stage changes over the compatible rows:")
    for change in every.changes:
        print(
            f"  {change.before} -> {change.after} ({change.phase}):"
            f" observed {change.observed}, lost {change.lost}, gained"
            f" {change.gained}, net {change.net}, loss {_rate(change.loss)}"
        )
    incompatible = Counter(
        row.compatibility for row in card.rows if row.status == "incompatible"
    )
    print(f"incompatible rows by reason: {dict(sorted(incompatible.items()))}")
    print(f"cases with no row (holdout, or not in the corpus): {list(card.left_out)}")
    check = card.assembly
    print(
        f"assembly run again: {check.reproduced} block(s) reproduced,"
        f" {len(check.differ)} differ, {len(check.refused)} refused,"
        f" {check.unreadable} report(s) unreadable,"
        f" {check.capture_faults} case(s) with a missing or stray capture"
    )
    charged = charged_misses(card.rows)
    led = sum(1 for row in charged if row.lead_rules)
    by_cause = Counter(row.cause for row in charged)
    print(
        f"charged misses: {len(charged)} ({led} with a rule today's code fires,"
        f" {len(charged) - led} with none), by cause {dict(sorted(by_cause.items()))}"
    )
    fan = card.fan_in
    print(
        f"fan-in run again: {fan.compatible_blocks} block(s) compatible,"
        f" {len(fan.incompatible)} incompatible ({fan.unread_blocks} with no"
        f" proposal read); proposals {fan.inspected}"
        f" inspected, {fan.accounted} accounted ({_rate(fan.accounted_rate)}),"
        f" {fan.unexplained} unexplained, {fan.extra} extra output ID(s),"
        f" {fan.unavailable} unavailable; {fan.unreadable} report(s) unreadable,"
        f" {fan.capture_faults} capture fault(s)"
    )
    for case, reason in fan.incompatible:
        print(f"  fan-in incompatible: {case}: {reason}")
    card.critic.render()
    print("repeats: 1; run-to-run variation unmeasured")
