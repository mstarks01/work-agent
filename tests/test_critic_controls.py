"""The critic's control step: what a draft is shown of the job's Controls, how
the review seam checks the rulings on them, and what the run keeps (ADR 0126).
"""

from __future__ import annotations

from typing import get_args

import pytest
from pydantic import ValidationError

from analysis_service.claims import (
    AnalysisMarks,
    ControlRowRef,
    CriticStep,
    ProposedVerdict,
    QuoteCandidate,
    Ruling,
    Verdict,
)
from analysis_service.controls import Control, ControlDocument
from analysis_service.critic import review_issues
from analysis_service.critic_round import Accepted, CriticRound, Reask
from analysis_service.fact_answers import FactAnswer
from analysis_service.fan_in import fan_in, returned_to_deferred
from analysis_service.frameworks import PACKAGES
from analysis_service.frameworks.asvs.record import (
    AsvsAnalysis,
    DraftRequirementRuling,
    RequirementProposal,
    RequirementProposals,
)
from analysis_service.job_controls import (
    AppliedControl,
    ControlFact,
    ControlFacts,
    CopiedControl,
    RowRuling,
    applied_controls,
    controls_section,
    refuse_disagreement,
    row_outcome,
    row_rulings,
    unreached_rows,
)
from analysis_service.sources import text_digest
from analysis_service.system_model import SystemModel
from tests.factories import sample_analysis, sample_draft, sample_threat, valid_model

DB_CONTROL = "control-" + "d" * 32
APP_CONTROL = "control-" + "a" * 32
ZONE_CONTROL = "control-" + "c" * 32
EVIDENCE_ID = "evidence-" + "e" * 32
RDS_TF = 'resource "aws_db_instance" "orders" {\n  storage_encrypted = true\n}\n'


def _row(unit: str, framework: str = "asvs") -> dict:
    version = "5.0.0" if framework == "asvs" else "1.0"
    return {"framework": framework, "unit": unit, "version": version}


def _copied(control_id: str, scope: dict, rows: list[dict]) -> CopiedControl:
    evidence = {
        "id": EVIDENCE_ID,
        "name": "rds.tf",
        "class": "config",
        "sha256": text_digest(RDS_TF),
        "size_bytes": len(RDS_TF),
    }
    document = ControlDocument.model_validate(
        {
            "id": control_id,
            "name": f"Control {control_id[-1]}",
            "scope": scope,
            "facts": [],
            "rows": rows,
            "evidence": [evidence],
        }
    )
    return CopiedControl.of(
        Control.create(owner_id="alice", document=document), {EVIDENCE_ID: RDS_TF}
    )


def _db(*rows: dict) -> CopiedControl:
    return _copied(DB_CONTROL, {"type": "store", "name": "Orders DB"}, list(rows))


def _app(*rows: dict) -> CopiedControl:
    return _copied(APP_CONTROL, {"type": "application", "name": ""}, list(rows))


def _zone(*rows: dict) -> CopiedControl:
    scope = {"type": "boundary", "name": "Internal Network"}
    return _copied(ZONE_CONTROL, scope, list(rows))


def _applied(*copies: CopiedControl, model: SystemModel | None = None):
    return applied_controls(copies, None, model or valid_model())


def _asvs_draft(requirement: str, *elements: str) -> DraftRequirementRuling:
    chapter, section, item = requirement.removeprefix("V").split(".")
    lane = {"11": "cryptography", "6": "authentication"}[chapter]
    return DraftRequirementRuling.model_validate(
        {
            **sample_draft("S-01").model_dump(
                exclude={
                    "category",
                    "verb",
                    "attacker_position",
                    "severity",
                    "mitigations",
                    "affected_element_ids",
                }
            ),
            "id": f"v5.0.0-{chapter}.{section}.{item}",
            "chapter": lane,
            "affected_element_ids": list(elements),
        }
    )


def _view(drafts, controls: list[AppliedControl], framework="asvs") -> list[dict]:
    critic_round = CriticRound.of(
        framework, valid_model(), drafts, AnalysisMarks(), None, controls
    )
    return critic_round.view()


def _shown(view: dict) -> set[tuple[str, str]]:
    return {
        (entry["control"], row["unit"])
        for entry in view.get("controls", [])
        for row in entry["rows"]
    }


class TestTheControlsKey:
    """Each draft is shown the applied Controls that reach its element and
    the rows on its unit, at the unit's grain or a grain that holds it."""

    def test_an_element_scope_on_the_drafts_element_is_shown(self):
        [view] = _view(
            [_asvs_draft("V11.2.1", "store:orders-db")], _applied(_db(_row("V11.2.1")))
        )

        assert _shown(view) == {(DB_CONTROL, "V11.2.1")}

    def test_an_application_scope_reaches_a_draft_on_no_element(self):
        [view] = _view([_asvs_draft("V11.2.1")], _applied(_app(_row("V11.2.1"))))

        assert _shown(view) == {(APP_CONTROL, "V11.2.1")}

    def test_a_zone_scope_reaches_an_element_it_covers(self):
        [view] = _view(
            [_asvs_draft("V11.2.1", "store:orders-db")],
            _applied(_zone(_row("V11.2.1"))),
        )

        assert _shown(view) == {(ZONE_CONTROL, "V11.2.1")}

    def test_a_row_at_a_containing_grain_is_shown(self):
        [view] = _view(
            [_asvs_draft("V11.2.1", "store:orders-db")],
            _applied(_db(_row("V11"), _row("V11.2"))),
        )

        assert _shown(view) == {(DB_CONTROL, "V11"), (DB_CONTROL, "V11.2")}

    def test_a_row_on_another_unit_is_not_shown(self):
        [view] = _view(
            [_asvs_draft("V11.2.1", "store:orders-db")],
            _applied(_db(_row("V11.2.2"), _row("V6"))),
        )

        assert "controls" not in view

    def test_a_control_on_another_element_is_not_shown(self):
        [view] = _view(
            [_asvs_draft("V11.2.1", "process:web-app")], _applied(_db(_row("V11")))
        )

        assert "controls" not in view

    def test_a_row_of_another_framework_is_not_shown(self):
        [view] = _view(
            [_asvs_draft("V11.2.1", "store:orders-db")],
            _applied(_db(_row("information-disclosure", "stride"))),
        )

        assert "controls" not in view

    def test_a_stride_draft_is_shown_a_row_on_its_lane(self):
        draft = sample_draft("I-01", "information-disclosure")
        rows = (_row("information-disclosure", "stride"), _row("spoofing", "stride"))
        [view] = _view([draft], _applied(_app(*rows)), framework="stride")

        assert _shown(view) == {(APP_CONTROL, "information-disclosure")}

    def test_each_excerpt_is_the_services_copy_with_its_class_and_digest(self):
        [view] = _view(
            [_asvs_draft("V11.2.1", "store:orders-db")], _applied(_db(_row("V11")))
        )

        [entry] = view["controls"]
        assert entry["name"] == "Control d"
        assert entry["evidence"] == [
            {
                "label": "rds.tf",
                "class": "config",
                "sha256": text_digest(RDS_TF),
                "excerpt": RDS_TF,
            }
        ]

    def test_the_re_ask_is_shown_the_same_key(self):
        draft = _asvs_draft("V11.2.1", "store:orders-db")
        controls = _applied(_db(_row("V11")))
        critic_round = CriticRound.of(
            "asvs", valid_model(), [draft], AnalysisMarks(), None, controls
        )

        assert critic_round.view(only={draft.id}) == critic_round.view()


DRAFT = _asvs_draft("V11.2.1", "store:orders-db")
ROWS = (_row("V11"), _row("V11.2.1"))


def _round(*copies: CopiedControl) -> CriticRound:
    return CriticRound.of(
        "asvs", valid_model(), [DRAFT], AnalysisMarks(), None, _applied(*copies)
    )


def _ruling(**verdict) -> dict:
    return {"id": DRAFT.id, "verdict": {"reason": "", **verdict}}


def _refused(unit: str, control: str = DB_CONTROL) -> dict:
    return {"control": control, "unit": unit, "reason": "It names no key store."}


APPLIED = {
    "rejected_because": "control",
    "control_row": {"control": DB_CONTROL, "unit": "V11"},
    "reason": "rds.tf: storage_encrypted = true",
}


def _problems(*copies: CopiedControl, **verdict) -> list[str]:
    outcome = _round(*copies).first([_ruling(**verdict)])
    assert isinstance(outcome, Reask)
    return outcome.messages


class TestTheReviewSeam:
    """The seam refuses a row the draft was not shown, a row on another step,
    and a ruling that leaves a shown row unanswered."""

    def test_an_applied_row_and_a_refused_row_are_accepted(self):
        outcome = _round(_db(*ROWS)).first(
            [_ruling(**APPLIED, refused_controls=[_refused("V11.2.1")])]
        )

        assert isinstance(outcome, Accepted)
        [claim] = outcome.rejected
        assert claim.verdict.control_row.unit == "V11"
        assert claim.rules_on_unit()

    def test_a_control_row_on_a_row_not_shown_is_refused(self):
        verdict = {**APPLIED, "control_row": {"control": DB_CONTROL, "unit": "V6"}}
        problems = _problems(
            _db(*ROWS),
            **verdict,
            refused_controls=[_refused("V11"), _refused("V11.2.1")],
        )

        assert any("V6" in problem and "not shown" in problem for problem in problems)

    def test_a_control_row_on_a_kept_draft_is_refused(self):
        problems = _problems(
            _db(_row("V11")), control_row={"control": DB_CONTROL, "unit": "V11"}
        )

        assert any("control_row" in problem for problem in problems)

    @pytest.mark.parametrize("step", ["evidence", "reasoning", "lane"])
    def test_a_draft_rejected_before_the_control_step_loses_its_rows(self, step):
        # The draft stopped before the control step, so code drops each row it
        # names, a row it was not shown included, and the seam asks nothing.
        outcome = _round(_db(*ROWS)).first(
            [
                _ruling(
                    rejected_because=step,
                    reason="It reads an obligation V11.2.1 does not carry.",
                    control_row={"control": DB_CONTROL, "unit": "V11"},
                    refused_controls=[_refused("V11.2.1"), _refused("V6")],
                )
            ]
        )

        assert isinstance(outcome, Accepted)
        [claim] = outcome.rejected
        assert claim.verdict.rejected_because == step
        assert claim.verdict.control_row is None
        assert claim.verdict.refused_controls == []

    def test_a_control_rejection_with_no_row_is_refused(self):
        problems = _problems(
            _db(_row("V11")), rejected_because="control", reason="rds.tf covers it"
        )

        assert any("control_row" in problem for problem in problems)

    def test_a_ruling_that_leaves_a_shown_row_unanswered_is_sent_back(self):
        problems = _problems(_db(*ROWS), **APPLIED)

        assert any("V11.2.1" in problem and "answer" in problem for problem in problems)

    def test_a_kept_draft_answers_every_shown_row(self):
        problems = _problems(_db(*ROWS), refused_controls=[_refused("V11")])

        assert any("V11.2.1" in problem for problem in problems)

    def test_a_refused_row_the_draft_was_not_shown_is_sent_back(self):
        problems = _problems(
            _db(_row("V11")),
            refused_controls=[_refused("V11"), _refused("V11", control=APP_CONTROL)],
        )

        assert any(APP_CONTROL in problem for problem in problems)

    def test_a_draft_shown_no_row_names_none(self):
        problems = _problems(refused_controls=[_refused("V11")])

        assert any("not shown" in problem for problem in problems)

    def test_a_draft_rejected_at_an_earlier_step_answers_no_row(self):
        outcome = _round(_db(*ROWS)).first(
            [_ruling(rejected_because="reasoning", reason="It misreads V11.2.1.")]
        )

        assert isinstance(outcome, Accepted)


class TestTheRowOutcome:
    """Each row's rulings come from the report's verdicts, and one reader
    reads the row's outcome off them."""

    def test_each_row_lists_its_rulings_from_the_claims(self):
        outcome = _round(_db(*ROWS)).first(
            [_ruling(**APPLIED, refused_controls=[_refused("V11.2.1")])]
        )

        rulings = row_rulings([*outcome.claims, *outcome.rejected], [])

        assert rulings == {
            ControlRowRef(control=DB_CONTROL, unit="V11"): [
                RowRuling(claim=DRAFT.id, ruling="applied", reason=APPLIED["reason"])
            ],
            ControlRowRef(control=DB_CONTROL, unit="V11.2.1"): [
                RowRuling(
                    claim=DRAFT.id, ruling="refused", reason="It names no key store."
                )
            ],
        }

    def test_a_row_that_applied_once_is_applied(self):
        rulings = [
            RowRuling(claim="a", ruling="refused", reason="no"),
            RowRuling(claim="b", ruling="applied", reason="yes"),
        ]

        assert row_outcome(rulings, unreached=False) == "applied"

    def test_a_row_refused_on_every_ruling_is_refused(self):
        rulings = [
            RowRuling(claim="a", ruling="refused", reason="no"),
            RowRuling(claim="b", ruling="refused", reason="no"),
        ]

        assert row_outcome(rulings, unreached=False) == "refused"

    def test_a_row_with_no_ruling_found_nothing(self):
        assert row_outcome([], unreached=False) == "no-finding"

    def test_a_row_with_no_ruling_and_a_finding_it_missed_is_not_reached(self):
        assert row_outcome([], unreached=True) == "not-reached"

    def test_a_ruling_outweighs_a_finding_the_row_missed(self):
        rulings = [RowRuling(claim="a", ruling="applied", reason="yes")]

        assert row_outcome(rulings, unreached=True) == "applied"


class TestTheUnreachedRows:
    """A row misses a finding on its unit that the Control's scope does not
    reach, and the report says so in place of "no finding"."""

    def test_a_finding_on_no_element_misses_a_part_scope(self):
        [control] = _applied(_db(_row("V11.2.1")))

        unreached = unreached_rows([_asvs_draft("V11.2.1")], PACKAGES["asvs"], control)

        assert unreached == {"V11.2.1"}

    def test_a_finding_on_another_element_misses_a_part_scope(self):
        [control] = _applied(_db(_row("V11.2")))

        drafts = [_asvs_draft("V11.2.1", "process:web-app")]

        assert unreached_rows(drafts, PACKAGES["asvs"], control) == {"V11.2"}

    def test_a_finding_the_scope_reaches_is_not_missed(self):
        [control] = _applied(_db(_row("V11.2.1")))

        drafts = [_asvs_draft("V11.2.1", "store:orders-db")]

        assert unreached_rows(drafts, PACKAGES["asvs"], control) == frozenset()

    def test_the_application_reaches_a_finding_on_no_element(self):
        [control] = _applied(_app(_row("V11.2.1")))

        drafts = [_asvs_draft("V11.2.1")]

        assert unreached_rows(drafts, PACKAGES["asvs"], control) == frozenset()

    def test_the_report_says_a_row_that_missed_a_finding_is_not_reached(self):
        db = _db(_row("spoofing", "stride"), _row("tampering", "stride"))
        block = sample_analysis([sample_threat("spoofing")])

        [entry] = controls_section([db], None, None, valid_model(), [block])

        outcomes = {row.unit: (row.outcome, row.meaning) for row in entry.rows}
        assert outcomes["spoofing"][0] == "not-reached"
        assert outcomes["spoofing"][1].startswith("Not reached")
        assert outcomes["tampering"][0] == "no-finding"

    def test_the_report_leaves_out_a_row_of_a_framework_not_run(self):
        db = _db(_row("spoofing", "stride"), _row("V11.2.1"))
        block = sample_analysis([sample_threat("spoofing")])

        [entry] = controls_section([db], None, None, valid_model(), [block])

        assert [row.unit for row in entry.rows] == ["spoofing"]

    def test_a_finding_on_another_unit_misses_no_row(self):
        [control] = _applied(_db(_row("V11.2.1")))

        drafts = [_asvs_draft("V6.2.1")]

        assert unreached_rows(drafts, PACKAGES["asvs"], control) == frozenset()


def _config_proposal() -> RequirementProposals:
    proposal = RequirementProposal(
        title="Orders DB encryption is not stated",
        description="Whether the store encrypts at rest is a deployed setting.",
        requirement="2.1",
        needs_evidence="config",
        direction="question",
        affected_element_ids=["store:orders-db"],
        quotes=[QuoteCandidate(text="orders are stored in Postgres", source_label="d")],
    )
    return RequirementProposals(claims=[proposal])


class TestTheUnDefer:
    """A row un-defers its unit, and a draft the critic keeps against every
    row goes back to the deferred set with its kind."""

    def _merged(self, covered):
        return fan_in(
            {"cryptography": _config_proposal()},
            PACKAGES["asvs"],
            valid_model(),
            {},
            covered=covered,
        )

    def test_a_covered_config_proposal_becomes_a_draft(self):
        merged = self._merged({"V11.2.1"})

        assert [draft.id for draft in merged.drafts] == ["v5.0.0-11.2.1"]
        assert merged.deferred == {}
        assert merged.undeferred == {"V11.2.1": "config"}

    def test_with_no_row_the_proposal_stays_deferred(self):
        merged = self._merged(frozenset())

        assert merged.drafts == []
        assert merged.deferred == {"V11.2.1": "config"}
        assert merged.undeferred == {}

    def _ruled(self, verdict: dict):
        merged = self._merged({"V11.2.1"})
        [draft] = merged.drafts
        critic_round = CriticRound.of(
            "asvs",
            valid_model(),
            merged.drafts,
            merged.marks,
            None,
            _applied(_db(_row("V11.2.1"))),
        )
        outcome = critic_round.first([{"id": draft.id, "verdict": verdict}])
        assert isinstance(outcome, Accepted)
        return merged, outcome

    def test_a_draft_kept_against_every_row_goes_back_with_its_kind(self):
        merged, outcome = self._ruled(
            {"refused_controls": [_refused("V11.2.1")], "reason": "open"}
        )

        claims, returned, deferred = returned_to_deferred(
            outcome.claims, outcome.rejected, merged.undeferred, PACKAGES["asvs"].record
        )

        assert claims == []
        assert deferred == {"V11.2.1": "config"}
        [entry] = returned
        assert (entry.claim_id, entry.unit, entry.needs) == (
            "v5.0.0-11.2.1",
            "V11.2.1",
            "config",
        )
        assert (
            row_outcome(row_rulings(claims, returned)[ROW], unreached=False)
            == "refused"
        )
        entries = AsvsAnalysis.scope_entries(
            lanes=["cryptography"],
            claims=[*claims, *outcome.rejected],
            options={"level": 3},
            deferred=deferred,
        )
        [entry] = [entry for entry in entries if entry.unit == "V11.2.1"]
        assert (entry.state, entry.needs) == ("needs-other-evidence", "config")

    def test_a_draft_a_row_covers_stays_rejected_and_rules_its_unit(self):
        merged, outcome = self._ruled(
            {**APPLIED, "control_row": {"control": DB_CONTROL, "unit": "V11.2.1"}}
        )

        claims, returned, deferred = returned_to_deferred(
            outcome.claims, outcome.rejected, merged.undeferred, PACKAGES["asvs"].record
        )

        assert (claims, returned, deferred) == ([], [], {})
        assert (
            row_outcome(row_rulings(outcome.rejected, returned)[ROW], unreached=False)
            == "applied"
        )
        entries = AsvsAnalysis.scope_entries(
            lanes=["cryptography"],
            claims=outcome.rejected,
            options={"level": 3},
            deferred=deferred,
        )
        assert "V11.2.1" not in {entry.unit for entry in entries}

    def test_a_draft_rejected_at_step_one_leaves_its_unit_deferred(self):
        """A rejection that does not rule on the unit leaves it open, and the
        job still carries no evidence of its kind."""
        merged, outcome = self._ruled(
            {"rejected_because": "reasoning", "reason": "It misreads V11.2.1."}
        )

        claims, returned, deferred = returned_to_deferred(
            outcome.claims, outcome.rejected, merged.undeferred, PACKAGES["asvs"].record
        )

        assert (claims, returned) == ([], [])
        assert deferred == {"V11.2.1": "config"}
        entries = AsvsAnalysis.scope_entries(
            lanes=["cryptography"],
            claims=outcome.rejected,
            options={"level": 3},
            deferred=deferred,
        )
        [entry] = [entry for entry in entries if entry.unit == "V11.2.1"]
        assert (entry.state, entry.needs) == ("needs-other-evidence", "config")


ROW = ControlRowRef(control=DB_CONTROL, unit="V11.2.1")


@pytest.mark.parametrize("step", [None, *get_args(CriticStep)])
@pytest.mark.parametrize("names_a_row", [False, True])
def test_the_verdict_and_the_seam_hold_one_row_rule(step, names_a_row):
    """``Verdict`` and the review seam both read "a control_row exactly on a
    control rejection", so each combination is put to both. The verdict is
    built from the ruling the seam completed, as assembly builds it."""
    fields = {
        "reason": "rds.tf: storage_encrypted = true",
        "rejected_because": step,
        "control_row": {"control": DB_CONTROL, "unit": "V11"} if names_a_row else None,
        # The one shown row is answered either way, so only the row rule speaks.
        "refused_controls": [] if names_a_row else [_refused("V11")],
    }
    seam = review_issues(
        [DRAFT],
        [Ruling(id=DRAFT.id, verdict=ProposedVerdict(**fields))],
        valid_model(),
        {DRAFT.id: frozenset({ControlRowRef(control=DB_CONTROL, unit="V11")})},
    )
    [completed] = seam.rulings
    try:
        Verdict(
            status="rejected" if step else "confirmed",
            **completed.verdict.model_dump(),
        )
    except ValidationError:
        verdict_refuses = True
    else:
        verdict_refuses = False
    seam_refuses = any(problem.kind == "control-row" for problem in seam.problems)

    assert seam_refuses is verdict_refuses


def test_a_control_whose_fact_its_part_cannot_take_cannot_apply():
    # The answer check refuses a question about an element the model lacks,
    # and the refusal names the Control.
    wrong = FactAnswer(
        key=("store:nowhere", "", "", "", "audit-evidence", ""),
        facets={"records-actor": "yes"},
    )
    written = ControlFacts(
        control=DB_CONTROL,
        name="Audit",
        facts=[ControlFact(answer=wrong, state="written")],
    )
    with pytest.raises(ValueError, match="The Control “Audit” cannot apply:"):
        refuse_disagreement([written], valid_model(), None)
