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


def test_the_signed_file_loads_and_applies(case):  # noqa: F811
    loaded = run.signed_answers([case], DRAFT.parent)
    assert loaded[case.id].signed_by == "mstarks01"
    assert loaded[case.id].drafted_by == "agent"


def test_an_unsigned_copy_is_refused(case, tmp_path):  # noqa: F811
    raw = json.loads(DRAFT.read_text(encoding="utf-8"))
    raw["signed_by"] = None
    (tmp_path / DRAFT.name).write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(modes.EvalRunError, match="no signed answers"):
        run.signed_answers([case], tmp_path)


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


class TestAnswersWithinRounds:
    """An owner who stops after N rounds answers only what those rounds ask (#1289)."""

    def flows(self, answers):
        return [answer.key[0].rsplit(">", 1)[-1] for answer in answers]

    def test_the_first_round_asks_every_withheld_fact(
        self,
        case,  # noqa: F811
    ):
        """Ranked by score alone, round 1 asked one of the three and round 2
        the rest (QA-2026-09-26-03-E27); ranked per choice, round 1 asks all
        three (QA-2026-09-26-03-E33)."""
        from evals.harness.withheld import answers_within_rounds

        signed = load_answer_file(DRAFT)
        first = answers_within_rounds(case, signed, 1, ("stride",))
        assert set(first) == set(signed.answers)
        assert "submit-order" in self.flows(first)

    def test_answers_keep_the_file_s_own_order_and_values(self, case):  # noqa: F811
        from evals.harness.withheld import answers_within_rounds

        signed = load_answer_file(DRAFT)
        every = answers_within_rounds(case, signed, 10, ("stride",))
        assert every == signed.answers

    def test_the_option_narrows_the_answered_mode_only(self, capsys):
        argv = ["run", "--mode", "withheld", "--case", "01-payments-checkout"]
        assert run.main([*argv, "--answer-rounds", "1"]) == 1
        assert "narrows the answered mode only" in capsys.readouterr().err
