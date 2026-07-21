"""Judge-vs-human agreement over the hand-labelled fixtures.

Ticket 009 decision 13: the SME hand-labelled ~100 candidate pairs match /
no-match in the same session that blessed the corpus, and judge-human agreement
must be **>= 90%** (comparator: Semgrep's 92-96% on an analogous triage task).
Failing the bar means the judge prompt needs work, not ship-anyway.

This is what makes a judge change a real gate rather than a dependency bump:
re-run it on any change to ``evals/config/judge.toml``, because a new judge
silently re-scores history.

The consequence to state plainly, every time these numbers are quoted: recall
and precision from this suite are **judge-relative**. They are valid for
tracking movement and comparing configurations, and they are not absolutes
comparable to published figures from other tools.

The same fixtures do double duty as the offline unit-test corpus for
:mod:`evals.harness.scorer` (decision 17), where a scripted judge replays the
recorded labels and no Vertex call happens at all.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from evals.harness.judge import ClaimPair, Judge
from stride_service.report import STRIDE_CATEGORIES, StrideCategory

EVALS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PAIRS_PATH = EVALS_ROOT / "judge_calibration" / "pairs.json"

# Ticket 009 decision 13.
AGREEMENT_BAR = 0.90

Label = Literal["match", "no-match"]


class CalibrationError(ValueError):
    """The calibration fixtures are missing or malformed."""


@dataclass(frozen=True)
class LabelledPair:
    """One hand-labelled pair: the human's answer, and why."""

    case: str
    category: StrideCategory
    reference_claim: str
    candidate_claim: str
    label: Label
    note: str

    @property
    def human_match(self) -> bool:
        return self.label == "match"

    def to_claim_pair(self) -> ClaimPair:
        return ClaimPair(
            case=self.case,
            category=self.category,
            reference_claim=self.reference_claim,
            candidate_claim=self.candidate_claim,
        )


@dataclass(frozen=True)
class Disagreement:
    """One pair the judge and the human read differently.

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
            "human": self.pair.label,
            "judge": "match" if self.judge_match else "no-match",
            "human_note": self.pair.note,
            "judge_rationale": self.judge_rationale,
        }


@dataclass(frozen=True)
class CalibrationResult:
    """Agreement, the two error directions, and every disagreement."""

    total: int
    agreements: int
    false_matches: tuple[Disagreement, ...]
    false_non_matches: tuple[Disagreement, ...]

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
            "false_non_matches": [
                entry.to_json() for entry in self.false_non_matches
            ],
        }


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


def measure_agreement(
    judge: Judge, pairs: Sequence[LabelledPair]
) -> CalibrationResult:
    """Run the judge over every labelled pair and compare with the human."""
    if not pairs:
        raise CalibrationError("no labelled pairs to calibrate against")

    agreements = 0
    false_matches: list[Disagreement] = []
    false_non_matches: list[Disagreement] = []
    for pair in pairs:
        ruling = judge.equivalent(pair.to_claim_pair())
        if ruling.match == pair.human_match:
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
    )
