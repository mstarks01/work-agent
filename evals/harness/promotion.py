"""The gates #926's promotion decision is read against, declared before the runs.

A threshold chosen after the numbers arrive is not a threshold. #926's
acceptance list says so in its own words — *do not choose thresholds
retrospectively* — so this table is written now, against the one baseline that
exists, and the runs that follow are read against it rather than the other way
round.

**What is being decided.** Whether ``ANALYSIS_ASSERTIONS`` ships on by default.
The layer is complete as code and off by default, so the decision is a
deployment one: a job that sets the flag pays one base-tier call and gets a
catalog its lanes may cite.

**Two kinds of gate, and the difference matters.**

*Resource* gates are ratios against the baseline arm, and one pair measures
them: a charge and a latency do not need a spread to be read, they need a
budget somebody set in advance. Those budgets are here, with the measurement
they were set from.

*Quality* gates are differences in a scored figure, and one pair cannot read
one. The corpus spread is 3.37 must-finds, and case 01 carries eight, so a
single pair is inside the noise by construction. A quality gate here therefore
declares its **limit in standard deviations of the repeat spread**, and
:func:`decide` returns ``inconclusive`` for it until a caller supplies a spread
measured from repeats. That is the honest shape: the rule is fixed now, and the
scale arrives with the measurement.

**A gate names where its figure is read**, and
``tests/test_evals_promotion.py`` holds every name against a real artifact, so
a gate cannot quietly read a key nothing writes.

**An absent figure is never a zero.** An artifact written before a field
existed, or a job that ran no assertion pass, leaves the gate over that figure
*unread* rather than failing it. A gate that read an absence as a measurement
would refuse a promotion for a reason nobody measured.

Nothing here decides the promotion. It reports which gates passed, which
failed, and which nobody can read yet — and a run with an unread gate is not a
promotion, which is a reading a person makes.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from analysis_service.assertions import AssertionRecord

#: The baseline arm the budgets below were set from: one case, STRIDE only,
#: ``analysis`` mode so the blessed model is fixed, `ANALYSIS_ASSERTIONS`
#: unset. Recorded as values rather than as a path, because the artifacts are
#: under the gitignored ``evals/runs/`` and a budget nobody can re-read is a
#: number with no provenance.
BASELINE: Mapping[str, float] = MappingProxyType(
    {
        "charge_usd": 0.2791,
        "node_seconds": 165.4,
        "must_find_coverage": 0.500,
        "reference_coverage": 0.526,
        "claims": 16,
    }
)

#: When the baseline was measured, and by which pair. A budget is a fact about
#: a day's tree: read this before quoting one.
#:
#: **These limits were written after that pair's treatment arm was read**, and
#: several ``why`` fields quote it. They are fixed from here on for the runs
#: that follow, and they are not evidence that the first pair met a limit
#: declared before it: a pair that set a limit cannot also be tested by it.
#: The pair's artifacts sit under the gitignored ``evals/runs/`` and are not in
#: the tree, so its figures are the record rather than something to recompute.
#:
#: **What these gates cannot yet decide.** No gate reads required-fact recall,
#: which is the extraction's own factual endpoint and is computed by
#: ``run.py replay`` from the emission archive rather than written to a run
#: artifact; and the ``sd`` gates compare one treatment run against this one
#: baseline run, scaled by a spread, rather than repeated arms against each
#: other. Both are #926's to settle before a promotion is read off this table.
BASELINE_RUN = (
    "2026-09-23, case 01-payments-checkout, evals/runs/20260923T-assertions-ab/off.json"
)

Unit = Literal["ratio", "count", "sd"]
Verdict = Literal["pass", "fail", "inconclusive"]


# Each reader returns ``None`` where the artifact does not carry the figure at
# all — an artifact written before the field existed, or a job that ran no
# assertion pass. **That is not a zero.** A gate over a figure nobody recorded
# is unread, and :func:`read_gate` reports it as such rather than letting an
# absence read as a measurement.


def _charge(artifact: Mapping[str, Any], report: Mapping[str, Any]) -> float | None:
    return sum(artifact["node_charges"].values())


def _node_seconds(
    artifact: Mapping[str, Any], report: Mapping[str, Any]
) -> float | None:
    return sum(row["total_ms"] for row in artifact["node_latency"].values()) / 1000


def _metric(artifact: Mapping[str, Any], name: str) -> float | None:
    scores = artifact.get("scores") or []
    if not scores:
        return None
    return scores[0].get("metrics", {}).get(name)


def _must_find(artifact: Mapping[str, Any], report: Mapping[str, Any]) -> float | None:
    return _metric(artifact, "must_find_coverage")


def _reference(artifact: Mapping[str, Any], report: Mapping[str, Any]) -> float | None:
    return _metric(artifact, "reference_coverage")


def _backed(artifact: Mapping[str, Any], report: Mapping[str, Any]) -> float | None:
    backed = (artifact.get("losses_aggregate") or {}).get("assertion_backed")
    return None if backed is None else backed["matched"]


def _record(report: Mapping[str, Any]) -> AssertionRecord | None:
    held = report.get("assertions")
    return None if not held else AssertionRecord.model_validate(held)


def _refused_share(
    artifact: Mapping[str, Any], report: Mapping[str, Any]
) -> float | None:
    """Rows the gate refused over rows proposed: a **structural** refusal rate.

    Distinct rows, through :meth:`~AssertionRecord.refused_rows`, never a count
    of issues: one row draws several reasons, and a graph contradiction refuses
    nothing. Before #926's audit this divided the issue count and called it the
    unsupported-assertion rate, which it is not — a row can quote the source
    exactly and state something the quote does not say, and the gate passes
    it. :func:`_unsupported_share` is that question.
    """
    record = _record(report)
    if record is None or not record.proposed:
        return None
    return record.refused_rows() / record.proposed


def _unsupported_share(
    artifact: Mapping[str, Any], report: Mapping[str, Any]
) -> float | None:
    """Rows a reviewer found unsupported, over the rows anybody assessed.

    **Semantic support, and nothing in this service measures it yet.** Every
    row's ``assessment`` is ``unchecked`` until a reviewer or an external check
    writes one (ADR 0034), so this reads ``None`` — unread, not zero — on every
    run today. A located quote is not support, and a gate that read span
    validity as support passed a flipped MFA answer, a webhook carrying a
    receipt store's credential and a mechanism spelled ``the of and a``.
    """
    record = _record(report)
    if record is None:
        return None
    assessed = [
        entry for entry in record.catalog.entries if entry.assessment != "unchecked"
    ]
    if not assessed:
        return None
    return sum(entry.assessment != "supported" for entry in assessed) / len(assessed)


@dataclass(frozen=True)
class Gate:
    """One predeclared limit, and where the figure that meets it is read.

    ``read`` takes the treatment run's artifact and the report beside it.
    ``against`` is the baseline figure the limit is a ratio of, or ``""`` for a
    gate read on the treatment alone.

    ``unit`` decides how ``limit`` is read and what :func:`decide` can do with
    it. A ``ratio`` gate compares the treatment to the baseline; a ``count``
    gate compares the treatment to the limit outright; an ``sd`` gate needs a
    spread measured from repeats and is ``inconclusive`` without one.
    """

    question: str
    read: Callable[[Mapping[str, Any], Mapping[str, Any]], float | None]
    unit: Unit
    limit: float
    #: Which way passes: a figure at most the limit, or at least it.
    direction: Literal["at-most", "at-least"]
    against: str = ""
    why: str = ""


#: Every gate the promotion is read against. Resource gates first, because they
#: are the ones one pair can answer.
GATES: Mapping[str, Gate] = MappingProxyType(
    {
        "charge": Gate(
            question="does a job cost more than the budget allows?",
            read=_charge,
            unit="ratio",
            limit=1.25,
            direction="at-most",
            against="charge_usd",
            why=(
                "the pair measured 1.085x, so a quarter above the baseline is"
                " headroom for a larger catalog rather than a ceiling nothing"
                " can hit"
            ),
        ),
        "latency": Gate(
            question=(
                "does the summed node time grow beyond the budget? Summed node"
                " time, not a job's wall time: the pair measured 2.08x summed"
                " and 3.31x wall, and no artifact records the wall time"
            ),
            read=_node_seconds,
            unit="ratio",
            limit=2.50,
            direction="at-most",
            against="node_seconds",
            why=(
                "the `assert` call sits on the critical path and the pair"
                " measured 2.08x on one observation, against a benchmark three"
                " times faster for the same model — so the budget admits the"
                " slow reading and refuses a worse one"
            ),
        ),
        "backs-claims": Gate(
            question="does the layer put a stated fact under a real claim?",
            read=_backed,
            unit="count",
            limit=1,
            direction="at-least",
            why=(
                "a layer that backs no claim answering a reference cannot help"
                " a report, whatever it costs. The pair measured 4"
            ),
        ),
        "structural-refusals": Gate(
            question="what share of proposed rows does the gate refuse?",
            read=_refused_share,
            unit="count",
            limit=0.10,
            direction="at-most",
            why=(
                "the pair measured 0 of 15. A tenth is the point at which the"
                " node is writing rows the gate cannot accept rather than"
                " occasionally overreaching. A structural rate: a row the gate"
                " accepts may still say something its quote does not"
            ),
        ),
        "unsupported-assertions": Gate(
            question="what share of assessed rows did a reviewer find unsupported?",
            read=_unsupported_share,
            unit="count",
            limit=0.10,
            direction="at-most",
            why=(
                "declared 2026-09-23, before any row has been assessed, at the"
                " structural gate's tenth. Unread until reviewed assessments"
                " exist, so no run promotes on span validity alone"
            ),
        ),
        "must-find-coverage": Gate(
            question="does the score fall further than the spread explains?",
            read=_must_find,
            unit="sd",
            limit=-1.0,
            direction="at-least",
            against="must_find_coverage",
            why=(
                "non-regression, in units of the repeat spread rather than of a"
                " number read off one pair. One standard deviation down is the"
                " limit; the spread arrives with the repeats"
            ),
        ),
        "reference-coverage": Gate(
            question="does reference coverage fall further than the spread explains?",
            read=_reference,
            unit="sd",
            limit=-1.0,
            direction="at-least",
            against="reference_coverage",
            why="the same rule on the wider denominator",
        ),
    }
)


@dataclass(frozen=True)
class Reading:
    """One gate, read against one pair of runs."""

    gate: str
    verdict: Verdict
    #: What the treatment run measured, or ``None`` where the artifact carries
    #: no such figure.
    measured: float | None
    #: The baseline figure the limit is relative to, or ``None`` for a gate
    #: read on the treatment alone.
    baseline: float | None
    #: The figure compared against the limit — a ratio, a count, or a
    #: difference in standard deviations. ``None`` where nothing can be read.
    against_limit: float | None
    why: str = ""


def _ratio(measured: float, baseline: float) -> float | None:
    return None if not baseline else measured / baseline


def _in_spreads(difference: float, spread: float) -> float:
    """A difference in standard deviations of the repeat spread.

    **A measured spread of zero is a measurement**, not a missing one: repeats
    that agreed exactly say any difference at all is outside the noise. Read as
    falsy, it left a gate ``inconclusive`` however many identical repeats ran.
    """
    if spread:
        return difference / spread
    return 0.0 if difference == 0 else math.copysign(math.inf, difference)


def read_gate(
    name: str,
    artifact: Mapping[str, Any],
    report: Mapping[str, Any],
    *,
    spread: float | None = None,
) -> Reading:
    """One gate against one treatment run, or why it cannot be read.

    ``spread`` is the standard deviation of the gate's own figure over repeats
    of the baseline arm. An ``sd`` gate without one is ``inconclusive``: the
    limit is a multiple of a number nobody has measured, and guessing it is the
    retrospective choice this table exists to prevent.
    """
    gate = GATES[name]
    measured = gate.read(artifact, report)
    baseline = BASELINE[gate.against] if gate.against else None
    if measured is None:
        return Reading(
            name,
            "inconclusive",
            None,
            baseline,
            None,
            "the artifact carries no such figure, which is not a zero",
        )
    if gate.unit == "count":
        value: float | None = measured
    elif gate.unit == "ratio":
        assert baseline is not None
        value = _ratio(measured, baseline)
    elif spread is not None:
        assert baseline is not None
        value = _in_spreads(measured - baseline, spread)
    else:
        return Reading(
            name,
            "inconclusive",
            measured,
            baseline,
            None,
            "no repeat spread has been measured, so this limit has no scale",
        )
    if value is None:
        return Reading(
            name, "inconclusive", measured, baseline, None, "the baseline is zero"
        )
    passed = value <= gate.limit if gate.direction == "at-most" else value >= gate.limit
    return Reading(name, "pass" if passed else "fail", measured, baseline, value)


def decide(
    artifact: Mapping[str, Any],
    report: Mapping[str, Any],
    *,
    spread: Mapping[str, float] = MappingProxyType({}),
) -> tuple[Reading, ...]:
    """Every gate, read. ``spread`` maps a gate name to its repeat spread."""
    return tuple(
        read_gate(name, artifact, report, spread=spread.get(name)) for name in GATES
    )


def render(readings: Sequence[Reading]) -> str:
    """The gate table, as text. It reports no verdict of its own."""
    lines = [
        "## The predeclared promotion gates, read",
        "",
        (
            f"Budgets set from the baseline arm of {BASELINE_RUN}. A gate"
            " reading `inconclusive` is one nobody can read yet, and a run with"
            " an unread gate is not a promotion."
        ),
        "",
        "| gate | verdict | measured | baseline | against limit | limit |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for row in readings:
        gate = GATES[row.gate]
        baseline = "—" if row.baseline is None else f"{row.baseline:.4g}"
        against = "—" if row.against_limit is None else f"{row.against_limit:.3f}"
        measured = "—" if row.measured is None else f"{row.measured:.4g}"
        lines.append(
            f"| `{row.gate}` | {row.verdict} | {measured} | {baseline}"
            f" | {against} | {gate.direction} {gate.limit} ({gate.unit}) |"
        )
    unread = [row for row in readings if row.verdict == "inconclusive"]
    failed = [row for row in readings if row.verdict == "fail"]
    lines += [
        "",
        (
            f"**{len(readings) - len(unread) - len(failed)} pass,"
            f" {len(failed)} fail, {len(unread)} unread.**"
        ),
        "",
    ]
    for row in unread:
        lines.append(f"- `{row.gate}` unread: {row.why}")
    for row in failed:
        lines.append(
            f"- `{row.gate}`: {GATES[row.gate].question} {GATES[row.gate].why}"
        )
    return "\n".join(lines) + "\n"


def arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "artifact", type=Path, help="the treatment run's artifact, flag on"
    )
    parser.add_argument(
        "--case",
        default="01-payments-checkout",
        help="which case's report beside the artifact to read",
    )
    parser.add_argument(
        "--spread",
        type=Path,
        help="JSON mapping a gate name to the standard deviation of its figure"
        " over repeats of the baseline arm",
    )


def command_gates(args: argparse.Namespace) -> int:
    """Read the gates against one treatment run. It runs no model."""
    try:
        artifact = json.loads(args.artifact.read_text(encoding="utf-8"))
        report_path = args.artifact.with_suffix(".reports") / f"{args.case}.report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        spread = (
            {} if args.spread is None else json.loads(args.spread.read_text("utf-8"))
        )
    except (OSError, ValueError) as error:
        print(f"cannot read: {error}", file=sys.stderr)
        return 1
    readings = decide(artifact, report, spread=spread)
    print(render(readings), end="")
    return 0 if all(row.verdict == "pass" for row in readings) else 1
