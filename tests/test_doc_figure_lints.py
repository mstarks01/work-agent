"""Figures the prose states, recomputed from the code that produces them.

## The one distinction this module rests on

A number in a guide is one of two things, and they must never be checked alike.

A **recomputable figure** is a property of the repository as it stands: the
corpus holds 13 cases, the widest fan-out is 23 lanes, the identity rule agrees
with 295 of 315 readable labels. Change the thing and the number changes with
it, so prose that still states the old one is simply wrong. Every figure below
is of this kind, and every one is computed offline with no provider call.

**A module docstring is prose too.** `fingerprint.py` and `verbs.py` argue for
version 2 from these numbers, and they went stale the moment the fixtures grew
because nothing here read a `.py` file. A claim names any path in the
repository; the extension decides nothing.

A **recorded observation** is what a run once produced: luna's 3.4%
unverified-quote rate, the 231 of 243 claims a mechanical check fired on,
the 30 labels sitting 01 read. Those stay true when the code moves, because
they describe an event rather than a state. Nothing here checks them, and a
lint that did would demand a re-run to stay green — which for the live figures
means demanding money.

Getting that split wrong is not hypothetical. ``evals/README.md`` states "13
cases" twice: once for the corpus, and once for the ``gpt-5.6-luna`` sweep that
swept 13 of them on 2026-08-23. A fourteenth case makes the first wrong and
leaves the second correct. So a claim is declared **per document**, never as one
phrase matched everywhere.

## What a failure means

The number moved and the sentence did not. Read the recomputed value in the
message, find the claim, and decide which is right — the fix is sometimes the
code, which is why this reports the pair rather than rewriting the prose.

This decides that a stated figure is current. It does not decide that the
sentence around it draws the right conclusion.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

from analysis_service.frameworks import widest_fan_out
from evals import verify_corpus
from evals.harness.calibration import (
    AGREEMENT_BAR,
    load_pairs,
    measure_agreement,
    measure_merges,
)
from evals.harness.identity import MechanicalIdentity, SubsetVerbIdentity
from evals.harness.reference import load_corpus
from evals.harness.run import _flows_by_case
from evals.harness.sitting import unreviewed_cases

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Small numbers as the prose writes them. Only the counts a guide spells out
#: need an entry; a figure written in digits everywhere needs none.
#:
#: The range runs down to zero as well as up, because the unread count below
#: is a figure this repository is actively trying to shrink.
WORDS = {
    0: "None",
    1: "One",
    2: "Two",
    3: "Three",
    4: "Four",
    5: "Five",
    6: "Six",
    7: "Seven",
    8: "Eight",
    9: "Nine",
    10: "Ten",
    11: "Eleven",
    12: "Twelve",
    13: "Thirteen",
    14: "Fourteen",
    15: "Fifteen",
}


@dataclass(frozen=True)
class Figure:
    """One recomputable number, and every sentence that states it.

    ``compute`` returns the named values a claim's template may use, so a
    figure with two halves — an element band, a ratio and its percentage — is
    one entry rather than several that could drift apart.

    ``claims`` carries a **count** beside each document and template, and the
    count is the whole reason this is not a substring check. ``README.md``
    states the calibration score twice — once in prose and once in the harness
    table — and a presence test passes while either one of them is stale. So a
    claim declares how many times the document says it, and one mention drifting
    out of step with its twin drops the count and fails.

    Adding a mention therefore fails too, until the count is raised. That is
    correct rather than annoying: a new sentence stating a figure is a new claim
    to keep true, and the table is where this repository records those.
    """

    name: str
    compute: Callable[[], Mapping[str, object]]
    claims: tuple[tuple[str, str, int], ...]


def _calibration() -> Mapping[str, object]:
    """The admission-gate ratio, for both rules the guides tabulate."""
    matcher = SubsetVerbIdentity(_flows_by_case(load_corpus(verify_corpus.CORPUS_DIR)))
    pairs = load_pairs()
    result = measure_agreement(matcher, pairs)
    floor = measure_agreement(MechanicalIdentity(), pairs)
    return {
        "agreed": result.agreements,
        "total": result.total,
        "percent": f"{result.agreement * 100:.1f}",
        "floor_agreed": floor.agreements,
        "floor_total": floor.total,
        "floor_percent": f"{floor.agreement * 100:.1f}",
    }


def _error_directions() -> Mapping[str, object]:
    """Every way the shipped rule fails, which is the primary measurement.

    One figure rather than three, because every sentence that states a split
    count states the merge counts beside it — and a guide that updated one
    alone would be worse than one that updated none. The three denominators are
    three populations and are named apart for that reason.
    """
    corpus = load_corpus(verify_corpus.CORPUS_DIR)
    matcher = SubsetVerbIdentity(_flows_by_case(corpus))
    pairs = [pair for pair in load_pairs() if pair.is_scored]
    result = measure_agreement(matcher, pairs)
    merges = measure_merges(corpus, "stride", _flows_by_case(corpus))
    positives = sum(1 for pair in pairs if pair.label_match)
    assigned = [pair for pair in pairs if pair.candidate_element_ids is not None]
    floor = _frontier_row(assigned, corpus)
    return {
        "splits": len(result.false_non_matches),
        "split_of": positives,
        "cand_merges": len(result.false_matches),
        "cand_of": result.total - positives,
        "merges": len(merges.merges),
        "merge_of": merges.within_lane_pairs,
        "floor_splits": floor["splits"],
        "floor_cand_merges": floor["candidate_merges"],
    }


def _frontier_row(pairs, corpus):
    """One row of the frontier, so a docstring quoting it cannot go stale.

    ``fingerprint.py`` and ``verbs.py`` both argue for the action verb from the
    ``endpoint subset`` row — what version 1 costs without it — so the figure
    that pins their prose has to recompute that row rather than the shipped
    rule's.
    """
    from evals.harness.identity import endpoint_subset

    flows = _flows_by_case(corpus)
    splits = candidate_merges = 0
    for pair in pairs:
        ruled = endpoint_subset(
            pair.reference_element_ids,
            pair.candidate_element_ids,
            flows[pair.case],
        )
        if pair.label_match and not ruled:
            splits += 1
        elif ruled and not pair.label_match:
            candidate_merges += 1
    return {"splits": splits, "candidate_merges": candidate_merges}


def _corpus() -> Mapping[str, object]:
    count = len(verify_corpus.case_dirs())
    return {"value": count, "word": WORDS.get(count, str(count))}


def _unreviewed() -> Mapping[str, object]:
    """Cases nobody has sat with, beside the corpus size the prose pairs it to.

    Both halves in one figure because both sentences state the pair, and a
    guide saying "12 of the 13" goes wrong when either number moves. This is
    the figure the contribution path exists to change: every merged sitting
    PR clears a line, so it is the most likely of all of them to go stale.
    """
    count = len(unreviewed_cases(REPO_ROOT))
    return {
        "value": count,
        "word": WORDS.get(count, str(count)),
        "corpus": len(verify_corpus.case_dirs()),
    }


FIGURES: tuple[Figure, ...] = (
    Figure(
        name="the widest framework fan-out",
        compute=lambda: {"value": widest_fan_out()},
        claims=(("evals/TUNING.md", "{value} today", 1),),
    ),
    Figure(
        name="the corpus size",
        compute=_corpus,
        claims=(
            ("evals/README.md", "{word} cases", 1),
            ("evals/BLESSING.md", "All {value} cases", 1),
        ),
    ),
    Figure(
        name="the cases nobody has sat with",
        compute=_unreviewed,
        claims=(
            ("CONTRIBUTING.md", "{value} of the {corpus} cases", 1),
            ("evals/BLESSING.md", "{word} of the {corpus} cases", 1),
        ),
    ),
    Figure(
        name="the identity rule's two error directions",
        compute=_error_directions,
        claims=(
            ("evals/README.md", "splits over {split_of} equivalent candidate pairs", 1),
            (
                "evals/README.md",
                "{cand_merges} false merges over {cand_of} candidate",
                1,
            ),
            ("evals/README.md", "{merges} false merges over {merge_of}", 1),
            (
                "evals/README.md",
                (
                    "{splits} false splits of {split_of}, {cand_merges} false"
                    " merges of {cand_of} and {merges} false merges of"
                    " {merge_of}"
                ),
                1,
            ),
            (
                "evals/TUNING.md",
                (
                    "{splits} false splits of {split_of} equivalent candidate"
                    " pairs, {cand_merges} false merges of"
                ),
                1,
            ),
            (
                "docs/agents/claim-identity.md",
                (
                    "| False splits (of {split_of}) | Candidate merges (of"
                    " {cand_of}) | Reference merges (of {merge_of}) |"
                ),
                1,
            ),
            (
                "docs/agents/claim-identity.md",
                (
                    "| **endpoint subset + verb** | **{splits}** |"
                    " **{cand_merges}** | **{merges}** |"
                ),
                1,
            ),
            (
                "docs/agents/claim-identity.md",
                (
                    "over the {cand_of} scored candidate negatives, and"
                    " {merges} false merges"
                ),
                1,
            ),
            (
                "docs/agents/claim-identity.md",
                (
                    "{splits} of {split_of} labelled\nmatches split,"
                    " {cand_merges} of {cand_of} candidate negatives merged,"
                    " {merges} of {merge_of} reference pairs\nmerged"
                ),
                1,
            ),
            (
                "evals/harness/fingerprint.py",
                (
                    "takes the candidate merges from {floor_cand_merges} to"
                    " {cand_merges}"
                ),
                1,
            ),
            (
                "evals/harness/fingerprint.py",
                (
                    "agreement alone at {floor_splits} false splits of"
                    " {split_of}, {floor_cand_merges} false merges of {cand_of}"
                ),
                1,
            ),
            (
                "evals/harness/verbs.py",
                ("{floor_cand_merges} false merges of\n{cand_of} candidate negatives"),
                1,
            ),
            (
                "evals/harness/verbs.py",
                "merges {floor_cand_merges} of {cand_of}",
                1,
            ),
        ),
    ),
    Figure(
        name="the identity rule's agreement with the labels",
        compute=_calibration,
        claims=(
            ("evals/README.md", "A rule at {percent}%", 1),
            (
                "docs/agents/claim-identity.md",
                "| `SubsetVerbIdentity` | **{agreed}/{total} = {percent}%** |",
                1,
            ),
            (
                "docs/agents/claim-identity.md",
                (
                    "| `MechanicalIdentity` (element equality) |"
                    " {floor_agreed}/{floor_total} = {floor_percent}% |"
                ),
                1,
            ),
        ),
    ),
    Figure(
        name="the labelled match fixtures",
        compute=lambda: {"value": len(load_pairs())},
        claims=(
            ("evals/README.md", "of the {value} match labels", 1),
            ("evals/TUNING.md", "of the {value}", 1),
            ("evals/BLESSING.md", "of the {value}", 2),
        ),
    ),
    Figure(
        name="the element band a case is sized to",
        compute=lambda: {
            "min": verify_corpus.MIN_ELEMENTS,
            "max": verify_corpus.MAX_ELEMENTS,
        },
        claims=(
            ("evals/README.md", "{min}–{max} elements", 1),
            ("evals/BLESSING.md", "{min}–{max} elements", 1),
        ),
    ),
    Figure(
        name="the rule-label agreement bar",
        compute=lambda: {"percent": round(AGREEMENT_BAR * 100)},
        claims=(
            ("evals/README.md", "{percent}% bar", 2),
            ("docs/agents/claim-identity.md", "clears the {percent}% bar", 1),
        ),
    ),
)

CASES = [
    pytest.param(
        figure, document, template, count, id=f"{figure.name}::{document}::{template}"
    )
    for figure in FIGURES
    for document, template, count in figure.claims
]


@pytest.mark.parametrize(("figure", "document", "template", "count"), CASES)
def test_the_prose_states_the_current_figure(figure, document, template, count):
    """Every sentence stating this figure names the number the code produces."""
    values = figure.compute()
    expected = template.format(**values)
    found = (REPO_ROOT / document).read_text(encoding="utf-8").count(expected)

    assert found == count, (
        f"{document} states {expected!r} {found} time(s) and this figure"
        f" declares {count}. The computed values for {figure.name} are"
        f" {dict(values)}. Either a mention went stale while its twin stayed"
        " current, or a new mention landed and the count needs raising."
    )


def test_every_figure_is_claimed_somewhere():
    """Guards the guard: a figure with no claim checks nothing."""
    unclaimed = sorted(figure.name for figure in FIGURES if not figure.claims)

    assert not unclaimed, f"these figures state no claim to check: {unclaimed}"
