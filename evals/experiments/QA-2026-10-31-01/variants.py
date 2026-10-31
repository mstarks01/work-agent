"""The two early-list orders of QA-2026-10-31-01 that lost, against 0a2fcb5f.

In both, the floor reads a field question's relevance, as the order does.
They differ in ``early_questions.reads``: E1 lets a question kind count every
candidate on its element, and E2 keeps the shipped reader, where a kind counts
none. Run it with the folder of archived reports the comparison read::

    python evals/experiments/QA-2026-10-31-01/variants.py <root>

It reads both orders with ``early-policies``'s walk and prints each figure
that differs from ``variants.json``.
"""

import json
import sys
from dataclasses import replace
from pathlib import Path
from unittest import mock

import analysis_service.early_questions as early_module
from analysis_service import answer_state
from analysis_service.question_kinds import QUESTION_KINDS
from evals.harness.early_policies import _each, _shipped_list, paired, summary, sweep

SHIPPED_READS = early_module.reads
SHIPPED_FLOOR = answer_state.passes_floor


def _by_relevance(question, listed):
    """The floor, read off a field question's relevance."""
    if question.kind != "capability":
        question = replace(question, score=question.relevance)
    return SHIPPED_FLOOR(question, listed)


def _order(reads, model, chosen):
    with (
        mock.patch.object(early_module, "reads", reads),
        mock.patch.object(answer_state, "passes_floor", _by_relevance),
    ):
        return _each(_shipped_list(model, chosen))


def kinds_count_every_candidate(model, chosen):
    """E1: a question kind counts every candidate on its element."""
    return _order(
        lambda candidate, field: (
            field in QUESTION_KINDS or SHIPPED_READS(candidate, field)
        ),
        model,
        chosen,
    )


def floor_reads_the_relevance(model, chosen):
    """E2: a question kind counts no candidate."""
    return _order(SHIPPED_READS, model, chosen)


def main(root: Path) -> None:
    """Read E1 and E2 over ``root`` and print each figure unlike the record."""
    variants = {
        "fact-reads": kinds_count_every_candidate,
        "fact-reads-prior": floor_reads_the_relevance,
    }
    with mock.patch("evals.harness.early_policies.POLICIES", variants):
        found = summary(sweep(paired(root)))
    recorded = json.loads(Path(__file__).with_name("variants.json").read_text())
    differ = [
        (group, row)
        for group, rows in found.items()
        for row, figures in rows.items()
        if recorded["summary"][group].get(row) != figures
    ]
    print(f"{len(differ)} figures differ from variants.json", *differ, sep="\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
