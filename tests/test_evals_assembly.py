"""The assembly replay: a critic's rulings rebuilt from a report and assembled again.

Driven by a scripted sweep, so the archive is one the shipped graph wrote. The
report-omission fixture is the one #1091 asked for: a finding the critic kept
that the report lacks, found by nothing earlier in the pipeline.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from analysis_service import graph
from analysis_service.assertions import AssertionCatalog, AssertionRecord
from analysis_service.claims import CLAIM_BOUND_MARKS, Severity, Verdict
from analysis_service.report import Report
from evals.harness import assembly
from evals.harness.artifact import RepoCommit, load_artifact
from evals.harness.bundle import reports_dir
from evals.harness.reference import load_case
from evals.harness.run import main
from tests.factories import repo_package_loaders, valid_model
from tests.source_tree import REPO_ROOT
from tests.test_assertion_use import ABSENCE, SHOPPERS
from tests.test_critic_controls import DB_CONTROL, _applied, _asvs_draft, _db, _row
from tests.test_evals_descendants import archive  # noqa: F401  (fixture)
from tests.test_evals_run_grounds import case  # noqa: F401  (fixture)
from tests.test_graph import (
    BOTH_DISCLAIMERS,
    FakeContext,
    domain_loader,  # noqa: F401  (fixture)
)

#: A committed Baseline whose critic rejected a claim as ``duplicate``, a step
#: ADR 0059 removed, in case ``02-iot-fleet-telemetry`` on claim ``D-01``.
OLD_BASELINE = (
    REPO_ROOT / "evals/baselines/6bff717-gpt-5.6-terra-24dda4db/mstarks01-e32ad9c5.json"
)
DUPLICATE_CASE = "02-iot-fleet-telemetry"


def _report_path(archive, case):  # noqa: F811
    return reports_dir(archive) / f"{case.id}.report.json"


def test_an_archive_assembles_to_its_own_report(archive, case):  # noqa: F811
    (row,) = assembly.replay_artifact(load_artifact(archive), [case]).rows

    assert row.reported and row.replayed == row.reported
    assert row.rejected_replayed == row.rejected_reported
    assert (row.omitted, row.unexplained, row.refused) == ((), (), "")


def _without_claim(report: Report) -> tuple[Report, str]:
    """``report`` with its last STRIDE claim gone and the block still consistent.

    A real omission would leave a report its own validators accept, so the
    fixture removes the claim, every mark naming it, and recomputes the summary
    with the block's own builder. The draft stays in the archived drafts file.
    """
    block = report.analyses[0]
    gone = block.claims[-1].id
    kept = block.claims[:-1]
    marks = {
        name: [mark for mark in getattr(block, name) if mark.claim_id != gone]
        for name in CLAIM_BOUND_MARKS
        if hasattr(block, name)
    }
    rebuilt = block.model_copy(
        update={
            "claims": kept,
            "summary": type(block).summarize(kept, block.rejected_claims),
            **marks,
        }
    )
    return report.model_copy(update={"analyses": [rebuilt]}), gone


def test_a_ruled_draft_the_report_lacks_is_an_omission(archive, case):  # noqa: F811
    """The report-omission fixture: the claim leaves the report, its draft stays."""
    path = _report_path(archive, case)
    report, gone = _without_claim(Report.model_validate_json(path.read_text()))
    path.write_text(report.model_dump_json())

    (row,) = assembly.replay_artifact(load_artifact(archive), [case]).rows

    assert row.omitted == (gone,)
    assert row.unexplained == ()


def test_a_claim_assembly_files_elsewhere_is_unexplained(
    archive,  # noqa: F811
    case,  # noqa: F811
    monkeypatch,
):
    """Assembly changed under the archive: it now rejects what the critic kept."""
    real = assembly.CriticRound.outcome

    def rejects_first(self, emitted):
        outcome = real(self, emitted)
        return replace(
            outcome,
            claims=outcome.claims[1:],
            rejected=[*outcome.rejected, outcome.claims[0]],
        )

    monkeypatch.setattr(assembly.CriticRound, "outcome", rejects_first)

    (row,) = assembly.replay_artifact(load_artifact(archive), [case]).rows

    assert len(row.unexplained) == 1
    assert row.omitted == ()


@pytest.mark.parametrize("kind", ["report", "drafts"])
def test_a_finished_case_missing_a_capture_is_named_and_the_rest_replay(
    archive,  # noqa: F811
    case,  # noqa: F811
    kind,
):
    """The artifact records the case as finished, so its capture was lost."""
    other = next(name for name in load_artifact(archive).cases if name != case.id)
    (reports_dir(archive) / f"{case.id}.{kind}.json").unlink()
    neighbour = replace(case, meta=case.meta.model_copy(update={"id": other}))

    sweep = assembly.replay_artifact(load_artifact(archive), [case, neighbour])

    [line] = sweep.capture_faults
    assert line.startswith(f"{case.id}: ") and f"no {kind} file" in line
    assert [row.case for row in sweep.rows] == [other]
    assert sweep.rows[0].accounted == sweep.rows[0].draft_ids
    assert main(["assembly", str(archive)]) == 1


def test_a_sweep_that_writes_no_report_gives_no_row(archive, case, capsys):  # noqa: F811
    """An extraction sweep has no block to replay: the command says so."""
    document = json.loads(archive.read_text(encoding="utf-8"))
    archive.write_text(json.dumps({**document, "mode": "extraction"}), "utf-8")

    sweep = assembly.replay_artifact(load_artifact(archive), [case])
    code = main(["assembly", str(archive)])

    assert sweep == ([], [], [], False)
    assert code == 0
    assert capsys.readouterr().out.splitlines()[-1] == (
        f"{archive}: the extraction mode writes no report"
    )


def test_a_ruling_today_s_critic_may_not_write_refuses_its_block():
    """A ``duplicate`` rejection rebuilds a ruling today's ``ThreatRuling`` refuses."""
    old_case = load_case(REPO_ROOT / "evals/corpus" / DUPLICATE_CASE)

    (row,) = assembly.replay_artifact(load_artifact(OLD_BASELINE), [old_case]).rows

    assert row.refused.startswith(
        "D-01: today's ThreatRuling refuses verdict.rejected_because holds 'duplicate'"
    )
    assert (row.replayed, row.rejected_replayed) == ((), ())


def test_the_command_names_the_refused_block_and_reads_the_rest(capsys, monkeypatch):
    """The baseline predates #1208; the ancestry answer is given, because a
    shallow checkout cannot place its commit."""
    monkeypatch.setattr(assembly, "severity_oracle", lambda commit: "critic-set")
    code = main(
        [
            "assembly",
            str(OLD_BASELINE),
            "--case",
            DUPLICATE_CASE,
            "--case",
            "01-payments-checkout",
        ]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert f"{DUPLICATE_CASE:34} stride refused by today's check:" in out
    assert "01-payments-checkout" in out and " same" in out
    assert out.splitlines()[-3:] == [
        (
            "2 block(s), 0 differ, 1 refused by today's check;"
            " 0 report(s) refused by today's Report; 0 case(s) with a missing or stray capture"
        ),
        (
            "drafts: 32 inspected, 18 accounted, 0 omitted, 0 filed differently,"
            " 14 unavailable; 0 ruled claim(s) with no draft"
        ),
        (
            "ruled severity: 0 compared, 0 differ; 18 claim(s) incompatible,"
            " because the run's critic set severity; 0 claim(s) with no oracle"
        ),
    ]


#: The case whose report :func:`unreadable_sweep` rewrites.
UNREADABLE_CASE = "01-payments-checkout"


@pytest.fixture
def unreadable_sweep(tmp_path) -> Path:
    """A copy of :data:`OLD_BASELINE` whose :data:`UNREADABLE_CASE` report
    holds a catalog entry with no ``origin``, a field today's Report requires."""
    sweep = tmp_path / OLD_BASELINE.name
    shutil.copy(OLD_BASELINE, sweep)
    shutil.copytree(reports_dir(OLD_BASELINE), reports_dir(sweep))
    path = reports_dir(sweep) / f"{UNREADABLE_CASE}.report.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    catalog = AssertionCatalog(subjects=[SHOPPERS], entries=[ABSENCE])
    payload["assertions"] = AssertionRecord(proposed=1, catalog=catalog).model_dump(
        mode="json"
    )
    del payload["assertions"]["catalog"]["entries"][0]["origin"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    return sweep


def test_the_command_names_an_unreadable_report_and_reads_the_rest(
    unreadable_sweep, capsys
):
    code = main(
        [
            "assembly",
            str(unreadable_sweep),
            "--case",
            DUPLICATE_CASE,
            "--case",
            UNREADABLE_CASE,
        ]
    )

    out = capsys.readouterr().out
    report = reports_dir(unreadable_sweep) / f"{UNREADABLE_CASE}.report.json"
    assert code == 0
    assert (
        f"{report}: today's Report refuses"
        " assertions.catalog.entries.0.origin: Field required"
    ) in out.splitlines()
    assert f"{DUPLICATE_CASE:34} stride refused by today's check:" in out
    assert out.splitlines()[-3] == (
        "1 block(s), 0 differ, 1 refused by today's check;"
        " 1 report(s) refused by today's Report; 0 case(s) with a missing or stray capture"
    )


def _rewritten(archive, case, rewrite) -> None:  # noqa: F811
    """The archived report with its one block replaced by ``rewrite(block)``."""
    path = _report_path(archive, case)
    report = Report.model_validate_json(path.read_text())
    block = rewrite(report.analyses[0])
    path.write_text(report.model_copy(update={"analyses": [block]}).model_dump_json())


def _filed(block, claims, rejected):
    """``block`` holding ``claims`` and ``rejected``, with its summary recounted."""
    return block.model_copy(
        update={
            "claims": claims,
            "rejected_claims": rejected,
            "summary": type(block).summarize(claims, rejected),
        }
    )


@pytest.fixture
def rejecting_archive(archive, case):  # noqa: F811
    """The scripted archive with its first claim rejected by the critic.

    The scripted critic keeps every draft, so a swap needs a rejected claim to
    swap. The report stays one assembly makes of its rulings.
    """

    def reject_first(block):
        first, *kept = block.claims
        verdict = Verdict(status="rejected", reason="r", rejected_because="reasoning")
        return _filed(block, kept, [first.model_copy(update={"verdict": verdict})])

    _rewritten(archive, case, reject_first)
    return archive


def test_a_rejected_claim_assembles_to_its_own_array(rejecting_archive, case):  # noqa: F811
    (row,) = assembly.replay_artifact(load_artifact(rejecting_archive), [case]).rows

    assert row.rejected_reported and row.rejected_replayed == row.rejected_reported
    assert (row.omitted, row.unexplained, row.refused) == ((), (), "")


def test_an_equal_count_swap_is_two_claims_filed_differently(
    rejecting_archive,
    case,  # noqa: F811
    monkeypatch,
):
    """Assembly trades one kept claim for one rejected claim: the totals match."""
    real = assembly.CriticRound.outcome

    def swaps_one_pair(self, emitted):
        outcome = real(self, emitted)
        (kept, *claims), (dropped, *rejected) = outcome.claims, outcome.rejected
        return replace(outcome, claims=[dropped, *claims], rejected=[kept, *rejected])

    monkeypatch.setattr(assembly.CriticRound, "outcome", swaps_one_pair)

    (row,) = assembly.replay_artifact(load_artifact(rejecting_archive), [case]).rows

    assert len(row.replayed) == len(row.reported)
    assert row.unexplained == tuple(sorted((row.reported[0], row.rejected_reported[0])))
    assert set(row.reported[1:]).isdisjoint(row.unexplained)
    assert row.omitted == ()


def test_a_swap_against_the_claims_verdicts_fails_the_load(
    rejecting_archive,
    case,  # noqa: F811
):
    """The report's own check owns where a verdict files a claim."""

    def swap(block):
        (kept, *claims), (dropped, *rejected) = block.claims, block.rejected_claims
        return _filed(block, [dropped, *claims], [kept, *rejected])

    _rewritten(rejecting_archive, case, swap)

    sweep = assembly.replay_artifact(load_artifact(rejecting_archive), [case])

    assert sweep.rows == []
    assert "rejected_claims but its verdict is" in sweep.unreadable[0]


def test_a_summary_that_miscounts_its_block_fails_the_load(archive, case):  # noqa: F811
    """The report's own check owns the summary, so a replay row never sees one."""

    def miscount(block):
        count = block.summary.claim_count + 1
        summary = block.summary.model_copy(update={"claim_count": count})
        return block.model_copy(update={"summary": summary})

    _rewritten(archive, case, miscount)

    sweep = assembly.replay_artifact(load_artifact(archive), [case])

    assert sweep.rows == []
    assert "summary.claim_count=" in sweep.unreadable[0]


def test_a_changed_claim_id_cannot_hide_its_draft(archive, case):  # noqa: F811
    """The report renames one claim: its draft is omitted, and its neighbours hold."""
    renamed = {}

    def rename(block):
        *kept, last = block.claims
        new = renamed[last.id] = f"{last.id[0]}-99"
        marks = {
            name: [
                mark.model_copy(update={"claim_id": new})
                if mark.claim_id == last.id
                else mark
                for mark in getattr(block, name)
            ]
            for name in CLAIM_BOUND_MARKS
            if hasattr(block, name)
        }
        claim = last.model_copy(update={"id": new})
        filed = _filed(block, [*kept, claim], block.rejected_claims)
        return filed.model_copy(update=marks)

    _rewritten(archive, case, rename)

    (row,) = assembly.replay_artifact(load_artifact(archive), [case]).rows

    assert row.omitted == tuple(renamed)
    assert row.unexplained == row.undrafted == tuple(renamed.values())
    assert row.filed_differently == ()
    assert len(row.accounted) + len(row.omitted) == len(row.draft_ids)
    assert (row.replayed, row.refused) == (row.reported[:-1], "")


def test_a_draft_id_held_twice_is_unavailable(archive, case):  # noqa: F811
    """Fan-in keeps one draft under each ID, so a second copy is a broken capture."""
    path = reports_dir(archive) / f"{case.id}.drafts.json"
    held = json.loads(path.read_text(encoding="utf-8"))
    held["stride"].append(held["stride"][0])
    path.write_text(json.dumps(held), encoding="utf-8")

    (row,) = assembly.replay_artifact(load_artifact(archive), [case]).rows

    assert row.repeated == (held["stride"][0]["id"],)
    assert row.unexplained == ()


def _assembled_with_a_return(loader):
    """An ASVS block the shipped ``assemble`` node built: one claim the critic
    kept against every Control row, so its unit went back to the deferred set
    (ADR 0126), and one kept neighbour."""
    asvs = graph.FrameworkNodes("asvs")
    keys = graph.GraphKeys.of(("asvs",))
    model = valid_model()
    ctx = FakeContext(**{graph.STATE_FRAMEWORK_OPTIONS: {"asvs": {"level": 3}}})
    graph.prepare_analysis(
        model.model_dump(mode="json"),
        ctx,
        keys,
        ("asvs",),
        loader,
        repo_package_loaders(("asvs",)),
        assertions=False,
    )
    returned = _asvs_draft("V11.2.1", "store:orders-db")
    neighbour = _asvs_draft("V6.2.1", "process:storefront-api")
    refused = {"control": DB_CONTROL, "unit": "V11.2.1", "reason": "names no key"}
    ctx.state[graph.STATE_APPLIED_CONTROLS] = [
        control.model_dump(mode="json") for control in _applied(_db(_row("V11.2.1")))
    ]
    ctx.state[asvs.key("drafts")] = [
        draft.model_dump(mode="json") for draft in (returned, neighbour)
    ]
    ctx.state[asvs.key("reviewed")] = {
        "claims": [
            {
                "id": returned.id,
                "verdict": {"reason": "", "refused_controls": [refused]},
            },
            {"id": neighbour.id, "verdict": {"reason": ""}},
        ]
    }
    ctx.state[asvs.key("undeferred")] = {"V11.2.1": "config"}
    ctx.state[asvs.key("accepted")] = True
    graph.assemble_report(
        model.model_dump(mode="json"),
        ctx,
        keys,
        ("asvs",),
        {"asvs": BOTH_DISCLAIMERS["asvs"]},
        domain_packs=None,
    )
    [block] = graph.Analysis.from_state(ctx.state[graph.STATE_ANALYSIS]).analyses
    return block, [returned, neighbour], model


def test_a_returned_claim_is_accounted_and_its_neighbour_kept(
    case,  # noqa: F811
    domain_loader,  # noqa: F811
):
    block, (returned, neighbour), model = _assembled_with_a_return(domain_loader)

    row = assembly.replay_case(case, block, [returned, neighbour], model, "compared")

    assert row.returned_reported == row.returned_replayed == (returned.id,)
    assert row.replayed == row.reported == (neighbour.id,)
    assert (row.omitted, row.unexplained, row.refused) == ((), (), "")


def test_a_return_on_another_unit_than_its_draft_s_is_filed_differently(
    case,  # noqa: F811
    domain_loader,  # noqa: F811
):
    """A returned claim carries no ruling, so its unit is what the replay checks."""
    block, (returned, neighbour), model = _assembled_with_a_return(domain_loader)
    [entry] = block.returned_claims
    moved = entry.model_copy(update={"unit": "V11.2.2"})
    block = block.model_copy(update={"returned_claims": [moved]})

    row = assembly.replay_case(case, block, [returned, neighbour], model, "compared")

    assert row.unexplained == (returned.id,)
    assert row.replayed == row.reported == (neighbour.id,)


def test_an_omitted_draft_held_twice_is_counted_once(archive, case):  # noqa: F811
    """Each draft falls in one count, so the counts add up to the drafts read."""
    path = _report_path(archive, case)
    report, gone = _without_claim(Report.model_validate_json(path.read_text()))
    path.write_text(report.model_dump_json())
    drafts_path = reports_dir(archive) / f"{case.id}.drafts.json"
    held = json.loads(drafts_path.read_text(encoding="utf-8"))
    held["stride"].extend(d for d in list(held["stride"]) if d["id"] == gone)
    drafts_path.write_text(json.dumps(held), encoding="utf-8")

    (row,) = assembly.replay_artifact(load_artifact(archive), [case]).rows

    assert (row.omitted, row.repeated, row.unavailable) == ((gone,), (gone,), ())
    counted = row.accounted + row.omitted + row.filed_differently + row.unavailable
    assert sorted(counted) == sorted(row.draft_ids)


def test_an_undrafted_claim_today_s_record_refuses_leaves_its_block_replayed(
    archive,  # noqa: F811
    case,  # noqa: F811
):
    """A ruled claim with no draft is named, whatever its ruling holds."""

    def add_stray(block):
        first = block.claims[0]
        verdict = Verdict(status="rejected", reason="r", rejected_because="duplicate")
        stray = first.model_copy(update={"id": "S-99", "verdict": verdict})
        return _filed(block, block.claims, [*block.rejected_claims, stray])

    _rewritten(archive, case, add_stray)

    (row,) = assembly.replay_artifact(load_artifact(archive), [case]).rows

    assert (row.refused, row.undrafted) == ("", ("S-99",))
    assert row.accounted == row.draft_ids


def _raise_first_severity(block):
    """``block`` with its first claim's severity moved off its draft's."""
    first, *rest = block.claims
    rating = "low" if first.severity.likelihood == "high" else "high"
    severity = Severity(
        likelihood=rating, impact=rating, justification=first.severity.justification
    )
    moved = first.model_copy(update={"severity": severity})
    return _filed(block, [moved, *rest], block.rejected_claims)


def test_a_reported_severity_its_draft_does_not_hold_is_named(
    archive,  # noqa: F811
    case,  # noqa: F811
    monkeypatch,
):
    """The run descends from #1208: no ruling replaces a severity, so the
    report's must be the draft's."""
    monkeypatch.setattr(assembly, "severity_oracle", lambda commit: "compared")
    _rewritten(archive, case, _raise_first_severity)

    (row,) = assembly.replay_artifact(load_artifact(archive), [case]).rows

    assert row.severity_differs == (row.reported[0],)
    assert row.severity_compared == len(row.reported)
    assert row.unexplained == ()


def test_an_archive_s_severities_match_their_drafts(archive, case, monkeypatch):  # noqa: F811
    monkeypatch.setattr(assembly, "severity_oracle", lambda commit: "compared")

    (row,) = assembly.replay_artifact(load_artifact(archive), [case]).rows

    assert row.severity_differs == ()
    assert row.severity_compared == len(row.reported) + len(row.rejected_reported)


@pytest.mark.parametrize("oracle", ["critic-set", "unknown"])
def test_a_run_with_no_severity_oracle_compares_none(
    archive,  # noqa: F811
    case,  # noqa: F811
    monkeypatch,
    oracle,
):
    """A run before #1208, or one the checkout cannot place, is not read
    against its drafts, so a moved severity is no difference."""
    monkeypatch.setattr(assembly, "severity_oracle", lambda commit: oracle)
    _rewritten(archive, case, _raise_first_severity)

    (row,) = assembly.replay_artifact(load_artifact(archive), [case]).rows

    assert (row.severity_oracle, row.severity_differs) == (oracle, ())
    assert row.severity_compared == 0
    assert row.severity_unchecked == len(row.reported)


#: A commit the 2026-09-28 history rewrite renamed, and its ID now. Its old
#: ID does not descend from the renamed #1208, and its new one does.
RENAMED = (
    "00fedc2b6b13eb27d714a2a61e6e08020cd214c4",
    "b82160bf7c425ed129529de4a8df15a7977ae2a7",
)


@pytest.mark.parametrize(
    ("held", "descends", "oracle"),
    [(False, False, "unknown"), (True, False, "critic-set"), (True, True, "compared")],
)
def test_the_oracle_reads_git_at_the_commit_s_id_now(
    monkeypatch, held, descends, oracle
):
    """Git is asked about the renamed ID, and each of its answers maps to one
    oracle. The answers are given, because a shallow clone cannot place them."""
    asked = []

    def git(root, *args):
        asked.append(args)
        if args[0] == "cat-file":
            return "" if held else None
        return "" if descends else None

    monkeypatch.setattr(assembly, "_git", git)

    answer = assembly.severity_oracle(RepoCommit(commit=RENAMED[0], clean=True))

    assert answer == oracle
    assert all(RENAMED[1] in " ".join(args) for args in asked)
    assert all(RENAMED[0] not in " ".join(args) for args in asked)


def test_a_commit_that_is_not_hex_never_reaches_git():
    with pytest.raises(ValidationError):
        RepoCommit(commit="-c core.pager=x", clean=True)
