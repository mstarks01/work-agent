"""The must-find scorecard: one row per reference, one status, and stage changes.

The stage tests drive real drafts of a committed Baseline through the scorer,
so a duplicate and a last draft are what the matcher makes of them.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace
from itertools import pairwise
from pathlib import Path

import pytest

from analysis_service.claims import FrameworkName
from evals.harness import fan_in_accounting, roster, run, scorecard
from evals.harness.artifact import RepoCommit, ScoredAgainst, load_artifact
from evals.harness.assembly import AssemblySweep, replay_artifact
from evals.harness.bundle import runs_from_reports, stride_block
from evals.harness.critic_yield import OMITTED, CriticAudit, fate_by_claim
from evals.harness.fan_in_accounting import FanInAccount, FanInSweep
from evals.harness.identity import SubsetVerbIdentity
from evals.harness.ledger import Ledger
from evals.harness.modes import EvalRunError
from evals.harness.reference import development_cases, flows_by_case, load_corpus
from evals.harness.run import main
from evals.harness.scorecard import (
    STAGES,
    STATUSES,
    MixedRunsError,
    ReferenceRow,
    RunIdentity,
    capture,
    case_rows,
    charged_misses,
    summarize,
)
from evals.harness.scorer import Rate, none_stated, score_case
from tests.source_tree import REPO_ROOT

BASELINE = (
    REPO_ROOT / "evals/baselines/6bff717-gpt-5.6-terra-24dda4db/mstarks01-e32ad9c5.json"
)
CASE = "02-iot-fleet-telemetry"
NO_ASSEMBLY = scorecard.AssemblyCheck(0, (), (), 0, 0)
NO_FAN_IN = scorecard.FanInCheck(0, (), 0, 0, 0, 0, 0, 0, 0, 0)
NO_CRITIC = CriticAudit.of(())


def _identity(artifact: str = "a.json") -> RunIdentity:
    commit = RepoCommit(commit="abcdef1", clean=True)
    return RunIdentity(
        artifact=artifact,
        run_commit=commit,
        run_corpus="run-corpus",
        scored=ScoredAgainst(corpus_digest="today", repo_commit=commit),
    )


@pytest.fixture(scope="module")
def archived():
    """Case 02 of the Baseline: the case, its run, and the run's matcher."""
    loaded = load_artifact(BASELINE)
    (case,) = [c for c in load_corpus(REPO_ROOT / "evals/corpus") if c.id == CASE]
    run = runs_from_reports(loaded, [case])[CASE]
    flows = flows_by_case([case], {CASE: run.report.system_model})
    return case, run, SubsetVerbIdentity(flows), flows[CASE]


def _matched(case, drafts, matcher) -> list[int]:
    score = score_case(case, drafts, matcher, Ledger())
    return [pair.reference_index for pair in score.matched]


def _rows(archived, drafted, reported, *, positions=None):
    """Rows of case 02; ``positions`` replaces the positions the drafts state."""
    case, run, matcher, flows = archived
    held = capture(
        run.report.system_model,
        flows,
        drafted,
        _matched(case, drafted, matcher),
        _matched(case, reported, matcher),
    )
    if positions is not None:
        held = replace(held, stated_positions=frozenset(positions))
    return case_rows(_identity(), case, held, None)


def _sole_match(archived):
    """A must-find reference and the one draft whose removal unmatches it."""
    case, run, matcher, _ = archived
    drafts = list(run.merged_drafts)
    must_find = {
        index for index, ref in enumerate(case.stride_claims()) if ref.must_find
    }
    for index in _matched(case, drafts, matcher):
        if index not in must_find:
            continue
        for draft in drafts:
            rest = [other for other in drafts if other is not draft]
            if index not in _matched(case, rest, matcher):
                return index, draft, rest
    raise AssertionError("no must-find has exactly one matching draft")


def _row(rows, index):
    return next(row for row in rows if row.reference_index == index)


def _change(rows):
    every = summarize(rows, NO_ASSEMBLY, NO_FAN_IN, (), NO_CRITIC).lanes[-1]
    return next(change for change in every.changes if change.after == "reported")


def test_a_rejected_duplicate_loses_no_reference(archived):
    index, draft, _ = _sole_match(archived)
    drafts = list(archived[1].merged_drafts)
    duplicate = draft.model_copy(update={"id": f"{draft.id}-copy"})

    rows = _rows(archived, [*drafts, duplicate], drafts, positions={"outside"})

    assert _row(rows, index).stages["drafted"] is True
    assert _row(rows, index).stages["reported"] is True
    assert _change(rows).lost == 0


def test_a_rejected_last_draft_is_a_reference_loss(archived):
    index, _, rest = _sole_match(archived)
    drafts = list(archived[1].merged_drafts)

    rows = _rows(archived, drafts, rest, positions={"outside"})
    change = _change(rows)

    assert _row(rows, index).stages == {
        "proposed": None,
        "drafted": True,
        "reported": False,
    }
    assert change.lost == 1 and change.gained == 0 and change.net == -1
    assert change.loss == Rate(1, change.represented_before)


def _synthetic(drafted: bool | None, reported: bool | None, **fields) -> ReferenceRow:
    row = ReferenceRow(
        run=_identity(),
        case="c",
        lane="spoofing",
        reference_index=0,
        verb="impersonate",
        elements=("entity:a",),
        ruled_position=None,
        compatibility="compatible",
        stages={"proposed": None, "drafted": drafted, "reported": reported},
        cause=None,
        lead_rules=(),
    )
    return replace(row, **fields)


def test_a_gain_and_a_loss_in_one_change_are_both_counted():
    rows = [
        _synthetic(True, False, reference_index=0),
        _synthetic(False, True, reference_index=1),
    ]

    change = _change(rows)

    assert (change.lost, change.gained, change.net) == (1, 1, 0)
    assert change.loss == Rate(1, 1)


def test_an_empty_denominator_is_undefined_and_never_zero():
    rows = [
        _synthetic(None, None, compatibility="position-unstated"),
        _synthetic(None, None, compatibility="uncaptured"),
    ]

    every = summarize(rows, NO_ASSEMBLY, NO_FAN_IN, (), NO_CRITIC).lanes[-1]

    assert Rate(0, 0).value is None
    assert every.compatible_matched == Rate(0, 0)
    assert every.represented["reported"] is None
    assert all(change.loss is None and change.net is None for change in every.changes)


def test_an_uncaptured_case_is_never_a_loss(archived):
    case = archived[0]

    rows = case_rows(_identity(), case, None, None)
    every = summarize(rows, NO_ASSEMBLY, NO_FAN_IN, (), NO_CRITIC).lanes[-1]

    assert {row.status for row in rows} == {"uncaptured"}
    assert all(set(row.stages.values()) == {None} for row in rows)
    assert every.represented == {"proposed": None, "drafted": None, "reported": None}
    assert all(change.observed == 0 for change in every.changes)


def test_the_proposed_stage_is_uncaptured_on_every_row(archived):
    drafts = list(archived[1].merged_drafts)

    rows = _rows(archived, drafts, drafts)

    assert {row.stages["proposed"] for row in rows} == {None}


def test_a_ruled_position_no_draft_states_is_position_unstated(archived):
    case = archived[0]
    drafts = list(archived[1].merged_drafts)
    ruled = {index for index in case.ruled_positions}

    unstated = _rows(archived, drafts, drafts, positions={None})
    stated = _rows(archived, drafts, drafts, positions={"outside"})

    assert ruled, "the case carries no ruled position"
    for index in ruled & {row.reference_index for row in unstated}:
        assert _row(unstated, index).compatibility == "position-unstated"
        assert _row(stated, index).compatibility == "compatible"


def test_the_archived_drafts_state_no_position(archived):
    _, run, _, flows = archived
    drafts = list(run.merged_drafts)

    held = capture(run.report.system_model, flows, drafts, (), ())

    assert held.stated_positions == {None}


def test_a_reference_the_model_no_longer_holds_is_re_keyed(archived):
    case, run, _, flows = archived
    drafts = list(run.merged_drafts)
    model = run.report.system_model
    renamed = model.model_copy(
        update={
            "external_entities": [
                entity.model_copy(update={"id": f"{entity.id}-old"})
                for entity in model.external_entities
            ]
        }
    )
    held = capture(renamed, flows, drafts, (), ())

    rows = case_rows(_identity(), case, held, None)
    entity_rows = [
        row
        for row in rows
        if any(element.startswith("entity:") for element in row.elements)
    ]

    assert entity_rows
    assert {row.compatibility for row in entity_rows} == {"re-keyed"}
    assert {row.status for row in entity_rows} == {"incompatible"}


def test_a_matched_reference_is_never_re_keyed(archived):
    """Over a model with no element, only the matched references stay readable."""
    case, run, matcher, flows = archived
    drafts = list(run.merged_drafts)
    matched = set(_matched(case, drafts, matcher))
    empty = type(run.report.system_model)()
    held = replace(
        capture(empty, flows, drafts, matched, matched),
        stated_positions=frozenset({"outside"}),
    )

    rows = case_rows(_identity(), case, held, None)

    assert matched & {row.reference_index for row in rows}
    for row in rows:
        expected = "compatible" if row.reference_index in matched else "re-keyed"
        assert row.compatibility == expected


def test_compatible_and_incompatible_rows_report_both_populations():
    rows = [
        _synthetic(True, True, reference_index=0),
        _synthetic(True, False, reference_index=1),
        _synthetic(None, None, reference_index=2, compatibility="uncaptured"),
        _synthetic(True, False, reference_index=3, compatibility="re-keyed"),
        _synthetic(False, False, reference_index=4, compatibility="position-unstated"),
    ]

    every = summarize(rows, NO_ASSEMBLY, NO_FAN_IN, (), NO_CRITIC).lanes[-1]

    assert every.by_status == {
        "matched": 1,
        "missed": 1,
        "incompatible": 2,
        "uncaptured": 1,
    }
    assert every.total == len(rows)
    assert every.compatible_matched == Rate(1, 2)
    assert every.population_matched == Rate(1, 5)


def test_the_stage_changes_are_every_pair_of_adjacent_stages():
    pairs = [(before, after) for before, after, _ in scorecard.CHANGES]

    assert pairs == list(pairwise(STAGES))


def _account(case: str, framework: FrameworkName = "stride", **fields) -> FanInAccount:
    row = FanInAccount(
        case=case,
        framework=framework,
        proposals=("p1", "p2", "p3"),
        kept=("p1",),
        dropped=(("p2", "no quote"),),
        unknown=(),
        deferred=(),
        unexplained=("p3",),
        extra=("x1",),
        quotes_verified=0,
        quotes_repaired=0,
        quotes_refused=0,
    )
    return replace(row, **fields)


def test_the_fan_in_check_keeps_incompatible_blocks_apart():
    sweep = FanInSweep(
        rows=[
            _account("a"),
            _account("b", incompatible="the replayed drafts differ"),
            _account("d", proposals=(), incompatible="the archive holds no proposals"),
            _account("c", framework="asvs"),
        ],
        unreadable=["u"],
        capture_faults=["f1", "f2"],
        writes_reports=True,
    )

    check = scorecard.fan_in_check(sweep)

    assert check.compatible_blocks == 1
    assert check.incompatible == (
        ("b", "the replayed drafts differ"),
        ("d", "the archive holds no proposals"),
    )
    assert check.unread_blocks == 1
    assert (check.inspected, check.accounted, check.unexplained) == (6, 2, 1)
    assert (check.extra, check.unavailable) == (1, 3)
    assert (check.unreadable, check.capture_faults) == (1, 2)
    assert check.accounted_rate == Rate(2, 3)


def test_the_fan_in_check_agrees_with_the_fan_in_command(capsys):
    """The check's totals are the ones ``run.py fan-in`` prints for the sweep."""
    loaded = load_artifact(BASELINE)
    cases = [
        case
        for case in development_cases(load_corpus(REPO_ROOT / "evals/corpus"))
        if case.id in loaded.cases
    ]
    sweep = fan_in_accounting.replay_artifact(loaded, cases)
    assert {row.framework for row in sweep.rows} == {"stride"}

    check = scorecard.fan_in_check(sweep)
    main(["fan-in", str(BASELINE)])
    printed = capsys.readouterr().out

    blocks = check.compatible_blocks + len(check.incompatible)
    assert (
        f"{blocks} block(s), {len(check.incompatible)} incompatible;"
        f" {check.unreadable} report(s) refused by today's Report;"
        f" {check.capture_faults} case(s) with a missing or stray capture"
    ) in printed
    assert (
        f"proposals: {check.inspected} inspected, {check.accounted} accounted,"
        f" {check.unexplained} unexplained, {check.unavailable} unavailable;"
        f" {check.extra} extra"
    ) in printed


def test_each_compatible_proposal_takes_one_fate():
    loaded = load_artifact(BASELINE)
    cases = [
        case
        for case in development_cases(load_corpus(REPO_ROOT / "evals/corpus"))
        if case.id in loaded.cases
    ]
    rows = [
        row
        for row in fan_in_accounting.replay_artifact(loaded, cases).rows
        if not row.incompatible
    ]

    assert rows
    for row in rows:
        fates = Counter(
            (
                *row.kept,
                *(claim_id for claim_id, _ in row.dropped),
                *row.unknown,
                *row.deferred,
            )
        )
        assert fates == Counter(row.proposals)


def test_an_empty_fan_in_check_reads_no_rate():
    check = scorecard.fan_in_check(
        FanInSweep(rows=[], unreadable=[], capture_faults=[], writes_reports=True)
    )

    assert check.accounted_rate == Rate(0, 0)
    assert check.accounted_rate.value is None


def test_the_assembly_check_counts_unreadable_reports_and_capture_faults():
    sweep = AssemblySweep(
        rows=[], unreadable=["a"], capture_faults=["b", "c"], writes_reports=True
    )

    check = scorecard.assembly_check(sweep)

    assert (check.unreadable, check.capture_faults) == (1, 2)
    assert (check.reproduced, check.differ, check.refused) == (0, (), ())


def test_no_rows_are_refused_as_no_run():
    with pytest.raises(ValueError, match="at least one row"):
        summarize([], NO_ASSEMBLY, NO_FAN_IN, (), NO_CRITIC)


def test_none_stated_reads_only_items_that_state_no_position():
    assert none_stated("outside", [None, None])
    assert not none_stated("outside", [None, "on-path"])
    assert not none_stated(None, [None])
    assert not none_stated("outside", [])


def test_rows_of_two_runs_are_refused():
    rows = [
        _synthetic(True, True),
        _synthetic(True, True, run=_identity("b.json")),
    ]

    with pytest.raises(MixedRunsError):
        summarize(rows, NO_ASSEMBLY, NO_FAN_IN, (), NO_CRITIC)


def test_charged_misses_leave_out_unstated_and_uncharged_rows():
    rows = [
        _synthetic(False, False, reference_index=0, cause="verb"),
        _synthetic(False, False, reference_index=1, cause="unstated"),
        _synthetic(True, True, reference_index=2),
        _synthetic(False, False, reference_index=3, cause="unled"),
    ]

    assert [row.reference_index for row in charged_misses(rows)] == [0, 3]


def test_charged_misses_leave_out_a_re_keyed_row():
    rows = [
        _synthetic(False, False, reference_index=0, cause="place"),
        _synthetic(
            False, False, reference_index=1, cause="place", compatibility="re-keyed"
        ),
        _synthetic(
            False,
            False,
            reference_index=2,
            cause="verb",
            compatibility="position-unstated",
        ),
    ]

    assert [row.reference_index for row in charged_misses(rows)] == [0, 2]


def test_a_sweep_in_another_mode_is_refused(monkeypatch):
    held = load_artifact(BASELINE)
    monkeypatch.setattr(
        run, "load_artifact", lambda path: replace(held, mode="end-to-end")
    )

    with pytest.raises(EvalRunError, match="reads an analysis sweep"):
        run.scorecard_of(BASELINE, REPO_ROOT / "evals/corpus", Ledger())


def test_the_command_reconciles_with_score(tmp_path: Path):
    card_path = tmp_path / "card.json"
    scored_path = tmp_path / "scored.json"

    assert main(["scorecard", str(BASELINE), "--out", str(card_path)]) == 0
    assert main(["score", str(BASELINE), "--out", str(scored_path)]) == 0
    card = json.loads(card_path.read_text())
    scored = json.loads(scored_path.read_text())

    lanes = {lane["lane"]: lane for lane in card["lanes"]}
    every = lanes.pop("all")
    assert len(lanes) == 6
    assert card["left_out"] == []
    assert (
        every["total"]
        == len(card["rows"])
        == sum(score["counts"]["must_find_total"] for score in scored["scores"])
    )
    assert every["by_status"]["matched"] == sum(
        score["counts"]["must_find_matched"] for score in scored["scores"]
    )
    for lane in card["lanes"]:
        assert sum(lane["by_status"][status] for status in STATUSES) == lane["total"]
    assert len({json.dumps(row["run"], sort_keys=True) for row in card["rows"]}) == 1
    assert {row["stages"]["proposed"] for row in card["rows"]} == {None}
    # Each must-find the critic yield calls lost for good is a reference the
    # scorecard reads as drafted and not reported.
    reported_by_case = {
        score["case"]: {pair["reference_index"] for pair in score["matched"]}
        for score in scored["scores"]
    }
    lost_for_good = {
        (entry["case"], killed["reference_index"])
        for entry in scored["critic_yield"]
        for killed in entry["killed"]
        if killed["disposition"] == "matched-must-find"
        and killed["reference_index"] not in reported_by_case[entry["case"]]
    }
    lost_rows = {
        (row["case"], row["reference_index"])
        for row in card["rows"]
        if row["stages"]["drafted"] and not row["stages"]["reported"]
    }
    assert lost_for_good and lost_for_good <= lost_rows
    # The loss attribution reads the position rule through the same scorer
    # helper: no row read as position-unstated is a lane's other position.
    assert all(
        row["cause"] != "position"
        for row in card["rows"]
        if row["compatibility"] == "position-unstated"
    )
    check = card["assembly"]
    replayed = check["reproduced"] + len(check["differ"]) + len(check["refused"])
    assert (check["unreadable"], check["capture_faults"]) == (0, 0)
    assert replayed == len(scored["scores"])
    fan = card["fan_in"]
    assert fan["compatible_blocks"] + len(fan["incompatible"]) == len(scored["scores"])
    # The critic panel pools the same yields that ``score`` writes: every
    # draft sits in one population cell.
    totals = scored["critic_yield_aggregate"]
    critic = card["critic"]
    assert critic["reference_loss"] == {
        "numerator": totals["matched_lost"],
        "denominator": totals["matched_killed"],
        "value": Rate(totals["matched_lost"], totals["matched_killed"]).value,
    }
    cells = critic["populations"]["counts"]
    assert sum(cell["drafts"] for cell in cells) == totals["drafts_in"]
    assert (
        sum(cell["drafts"] for cell in cells if cell["fate"] == "rejected")
        <= totals["killed"]
    )


def test_the_critic_panel_reads_only_the_first_series_votes(tmp_path: Path):
    """A voter outside the maintainer series labels no draft, as in ``score``."""
    roster_path = tmp_path / "voters.toml"
    roster_path.write_text(
        f'version = {roster.VERSION}\n[voters.mstarks01]\nstanding = "contributor"\n',
        "utf-8",
    )
    narrowed = tmp_path / "narrowed.json"
    full = tmp_path / "full.json"

    assert main(["scorecard", str(BASELINE), "--out", str(full)]) == 0
    assert (
        main(
            [
                "scorecard",
                str(BASELINE),
                "--roster",
                str(roster_path),
                "--out",
                str(narrowed),
            ]
        )
        == 0
    )

    populations = json.loads(full.read_text())["critic"]["populations"]
    assert populations["stale_drafts"] > 0
    read = json.loads(narrowed.read_text())
    assert read["critic"]["populations"]["stale_drafts"] == 0
    assert read["critic"]["populations"]["not_real_gap"] == "no-down-vote"
    assert read["record"]["table"]["series"] == "maintainer"


def test_the_omitted_fate_agrees_with_the_assembly_replay():
    """Two readers of "the block carries this draft in no list", held equal."""
    loaded = load_artifact(BASELINE)
    cases = [
        case
        for case in development_cases(load_corpus(REPO_ROOT / "evals/corpus"))
        if case.id in loaded.cases
    ]
    runs = runs_from_reports(loaded, cases)
    rows = [
        row for row in replay_artifact(loaded, cases).rows if row.framework == "stride"
    ]

    assert rows
    for row in rows:
        fate_of = fate_by_claim(stride_block(runs[row.case].report))
        omitted = {
            draft_id
            for draft_id in row.draft_ids
            if fate_of.get(draft_id, OMITTED) == OMITTED
        }
        assert omitted == set(row.omitted), row.case
