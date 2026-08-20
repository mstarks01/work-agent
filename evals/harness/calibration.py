"""Rule-vs-label agreement over the recorded fixtures.

``evals/calibration_labels/pairs.json`` holds candidate pairs marked match /
no-match, and a matcher's agreement with those labels must be **>= 90%**
(comparator: Semgrep's 92-96% on an analogous triage task). The matcher under
this bar is the identity rule from :mod:`evals.harness.identity` — the model
judge this scoreboard was built for is retired, and the scoreboard outlived it:
any future rule version is priced here against the same labels before it ships.

**The labels are agent-authored, and a person has read 30 of the 339.** Review
sitting 01 (``evals/calibration_labels/REVIEW-01.md``, 2026-08-18) took the 30
hardest pairs and relabelled one. So agreement measures whether a rule
reproduces what an earlier agent wrote down, and not whether either is right;
the comparator above is a figure from a task with full reviewer agreement, and
this number is not comparable to it. ``evals/README.md`` states the provenance
once for the whole directory. Everything below says "the labels" rather than
"the human" for that reason.

**A rule may refuse a pair, and a refusal is not a miss.** The identity rule
refuses a pair it cannot read — no candidate element IDs, no verb — rather
than grading half of itself. Refused pairs are counted and reported beside the
agreement, never inside it: folding them in either direction would let a rule
buy accuracy by refusing the hard pairs.

The consequence to state plainly, every time these numbers are quoted: recall
and precision from this suite are **rule-relative**. They are valid for
tracking movement and comparing configurations, and they are not absolutes
comparable to published figures from other tools.

The same fixtures do double duty as the offline unit-test corpus for
:mod:`evals.harness.scorer`, where a scripted matcher replays the recorded
labels and no provider call happens at all.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from evals.harness.identity import ClaimPair, IdentityError, Matcher
from stride_service.frameworks.stride.record import STRIDE_CATEGORIES, StrideCategory

EVALS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PAIRS_PATH = EVALS_ROOT / "calibration_labels" / "pairs.json"

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
    """One pair the matcher and the recorded label read differently.

    Kept whole rather than counted: the bar failing is a prompt-authoring
    task, and a bare percentage says nothing about which distinction the rule
    is missing. False matches inflate recall; false non-matches deflate it.
    """

    pair: LabelledPair
    matcher_match: bool
    matcher_rationale: str

    def to_json(self) -> dict[str, Any]:
        return {
            "case": self.pair.case,
            "category": self.pair.category,
            "reference_claim": self.pair.reference_claim,
            "candidate_claim": self.pair.candidate_claim,
            "label": self.pair.label,
            "matcher": "match" if self.matcher_match else "no-match",
            "label_note": self.pair.note,
            "matcher_rationale": self.matcher_rationale,
        }


@dataclass(frozen=True)
class CalibrationResult:
    """Agreement, the two error directions, and every disagreement.

    ``total`` counts the pairs the matcher answered. ``refused`` counts the
    pairs it declined to read; they sit outside the agreement because a rule
    that says "I cannot answer this" is not wrong about it — and the count is
    always reported beside the bar, so a rule cannot buy accuracy by refusing
    the hard pairs unseen.
    """

    total: int
    agreements: int
    false_matches: tuple[Disagreement, ...]
    false_non_matches: tuple[Disagreement, ...]
    refused: int = 0

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
            "refused": self.refused,
            "false_matches": [entry.to_json() for entry in self.false_matches],
            "false_non_matches": [entry.to_json() for entry in self.false_non_matches],
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


def measure_agreement(
    matcher: Matcher, pairs: Sequence[LabelledPair]
) -> CalibrationResult:
    """Run the matcher over every labelled pair and compare with the label.

    A refusal (:class:`~evals.harness.identity.IdentityError`) is counted and
    set aside, never scored: the fixtures deliberately keep pairs the rule
    cannot read, so the refusing path stays exercised.
    """
    if not pairs:
        raise CalibrationError("no labelled pairs to calibrate against")

    agreements = 0
    refused = 0
    false_matches: list[Disagreement] = []
    false_non_matches: list[Disagreement] = []
    for pair in pairs:
        try:
            ruling = matcher.equivalent(pair.to_claim_pair())
        except IdentityError:
            refused += 1
            continue
        if ruling.match == pair.label_match:
            agreements += 1
            continue
        disagreement = Disagreement(
            pair=pair, matcher_match=ruling.match, matcher_rationale=ruling.rationale
        )
        if ruling.match:
            false_matches.append(disagreement)
        else:
            false_non_matches.append(disagreement)

    return CalibrationResult(
        total=len(pairs) - refused,
        agreements=agreements,
        false_matches=tuple(false_matches),
        false_non_matches=tuple(false_non_matches),
        refused=refused,
    )
