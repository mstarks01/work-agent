"""Every measurement a sweep reports, as one table keyed by instrument.

## Why a table

An **instrument** is one reading over a finished sweep: a per-case row, a fold
over those rows, a rendering, and the artifact keys it owns. Seven of them
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

``judged`` says the reading needs the judge. The mechanical instruments render
before the judge is built, so a sweep that cannot reach a provider still prints
the numbers that cost no provider call.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from evals.harness import applicability, coverage, critic_yield, grounds, modes, scorer
from evals.harness.applicability import ApplicabilityScore, ApplicabilityYield
from evals.harness.coverage import LaneCoverage, TaggedRow
from evals.harness.critic_yield import CriticYield
from evals.harness.grounds import CaseGrounds, GroundsFailure
from evals.harness.provenance import RunProvenance
from evals.harness.scorer import CaseScore
from stride_service.report import FrameworkName, NodeLatency, TokenUsage


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

    ``applicability`` is the ASVS half, one row per case that declares the
    framework. It is a separate list rather than a column on ``payloads``
    because it is a different instrument: STRIDE's scorer grades an open claim
    set through a judge, and this one is a confusion matrix over a finite
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
    #: The frameworks this sweep's graphs were built for, so the coverage table
    #: can report a package whose every lane went silent rather than drop it.
    frameworks: tuple[FrameworkName, ...]
    extractions: list[modes.ExtractionScore]
    applicability: list[ApplicabilityScore]
    applicability_yields: list[ApplicabilityYield]

    @property
    def observations(self) -> dict[str, frozenset[str]]:
        """The node -> fingerprint sets the certification verdict rules on."""
        return self.provenance.observations()


@dataclass(frozen=True)
class Sweep:
    """One finished sweep, as every instrument reads it.

    The judged halves default to empty so the mechanical instruments can render
    from the same value before a judge exists. ``--no-scoring`` leaves them
    empty for good, which is why every instrument's artifact keys are written
    from an empty input rather than skipped: a sweep that scored nothing and a
    sweep whose scores were all zero have to stay distinguishable.
    """

    run: ModeRun
    scores: tuple[CaseScore, ...] = ()
    yields: tuple[CriticYield, ...] = ()

    @property
    def lanes(self) -> list[LaneCoverage]:
        """The pooled coverage rows, over the frameworks the sweep built."""
        return coverage.aggregate_coverage(self.run.coverage, self.run.frameworks)


@dataclass(frozen=True)
class Instrument:
    """One reading over a finished sweep: what it prints, what it writes.

    ``render`` and ``artifact`` both take the whole :class:`Sweep` so the table
    can hold them uniformly. Each instrument module keeps its own function
    signature in its own terms; the entry below is the one line that unpacks.
    """

    render: Callable[[Sweep], None]
    artifact: Callable[[Sweep], dict[str, Any]]
    #: The packages whose record this instrument reads. Empty means neutral.
    frameworks: tuple[FrameworkName, ...] = ()
    #: Whether the reading needs the judge.
    judged: bool = False

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
    ),
    "grounds": Instrument(
        render=lambda sweep: grounds.render(
            sweep.run.grounds, sweep.run.grounds_failures
        ),
        artifact=lambda sweep: grounds.artifact(
            sweep.run.grounds, sweep.run.grounds_failures
        ),
    ),
    "coverage": Instrument(
        render=lambda sweep: coverage.render(
            sweep.lanes, offered=bool(sweep.run.coverage)
        ),
        artifact=lambda sweep: coverage.artifact(sweep.lanes),
    ),
    "applicability": Instrument(
        render=lambda sweep: applicability.render(sweep.run.applicability),
        artifact=lambda sweep: applicability.artifact(sweep.run.applicability),
        frameworks=("asvs",),
    ),
    "applicability_yield": Instrument(
        render=lambda sweep: applicability.render_yield(sweep.run.applicability_yields),
        artifact=lambda sweep: applicability.artifact_yield(
            sweep.run.applicability_yields
        ),
        frameworks=("asvs",),
    ),
    "scores": Instrument(
        render=lambda sweep: scorer.render(sweep.scores),
        artifact=lambda sweep: scorer.artifact(sweep.scores),
        frameworks=("stride",),
        judged=True,
    ),
    "critic_yield": Instrument(
        render=lambda sweep: critic_yield.render(sweep.yields),
        artifact=lambda sweep: critic_yield.artifact(sweep.yields),
        frameworks=("stride",),
        judged=True,
    ),
}


def render_all(sweep: Sweep, *, judged: bool) -> None:
    """Print one half of the table: the mechanical readings, or the judged ones.

    The split is what keeps a provider failure from costing the numbers that
    never needed a provider. It reads ``judged`` off each entry rather than
    naming instruments, so a new mechanical instrument prints in the free pass
    with no edit here.
    """
    for instrument in INSTRUMENTS.values():
        if instrument.judged == judged and instrument.applies_to(sweep.run.frameworks):
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
