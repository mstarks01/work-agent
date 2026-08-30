"""Every measurement a sweep reports, as one table keyed by instrument.

## Why a table

An **instrument** is one reading over a finished sweep: a per-case row, a fold
over those rows, a rendering, and the artifact keys it owns. Eight of them
exist, and each one already had all four parts — but nothing named the shape,
so each was wired by hand into four places in :mod:`evals.harness.run`: a field
on ``ModeRun``, an accumulator in ``_run_mode``, a ``_print_*`` function, and a
literal in the artifact. Adding ASVS's two instruments cost six artifact keys
and two renderers, written one at a time.

This is the rule ``docs/agents/framework-parity.md`` states for frameworks,
applied to the other axis: **prefer a table over a constant or a branch.** A
missing entry here raises at the first call; a forgotten ``_print_*`` call
quietly reported one measurement fewer.

## What is not an instrument

Certification, token usage and latency are the sweep's envelope rather than
readings over its claims: they carry no per-framework dimension, no fold that
could disagree with a printed line, and no promotion feed. They stay in
:mod:`evals.harness.run`.

## The two fields that decide when an entry runs

``frameworks`` names the packages whose record the instrument reads. Empty
means neutral — it reads whatever blocks the sweep produced. A non-empty tuple
means the instrument only applies to a sweep that ran one of those packages,
which is what lets a sweep of one framework skip another framework's scorer
rather than fail in it.

``scored`` says the reading needs the sweep's scores. The run-level instruments
render first, so a sweep whose scoring path fails still prints
the numbers that cost no provider call.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from analysis_service.report import (
    FrameworkAnalysis,
    FrameworkName,
    NodeLatency,
    Report,
    TokenUsage,
)
from evals.harness import (
    applicability,
    coverage,
    critic_yield,
    filler,
    grounds,
    instruction,
    modes,
    scorer,
    writing,
)
from evals.harness.coverage import LaneCoverage, TaggedRow
from evals.harness.critic_yield import CriticYield
from evals.harness.grounds import CaseGrounds, GroundsFailure
from evals.harness.instruction import NodeInstruction
from evals.harness.provenance import RunProvenance
from evals.harness.reference import GoldenCase
from evals.harness.scorer import CaseScore
from evals.harness.writing import CaseWriting

#: What a package's per-case scorer is handed, and what it gives back: the
#: case, that package's own block off the report, and the pre-critic drafts
#: it produced. The return is keyed by instrument name.
CaseScorer = Callable[[GoldenCase, FrameworkAnalysis, Sequence[Any]], Mapping[str, Any]]


@dataclass(frozen=True)
class ModeRun:
    """Everything one sweep of one mode produced.

    ``provenance`` is the sweep's generation identities — per node execution,
    what was requested, what answered, and the hash that pair produced. Both
    the certification verdict and the artifact are derived from it, so the
    thing a promotion reads back is the same record the verdict was computed
    from rather than a parallel one written alongside. ``expected_nodes`` is
    the *built* graph's LLM nodes, so a mode that enters at ``extract`` and
    stops is not held to the tiers it never routes through.

    ``usage`` is the sweep's token cost per node, summed across every case and
    every execution within a case. A sweep is the only place a real per-node
    number comes from — a single job's numbers are one sample of one system —
    so it is folded here rather than left to whoever reads the artifact.

    ``latency`` is the same fold over the same executions, in wall-clock. It is
    folded here for the same reason and one more: ``duration_ms`` is recorded
    per node run and read back by nothing, so a sweep that does not fold it
    measures the latency the Evaluation section of
    [#115](https://github.com/mstarks01/work-agent/issues/115) asks for and
    then throws it away.

    ``grounds`` and ``grounds_failures`` are the two halves of the same
    instrument: what the cases that finished did with ``grounds``, and what the
    cases that did not finished on. A case appears in exactly one of them.

    ``coverage`` is every case's per-lane coverage rows, unpooled, one entry per
    ``(framework, row)`` pair. One case's row is not a readable number — see
    :mod:`evals.harness.coverage` — so what rides here is the input to the
    aggregate rather than the aggregate itself, and the framework rides beside
    it because a lane slug belongs to whichever package declared it.

    ``rows`` holds what each package's own per-case scorer produced, keyed by
    the instrument that reads it — ASVS's applicability rows arrive here, one
    per case that declares the framework. They are separate from ``payloads``
    because they are different instruments: STRIDE's scorer grades an open
    claim set through the identity rule, and ASVS's is a confusion matrix over a finite
    catalog with no model call anywhere (#167). Pooling them would put two
    numbers that are not comparable under one heading.

    ``extractions`` is what the extraction mode produced and every other mode
    leaves empty. It is kept beside its own payloads because the sweep prints
    an aggregate over it, and folding a printed number out of JSON already
    written is how the printed line and the artifact come to disagree.
    """

    payloads: list[dict[str, Any]]
    failures: list[str]
    runs: dict[str, modes.AnalysisRun]
    provenance: RunProvenance
    expected_nodes: list[str]
    usage: dict[str, TokenUsage]
    latency: dict[str, NodeLatency]
    grounds: list[CaseGrounds]
    grounds_failures: list[GroundsFailure]
    coverage: list[TaggedRow]
    #: What each LLM node was told, one row per node across every graph the
    #: sweep built. Collected from the built pipelines rather than recomposed,
    #: so the size reported is the size of the text that actually ran.
    instructions: list[NodeInstruction]
    #: The frameworks this sweep's graphs were built for, so the coverage table
    #: can report a package whose every lane went silent rather than drop it.
    frameworks: tuple[FrameworkName, ...]
    extractions: list[modes.ExtractionScore]
    #: Per-case rows from the package scorers, keyed by the instrument that
    #: reads each one. The neutral instruments above keep their own fields
    #: because they read every block and need no per-package declaration; these
    #: are what one package's own record earned, so they arrive under a key
    #: rather than as a field somebody has to add.
    rows: Mapping[str, tuple[Any, ...]]
    #: Cases the estimate gate's hold stopped before, never attempted (#334).
    #: Empty on a sweep that ran to the end, which is what makes a stopped
    #: sweep a visibly partial record rather than one claiming to be whole.
    stopped_before: tuple[str, ...] = ()

    @classmethod
    def empty(cls, frameworks: tuple[FrameworkName, ...] = ()) -> ModeRun:
        """A sweep that ran ``frameworks`` and measured nothing.

        What a reading over an artifact needs: re-scoring a finished sweep
        recomputes the instruments that read the ledger and leaves every
        run-level block as the sweep wrote it, so it needs a run-shaped value
        that claims to have measured nothing rather than a second Sweep type.
        """
        return cls(
            payloads=[],
            failures=[],
            runs={},
            provenance=RunProvenance(
                sampling_config_version=1,
                tiers_config_version=1,
                sampling={},
                node_runs={},
            ),
            expected_nodes=[],
            usage={},
            latency={},
            grounds=[],
            grounds_failures=[],
            coverage=[],
            instructions=[],
            frameworks=frameworks,
            extractions=[],
            rows={},
        )

    @property
    def observations(self) -> dict[str, frozenset[str]]:
        """The node -> fingerprint sets the certification verdict rules on."""
        return self.provenance.observations()


@dataclass(frozen=True)
class Sweep:
    """One finished sweep, as every instrument reads it.

    The scored halves default to empty so the run-level instruments can render
    from the same value before scoring runs. A failed scoring pass leaves them
    empty for good, which is why every instrument's artifact keys are written
    from an empty input rather than skipped: a sweep that scored nothing and a
    sweep whose scores were all zero have to stay distinguishable.
    """

    run: ModeRun
    scores: tuple[CaseScore, ...] = ()
    yields: tuple[CriticYield, ...] = ()
    #: What reviewers said about the prose, read out of the vote ledger. Its
    #: own field rather than a row on ``run`` because it reads the ledger, and
    #: the ledger is only loaded on the scored pass.
    writing: tuple[CaseWriting, ...] = ()

    def rows(self, instrument: str) -> tuple[Any, ...]:
        """The per-case rows this instrument's scorers produced, if any.

        Empty for an instrument no package in this sweep declared a scorer for,
        which is the same shape as one whose scorer ran and found nothing. The
        difference is carried by ``run.frameworks``, which says what ran.
        """
        return self.run.rows.get(instrument, ())

    @property
    def lanes(self) -> list[LaneCoverage]:
        """The pooled coverage rows, over the frameworks the sweep built."""
        return coverage.aggregate_coverage(self.run.coverage, self.run.frameworks)


@dataclass(frozen=True)
class Column:
    """One column an instrument contributes to the published comparison table.

    ``read`` takes one sweep's artifact blocks and the framework whose block
    is being printed, and answers ``None`` where the sweep measured nothing.
    The reader lives in the module that owns the block's shape, so the
    generator names no framework and no column — it walks this table.

    ``needs_votes`` marks a number that reads zero on a cold ledger rather
    than being measured. #330: a zero reads as a measured failure, and an
    absent vote is not a measurement, so the table prints "no votes yet"
    instead.
    """

    label: str
    read: Callable[[Mapping[str, Any], FrameworkName], float | None]
    needs_votes: bool = False


@dataclass(frozen=True)
class Instrument:
    """One reading over a finished sweep: what it prints, what it writes.

    ``render`` and ``artifact`` both take the whole :class:`Sweep` so the table
    can hold them uniformly. Each instrument module keeps its own function
    signature in its own terms; the entry below is the one line that unpacks.

    ``keys`` names the artifact keys this instrument owns. It is declared rather
    than discovered so that a reader can be told what an artifact of this
    version must carry without running a sweep to find out — which is what
    :func:`~evals.harness.artifact.load_artifact` checks, and what stopped a
    dropped block from reading as a sweep that measured nothing.
    ``test_each_instrument_writes_the_keys_it_declares`` holds the two together.
    """

    render: Callable[[Sweep], None]
    artifact: Callable[[Sweep], dict[str, Any]]
    keys: tuple[str, ...]
    #: The packages whose record this instrument reads. Empty means neutral.
    frameworks: tuple[FrameworkName, ...] = ()
    #: Whether the reading needs the sweep's scores rather than only its runs.
    scored: bool = False
    #: What this instrument publishes in ``evals/baselines/README.md`` (#330).
    #: Empty means it publishes nothing there. A package that adds an
    #: instrument with columns gets them printed with no edit to the
    #: generator.
    published: tuple[Column, ...] = ()

    def applies_to(self, ran: Sequence[FrameworkName]) -> bool:
        """Whether a sweep that ran ``ran`` has anything for this instrument."""
        return not self.frameworks or bool(set(self.frameworks) & set(ran))


#: Every instrument, in the order a sweep reports them. Keyed by name so a
#: reader can name one, and so a package that adds an instrument adds a key
#: rather than editing four call sites.
INSTRUMENTS: dict[str, Instrument] = {
    "extraction": Instrument(
        render=lambda sweep: modes.render_extraction(sweep.run.extractions),
        artifact=lambda sweep: modes.artifact_extraction(sweep.run.extractions),
        keys=("attribute_aggregate",),
    ),
    "grounds": Instrument(
        render=lambda sweep: grounds.render(
            sweep.run.grounds, sweep.run.grounds_failures
        ),
        artifact=lambda sweep: grounds.artifact(
            sweep.run.grounds, sweep.run.grounds_failures
        ),
        keys=("grounds", "grounds_failures", "grounds_aggregate"),
    ),
    "coverage": Instrument(
        render=lambda sweep: coverage.render(
            sweep.lanes, offered=bool(sweep.run.coverage)
        ),
        artifact=lambda sweep: coverage.artifact(sweep.lanes),
        keys=("coverage", "coverage_totals"),
    ),
    "filler": Instrument(
        # Reads finished reports rather than a scored pass, because the defect
        # it looks for passes every offline check by construction: the suite
        # scripts the agents, so pointers always resolve and quotes always
        # verify. Neutral — it reads the shared claim and verdict shape, so a
        # package nobody has written is measured on arrival.
        render=lambda sweep: filler.render(
            filler.rows(run.report for run in sweep.run.runs.values())
        ),
        artifact=lambda sweep: filler.artifact(
            filler.rows(run.report for run in sweep.run.runs.values())
        ),
        keys=("filler",),
    ),
    "instruction": Instrument(
        render=lambda sweep: instruction.render(sweep.run.instructions),
        artifact=lambda sweep: instruction.artifact(sweep.run.instructions),
        keys=("instruction", "instruction_totals"),
    ),
    "applicability": Instrument(
        render=lambda sweep: applicability.render(sweep.rows("applicability")),
        artifact=lambda sweep: applicability.artifact(sweep.rows("applicability")),
        frameworks=("asvs",),
        keys=(
            "applicability",
            "applicability_aggregate",
            "applicability_exemplar_delta",
            "over_applied_for_promotion",
        ),
        published=(
            Column(
                "recall", lambda blocks, _: applicability.published(blocks, "recall")
            ),
            Column(
                "precision",
                lambda blocks, _: applicability.published(blocks, "precision"),
            ),
        ),
    ),
    "disposition": Instrument(
        # ASVS's second per-case instrument. It reads the same block the matrix
        # does and answers the other half of the question: not whether the
        # requirement is in play, but whether the run reached the right next
        # action for it (#471).
        render=lambda sweep: applicability.render_dispositions(
            sweep.rows("disposition")
        ),
        artifact=lambda sweep: applicability.disposition_artifact(
            sweep.rows("disposition")
        ),
        frameworks=("asvs",),
        keys=("disposition", "disposition_aggregate"),
        published=(
            Column(
                "disp_acc",
                lambda blocks, _: applicability.published_disposition(
                    blocks, "accuracy"
                ),
            ),
            Column(
                "false_prose",
                lambda blocks, _: applicability.published_disposition(
                    blocks, "false_prose_request_rate"
                ),
            ),
        ),
    ),
    "applicability_yield": Instrument(
        render=lambda sweep: applicability.render_yield(
            sweep.rows("applicability_yield")
        ),
        artifact=lambda sweep: applicability.artifact_yield(
            sweep.rows("applicability_yield")
        ),
        frameworks=("asvs",),
        keys=("applicability_yield", "applicability_yield_aggregate"),
    ),
    "scores": Instrument(
        render=lambda sweep: scorer.render(sweep.scores),
        artifact=lambda sweep: scorer.artifact(sweep.scores),
        frameworks=("stride",),
        scored=True,
        keys=("scores", "exemplar_delta", "unlisted_for_promotion"),
        published=(
            Column("recall", lambda blocks, _: scorer.published(blocks, "recall")),
            Column(
                "must-find recall",
                lambda blocks, _: scorer.published(blocks, "must_find_recall"),
            ),
            Column(
                "rejected rate",
                lambda blocks, _: scorer.published(blocks, "rejected_rate"),
                needs_votes=True,
            ),
        ),
    ),
    "critic_yield": Instrument(
        render=lambda sweep: critic_yield.render(sweep.yields),
        artifact=lambda sweep: critic_yield.artifact(sweep.yields),
        frameworks=("stride",),
        scored=True,
        keys=("critic_yield", "critic_yield_aggregate"),
    ),
    "writing": Instrument(
        render=lambda sweep: writing.render(sweep.writing),
        artifact=lambda sweep: writing.artifact(sweep.writing),
        scored=True,
        keys=("writing", "writing_aggregate"),
        published=(
            Column(
                "writing objections",
                lambda blocks, framework: writing.published(blocks, framework),
                needs_votes=True,
            ),
        ),
    ),
}


def render_all(sweep: Sweep, *, scored: bool) -> None:
    """Print one half of the table: the run-level readings, or the scored ones.

    The split is what keeps a provider failure from costing the numbers that
    never needed a provider. It reads ``scored`` off each entry rather than
    naming instruments, so a new mechanical instrument prints in the free pass
    with no edit here.
    """
    for instrument in INSTRUMENTS.values():
        if instrument.scored == scored and instrument.applies_to(sweep.run.frameworks):
            instrument.render(sweep)


def artifact_blocks(sweep: Sweep) -> dict[str, Any]:
    """Every instrument's artifact keys, merged.

    **Every entry writes its keys, including the ones this sweep's frameworks
    did not exercise.** An ASVS key absent from a STRIDE-only artifact and an
    ASVS key holding an empty list are different claims to a reader comparing
    two sweeps, and only the second one is true. ``applies_to`` gates the
    *rendering*, never the record.
    """
    blocks: dict[str, Any] = {}
    for name, instrument in INSTRUMENTS.items():
        keys = instrument.artifact(sweep)
        clash = set(keys) & set(blocks)
        if clash:
            raise ValueError(
                f"instrument {name!r} writes artifact keys another instrument"
                f" already owns: {sorted(clash)}"
            )
        blocks |= keys
    return blocks


#: The per-case scorer each package's own record earns, beyond the
#: framework-neutral instruments every block already gets.
#:
#: **Keyed, never branched.** A package added to
#: :data:`~analysis_service.frameworks.PACKAGES` and missing here raises at the
#: first case that carries its block, which is the whole reason this is a table:
#: the ``if`` it replaced dispatched to one package by name and would have gone
#: on scoring nothing for a third, quietly.
#:
#: ``None`` is a declaration, not a hole. It says this package earns no
#: *per-case* scorer, which is true of a package whose numbers pool rather than
#: fold, as STRIDE's do. It does not say the package goes unmeasured: the
#: neutral instruments read every block, and a sweep-level instrument may still
#: name the package on its ``frameworks`` — ``scores`` and ``critic_yield``
#: both name STRIDE. ``test_every_package_declares_a_scorer`` is what keeps
#: this entry a decision.
PACKAGE_SCORERS: dict[FrameworkName, CaseScorer | None] = {
    "stride": None,
    "asvs": applicability.score_case,
}


def score_blocks(
    case: GoldenCase, report: Report, drafts: Mapping[FrameworkName, Sequence[Any]]
) -> dict[str, Any]:
    """Every block's own per-case rows, keyed by the instrument that reads each.

    Walks the report's blocks rather than asking for a package by name, so a
    sweep measures what it ran. A package whose scorer is ``None`` contributes
    nothing here and is still measured by every neutral instrument.
    """
    scored: dict[str, Any] = {}
    for block in report.analyses:
        scorer = PACKAGE_SCORERS[block.framework]
        if scorer is None:
            continue
        scored |= scorer(case, block, drafts.get(block.framework, ()))
    return scored


@dataclass(frozen=True)
class CaseMeasurement:
    """Every reading one finished case offers, before any of it is pooled.

    One value rather than four appends into four accumulators. The sweep loop
    used to hold the whole of this inline, which meant an instrument that
    measures a case grew the loop; now it grows this function and the table,
    and the loop stays "run the case, measure it, keep the result".

    ``rows`` is keyed by instrument name, the way :func:`score_blocks` returns
    it, so a package that earns a per-case scorer needs nothing here either.
    """

    payload: dict[str, Any]
    grounds: list[Any]
    coverage: list[TaggedRow]
    rows: Mapping[str, Any]


def measure_case(
    case: GoldenCase, run: Any, structural_issues: Sequence[str]
) -> CaseMeasurement:
    """Read one finished case, over every block it produced.

    **Every block the job selected, not one package's alone.** Coverage and
    grounds are folds over what a lane agent was offered and what its drafts
    cite, and no package is exempt from either — ADR 0002 exempts none from
    finding-level attribution, and Coverage is reported per lane of every
    framework a job runs.

    The measurement rides in the artifact rather than in the report, so a reader
    gets the number without opening a second file. The report is kept too, in
    the directory beside the artifact, and that is what a question this payload
    did not anticipate is answered from.
    """
    measurements = []
    rows_by_lane: list[TaggedRow] = []
    for block in run.report.analyses:
        rows_by_lane += [(block.framework, row) for row in block.coverage]
        measurements.append(
            grounds.measure_grounds(
                case.id,
                block.framework,
                run.drafts.get(block.framework, ()),
                block.unverified_grounds,
                block.dropped_claims,
                block.repaired_quotes,
            )
        )
    scored = score_blocks(case, run.report, run.drafts)
    payload: dict[str, Any] = {
        "case": case.id,
        "structural_issues": list(structural_issues),
        "grounds": [entry.to_json() for entry in measurements],
        **{name: row.to_json() for name, row in scored.items()},
    }
    return CaseMeasurement(
        payload=payload,
        grounds=measurements,
        coverage=rows_by_lane,
        rows=scored,
    )
