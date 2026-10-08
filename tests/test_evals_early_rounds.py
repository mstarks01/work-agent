"""The early-rounds replay ends, and its figures agree with each other."""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.harness import early_rounds
from evals.harness.early_rounds import ANSWERERS, replay
from tests.factories import FrameworkSelection, sample_report, valid_model
from tests.test_asvs import _block

STRIDE: dict = {"stride": {}}
ASVS: dict = {"asvs": {"level": 1}}
BOTH: dict = STRIDE | ASVS
CORPUS = Path(__file__).resolve().parents[1] / "evals" / "corpus"


def open_controls():
    model = valid_model()
    for flow in model.data_flows:
        flow.authentication = "unknown"
        flow.encryption_in_transit = "unknown"
    return model


@pytest.mark.parametrize("name", sorted(ANSWERERS))
@pytest.mark.parametrize(
    "frameworks", [STRIDE, ASVS, BOTH], ids=["stride", "asvs", "both"]
)
def test_every_answerer_reaches_the_end(name, frameworks):
    found = replay("case", open_controls(), valid_model(), frameworks, name)

    assert found.ended
    assert found.above_floor, "a control: the model has questions at the floor"
    assert found.asked <= found.above_floor + found.added


def test_a_stated_control_closes_leads_and_takes_questions_away():
    """Naming a mechanism for every control lowers later scores (E20)."""
    none = replay("case", open_controls(), valid_model(), STRIDE, "controls-none")
    stated = replay("case", open_controls(), valid_model(), STRIDE, "controls-stated")
    assert stated.asked <= none.asked


def test_an_owner_who_skips_everything_is_asked_each_question_once():
    found = replay("case", open_controls(), valid_model(), STRIDE, "skip-all")
    assert found.repeats == 0
    assert found.skipped == found.asked
    # The skipped questions leave the ones under the floor (ADR 0068).
    assert found.stop == "below-floor"


def test_an_owner_who_answers_in_part_and_skips_the_rest_is_not_asked_again():
    """Answering one facet and saving asked the same question fifty rounds
    over, before "Skip the rest" could set it aside (#1289)."""
    found = replay("case", open_controls(), valid_model(), STRIDE, "facets-partial")
    assert found.ended
    assert found.repeats == 0


def test_a_replay_records_what_its_stop_leaves_open():
    """The workload replay reports the stop's summary (#1542 B, ADR 0068):
    the questions under a floor and held back, and each framework's units by
    band and state."""
    found = replay(
        "case", valid_model(), valid_model(), {"asvs": {"level": 2}}, "capability-yes"
    )
    assert found.stop in {"budget-exhausted", "below-floor", "nothing-left"}
    bands = found.units["asvs"]
    assert set(bands) == {"level 1", "level 2"}
    assert all("unaskable" in states for states in bands.values())
    assert found.held_back + found.below_floor > 0, "a control: something stays out"
    stride = replay("case", valid_model(), valid_model(), STRIDE, "capability-yes")
    assert stride.units == {}


def test_a_replay_counts_each_package_apart():
    """Each selected package gets its own asked, held-back and below-floor
    counts, so the replay shows when one package starves another (#1562)."""
    both = replay("case", open_controls(), valid_model(), BOTH, "capability-yes")
    assert set(both.packages) == {"stride", "asvs"}
    asked = [figures["asked"] for figures in both.packages.values()]
    assert max(asked) <= both.asked <= sum(asked)
    assert sum(f["held_back"] for f in both.packages.values()) >= both.held_back
    alone = replay("case", open_controls(), valid_model(), STRIDE, "capability-yes")
    assert alone.packages == {
        "stride": {
            "asked": alone.asked,
            "held_back": alone.held_back,
            "below_floor": alone.below_floor,
        }
    }


def _archived(root: Path, case: str, report) -> None:
    """Write ``report`` where the replay finds an archived report of ``case``."""
    folder = root / case
    folder.mkdir()
    (folder / f"{case}.report.json").write_text(report.model_dump_json())


def _asvs_report(level: int):
    report = sample_report(analyses=[_block(level)])
    selection = FrameworkSelection(name="asvs", options={"level": level})
    return report.model_copy(
        update={"job": report.job.model_copy(update={"frameworks": [selection]})}
    )


def test_a_joint_replay_keeps_the_options_the_report_chose(tmp_path, monkeypatch):
    """A joint replay of an ASVS level 1 report asks at level 1, though an
    archived job at level 2 comes first (#1562)."""
    _archived(tmp_path, "01-payments-checkout", _asvs_report(2))
    _archived(tmp_path, "13-dispatch-control-plane", _asvs_report(1))
    _archived(tmp_path, "05-cookbook-queue-webapp", sample_report())
    calls = []
    monkeypatch.setattr(
        early_rounds,
        "replay",
        lambda case, extracted, blessed, frameworks, name: calls.append(
            (case, dict(frameworks), name)
        ),
    )

    early_rounds.replays(tmp_path, CORPUS)

    joint = {case: frameworks for case, frameworks, _ in calls if len(frameworks) > 1}
    assert joint["13-dispatch-control-plane"]["asvs"] == {"level": 1}
    assert joint["01-payments-checkout"]["asvs"] == {"level": 2}
