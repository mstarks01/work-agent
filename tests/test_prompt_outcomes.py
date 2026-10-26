"""Each prompt sentence that states what the code does, tested with the code.

A prompt tells a model what happens to its answer: a refusal, a re-ask, a drop,
an open question. The code decides that outcome, and the prompt only says it.
So each test here checks two things together: that the prompt holds the
sentence, and that the code does what the sentence says. A change to either
side fails the test, and the failure names the other side.

Add a test here for each new prompt sentence that states an outcome of the
code. A whole sentence is pinned, because a pin on a part of a sentence stays
green when the rest of the sentence changes. The one exception is a sentence
in an ASVS lane's ``skill.md``: those files carry CC BY-SA 4.0, so the test
pins a short phrase and never copies the sentence (``docs/agents/licensing.md``).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from analysis_service import graph
from analysis_service.assertions import assertion_id
from analysis_service.capabilities import resolve
from analysis_service.claims import ProposedVerdict
from analysis_service.compact import COMPACT_FORMAT
from analysis_service.critic import review_issues
from analysis_service.evidence import evidence_catalog, ground_gloss
from analysis_service.frameworks import PACKAGES, schemas_for
from analysis_service.frameworks.asvs.record import RequirementProposal
from tests.factories import sample_draft, sample_ruling, valid_model
from tests.test_capabilities import _facts
from tests.test_compact import compact_fixture
from tests.test_evidence import assertions, row
from tests.test_graph import (  # noqa: F401  (four fixtures)
    KEYS,
    RECRITIC_NODE,
    ROUTER_NODE,
    FakeContext,
    domain_loader,
    package_loaders,
    pipeline,
    prompt_loader,
    routed_targets,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def assert_states(path: str, sentence: str) -> None:
    """The file at ``path`` holds ``sentence``, whatever its line breaks."""
    text = " ".join((REPO_ROOT / path).read_text().split())
    assert " ".join(sentence.split()) in text, (
        f"{path} does not say {sentence!r}. The code below does what the"
        " sentence says: change the two together."
    )


@pytest.mark.parametrize("field", ["exposure", "operations"])
def test_an_omitted_compact_attribute_ends_the_job_with_no_repair(
    pipeline,  # noqa: F811
    field,
):
    assert_states(
        "prompts/extract-compact.md",
        "An omitted attribute, or a flow with no `operations`, is worse: the"
        " schema refuses the whole object, and the job ends with no repair.",
    )
    payload = compact_fixture()
    owner = "data_flows" if field == "operations" else "processes"
    del payload[owner][0][field]
    event = graph.validate_extraction(
        FakeContext(), KEYS, payload, None, COMPACT_FORMAT
    )

    assert event.actions.route == graph.ROUTE_UNCONVERTIBLE
    assert (
        routed_targets(pipeline, graph.VALIDATE_NODE)[graph.ROUTE_UNCONVERTIBLE]
        == graph.REJECT_NODE
    )


def test_a_field_the_ruling_does_not_carry_refuses_the_whole_answer():
    assert_states(
        "prompts/critic.md",
        "Include nothing it does not name: a field this framework's record does"
        " not carry is refused, and your whole answer is refused with it.",
    )
    sound = sample_ruling("S-01").model_dump(mode="json")
    surplus = {**sample_ruling("S-02").model_dump(mode="json"), "surplus": "x"}

    with pytest.raises(ValidationError):
        graph.rulings_of([sound, surplus], schemas_for("stride"))


def test_a_ruling_whose_fields_do_not_fit_comes_back_to_the_critic(
    pipeline,  # noqa: F811
):
    assert_states(
        "prompts/critic.md",
        "Code checks your rulings after you answer, and a ruling whose fields do"
        " not fit together comes back to you.",
    )
    unreasoned = sample_ruling(verdict=ProposedVerdict(rejected_because="lane"))
    problems = review_issues([sample_draft("S-01")], [unreasoned], valid_model())

    assert [problem.kind for problem in problems.problems] == ["verdict-shape"]
    assert routed_targets(pipeline, ROUTER_NODE)[graph.ROUTE_REVISE] == RECRITIC_NODE


def test_a_logging_gap_needs_a_stated_fact():
    assert_states(
        "frameworks/asvs/lanes/security-logging-and-error-handling/skill.md",
        "a gap needs a stated fact",
    )
    unstated = RequirementProposal(
        title="t",
        description="d",
        requirement="2.1",
        direction="gap",
        needs_evidence="",
        absent_elements=["log-store"],
    )

    assert PACKAGES["asvs"].record.unsupported(unstated)


def test_code_records_the_parent_from_a_present_child():
    assert_states(
        "prompts/capabilities.md",
        "A capability with a parent includes the parent, and code records the"
        " parent from the child.",
    )
    facts = resolve(_facts(turn_server="present"))

    assert facts["webrtc"].state == "present"
    assert facts["webrtc"].derived_from == "turn-server"


def test_a_disputed_row_is_open_whichever_value_it_states():
    assert_states(
        "prompts/analyze.md",
        "A row that says the sources disagree is open too, whichever value it states.",
    )
    absent, required = row(), row(value="required")
    held = assertions(absent, required)
    catalog = evidence_catalog(valid_model(), held)

    for side in (absent, required):
        assert catalog[assertion_id(side)].kind == "unknown-assertion"
    assert "the sources disagree" in ground_gloss(catalog[assertion_id(required)], held)
