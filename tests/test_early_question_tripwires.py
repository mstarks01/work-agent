"""Tripwires: measurements that fail when a fix deferred by decision is worth building.

A tripwire fails in one of two ways, and its message says which. **The figure
crossed its threshold**: the deferred fix is now warranted, and the message
names the issue that holds it. **The tripwire can no longer measure**: a
table it reads changed, and the test needs repair, not the fix.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

import pytest

from analysis_service.answer_round import passes_floor
from analysis_service.assertions import REGISTRY, AssertionCatalog, settled
from analysis_service.early_questions import early_questions
from analysis_service.question_kinds import QUESTION_KINDS
from analysis_service.questions import prepared_model
from analysis_service.report import Report
from analysis_service.system_model import Process, SystemModel
from tests.factories import PROJECT_ROOT, valid_model

#: The committed Baselines, which CI reads too; a gitignored run is not here.
BASELINES = sorted(PROJECT_ROOT.glob("evals/baselines/*/*.reports/*.report.json"))

#: The facet choices a description states, per description, at which the
#: source binding of #1347 is worth building. E24 measured 0.08 over 248
#: archived reports and 0.12 over the Baselines.
STATED_FACET_THRESHOLD = 1.0

Stated = Callable[[SystemModel, AssertionCatalog | None, str], bool]


def _stated_exposure(
    model: SystemModel, catalog: AssertionCatalog | None, element_id: str
) -> bool:
    """The element's ``exposure`` answers ``reachability.internet``."""
    del catalog
    exposure = getattr(model.get(element_id), "exposure", None)
    return exposure in REGISTRY["internet-exposure"].terms


def _stated_attribution(
    model: SystemModel, catalog: AssertionCatalog | None, element_id: str
) -> bool:
    """A settled, unscoped ``record-attribution`` row answers ``records-actor``."""
    del model
    return catalog is not None and any(
        row.predicate == "record-attribution"
        and row.subject == element_id
        and not row.scope
        for row in settled(catalog)
    )


#: Each source fact that answers one facet at one element's scope
#: (``QA-2026-09-26-03-E24``), by the question kind and facet it answers.
STATED_FACETS: Mapping[tuple[str, str], Stated] = {
    ("reachability", "internet"): _stated_exposure,
    ("audit-evidence", "records-actor"): _stated_attribution,
}


def _cannot_measure() -> list[str]:
    """What the tripwire reads and no longer finds, or nothing."""
    missing = []
    for kind, facet in STATED_FACETS:
        question = QUESTION_KINDS.get(kind)
        if question is None or facet not in {f.id for f in question.facets}:
            missing.append(f"the facet {kind}.{facet}")
    exposure = REGISTRY.get("internet-exposure")
    if "exposure" not in Process.model_fields or exposure is None:
        missing.append(
            "the process attribute exposure or the internet-exposure predicate"
        )
    elif exposure.projects_into != "exposure":
        missing.append("internet-exposure projecting into exposure")
    attribution = REGISTRY.get("record-attribution")
    if attribution is None or attribution.terms != {"principal", "intermediary"}:
        missing.append("the record-attribution predicate with its two terms")
    if not BASELINES:
        missing.append("a committed Baseline report")
    return missing


def _stated_choices(report: Report) -> int:
    """The facet choices at or above the floor that this report's sources state."""
    catalog = report.assertions.catalog if report.assertions else None
    model = prepared_model(report.system_model, catalog)
    frameworks = {s.name: s.options for s in report.job.frameworks}
    listed = early_questions(report.system_model, frameworks, catalog)
    return sum(
        1
        for question in listed
        if passes_floor(question, listed)
        for (kind, _), stated in STATED_FACETS.items()
        if question.key[4] == kind and stated(model, catalog, question.key[0])
    )


def test_stated_facets_stay_rare():
    """Fails when early questions ask enough stated facets to warrant #1347."""
    missing = _cannot_measure()
    if missing:
        pytest.fail(
            "the tripwire can no longer measure E24's gap; repair this test, not"
            f" #1347. Not found: {'; '.join(missing)}"
        )
    reports = [
        Report.model_validate_json(path.read_text(encoding="utf-8"))
        for path in BASELINES
    ]
    rate = sum(_stated_choices(report) for report in reports) / len(reports)
    assert rate < STATED_FACET_THRESHOLD, (
        f"Early questions now ask {rate:.2f} facet choices per archived"
        f" description that the description already states (threshold"
        f" {STATED_FACET_THRESHOLD}; E24 measured 0.12 on the Baselines). The"
        " source-to-facet binding in QA-2026-09-26-03-E24 is now warranted:"
        " build it (issue #1347), or raise the threshold and record why."
    )


@pytest.mark.parametrize(("facet", "stated"), list(STATED_FACETS.items()))
def test_each_reader_sees_a_stated_fact(facet, stated):
    """A positive control: a reader that sees nothing would keep the wire quiet."""
    model = valid_model()
    element = model.processes[0]
    if facet == ("reachability", "internet"):
        element.exposure = "internet-facing"
        assert stated(model, None, element.id)
        element.exposure = "unknown"
        assert not stated(model, None, element.id)
        return
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
                    "support": [],
                }
            ],
        }
    )
    assert stated(model, catalog, element.id)
    assert not stated(model, None, element.id)
