"""The offline comparison between #1003's four extraction arms.

The experiment asks whether reading source facts before binding them to a
System Model recovers more of what the sources state. Four arms answer it, and
this module is what turns their saved artifacts into the number the decision
gates read. **It runs no model and reads no credential**: every figure here is
computed from artifacts a sweep already wrote, which is what makes the
comparison reproducible and re-runnable after a reference correction.

The primary endpoint is the macro-average per-case recall of **explicitly
stated required facts**. Three rules make that one number rather than several:

**A recovered fact is one the replay calls ``found``**, which is the signed
reference's own matcher — right subject, right predicate, right value, right
scope, under the aliases a reviewer signed. Nothing here re-decides a match;
:func:`~evals.harness.replay.replay_assertions` is the one reader of that
question, and this module counts what it answered.

**The denominator is the case's stated rows and never the run's.** A run that
produced nothing recovers none of them rather than dropping out, so a route
that fails half the time cannot score well on the half it finishes. Failure
rate is reported beside recall and never inside it.

**Repeats average inside a case before cases average.** A case run five times
is one observation, not five: the uncertainty that matters is between system
descriptions, and treating correlated repeats as independent is how a
correlated spread reads as a significant difference.

The uncertainty is a **cluster bootstrap over cases**, so every repeat of a
resampled case travels with it, and the interval is corrected for the four
planned comparisons in :data:`COMPARISONS`. A fifth comparison corrects itself,
because the correction reads the table.

Nothing here decides the experiment. It computes the figures the preregistered
gates are read against, and a gate whose interval spans zero is *inconclusive*
rather than passed — which is a reading a person makes, not a function.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from analysis_service.assertions import UNKNOWN, assertion_id
from analysis_service.deployment import (
    ASSERTIONS_VAR,
    FACTS_FIRST_EXTRACTION_VAR,
    SOURCE_REVIEW_VAR,
)
from analysis_service.evidence import render_rows
from analysis_service.graph import render_model
from analysis_service.markdown_loader import MarkdownLoader, estimate_tokens
from analysis_service.prompts import (
    compose_assert_prompt,
    compose_extract_prompt,
    compose_facts_prompt,
    compose_reread_prompt,
)
from analysis_service.sources import render_sources
from evals.harness.reference import GoldenCase, load_corpus
from evals.harness.replay import (
    PRODUCED_FATES,
    ROW_FATES,
    AssertionReplay,
    SignedReference,
    signed_reference,
)


@dataclass(frozen=True)
class ArmRule:
    """One arm: the question it answers, and the variables that ask it.

    ``variables`` is what an operator sets to run it, and it is the whole of
    what separates one arm from another — the corpus, the commit, the prompts
    and the model configuration are held fixed. ``tests/test_evals_arms.py``
    builds each arm's pipeline from these and checks the head it names, so an
    arm here and a route in the graph cannot drift apart.
    """

    question: str
    head: tuple[str, ...]
    variables: Mapping[str, str]


#: The four arms, keyed by the letters #1003 gives them. A table rather than
#: four constants: every figure below is computed per arm by walking it, so a
#: fifth arm is a row here and no edit anywhere else.
ARMS: Mapping[str, ArmRule] = MappingProxyType(
    {
        "A": ArmRule(
            question="the baseline: current extraction, with assertions enabled",
            head=("extract", "read", "assert", "prepare"),
            variables=MappingProxyType({ASSERTIONS_VAR: "true"}),
        ),
        "B": ArmRule(
            question="does facts-first help without another review pass?",
            head=("facts", "resolve", "prepare"),
            variables=MappingProxyType({FACTS_FIRST_EXTRACTION_VAR: "true"}),
        ),
        "C": ArmRule(
            question="does the current architecture benefit as much from recovery?",
            head=("extract", "read", "assert", "reading", "reread", "apply", "prepare"),
            variables=MappingProxyType(
                {ASSERTIONS_VAR: "true", SOURCE_REVIEW_VAR: "true"}
            ),
        ),
        "D": ArmRule(
            question="does recovery add value to facts-first?",
            head=("facts", "resolve", "reading", "reread", "apply", "prepare"),
            variables=MappingProxyType(
                {FACTS_FIRST_EXTRACTION_VAR: "true", SOURCE_REVIEW_VAR: "true"}
            ),
        ),
    }
)

#: The comparisons #1003 declares before any run, left against right. Declared
#: rather than derived from every pair of arms, because the multiplicity
#: correction has to answer for the comparisons that were **planned**: six
#: pairwise differences corrected as four would overstate every interval.
COMPARISONS: tuple[tuple[str, str], ...] = (
    ("B", "A"),
    ("C", "A"),
    ("D", "B"),
    ("D", "C"),
)

#: The interval #1003's first gate names.
LEVEL = 0.95


def corrected_level(planned: Sequence[tuple[str, str]] = COMPARISONS) -> float:
    """The level one comparison of ``planned`` is reported at, Bonferroni.

    **The one reader of how wide the family makes an interval.** The comparison
    computes it and the report prints it, and a caller that compares a
    different set of arms gets the level its own plan earns rather than the
    module default's — which is the hole a second spelling of this arithmetic
    left: the interval would widen and the sentence beside it would not.
    """
    return 1 - (1 - LEVEL) / len(planned)


#: The level the four planned comparisons are reported at.
CORRECTED_LEVEL = corrected_level()

#: How many resamples the interval rests on. Enough that the percentile moves
#: less than the third decimal between seeds, and small enough that the whole
#: comparison runs in a second.
BOOTSTRAP_DRAWS = 10_000

#: The seed the resampling runs under, recorded because an interval nobody can
#: reproduce is not evidence. A published figure names it.
BOOTSTRAP_SEED = 1003


def required_rows(reference: SignedReference) -> tuple[str, ...]:
    """Every reference row the primary endpoint's denominator counts.

    **Explicitly stated**, which is the endpoint's own wording: the source says
    the thing, and what it says is a value rather than the unknown sentinel. A
    stated *absence* is one of these — "no MFA" is a fact the source states, and
    the one this layer exists to keep — while an ``unknown`` row records a
    question the source raised and left open, and a route that leaves it open
    has lost nothing.

    A justified inference is out too. #1003's primary endpoint is stated facts,
    and inference recall is reported beside it rather than mixed into it.
    """
    return tuple(
        assertion_id(entry)
        for entry in reference.entries
        if entry.basis == "stated" and entry.value != UNKNOWN
    )


@dataclass(frozen=True)
class ArmRun:
    """One arm's run of one case: what it recovered, and what it cost.

    ``required`` is the case's denominator and never this run's, so a run that
    produced nothing scores zero rather than dropping out. ``valid`` says the
    job produced usable output at all; a false one carries empty fates by
    construction, because there was nothing to grade.
    """

    case_id: str
    arm: str
    repeat: int
    required: int
    #: :data:`~evals.harness.replay.ROW_FATES` -> count, over the required rows
    #: alone. A fate outside the required set is a row this endpoint does not
    #: count, and it is dropped here rather than filtered by every reader.
    fates: Mapping[str, int] = field(default_factory=dict)
    #: :data:`~evals.harness.replay.PRODUCED_FATES` -> count, over every row the
    #: run produced that no reference row took.
    produced: Mapping[str, int] = field(default_factory=dict)
    valid: bool = True
    cost_usd: float = 0.0
    seconds: float = 0.0

    @property
    def recovered(self) -> int:
        """Required rows this run answered with the right value at the right scope."""
        return self.fates.get("found", 0) if self.valid else 0

    @property
    def recall(self) -> float:
        """The primary endpoint for one run. A case with no required row is 1.0."""
        if not self.required:
            return 1.0
        return self.recovered / self.required

    @classmethod
    def of(
        cls,
        replay: AssertionReplay,
        reference: SignedReference,
        *,
        arm: str,
        repeat: int = 0,
        valid: bool = True,
        cost_usd: float = 0.0,
        seconds: float = 0.0,
    ) -> ArmRun:
        """One run, read off the replay that graded it.

        The fates are narrowed to the required rows here, which is the one place
        the endpoint's denominator is applied: a reader counting ``found`` in
        this record is counting stated facts by construction.
        """
        required = frozenset(required_rows(reference))
        fates: dict[str, int] = dict.fromkeys(ROW_FATES, 0)
        for row in replay.rows:
            if row.reference in required:
                fates[row.fate] += 1
        produced: dict[str, int] = dict.fromkeys(PRODUCED_FATES, 0)
        for fate in replay.produced.values():
            produced[fate] += 1
        return cls(
            case_id=replay.case_id,
            arm=arm,
            repeat=repeat,
            required=len(required),
            fates=MappingProxyType(fates),
            produced=MappingProxyType(produced),
            valid=valid,
            cost_usd=cost_usd,
            seconds=seconds,
        )

    @classmethod
    def unusable(
        cls,
        case_id: str,
        reference: SignedReference,
        *,
        arm: str,
        repeat: int = 0,
        cost_usd: float = 0.0,
        seconds: float = 0.0,
    ) -> ArmRun:
        """A run whose output nothing could grade, counted rather than dropped.

        **The row #1003 asks never to disappear.** A transport failure, a
        malformed emission, a model the validity gate refused twice — each ends
        with no catalog to score, and each recovers none of the case's required
        facts. Reporting it as a failure *and* as a zero is the honest pair:
        removing it from the denominator would make an unreliable route look
        like a good one.
        """
        return cls(
            case_id=case_id,
            arm=arm,
            repeat=repeat,
            required=len(required_rows(reference)),
            fates=MappingProxyType(dict.fromkeys(ROW_FATES, 0)),
            produced=MappingProxyType(dict.fromkeys(PRODUCED_FATES, 0)),
            valid=False,
            cost_usd=cost_usd,
            seconds=seconds,
        )


def case_recall(runs: Collection[ArmRun], arm: str) -> dict[str, float]:
    """One arm's mean recall per case, averaging that case's repeats first.

    **The averaging order is the whole of why this function exists.** Five
    repeats of one case are one observation of one system description, and
    pooling them with another case's repeats weights a case by how many times
    it happened to run.
    """
    by_case: dict[str, list[float]] = {}
    for run in runs:
        if run.arm == arm:
            by_case.setdefault(run.case_id, []).append(run.recall)
    return {case: statistics.fmean(values) for case, values in by_case.items()}


def macro_recall(runs: Collection[ArmRun], arm: str) -> float:
    """One arm's macro-average recall: the mean over cases, each averaged first."""
    per_case = case_recall(runs, arm)
    return statistics.fmean(per_case.values()) if per_case else 0.0


def failure_rate(runs: Collection[ArmRun], arm: str) -> float:
    """The share of one arm's runs that produced nothing to grade."""
    ours = [run for run in runs if run.arm == arm]
    if not ours:
        return 0.0
    return sum(not run.valid for run in ours) / len(ours)


def unsupported_rate(runs: Collection[ArmRun], arm: str) -> float:
    """Produced rows no reference row took, per produced row.

    The non-inferiority number #1003's second gate reads. A row is ``matched``
    where a reference row took it, ``misattached`` where it sat on a subject the
    reference puts elsewhere, and ``unreviewed`` where nothing signed says
    whether it is real — and the last is *not* evidence of a wrong fact, only of
    one nobody adjudicated. The gate reads the rate and the counts together for
    that reason.
    """
    produced = [run.produced for run in runs if run.arm == arm]
    total = sum(sum(one.values()) for one in produced)
    if not total:
        return 0.0
    unmatched = sum(
        count for one in produced for fate, count in one.items() if fate != "matched"
    )
    return unmatched / total


@dataclass(frozen=True)
class PairedDifference:
    """One planned comparison: the paired mean difference and its interval.

    ``cases`` is the number of independent system descriptions the difference
    rests on, which is the sample size that matters — never the number of runs.
    ``low`` and ``high`` are percentiles of a cluster bootstrap at
    :attr:`level`, so a whole case travels with its repeats on every resample.
    """

    left: str
    right: str
    cases: int
    difference: float
    low: float
    high: float
    level: float

    @property
    def decisive(self) -> bool:
        """Whether the interval excludes zero on the side the difference points.

        It is not a verdict. #1003's first gate asks for a lower bound above
        zero **and** a difference of at least five points; this answers the
        first half, and a reader who treats it as the whole gate has skipped
        the effect size.
        """
        return self.low > 0 or self.high < 0


def paired_difference(
    runs: Collection[ArmRun],
    left: str,
    right: str,
    *,
    level: float = CORRECTED_LEVEL,
    draws: int = BOOTSTRAP_DRAWS,
    seed: int = BOOTSTRAP_SEED,
) -> PairedDifference:
    """``left`` minus ``right``, per case, with a cluster bootstrap interval.

    Only cases **both** arms ran are compared. A case one arm skipped carries no
    paired difference, and pooling the arm that did run it against the mean of
    the other is how an unbalanced set produces a difference nobody observed.

    The resample is over cases, drawn with replacement, and the interval is the
    percentiles of the resampled means. ``seed`` is a parameter rather than a
    global so a caller can show the answer does not turn on it, and the default
    is recorded in :data:`BOOTSTRAP_SEED` because an interval nobody can
    reproduce is not evidence.
    """
    ours, theirs = case_recall(runs, left), case_recall(runs, right)
    shared = sorted(set(ours) & set(theirs))
    differences = [ours[case] - theirs[case] for case in shared]
    if not differences:
        return PairedDifference(left, right, 0, 0.0, 0.0, 0.0, level)
    drawn = _bootstrap(differences, draws=draws, seed=seed)
    tail = (1 - level) / 2
    return PairedDifference(
        left=left,
        right=right,
        cases=len(shared),
        difference=statistics.fmean(differences),
        low=_percentile(drawn, tail),
        high=_percentile(drawn, 1 - tail),
        level=level,
    )


def _bootstrap(values: Sequence[float], *, draws: int, seed: int) -> list[float]:
    """The mean of ``len(values)`` cases resampled with replacement, ``draws`` times."""
    rng = random.Random(seed)
    size = len(values)
    return sorted(statistics.fmean(rng.choices(values, k=size)) for _ in range(draws))


def _percentile(sorted_values: Sequence[float], fraction: float) -> float:
    """One percentile of an already sorted sample, by nearest rank.

    Nearest rank rather than interpolation, because the sample is 10,000 draws
    and the difference between the two is far below the third decimal — and a
    rank is a value the sample actually holds, which an interpolated one is not.
    """
    index = min(
        len(sorted_values) - 1, max(0, round(fraction * (len(sorted_values) - 1)))
    )
    return sorted_values[index]


def comparisons(
    runs: Collection[ArmRun], planned: Sequence[tuple[str, str]] = COMPARISONS
) -> tuple[PairedDifference, ...]:
    """Every planned comparison, each at the family-corrected level."""
    return tuple(
        paired_difference(runs, left, right, level=corrected_level(planned))
        for left, right in planned
    )


def report(
    runs: Collection[ArmRun], planned: Sequence[tuple[str, str]] = COMPARISONS
) -> str:
    """The per-case and per-error-class report #1003 asks for, as text.

    Three tables and nothing aggregated away. The first is each arm's macro
    recall with the failure rate beside it, because a route that scores well on
    the jobs it finishes is not the same as one that finishes. The second is
    every arm's error classes, so a difference in recall can be read against
    what moved. The third is the planned comparisons, at the corrected level,
    each saying how many independent descriptions it rests on.

    It names no verdict. #1003's gates are read against these numbers by a
    person, and an interval spanning zero is *inconclusive* rather than passed.
    """
    arms = [arm for arm in ARMS if any(run.arm == arm for run in runs)]
    lines = ["## Arms", "", "| arm | cases | runs | recall | failed | unsupported |"]
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for arm in arms:
        ours = [run for run in runs if run.arm == arm]
        lines.append(
            f"| {arm} | {len(case_recall(runs, arm))} | {len(ours)} |"
            f" {macro_recall(runs, arm):.3f} | {failure_rate(runs, arm):.3f} |"
            f" {unsupported_rate(runs, arm):.3f} |"
        )
    lines += ["", "## Error classes", "", "| arm | " + " | ".join(ROW_FATES) + " |"]
    lines.append("| --- |" + " --- |" * len(ROW_FATES))
    for arm in arms:
        counts = _fate_totals(runs, arm)
        lines.append(
            f"| {arm} | " + " | ".join(str(counts[fate]) for fate in ROW_FATES) + " |"
        )
    lines += [
        "",
        "## Planned comparisons",
        "",
        (
            f"Cluster bootstrap over cases, {BOOTSTRAP_DRAWS} draws, seed"
            f" {BOOTSTRAP_SEED}, at {corrected_level(planned):.4f} —"
            f" {LEVEL:.2f} corrected for {len(planned)} planned comparisons."
        ),
        "",
        "| comparison | cases | difference | interval |",
        "| --- | --- | --- | --- |",
    ]
    for found in comparisons(runs, planned):
        lines.append(
            f"| {found.left} − {found.right} | {found.cases} |"
            f" {found.difference:+.3f} | {found.low:+.3f} to {found.high:+.3f} |"
        )
    lines += ["", "## Per case", "", "| case | " + " | ".join(arms) + " |"]
    lines.append("| --- |" + " --- |" * len(arms))
    recalls = {arm: case_recall(runs, arm) for arm in arms}
    for case in sorted({run.case_id for run in runs}):
        cells = " | ".join(
            f"{recalls[arm][case]:.3f}" if case in recalls[arm] else "—" for arm in arms
        )
        lines.append(f"| {case} | {cells} |")
    return "\n".join(lines) + "\n"


def _fate_totals(runs: Collection[ArmRun], arm: str) -> Mapping[str, int]:
    """One arm's error classes, summed over every run of every case."""
    totals: dict[str, int] = dict.fromkeys(ROW_FATES, 0)
    for run in runs:
        if run.arm != arm:
            continue
        for fate, count in run.fates.items():
            totals[fate] += count
    return totals


#: The version of the file :func:`load_runs` reads. A sweep writes it, and a
#: file written under another spelling is refused rather than read part-way:
#: the fate names are the endpoint's own vocabulary, and a file keyed by an
#: older set would silently score zero for a class that was renamed.
ARTIFACT_VERSION = 1


class ArmsError(ValueError):
    """A runs file this module will not read, with the reason in the message."""


def to_json(run: ArmRun) -> dict[str, Any]:
    """One run as the row a sweep writes, in the shape :func:`load_runs` reads."""
    return {
        "case": run.case_id,
        "arm": run.arm,
        "repeat": run.repeat,
        "required": run.required,
        "fates": {fate: run.fates.get(fate, 0) for fate in ROW_FATES},
        "produced": {fate: run.produced.get(fate, 0) for fate in PRODUCED_FATES},
        "valid": run.valid,
        "cost_usd": run.cost_usd,
        "seconds": run.seconds,
    }


def write_runs(path: Path, runs: Sequence[ArmRun]) -> Path:
    """Every run to one file, versioned, for a comparison somebody re-runs."""
    payload = {
        "artifact_version": ARTIFACT_VERSION,
        "runs": [to_json(run) for run in runs],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def load_runs(path: Path) -> tuple[ArmRun, ...]:
    """Every run one file records, or a refusal naming what is wrong with it.

    **It fails closed on a fate name it does not know.** A file keyed by an
    older vocabulary would read as zeros for whatever was renamed, which is a
    silent loss in the one number this module exists to compute — so a row's
    fate keys must be exactly :data:`~evals.harness.replay.ROW_FATES`, and an
    arm must be one :data:`ARMS` holds.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    version = payload.get("artifact_version")
    if version != ARTIFACT_VERSION:
        raise ArmsError(
            f"{path}: artifact_version {version!r} is not {ARTIFACT_VERSION};"
            " this comparison reads one spelling of the file"
        )
    runs = []
    for index, row in enumerate(payload.get("runs", [])):
        runs.append(_run_of(path, index, row))
    return tuple(runs)


def _run_of(path: Path, index: int, row: Mapping[str, Any]) -> ArmRun:
    """One row of a runs file, checked against the vocabularies it keys on."""
    arm = row.get("arm")
    if arm not in ARMS:
        raise ArmsError(
            f"{path}: run {index} names arm {arm!r}, which is not one of {sorted(ARMS)}"
        )
    fates = row.get("fates", {})
    if set(fates) != set(ROW_FATES):
        raise ArmsError(
            f"{path}: run {index} keys its fates on {sorted(fates)} and this"
            f" comparison counts {sorted(ROW_FATES)}"
        )
    produced = row.get("produced", {})
    if set(produced) != set(PRODUCED_FATES):
        raise ArmsError(
            f"{path}: run {index} keys its produced rows on {sorted(produced)}"
            f" and this comparison counts {sorted(PRODUCED_FATES)}"
        )
    return ArmRun(
        case_id=str(row["case"]),
        arm=str(arm),
        repeat=int(row.get("repeat", 0)),
        required=int(row["required"]),
        fates=MappingProxyType(dict(fates)),
        produced=MappingProxyType(dict(produced)),
        valid=bool(row.get("valid", True)),
        cost_usd=float(row.get("cost_usd", 0.0)),
        seconds=float(row.get("seconds", 0.0)),
    )


def price_arguments(parser: argparse.ArgumentParser) -> None:
    """Where the cases are, and where the estimate goes."""
    parser.add_argument(
        "--corpus",
        type=Path,
        default=Path("evals") / "corpus",
        help="the corpus directory to estimate over",
    )
    parser.add_argument(
        "--prompts",
        type=Path,
        default=Path("prompts"),
        help="the prompt root the instructions are composed from",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="write the estimate here as well as printing it",
    )


def command_price_arms(args: argparse.Namespace) -> int:
    """Estimate what each arm is given, before anybody authorises a run.

    No provider, no credential, no spend: the instructions are composed from
    the tree and the rendered artifacts come from the corpus through the
    functions the graph renders with. It answers the input half of #1003's cost
    gate and says plainly that it answers no more than that.
    """
    cases = load_corpus(args.corpus)
    references = {}
    for case in cases:
        found = signed_reference(args.corpus, case)
        if found is not None:
            references[case.id] = found
    built = input_report(cases, MarkdownLoader(args.prompts), references)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(built, encoding="utf-8")
    print(built, end="")
    return 0


def arguments(parser: argparse.ArgumentParser) -> None:
    """The one input and the optional output of the offline comparison."""
    parser.add_argument(
        "--runs",
        type=Path,
        required=True,
        help="the runs file a sweep wrote, as read by evals.harness.arms",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="write the report here as well as printing it",
    )


def command_compare_arms(args: argparse.Namespace) -> int:
    """Recompute #1003's comparison from saved runs, with no model call.

    The whole command is a read and a print. Nothing here asks a provider for
    anything, so the same file gives the same report on every machine, and a
    reference correction is answered by re-running this rather than by paying
    for the sweep again.
    """
    runs = load_runs(args.runs)
    if not runs:
        print(f"{args.runs} records no run", file=sys.stderr)
        return 1
    built = report(runs)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(built, encoding="utf-8")
    print(built, end="")
    return 0


# --- What an arm is given, before anybody pays for what it writes ------------
#
# #1003's fifth gate holds each arm's end-to-end cost within twice arm A's. The
# output half of that cannot be known before a run. The **input** half can, and
# exactly: a node's static instruction is composed from files in the tree, and
# what it is shown is rendered from the case by the same functions the graph
# calls. So the ratio between the arms on the input side is computable with no
# provider, no credential and no spend — which is what AGENTS.md asks for
# before a run is authorised.
#
# The lane agents and the critics are absent on purpose. Every arm ends at one
# `prepare` and one fan-out, so they cost the same on all four; what differs is
# the head, and the head is what this prices.


@dataclass(frozen=True)
class HeadNode:
    """One LLM node of an arm's head: its instruction, and what it is shown.

    ``compose`` is the function the graph builds the node's instruction with,
    named rather than re-composed here, so a prompt edit moves this estimate
    the moment it lands. ``shown`` names the rendered artifacts the node's
    placeholders carry, keyed into :func:`shown_tokens`.
    """

    compose: Callable[[MarkdownLoader], str]
    shown: tuple[str, ...]


#: The LLM nodes a head can carry, and what each is given. Keyed by node, so a
#: node that joins an arm is priced by what it reads rather than by where it
#: sits. ``repair`` is absent: it runs only where the validity gate refuses a
#: model, so counting it in every case would price a failure that usually does
#: not happen — a run's repair rate is a measurement, and this is an estimate
#: of the pass that always runs.
HEAD_NODES: Mapping[str, HeadNode] = MappingProxyType(
    {
        "extract": HeadNode(compose_extract_prompt, ("sources",)),
        "facts": HeadNode(compose_facts_prompt, ("sources",)),
        "assert": HeadNode(compose_assert_prompt, ("sources", "model")),
        "reread": HeadNode(compose_reread_prompt, ("sources", "model", "rows")),
    }
)


def shown_tokens(
    case: GoldenCase, reference: SignedReference | None = None
) -> Mapping[str, int]:
    """What each rendered artifact costs a node on one case, in tokens.

    Rendered through the functions the graph itself calls, so an estimate here
    and a real call differ by the transport's own overhead and not by two
    spellings of one artifact.

    ``rows`` is the catalog a reading node would be shown, and on a case the
    run has not happened for, the **signed reference's** catalog stands in for
    it. That is a proxy and it is named as one: a produced catalog is usually
    smaller, so the figure over-states rather than under-states, which is the
    only safe direction for a number somebody consents to spend against.
    """
    rows = 0 if reference is None else estimate_tokens(render_rows(reference.catalog))
    return MappingProxyType(
        {
            "sources": estimate_tokens(render_sources(case.sources)),
            "model": estimate_tokens(render_model(case.model.model_dump(mode="json"))),
            "rows": rows,
        }
    )


@dataclass(frozen=True)
class NodeInput:
    """One node's input on one case: what it is told, and what it is shown."""

    node: str
    instruction: int
    shown: int

    @property
    def tokens(self) -> int:
        return self.instruction + self.shown


@dataclass(frozen=True)
class ArmInput:
    """What one arm's head is given on one case, node by node."""

    arm: str
    case_id: str
    nodes: tuple[NodeInput, ...]

    @property
    def tokens(self) -> int:
        """The whole head's input on this case. Output is not in it."""
        return sum(node.tokens for node in self.nodes)


def head_nodes(arm: str) -> tuple[str, ...]:
    """The LLM nodes of one arm's head, in the order it runs them.

    Read off :attr:`ArmRule.head` and narrowed to :data:`HEAD_NODES`, so the
    code nodes an arm carries — the readings, the resolver, the applicator —
    cost nothing here because they call nobody.
    """
    return tuple(node for node in ARMS[arm].head if node in HEAD_NODES)


def arm_input(
    arm: str,
    case: GoldenCase,
    loader: MarkdownLoader,
    reference: SignedReference | None = None,
) -> ArmInput:
    """What one arm's head is given on one case."""
    shown = shown_tokens(case, reference)
    return ArmInput(
        arm=arm,
        case_id=case.id,
        nodes=tuple(
            NodeInput(
                node=node,
                instruction=estimate_tokens(HEAD_NODES[node].compose(loader)),
                shown=sum(shown[name] for name in HEAD_NODES[node].shown),
            )
            for node in head_nodes(arm)
        ),
    )


def input_report(
    cases: Sequence[GoldenCase],
    loader: MarkdownLoader,
    references: Mapping[str, SignedReference] = MappingProxyType({}),
    baseline: str = "A",
) -> str:
    """What every arm is given over a set of cases, against the baseline's.

    **It prices the input and says so.** A run's cost is this plus what the
    models write, and nothing offline knows the second — so the ratio here
    bounds #1003's fifth gate on one side and settles it on neither. A reader
    taking it for the whole gate has left out the half that is usually larger.
    """
    totals = {
        arm: sum(
            arm_input(arm, case, loader, references.get(case.id)).tokens
            for case in cases
        )
        for arm in ARMS
    }
    floor = totals.get(baseline, 0)
    lines = [
        f"## Input tokens over {len(cases)} cases",
        "",
        (
            "Static instruction plus what each head node is shown, through the"
            " functions the graph renders with. Output is not estimated here,"
            " and the lane agents and critics are excluded because every arm"
            " runs the same ones."
        ),
        "",
        f"| arm | nodes | tokens | against {baseline} |",
        "| --- | --- | --- | --- |",
    ]
    for arm, total in totals.items():
        ratio = f"{total / floor:.2f}x" if floor else "—"
        lines.append(
            f"| {arm} | {', '.join(head_nodes(arm)) or '—'} | {total} | {ratio} |"
        )
    return "\n".join(lines) + "\n"
