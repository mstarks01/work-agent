"""Whether a coverage gain rests on supported output, read before a promotion.

A run can raise coverage by saying more. Flood a report with findings at every
place the reference sets cite, and the scorer matches more of them, because an
unlisted finding is not counted wrong (``evals/README.md``). So a coverage gain
needs a second reading beside it: did the candidate say more than the baseline
did, and did a person accept what it added?

Three gates answer that. By default each reads the development cases only
(the held-out ones confirm a gain and never shape one):

* ``volume`` — the unmatched findings per case may not rise by more than one
  standard deviation of the repeat spread. This half works offline, and it is
  what a flooded run fails first.
* ``reviewed`` — every unmatched finding of the candidate is answered by a
  vote. A finding nobody judged cannot count for or against the gain, so the
  gain waits for the votes.
* ``rejected-rate`` — the share of findings a person rejected may not rise by
  more than one standard deviation of the spread.

The design is the owner's decision of 2026-10-11 on #1093: volume and votes,
both. Like the ``sd`` gates of :mod:`evals.harness.promotion`, a gate whose
spread nobody has measured reads ``inconclusive``, and a gain with an unread
gate is not a promotion.

``--held-out`` is rung 5 of the experiment protocol, held-out confirmation:
the same gates over the held-out cases alone. Each case it reads spends one of
that case's ``HOLDOUT_READS``, so the reading names each case for the row's
``holdout_reads``, and it refuses a case the ledger has exhausted, which is a
development case now (:func:`~evals.harness.audit.held_out`).

The readers are a table keyed by framework, because what counts as an
unmatched finding is the package's own question. A package with no vote
reading says so in the table, and its vote gates read ``inconclusive`` rather
than pass.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from analysis_service.claims import FrameworkName
from evals.harness import audit
from evals.harness.artifact import figures_corpus
from evals.harness.comparison import flagged_cases, held_out_cases
from evals.harness.instruments import Cases
from evals.harness.promotion import Reading, in_spreads, read_spread

#: One figure, read from one artifact over the cases a selector keeps, or
#: ``None`` where the artifact carries no such figure.
Reader = Callable[[Mapping[str, Any], Cases], float | None]


class RefusedError(ValueError):
    """A reading the command will not make, naming why."""


class IncomparableError(RefusedError):
    """Two artifacts whose figures do not compare, naming what differs."""


#: What two artifacts must share before a gain between them means anything,
#: each with how it is read. The corpus is read through
#: :func:`~evals.harness.artifact.figures_corpus`, because a re-scored
#: artifact's figures follow the corpus it was scored on. A version or a mode
#: that differs changes what the figures count. The repository commit is not
#: here: the candidate's own change is what the gain measures.
COMPARABLE: Mapping[str, Callable[[Mapping[str, Any]], object]] = MappingProxyType(
    {
        "corpus": figures_corpus,
        "artifact_version": lambda raw: raw.get("artifact_version"),
        "flow_identity_version": lambda raw: raw.get("flow_identity_version"),
        "mode": lambda raw: raw.get("mode"),
    }
)


def refuse_incomparable(
    baseline: Mapping[str, Any], candidate: Mapping[str, Any]
) -> None:
    """Refuse a pair whose figures do not compare, before any gate is read.

    A part that either artifact lacks is refused too: a pair nobody can show
    to be comparable is read as not comparable.
    """
    differ = [
        f"{name} {read(baseline)!r} against {read(candidate)!r}"
        for name, read in COMPARABLE.items()
        if read(baseline) is None or read(baseline) != read(candidate)
    ]
    if differ:
        raise IncomparableError(
            "the two artifacts do not compare: " + "; ".join(differ) + ". Read a"
            " gain only between sweeps scored on one corpus, under one artifact"
            " and identity version, in one mode; re-score both with `run.py"
            " score` on one corpus first"
        )


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _stride_volume(blocks: Mapping[str, Any], cases: Cases) -> float | None:
    """Unmatched findings a person could review, per case."""
    return _mean([row["counts"]["eligible"] for row in cases.rows(blocks, "scores")])


def _stride_unvoted(blocks: Mapping[str, Any], cases: Cases) -> float | None:
    """Unmatched findings no vote answers, summed over the cases."""
    rows = cases.rows(blocks, "scores")
    return float(sum(row["counts"]["unvoted"] for row in rows)) if rows else None


def _stride_rejected(blocks: Mapping[str, Any], cases: Cases) -> float | None:
    """The share of produced findings a person rejected, per case."""
    return _mean(
        [row["metrics"]["rejected_rate"] for row in cases.rows(blocks, "scores")]
    )


def _asvs_volume(blocks: Mapping[str, Any], cases: Cases) -> float | None:
    """Requirements the run applied that the case did not list, per case."""
    return _mean(
        [
            len(row["over_applied"]) + len(row["off_catalog"])
            for row in cases.rows(blocks, "applicability")
        ]
    )


@dataclass(frozen=True)
class Readers:
    """One package's readers for the three gates. ``None`` is a stated absence."""

    volume: Reader
    unvoted: Reader | None
    rejected: Reader | None
    #: Why a vote reader is absent, printed where its gate reads.
    absent: str = ""


#: Every package's readers. A package missing here raises ``KeyError`` at the
#: first gain read for it, which is the forcing edit
#: ``docs/agents/framework-parity.md`` asks of a table keyed by framework.
READERS: Mapping[FrameworkName, Readers] = MappingProxyType(
    {
        "stride": Readers(
            volume=_stride_volume,
            unvoted=_stride_unvoted,
            rejected=_stride_rejected,
        ),
        "asvs": Readers(
            volume=_asvs_volume,
            unvoted=None,
            rejected=None,
            absent=(
                "the applicability rows carry no vote standing: an over-applied"
                " requirement is settled by a person through pairing, not by a"
                " vote this artifact counts"
            ),
        ),
    }
)


@dataclass(frozen=True)
class CaseSplit:
    """Which cases one reading counts in each artifact, and what it says of them."""

    held_out: bool
    baseline: Cases
    candidate: Cases
    #: The lines the table prints under itself about the cases it read.
    notes: tuple[str, ...]

    @property
    def part(self) -> str:
        return "held-out cases" if self.held_out else "development cases"


def split(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    held_out: bool,
    ledger: Sequence[audit.Experiment] | None = None,
) -> CaseSplit:
    """The cases a reading counts, through the one reader of the split.

    A development reading counts an exhausted holdout and names it. A held-out
    reading refuses one, and raises :class:`RefusedError` where an artifact
    has no held-out case left.
    """
    ledger = audit.load() if ledger is None else ledger
    flagged = flagged_cases(baseline) | flagged_cases(candidate)
    held = {
        name: held_out_cases(raw, ledger)
        for name, raw in (("baseline", baseline), ("candidate", candidate))
    }
    if not held_out:
        line = audit.exhausted_line(flagged, ledger)
        return CaseSplit(
            held_out=False,
            baseline=Cases(held["baseline"], holdout=False),
            candidate=Cases(held["candidate"], holdout=False),
            notes=(line,) if line else (),
        )
    empty = [name for name, cases in held.items() if not cases]
    if empty:
        verb = "hold" if len(empty) > 1 else "holds"
        raise RefusedError(
            f"the {' and the '.join(empty)} {verb} no case that is still held"
            " out, so no held-out case is left to confirm a gain on"
        )
    spent = audit.exhausted(ledger)
    still = audit.still_held_out(flagged, ledger)
    refused = {case: spent[case] for case in sorted(flagged - still)}
    reads = audit.exposure(ledger)
    read = sorted(held["baseline"] | held["candidate"])
    notes = [
        f"- refused: {case} is exhausted (read {count} of"
        f" {audit.HOLDOUT_READS} times), so it is a development case and"
        " confirms nothing"
        for case, count in refused.items()
    ]
    notes.append(
        "Each case read here spends one of its reads. Record every one in the"
        " experiment row's `holdout_reads`: "
        + ", ".join(
            f"{case} (read {len(reads.get(case, ()))} of {audit.HOLDOUT_READS}"
            " times before this reading)"
            for case in read
        )
    )
    return CaseSplit(
        held_out=True,
        baseline=Cases(held["baseline"], holdout=True),
        candidate=Cases(held["candidate"], holdout=True),
        notes=tuple(notes),
    )


@dataclass(frozen=True)
class GainGate:
    """One gate: its question, its limit, and how the limit is read."""

    question: str
    unit: Literal["sd", "count"]
    #: The highest passing figure: a difference in spreads, or a count.
    limit: float


GATES: Mapping[str, GainGate] = MappingProxyType(
    {
        "volume": GainGate(
            question="did the candidate say more than the spread explains?",
            unit="sd",
            limit=1.0,
        ),
        "reviewed": GainGate(
            question="is every unmatched finding of the candidate answered?",
            unit="count",
            limit=0.0,
        ),
        "rejected-rate": GainGate(
            question="did a person reject more than the spread explains?",
            unit="sd",
            limit=1.0,
        ),
    }
)


def _reader(framework: FrameworkName, gate: str) -> Reader | None:
    readers = READERS[framework]
    return {
        "volume": readers.volume,
        "reviewed": readers.unvoted,
        "rejected-rate": readers.rejected,
    }[gate]


def read(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    framework: FrameworkName,
    *,
    spread: Mapping[str, float] = MappingProxyType({}),
    held_out: bool = False,
) -> tuple[tuple[Reading, ...], CaseSplit]:
    """Every gate, read on each artifact's development or held-out cases, and the split.

    Raises :class:`IncomparableError` first where the two do not compare, and
    :class:`RefusedError` where a held-out reading has no case to read.
    """
    refuse_incomparable(baseline, candidate)
    cases = split(baseline, candidate, held_out=held_out)
    readings = []
    for name, gate in GATES.items():
        reader = _reader(framework, name)
        if reader is None:
            readings.append(
                Reading(
                    name, "inconclusive", None, None, None, READERS[framework].absent
                )
            )
            continue
        before = reader(baseline, cases.baseline)
        after = reader(candidate, cases.candidate)
        readings.append(_decide(name, gate, before, after, spread.get(name)))
    return tuple(readings), cases


def _decide(
    name: str,
    gate: GainGate,
    before: float | None,
    after: float | None,
    spread: float | None,
) -> Reading:
    if after is None or (gate.unit == "sd" and before is None):
        return Reading(
            name,
            "inconclusive",
            after,
            before,
            None,
            "an artifact carries no such figure, which is not a zero",
        )
    if gate.unit == "count":
        value = after
    elif spread is None:
        return Reading(
            name,
            "inconclusive",
            after,
            before,
            None,
            "no repeat spread has been measured, so this limit has no scale",
        )
    else:
        assert before is not None
        value = in_spreads(after - before, spread)
    verdict: Literal["pass", "fail"] = "pass" if value <= gate.limit else "fail"
    return Reading(name, verdict, after, before, value)


def render(
    readings: Sequence[Reading], framework: FrameworkName, cases: CaseSplit
) -> str:
    """The gate table, as text. It reports no promotion of its own."""
    lines = [
        f"## Does the gain rest on supported output? ({framework}, {cases.part})",
        "",
        "| gate | verdict | candidate | baseline | against limit | limit |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for row in readings:
        gate = GATES[row.gate]
        cells = [
            "—" if value is None else f"{value:.4g}"
            for value in (row.measured, row.baseline, row.against_limit)
        ]
        lines.append(
            f"| `{row.gate}` | {row.verdict} | {' | '.join(cells)}"
            f" | at-most {gate.limit:g} ({gate.unit}) |"
        )
    lines.append("")
    for row in readings:
        if row.verdict == "fail":
            lines.append(f"- `{row.gate}` fails: {GATES[row.gate].question}")
        elif row.verdict == "inconclusive":
            lines.append(f"- `{row.gate}` unread: {row.why}")
    if all(row.verdict == "pass" for row in readings):
        lines.append("Every gate passes: the gain does not rest on unsupported output.")
    else:
        lines.append(
            "A gain with a failed or unread gate is not a promotion. Report"
            " the gain as unconfirmed."
        )
    lines.extend(cases.notes)
    return "\n".join(lines) + "\n"


def arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("baseline", type=Path, help="the baseline sweep's artifact")
    parser.add_argument("candidate", type=Path, help="the candidate sweep's artifact")
    parser.add_argument(
        "--framework",
        required=True,
        choices=sorted(READERS),
        help="which package's findings to read",
    )
    parser.add_argument(
        "--spread",
        type=Path,
        help="JSON mapping a gate name to the standard deviation of its figure"
        " over repeats of the baseline",
    )
    parser.add_argument(
        "--held-out",
        action="store_true",
        help="rung 5, held-out confirmation: read the gates on the held-out"
        " cases alone. Each case read spends one of its HOLDOUT_READS; record"
        " it in the experiment row's holdout_reads",
    )


def command_gain(args: argparse.Namespace) -> int:
    """Read the gain gates against a baseline and a candidate. It runs no model."""
    try:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
        spread = {} if args.spread is None else read_spread(args.spread, GATES)
    except (OSError, ValueError) as error:
        print(f"cannot read: {error}", file=sys.stderr)
        return 1
    try:
        readings, cases = read(
            baseline, candidate, args.framework, spread=spread, held_out=args.held_out
        )
    except RefusedError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    print(render(readings, args.framework, cases), end="")
    return 0
