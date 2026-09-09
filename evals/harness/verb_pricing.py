"""Price a verb equivalence before it ships: what it merges, and what it buys.

The identity rule separates two claims at one place by their action verb, and
:data:`~evals.harness.verbs.EQUIVALENT` says which verbs name one action. A
candidate entry there is a decision with a price on three axes and a gain on
one, and every number is offline:

* **false splits** over the labelled pairs a person called a match, which a
  merge can only lower;
* **false merges** over the labelled pairs a person called two findings;
* **reference merges** over the within-lane pairs of the corpus itself, which
  records both sides as distinct, so every one is an error;
* **the gain**: a finished sweep's reports re-scored under the candidate, as
  matched references and must-finds, against the shipped rule.

This is the frontier ``tests/test_evals_identity.py`` pins for the shipped
rule, opened to a candidate and given a gain column. It exists because on
2026-09-09 an exemplar edit went to a paid sweep with no such number, and the
same afternoon a script in a scratch directory priced three candidates in
seconds. A measurement nobody can run is not a measurement (#730).

Nothing here adopts anything. A candidate that prices well is still a decision
for the maintainer, because a reference merge is a pair the corpus says are
two findings, and the corpus is the standard.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from evals.harness.calibration import LabelledPair
from evals.harness.identity import FlowMap, SubsetVerbIdentity, endpoint_form
from evals.harness.ledger import Ledger
from evals.harness.reference import GoldenCase, ReferenceThreat
from evals.harness.scorer import score_case
from evals.harness.verbs import check_verb, same_action

Groups = tuple[frozenset[str], ...]


@dataclass(frozen=True)
class Merge:
    """One pair a candidate would call one finding that the shipped rule does not."""

    case: str
    lane: str
    left_verb: str
    right_verb: str
    left: str
    right: str

    def to_json(self) -> dict[str, Any]:
        return {
            "case": self.case,
            "lane": self.lane,
            "left_verb": self.left_verb,
            "right_verb": self.right_verb,
            "left": self.left,
            "right": self.right,
        }


@dataclass(frozen=True)
class VerbPrice:
    """A candidate equivalence on the three error axes, and its gain."""

    groups: Groups
    false_splits: int
    false_merges: int
    reference_merges: int
    #: What the sweep's reports match under this table. ``None`` without a sweep.
    matched: int | None = None
    must_find: int | None = None
    #: The merges the candidate adds over the shipped rule, named, because a
    #: count of wrong merges is a number a person cannot act on.
    new_false_merges: tuple[Merge, ...] = field(default_factory=tuple)
    new_reference_merges: tuple[Merge, ...] = field(default_factory=tuple)

    def to_json(self) -> dict[str, Any]:
        return {
            "groups": [sorted(group) for group in self.groups],
            "false_splits": self.false_splits,
            "false_merges": self.false_merges,
            "reference_merges": self.reference_merges,
            "matched": self.matched,
            "must_find": self.must_find,
            "new_false_merges": [merge.to_json() for merge in self.new_false_merges],
            "new_reference_merges": [
                merge.to_json() for merge in self.new_reference_merges
            ],
        }


def parse_groups(spellings: Sequence[str]) -> Groups:
    """``"forge=inject=plant"`` to a group; every member must be a verb the vocabulary carries."""
    groups = []
    for spelling in spellings:
        members = frozenset(part.strip() for part in spelling.split("="))
        if len(members) < 2:
            raise ValueError(f"{spelling!r}: an equivalence names at least two verbs")
        for verb in members:
            check_verb(verb)
        groups.append(members)
    return tuple(groups)


def _rules_one(
    case: str,
    left_ids: Sequence[str],
    right_ids: Sequence[str],
    left_verb: str,
    right_verb: str,
    flows: FlowMap,
    groups: Groups | None,
) -> bool:
    """The shipped rule's shape, with the equivalence table given rather than read."""
    left = endpoint_form(left_ids, flows)
    right = endpoint_form(right_ids, flows)
    if not left or not right:
        return False
    return (left <= right or right <= left) and same_action(
        left_verb, right_verb, groups
    )


def _labelled(
    pairs: Sequence[LabelledPair],
    flows_by_case: Mapping[str, FlowMap],
    groups: Groups | None,
) -> tuple[int, list[Merge]]:
    splits = 0
    merges: list[Merge] = []
    for pair in pairs:
        if not pair.is_scored or pair.candidate_element_ids is None:
            continue
        if pair.reference_verb is None or pair.candidate_verb is None:
            continue
        ruled = _rules_one(
            pair.case,
            pair.reference_element_ids,
            pair.candidate_element_ids,
            pair.reference_verb,
            pair.candidate_verb,
            flows_by_case.get(pair.case, {}),
            groups,
        )
        if pair.label_match and not ruled:
            splits += 1
        elif ruled and not pair.label_match:
            merges.append(
                Merge(
                    pair.case,
                    pair.category,
                    pair.reference_verb,
                    pair.candidate_verb,
                    pair.reference_claim,
                    pair.candidate_claim,
                )
            )
    return splits, merges


def _references(
    corpus: Sequence[GoldenCase],
    flows_by_case: Mapping[str, FlowMap],
    groups: Groups | None,
) -> list[Merge]:
    merges: list[Merge] = []
    for case in corpus:
        claims = [
            claim
            for claim in case.references.get("stride", ())
            if isinstance(claim, ReferenceThreat)
        ]
        flows = flows_by_case.get(case.meta.id, {})
        for left, right in itertools.combinations(claims, 2):
            if (
                left.category != right.category
                or left.verb is None
                or right.verb is None
            ):
                continue
            if _rules_one(
                case.meta.id,
                left.affected_element_ids,
                right.affected_element_ids,
                left.verb,
                right.verb,
                flows,
                groups,
            ):
                merges.append(
                    Merge(
                        case.meta.id,
                        left.category,
                        left.verb,
                        right.verb,
                        left.claim,
                        right.claim,
                    )
                )
    return merges


def _gain(
    corpus: Sequence[GoldenCase],
    produced: Mapping[str, Sequence[Any]],
    flows_by_case: Mapping[str, FlowMap],
    groups: Groups | None,
) -> tuple[int, int]:
    """Matched references and must-finds over the sweep's reports, under ``groups``.

    An empty ledger, on purpose: a standing decides what an unmatched draft
    is, never whether a reference matched, so the gain reads the same on a
    cold ledger and a full one.
    """
    matcher = SubsetVerbIdentity(flows_by_case, groups)
    matched = must_find = 0
    for case in corpus:
        if case.id not in produced:
            continue
        score = score_case(case, produced[case.id], matcher, Ledger())
        matched += len(score.matched)
        must_find += sum(1 for pair in score.matched if pair.tier == "must-find")
    return matched, must_find


def price(
    groups: Groups | None,
    corpus: Sequence[GoldenCase],
    flows_by_case: Mapping[str, FlowMap],
    pairs: Sequence[LabelledPair],
    produced: Mapping[str, Sequence[Any]] | None = None,
) -> VerbPrice:
    """One candidate table on the three axes, and its gain over ``produced``.

    ``groups=None`` prices the shipped table, which is the row every other
    row is read against. The new-merge lists are the candidate's merges less
    the shipped rule's, so the row for the shipped rule carries none.
    """
    shipped_splits, shipped_false = _labelled(pairs, flows_by_case, None)
    shipped_reference = _references(corpus, flows_by_case, None)
    if groups is None:
        splits, false = shipped_splits, shipped_false
        reference = shipped_reference
    else:
        splits, false = _labelled(pairs, flows_by_case, groups)
        reference = _references(corpus, flows_by_case, groups)
    matched = must_find = None
    if produced is not None:
        matched, must_find = _gain(corpus, produced, flows_by_case, groups)
    return VerbPrice(
        groups=groups or (),
        false_splits=splits,
        false_merges=len(false),
        reference_merges=len(reference),
        matched=matched,
        must_find=must_find,
        new_false_merges=tuple(merge for merge in false if merge not in shipped_false),
        new_reference_merges=tuple(
            merge for merge in reference if merge not in shipped_reference
        ),
    )


def render(shipped: VerbPrice, candidates: Sequence[VerbPrice]) -> None:
    """The shipped rule's row, then one row per candidate, then every new merge named."""
    scored = shipped.matched is not None
    head = f"{'equivalence':44} {'splits':>7} {'merges':>7} {'ref':>5}"
    if scored:
        head += f" {'matched':>9} {'must-find':>10}"
    print(head)

    def row(label: str, priced: VerbPrice) -> str:
        line = (
            f"{label:44} {priced.false_splits:>7} {priced.false_merges:>7}"
            f" {priced.reference_merges:>5}"
        )
        if scored and priced.matched is not None and priced.must_find is not None:
            assert shipped.matched is not None and shipped.must_find is not None
            line += (
                f" {priced.matched:>4} ({priced.matched - shipped.matched:+d})"
                f" {priced.must_find:>5} ({priced.must_find - shipped.must_find:+d})"
            )
        return line

    print(row("shipped rule", shipped))
    for priced in candidates:
        label = " + ".join("=".join(sorted(group)) for group in priced.groups)
        print(row(label, priced))
        for merge in priced.new_false_merges:
            print(
                f"  labelled two findings, would merge: {merge.case} [{merge.lane}]"
                f" {merge.left_verb} vs {merge.right_verb}: {merge.left[:60]} |"
                f" {merge.right[:60]}"
            )
        for merge in priced.new_reference_merges:
            print(
                f"  corpus says two, would merge: {merge.case} [{merge.lane}]"
                f" {merge.left_verb} vs {merge.right_verb}: {merge.left[:60]} |"
                f" {merge.right[:60]}"
            )
    print(
        "splits: labelled matches the rule splits, of the scored pairs; merges:"
        " labelled non-matches it merges; ref: within-lane reference pairs it"
        " merges, every one an error. A candidate is a decision, not a fix."
    )
