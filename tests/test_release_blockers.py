"""#926's release blockers, driven through analysis preparation.

The owner's decision of 2026-09-23 (option (iii)) sets two guarantees, one per
review state, and makes the critical falsification probes release blockers:

* **Unreviewed:** an unchecked assertion informs conditional analysis with its
  uncertainty visible. It never silences an uncertainty lead and never becomes
  a verified control.
* **Reviewed:** no reviewer-rejected fact reaches analysis. A row marked
  ``unsupported`` supports no definite conclusion. (Not "no wrong fact": a
  review can miss one.)

Every test here runs ``prepare_analysis`` itself, with every framework package
selected, and reads what it wrote into session state — the evidence table a
lane is shown and the model it reasons over — rather than asking whether a
validator raised a code. The corrupted inputs come from
:func:`evals.harness.falsify.reading`, the builder ``run.py falsify`` measures,
so the instrument and the blockers cannot drift apart.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from analysis_service import graph
from analysis_service.assertions import (
    ABSENT,
    UNKNOWN,
    AssertionCatalog,
    AssertionRecord,
    assertion_id,
    projected_attribute,
)
from analysis_service.frameworks import PACKAGES
from analysis_service.markdown_loader import MarkdownLoader
from analysis_service.system_model import SystemModel
from evals.harness import falsify
from evals.harness.reference import load_corpus
from evals.harness.replay import signed_reference
from tests.factories import repo_package_loaders
from tests.test_graph import ASVS_OPTIONS, PROJECT_ROOT, FakeContext

CORPUS = PROJECT_ROOT / "evals" / "corpus"
FRAMEWORKS = tuple(PACKAGES)
KEYS = graph.GraphKeys.of(FRAMEWORKS)
CASES = {case.id: case for case in load_corpus(CORPUS)}
ROW = re.compile(r"^\| `([^`]+)` \| (.*) \|$", re.MULTILINE)


class Prepared:
    """What ``prepare_analysis`` left in state for one catalog."""

    def __init__(
        self, catalog: AssertionCatalog, model: SystemModel, sources: dict
    ) -> None:
        record = AssertionRecord(proposed=len(catalog.entries), catalog=catalog)
        ctx = FakeContext(
            **{
                graph.STATE_ASSERTION_CATALOG: record.model_dump(mode="json"),
                graph.STATE_SOURCE_TEXTS: sources,
                graph.STATE_FRAMEWORK_OPTIONS: ASVS_OPTIONS[
                    graph.STATE_FRAMEWORK_OPTIONS
                ],
            }
        )
        graph.prepare_analysis(
            model.model_dump(mode="json"),
            ctx,
            KEYS,
            FRAMEWORKS,
            MarkdownLoader(Path(PROJECT_ROOT) / "domains"),
            repo_package_loaders(FRAMEWORKS),
            assertions=True,
        )
        held = ctx.state.get(graph.STATE_VALID_MODEL)
        self.model = model if held is None else SystemModel.model_validate(held)
        self.catalog = AssertionRecord.model_validate(
            ctx.state[graph.STATE_ASSERTION_CATALOG]
        ).catalog
        #: The evidence table as a lane receives it: reference, then gloss.
        self.table = dict(ROW.findall(ctx.state[graph.STATE_EVIDENCE_CATALOG]))

    @property
    def leads(self) -> frozenset[str]:
        """What the table offers a lane as an open question or an absence.

        Named by topic — an element's attribute, a subject's predicate — the
        reading ``falsify.consumer_view`` uses, parsed here from the rendered
        table so the two readers are compared rather than one trusted.
        """
        rows = {assertion_id(entry): entry for entry in self.catalog.entries}
        found = set()
        for ref in self.table:
            prefix, _, rest = ref.partition(":")
            if prefix in ("unknown", "absent"):
                element, _, attribute = rest.rpartition(":")
                found.add(f"{element}.{attribute}")
            elif ref in rows and rows[ref].value in (UNKNOWN, ABSENT):
                found.add(f"{rows[ref].subject}.{rows[ref].predicate}")
        return frozenset(found)


def prepared(
    name: str, *, reviewed: bool
) -> tuple[falsify.Reading, Prepared, Prepared]:
    case = CASES[falsify.PROBES[name].case_id]
    reference = signed_reference(CORPUS, case)
    assert reference is not None
    built = falsify.reading(name, case, reference, reviewed=reviewed)
    sources = dict(built.sources)
    return (
        built,
        Prepared(built.catalog, built.model, sources),
        Prepared(built.without, built.model, sources),
    )


CRITICAL = sorted(falsify.CRITICAL)


@pytest.mark.parametrize("name", CRITICAL)
def test_an_unchecked_row_never_silences_a_lead(name) -> None:
    """Release blocker, unreviewed: every lead the job offers without the
    corrupted rows, it still offers with them."""
    _, corrupted, without = prepared(name, reviewed=False)

    assert without.leads <= corrupted.leads


@pytest.mark.parametrize("name", CRITICAL)
def test_an_unchecked_row_reaches_a_lane_marked_unchecked(name) -> None:
    """Release blocker, unreviewed: a corrupted row a lane can cite says so."""
    built, corrupted, _ = prepared(name, reviewed=False)

    for ref, gloss in corrupted.table.items():
        if ref in built.changed:
            assert gloss.endswith("— unchecked"), (ref, gloss)


@pytest.mark.parametrize("name", CRITICAL)
def test_an_unchecked_row_is_never_a_verified_control(name) -> None:
    """Release blocker, unreviewed: where the model without the corrupted
    rows reads a lead, the model with them does not read a stated control
    that only a corrupted row could have put there."""
    built, corrupted, without = prepared(name, reviewed=False)
    before = {element.id: element for element in without.model.elements()}
    after = {element.id: element for element in corrupted.model.elements()}
    for entry in corrupted.catalog.entries:
        if assertion_id(entry) not in built.changed:
            continue
        attribute = projected_attribute(entry.predicate, entry.subject)
        if not attribute or entry.subject not in before:
            continue
        was = str(getattr(before[entry.subject], attribute))
        now = str(getattr(after[entry.subject], attribute))
        if not was.startswith(("unknown", "none")) and was.strip():
            continue
        assert now == was or now.startswith("unknown"), (entry.subject, was, now)


@pytest.mark.parametrize("name", CRITICAL)
def test_no_reviewer_rejected_fact_reaches_analysis(name) -> None:
    """Release blocker, reviewed: a row marked ``unsupported`` is neither in
    the evidence table nor the value of any attribute."""
    built, corrupted, _ = prepared(name, reviewed=True)
    by_id = {element.id: element for element in corrupted.model.elements()}

    assert not built.changed & set(corrupted.table)
    for entry in corrupted.catalog.entries:
        if assertion_id(entry) not in built.changed:
            continue
        attribute = projected_attribute(entry.predicate, entry.subject)
        if attribute and entry.subject in by_id:
            assert getattr(by_id[entry.subject], attribute) != entry.value


@pytest.mark.parametrize("name", CRITICAL)
def test_review_leaves_no_lead_silenced(name) -> None:
    _, corrupted, without = prepared(name, reviewed=True)

    assert without.leads <= corrupted.leads


@pytest.mark.parametrize("name", CRITICAL)
@pytest.mark.parametrize("reviewed", [False, True])
def test_the_instrument_and_preparation_agree(name, reviewed) -> None:
    """``run.py falsify`` reads the same leads the job renders."""
    built, corrupted, _ = prepared(name, reviewed=reviewed)
    _, leads = falsify.consumer_view(built.catalog, built.model, built.sources)

    assert leads == corrupted.leads
