"""Where a wrong fact on a paired element went wrong (#2271)."""

from __future__ import annotations

import pytest

from evals.harness import fact_trace, replay
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


def test_an_element_the_raw_block_lacks_is_uncaptured(golden):
    graded = replay.replay_extraction(
        golden, ExtractionResult(golden.id, golden.model, ())
    )

    assert {fact.stage for fact in graded.facts} == {"uncaptured"}


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

    assert _exposure(graded).stage == "uncaptured"


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


def test_an_id_named_twice_with_different_values_is_a_conflict():
    raw = {
        "processes": [
            {"id": PROCESS, "exposure": "internal"},
            {"id": PROCESS, "exposure": "internet-facing"},
            {"id": "process:same", "exposure": "internal"},
            {"id": "process:same", "exposure": "internal"},
        ],
        "data_flows": "not a list",
    }

    assert fact_trace.conflicting_ids(raw, ["exposure"]) == (PROCESS,)


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
