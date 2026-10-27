"""The lane exemplars, sent through the fan-in that every real lane output meets.

The exemplar systems A and B are prose in ``prompts/analyze.md``. The fan-in
needs a **System Model**, so each system has a JSON fixture under
``tests/fixtures/exemplar-systems/``. Three tests hold each fixture to its
prose: the fixture passes the validity gate against the prose source block, its
element and flow IDs are the IDs the prose defines, and the evidence catalog
that code derives from it is the table the prose shows.

The fan-in test then sends each lane's exemplar drafts through
:func:`~analysis_service.fan_in.fan_in`, with the inputs the prepare step
computes for that model. Each check that the fan-in runs on lane output covers
the exemplars too, with no lint for each rule.
"""

import json
from pathlib import Path

import pytest

from analysis_service.claims import AnalysisMarks
from analysis_service.evidence import evidence_catalog, render_catalog
from analysis_service.fan_in import DraftJoinError, fan_in
from analysis_service.frameworks import PACKAGES, run_precondition, schemas_for
from analysis_service.graph import FrameworkNodes, framework_lane_inputs
from analysis_service.prompts import lane_exemplars_doc
from analysis_service.system_model import SystemModel
from analysis_service.validation import validate
from evals.harness.exemplar_verbs import proposal_type
from tests.test_capability_reader import WIDEST_OPTIONS
from tests.test_prompt_lints import (
    EXEMPLAR_LANES,
    PACKAGE_LOADERS,
    exemplar_systems,
    json_blocks,
    owning_system,
    source_block,
    system_for_label,
    system_ids,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "exemplar-systems"


def fixture_model(name):
    """The System Model fixture for one exemplar system, by its letter."""
    return SystemModel.model_validate_json(
        (FIXTURES / f"{name.lower()}.json").read_text()
    )


def sources_of(name):
    """One exemplar system's source text, keyed by its label, as a job holds it."""
    label, text = source_block(exemplar_systems()[name])
    return {label: text}


def test_every_exemplar_system_has_a_fixture():
    fixtures = {path.stem.upper() for path in FIXTURES.glob("*.json")}
    assert fixtures == set(exemplar_systems())


@pytest.mark.parametrize("name", sorted(exemplar_systems()))
def test_the_exemplar_fixture_passes_the_validity_gate(name):
    issues = validate(fixture_model(name), sources=sources_of(name))
    assert [(issue.code, issue.element_id) for issue in issues] == []


@pytest.mark.parametrize("name", sorted(exemplar_systems()))
def test_the_exemplar_fixture_holds_the_ids_its_prose_defines(name):
    model = fixture_model(name)
    assert {element.id for element in model.elements()} == system_ids(
        exemplar_systems()[name]
    )


@pytest.mark.parametrize("name", sorted(exemplar_systems()))
def test_the_exemplar_fixture_derives_the_catalog_its_prose_shows(name):
    rendered = render_catalog(evidence_catalog(fixture_model(name)), assertions=None)
    assert rendered.strip() in exemplar_systems()[name]


def system_name(body):
    """The letter of the exemplar system whose prose is ``body``."""
    return next(name for name, text in exemplar_systems().items() if text == body)


def owners(framework, proposal):
    """The exemplar systems the fan-in sees one draft against.

    A draft that cites an element belongs to the one system that defines it
    (:func:`~tests.test_prompt_lints.owning_system`). A draft that cites no
    element can still quote a source, and the label of that source names its
    system. A draft that does neither ties itself to the facts of no system.
    It can arrive in each job that runs its framework, so the fan-in sees it
    against each system where the framework's precondition holds.
    """
    owner = owning_system(proposal)
    if owner is not None:
        return {system_name(owner)}
    labelled = {system_for_label(quote.source_label) for quote in proposal.quotes}
    if labelled:
        return {system_name(body) for body in labelled if body is not None}
    package = PACKAGES[framework]
    return {
        name
        for name in exemplar_systems()
        if run_precondition(package, fixture_model(name)) == "satisfied"
    }


def exemplar_batches(framework, lane):
    """One lane's exemplar drafts as emitted JSON, grouped by exemplar system."""
    text = PACKAGE_LOADERS[framework].load(lane_exemplars_doc(lane))
    record = proposal_type(framework)
    batches = {}
    for block in json_blocks(text):
        emitted = json.loads(block)
        for name in owners(framework, record.model_validate(emitted)):
            batches.setdefault(name, []).append(emitted)
    return batches


def mark_lines(marks, drafts):
    """Each mark in ``marks``, as its kind, the title of its draft and the mark."""
    titles = {draft.id: draft.title for draft in drafts}
    lines = []
    for kind in AnalysisMarks.model_fields:
        for mark in getattr(marks, kind):
            claim_id = getattr(mark, "claim_id", None)
            title = getattr(mark, "title", None) or titles.get(claim_id, claim_id)
            lines.append(f"{kind}: {title}: {mark}")
    return lines


@pytest.mark.parametrize("framework,lane", EXEMPLAR_LANES)
def test_every_exemplar_draft_passes_the_fan_in_unmarked(framework, lane):
    """The fan-in keeps or defers each exemplar draft, and marks nothing.

    A deferred draft is one the package routes away from the critic, because
    the job carries no evidence of the kind the draft asks for. The fan-in runs
    no further check on it, so the per-rule lints still hold it.

    The ruled-out and ruled-in units come from the function the prepare step
    calls, at the widest options of each package, so every unit is in scope.
    """
    nodes = FrameworkNodes(framework)
    package = nodes.package
    for name, emitted in sorted(exemplar_batches(framework, lane).items()):
        model = fixture_model(name)
        where = f"{framework} {lane} on system {name}"
        assert run_precondition(package, model) == "satisfied", (
            f"{where}: the {framework} lanes never run on this system"
        )
        inputs = framework_lane_inputs(
            nodes,
            model,
            WIDEST_OPTIONS[framework],
            None,
            PACKAGE_LOADERS[framework],
        )
        batch = schemas_for(framework).proposals.model_validate({"claims": emitted})
        try:
            merged = fan_in(
                {lane: batch},
                package,
                model,
                sources_of(name),
                inputs.ruled_out,
                None,
                inputs.ruled_in,
            )
        except DraftJoinError as exc:
            pytest.fail(f"{where}: {exc}")
        kept = {draft.title for draft in merged.drafts}
        missing = [draft["title"] for draft in emitted if draft["title"] not in kept]
        assert len(missing) == len(merged.deferred), (
            f"{where}: {missing} became no draft, and the package deferred"
            f" {len(merged.deferred)}: {mark_lines(merged.marks, merged.drafts)}"
        )
        assert merged.marks == AnalysisMarks(), (
            f"{where}: {mark_lines(merged.marks, merged.drafts)}"
        )
