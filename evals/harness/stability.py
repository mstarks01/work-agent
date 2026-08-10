"""Run-to-run stability: how much of a sweep's recall survives re-running it.

Every other number in this harness comes from one sweep, and a single sweep
cannot tell a real movement from sampling noise. Two sweeps of the same corpus
under the same generation identities can: the references that matched in both
are what the system reliably finds, and the ones that matched in one are the
spread that any comparison of two other numbers has to clear before it means
anything.

The instrument is the **reference index**, not the threat text. A produced
threat gets a fresh ID every run and its wording moves, so text can only be
compared through the judge; the reference it was matched to is a corpus
coordinate that means the same thing in every sweep. So this module measures
set overlap over ``scores[].matched[].reference_index`` and needs no judge, no
provider and no credentials — it reads finished artifacts, the way ``promote``
does.

**What it cannot see.** Two runs agreeing on a reference is not two runs
producing the same threat: the same reference can be matched by threats that
differ in severity, elements and wording. Stability here is stability *of
recall*, which is the number the corpus grades and the one worth defending.

Non-gating, like the rest of the instruments. The spread this reports is the
input to any future threshold, not a threshold itself.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.harness.provenance import EvalArtifact, ProvenanceError, load_artifact
from evals.harness.scorer import ratio


@dataclass(frozen=True)
class ScoredRun:
    """One finished sweep, reduced to what a stability comparison reads."""

    label: str
    mode: str
    models: dict[str, Any]
    matched: dict[str, frozenset[int]]
    references: dict[str, int]
    recall: dict[str, float]

    @property
    def cases(self) -> frozenset[str]:
        return frozenset(self.matched)


@dataclass(frozen=True)
class CaseStability:
    """One case's spread across the runs that scored it.

    ``always`` / ``sometimes`` / ``never`` partition the case's references, so
    they sum to ``references`` and the middle bucket is the whole finding:
    ``never`` is a coverage gap to work on and ``always`` is settled, while
    ``sometimes`` is the band in which a one-sweep recall number can move
    without anything having changed.
    """

    case_id: str
    runs: int
    references: int
    recalls: tuple[float, ...]
    always: int
    sometimes: int
    never: int
    mean_jaccard: float

    @property
    def recall_spread(self) -> float:
        return max(self.recalls) - min(self.recalls)

    @property
    def volatile_rate(self) -> float:
        return ratio(self.sometimes, self.references)

    def to_json(self) -> dict[str, Any]:
        return {
            "case": self.case_id,
            "runs": self.runs,
            "references": self.references,
            "recalls": [round(recall, 3) for recall in self.recalls],
            "recall_spread": round(self.recall_spread, 3),
            "always_matched": self.always,
            "sometimes_matched": self.sometimes,
            "never_matched": self.never,
            "volatile_rate": round(self.volatile_rate, 3),
            "mean_jaccard": round(self.mean_jaccard, 3),
        }


def read_run(artifact: EvalArtifact) -> ScoredRun:
    """Reduce a loaded artifact to its per-case matched-reference sets.

    A sweep run with ``--no-scoring`` carries no ``scores`` block and is
    refused here rather than reported as a run that matched nothing: an empty
    overlap and an unscored sweep are opposite facts about the system.
    """
    scores = artifact.raw.get("scores")
    if not isinstance(scores, list) or not scores:
        raise ProvenanceError(
            f"{artifact.path}: no scores block, so this sweep measured no recall"
            " to compare — re-run it without --no-scoring"
        )
    matched: dict[str, frozenset[int]] = {}
    references: dict[str, int] = {}
    recall: dict[str, float] = {}
    for score in scores:
        # Refused by name rather than raised through: an artifact whose score
        # blocks are the wrong shape is a file to re-produce, and a KeyError
        # out of a comparison reads as a defect in the comparison.
        try:
            case_id = str(score["case"])
            matched[case_id] = frozenset(
                int(pair["reference_index"]) for pair in score["matched"]
            )
            references[case_id] = int(score["counts"]["references"])
            recall[case_id] = float(score["metrics"]["recall"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProvenanceError(
                f"{artifact.path}: malformed score block: {exc}"
            ) from exc
    return ScoredRun(
        label=artifact.path.name,
        mode=artifact.mode,
        models=dict(artifact.raw.get("models", {})),
        matched=matched,
        references=references,
        recall=recall,
    )


def load_runs(paths: Iterable[Path | str]) -> list[ScoredRun]:
    """Load every artifact named, through the loader ``promote`` uses."""
    return [read_run(load_artifact(path)) for path in paths]


def _mean_jaccard(sets: Sequence[frozenset[int]]) -> float:
    """Mean pairwise Jaccard, with the empty-vs-empty pair scored 1.0.

    Two runs that both found nothing agree completely, and calling that 0.0
    would report the most stable outcome there is as the least stable one.
    """
    pairs = list(itertools.combinations(sets, 2))
    overlaps = [
        1.0 if not (left | right) else len(left & right) / len(left | right)
        for left, right in pairs
    ]
    return ratio(sum(overlaps), len(overlaps))


def compare_runs(runs: Sequence[ScoredRun]) -> list[CaseStability]:
    """Per-case stability over the cases **every** run scored.

    Restricted to the intersection on purpose: a case one sweep skipped has no
    second measurement, and folding its single run in as if it were stable
    would flatter exactly the number this exists to expose.
    """
    if len(runs) < 2:
        raise ValueError("stability needs at least two scored runs")
    shared = frozenset.intersection(*(run.cases for run in runs))
    if not shared:
        raise ValueError("the runs share no scored case, so nothing is comparable")

    stability = []
    for case_id in sorted(shared):
        sets = [run.matched[case_id] for run in runs]
        references = runs[0].references[case_id]
        always = len(frozenset.intersection(*sets))
        ever = len(frozenset.union(*sets))
        stability.append(
            CaseStability(
                case_id=case_id,
                runs=len(runs),
                references=references,
                recalls=tuple(run.recall[case_id] for run in runs),
                always=always,
                sometimes=ever - always,
                never=references - ever,
                mean_jaccard=_mean_jaccard(sets),
            )
        )
    return stability


def aggregate_stability(stability: Sequence[CaseStability]) -> dict[str, Any]:
    """The corpus-wide view, pooled over references rather than over cases."""
    references = sum(entry.references for entry in stability)
    always = sum(entry.always for entry in stability)
    sometimes = sum(entry.sometimes for entry in stability)
    return {
        "cases": len(stability),
        "runs": max((entry.runs for entry in stability), default=0),
        "references": references,
        "always_matched": always,
        "sometimes_matched": sometimes,
        "never_matched": sum(entry.never for entry in stability),
        "always_rate": round(ratio(always, references), 3),
        "volatile_rate": round(ratio(sometimes, references), 3),
        "mean_jaccard": round(
            ratio(sum(entry.mean_jaccard for entry in stability), len(stability)), 3
        ),
        "worst_case_recall_spread": round(
            max((entry.recall_spread for entry in stability), default=0.0), 3
        ),
    }


def comparability_warnings(runs: Sequence[ScoredRun]) -> list[str]:
    """What makes these runs not a clean repeat of each other.

    Reported rather than refused, on the same principle
    :class:`~evals.harness.provenance.EvalArtifact` applies to a sweep with
    failures: comparing two modes or two judges is sometimes exactly the
    question being asked, and the tool's job is to make sure it is a chosen
    comparison rather than an accidental one. Anything here means the spread
    below includes a configuration difference, not just sampling noise.
    """
    warnings = []
    modes = {run.mode for run in runs}
    if len(modes) > 1:
        warnings.append(f"runs are of different modes: {', '.join(sorted(modes))}")
    warnings += _model_warnings(runs)
    cases = [run.cases for run in runs]
    unshared = frozenset.union(*cases) - frozenset.intersection(*cases)
    if unshared:
        warnings.append(
            "cases scored by only some runs, excluded from the comparison:"
            f" {', '.join(sorted(unshared))}"
        )
    return warnings


def _model_warnings(runs: Sequence[ScoredRun]) -> list[str]:
    """Every ``models`` field the runs disagree on, named individually.

    Field by field because the two disagreements mean different things: a
    changed generator is the thing under test, and a changed judge silently
    re-measures every case that did not change at all.
    """
    fields = sorted({field for run in runs for field in run.models})
    return [
        f"runs disagree on {field}: {', '.join(sorted(values))}"
        for field, values in ((field, _values(runs, field)) for field in fields)
        if len(values) > 1
    ]


def _values(runs: Sequence[ScoredRun], field: str) -> set[str]:
    def rendered(value: Any) -> str:
        if isinstance(value, Mapping):
            return ", ".join(f"{key}={value[key]}" for key in sorted(value))
        return str(value)

    return {rendered(run.models.get(field)) for run in runs}
