"""Rule-vs-label agreement over the recorded fixtures.

**The two error directions are the measurement; the ratio is the door.** A
false split — an equivalent finding the rule calls distinct — costs a reviewer
one unmatched finding. A false merge — two distinct findings the rule calls
one — destroys a finding and inflates recall, and no reviewer sees it happen.
The consequences differ, so the counts are reported apart and never averaged
into one number. :func:`measure_agreement` answers the split direction over the
recorded labels; :func:`measure_merges` answers the merge direction over the
corpus's own distinct claims.

``evals/calibration_labels/pairs.json`` holds candidate pairs marked match /
no-match, and a matcher's agreement with those labels must be **>= 90%**
(comparator: Semgrep's 92-96% on an analogous triage task). The matcher under
this bar is the identity rule from :mod:`evals.harness.identity` — the model
judge this scoreboard was built for is retired, and the scoreboard outlived it:
any future rule version is priced here against the same labels before it ships.

**What the bar is for, and what it is not.** It is the admission gate for a
*candidate* rule: a rule nobody has measured yet has no pinned counts to
regress against, and the bar is the only thing that can price it. Once a rule
ships, its pinned split and merge counts in ``tests/test_evals_identity.py``
are the regression signal, and they bind harder than the ratio — they are
asserted exactly, so a rule cannot rot downwards inside the bar's slack. Read a
passing bar as "this rule is worth measuring properly", never as matcher
accuracy and never as external validation.

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

**A label may decline too.** Two dispositions carry no answer an identity rule
can be graded against, and each sits outside the agreement for the reason a
refusal does — nobody asked the rule a question it could be wrong about. They
are counted under their own keys in ``set_aside``, never folded together.

``unclear`` is the reader declining: two write-ups they could not tell apart
from the sentences alone. No shipped pair carries it. Review sitting 01
returned four and step 5 of ``evals/BLESSING.md`` now states the test that
decides them, so each resolved to a binary label; the disposition exists so the
next undecidable pair is recorded rather than forced.

``unsupported`` is a **different axis, not a softer no-match**. The candidate
names the same place and the same action as the reference and is still not a
match, because it asserts a fact the **System Model** does not hold — a card
that never crosses a link, an MFA control nobody wrote down. Whether a claim is
grounded is not decidable from elements and verbs, so scoring an identity rule
against these measures nothing about identity. ``BLESSING.md`` step 5 asks for
them deliberately and routes them to the downstream "unsupported" bucket; they
sat inside this score only while the rule refused the whole ``no-match`` half
and nobody could see them.

**The exclusion is stated, not inferred.** Each ``unsupported`` fixture records
the reason in its note, ``build_pairs.py`` carries the label, and the count
prints beside every score. The number before the partition is on the record
too: over all 330 readable pairs the rule has **20** false merges and 89.4%
agreement, and 15 of those 20 are these fixtures.

The consequence to state plainly, every time these numbers are quoted: recall
and precision from this suite are **rule-relative**. They are valid for
tracking movement and comparing configurations, and they are not absolutes
comparable to published figures from other tools.

The same fixtures do double duty as the offline unit-test corpus for
:mod:`evals.harness.scorer`, where a scripted matcher replays the recorded
labels and no provider call happens at all.
"""

from __future__ import annotations

import itertools
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, get_args

from analysis_service.frameworks import FrameworkName
from analysis_service.frameworks.stride.record import STRIDE_CATEGORIES, StrideCategory
from evals.harness.identity import ClaimPair, IdentityError, Matcher
from evals.harness.reference import GoldenCase, ReferenceThreat

EVALS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PAIRS_PATH = EVALS_ROOT / "calibration_labels" / "pairs.json"

AGREEMENT_BAR = 0.90

#: What a recorded label may say. Two of the four carry no answer an identity
#: rule can be graded against, and each says why it does not:
#:
#: - ``unclear`` — a reader could not decide the pair from the two sentences
#:   alone, which is what review sitting 01 asked its reader to write.
#: - ``unsupported`` — the candidate names the **same place and the same
#:   action** as the reference and is still not a match, because it asserts a
#:   fact the **System Model** does not hold. That is a groundedness question,
#:   and no comparison of elements and verbs can reach it. ``BLESSING.md`` step
#:   5 asks for these deliberately and sends them to the downstream
#:   "unsupported" bucket; they were scored here only because the rule used to
#:   refuse the whole ``no-match`` half.
Label = Literal["match", "no-match", "unclear", "unsupported"]

#: The labels that carry an answer an identity rule can be graded against. The
#: two dispositions above are absent by construction, which is what keeps them
#: out of the denominator.
SCORED_LABELS: tuple[Label, ...] = ("match", "no-match")


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
    def is_scored(self) -> bool:
        """Does this pair carry an answer a rule can be graded against?"""
        return self.label in SCORED_LABELS

    @property
    def label_match(self) -> bool:
        """What the recorded label says, as a boolean the scorer can compare.

        It raises on an ``unclear`` pair rather than returning ``False``.
        Reading "not a match" out of "nobody could tell" would grade a rule
        against an answer the label declined to give.
        """
        if not self.is_scored:
            raise CalibrationError(
                f"{self.case}: pair {self.candidate_claim!r} is labelled"
                f" {self.label!r}, which carries no answer to compare a rule"
                " against; exclude it with is_scored first"
            )
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
    pairs it declined to read, and ``set_aside`` counts the pairs each
    disposition kept out of the score, keyed by the label that did it. All of
    them sit outside the agreement, because neither the rule nor the label can
    be wrong about a question nobody asked it. Every count is always reported
    beside the bar, so a rule cannot buy accuracy by refusing the hard pairs
    unseen.

    ``set_aside`` is keyed rather than one field per disposition: a new
    disposition is an entry, and it is reported without an edit here.

    ``false_non_matches`` is the **false split** direction and
    ``false_matches`` the **false merge** direction over these labels. Read
    them apart: a split costs a reviewer one unmatched finding, a merge
    destroys a finding silently.
    """

    total: int
    agreements: int
    false_matches: tuple[Disagreement, ...]
    false_non_matches: tuple[Disagreement, ...]
    refused: int = 0
    set_aside: Mapping[Label, int] = field(default_factory=dict)

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
            "set_aside": dict(self.set_aside),
            "false_splits": len(self.false_non_matches),
            "false_merges": len(self.false_matches),
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
        if pair.label not in get_args(Label):
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
    cannot read, so the refusing path stays exercised. A label that carries no
    answer — ``unclear`` or ``unsupported`` — is set aside the same way and
    counted under its own key, because a rule cannot disagree with an answer
    nobody gave it.
    """
    if not pairs:
        raise CalibrationError("no labelled pairs to calibrate against")

    agreements = 0
    refused = 0
    set_aside: Counter[Label] = Counter()
    false_matches: list[Disagreement] = []
    false_non_matches: list[Disagreement] = []
    for pair in pairs:
        if not pair.is_scored:
            set_aside[pair.label] += 1
            continue
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
        total=len(pairs) - refused - sum(set_aside.values()),
        agreements=agreements,
        false_matches=tuple(false_matches),
        false_non_matches=tuple(false_non_matches),
        refused=refused,
        set_aside=dict(set_aside),
    )


@dataclass(frozen=True)
class MergedPair:
    """Two distinct reference claims a matcher rules equal, within one lane."""

    case: str
    lane: str
    left: str
    right: str


@dataclass(frozen=True)
class MergeResult:
    """The false-merge direction, over claims the corpus records as distinct.

    Every within-lane pair of reference claims in one case is a pair the corpus
    already calls two findings. So every merge here is an error, and the count
    needs no label to interpret: ``merges`` over ``within_lane_pairs``.

    This is the direction the recorded labels cannot answer today. Their
    ``no-match`` half carries no candidate element IDs and no verb, so
    :class:`~evals.harness.identity.SubsetVerbIdentity` refuses all of it and
    :func:`measure_agreement` scores the split direction alone. Reference
    claims carry both fields, which is why the merge measurement runs over the
    corpus instead. Reference-vs-reference is a different distribution from the
    candidate-vs-reference pairs a live run emits, and that gap is
    `#511 <https://github.com/mstarks01/work-agent/issues/511>`_.
    """

    within_lane_pairs: int
    merges: tuple[MergedPair, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "within_lane_pairs": self.within_lane_pairs,
            "merges": len(self.merges),
            "merged_pairs": [
                {
                    "case": merged.case,
                    "lane": merged.lane,
                    "left": merged.left,
                    "right": merged.right,
                }
                for merged in self.merges
            ],
        }


def measure_merges(
    matcher: Matcher, corpus: Sequence[GoldenCase], framework: FrameworkName
) -> MergeResult:
    """Price a matcher on the merge direction, over one package's claim sets.

    Only cases whose every claim carries a verb are read. A case part-way
    through verb assignment would otherwise contribute refusals that look like
    separations, and quietly flatter the rule.

    It raises for a package whose reference claims carry no lane, rather than
    inventing one. STRIDE's claim set is open and its lane is a category, so
    two claims in one lane are comparable; a package that keys on a catalog
    requirement reaches no such question in this shape. Giving that package a
    merge measurement it *can* answer is
    `#512 <https://github.com/mstarks01/work-agent/issues/512>`_.
    """
    pairs = 0
    merges: list[MergedPair] = []
    for case in corpus:
        claims = case.references.get(framework, ())
        if not claims:
            continue
        threats = [claim for claim in claims if isinstance(claim, ReferenceThreat)]
        if len(threats) != len(claims):
            raise CalibrationError(
                f"{case.id}: {framework} reference claims carry no lane, so a"
                " within-lane merge count is not defined for this package;"
                " see issue #512"
            )
        if not all(claim.verb for claim in threats):
            continue
        for left, right in itertools.combinations(threats, 2):
            if left.lane != right.lane:
                continue
            pairs += 1
            ruling = matcher.equivalent(
                ClaimPair(
                    case=case.id,
                    category=left.category,
                    reference_claim=left.claim,
                    candidate_claim=right.claim,
                    reference_element_ids=tuple(left.affected_element_ids),
                    candidate_element_ids=tuple(right.affected_element_ids),
                    reference_verb=left.verb,
                    candidate_verb=right.verb,
                )
            )
            if ruling.match:
                merges.append(
                    MergedPair(
                        case=case.id,
                        lane=left.lane,
                        left=left.claim,
                        right=right.claim,
                    )
                )
    if not pairs:
        raise CalibrationError(
            f"no {framework} case carries a complete set of verbs, so the merge"
            " direction cannot be measured"
        )
    return MergeResult(within_lane_pairs=pairs, merges=tuple(merges))
