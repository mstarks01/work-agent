"""Where a wrong fact on a paired element went wrong (#2271)."""

from __future__ import annotations

from typing import get_args

import pytest

from analysis_service.analysis import ControlState
from analysis_service.compact import FULL_FORMAT, parse_extraction
from evals.harness import fact_trace, replay
from evals.harness.alignment import ProtocolState
from evals.harness.fact_trace import FactTrace
from evals.harness.modes import AttributeCheck, ExtractionResult
from tests.test_evals_alignment import case

PROCESS = "process:order-service"


@pytest.fixture(scope="module")
def golden():
    return case("01")


def _emitted(model):
    """A raw emission that holds ``model`` as the extract node writes one."""
    return model.model_dump(mode="json")


def _exposed(golden, value):
    copy = golden.model.model_copy(deep=True)
    next(p for p in copy.processes if p.id == PROCESS).exposure = value
    return copy


def _exposure(graded) -> FactTrace:
    (fact,) = [
        fact
        for fact in graded.facts
        if fact.check.element_id == PROCESS and fact.check.attribute == "exposure"
    ]
    return fact


def test_a_value_wrong_in_the_raw_emission_is_wrong_in_raw(golden):
    wrong = _exposed(golden, "internet-facing")

    graded = replay.replay_extraction(
        golden, ExtractionResult(golden.id, wrong, (), raw=_emitted(wrong))
    )

    assert _exposure(graded).stage == "wrong-in-raw"


def test_a_right_raw_value_the_parse_changed_is_changed_by_code(golden):
    wrong = _exposed(golden, "internet-facing")

    graded = replay.replay_extraction(
        golden, ExtractionResult(golden.id, wrong, (), raw=_emitted(golden.model))
    )

    assert _exposure(graded).stage == "changed-by-code"


def test_a_wrong_raw_value_the_parse_righted_is_corrected_by_code(golden):
    wrong = _exposed(golden, "internet-facing")

    graded = replay.replay_extraction(
        golden,
        ExtractionResult(golden.id, golden.model, (), raw=_emitted(wrong)),
    )

    assert _exposure(graded).stage == "corrected-by-code"


def test_an_element_the_raw_block_lacks_is_raw_unread(golden):
    graded = replay.replay_extraction(
        golden, ExtractionResult(golden.id, golden.model, ())
    )

    assert {fact.stage for fact in graded.facts} == {"raw-unread"}


def test_a_raw_entry_pairs_by_position_and_not_by_its_emitted_id(golden):
    """The parse derives IDs again, so a raw entry that carries another
    element's ID is still read for the element at its own position."""
    raw = _emitted(golden.model)
    processes = raw["processes"]
    target = next(i for i, p in enumerate(processes) if p["id"] == PROCESS)
    other = next(i for i, p in enumerate(processes) if p["id"] != PROCESS)
    assert other < target, "the decoy entry must come first in the walk"
    processes[other] = {**processes[other], "id": PROCESS, "exposure": "dmz"}

    graded = replay.replay_extraction(
        golden, ExtractionResult(golden.id, golden.model, (), raw=raw)
    )

    assert _exposure(graded).stage == "agrees"


def test_a_group_whose_lengths_differ_pairs_nothing(golden):
    raw = _emitted(golden.model)
    raw["processes"] = raw["processes"][:-1]

    graded = replay.replay_extraction(
        golden, ExtractionResult(golden.id, golden.model, (), raw=raw)
    )

    assert _exposure(graded).stage == "raw-unread"


@pytest.mark.parametrize(
    ("blessed", "extracted", "raw", "stage"),
    [
        ("internal", "internal", "internal", "agrees"),
        ("internal", "internet-facing", "dmz", "changed-and-wrong"),
    ],
)
def test_each_stage_reads_the_three_values(blessed, extracted, raw, stage):
    check = AttributeCheck(PROCESS, "exposure", blessed, extracted)

    assert FactTrace(check, raw).stage == stage


@pytest.mark.parametrize(
    ("blessed", "extracted", "certainty"),
    [
        ("unverified", "stated", "unjustified-certainty"),
        ("unverified", "absent", "unjustified-certainty"),
        ("silent", "stated", "unjustified-certainty"),
        ("absent", "unverified", "silence-for-absence"),
        ("stated", "unverified", "silence-for-statement"),
        ("stated", "silent", "silence-for-statement"),
        ("stated", "absent", "contradicts"),
        ("absent", "stated", "contradicts"),
        ("stated", "stated", None),
    ],
)
def test_a_wrong_state_names_how_the_states_differ(blessed, extracted, certainty):
    check = AttributeCheck("flow:a>b>c", "authentication", blessed, extracted)

    assert FactTrace(check, extracted).certainty == certainty


def test_each_duplicate_id_issue_of_the_parse_is_counted(golden):
    raw = _emitted(golden.model)
    first, second = raw["processes"][:2]
    second["id"] = first["id"]
    parsed, issues = parse_extraction(raw, FULL_FORMAT, sources={}, diagrams={})

    graded = replay.replay_extraction(
        golden, ExtractionResult(golden.id, parsed, tuple(issues), raw=raw)
    )

    assert len(graded.duplicate_id_issues) == sum(
        issue.code == "duplicate-id" for issue in issues
    )
    assert graded.duplicate_id_issues


def test_a_parsed_id_two_elements_carry_pairs_nothing(golden):
    raw = _emitted(golden.model)
    parsed = golden.model.model_copy(deep=True)
    first, second = parsed.processes[:2]
    parsed.processes[1] = second.model_copy(update={"id": first.id})

    assert first.id not in fact_trace.emitted_entries(raw, parsed)


def test_the_pool_splits_each_attribute_by_stage_and_prints_it(golden, capsys):
    wrong = _exposed(golden, "internet-facing")
    graded = replay.replay_extraction(
        golden, ExtractionResult(golden.id, wrong, (), raw=_emitted(golden.model))
    )
    arm = replay.Arm(
        nodes=("extract",),
        instructions=("digest",),
        models=("model",),
        treatment="treatment",
        treatment_by="sweep",
    )
    sweep = replay.SweepReplay(
        artifact="a.json",
        arm=arm,
        commit="c",
        clean=True,
        corpus_digest="d",
        extractions=(graded,),
    )

    pool = replay.pooled_extraction([sweep])
    replay._render_extraction(pool, targets=0)

    assert pool["facts"]["by_stage"]["process.exposure"]["changed-by-code"] == 1
    assert "process.exposure" in capsys.readouterr().out


def test_every_differing_pair_of_one_reducers_states_has_a_certainty():
    pairs = {
        (a, b)
        for states in (get_args(ControlState), get_args(ProtocolState))
        for a in states
        for b in states
        if a != b
    }

    assert set(fact_trace._CERTAINTY) == pairs


def test_the_position_pairing_is_the_parses_own(golden):
    """The processes' emitted IDs swapped: the parse derives IDs from names,
    and each parsed element pairs with the raw entry that carries its name."""
    raw = _emitted(golden.model)
    first, second = raw["processes"][:2]
    first["id"], second["id"] = second["id"], first["id"]
    parsed, _ = parse_extraction(raw, FULL_FORMAT, sources={}, diagrams={})

    assert parsed is not None
    paired = fact_trace.emitted_entries(raw, parsed)
    assert len(paired) == len(list(parsed.elements()))
    for element in parsed.elements():
        assert paired[element.id]["name"] == element.name
