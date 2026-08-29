"""Figures the prose states, recomputed from the code that produces them.

## The one distinction this module rests on

A number in a guide is one of two things, and they must never be checked alike.

A **recomputable figure** is a property of the repository as it stands: the
corpus holds 13 cases, the widest fan-out is 23 lanes, the identity rule agrees
with 185 of 200 readable labels. Change the thing and the number changes with
it, so prose that still states the old one is simply wrong. Every figure below
is of this kind, and every one is computed offline with no provider call.

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
)
from evals.harness.identity import SubsetVerbIdentity
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
    """What ``run calibrate`` reports, computed the way that command does."""
    matcher = SubsetVerbIdentity(_flows_by_case(load_corpus(verify_corpus.CORPUS_DIR)))
    result = measure_agreement(matcher, load_pairs())
    return {
        "agreed": result.agreements,
        "total": result.total,
        "percent": f"{result.agreement * 100:.1f}",
    }


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
        name="the identity rule's agreement with the labels",
        compute=_calibration,
        claims=(
            ("evals/README.md", "scores {agreed}/{total}", 2),
            ("evals/README.md", "A rule at {percent}%", 1),
            ("evals/TUNING.md", "{percent}%", 1),
        ),
    ),
    Figure(
        name="the labelled match fixtures",
        compute=lambda: {"value": len(load_pairs())},
        claims=(
            ("evals/README.md", "of the {value} match labels", 1),
            ("evals/TUNING.md", "of the {value}", 1),
            ("evals/BLESSING.md", "of the {value}", 1),
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
        claims=(("evals/README.md", "{percent}% bar", 4),),
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
