"""The quality audit's two rules in code: which phase owns a node, and what a prior audit found.

A quality audit asks two questions a Markdown instruction cannot answer on its
own. *Where did this loss happen?* and *has somebody already tested this?* Both
are rules, and a rule this repository states in prose alone grows a second
reader the first time anything else needs it (``docs/agents/code-review.md``).
So both live here, and the skill under ``.claude/skills/quality-audit/`` calls
them.

## The phase table

:data:`PHASES` is the six-phase partition the audit reports against, keyed by
phase and naming the graph nodes each one covers. It is a table rather than a
branch for the reason ``docs/agents/framework-parity.md`` gives: a node added
tomorrow has to land in exactly one phase, and
``tests/test_evals_audit.py`` compares the table against the three registries
that hold the real names — the ``*_NODE`` constants of
:mod:`analysis_service.graph`, its :data:`~analysis_service.graph.ROLES`, and
the lane node of every lane in :data:`~analysis_service.frameworks.PACKAGES`.
A node covered by no phase fails there rather than being attributed by whoever
is reading.

**A phase is coarser than a node on purpose.** The audit's unit is the stage a
fix would land in, and ``facts``, ``inventory`` and ``rows`` are one stage run
three ways (#1003). Attribution below node granularity is the artifact's job.

## The experiment ledger

An audit that cannot read its own history repeats a refuted experiment and
charges the repeat to the user. :class:`Experiment` is one such record and
:func:`load` reads them all, from ``evals/experiments/``, one JSONL file per
audit so two audits never conflict in the same file.

It borrows three decisions from the vote ledger (:mod:`evals.harness.ledger`)
because they answer the same problems:

* **Append-only.** A correction is a new row naming the row it supersedes, so a
  conclusion reconstructs at any past date.
* **A closed vocabulary.** :data:`OUTCOMES` is the whole set an experiment may
  end in, and a row outside it raises at load. ``null`` and ``refuted`` are
  first-class: the issue this serves asks that a negative result survive, and a
  vocabulary with no word for one is how it gets summarised away.
* **Components beside the key.** A row records the repository paths its
  conclusion **reads**, so :func:`staleness` decides from the tree whether the
  conclusion still stands, rather than from what an earlier audit believed.

Staleness is computed and never asserted. ``stale`` means one of the paths the
record names changed between its revision and the tree now; it is a reason to
re-read the record, not a verdict that the record is wrong.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from analysis_service.claims import FrameworkName
from analysis_service.frameworks import PACKAGES
from evals.harness.provenance import REPO_ROOT

#: Where the ledger lives: one JSONL file per audit, named by its audit ID.
#: Per audit rather than one shared file for the reason the vote ledger is per
#: voter -- two audits running from two checkouts then merge without conflict.
EXPERIMENTS_DIR = REPO_ROOT / "evals" / "experiments"

Phase = Literal[
    "extraction",
    "preparation",
    "analysis",
    "fan-in",
    "criticism",
    "reporting",
]

Outcome = Literal[
    "supported",
    "refuted",
    "null",
    "inconclusive",
    "blocked",
    "superseded",
]

#: Every outcome an experiment may end in. A closed set, so a row outside it
#: raises at load instead of inventing a seventh word nothing folds over.
OUTCOMES: tuple[Outcome, ...] = (
    "supported",
    "refuted",
    "null",
    "inconclusive",
    "blocked",
    "superseded",
)


@dataclass(frozen=True)
class PhaseEntry:
    """One phase: what it is asked about, and which graph nodes answer for it."""

    #: The diagnostic question the audit asks of this phase.
    question: str
    #: Every graph node name or per-framework role this phase covers.
    nodes: frozenset[str]
    #: The offline instruments that say something about this phase, as the
    #: ``evals.harness.run`` subcommands that print them. Named so a proposal
    #: reaches for the free reading before it reaches for a paid one.
    instruments: tuple[str, ...]


#: The six phases, each naming the graph nodes it owns.
#:
#: ``extraction`` carries the whole head — the reading calls, the deterministic
#: resolver, the validity gate and the bounded repair — because a fact lost
#: anywhere in it is lost before a lane can read it, and the head's own routes
#: are what #1003 compares. ``preparation`` is the routing half: what reaches
#: which lane. ``analysis`` is one lane agent writing a draft. ``fan-in`` is the
#: join and the merge. ``criticism`` is the critic, its bounded re-ask and the
#: rereview. ``reporting`` is ``assemble``, the terminal node that builds the
#: served report out of whatever every earlier phase left.
PHASES: Mapping[Phase, PhaseEntry] = MappingProxyType(
    {
        "extraction": PhaseEntry(
            question=(
                "Were the necessary facts retained, with the correct subject,"
                " scope, polarity and uncertainty?"
            ),
            nodes=frozenset(
                {
                    "extract",
                    "facts",
                    "inventory",
                    "rows",
                    "reading_inventory",
                    "resolve",
                    "assert",
                    "reread",
                    "reading",
                    "apply",
                    "catalog",
                    "read",
                    "validate",
                    "repair",
                    "revalidate",
                    "reject",
                }
            ),
            instruments=("replay", "bind", "oracle", "bottleneck"),
        ),
        "preparation": PhaseEntry(
            question=(
                "Did the relevant evidence and the assessment opportunities"
                " reach the correct lane?"
            ),
            nodes=frozenset({"prepare", "router"}),
            instruments=("score", "extraction-losses"),
        ),
        "analysis": PhaseEntry(
            question="Was a substantively correct candidate generated at all?",
            nodes=frozenset({"analyze"}),
            instruments=("score", "price-verbs"),
        ),
        "fan-in": PhaseEntry(
            question=(
                "Did deduplication and merging preserve distinct findings and"
                " their evidence?"
            ),
            nodes=frozenset({"join", "merge"}),
            instruments=("score", "price-verbs"),
        ),
        "criticism": PhaseEntry(
            question=("Was the candidate judged correctly, on sufficient evidence?"),
            nodes=frozenset({"critic", "recritic", "rereview", "critic_failed"}),
            instruments=("score", "review"),
        ),
        "reporting": PhaseEntry(
            question=(
                "Did the conclusion, its qualifications and a useful remediation"
                " survive into the report?"
            ),
            nodes=frozenset({"assemble"}),
            instruments=("score", "review"),
        ),
    }
)


def phase_of(node: str) -> Phase:
    """The phase that owns ``node``, as an artifact spells the name.

    An artifact carries the graph's own names, and a per-framework node is
    ``<role>_<framework>`` while a lane agent is ``analyze_<framework>_<lane>``.
    So a name no phase owns outright is read again by its role, the part before
    the first underscore. The whole name is tried first, because
    ``reading_inventory`` and ``critic_failed`` are themselves names.

    Raises :class:`KeyError` for a name neither reading covers, which is the
    point of the table: an unattributed node is a hole somebody has to close,
    not a silent ``unknown``.
    """
    for candidate in (node, node.split("_", 1)[0]):
        for name, entry in PHASES.items():
            if candidate in entry.nodes:
                return name
    raise KeyError(
        f"no phase covers the node {node!r}; add it to evals.harness.audit.PHASES"
    )


class ExperimentError(ValueError):
    """A ledger row the loader refuses, naming the file and the reason."""


@dataclass(frozen=True)
class Experiment:
    """One experiment an audit ran, and what it concluded.

    The fields are what the audit loop reads back. A record carries its own
    falsifier because a hypothesis with no way to lose is not an experiment,
    and its own ``scope`` because the conclusion holds over the material it was
    measured on and nothing wider.
    """

    #: Stable within the ledger. Two rows may not share one.
    experiment_id: str
    #: The audit that ran it. Also the ledger file's stem.
    audit_id: str
    #: When it was recorded, UTC ISO 8601.
    recorded: str
    #: The commit the experiment ran against.
    revision: str
    #: The failure signature it addresses. The lookup key: an audit asks for
    #: this before proposing anything.
    signature: str
    #: What it claimed, and the observation that would have refuted it.
    hypothesis: str
    falsifier: str
    #: The change or the substitution the experiment made.
    intervention: str
    #: One of :data:`OUTCOMES`.
    outcome: Outcome
    #: What the conclusion covers: which cases, which framework, which models.
    scope: str
    #: The phase the loss was charged to, or ``None`` where attribution stayed
    #: unknown. Unknown is a legitimate answer and gets a value of its own.
    phase: Phase | None = None
    #: The framework package the conclusion is about, checked against
    #: ``PACKAGES``. ``None`` means the conclusion is neutral -- it holds
    #: whatever package a run selected -- which is a different claim from
    #: holding for one of them, and the two are kept apart for the reason
    #: ``docs/agents/framework-parity.md`` gives.
    framework: FrameworkName | None = None
    #: The corpus case, where the conclusion is about one. Free text, because a
    #: case directory is not a closed vocabulary and an experiment may name a
    #: fixture that is not a case at all.
    case: str = ""
    #: Repository paths the conclusion depends on. :func:`staleness` reads
    #: these; a record naming none can never go stale and says so.
    reads: tuple[str, ...] = ()
    #: Artifacts a reader can open: sweep JSON, replay output, a report.
    artifacts: tuple[str, ...] = ()
    #: What the experiment was expected to cost and what it actually cost, in
    #: US dollars. ``None`` for an offline experiment, which is not zero: an
    #: offline experiment has no provider cost to state.
    estimated_usd: float | None = None
    actual_usd: float | None = None
    #: The experiment this one resumes, and the one it corrects.
    parent: str | None = None
    supersedes: str | None = None
    #: What would make this worth revisiting.
    reconsider_when: str = ""

    def to_json(self) -> dict[str, Any]:
        """The row as the ledger file carries it."""
        return {
            "experiment_id": self.experiment_id,
            "audit_id": self.audit_id,
            "recorded": self.recorded,
            "revision": self.revision,
            "signature": self.signature,
            "hypothesis": self.hypothesis,
            "falsifier": self.falsifier,
            "intervention": self.intervention,
            "outcome": self.outcome,
            "scope": self.scope,
            "phase": self.phase,
            "framework": self.framework,
            "case": self.case,
            "reads": list(self.reads),
            "artifacts": list(self.artifacts),
            "estimated_usd": self.estimated_usd,
            "actual_usd": self.actual_usd,
            "parent": self.parent,
            "supersedes": self.supersedes,
            "reconsider_when": self.reconsider_when,
        }


_REQUIRED = (
    "experiment_id",
    "audit_id",
    "recorded",
    "revision",
    "signature",
    "hypothesis",
    "falsifier",
    "intervention",
    "outcome",
    "scope",
)


def parse(row: Mapping[str, Any], *, source: str) -> Experiment:
    """One ledger row, checked against the vocabulary before it is believed.

    ``source`` names the file for the error. Every refusal names the row's own
    ``experiment_id`` where it has one, because a ledger is read by whoever
    opens it next rather than by whoever wrote the bad row.
    """
    where = f"{source}: {row.get('experiment_id', '<no experiment_id>')}"
    missing = [key for key in _REQUIRED if not row.get(key)]
    if missing:
        raise ExperimentError(f"{where}: missing {', '.join(missing)}")
    outcome = row["outcome"]
    if outcome not in OUTCOMES:
        raise ExperimentError(
            f"{where}: outcome {outcome!r} is not one of {', '.join(OUTCOMES)}"
        )
    phase = row.get("phase")
    if phase is not None and phase not in PHASES:
        raise ExperimentError(
            f"{where}: phase {phase!r} is not one of {', '.join(PHASES)}"
        )
    framework = row.get("framework")
    if framework is not None and framework not in PACKAGES:
        raise ExperimentError(
            f"{where}: framework {framework!r} is not one of"
            f" {', '.join(sorted(PACKAGES))}"
        )
    return Experiment(
        experiment_id=row["experiment_id"],
        audit_id=row["audit_id"],
        recorded=row["recorded"],
        revision=row["revision"],
        signature=row["signature"],
        hypothesis=row["hypothesis"],
        falsifier=row["falsifier"],
        intervention=row["intervention"],
        outcome=outcome,
        scope=row["scope"],
        phase=phase,
        framework=framework,
        case=row.get("case", ""),
        reads=tuple(row.get("reads") or ()),
        artifacts=tuple(row.get("artifacts") or ()),
        estimated_usd=row.get("estimated_usd"),
        actual_usd=row.get("actual_usd"),
        parent=row.get("parent"),
        supersedes=row.get("supersedes"),
        reconsider_when=row.get("reconsider_when", ""),
    )


def load(directory: Path | None = None) -> tuple[Experiment, ...]:
    """Every experiment on record, oldest first, with duplicate IDs refused.

    An empty directory is an empty ledger and not an error: the first audit to
    run has no history, and refusing there would make the loop unstartable.
    """
    root = EXPERIMENTS_DIR if directory is None else directory
    found: list[Experiment] = []
    seen: dict[str, str] = {}
    for path in sorted(root.glob("*.jsonl")) if root.is_dir() else ():
        for number, line in enumerate(path.read_text("utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            source = f"{path.name}:{number}"
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ExperimentError(f"{source}: not JSON: {exc}") from exc
            experiment = parse(row, source=source)
            first = seen.get(experiment.experiment_id)
            if first is not None:
                raise ExperimentError(
                    f"{source}: experiment_id {experiment.experiment_id!r}"
                    f" is already used at {first}"
                )
            seen[experiment.experiment_id] = source
            found.append(experiment)
    return tuple(sorted(found, key=lambda one: one.recorded))


def append(experiment: Experiment, directory: Path | None = None) -> Path:
    """Add one row to its audit's file, creating the file where it is the first.

    Append-only: nothing here opens an existing row. Returns the file written.
    """
    root = EXPERIMENTS_DIR if directory is None else directory
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{experiment.audit_id}.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(experiment.to_json(), sort_keys=True) + "\n")
    return path


def superseded(experiments: Iterable[Experiment]) -> frozenset[str]:
    """Every experiment ID a later row corrects."""
    return frozenset(
        one.supersedes for one in experiments if one.supersedes is not None
    )


def relevant(
    experiments: Sequence[Experiment],
    *,
    signature: str = "",
    phase: Phase | None = None,
    framework: FrameworkName | None = None,
) -> tuple[Experiment, ...]:
    """The prior experiments an audit must read before it proposes anything.

    A match is a shared ``signature``, ``phase`` or ``framework``, because each
    is how a proposal is recognisably the same work as an earlier one: the same
    observed failure, the same stage, or the same package. A framework-neutral
    row matches every framework asked for, since a conclusion that holds
    whatever package ran holds for the one in hand.

    Superseded rows are kept — a correction says what was wrong, and dropping
    the row it corrects loses that — and :func:`superseded` marks them.

    With no argument this returns everything, which is what an audit opening a
    cold ledger wants.
    """
    if not signature and phase is None and framework is None:
        return tuple(experiments)
    return tuple(
        one
        for one in experiments
        if (signature and one.signature == signature)
        or (phase and one.phase == phase)
        or (framework and one.framework in (framework, None))
    )


@dataclass(frozen=True)
class Staleness:
    """Whether a record's conclusion still rests on the tree it was measured on."""

    #: The paths the record names that have changed since its revision.
    changed: tuple[str, ...] = ()
    #: Set where ``git`` cannot answer — no checkout, or a revision this clone
    #: does not carry. Neither stale nor current, and it says so rather than
    #: defaulting to either.
    undecidable: str = ""
    #: Set where the record names no path, so nothing could have changed it.
    unanchored: bool = False

    @property
    def state(self) -> str:
        """One word for the report: ``stale``, ``current``, or why not."""
        if self.undecidable:
            return "undecidable"
        if self.unanchored:
            return "unanchored"
        return "stale" if self.changed else "current"


def staleness(experiment: Experiment, changed: Iterable[str]) -> Staleness:
    """Which of a record's ``reads`` sit in ``changed``.

    Pure, so the git call stays at the edge and a test names the paths itself.
    """
    if not experiment.reads:
        return Staleness(unanchored=True)
    moved = set(changed)
    return Staleness(changed=tuple(path for path in experiment.reads if path in moved))


def changed_since(revision: str, root: Path | None = None) -> tuple[str, ...] | None:
    """Every tracked path that differs between ``revision`` and the tree now.

    ``None`` where ``git`` cannot answer, which :func:`staleness`'s caller
    turns into ``undecidable``. Reads the working tree as well as the commit,
    so an audit run against uncommitted edits is not told its history is
    current.
    """
    cwd = str(root if root is not None else REPO_ROOT)
    try:
        done = subprocess.run(
            ["git", "diff", "--name-only", revision],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return tuple(line for line in done.stdout.splitlines() if line)


@dataclass
class Lookup:
    """One prior experiment beside what the tree now says about it."""

    experiment: Experiment
    staleness: Staleness
    superseded: bool = False


def lookup(
    *,
    signature: str = "",
    phase: Phase | None = None,
    framework: FrameworkName | None = None,
    directory: Path | None = None,
    root: Path | None = None,
) -> list[Lookup]:
    """The prior experiments for a signature, each with its staleness read.

    The one call the audit loop makes at step "retrieve relevant prior
    experiments". It reads the tree for every distinct revision the matches
    name, once each, rather than once per row.
    """
    experiments = load(directory)
    corrected = superseded(experiments)
    matches = relevant(
        experiments, signature=signature, phase=phase, framework=framework
    )
    diffs: dict[str, tuple[str, ...] | None] = {}
    found = []
    for one in matches:
        if one.revision not in diffs:
            diffs[one.revision] = changed_since(one.revision, root)
        changed = diffs[one.revision]
        read = (
            Staleness(undecidable=f"git cannot reach {one.revision}")
            if changed is None
            else staleness(one, changed)
        )
        found.append(
            Lookup(
                experiment=one,
                staleness=read,
                superseded=one.experiment_id in corrected,
            )
        )
    return found


@dataclass
class Report:
    """What the ``experiments`` command prints, as lines."""

    lines: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return "\n".join(self.lines)


def render(found: Sequence[Lookup]) -> Report:
    """The lookup as a reader sees it: one block per experiment, newest last."""
    report = Report()
    if not found:
        report.lines.append("No prior experiment matches. This is new ground.")
        return report
    report.lines.append(f"{len(found)} prior experiment(s):")
    for one in found:
        experiment = one.experiment
        marks = [one.staleness.state]
        if one.superseded:
            marks.append("superseded")
        report.lines.append("")
        report.lines.append(
            f"  {experiment.experiment_id}  [{experiment.outcome}]"
            f"  ({', '.join(marks)})"
        )
        report.lines.append(f"    signature: {experiment.signature}")
        report.lines.append(f"    phase:     {experiment.phase or 'unknown'}")
        report.lines.append(
            f"    framework: {experiment.framework or 'neutral'}"
            f"{'  case: ' + experiment.case if experiment.case else ''}"
        )
        report.lines.append(
            f"    recorded:  {experiment.recorded} @ {experiment.revision}"
        )
        report.lines.append(f"    claimed:   {experiment.hypothesis}")
        report.lines.append(f"    scope:     {experiment.scope}")
        if one.staleness.changed:
            report.lines.append(f"    changed:   {', '.join(one.staleness.changed)}")
        if experiment.reconsider_when:
            report.lines.append(f"    revisit:   {experiment.reconsider_when}")
    return report


def phase_table() -> Iterator[str]:
    """The phase table as lines, for an audit that prints what it attributes to."""
    for name, entry in PHASES.items():
        yield f"{name}: {entry.question}"
        yield f"    nodes:       {', '.join(sorted(entry.nodes))}"
        yield f"    instruments: {', '.join(entry.instruments)}"


def arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--signature",
        default="",
        help="the failure signature to look up. With --phase, a row matching"
        " either is returned: the two are how a proposal is recognisably the"
        " same work as an earlier one.",
    )
    parser.add_argument(
        "--phase",
        default=None,
        choices=sorted(PHASES),
        help="the phase to look up",
    )
    parser.add_argument(
        "--framework",
        default=None,
        choices=sorted(PACKAGES),
        help="the framework package to look up. A framework-neutral row"
        " matches every package.",
    )
    parser.add_argument(
        "--record",
        default=None,
        metavar="FILE",
        help="a JSON file holding one experiment to append. The row is checked"
        " against the vocabulary before it is written, and a duplicate"
        " experiment_id is refused.",
    )


def command_experiments(args: argparse.Namespace) -> int:
    """Read the experiment ledger, or append one row to it. No credentials."""
    try:
        if args.record:
            row = json.loads(Path(args.record).read_text("utf-8"))
            experiment = parse(row, source=args.record)
            known = {one.experiment_id for one in load()}
            if experiment.experiment_id in known:
                raise ExperimentError(
                    f"{args.record}: experiment_id"
                    f" {experiment.experiment_id!r} is already on record"
                )
            print(f"recorded {experiment.experiment_id} in {append(experiment)}")
            return 0
        print(
            render(
                lookup(
                    signature=args.signature,
                    phase=args.phase,
                    framework=args.framework,
                )
            )
        )
    except (ExperimentError, OSError, json.JSONDecodeError) as error:
        print(f"cannot read the experiment ledger: {error}", file=sys.stderr)
        return 1
    return 0


def phase_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--node",
        default=None,
        help="answer for one node, as an artifact spells it"
        " (``analyze_<framework>_<lane>``), rather than printing the whole"
        " table",
    )


def command_phases(args: argparse.Namespace) -> int:
    """Print the phase table, or the phase one node sits in. No credentials."""
    if args.node:
        try:
            print(phase_of(args.node))
        except KeyError as error:
            print(error.args[0], file=sys.stderr)
            return 1
        return 0
    for line in phase_table():
        print(line)
    return 0
