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

**What a promotion needs** (the owner's decision of 2026-09-23): the two
behavioural guarantees (``critical-fixtures``), reviewed semantic accuracy
over a frozen population (``support-shares``), required-fact recall, and the
report-quality gates. The report's disclosure of its review state is required
as well and is not a substitute for any of them. None of this needs a truth
detector: each guarantee is about what the job does with a row's review
state, not about whether the row is true.

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

from pydantic import ValidationError

from analysis_service.assertions import AssertionRecord
from analysis_service.sources import text_digest
from evals.harness import falsify
from evals.harness.arms import ArmRun
from evals.harness.artifact import load_artifact
from evals.harness.bundle import assertions_from_reports
from evals.harness.modes import AssertionResult
from evals.harness.reference import GoldenCase, load_corpus
from evals.harness.replay import replay_assertions, signed_reference

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
        "required_fact_recall": 0.561,
    }
)

#: The assertion-mode runs ``required_fact_recall`` is read from, one pair of
#: files per repeat: case 01 in the first, the four holdout cases in the
#: second. Tracked under ``evals/emissions/``, so the figure is recomputed by
#: ``tests/test_evals_promotion.py`` rather than trusted. Declared 2026-09-24,
#: before any treatment run is read against it, and re-read the same day over
#: the reference that rules a tier read off contents ``inferred``: the rule
#: (baseline less one spread) did not move, the denominator did.
RECALL_RUNS: tuple[tuple[str, str], ...] = tuple(
    (
        f"evals/emissions/20260916T-assert-step6/luna-after-r{repeat}.json",
        f"evals/emissions/20260916T-assert-holdouts/luna-after-r{repeat}.json",
    )
    for repeat in range(1, 6)
)

#: The cases :data:`RECALL_RUNS` covers. A treatment repeat is read over
#: exactly these, because recall ranges from 0.24 to 1.0 by case.
RECALL_CASES: frozenset[str] = frozenset(
    {
        "01-payments-checkout",
        "04-ml-inference-service",
        "09-cookbook-sokify-retail",
        "11-sparse-shift-scheduling",
        "13-dispatch-control-plane",
    }
)

#: The model the ``assert`` node requested in every baseline run. Recall
#: depends on the model — on case 01, 0.52 on this one and 0.31 on another —
#: so a treatment on any other model is not read against this baseline.
RECALL_MODEL = "openrouter/openai/gpt-5.6-luna"

#: The standard deviation of the pooled recall over the five baseline repeats
#: (21 to 26 of 41 required facts). Declared from the repeats rather than
#: passed in, because the repeats exist.
RECALL_SPREAD = 0.046

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
#: **What these gates cannot yet decide** is :data:`PENDING`: the review
#: population, semantic support and case diversity. Each is read ``inconclusive`` until it has a measurement,
#: so no run promotes on the gates above alone. The ``sd`` gates also compare
#: one treatment run against this one baseline run, scaled by a spread, rather
#: than repeated arms against each other.
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


def _unread_here(artifact: Mapping[str, Any], report: Mapping[str, Any]) -> None:
    """A gate whose figure one treatment report cannot give (see :func:`read_recall`)."""
    return


def _backed(artifact: Mapping[str, Any], report: Mapping[str, Any]) -> float | None:
    backed = (artifact.get("losses_aggregate") or {}).get("assertion_backed")
    return None if backed is None else backed["matched"]


class UnreadableRecord(ValueError):
    """A report whose assertion record this tree's schema refuses."""


def _record(report: Mapping[str, Any]) -> AssertionRecord | None:
    held = report.get("assertions")
    if not held:
        return None
    try:
        return AssertionRecord.model_validate(held)
    except ValidationError as error:
        raise UnreadableRecord(
            "the report's assertion record does not validate under this tree's"
            f" schema ({error.error_count()} errors), which is not a zero"
        ) from error


def _measure(
    read: Callable[[Mapping[str, Any], Mapping[str, Any]], float | None],
    artifact: Mapping[str, Any],
    report: Mapping[str, Any],
) -> tuple[float | None, str]:
    """What ``read`` measured, or ``None`` and why the report could not be read."""
    try:
        return read(artifact, report), ""
    except UnreadableRecord as error:
        return None, str(error)


def _refused_share(
    artifact: Mapping[str, Any], report: Mapping[str, Any]
) -> float | None:
    """Rows the gate refused over rows proposed: a **structural** refusal rate.

    Distinct rows, through :meth:`~AssertionRecord.refused_rows`, never a count
    of issues: one row draws several reasons, and a graph contradiction refuses
    nothing. Before #926's audit this divided the issue count and called it the
    unsupported-assertion rate, which it is not — a row can quote the source
    exactly and state something the quote does not say, and the gate passes
    it. :func:`support_shares` reports that question and :data:`PENDING`
    holds its gate.
    """
    record = _record(report)
    if record is None or not record.proposed:
        return None
    return record.refused_rows() / record.proposed


#: The four states a row's support can be in, reported together. ``unreviewed``
#: is ``unchecked``: nobody has looked. Reported as one set because any one of
#: them read alone can hide the others — a low unsupported share over a sample
#: that is mostly ``unresolved`` or ``unreviewed`` measures nothing.
SUPPORT_STATES: tuple[str, ...] = (
    "supported",
    "unsupported",
    "unresolved",
    "unreviewed",
)


def support_shares(report: Mapping[str, Any]) -> Mapping[str, float] | None:
    """Each support state's share of the report's catalog rows, or ``None``.

    **Semantic support, reported and not gated.** A located quote is not
    support: the gate that read span validity as support passed a flipped MFA
    answer, a webhook carrying a receipt store's credential and a mechanism
    spelled ``the of and a``. Every row is ``unreviewed`` until a reviewer
    writes an assessment (ADR 0034), and no threshold gates these shares: the
    owner reads them on each reviewed run — see :data:`PENDING`'s
    ``support-shares``.

    Read over every row the record kept, until ``review-population`` freezes
    the rows it should be read over.
    """
    record = _record(report)
    if record is None or not record.catalog.entries:
        return None
    rows = record.catalog.entries
    state = {"unchecked": "unreviewed"}
    counts = dict.fromkeys(SUPPORT_STATES, 0)
    for entry in rows:
        counts[state.get(entry.assessment, entry.assessment)] += 1
    return MappingProxyType({key: count / len(rows) for key, count in counts.items()})


#: Where the critical fixtures read their cases and signed references.
CORPUS = Path(__file__).resolve().parents[1] / "corpus"


def _critical_failures(
    artifact: Mapping[str, Any], report: Mapping[str, Any]
) -> float | None:
    """Critical probes that break the guarantee of their review state.

    **A property of the code, not of the run**, so it reads neither argument.
    Each of #925's corruptions is driven through analysis preparation
    (:func:`~evals.harness.falsify.consumed`) twice. Unreviewed, it fails if a
    corrupted row silences a lead; reviewed, if a reviewer-rejected row still
    reaches analysis at all. ``tests/test_release_blockers.py`` holds the same
    two guarantees through ``prepare_analysis`` as release blockers.
    """
    found = falsify.consumed(CORPUS)
    return len(falsify.critical_failures(found, reviewed=False)) + len(
        falsify.critical_failures(found, reviewed=True)
    )


def _case_of(report: Mapping[str, Any]) -> GoldenCase | None:
    """The corpus case a report was run on, by its sources' digests.

    A report carries no case ID, and it does carry the sha256 of every source
    it read, which names the case exactly and cannot drift from the text the
    way a label or a system name can.
    """
    wanted = {source["sha256"] for source in report.get("input", {}).get("sources", [])}
    if not wanted:
        return None
    for case in load_corpus(CORPUS):
        if {text_digest(source.text) for source in case.sources} == wanted:
            return case
    return None


def _required_fact_recall(
    artifact: Mapping[str, Any], report: Mapping[str, Any]
) -> float | None:
    """Required-fact recall of the catalog this report embeds.

    Read off the report rather than written into the artifact: the catalog is
    already on the report, and an artifact key would move the artifact version
    and re-seal every Baseline for a figure the report can answer. Graded by
    the signed reference's own matcher through
    :func:`~evals.harness.replay.replay_assertions` and counted by
    :class:`~evals.harness.arms.ArmRun`, the one reader of the endpoint.
    ``None`` for a job that ran no assertion pass or a case nobody signed.
    """
    record = _record(report)
    case = _case_of(report)
    if record is None or case is None:
        return None
    reference = signed_reference(CORPUS, case)
    if reference is None:
        return None
    graded = replay_assertions(case, reference, AssertionResult(case.id, {}, record))
    return ArmRun.of(graded, reference, arm="treatment").recall


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
                " and 3.31x wall, and no artifact records the wall time."
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
        "critical-fixtures": Gate(
            question=(
                "does an unchecked row silence a lead, or a reviewer-rejected"
                " row reach analysis?"
            ),
            read=_critical_failures,
            unit="count",
            limit=0,
            direction="at-most",
            why=(
                "the owner's decision of 2026-09-23, option (iii): zero failures"
                " on the critical fixtures under each guarantee. The promise is"
                " that no reviewer-rejected fact reaches analysis, not that no"
                " wrong fact does: a review can miss one"
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
        "required-fact-recall": Gate(
            question="does the layer still find the facts the sources state?",
            read=_unread_here,
            unit="sd",
            limit=-1.0,
            direction="at-least",
            against="required_fact_recall",
            why=(
                "non-regression on the rule the coverage gates use. Read in"
                " assertion mode on the baseline's five cases and model"
                " (`run.py gates --recall`), because a report resolves its"
                " catalog against its own extracted graph and would charge"
                " binding losses to the layer. Precision alone can be met by"
                " emitting few facts, so support never promotes without this"
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
class Pending:
    """A gate the promotion needs and that has no declared limit yet.

    Written down now so the design is fixed before the evidence arrives, and
    read as ``inconclusive`` by :func:`decide`, so a run cannot promote while
    one is open. ``limit`` is the rule where one is already justified and
    ``""`` where none is; ``unset`` says what has to exist before a number can
    be chosen, so the number is chosen before the measurement rather than
    after it.
    """

    question: str
    measures: str
    limit: str
    unset: str


#: The measurement design #926 settled on 2026-09-23 (option (c) of the
#: support-gate decision, with the owner's corrections), as gates nobody can
#: read yet. A name here is never also in :data:`GATES`.
PENDING: Mapping[str, Pending] = MappingProxyType(
    {
        "review-population": Pending(
            question="which rows is semantic support measured over?",
            measures=(
                "every row that would have reached a consumer — projected into"
                " the graph or offered as evidence — plus every row the gate"
                " refused, per case, recorded with the catalog it came from"
                " before any reviewer looks"
            ),
            limit="frozen before the first assessment",
            unset=(
                "`run.py freeze-population` records one and refuses a catalog"
                " already under review. The gates read reports, not a frozen"
                " file, so a person confirms which population a review covered."
                " Choosing the settled rows after review could drop exactly the"
                " unsupported rows the measure has to count"
            ),
        ),
        "support-shares": Pending(
            question=(
                "how accurate are the facts analysis rested on, by review?"
                " (reviewed semantic accuracy)"
            ),
            measures=(
                "supported, unsupported, unresolved and unreviewed shares of the"
                " frozen population, reported together (`support_shares`)"
            ),
            limit="none: the owner reads the four shares on each reviewed run",
            unset=(
                "a review of the frozen population. Neither a 10% unsupported"
                " nor a 20% unresolved ceiling had an acceptance rationale, and"
                " on 2026-09-24, before the first assessment, the owner declared"
                " no threshold. So this gate never passes by itself: the owner"
                " reads the shares and decides whether the run is good enough"
            ),
        ),
        "case-diversity": Pending(
            question="is the evidence about systems, or about one system repeated?",
            measures=(
                "distinct cases, and held-out sources through #744, behind every"
                " figure above; repeats of one case measure run-to-run spread"
                " and are never counted as further systems"
            ),
            limit="",
            unset=(
                "one case carries every run so far. Rows from one case and"
                " repeats of it are correlated, so a sample-size rule such as a"
                " Wilson bound over-counts them"
            ),
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


def pooled_recall(paths: Sequence[Path]) -> float:
    """Required-fact recall over one repeat's assertion-mode files, pooled by row.

    Found rows over required rows across :data:`RECALL_CASES`, each case
    graded by its signed reference's matcher through
    :class:`~evals.harness.arms.ArmRun`, the one reader of the endpoint.
    Pooled by row rather than averaged by case, so case 11's three rows weigh
    three and case 01's fifteen weigh fifteen.
    """
    cases = [case for case in load_corpus(CORPUS) if case.id in RECALL_CASES]
    found = required = 0
    for path in paths:
        held = [case for case in cases if case.id in load_artifact(path).cases]
        for case_id, result in assertions_from_reports(path, held).items():
            case = next(case for case in held if case.id == case_id)
            reference = signed_reference(CORPUS, case)
            if reference is None:
                raise ValueError(f"{case_id}: no signed reference to grade against")
            run = ArmRun.of(
                replay_assertions(case, reference, result), reference, arm="treatment"
            )
            found += run.recovered
            required += run.required
    if not required:
        raise ValueError("no required row in these files")
    return found / required


def read_recall(repeats: Sequence[Sequence[Path]]) -> Reading:
    """``required-fact-recall`` over a treatment's repeats, or why it is unread.

    Each repeat is the files that together cover :data:`RECALL_CASES`, run on
    :data:`RECALL_MODEL`. The figure is the mean pooled recall, compared with
    the baseline in units of :data:`RECALL_SPREAD`, which is how the baseline
    itself was measured.
    """
    name = "required-fact-recall"
    gate = GATES[name]
    baseline = BASELINE[gate.against]

    def unread(why: str) -> Reading:
        return Reading(name, "inconclusive", None, baseline, None, why)

    if not repeats:
        return unread("no assertion-mode repeat was given (`--recall`)")
    for paths in repeats:
        covered = frozenset().union(*(load_artifact(path).cases for path in paths))
        if not RECALL_CASES <= covered:
            missing = ", ".join(sorted(RECALL_CASES - covered))
            return unread(f"a repeat does not cover the baseline's cases ({missing})")
        models = frozenset().union(*(_assert_models(path) for path in paths))
        if models != {RECALL_MODEL}:
            return unread(
                f"a repeat ran {', '.join(sorted(models)) or 'no assert node'},"
                f" and the baseline ran {RECALL_MODEL}"
            )
    measured = sum(pooled_recall(paths) for paths in repeats) / len(repeats)
    value = _in_spreads(measured - baseline, RECALL_SPREAD)
    passed = value >= gate.limit
    return Reading(name, "pass" if passed else "fail", measured, baseline, value)


def _assert_models(path: Path) -> frozenset[str]:
    """The models the ``assert`` node requested in one sweep, from its provenance."""
    held = json.loads(path.read_text(encoding="utf-8"))
    runs = held.get("provenance", {}).get("node_runs", {}).get("assert", [])
    return frozenset(run["requested_model"] for run in runs)


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
    measured, unreadable = _measure(gate.read, artifact, report)
    baseline = BASELINE[gate.against] if gate.against else None
    if measured is None:
        return Reading(
            name,
            "inconclusive",
            None,
            baseline,
            None,
            unreadable or "the artifact carries no such figure, which is not a zero",
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
    recall: Sequence[Sequence[Path]] = (),
) -> tuple[Reading, ...]:
    """Every gate, read, then every :data:`PENDING` gate as unread.

    ``spread`` maps a gate name to its repeat spread. ``recall`` is the
    treatment's assertion-mode repeats, which ``required-fact-recall`` reads
    in place of the report (:func:`read_recall`).
    """
    return (
        *(
            read_recall(recall)
            if name == "required-fact-recall"
            else read_gate(name, artifact, report, spread=spread.get(name))
            for name in GATES
        ),
        *(
            Reading(name, "inconclusive", None, None, None, pending.unset)
            for name, pending in PENDING.items()
        ),
    )


def render(readings: Sequence[Reading]) -> str:
    """The gate table, as text. It reports no verdict of its own."""
    lines = [
        "## The predeclared promotion gates, read",
        "",
        (
            f"Budgets set from the baseline arm of {BASELINE_RUN}. Required-fact"
            f" recall from {len(RECALL_RUNS)} assertion-mode repeats in"
            f" {', '.join(sorted({str(Path(path).parent) for pair in RECALL_RUNS for path in pair}))}."
            " A gate reading `inconclusive` is one nobody can read yet, and a"
            " run with an unread gate is not a promotion."
        ),
        "",
        "| gate | verdict | measured | baseline | against limit | limit |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for row in readings:
        baseline = "—" if row.baseline is None else f"{row.baseline:.4g}"
        against = "—" if row.against_limit is None else f"{row.against_limit:.3f}"
        measured = "—" if row.measured is None else f"{row.measured:.4g}"
        gate = GATES.get(row.gate)
        limit = (
            f"{gate.direction} {gate.limit} ({gate.unit})"
            if gate is not None
            else PENDING[row.gate].limit or "undeclared"
        )
        lines.append(
            f"| `{row.gate}` | {row.verdict} | {measured} | {baseline}"
            f" | {against} | {limit} |"
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
    parser.add_argument(
        "--recall",
        type=Path,
        nargs="+",
        action="append",
        default=[],
        help="one treatment repeat's assertion-mode sweep files, covering the"
        " baseline's five cases; give it once per repeat",
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
    try:
        readings = decide(artifact, report, spread=spread, recall=args.recall)
    except (OSError, ValueError) as error:
        print(f"cannot read the recall repeats: {error}", file=sys.stderr)
        return 1
    print(render(readings), end="")
    measured, unreadable = _measure(_required_fact_recall, artifact, report)
    if measured is not None:
        print(
            "\nRequired-fact recall of this report's own catalog, end to end,"
            f" reported and not gated: {measured:.3f}"
        )
    elif unreadable:
        print(f"\n{unreadable}")
    shares = support_shares(report)
    if shares is not None:
        print(
            "\nSupport over every kept row, reported and not gated: "
            + ", ".join(f"{state} {shares[state]:.0%}" for state in SUPPORT_STATES)
        )
    return 0 if all(row.verdict == "pass" for row in readings) else 1
