"""The descendants replay: the stages after a lane, run again on the archive.

Driven by a scripted sweep, so the archive it replays is one the shipped graph
wrote. Each fixture is one way a finding can fare after the lane: kept, left
for a critic to decide, or removed by the fan-in.
"""

from __future__ import annotations

import json

import pytest

from evals.harness import descendants
from evals.harness.artifact import load_artifact
from evals.harness.bundle import reports_dir
from tests import test_evals_run_grounds as grounds
from tests.eval_factories import write_finished_sweep
from tests.test_evals_modes import reaching_refs
from tests.test_evals_run_grounds import case  # noqa: F401  (fixture)


@pytest.fixture
def archive(monkeypatch, case, tmp_path):  # noqa: F811
    run = grounds.sweep(monkeypatch, case, None)
    return write_finished_sweep(tmp_path / "artifact.json", run)


def unmatched_reference(case, row):  # noqa: F811
    """A STRIDE reference the scripted run did not match, and its index."""
    references = case.stride_claims()
    index = next(i for i in range(len(references)) if i not in row.archived)
    return index, references[index]


def proposal_for(case, reference, title):  # noqa: F811
    return {
        "sequence": 90,
        "title": title,
        "description": f"{reference.claim} Injected by the test.",
        "affected_element_ids": list(reference.affected_element_ids),
        "verb": reference.verb,
        "severity": {
            "likelihood": reference.severity.likelihood,
            "impact": reference.severity.impact,
            "justification": "injected",
        },
        "evidence_refs": reaching_refs(case, reference.affected_element_ids),
    }


def test_the_archive_replays_to_itself(archive, case):  # noqa: F811
    (row,) = descendants.replay_artifact(load_artifact(archive), [case])

    assert row.lower == row.archived == row.upper
    assert row.unruled == ()


def test_an_injected_finding_is_left_for_a_critic_to_decide(archive, case):  # noqa: F811
    (plain,) = descendants.replay_artifact(load_artifact(archive), [case])
    index, reference = unmatched_reference(case, plain)
    injected = {
        case.id: {
            reference.category: [proposal_for(case, reference, "injected finding")]
        }
    }

    (row,) = descendants.replay_artifact(load_artifact(archive), [case], injected)

    assert index in row.upper, "the fan-in keeps it and it matches"
    assert index not in row.lower, "no critic ruled on it"
    assert "injected finding" in row.unruled


def test_an_injected_finding_the_fan_in_removes_says_why(archive, case):  # noqa: F811
    (plain,) = descendants.replay_artifact(load_artifact(archive), [case])
    index, reference = unmatched_reference(case, plain)
    broken = proposal_for(case, reference, "names nothing real")
    broken["affected_element_ids"] = ["process:not-in-the-model"]
    injected = {case.id: {reference.category: [broken]}}

    (row,) = descendants.replay_artifact(load_artifact(archive), [case], injected)

    assert index not in row.upper
    assert [title for title, _ in row.dropped] == ["names nothing real"]


def test_a_finished_case_with_a_lost_proposals_file_is_refused(archive, case):  # noqa: F811
    (reports_dir(archive) / f"{case.id}.proposals.json").unlink()

    with pytest.raises(descendants.EvalRunError, match="holds no proposals file"):
        descendants.replay_artifact(load_artifact(archive), [case])


def test_a_finished_case_with_no_report_is_named(archive, case):  # noqa: F811
    """The artifact records the case as finished, so its report was lost."""
    (reports_dir(archive) / f"{case.id}.report.json").unlink()

    with pytest.raises(descendants.EvalRunError, match=f"{case.id}: .* no report"):
        descendants.replay_artifact(load_artifact(archive), [case])


def test_a_case_the_artifact_does_not_list_is_named(archive, case):  # noqa: F811
    """The sweep did not run the case, so no report of it is this sweep's."""
    raw = json.loads(archive.read_text(encoding="utf-8"))
    raw["cases"] = [name for name in raw["cases"] if name != case.id]
    archive.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(descendants.EvalRunError, match=f"{case.id}: .* not list"):
        descendants.replay_artifact(load_artifact(archive), [case])


@pytest.mark.parametrize(
    ("keys", "named"),
    [(("01-payments-checkut", "spoofing"), "01-payments-checkut"), (None, "spoofng")],
)
def test_an_injected_key_nothing_replays_is_refused(archive, case, keys, named):  # noqa: F811
    """A misspelled case would inject nothing, and a misspelled lane would crash."""
    case_id, lane = keys or (case.id, "spoofng")
    with pytest.raises(descendants.EvalRunError, match=named):
        descendants.replay_artifact(
            load_artifact(archive), [case], {case_id: {lane: []}}
        )
