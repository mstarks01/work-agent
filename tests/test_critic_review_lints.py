"""The critic-review fixture set: well formed, really grounded, and signed or not.

The set answers one question — does the critic read a draft's argument, or only
its references — and it is the only instrument for it, because whether a
conclusion follows from a fact is the judgement no rule can make. That makes its
provenance load-bearing in a way a corpus lint's is not: an expectation nobody
signed is one agent grading another agent's change.

So these checks do two jobs. They hold each fixture to the real corpus model, so
a draft cannot drift into describing a system the corpus no longer carries. And
they read ``reviewed_by``, so an unsigned set can be run and read but cannot be
quoted as evidence.

Deterministic, credential-free, and free of provider calls.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from analysis_service.frameworks.stride.record import DraftThreat
from analysis_service.grounding import normalize, verify_normalized
from analysis_service.system_model import ModelIndex, SystemModel
from evals.critic_review.model import CriticFixture, FixtureKind

REPO_ROOT = Path(__file__).resolve().parents[1]
CASES = REPO_ROOT / "evals" / "critic_review" / "cases.json"


def fixtures() -> list[CriticFixture]:
    raw = json.loads(CASES.read_text(encoding="utf-8"))
    return [CriticFixture.model_validate(entry) for entry in raw]


def corpus_model(case_id: str) -> SystemModel:
    path = REPO_ROOT / "evals" / "corpus" / case_id / "model.json"
    return SystemModel.model_validate(json.loads(path.read_text(encoding="utf-8")))


def source_text(case_id: str) -> str:
    return (REPO_ROOT / "evals" / "corpus" / case_id / "source.md").read_text("utf-8")


ALL = fixtures()
IDS = [entry.id for entry in ALL]


def test_the_set_is_small_and_carries_every_kind():
    """Four kinds, and none of them empty.

    A set missing ``credible-conditional`` would be passed by a critic that
    rejects every conditional draft, which is the cheapest wrong answer to the
    change and the one a kill-counting measurement rewards.
    """
    kinds = {entry.kind for entry in ALL}

    assert kinds == set(FixtureKind.__args__)
    assert len(ALL) == len(set(IDS)), "fixture ids are unique"


def test_both_halves_of_the_measurement_have_rows():
    """Findings to remove and findings to preserve, or the set measures one thing."""
    survives = [entry for entry in ALL if entry.expect.survives]
    removed = [entry for entry in ALL if not entry.expect.survives]

    assert survives and removed


@pytest.mark.parametrize("fixture", ALL, ids=IDS)
def test_each_draft_is_one_a_lane_agent_could_have_emitted(fixture):
    """The package's own record parses it, so the fixture is a draft and not a sketch."""
    DraftThreat.model_validate(fixture.draft)


@pytest.mark.parametrize("fixture", ALL, ids=IDS)
def test_every_element_a_fixture_names_is_in_its_corpus_model(fixture):
    """A fixture describing elements the case does not hold tests nothing."""
    model = corpus_model(fixture.case)
    known = {element.id for element in model.elements()}
    draft = DraftThreat.model_validate(fixture.draft)

    assert set(draft.affected_element_ids) <= known
    for ground in draft.grounds:
        if ground.element_id:
            assert ground.element_id in known, ground.element_id
        if ground.flow_id:
            assert ground.flow_id in ModelIndex.of(model).flow_endpoints


@pytest.mark.parametrize("fixture", ALL, ids=IDS)
def test_every_quoted_ground_is_really_in_the_source(fixture):
    """The quotes are the case's own words, checked the way the gate checks them.

    A fixture arguing against a sentence nobody submitted would be rejected for
    its citation rather than for its reasoning, and would stop testing the thing
    it was written for.

    Read through :func:`~analysis_service.grounding.verify_normalized`, which is
    what the fan-in runs on a real ground. A second rule here would eventually
    accept a quote the service rejects, and the fixture would be measuring the
    lint rather than the critic — the sources are hard-wrapped prose, so a plain
    substring test disagrees with the gate on the first quote spanning a line
    break.
    """
    folded = normalize(source_text(fixture.case))
    draft = DraftThreat.model_validate(fixture.draft)

    for ground in draft.grounds:
        if ground.kind == "quote":
            assert verify_normalized(ground.text, folded), ground.text


@pytest.mark.parametrize("fixture", ALL, ids=IDS)
def test_an_unknown_ground_names_an_attribute_the_model_really_leaves_open(fixture):
    """The shield has to be real, or the bypass fixture does not exercise it."""
    model = corpus_model(fixture.case)
    by_id = {element.id: element for element in model.elements()}
    draft = DraftThreat.model_validate(fixture.draft)

    for ground in draft.grounds:
        if ground.kind != "unknown-attribute":
            continue
        value = getattr(by_id[ground.element_id], ground.attribute, None)
        assert value == "unknown", (
            f"{fixture.id}: {ground.element_id}.{ground.attribute} is {value!r},"
            " so this ground would not resolve as an unknown on a real run"
        )


@pytest.mark.parametrize("fixture", ALL, ids=IDS)
def test_a_rejection_expectation_names_the_step_that_kills_it(fixture):
    """An expectation of "rejected" that names no step is half an expectation."""
    if fixture.expect.status == "rejected":
        assert fixture.expect.rejected_because is not None


@pytest.mark.parametrize("fixture", ALL, ids=IDS)
def test_an_exact_status_agrees_with_whether_the_draft_survives(fixture):
    """The two expectation fields cannot disagree about the same fixture."""
    if fixture.expect.status is None:
        return
    assert fixture.expect.survives == (fixture.expect.status != "rejected")


def test_an_unsigned_set_is_named_rather_than_trusted():
    """The gate on quoting this set as evidence.

    Not a failure while the set is unsigned — it is useful to run before anyone
    reads it. What it must never do is pass silently, because a number from an
    unsigned set looks exactly like a number from a signed one.
    """
    unsigned = [entry.id for entry in ALL if entry.reviewed_by is None]

    if unsigned:
        pytest.skip(
            f"{len(unsigned)} of {len(ALL)} fixtures are unsigned, so this set"
            " states what an agent proposed and not what a reader ruled. It may"
            " be run; it may not be quoted as evidence that the review change"
            f" works. Unsigned: {unsigned}"
        )
    assert all(entry.reviewed_by for entry in ALL)
