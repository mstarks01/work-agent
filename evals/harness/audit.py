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
fix would land in. Attribution below node granularity is the artifact's job.

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

## The improvement loop's limits

An audit that changes code needs limits that hold whoever writes the row. A
:class:`Grant` records what the user authorised, in their words, and the
grants of one audit add up. :func:`admit` is the one reader of those limits:
``--record`` calls it before it appends, and it refuses a row that spends past
the grants, makes a change no grant allows, touches a :data:`PROTECTED` path,
extends past depth one, or repeats another audit's current conclusion without
a reason. A failed candidate kept rather than reverted is refused at
:func:`parse`, because that needs no other row to decide.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, TypeGuard

from analysis_service.claims import FrameworkName
from analysis_service.frameworks import PACKAGES
from evals.harness.history import current_commit
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

ConditionName = Literal["current-pipeline", "corrected-extraction", "direct-facts"]
Stage = Literal["analysis", "final-report"]

#: Which stage each condition of the skill's three-condition diagnostic can be
#: measured at. ``current-pipeline`` is the shipped route. ``corrected-extraction``
#: feeds the signed model through the same preparation and consumers, which
#: ``analysis`` mode already runs to a report. ``direct-facts`` adds every signed
#: source fact as an assertion row, so a fact the System Model has no field for
#: reaches the lanes, and the ``direct-facts`` eval mode runs it to a report;
#: ``run.py lane-replay`` reads it at the analysis stage for one call. A
#: condition added here without an entry cannot be recorded at all.
CONDITION_STAGES: Mapping[ConditionName, tuple[Stage, ...]] = MappingProxyType(
    {
        "current-pipeline": ("analysis", "final-report"),
        "corrected-extraction": ("analysis", "final-report"),
        "direct-facts": ("analysis", "final-report"),
    }
)


@dataclass(frozen=True)
class Condition:
    """What one experiment in the three-condition diagnostic fed and read.

    The fields a comparison between conditions needs and the ledger's other
    fields do not hold: which input generation read, every way it differed from
    the shipped route, what the signed material could not put into the System
    Model, and what the input's builder had seen of the answers.
    """

    name: ConditionName
    #: What generation read, named so a reader can reopen it.
    input: str
    #: Every adapter, prompt or interface difference from ``current-pipeline``.
    adapters: tuple[str, ...]
    #: Source facts the System Model could not represent. Kept rather than
    #: forced into the nearest field, because the difference is the result.
    unrepresentable: tuple[str, ...]
    #: Who built the input and what they had seen of the expected findings and
    #: the observed misses. "Nothing" is an answer; silence is not.
    exposure: str
    #: Where the outcome was read, one of the condition's
    #: :data:`CONDITION_STAGES`.
    stage: Stage


Verdict = Literal["kept", "reverted"]
Belongs = Literal["skill", "harness", "instrumentation", "scorer", "references"]
Mode = Literal["audit", "implement", "extend"]

#: What a grant lets an audit do, narrowest first. ``implement`` makes candidate
#: changes; ``extend`` also builds a missing capability to unblock an
#: experiment. Each mode includes the ones before it.
MODES: tuple[Mode, ...] = ("audit", "implement", "extend")

#: Where an extension ticket says the missing capability belongs. A missing
#: measurement fixed in the wrong place changes what every past number meant.
BELONGS: tuple[Belongs, ...] = (
    "skill",
    "harness",
    "instrumentation",
    "scorer",
    "references",
)

#: The paths a candidate change may never touch, each with the reason. A
#: candidate that edits a reference answer, a human ruling or a gate can make
#: any output pass, so the ledger refuses its row. A correction to one of these
#: is proposed on its own, with the before and after scores kept.
PROTECTED: Mapping[str, str] = MappingProxyType(
    {
        "evals/corpus/": "reference answers, rulings, sources and holdout flags",
        "evals/answers/": "signed answer sets",
        "evals/review/": "human votes and submissions",
        "evals/calibration_labels/": "human labels",
        "evals/baselines/": "sealed Baselines",
        "evals/experiments/": "the experiment ledger, which only --record appends to",
        "evals/harness/scorer.py": "the scorer",
        "evals/harness/promotion.py": "the promotion gates",
        ".github/": "the CI gates",
        ".githooks/": "the push gates",
    }
)


def protected(paths: Iterable[str]) -> tuple[str, ...]:
    """Each of ``paths`` that :data:`PROTECTED` covers, with the reason."""
    return tuple(
        f"{path} ({reason})"
        for path in paths
        for prefix, reason in PROTECTED.items()
        if path == prefix or (prefix.endswith("/") and path.startswith(prefix))
    )


@dataclass(frozen=True)
class Candidate:
    """One candidate change an experiment made, and what became of it.

    A candidate is one lever on one branch. A change that did not work is
    reverted, and the row keeps the reason, so the next audit does not try it
    again.
    """

    branch: str
    commit: str
    #: ``kept`` only where the experiment was ``supported``.
    verdict: Verdict
    #: Why it was kept or reverted. Required for a revert.
    reason: str
    #: The paths the change touched, from ``git diff`` between the row's
    #: ``revision`` and ``commit``. ``--record`` fills it.
    paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class Ticket:
    """The extension ticket a ``blocked`` row carries.

    Its fields are the sections of ``templates/extension-ticket.md``, and
    :func:`render_ticket` writes the issue body from them.
    """

    title: str
    question: str
    evidence: str
    missing: str
    alternatives: str
    belongs: Belongs
    change: str
    acceptance: str
    cost: str
    risk: str


@dataclass(frozen=True)
class Rule:
    """A diagnostic rule distilled from several experiments.

    A row that carries one states the rule's signature in ``signature`` and
    its smallest intervention in ``intervention``. These are the other three
    things a rule carries.
    """

    #: The checks that confirm or refute the suspected mechanism.
    checks: str
    #: The remedies that worked, and the conditions where they failed.
    remedies: str
    #: Where the rule stops applying, and whether one case or several hold it.
    limits: str
    #: The experiments the rule rests on. Each must be on record.
    supporting: tuple[str, ...]


@dataclass(frozen=True)
class Grant:
    """What the user authorised an audit to do, in their own words.

    The grants of an audit add up. A resumed audit gets a further budget by a
    new grant, never by an edit to an old one, so every widening has the
    user's words beside it.
    """

    audit_id: str
    recorded: str
    #: The user's words, quoted. A grant nobody said is not a grant.
    words: str
    mode: Mode
    #: Dollars added by this grant. Zero is a grant of no paid inference.
    budget_usd: float
    #: Improvement attempts added by this grant.
    attempts: int


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
#: ``extraction`` carries the whole head — the reading call, the validity gate
#: and the bounded repair — because a fact lost anywhere in it is lost before a
#: lane can read it. ``preparation`` is the routing half: what reaches
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
                    "assert",
                    "catalog",
                    "pause",
                    "read",
                    "validate",
                    "repair",
                    "revalidate",
                    "reject",
                }
            ),
            instruments=("replay", "bind"),
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
    #: The diagnostic condition, for an experiment in the three-condition
    #: diagnostic, and ``None`` for every other experiment.
    condition: Condition | None = None
    #: The candidate change, for an experiment that made one.
    candidate: Candidate | None = None
    #: The extension ticket. Every ``blocked`` row recorded through
    #: ``--record`` carries one.
    ticket: Ticket | None = None
    #: The diagnostic rule, for a row that distils one.
    rule: Rule | None = None
    #: The ``blocked`` row this extension unblocks. Set only on the row that
    #: records the extension's own candidate change.
    resolves: str | None = None
    #: Why this repeats a current conclusion from another audit. Required
    #: where one exists, so a rerun is never silent.
    rerun_reason: str = ""

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
            "condition": None
            if self.condition is None
            else {
                "name": self.condition.name,
                "input": self.condition.input,
                "adapters": list(self.condition.adapters),
                "unrepresentable": list(self.condition.unrepresentable),
                "exposure": self.condition.exposure,
                "stage": self.condition.stage,
            },
            "candidate": None
            if self.candidate is None
            else {**asdict(self.candidate), "paths": list(self.candidate.paths)},
            "ticket": None if self.ticket is None else asdict(self.ticket),
            "rule": None
            if self.rule is None
            else {**asdict(self.rule), "supporting": list(self.rule.supporting)},
            "resolves": self.resolves,
            "rerun_reason": self.rerun_reason,
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
    for cost in ("estimated_usd", "actual_usd"):
        if row.get(cost) is not None and not _is_dollars(row[cost]):
            raise ExperimentError(
                f"{where}: {cost} {row[cost]!r} is not a dollar amount; a negative"
                " cost would widen the audit's budget"
            )
    candidate = _candidate(row.get("candidate"), where)
    if candidate is not None and candidate.verdict == "kept" and outcome != "supported":
        raise ExperimentError(
            f"{where}: a {outcome} candidate is kept; a change that did not"
            " work is reverted, and the row says why"
        )
    if row.get("resolves") and candidate is None:
        raise ExperimentError(
            f"{where}: resolves {row['resolves']!r} with no candidate;"
            " an extension is a change and records one"
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
        condition=_condition(row.get("condition"), where),
        candidate=candidate,
        ticket=_ticket(row.get("ticket"), where),
        rule=_rule(row.get("rule"), where),
        resolves=row.get("resolves"),
        rerun_reason=row.get("rerun_reason", ""),
    )


def _is_dollars(value: object) -> TypeGuard[float]:
    """Whether ``value`` is a finite amount of zero or more, and not a bool."""
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _block(held: object, block: str, where: str) -> dict[str, Any] | None:
    """One optional object of a row: ``None`` where absent, refused where not an object."""
    if held is None:
        return None
    if not isinstance(held, dict):
        raise ExperimentError(f"{where}: {block} is not an object")
    return held


def _texts(
    held: Mapping[str, Any], names: Sequence[str], block: str, where: str
) -> dict[str, str]:
    """The named strings of one block, refusing the first that is missing or blank."""
    for name in names:
        if not isinstance(held.get(name), str) or not held[name].strip():
            raise ExperimentError(f"{where}: {block} has no {name}")
    return {name: held[name] for name in names}


def _strings(
    held: Mapping[str, Any], name: str, block: str, where: str
) -> tuple[str, ...]:
    value = held.get(name, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ExperimentError(f"{where}: {block}: {name} is not a list of strings")
    return tuple(value)


def _candidate(row: object, where: str) -> Candidate | None:
    held = _block(row, "candidate", where)
    if held is None:
        return None
    texts = _texts(held, ("branch", "commit"), "candidate", where)
    verdict = held.get("verdict")
    if verdict not in ("kept", "reverted"):
        raise ExperimentError(
            f"{where}: candidate verdict {verdict!r} is not kept or reverted"
        )
    reason = held.get("reason") or ""
    if verdict == "reverted" and not reason.strip():
        raise ExperimentError(f"{where}: a reverted candidate has no reason")
    return Candidate(
        **texts,
        verdict=verdict,
        reason=reason,
        paths=_strings(held, "paths", "candidate", where),
    )


def _ticket(row: object, where: str) -> Ticket | None:
    held = _block(row, "ticket", where)
    if held is None:
        return None
    texts = _texts(held, [one.name for one in fields(Ticket)], "ticket", where)
    if texts["belongs"] not in BELONGS:
        raise ExperimentError(
            f"{where}: ticket belongs {texts['belongs']!r} is not one of"
            f" {', '.join(BELONGS)}"
        )
    return Ticket(**texts)  # type: ignore[arg-type]


def _rule(row: object, where: str) -> Rule | None:
    held = _block(row, "rule", where)
    if held is None:
        return None
    texts = _texts(held, ("checks", "remedies", "limits"), "rule", where)
    supporting = _strings(held, "supporting", "rule", where)
    if not supporting:
        raise ExperimentError(f"{where}: a rule names no supporting experiment")
    return Rule(**texts, supporting=supporting)


def _condition(held: object, where: str) -> Condition | None:
    """A row's diagnostic condition, checked against its vocabulary and stages."""
    if held is None:
        return None
    if not isinstance(held, dict):
        raise ExperimentError(f"{where}: condition is not an object")
    name = held.get("name")
    if name not in CONDITION_STAGES:
        raise ExperimentError(
            f"{where}: condition {name!r} is not one of {', '.join(CONDITION_STAGES)}"
        )
    name = held["name"]
    stages = CONDITION_STAGES[name]
    stage = held.get("stage")
    if stage not in stages:
        raise ExperimentError(
            f"{where}: condition {name!r} is read at {', '.join(stages)},"
            f" never at {stage!r}"
        )
    for text in ("input", "exposure"):
        if not isinstance(held.get(text), str) or not held[text].strip():
            raise ExperimentError(f"{where}: condition {name!r} has no {text}")
    lists = {}
    for listed in ("adapters", "unrepresentable"):
        value = held.get(listed)
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ExperimentError(
                f"{where}: condition {name!r}: {listed} is not a list of strings"
            )
        lists[listed] = tuple(value)
    return Condition(
        name=name,
        input=held["input"],
        adapters=lists["adapters"],
        unrepresentable=lists["unrepresentable"],
        exposure=held["exposure"],
        stage=stage,
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
    audit_id: str = "",
) -> tuple[Experiment, ...]:
    """The prior experiments an audit must read before it proposes anything.

    A match is a shared ``signature``, ``phase`` or ``framework``, because each
    is how a proposal is recognisably the same work as an earlier one: the same
    observed failure, the same stage, or the same package. A framework-neutral
    row matches every framework asked for, since a conclusion that holds
    whatever package ran holds for the one in hand.

    Superseded rows are kept — a correction says what was wrong, and dropping
    the row it corrects loses that — and :func:`superseded` marks them.

    ``audit_id`` matches every row of one audit, which is what a resume reads.

    With no argument this returns everything, which is what an audit opening a
    cold ledger wants.
    """
    if not signature and phase is None and framework is None and not audit_id:
        return tuple(experiments)
    return tuple(
        one
        for one in experiments
        if (signature and one.signature == signature)
        or (phase and one.phase == phase)
        or (framework and one.framework in (framework, None))
        or (audit_id and one.audit_id == audit_id)
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

    A revision a history rewrite renamed is read at its new ID, so a row keeps
    the ID it was recorded with and still answers.

    ``None`` where ``git`` cannot answer, which :func:`staleness`'s caller
    turns into ``undecidable``. Reads the working tree as well as the commit,
    so an audit run against uncommitted edits is not told its history is
    current.
    """
    cwd = str(root if root is not None else REPO_ROOT)
    try:
        done = subprocess.run(
            ["git", "diff", "--name-only", current_commit(revision)],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return tuple(line for line in done.stdout.splitlines() if line)


def paths_between(
    base: str, commit: str, root: Path | None = None
) -> tuple[str, ...] | None:
    """Every path ``commit`` changed against ``base``, or ``None`` where git cannot say."""
    cwd = str(root if root is not None else REPO_ROOT)
    try:
        done = subprocess.run(
            # Without renames, a file moved out of a protected path names
            # both ends rather than only the new one.
            [
                "git",
                "diff",
                "--name-only",
                "--no-renames",
                current_commit(base),
                commit,
            ],
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
    audit_id: str = "",
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
        experiments,
        signature=signature,
        phase=phase,
        framework=framework,
        audit_id=audit_id,
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


def _grants_dir(directory: Path | None) -> Path:
    """Grants sit beside the rows, one file per audit, out of :func:`load`'s glob."""
    return (EXPERIMENTS_DIR if directory is None else directory) / "grants"


def parse_grant(row: Mapping[str, Any], *, source: str) -> Grant:
    """One grant, checked before it is believed."""
    where = f"{source}: grant"
    texts = _texts(row, ("audit_id", "recorded", "words"), "grant", where)
    mode = row.get("mode")
    if mode not in MODES:
        raise ExperimentError(
            f"{where}: mode {mode!r} is not one of {', '.join(MODES)}"
        )
    budget = row.get("budget_usd")
    if not _is_dollars(budget):
        raise ExperimentError(f"{where}: budget_usd {budget!r} is not a dollar amount")
    attempts = row.get("attempts")
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
        raise ExperimentError(f"{where}: attempts {attempts!r} is not a count")
    return Grant(**texts, mode=mode, budget_usd=float(budget), attempts=attempts)


def load_grants(audit_id: str, directory: Path | None = None) -> tuple[Grant, ...]:
    """Every grant one audit holds, oldest first. None is a grant of nothing."""
    path = _grants_dir(directory) / f"{audit_id}.jsonl"
    if not path.is_file():
        return ()
    lines = path.read_text("utf-8").splitlines()
    grants = tuple(
        parse_grant(json.loads(line), source=f"{path.name}:{number}")
        for number, line in enumerate(lines, start=1)
        if line.strip()
    )
    for one in grants:
        if one.audit_id != audit_id:
            raise ExperimentError(
                f"{path.name}: holds a grant for {one.audit_id!r}, not {audit_id!r}"
            )
    return grants


def append_grant(grant: Grant, directory: Path | None = None) -> Path:
    """Add one grant to its audit's file. Append-only, like the rows."""
    root = _grants_dir(directory)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{grant.audit_id}.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(grant), sort_keys=True) + "\n")
    return path


@dataclass(frozen=True)
class Standing:
    """What one audit holds and has used: the reading a resume starts from."""

    audit_id: str
    #: How many grants the audit holds. None is a grant of nothing.
    grants: int
    #: The widest mode any grant gave, or ``audit`` where none did.
    mode: Mode
    budget_usd: float
    attempts: int
    spent_usd: float
    attempts_used: int
    #: ``blocked`` rows that no extension has resolved yet.
    open_blockers: tuple[str, ...]
    #: Each resolved blocker, with the extension that resolved it and the rows
    #: that resumed it.
    resolved: Mapping[str, tuple[str, tuple[str, ...]]]


def _cost(experiment: Experiment) -> float:
    """What a row counts against its budget: the actual cost, else the estimate.

    An estimate with no actual is a reservation, so a run that never reported
    its usage still counts.
    """
    if experiment.actual_usd is not None:
        return experiment.actual_usd
    return experiment.estimated_usd or 0.0


def standing(
    audit_id: str, ledger: Sequence[Experiment], grants: Sequence[Grant]
) -> Standing:
    """The grants of one audit added up, beside what its rows have used."""
    rows = [one for one in ledger if one.audit_id == audit_id]
    mode = max((one.mode for one in grants), key=MODES.index, default=MODES[0])
    extensions = {one.resolves: one.experiment_id for one in rows if one.resolves}
    resolved = {
        blocked: (
            extension,
            tuple(one.experiment_id for one in rows if one.parent == blocked),
        )
        for blocked, extension in extensions.items()
    }
    return Standing(
        audit_id=audit_id,
        grants=len(grants),
        mode=mode,
        budget_usd=sum(one.budget_usd for one in grants),
        attempts=sum(one.attempts for one in grants),
        spent_usd=sum(_cost(one) for one in rows),
        attempts_used=sum(
            1 for one in rows if one.candidate is not None and one.resolves is None
        ),
        open_blockers=tuple(
            one.experiment_id
            for one in rows
            if one.outcome == "blocked" and one.experiment_id not in extensions
        ),
        resolved=MappingProxyType(resolved),
    )


def _raised_by_an_extension(
    experiment: Experiment, by_id: Mapping[str, Experiment]
) -> bool:
    """Whether any row up ``experiment``'s parent chain is an extension."""
    seen: set[str] = set()
    current: Experiment | None = experiment
    while current is not None and current.experiment_id not in seen:
        if current.resolves:
            return True
        seen.add(current.experiment_id)
        current = by_id.get(current.parent) if current.parent else None
    return False


def admit(
    new: Experiment,
    ledger: Sequence[Experiment],
    grants: Sequence[Grant],
    prior: Sequence[Lookup] = (),
) -> None:
    """Refuse a new row that widens what the audit was granted, or hides a rerun.

    The one reader of the audit's limits. ``--record`` calls it before it
    appends, so a limit holds whoever writes the row:

    * the audit's spend, the new row's included, stays inside its grants;
    * a candidate change needs an ``implement`` grant, an extension needs
      ``extend``, and improvement attempts stay inside the granted count;
    * a candidate touches no :data:`PROTECTED` path;
    * an extension resolves one open ``blocked`` row of its own audit, and a
      blocker an extension raised gets no further extension;
    * a ``blocked`` row carries its extension ticket;
    * a rule rests on experiments that are on record;
    * a repeat of another audit's current conclusion says why it reran.

    ``prior`` is :func:`lookup` for the new row's signature; the git reading
    stays at the edge so a test can name it.
    """
    where = new.experiment_id
    by_id = {one.experiment_id: one for one in ledger}
    held = standing(new.audit_id, ledger, grants)

    spent = held.spent_usd + _cost(new)
    if spent > held.budget_usd + 1e-9:
        raise ExperimentError(
            f"{where}: the audit would spend ${spent:.4f} of ${held.budget_usd:.4f}"
            " granted; a further amount is a new grant in the user's words"
        )
    if new.candidate is not None:
        needed: Mode = "extend" if new.resolves else "implement"
        if MODES.index(held.mode) < MODES.index(needed):
            raise ExperimentError(
                f"{where}: a {'extension' if new.resolves else 'candidate change'}"
                f" needs an {needed!r} grant; {new.audit_id} holds {held.mode!r}"
            )
        if new.resolves is None and held.attempts_used >= held.attempts:
            raise ExperimentError(
                f"{where}: {new.audit_id} has used {held.attempts_used} of"
                f" {held.attempts} granted attempts"
            )
        touched = protected(new.candidate.paths)
        if touched:
            raise ExperimentError(
                f"{where}: the candidate touches {'; '.join(touched)}. Propose"
                " that correction on its own, with the before and after scores"
            )
    if new.resolves:
        blocked = by_id.get(new.resolves)
        if (
            blocked is None
            or blocked.audit_id != new.audit_id
            or blocked.outcome != "blocked"
        ):
            raise ExperimentError(
                f"{where}: resolves {new.resolves!r}, which is not a blocked row"
                f" of {new.audit_id}"
            )
        if new.resolves in held.resolved:
            raise ExperimentError(
                f"{where}: {new.resolves} is already resolved by"
                f" {held.resolved[new.resolves][0]}"
            )
        if _raised_by_an_extension(blocked, by_id):
            raise ExperimentError(
                f"{where}: {new.resolves} was raised under an extension, and an"
                " extension may not trigger another"
            )
    if new.outcome == "blocked" and new.ticket is None:
        raise ExperimentError(
            f"{where}: a blocked row carries its extension ticket"
            " (templates/extension-ticket.md)"
        )
    if new.parent and new.parent not in by_id:
        raise ExperimentError(f"{where}: parent {new.parent!r} is not on record")
    if new.rule is not None:
        missing = [one for one in new.rule.supporting if one not in by_id]
        if missing:
            raise ExperimentError(
                f"{where}: the rule rests on {', '.join(missing)}, not on record"
            )
    repeated = [
        one.experiment.experiment_id
        for one in prior
        if one.experiment.audit_id != new.audit_id
        and one.staleness.state == "current"
        and not one.superseded
    ]
    if repeated and not new.rerun_reason.strip():
        raise ExperimentError(
            f"{where}: {', '.join(repeated)} already answer this signature and"
            " read current; say why this reruns them in rerun_reason"
        )


def render_ticket(experiment: Experiment) -> tuple[str, str]:
    """The issue title and body for a ``blocked`` row's extension ticket."""
    ticket = experiment.ticket
    if ticket is None:
        raise ExperimentError(f"{experiment.experiment_id}: carries no ticket")
    # The order of ``templates/extension-ticket.md``, which a test holds.
    sections = (
        ("The blocked question", ticket.question),
        ("Evidence", ticket.evidence),
        ("The missing capability", ticket.missing),
        ("Alternatives checked", ticket.alternatives),
        ("Where the change belongs", ticket.belongs),
        ("The smallest change", ticket.change),
        ("Acceptance", ticket.acceptance),
        ("Cost", ticket.cost),
        ("Risk and rollback", ticket.risk),
    )
    lines: list[str] = []
    for heading, text in sections:
        lines += [f"## {heading}", "", text, ""]
    lines += [
        "## Resume",
        "",
        (
            f"Audit `{experiment.audit_id}`, experiment"
            f" `{experiment.experiment_id}` at `{experiment.revision}`. An"
            f" extension records a row with `resolves: {experiment.experiment_id}`,"
            " and the resumed experiment names it as `parent`."
        ),
    ]
    return ticket.title, "\n".join(lines)


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
        if experiment.rerun_reason:
            report.lines.append(f"    reran:     {experiment.rerun_reason}")
        if experiment.candidate is not None:
            report.lines.append(
                f"    candidate: {experiment.candidate.verdict}"
                f" {experiment.candidate.branch} @ {experiment.candidate.commit}"
                f"{': ' + experiment.candidate.reason if experiment.candidate.reason else ''}"
            )
        if experiment.resolves:
            report.lines.append(f"    resolves:  {experiment.resolves}")
        if experiment.rule is not None:
            report.lines.append(f"    rule:      checks: {experiment.rule.checks}")
            report.lines.append(f"               remedies: {experiment.rule.remedies}")
            report.lines.append(f"               limits: {experiment.rule.limits}")
            report.lines.append(
                f"               rests on: {', '.join(experiment.rule.supporting)}"
            )
    return report


def render_standing(held: Standing) -> Iterator[str]:
    """One audit's grants beside what it has used, for a resume to start from."""
    if not held.grants:
        yield f"{held.audit_id}: no grant on record, so mode audit and $0 to spend"
        yield f"    spent:     ${held.spent_usd:.4f}"
    else:
        yield f"{held.audit_id}: mode {held.mode}, {held.grants} grant(s)"
        yield f"    spent:     ${held.spent_usd:.4f} of ${held.budget_usd:.4f} granted"
        yield f"    attempts:  {held.attempts_used} of {held.attempts} granted"
    yield f"    blocked:   {', '.join(held.open_blockers) or 'none open'}"
    for blocked, (extension, resumed) in held.resolved.items():
        yield (
            f"    resolved:  {blocked} by {extension};"
            f" resumed by {', '.join(resumed) or 'nothing yet'}"
        )


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
        "--audit",
        default="",
        metavar="AUDIT_ID",
        help="every row of one audit, after its grants and what it has used:"
        " the reading a resume starts from",
    )
    parser.add_argument(
        "--grant",
        default=None,
        metavar="FILE",
        help="a JSON file holding one grant to append: the user's words, the"
        " mode, and the dollars and attempts it adds",
    )
    parser.add_argument(
        "--ticket",
        default=None,
        metavar="EXPERIMENT_ID",
        help="print the extension ticket of one blocked row as an issue title and body",
    )
    parser.add_argument(
        "--record",
        default=None,
        metavar="FILE",
        help="a JSON file holding one experiment to append. The row is checked"
        " against the vocabulary before it is written, and a duplicate"
        " experiment_id is refused.",
    )


def record(source: Path) -> str:
    """Check one row from ``source`` against the ledger and its grants, then append it.

    A candidate's paths come from git, not from the row, so the protected-path
    check reads what the change did rather than what its author listed.
    """
    experiment = parse(json.loads(source.read_text("utf-8")), source=str(source))
    ledger = load()
    if experiment.experiment_id in {one.experiment_id for one in ledger}:
        raise ExperimentError(
            f"{source}: experiment_id {experiment.experiment_id!r} is already on record"
        )
    if experiment.candidate is not None:
        paths = paths_between(experiment.revision, experiment.candidate.commit)
        if paths is None:
            raise ExperimentError(
                f"{source}: git cannot diff {experiment.candidate.commit} against"
                f" {experiment.revision}; record a candidate while its commit exists"
            )
        experiment = replace(
            experiment, candidate=replace(experiment.candidate, paths=paths)
        )
    admit(
        experiment,
        ledger,
        load_grants(experiment.audit_id),
        lookup(signature=experiment.signature),
    )
    return f"recorded {experiment.experiment_id} in {append(experiment)}"


def command_experiments(args: argparse.Namespace) -> int:
    """Read the experiment ledger, or append one row to it. No credentials."""
    try:
        if args.grant:
            grant = parse_grant(
                json.loads(Path(args.grant).read_text("utf-8")), source=args.grant
            )
            print(f"granted {grant.audit_id} in {append_grant(grant)}")
            return 0
        if args.ticket:
            found = {one.experiment_id: one for one in load()}
            if args.ticket not in found:
                raise ExperimentError(f"{args.ticket}: not on record")
            title, body = render_ticket(found[args.ticket])
            print(f"{title}\n\n{body}")
            return 0
        if args.record:
            print(record(Path(args.record)))
            return 0
        if args.audit:
            for line in render_standing(
                standing(args.audit, load(), load_grants(args.audit))
            ):
                print(line)
            print()
        print(
            render(
                lookup(
                    signature=args.signature,
                    phase=args.phase,
                    framework=args.framework,
                    audit_id=args.audit,
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
