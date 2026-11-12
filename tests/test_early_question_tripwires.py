"""Tripwires: measurements that fail when a fix deferred by decision is worth building.

A tripwire fails in one of two ways, and its message says which. **The figure
crossed its threshold**: the deferred fix is now warranted, and the message
names the issue that holds it. **The tripwire can no longer measure**: a
table it reads changed, and the test needs repair, not the fix.
"""

from __future__ import annotations

import pytest

from analysis_service.answer_state import passes_floor
from analysis_service.assertions import EXTRACTED, AssertionCatalog
from analysis_service.early_questions import early_questions
from analysis_service.fact_answers import key_ref
from analysis_service.open_facts import prepared_model, source_states
from analysis_service.question_kinds import QUESTION_KINDS, parse_question_kind
from analysis_service.report import Report
from analysis_service.system_model import SystemModel
from tests.factories import PROJECT_ROOT, valid_model

#: The committed Baselines, which CI reads too; a gitignored run is not here.
BASELINES = sorted(PROJECT_ROOT.glob("evals/baselines/*/*.reports/*.report.json"))

#: The part choices a description states, per description, at which an early
#: list that skips a stated part is worth building. QA-2026-09-26-03-E24
#: measured 0.08 over 248 archived reports and 0.12 over the Baselines.
STATED_PART_THRESHOLD = 1.0


def _stated_parts(
    model: SystemModel, catalog: AssertionCatalog | None, element_id: str, name: str
) -> int:
    """The parts of the kind ``name`` that the sources state about the element.

    Every part reads through :func:`source_states`, the subject rule of ADR
    0117, so a part whose predicate the assertion pass extracts counts here
    from the run that turns it on.
    """
    return sum(
        source_states(model, catalog, element_id, predicate)
        for _, predicate in QUESTION_KINDS[name].parts
    )


def _stated_choices(report: Report) -> int:
    """The part choices at or above the floor that this report's sources state."""
    catalog = report.assertions.catalog if report.assertions else None
    model = prepared_model(report.system_model, catalog)
    frameworks = {s.name: s.options for s in report.job.frameworks}
    listed = early_questions(report.system_model, frameworks, catalog)
    total = 0
    for question in listed:
        ref = key_ref(question.key)
        name = parse_question_kind(ref.question)
        if name is not None and passes_floor(question, listed):
            total += _stated_parts(model, catalog, ref.element_id, name)
    return total


def test_stated_parts_stay_rare():
    """Fails when early questions ask enough stated parts to warrant skipping them."""
    asked = {
        predicate for kind in QUESTION_KINDS.values() for _, predicate in kind.parts
    }
    if not BASELINES or not asked & EXTRACTED.keys():
        pytest.fail(
            "the tripwire can no longer measure: it needs a committed Baseline"
            " and a part of a question kind that the assertion pass extracts"
        )
    reports = [
        Report.model_validate_json(path.read_text(encoding="utf-8"))
        for path in BASELINES
    ]
    rate = sum(_stated_choices(report) for report in reports) / len(reports)
    assert rate < STATED_PART_THRESHOLD, (
        f"Early questions now ask {rate:.2f} part choices per archived"
        f" description that the description already states (threshold"
        f" {STATED_PART_THRESHOLD}; E24 measured 0.12 on the Baselines). An"
        " early list that skips a part the sources state is now warranted:"
        " build it, or raise the threshold and record why."
    )


def test_a_stated_part_counts():
    """A positive control: a reader that sees nothing would keep the wire quiet."""
    model = valid_model()
    element = model.processes[0]
    element.exposure = "internet-facing"
    assert _stated_parts(model, None, element.id, "reachability") == 1
    element.exposure = "unknown"
    assert _stated_parts(model, None, element.id, "reachability") == 0
    catalog = AssertionCatalog.model_validate(
        {
            "subjects": [
                {"id": element.id, "type": "component", "label": element.name}
            ],
            "entries": [
                {
                    "subject": element.id,
                    "predicate": "record-attribution",
                    "value": "principal",
                    "basis": "stated",
                    "origin": "assertion-pass",
                    "support": [],
                }
            ],
        }
    )
    assert _stated_parts(model, catalog, element.id, "audit-evidence") == 1
