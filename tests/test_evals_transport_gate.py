"""ADR 0035's gate, applied to arms built in the test rather than paid for.

The gate exists because it was once applied by hand and one criterion went
unchecked. So the property that matters here is not that a good arm passes: it
is that **each criterion can fail on its own**, and that an arm which is
missing the evidence for a criterion is refused rather than passed.
"""

from __future__ import annotations

import json

import pytest

from evals.harness import transport_gate as gate

CASES = ("01-a", "02-b", "03-c")


def _artifact(emitted: float, figures: dict[str, float], reasoning: float = 100.0):
    """One sweep artifact carrying exactly what the gate reads."""
    return {
        "corpus_digest": "corpus-1",
        "instruction": [{"node": "extract", "sha256": "digest-1"}],
        "node_usage": {
            "extract": {
                "completion_tokens": emitted + reasoning,
                "reasoning_tokens": reasoning,
            }
        },
        "mode_output": [
            {"case": case, **dict.fromkeys(gate.QUALITY_MARGINS, 1.0), **figures}
            for case in CASES
        ],
    }


def _arm(name, emitted, figures=None, issues=(), sweeps=5):
    """Five sweeps of one arm, every case valid unless ``issues`` says otherwise."""
    return gate.Arm(
        name=name,
        artifacts=tuple(
            _artifact(value, figures or {}) for value in _spread(emitted, sweeps)
        ),
        reports=tuple(
            tuple({"issues": list(issues)} for _ in CASES) for _ in range(sweeps)
        ),
    )


def _spread(mean, sweeps):
    """A spread around ``mean`` wide enough that ``stdev`` is not zero."""
    return [mean - 50 + 25 * index for index in range(sweeps)]


def test_a_cheaper_arm_that_matches_on_quality_promotes():
    full, compact = _arm("full", 1000), _arm("compact", 900)
    assert gate.apply(full, compact)


def test_the_primary_figure_alone_can_fail_it():
    """A saving inside the full arm's own spread is not a saving."""
    full, compact = _arm("full", 1000), _arm("compact", 990)
    assert not gate.apply(full, compact)


@pytest.mark.parametrize("figure,margin", gate.QUALITY_MARGINS.items())
def test_each_quality_margin_can_fail_on_its_own(figure, margin):
    full = _arm("full", 1000)
    compact = _arm("compact", 900, figures={figure: 1.0 - margin - 0.01})
    assert not gate.apply(full, compact)


def test_a_transport_fault_fails_although_every_figure_holds():
    """``duplicate-ref`` has a ceiling of zero: the full route cannot have one."""
    compact = _arm("compact", 900, issues=[{"code": "duplicate-ref"}])
    assert not gate.apply(_arm("full", 1000), compact)


def test_one_case_below_its_floor_vetoes_a_holding_corpus_mean():
    """The veto is per case, so a mean that holds cannot bury a collapse."""
    full = _arm("full", 1000)
    compact = _arm("compact", 900)
    for artifact in compact.artifacts:
        artifact["mode_output"][0]["recall"] = 0.5
    passed, reading = gate._veto(full, compact)
    assert not passed and "01-a/recall" in reading


def test_an_arm_missing_its_reports_is_refused_rather_than_passed():
    """The shape of missing evidence is the shape of a pass, so it is an error."""
    compact = _arm("compact", 900)
    blind = gate.Arm(compact.name, compact.artifacts, ((),) * 5)
    assert "extraction reports" in " ".join(gate.refusals(_arm("full", 1000), blind))


def test_an_arm_spanning_two_prompt_digests_is_refused():
    full = _arm("full", 1000)
    full.artifacts[0]["instruction"][0]["sha256"] = "digest-2"
    assert "prompt digests" in " ".join(gate.refusals(full, _arm("compact", 900)))


def test_two_corpus_digests_across_the_arms_are_refused():
    compact = _arm("compact", 900)
    for artifact in compact.artifacts:
        artifact["corpus_digest"] = "corpus-2"
    assert "corpus digests" in " ".join(gate.refusals(_arm("full", 1000), compact))


def test_every_criterion_the_record_states_is_in_the_table():
    """Eleven: the primary figure, six margins, validity, two faults, the veto."""
    named = 3  # the primary figure, first-pass validity and the per-case veto
    assert len(gate.CRITERIA) == named + len(gate.QUALITY_MARGINS) + len(
        gate.TRANSPORT_FAULTS
    )


def test_the_gate_reads_a_real_artifact_from_disk(tmp_path):
    """``load`` finds the reports by ``bundle.reports_dir``'s rule, not its own."""
    artifact = tmp_path / "full-r1.json"
    artifact.write_text(json.dumps(_artifact(1000.0, {})), encoding="utf-8")
    reports = tmp_path / "full-r1.reports"
    reports.mkdir()
    for case in CASES:
        (reports / f"{case}.extraction.json").write_text(
            json.dumps({"issues": []}), encoding="utf-8"
        )
    arm = gate.load("full", [artifact])
    assert arm.emitted == [1000.0] and arm.validity == [1.0]
