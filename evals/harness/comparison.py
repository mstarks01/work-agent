"""The published comparison over the merged Baselines: one generated file.

``evals/baselines/README.md`` sits beside the data it summarizes. Code builds
it, a person commits it, and a test recomputes it and fails on a mismatch. That
is the licence-lint shape, so a stale table fails closed rather than drifting.
CI never pushes a commit, and ``submit baseline`` rebuilds the file as part of
staging, so a contributor never learns the extra step exists (#330).

The generator names no framework and no column. A row prints one block per
framework in the Baseline's selection, and the columns of each block come from
:data:`~evals.harness.instruments.INSTRUMENTS`, through each entry's
``published`` tuple, read through the module that owns the block's shape. A
future package brings its own instrument entry, and the table prints it with no
edit here.

There is recognition without rank. A row names the logins that submitted its
sweeps, because the manifest records them and hiding them would be a pretence.
The sort key is the merge date and never a score. A leaderboard over a corpus
this small rewards overfitting to it, so the credit stays and the race does not.

Rows group by ``(commit, corpus digest)``, and the file says that numbers across
groups do not compare. A reference-set change moves every baseline, so the
grouping makes the invalid comparison hard to make by accident.

A spread prints beside every mean. ``TUNING.md`` records three findings
retracted because they rested on single-run numbers, and a published table must
not repeat that in public. Where a Baseline holds more than one sweep, the range
prints beside the mean.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from analysis_service.report import FrameworkName
from evals.harness import standings
from evals.harness.artifact import REPO_ROOT
from evals.harness.baseline import BaselineError, artifact_filename
from evals.harness.instruments import INSTRUMENTS, Column
from evals.harness.scorer import vote_coverage

#: Where the generated table lives, beside the Baselines it reads. Relative,
#: because every reader here takes a ``root`` — the repo's own tree in CI, a
#: temporary one under test — and an absolute constant would answer for only
#: the first of them.
TABLE_REL = Path("evals") / "baselines" / "README.md"

#: What a cell says when the number would be zero only because nobody has
#: voted. A zero reads as a measured failure; an absent vote is not a
#: measurement, and this is the distinction certification draws with
#: "unexercised".
NO_VOTES = "no votes yet"


@dataclass(frozen=True)
class Row:
    """One merged Baseline, as the table prints it."""

    name: str
    identity: dict[str, Any]
    submitters: tuple[str, ...]
    sweeps: int
    cost_usd: float
    merged: str
    #: ``{series: {framework: {column label: cell}}}``, already rendered.
    cells: dict[str, dict[str, dict[str, str]]]
    #: (findings a person answered, findings offered) across every sweep, so
    #: a reader can weigh how much of the row rests on judgement.
    coverage: tuple[int, int]

    @property
    def voted(self) -> bool:
        return self.coverage[0] > 0

    @property
    def group(self) -> tuple[str, str]:
        return (
            str(self.identity.get("repo_commit", "")),
            str(self.identity.get("corpus_digest", "")),
        )


def _merged_at(directory: Path, root: Path) -> str:
    """When this Baseline last landed, as the neutral sort key."""
    try:
        stamp = subprocess.run(
            ["git", "log", "-1", "--format=%cI", "--", str(directory)],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""
    return stamp[:10]


def _series_blocks(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """One artifact's scored blocks per series.

    The primary series' numbers are the artifact's top-level keys and the
    others live under ``series.blocks``, so this is where that asymmetry is
    unpacked once rather than at every reader.
    """
    series = raw.get("series") or {}
    primary = series.get("primary", standings.PRIMARY)
    blocks = {primary: raw}
    for name, stored in (series.get("blocks") or {}).items():
        blocks[name] = stored
    return blocks


def _format(values: list[float]) -> str:
    """A mean, with the range beside it when more than one sweep measured it."""
    if not values:
        return "—"
    mean = sum(values) / len(values)
    if len(values) == 1:
        return f"{mean:.3f}"
    return f"{mean:.3f} ({min(values):.3f}–{max(values):.3f})"


def _columns_for(framework: FrameworkName) -> list[Column]:
    return [
        column
        for instrument in INSTRUMENTS.values()
        if instrument.applies_to((framework,))
        for column in instrument.published
    ]


def read_baseline(directory: Path, root: Path = REPO_ROOT) -> Row | None:
    """One Baseline directory as a row, or None when it carries no manifest."""
    manifest_path = directory / "baseline.json"
    if not manifest_path.is_file():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    identity = manifest.get("identity", {})
    entries = manifest.get("sweeps", [])

    per_series: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        try:
            path = directory / artifact_filename(entry.get("artifact", ""))
        except BaselineError:
            # A published table is not the place to explain a malformed
            # manifest, and `verify-contribution` already refuses one.
            continue
        if not path.is_file():
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        for series, blocks in _series_blocks(raw).items():
            per_series.setdefault(series, []).append(blocks)

    counted = [
        vote_coverage(blocks)
        for series, sweeps in per_series.items()
        if series == standings.PRIMARY
        for blocks in sweeps
    ]
    coverage = (sum(one for one, _ in counted), sum(all_ for _, all_ in counted))
    voted = coverage[0] > 0

    cells: dict[str, dict[str, dict[str, str]]] = {}
    for series, sweeps in per_series.items():
        for framework in identity.get("frameworks", []):
            for column in _columns_for(framework):
                values = [
                    value
                    for blocks in sweeps
                    if (value := column.read(blocks, framework)) is not None
                ]
                cell = (
                    NO_VOTES
                    if column.needs_votes and not voted
                    else _format([float(value) for value in values])
                )
                cells.setdefault(series, {}).setdefault(framework, {})[column.label] = (
                    cell
                )

    return Row(
        name=directory.name,
        identity=identity,
        submitters=tuple(
            sorted({str(entry.get("submitted_by", "")) for entry in entries})
        ),
        sweeps=len(entries),
        cost_usd=sum(
            float(entry.get("cost", {}).get("actual_usd", 0.0)) for entry in entries
        ),
        merged=_merged_at(directory, root),
        cells=cells,
        coverage=coverage,
    )


HEADER = """<!-- Generated by `python -m evals.harness.run comparison`. Do not edit by hand:
     a test recomputes this file and fails when it is stale. -->

# Merged baselines

Every measured configuration this repository carries, with what it cost and
what it scored. A **Baseline** is one configuration's sweeps kept together
with their reports; [`../BASELINES.md`](../BASELINES.md) is how to contribute
one.

**Numbers only compare inside a group.** Rows are grouped by the repository
commit and the corpus digest they ran against, because a change to a prompt or
to a reference set moves every baseline under it. Comparing a number in one
group against a number in another measures the corpus change, not the model.

**Every number is rule-and-ledger-relative.** Recall is measured against
reference sets an agent wrote, and the vote-dependent columns rest on however
many findings a person has judged — the coverage column says how many. Read
[`../README.md`](../README.md) before quoting any of it.

**Rows are sorted by merge date, never by score.** A leaderboard over a corpus
this small rewards overfitting to it. The submitter is named because the
manifest records it.
"""

EMPTY = """
No Baseline is merged yet, so there is nothing to compare. The first
contributed sweep starts this table — see [`../BASELINES.md`](../BASELINES.md).
"""


def build(root: Path = REPO_ROOT) -> str:
    """The whole file, from the merged Baselines and nothing else."""
    directory = root / "evals" / "baselines"
    rows = []
    if directory.is_dir():
        for entry in sorted(path for path in directory.iterdir() if path.is_dir()):
            row = read_baseline(entry, root)
            if row is not None:
                rows.append(row)

    if not rows:
        return HEADER + EMPTY

    parts = [HEADER]
    rows.sort(key=lambda row: (row.merged, row.name))
    grouped: dict[tuple[str, str], list[Row]] = {}
    for row in rows:
        grouped.setdefault(row.group, []).append(row)

    for (commit, digest), group in sorted(
        grouped.items(), key=lambda item: min(row.merged for row in item[1])
    ):
        parts.append(
            f"\n## Commit `{commit[:12]}`, corpus `{digest[:12]}`\n"
            f"\n{len(group)} baseline(s). Numbers in this section compare with"
            " each other and with nothing above or below it.\n"
        )
        for row in group:
            parts.append(_render_row(row))
    return "".join(parts)


def _render_row(row: Row) -> str:
    models = ", ".join(
        f"`{tier}`: `{model}`"
        for tier, model in sorted(row.identity.get("models", {}).items())
    )
    frameworks = ", ".join(row.identity.get("frameworks", [])) or "none"
    summary = (
        f"\n{models} · frameworks {frameworks} · {row.sweeps} sweep(s)"
        f" · ${row.cost_usd:.2f} recorded"
        f" · submitted by {', '.join(row.submitters) or 'nobody'}"
        f" · merged {row.merged or 'unknown'}\n"
        f"\nVote coverage: {row.coverage[0]} of {row.coverage[1]} unmatched"
        f" finding(s) judged by a person.\n"
    )
    lines = [f"\n### `{row.name}`\n", summary]
    if not row.voted:
        lines.append(
            "\nNobody has voted on this Baseline's findings, so every"
            f" vote-dependent number below reads `{NO_VOTES}`.\n"
        )
    for series in standings.SERIES:
        blocks = row.cells.get(series)
        if not blocks:
            continue
        included = ", ".join(standings.SERIES[series])
        lines.append(f"\n**Series `{series}`** — reads {included} votes.\n")
        for framework, columns in sorted(blocks.items()):
            lines += [
                f"\n`{framework}`\n",
                f"\n| {' | '.join(columns)} |\n",
                f"|{' --- |' * len(columns)}\n",
                f"| {' | '.join(columns.values())} |\n",
            ]
    return "".join(lines)


def is_stale(root: Path = REPO_ROOT) -> bool:
    """Whether the committed file disagrees with what the data says today."""
    path = root / TABLE_REL
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    return current != build(root)


def write(root: Path = REPO_ROOT) -> Path:
    """Rebuild the file. What ``submit baseline`` and the command both call."""
    path = root / TABLE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build(root), encoding="utf-8")
    return path


def command_comparison(args: argparse.Namespace) -> int:
    """Rebuild the published comparison over the merged Baselines.

    A person runs this and commits the result; a test recomputes it and fails
    on a stale copy, so CI never needs to push (#330). ``submit baseline``
    calls it during staging, so a contributor never learns it exists.
    """
    path = write(REPO_ROOT)
    print(f"{path} rebuilt from {REPO_ROOT / TABLE_REL.parent}")
    return 0
