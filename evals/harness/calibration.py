"""Judge-vs-label agreement over the recorded fixtures.

``evals/judge_calibration/pairs.json`` holds candidate pairs marked match /
no-match, and judge-label agreement must be **>= 90%** (comparator: Semgrep's
92-96% on an analogous triage task). Failing the bar means the judge prompt needs
work, not ship-anyway.

**The labels are agent-authored, and a person has read 30 of the 339.** Review
sitting 01 (``evals/judge_calibration/REVIEW-01.md``, 2026-08-18) took the 30
hardest pairs and relabelled one. So this measures whether the judge reproduces
what an earlier agent wrote down, and not whether either is right; the comparator
above is a figure from a task with full reviewer agreement, and this number is
not comparable to it. ``evals/README.md`` states the provenance once for the
whole directory. Everything below says "the labels" rather than "the human" for
that reason.

**No judge has ever been measured against them.** The bar below is what this
module would enforce; ADR 0003 records that the check has never run.

This is what makes a judge change a real gate rather than a dependency bump:
re-run it on any change to ``evals/config/judge.toml``, because a new judge
silently re-scores history.

The consequence to state plainly, every time these numbers are quoted: recall
and precision from this suite are **judge-relative**. They are valid for
tracking movement and comparing configurations, and they are not absolutes
comparable to published figures from other tools.

The same fixtures do double duty as the offline unit-test corpus for
:mod:`evals.harness.scorer`, where a scripted judge replays the recorded labels
and no provider call happens at all.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from evals.harness.judge import ClaimPair, Judge
from stride_service.frameworks.stride.record import STRIDE_CATEGORIES, StrideCategory

EVALS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PAIRS_PATH = EVALS_ROOT / "judge_calibration" / "pairs.json"

AGREEMENT_BAR = 0.90

Label = Literal["match", "no-match"]


class CalibrationError(ValueError):
    """The calibration fixtures are missing or malformed."""


@dataclass(frozen=True)
class LabelledPair:
    """One recorded pair: the label, and the note that argued for it."""

    case: str
    category: StrideCategory
    reference_claim: str
    candidate_claim: str
    reference_element_ids: tuple[str, ...]
    candidate_element_ids: tuple[str, ...] | None
    #: The action each side names. The reference's comes free from the corpus;
    #: the candidate's is a hand assignment, and is ``None`` on the no-match
    #: half for the same reason its element IDs are.
    reference_verb: str | None
    candidate_verb: str | None
    label: Label
    note: str

    @property
    def label_match(self) -> bool:
        """What the recorded label says, as a boolean the scorer can compare."""
        return self.label == "match"

    def to_claim_pair(self) -> ClaimPair:
        return ClaimPair(
            case=self.case,
            category=self.category,
            reference_claim=self.reference_claim,
            candidate_claim=self.candidate_claim,
            reference_element_ids=self.reference_element_ids,
            candidate_element_ids=self.candidate_element_ids,
            reference_verb=self.reference_verb,
            candidate_verb=self.candidate_verb,
        )


@dataclass(frozen=True)
class Disagreement:
    """One pair the judge and the recorded label read differently.

    Kept whole rather than counted: the bar failing is a prompt-authoring
    task, and a bare percentage says nothing about which distinction the judge
    is missing. False matches inflate recall; false non-matches deflate it.
    """

    pair: LabelledPair
    judge_match: bool
    judge_rationale: str

    def to_json(self) -> dict[str, Any]:
        return {
            "case": self.pair.case,
            "category": self.pair.category,
            "reference_claim": self.pair.reference_claim,
            "candidate_claim": self.pair.candidate_claim,
            "label": self.pair.label,
            "judge": "match" if self.judge_match else "no-match",
            "label_note": self.pair.note,
            "judge_rationale": self.judge_rationale,
        }


@dataclass(frozen=True)
class CalibrationResult:
    """Agreement, the two error directions, and every disagreement.

    ``rulings`` is what this judge answered for every pair, in the order the
    pairs were given. It exists so two judges can be compared *to each other*
    and not only to the labels: agreement against the labels tells you how far
    each judge reproduces them, and nothing about whether the two would reach the same
    conclusion — two judges at 92% can disagree with each other on every one of
    the pairs they each got wrong. It is deliberately absent from
    :meth:`to_json`, where a hundred booleans would bury the disagreements a
    reader is actually there to read.
    """

    total: int
    agreements: int
    false_matches: tuple[Disagreement, ...]
    false_non_matches: tuple[Disagreement, ...]
    rulings: tuple[bool, ...] = ()

    @property
    def agreement(self) -> float:
        return self.agreements / self.total if self.total else 0.0

    @property
    def meets_bar(self) -> bool:
        return self.agreement >= AGREEMENT_BAR

    def to_json(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "agreements": self.agreements,
            "agreement": round(self.agreement, 4),
            "bar": AGREEMENT_BAR,
            "meets_bar": self.meets_bar,
            "false_matches": [entry.to_json() for entry in self.false_matches],
            "false_non_matches": [entry.to_json() for entry in self.false_non_matches],
        }


@dataclass(frozen=True)
class Candidate:
    """One judge in a comparison: what it is, and how it scored."""

    label: str
    result: CalibrationResult

    def to_json(self) -> dict[str, Any]:
        return {"judge": self.label, **self.result.to_json()}


@dataclass(frozen=True)
class JudgeComparison:
    """Several judges measured over the *same* labelled pairs.

    This answers the question a single agreement number cannot: does a
    conclusion depend on which vendor's model was asked? Selecting a production
    judge on measured agreement is the first half of that
    ([#116](https://github.com/mstarks01/work-agent/issues/116)); the second is
    whether the judges that *did* meet the bar would have ruled the same way.

    The objective is explicitly **not** unanimity. Judges are allowed to differ;
    what must not happen is a comparison of two subject models flipping because
    the judge changed vendor. Where agreement between judges is materially lower
    than agreement with the labels, that is the finding, and it is reported as
    uncertainty rather than resolved by picking a favourite.
    """

    candidates: tuple[Candidate, ...]
    pairs: tuple[LabelledPair, ...]

    @property
    def best(self) -> Candidate:
        """The highest-agreement candidate; ties break on the order given.

        A recommendation, never an application. Nothing here rewrites
        ``evals/config/judge.toml`` — changing the judge re-scores history, so
        it stays a reviewed commit that bumps the config version.
        """
        return max(self.candidates, key=lambda candidate: candidate.result.agreement)

    @property
    def meets_bar(self) -> bool:
        """Whether *any* candidate clears the bar.

        The single-judge check asks whether the production judge is defensible.
        A comparison asks whether a defensible judge exists at all — so it fails
        only when none does, which is a statement about the measurement system
        rather than about one model.
        """
        return any(candidate.result.meets_bar for candidate in self.candidates)

    def agreement_between(self, first: str, second: str) -> float:
        """How often two candidates ruled the same way, the labels aside."""
        left = self._rulings(first)
        right = self._rulings(second)
        if not left:
            return 0.0
        matched = sum(1 for a, b in zip(left, right, strict=True) if a == b)
        return matched / len(left)

    def divergences(self) -> tuple[dict[str, Any], ...]:
        """Every pair the candidates did not all rule the same way.

        Kept whole rather than counted, for the reason :class:`Disagreement`
        is: a rate says the judges differ somewhere, and the actionable question
        is *on what kind of claim*, which only the pairs themselves answer.
        """
        divergent = []
        for index, pair in enumerate(self.pairs):
            rulings = {
                candidate.label: candidate.result.rulings[index]
                for candidate in self.candidates
            }
            if len(set(rulings.values())) == 1:
                continue
            divergent.append(
                {
                    "case": pair.case,
                    "category": pair.category,
                    "reference_claim": pair.reference_claim,
                    "candidate_claim": pair.candidate_claim,
                    "label": pair.label,
                    "judges": {
                        label: "match" if match else "no-match"
                        for label, match in rulings.items()
                    },
                }
            )
        return tuple(divergent)

    def to_json(self) -> dict[str, Any]:
        labels = [candidate.label for candidate in self.candidates]
        return {
            "bar": AGREEMENT_BAR,
            "meets_bar": self.meets_bar,
            "best": self.best.label,
            "candidates": [candidate.to_json() for candidate in self.candidates],
            "pairwise_agreement": {
                f"{first} vs {second}": round(self.agreement_between(first, second), 4)
                for index, first in enumerate(labels)
                for second in labels[index + 1 :]
            },
            "divergences": list(self.divergences()),
        }

    def _rulings(self, label: str) -> tuple[bool, ...]:
        for candidate in self.candidates:
            if candidate.label == label:
                return candidate.result.rulings
        raise CalibrationError(f"no candidate labelled {label!r}")


def compare_judges(
    judges: Mapping[str, Judge], pairs: Sequence[LabelledPair]
) -> JudgeComparison:
    """Measure several judges over one set of pairs.

    Every judge sees the identical pairs in the identical order — the whole
    comparison rests on that, since agreement between two candidates is computed
    positionally.
    """
    if not judges:
        raise CalibrationError("no judges to compare")
    candidates = tuple(
        Candidate(label=label, result=measure_agreement(judge, pairs))
        for label, judge in judges.items()
    )
    return JudgeComparison(candidates=candidates, pairs=tuple(pairs))


def load_pairs(path: Path | str = DEFAULT_PAIRS_PATH) -> tuple[LabelledPair, ...]:
    """Load the generated fixtures, failing closed on anything unexpected."""
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CalibrationError(f"{path}: cannot be read: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CalibrationError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(raw, list) or not raw:
        raise CalibrationError(f"{path}: expected a non-empty list of pairs")

    pairs = []
    for index, entry in enumerate(raw):
        try:
            pair = LabelledPair(
                case=entry["case"],
                category=entry["category"],
                reference_claim=entry["reference_claim"],
                candidate_claim=entry["candidate_claim"],
                reference_element_ids=tuple(entry["reference_element_ids"]),
                candidate_element_ids=(
                    None
                    if entry["candidate_element_ids"] is None
                    else tuple(entry["candidate_element_ids"])
                ),
                reference_verb=entry.get("reference_verb"),
                candidate_verb=entry.get("candidate_verb"),
                label=entry["label"],
                note=entry.get("note", ""),
            )
        except (KeyError, TypeError) as exc:
            raise CalibrationError(f"{path}: pair {index} is malformed: {exc}") from exc
        if pair.label not in ("match", "no-match"):
            raise CalibrationError(f"{path}: pair {index} has label {pair.label!r}")
        if pair.category not in STRIDE_CATEGORIES:
            raise CalibrationError(
                f"{path}: pair {index} has category {pair.category!r}"
            )
        pairs.append(pair)
    return tuple(pairs)


def measure_agreement(judge: Judge, pairs: Sequence[LabelledPair]) -> CalibrationResult:
    """Run the judge over every labelled pair and compare with the label."""
    if not pairs:
        raise CalibrationError("no labelled pairs to calibrate against")

    agreements = 0
    rulings: list[bool] = []
    false_matches: list[Disagreement] = []
    false_non_matches: list[Disagreement] = []
    for pair in pairs:
        ruling = judge.equivalent(pair.to_claim_pair())
        rulings.append(ruling.match)
        if ruling.match == pair.label_match:
            agreements += 1
            continue
        disagreement = Disagreement(
            pair=pair, judge_match=ruling.match, judge_rationale=ruling.rationale
        )
        if ruling.match:
            false_matches.append(disagreement)
        else:
            false_non_matches.append(disagreement)

    return CalibrationResult(
        total=len(pairs),
        agreements=agreements,
        false_matches=tuple(false_matches),
        false_non_matches=tuple(false_non_matches),
        rulings=tuple(rulings),
    )
