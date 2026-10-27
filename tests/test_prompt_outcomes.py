"""Each prompt sentence that states what the code does, tested with the code.

A prompt tells a model what happens to its answer: a refusal, a re-ask, a drop,
an open question. The code decides that outcome, and the prompt only says it.
``.github/scripts/prompt_outcomes.toml`` holds one row for each such sentence:
the prompt, the sentence, the modules that decide it, and the name of the check
below that runs the code. The one test here runs over the rows: it asserts the
sentence is in the prompt file and runs the row's check. A change to either
side fails it, and the failure names the other side.

Add a row for each new prompt sentence that states an outcome of the code, and
a check function with the row's ``check`` name. A whole sentence is pinned,
because a pin on a part of a sentence stays green when the rest of the
sentence changes. The one exception is a sentence in an ASVS lane's
``skill.md``: those files carry CC BY-SA 4.0, so the row pins a short phrase
and never copies the sentence (``docs/agents/licensing.md``). A row whose
sentence no code decides carries ``wording`` with the reason instead of a
check, and only the pin runs.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import get_args

import pytest
from pydantic import ValidationError

from analysis_service import graph
from analysis_service.assertions import assertion_id
from analysis_service.capabilities import resolve
from analysis_service.claims import AnalysisMarks, ProposedVerdict, Severity
from analysis_service.compact import COMPACT_FORMAT
from analysis_service.critic_round import CriticRound, Reask, rulings_of
from analysis_service.evidence import evidence_catalog, ground_gloss
from analysis_service.fan_in import join_drafts
from analysis_service.frameworks import PACKAGES, schemas_for
from analysis_service.frameworks.asvs.record import RequirementProposal
from analysis_service.graph import parse_extraction
from analysis_service.system_model import normalize_name
from analysis_service.validation import IssueCode, parse_and_validate, validate
from tests.factories import sample_draft, sample_ruling, valid_model
from tests.source_tree import REPO_ROOT
from tests.test_capabilities import _facts
from tests.test_compact import compact_fixture
from tests.test_evidence import assertions, row
from tests.test_five_class_read import TABLE
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
from tests.test_validation import TestCitationsResolve, codes

pytestmark = pytest.mark.lint

OUTCOMES = TABLE["outcome"]

#: The checks, by the name a row's ``check`` gives. Each one runs the code
#: that decides the row's sentence. A parameter names a pytest fixture.
CHECKS: dict[str, Callable[..., None]] = {}

#: The ways an extraction is refused that no prompt sentence prevents, with the
#: reason. A size ceiling admits or refuses on the count of elements, and no
#: instruction keeps a large system small.
UNWORDED_ISSUE_CODES: dict[str, str] = {
    "too-many-elements": "an admission cap on model size, not a rule a model can follow"
}

#: The one source every citing element of a test model quotes.
SOURCES = {"Kickoff call": "Ana: a quote, and the orders DB is not encrypted."}


def decides(function: Callable[..., None]) -> Callable[..., None]:
    """Register ``function`` under its name as a row's check."""
    CHECKS[function.__name__] = function
    return function


def assert_states(path: str, sentence: str) -> None:
    """The file at ``path`` holds ``sentence``, whatever its line breaks."""
    text = " ".join((REPO_ROOT / path).read_text().split())
    assert " ".join(sentence.split()) in text, (
        f"{path} does not say {sentence!r}. The code the row's check runs does"
        " what the sentence says: change the two together."
    )


def _outcome_id(outcome: dict) -> str:
    return outcome.get("check") or f"{outcome['prompt']}: wording"


@pytest.mark.parametrize("outcome", OUTCOMES, ids=_outcome_id)
def test_each_outcome_sentence_is_true_of_the_code(outcome, request):
    assert_states(outcome["prompt"], outcome["sentence"])
    if "check" not in outcome:
        return
    check = CHECKS[outcome["check"]]
    fixtures = {
        name: request.getfixturevalue(name)
        for name in inspect.signature(check).parameters
    }
    check(**fixtures)


def test_every_check_a_row_names_is_a_function_and_every_function_has_a_row():
    assert set(CHECKS) == {row["check"] for row in OUTCOMES if "check" in row}


def test_every_extraction_failure_code_has_a_rule_in_the_prompt():
    """Self-completing against ``IssueCode``: a new way to refuse an extraction
    fails here until a row pins the rule that prevents it, or until
    :data:`UNWORDED_ISSUE_CODES` says no wording prevents it."""
    worded = {code for row in OUTCOMES for code in row.get("issue_codes", ())}
    assert not worded & set(UNWORDED_ISSUE_CODES)
    assert worded | set(UNWORDED_ISSUE_CODES) == set(get_args(IssueCode))


# --- Extraction ---------------------------------------------------------------


@decides
def a_payload_the_schema_refuses_returns_no_model():
    model, issues = parse_and_validate({"processes": [{"id": "process:x"}]})

    assert model is None
    assert issues and {issue.code for issue in issues} == {"schema"}


@decides
def a_failed_extraction_gets_one_repair_pass_then_a_rejection(pipeline):  # noqa: F811
    assert (
        routed_targets(pipeline, graph.VALIDATE_NODE)[graph.ROUTE_INVALID]
        == graph.REPAIR_NODE
    )
    assert graph.REVALIDATE_NODE in routed_targets(pipeline, graph.REPAIR_NODE).values()
    assert (
        routed_targets(pipeline, graph.REVALIDATE_NODE)[graph.ROUTE_INVALID]
        == graph.REJECT_NODE
    )
    assert routed_targets(pipeline, graph.REJECT_NODE) == {}


@decides
def two_elements_under_one_id_are_refused():
    model = valid_model()
    model.processes[0].id = "entity:customer"
    model.processes[0].name = "Customer"

    assert "duplicate-id" in codes(validate(model))


@decides
def a_ref_two_elements_claim_binds_to_neither():
    payload = compact_fixture()
    payload["processes"].append(dict(payload["processes"][0], ref="p:app"))

    _, issues = parse_extraction(payload, COMPACT_FORMAT)

    assert "duplicate-ref" in codes(issues)
    assert "invalid-reference" in codes(issues)


@decides
def an_id_that_differs_from_its_name_is_replaced_by_the_slug():
    model = valid_model()
    model.processes[0].name = "Web App Frontend Service"
    _, reported = parse_and_validate(model.model_dump())
    normalized, issues = parse_and_validate(model.model_dump(), normalize_ids=True)

    assert "id-mismatch" in codes(reported)
    assert issues == []
    assert normalized is not None
    assert normalized.processes[0].id == "process:web-app-frontend-service"
    assert "process:web-app>" not in normalized.model_dump_json()


@decides
def a_name_with_no_ascii_letter_or_digit_has_no_id():
    for name in ("é", "１", "数据库"):
        with pytest.raises(ValueError):
            normalize_name(name)
    data = valid_model().model_dump(mode="json")
    data["trust_boundaries"].append(
        {"id": "process:ghost", "name": "数据库", "kind": "other"}
    )

    _, issues = parse_and_validate(data, normalize_ids=True)

    assert [(issue.code, issue.field) for issue in issues] == [("empty-slug", "name")]


@decides
def a_flow_to_an_element_the_model_lacks_is_refused():
    model = valid_model()
    model.data_flows[1].destination = "store:missing-db"

    issues = [issue for issue in validate(model) if issue.code == "invalid-reference"]

    assert any(issue.field == "destination" for issue in issues)


@decides
def a_model_with_no_trust_zone_is_refused():
    model = valid_model()
    model.trust_boundaries = []

    (issue,) = [one for one in validate(model) if one.code == "no-trust-zones"]
    assert "no Trust Boundary" in issue.message
    assert validate(valid_model()) == []


@decides
def an_unknown_asset_tag_is_dropped_as_no_tag():
    model = valid_model()
    model.data_stores[0].assets = ["", " Unknown ", "pii"]

    normalized, issues = parse_and_validate(model.model_dump(), normalize_ids=True)

    assert "illegal-asset-tag" not in codes(issues)
    assert normalized is not None
    assert normalized.data_stores[0].assets == ["pii"]


@decides
def a_tag_outside_the_vocabulary_is_refused():
    model = valid_model()
    model.data_stores[0].assets = ["crown-jewels"]

    assert "illegal-asset-tag" in codes(validate(model))


def _citing(excerpt: str, label: str = "Kickoff call"):
    """One element cites ``excerpt`` under ``label``; every other one cites a
    quote the source holds."""
    return TestCitationsResolve().model_citing(label, excerpt)


@decides
def an_element_with_no_verbatim_excerpt_is_refused():
    assert validate(_citing("a quote"), sources=SOURCES) == []
    assert codes(validate(_citing("", label=""), sources=SOURCES)) == [
        "missing-citation"
    ]
    assert codes(validate(_citing("words nobody submitted"), sources=SOURCES)) == [
        "unverifiable-excerpt"
    ]


@decides
def an_assumption_on_an_unknown_attribute_is_refused():
    model = valid_model()
    model.processes[0].exposure = "unknown"

    (issue,) = validate(model)
    assert issue.code == "assumption-on-unknown"
    assert (issue.element_id, issue.field) == ("process:web-app", "assumptions")
    assert "'exposure'" in issue.message


@decides
def an_empty_control_is_refused():
    model = valid_model()
    model.data_flows[0].authentication = ""

    assert "blank-control" in codes(validate(model))


@decides
def a_control_that_opens_with_a_negation_is_refused():
    model = valid_model()
    model.data_flows[0].authentication = "no MFA on the password login"

    assert "ambiguous-control" in codes(validate(model))


@decides
def an_omitted_compact_attribute_ends_the_job_with_no_repair(pipeline):  # noqa: F811
    for owner, field in (("processes", "exposure"), ("data_flows", "operations")):
        payload = compact_fixture()
        del payload[owner][0][field]
        event = graph.validate_extraction(
            FakeContext(), KEYS, payload, None, COMPACT_FORMAT
        )
        assert event.actions.route == graph.ROUTE_UNCONVERTIBLE, field
    assert (
        routed_targets(pipeline, graph.VALIDATE_NODE)[graph.ROUTE_UNCONVERTIBLE]
        == graph.REJECT_NODE
    )


# --- Lane output -------------------------------------------------------------


@decides
def a_repeated_threat_id_keeps_the_first_draft_and_drops_the_rest():
    drafts = {
        "spoofing": [
            sample_draft("S-01", title="first"),
            sample_draft("S-01", title="second"),
        ]
    }

    joined = join_drafts(drafts, PACKAGES["stride"], valid_model())

    assert [draft.title for draft in joined.drafts] == ["first"]
    (mark,) = joined.marks.dropped_claims
    assert (mark.claim_id, mark.title) == ("S-01", "second")


@decides
def a_threat_that_names_only_zones_is_dropped():
    record = PACKAGES["stride"].record
    data = {
        **sample_draft().model_dump(),
        "affected_element_ids": ["boundary:internet"],
    }

    assert record.untargeted(["boundary:internet"])
    assert record.untargeted(["process:web-app"]) == ""
    with pytest.raises(ValidationError, match="names only trust zones"):
        record.model_validate(data)


@decides
def a_repeated_requirement_keeps_the_first_draft_and_drops_the_rest():
    asvs = PACKAGES["asvs"]
    lane = asvs.lanes[0]

    def draft(title: str):
        return asvs.record(
            id="v5.0.0-16.1.1",
            framework="asvs",
            framework_version=asvs.version,
            chapter=lane,
            title=title,
            description="d",
            affected_element_ids=["process:web-app"],
            grounds=sample_draft().grounds,
            direction="question",
        )

    joined = join_drafts({lane: [draft("first"), draft("second")]}, asvs, valid_model())

    assert [draft.title for draft in joined.drafts] == ["first"]
    (mark,) = joined.marks.dropped_claims
    assert (mark.claim_id, mark.title) == ("v5.0.0-16.1.1", "second")


@decides
def a_stated_band_that_contradicts_the_matrix_is_refused():
    with pytest.raises(ValidationError, match="contradicts the matrix"):
        Severity(likelihood="high", impact="high", level="low", justification="j")

    assert Severity(likelihood="high", impact="high", justification="j").level == (
        "critical"
    )


@decides
def a_gap_on_absent_elements_alone_is_dropped():
    unstated = RequirementProposal(
        title="t",
        description="d",
        requirement="2.1",
        direction="gap",
        needs_evidence="",
        absent_elements=["log-store"],
    )

    assert PACKAGES["asvs"].record.unsupported(unstated)


@decides
def a_disputed_row_is_open_whichever_value_it_states():
    absent, required = row(), row(value="required")
    held = assertions(absent, required)
    catalog = evidence_catalog(valid_model(), held)

    for side in (absent, required):
        assert catalog[assertion_id(side)].kind == "unknown-assertion"
    assert "the sources disagree" in ground_gloss(catalog[assertion_id(required)], held)


@decides
def code_records_the_parent_from_a_present_child():
    facts = resolve(_facts(turn_server="present"))

    assert facts["webrtc"].state == "present"
    assert facts["webrtc"].derived_from == "turn-server"


# --- Critic ------------------------------------------------------------------


@decides
def a_field_the_ruling_does_not_carry_refuses_the_whole_answer():
    sound = sample_ruling("S-01").model_dump(mode="json")
    surplus = {**sample_ruling("S-02").model_dump(mode="json"), "surplus": "x"}
    with pytest.raises(ValidationError):
        rulings_of([sound, surplus], schemas_for("stride"))

    for name in PACKAGES:
        with pytest.raises(ValidationError) as refused:
            rulings_of([{"surplus": "x"}], schemas_for(name))
        assert [
            error["loc"][-1]
            for error in refused.value.errors()
            if error["type"] == "extra_forbidden"
        ] == ["surplus"], name


@decides
def a_ruling_whose_fields_do_not_fit_comes_back_to_the_critic(pipeline):  # noqa: F811
    unreasoned = sample_ruling(verdict=ProposedVerdict(rejected_because="lane"))

    critic_round = CriticRound.of(
        "stride", valid_model(), [sample_draft("S-01")], AnalysisMarks(), None
    )

    outcome = critic_round.first([unreasoned.model_dump(mode="json")])

    assert isinstance(outcome, Reask)
    assert [problem.kind for problem in outcome.problems] == ["verdict-shape"]
    assert routed_targets(pipeline, ROUTER_NODE)[graph.ROUTE_REVISE] == RECRITIC_NODE
