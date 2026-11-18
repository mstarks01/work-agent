"""The fan-in accounting: each proposal a lane emitted, given its fate.

The fixtures run the shipped fan-in. Each negative fixture has a valid
neighbour that stays accounted.
"""

from __future__ import annotations

import json

import pytest

from analysis_service.claims import QuoteCandidate
from analysis_service.fan_in import fan_in
from analysis_service.frameworks import PACKAGES, schemas_for
from analysis_service.frameworks.asvs.record import RequirementProposal
from analysis_service.frameworks.stride import STRIDE
from evals.harness import fan_in_accounting
from evals.harness.artifact import load_artifact
from evals.harness.bundle import reports_dir
from evals.harness.run import main
from tests.factories import sample_proposal, valid_model
from tests.test_evals_descendants import archive  # noqa: F401  (fixture)
from tests.test_evals_run_grounds import case  # noqa: F401  (fixture)
from tests.test_fan_in import CROSSING, LABEL, SOURCES, batches

ASVS = PACKAGES["asvs"]


def _accounted(stride_batches, sources=SOURCES):
    merged = fan_in(stride_batches, STRIDE, valid_model(), sources, assertions=None)
    return fan_in_accounting.account("case", stride_batches, STRIDE, merged)


def test_every_package_has_a_stored_batch_type():
    assert set(fan_in_accounting.ARCHIVED_BATCHES) == set(PACKAGES)
    for name, batch in fan_in_accounting.ARCHIVED_BATCHES.items():
        assert issubclass(batch, schemas_for(name).proposals)


def test_a_repeated_id_is_one_draft_and_one_drop():
    row = _accounted(
        batches(
            spoofing=[sample_proposal("S-01"), sample_proposal("S-01")],
            tampering=[sample_proposal("T-01", "tampering")],
        )
    )

    assert sorted(row.proposals) == ["S-01", "S-01", "T-01"]
    assert row.kept == ("S-01", "T-01")
    assert row.dropped == (
        ("S-01", "repeats the ID of an earlier draft in this framework"),
    )
    assert (row.unexplained, row.accounted) == ((), 3)


def test_an_invalid_proposal_is_dropped_and_its_neighbour_kept():
    valid = sample_proposal("S-01").model_dump(mode="json")
    stored = {"spoofing": {"claims": [valid, {**valid, "sequence": 2, "title": ""}]}}
    lanes = {
        lane: schemas_for("stride").proposals.model_validate(raw)
        for lane, raw in stored.items()
    }

    row = _accounted(lanes)

    assert row.kept == ("S-01",)
    [(claim_id, reason)] = row.dropped
    assert claim_id == "S-02" and reason.startswith(
        "fails the proposal schema at title"
    )
    assert (row.unexplained, row.accounted) == ((), 2)


def test_a_draft_with_one_invented_reference_beside_a_real_one_is_kept():
    proposal = sample_proposal(
        "S-01", evidence_refs=[CROSSING, "crossing:flow:not-a-flow-in-this-model"]
    )

    row = _accounted(batches(spoofing=[proposal]))

    assert (row.kept, row.dropped, row.unexplained) == (("S-01",), (), ())


def test_a_lone_quote_on_a_source_the_job_lacks_drops_its_draft_only():
    lone = sample_proposal(
        "S-02",
        quotes=[{"text": "log in to the web app", "source_label": "no-such-source"}],
        evidence_refs=[],
    )

    row = _accounted(batches(spoofing=[sample_proposal("S-01"), lone]))

    assert row.kept == ("S-01",)
    [(claim_id, reason)] = row.dropped
    assert claim_id == "S-02" and "no-such-source" in reason
    assert row.unexplained == ()


def test_quotes_are_counted_verified_repaired_and_refused():
    proposal = sample_proposal(
        "S-01",
        quotes=[
            {"text": "Customers log in to the web app", "source_label": LABEL},
            {"text": "a sentence the submitter never wrote", "source_label": LABEL},
        ],
        evidence_refs=[CROSSING],
    )

    row = _accounted(batches(spoofing=[proposal]))

    assert (row.quotes_verified, row.quotes_repaired, row.quotes_refused) == (1, 0, 1)


def test_an_empty_batch_has_nothing_to_account():
    row = _accounted(batches(spoofing=[]))

    assert (row.proposals, row.kept, row.dropped, row.unexplained) == ((), (), (), ())


def test_a_deferred_proposal_is_accounted():
    lane = "encoding-and-sanitization"
    proposal = RequirementProposal(
        title="t",
        description="d",
        requirement="2.4",
        needs_evidence="code",
        direction="question",
        quotes=[QuoteCandidate(text="Shoppers sign in", source_label="note")],
    )
    stored = {lane: schemas_for("asvs").proposals(claims=[proposal])}
    merged = fan_in(stored, ASVS, valid_model(), {}, assertions=None)

    row = fan_in_accounting.account("case", stored, ASVS, merged)

    assert row.deferred == ("v5.0.0-1.2.4",)
    assert (row.kept, row.unexplained, row.accounted) == ((), (), 1)


def test_a_split_that_disagrees_with_the_fan_in_is_incompatible():
    lane = "encoding-and-sanitization"
    proposal = RequirementProposal(
        title="t",
        description="d",
        requirement="2.4",
        needs_evidence="code",
        direction="question",
        quotes=[QuoteCandidate(text="Shoppers sign in", source_label="note")],
    )
    stored = {lane: schemas_for("asvs").proposals(claims=[proposal])}
    merged = fan_in(stored, ASVS, valid_model(), {}, assertions=None)
    moved = merged._replace(deferred={"V1.2.5": "code"})

    row = fan_in_accounting.account("case", stored, ASVS, moved)

    assert row.incompatible.startswith("the package's split defers other units")
    assert row.accounted == 0


def test_a_proposal_the_output_loses_is_unexplained():
    stored = batches(
        spoofing=[sample_proposal("S-01")],
        tampering=[sample_proposal("T-01", "tampering")],
    )
    merged = fan_in(stored, STRIDE, valid_model(), SOURCES, assertions=None)
    lost = merged._replace(drafts=merged.drafts[1:])

    row = fan_in_accounting.account("case", stored, STRIDE, lost)

    assert row.unexplained == ("S-01",)
    assert row.kept == ("T-01",) and row.accounted == 1


def test_an_output_no_proposal_carries_is_extra():
    stored = batches(spoofing=[sample_proposal("S-01")])
    merged = fan_in(stored, STRIDE, valid_model(), SOURCES, assertions=None)
    extra = merged.drafts[0].model_copy(update={"id": "S-07"})
    grown = merged._replace(drafts=[*merged.drafts, extra])

    row = fan_in_accounting.account("case", stored, STRIDE, grown)

    assert (row.unexplained, row.extra, row.accounted) == ((), ("S-07",), 1)


def test_a_second_copy_of_a_kept_id_is_extra_and_its_proposal_accounted():
    stored = batches(
        spoofing=[sample_proposal("S-01")],
        tampering=[sample_proposal("T-01", "tampering")],
    )
    merged = fan_in(stored, STRIDE, valid_model(), SOURCES, assertions=None)
    doubled = merged._replace(drafts=[*merged.drafts, merged.drafts[0]])

    row = fan_in_accounting.account("case", stored, STRIDE, doubled)

    assert (row.unexplained, row.extra, row.accounted) == ((), ("S-01",), 2)


def test_an_archive_accounts_for_every_proposal(archive, case):  # noqa: F811
    sweep = fan_in_accounting.replay_artifact(load_artifact(archive), [case])

    (row,) = sweep.rows
    assert row.incompatible == "" and row.proposals
    assert row.accounted == len(row.proposals)
    assert row.unexplained == ()


def test_an_archive_whose_drafts_the_replay_does_not_rebuild_is_incompatible(
    archive,  # noqa: F811
    case,  # noqa: F811
):
    path = reports_dir(archive) / f"{case.id}.drafts.json"
    held = json.loads(path.read_text(encoding="utf-8"))
    held["stride"][0]["title"] = "a title the run never wrote"
    path.write_text(json.dumps(held), encoding="utf-8")

    (row,) = fan_in_accounting.replay_artifact(load_artifact(archive), [case]).rows

    assert row.incompatible.startswith("the replayed drafts differ")
    assert "1 changed" in row.incompatible
    assert row.accounted == 0


@pytest.mark.parametrize("kind", ["report", "drafts", "proposals"])
def test_a_missing_capture_is_named_and_fails_the_command(
    archive,  # noqa: F811
    case,  # noqa: F811
    kind,
    capsys,
):
    (reports_dir(archive) / f"{case.id}.{kind}.json").unlink()

    sweep = fan_in_accounting.replay_artifact(load_artifact(archive), [case])
    code = main(["fan-in", str(archive), "--case", case.id])

    [line] = sweep.capture_faults
    assert f"no {kind} file" in line and sweep.rows == []
    assert code == 1
    assert capsys.readouterr().out.splitlines()[-2].startswith("proposals: 0 inspected")


def test_the_command_prints_the_counts(archive, case, capsys):  # noqa: F811
    code = main(["fan-in", str(archive), "--case", case.id])

    lines = capsys.readouterr().out.splitlines()
    assert code == 0
    assert lines[-3].startswith("1 block(s), 0 incompatible;")
    assert lines[-2].startswith("proposals: ") and " 0 unexplained," in lines[-2]
    assert lines[-1].startswith("quotes on kept drafts: ")


def test_an_archived_draft_today_s_record_refuses_is_incompatible(
    archive,  # noqa: F811
    case,  # noqa: F811
):
    path = reports_dir(archive) / f"{case.id}.drafts.json"
    held = json.loads(path.read_text(encoding="utf-8"))
    del held["stride"][0]["title"]
    path.write_text(json.dumps(held), encoding="utf-8")

    (row,) = fan_in_accounting.replay_artifact(load_artifact(archive), [case]).rows

    assert row.incompatible.startswith("today's record refuses an archived draft")


def test_a_capture_that_is_not_json_is_named(archive, case):  # noqa: F811
    (reports_dir(archive) / f"{case.id}.proposals.json").write_text("{", "utf-8")

    sweep = fan_in_accounting.replay_artifact(load_artifact(archive), [case])

    [line] = sweep.capture_faults
    assert line.startswith(f"{case.id}: a capture is not JSON")
    assert sweep.rows == []


def _rewrite_capture(archive, case, kind, rewrite):  # noqa: F811
    path = reports_dir(archive) / f"{case.id}.{kind}.json"
    path.write_text(json.dumps(rewrite(json.loads(path.read_text("utf-8")))), "utf-8")


def test_sources_the_artifact_records_under_another_digest_are_incompatible(
    archive,  # noqa: F811
    case,  # noqa: F811
):
    document = json.loads(archive.read_text(encoding="utf-8"))
    document["rendered_sources"] = {case.id: "0" * 64}
    archive.write_text(json.dumps(document), encoding="utf-8")

    (row,) = fan_in_accounting.replay_artifact(load_artifact(archive), [case]).rows

    assert row.incompatible == (
        "the artifact records the case's sources under another digest"
    )


def test_a_case_with_no_proposals_for_its_block_is_incompatible(
    archive,  # noqa: F811
    case,  # noqa: F811
):
    _rewrite_capture(archive, case, "proposals", lambda held: {})

    (row,) = fan_in_accounting.replay_artifact(load_artifact(archive), [case]).rows

    assert row.incompatible == "the archive holds no proposals"


def test_a_batch_today_s_schema_refuses_is_incompatible(archive, case):  # noqa: F811
    def refuse(held):
        held["stride"]["spoofing"] = "not a batch"
        return held

    _rewrite_capture(archive, case, "proposals", refuse)

    (row,) = fan_in_accounting.replay_artifact(load_artifact(archive), [case]).rows

    assert "validation error" in row.incompatible


def test_a_capture_for_a_framework_the_report_lacks_is_named(archive, case):  # noqa: F811
    _rewrite_capture(archive, case, "proposals", lambda held: {**held, "asvs": {}})

    sweep = fan_in_accounting.replay_artifact(load_artifact(archive), [case])

    [line] = sweep.capture_faults
    assert "['asvs']" in line
    assert [row.incompatible for row in sweep.rows] == [""]
