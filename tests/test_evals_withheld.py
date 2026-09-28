"""The withheld-sentence test's file, applied to case 01 (#1225).

Held against the repository's own draft for case 01, so the file that a paid
run would read is the file these checks pass or refuse.
"""

from __future__ import annotations

import json

import pytest

from evals.harness import modes, run
from evals.harness.provenance import REPO_ROOT
from evals.harness.withheld import load_answer_file, withheld_case
from tests.test_evals_modes import case  # noqa: F401  (fixture)

DRAFT = REPO_ROOT / "evals" / "answers" / "01-payments-checkout.json"
NO_MFA = "we have not rolled out MFA"


def signed_copy(tmp_path, change=lambda raw: None):
    raw = json.loads(DRAFT.read_text(encoding="utf-8"))
    raw["signed_by"] = "a test"
    change(raw)
    path = tmp_path / DRAFT.name
    path.write_text(json.dumps(raw), encoding="utf-8")
    return load_answer_file(path)


def test_the_draft_is_unsigned_and_so_is_refused(case):  # noqa: F811
    assert load_answer_file(DRAFT) is None
    with pytest.raises(modes.EvalRunError, match="no signed answers"):
        run.signed_answers([case], DRAFT.parent)


def test_the_facts_leave_the_sources_and_the_model(case, tmp_path):  # noqa: F811
    withheld = withheld_case(case, signed_copy(tmp_path))
    assert NO_MFA in case.sources[0].text
    assert NO_MFA not in withheld.sources[0].text
    flow = withheld.model.get("flow:entity:shopper>process:storefront-api>place-order")
    assert flow is not None and flow.authentication == "unknown"


def test_each_answer_restates_a_field_the_test_set_back(case, tmp_path):  # noqa: F811
    """The answers give back exactly the blessed values the test took out."""
    answer_file = signed_copy(tmp_path)
    for answer in answer_file.answers:
        element_id, attribute, *_ = answer.key
        assert getattr(case.model.get(element_id), attribute) == answer.value
        assert (element_id, attribute, "unknown") in answer_file.withheld_fields


def test_a_fact_left_in_an_excerpt_is_refused(case, tmp_path):  # noqa: F811
    def keep_excerpts(raw):
        raw["withheld"]["model"] = [
            entry
            for entry in raw["withheld"]["model"]
            if entry["field"] != "source_excerpt"
        ]

    with pytest.raises(modes.EvalRunError, match="still in the case"):
        withheld_case(case, signed_copy(tmp_path, keep_excerpts))


def test_a_phrase_that_is_not_in_the_sources_is_refused(case, tmp_path):  # noqa: F811
    def misspell(raw):
        raw["withheld"]["source"][0]["text"] = "Shoppers sign in with a passkey."

    with pytest.raises(modes.EvalRunError, match="occurs 0 times"):
        withheld_case(case, signed_copy(tmp_path, misspell))


def test_each_target_is_a_reference_claim_of_the_case(case):  # noqa: F811
    """A target names a claim by its verb and elements, never by its index."""
    raw = json.loads(DRAFT.read_text(encoding="utf-8"))
    references = {
        (claim.verb, tuple(claim.affected_element_ids), claim.tier)
        for claim in case.references["stride"]
    }
    withheld = len(raw["withheld"]["source"])
    for target in raw["targets"]:
        key = (target["verb"], tuple(target["affected_element_ids"]), target["tier"])
        assert key in references, target
        assert 1 <= target["withheld"] <= withheld


def test_no_description_still_restates_a_withheld_control(case, tmp_path):  # noqa: F811
    withheld = withheld_case(case, signed_copy(tmp_path))
    for element in withheld.model.elements():
        assert "authenticated session" not in element.description
        assert "Terminates shopper sessions" not in element.description
