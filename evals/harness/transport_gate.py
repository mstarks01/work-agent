"""ADR 0035's gate, as one script that asserts every criterion.

The gate decides whether the compact extraction transport promotes. It was
written before the run that first answered it, and it has been applied four
times. This module exists because of how the first application went: two
criteria were checked by hand, ``first-pass validity`` was never asserted at
all, and the arm passed a gate it had failed. A gate applied by hand is a gate
whose unchecked criterion is invisible.

**Every criterion here is read off an artifact the sweep already wrote.** The
primary figure is ``node_usage.extract``; the quality figures are
``mode_output[]``; the failure rates are the per-case extraction
reports beside the artifact. Nothing is recomputed and no provider is called,
so the gate runs offline and a person who did not run the sweep can apply it.

**It refuses before it reports.** Two arms scored against two corpus digests,
or one arm spanning two prompt digests, make every delta below meaningless —
so those are errors rather than findings. The three live applications before
this module existed each had to argue that neither had happened.

Read ``docs/adr/0035-the-compact-transport-promotes-on-a-predeclared-gate.md``
for why each threshold is the number it is. This module holds the arithmetic
and never the argument.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from evals.harness.bundle import reports_dir

#: Each quality figure against the margin ADR 0035 allows it, in the order the
#: record tabulates them. A table rather than six comparisons, so a figure added
#: to the gate is an entry here and the per-case veto reads the same six.
QUALITY_MARGINS: Mapping[str, float] = MappingProxyType(
    {
        "recall": 0.03,
        "endpoint_recall": 0.03,
        "interaction_recall": 0.03,
        "precision": 0.05,
        "zone_partition_agreement": 0.03,
        "scored_field_agreement": 0.05,
    }
)

#: The standard deviations of the full arm's own spread the compact arm's mean
#: must sit below on the primary figure. A cost claim that cannot clear a whole
#: one is not worth a second route in the tree.
PRIMARY_SIGMAS = 1.0

#: The issue codes that are faults of the transport rather than of the model,
#: and so carry a ceiling of zero: the full route cannot produce either.
TRANSPORT_FAULTS: tuple[str, ...] = ("duplicate-ref", "schema")


@dataclass(frozen=True)
class Arm:
    """One arm's five sweeps: the artifacts, and the reports beside each."""

    name: str
    artifacts: tuple[Mapping[str, Any], ...]
    reports: tuple[tuple[Mapping[str, Any], ...], ...]

    @property
    def emitted(self) -> list[float]:
        """Emitted tokens per sweep: completion less reasoning, corpus-summed.

        The primary figure. Reasoning tokens come out because they are not the
        transport's to save — the compact schema changes what the model writes
        down, not what it thinks first.
        """
        return [
            float(
                run["node_usage"]["extract"]["completion_tokens"]
                - run["node_usage"]["extract"]["reasoning_tokens"]
            )
            for run in self.artifacts
        ]

    def figure(self, name: str) -> list[float]:
        """One quality figure's corpus mean, per sweep."""
        return [
            statistics.fmean([row[name] for row in run["mode_output"]])
            for run in self.artifacts
        ]

    def per_case(self, name: str) -> dict[str, list[float]]:
        """One quality figure per case, across the sweeps."""
        cases: dict[str, list[float]] = {}
        for run in self.artifacts:
            for row in run["mode_output"]:
                cases.setdefault(row["case"], []).append(row[name])
        return cases

    @property
    def extractions(self) -> Iterator[Mapping[str, Any]]:
        """Every first-pass extraction this arm produced, over every sweep."""
        for sweep in self.reports:
            yield from sweep

    def issue_count(self, code: str) -> int:
        """How many extractions raised this issue code, over every sweep."""
        return sum(
            any(issue["code"] == code for issue in record["issues"])
            for record in self.extractions
        )

    @property
    def validity(self) -> list[float]:
        """First-pass validity per sweep: the share needing no repair.

        An extraction is valid on the first pass when the gate raised nothing.
        Read per sweep rather than pooled, because the gate compares the compact
        arm's mean against the *spread* of the full arm's.
        """
        return [
            statistics.fmean([not record["issues"] for record in sweep])
            for sweep in self.reports
        ]


@dataclass(frozen=True)
class Criterion:
    """One line of the gate: what it is called, and whether this pair clears it."""

    name: str
    verdict: Callable[[Arm, Arm], tuple[bool, str]]


def _primary(full: Arm, compact: Arm) -> tuple[bool, str]:
    """Emitted tokens: at least one full-arm standard deviation below its mean."""
    base, spread = statistics.fmean(full.emitted), statistics.stdev(full.emitted)
    mean = statistics.fmean(compact.emitted)
    sigmas = (base - mean) / spread if spread else float("inf")
    saved = 100 * (base - mean) / base
    return sigmas >= PRIMARY_SIGMAS, f"{saved:+.1f}%, {-sigmas:.2f} sd"


def _margin(figure: str, margin: float) -> Callable[[Arm, Arm], tuple[bool, str]]:
    """One non-inferiority margin, one-sided: better by any amount buys nothing."""

    def verdict(full: Arm, compact: Arm) -> tuple[bool, str]:
        delta = statistics.fmean(compact.figure(figure)) - statistics.fmean(
            full.figure(figure)
        )
        return delta >= -margin, f"{delta:+.3f} against {margin}"

    return verdict


def _veto(full: Arm, compact: Arm) -> tuple[bool, str]:
    """No case may fall below its own baseline floor on any quality figure.

    The floor is the full arm's **lowest** reading of that case over its five
    sweeps, which is what ``evals/TUNING.md`` step 5 means by a case's own
    spread: a compact mean under it is outside anything the baseline did.
    A corpus mean can hold while one case collapses, and that case is a system
    somebody would have run.
    """
    fallen: list[str] = []
    for figure in QUALITY_MARGINS:
        floors = {case: min(runs) for case, runs in full.per_case(figure).items()}
        for case, runs in compact.per_case(figure).items():
            mean = statistics.fmean(runs)
            if mean < floors[case]:
                fallen.append(f"{case}/{figure} {mean:.3f}<{floors[case]:.3f}")
    return not fallen, "; ".join(fallen) if fallen else "no case below its floor"


def _fault(code: str) -> Callable[[Arm, Arm], tuple[bool, str]]:
    """A transport fault: the ceiling is zero because the full route cannot have it."""

    def verdict(full: Arm, compact: Arm) -> tuple[bool, str]:
        count = compact.issue_count(code)
        total = sum(1 for _ in compact.extractions)
        return count == 0, f"{count} of {total}"

    return verdict


def _validity(full: Arm, compact: Arm) -> tuple[bool, str]:
    """First-pass validity, within one standard deviation of the full arm's."""
    base, spread = statistics.fmean(full.validity), statistics.stdev(full.validity)
    mean = statistics.fmean(compact.validity)
    floor = base - spread
    return mean >= floor, f"{mean:.3f} against a {floor:.3f} floor"


#: The gate, in the order ADR 0035 states it. A table rather than a sequence of
#: asserts, so every criterion is applied on every run and a criterion nobody
#: checked cannot be the reason an arm looked like it passed.
CRITERIA: tuple[Criterion, ...] = (
    Criterion("primary — emitted tokens", _primary),
    *(
        Criterion(f"{figure} ({margin})", _margin(figure, margin))
        for figure, margin in QUALITY_MARGINS.items()
    ),
    Criterion("first-pass validity", _validity),
    *(Criterion(f"{code}, ceiling 0", _fault(code)) for code in TRANSPORT_FAULTS),
    Criterion("per-case veto", _veto),
)


def load(name: str, paths: Sequence[Path]) -> Arm:
    """One arm from its five artifacts, with the reports beside each.

    Where the reports sit is :func:`evals.harness.bundle.reports_dir`'s rule
    and is called rather than re-spelled: the writer and this reader disagreeing
    about it would make the gate silently read no reports at all, and an arm
    with no reports passes both failure-rate criteria.
    """
    artifacts = tuple(
        json.loads(path.read_text(encoding="utf-8")) for path in sorted(paths)
    )
    reports = tuple(
        tuple(
            json.loads(report.read_text(encoding="utf-8"))
            for report in sorted(reports_dir(path).glob("*.json"))
        )
        for path in sorted(paths)
    )
    return Arm(name=name, artifacts=artifacts, reports=reports)


def refusals(full: Arm, compact: Arm) -> list[str]:
    """What makes the comparison meaningless rather than unfavourable.

    A digest that moved inside an arm means that arm ran two different
    instructions, and its five-sweep spread is then a spread over two
    configurations. A corpus digest that differs between arms means the two were
    scored against different references. Neither is a finding.
    """
    faults: list[str] = []
    for arm in (full, compact):
        if len(arm.artifacts) != 5:
            faults.append(f"{arm.name}: {len(arm.artifacts)} sweeps, the gate says 5")
        # An arm whose reports are missing reads as zero faults and full
        # validity, which is the shape of a pass. So a sweep that wrote no
        # report beside its artifact is refused rather than counted: the two
        # failure-rate criteria and first-pass validity are read from here and
        # from nowhere else.
        for artifact, sweep in zip(arm.artifacts, arm.reports, strict=True):
            if len(sweep) != len(artifact["mode_output"]):
                faults.append(
                    f"{arm.name}: a sweep scored {len(artifact['mode_output'])} cases"
                    f" and left {len(sweep)} extraction reports beside it"
                )
        digests = {
            entry["sha256"]
            for run in arm.artifacts
            for entry in run["instruction"]
            if entry["node"] == "extract"
        }
        if len(digests) > 1:
            faults.append(f"{arm.name}: {len(digests)} extract prompt digests")
    corpora = {run["corpus_digest"] for arm in (full, compact) for run in arm.artifacts}
    if len(corpora) > 1:
        faults.append(f"{len(corpora)} corpus digests across the arms")
    return faults


def apply(full: Arm, compact: Arm) -> bool:
    """Print every criterion and its reading. Returns whether the arm promotes."""
    print(f"{'criterion':34}{'verdict':8}reading")
    failed = 0
    for criterion in CRITERIA:
        passed, reading = criterion.verdict(full, compact)
        failed += not passed
        print(f"{criterion.name:34}{'PASS' if passed else 'FAIL':8}{reading}")
    print(f"\n{failed} of {len(CRITERIA)} criteria failed")
    return failed == 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--full", nargs="+", type=Path, required=True)
    parser.add_argument("--compact", nargs="+", type=Path, required=True)
    args = parser.parse_args(argv)
    full = load("full", args.full)
    compact = load("compact", args.compact)
    if faults := refusals(full, compact):
        for fault in faults:
            print(f"refused: {fault}")
        return 2
    return 0 if apply(full, compact) else 1


if __name__ == "__main__":
    raise SystemExit(main())
