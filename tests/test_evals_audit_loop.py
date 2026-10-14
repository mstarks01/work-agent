"""The improvement loop's limits, each driven through the one reader that holds it.

Stage 3 of the quality audit (#1092) lets an audit change code, revert it,
resume, and build a missing capability. Each acceptance criterion there is a
refusal in :func:`evals.harness.audit.admit` or :func:`~evals.harness.audit.parse`,
and each class below drives one of them: a limit that nothing raises on is a
comment.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import subprocess
from pathlib import Path

import pytest

from analysis_service.frameworks import PACKAGES
from evals.harness import audit

REVISION = "0" * 40
TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / ".claude/skills/quality-audit/templates/extension-ticket.md"
)


def _row(**overrides) -> dict:
    row = {
        "experiment_id": "A-E1",
        "audit_id": "A",
        "recorded": "2026-10-11T00:00:00Z",
        "revision": REVISION,
        "signature": "stride case-01 must-find missed at the checkout boundary",
        "hypothesis": "the lane never cited the place",
        "falsifier": "a rejected draft citing the place",
        "intervention": "read the archived losses block",
        "outcome": "null",
        "scope": "case 01, stride, one archived sweep",
    }
    row.update(overrides)
    return row


def _candidate(**overrides) -> dict:
    held = {
        "branch": "qa/A-E1",
        "commit": "1" * 40,
        "verdict": "reverted",
        "reason": "the lane still cited the neighbouring element",
        "paths": ["frameworks/stride/lanes/spoofing/skill.md"],
        "parity": {
            name: "nothing: the lane is this package's own" for name in PACKAGES
        },
    }
    held.update(overrides)
    return held


def _ticket(**overrides) -> dict:
    held = {one.name: f"the {one.name}" for one in dataclasses.fields(audit.Ticket)}
    held.update(title="no replay seam for the critic", belongs="harness")
    held.update(overrides)
    return held


def _one(**overrides) -> audit.Experiment:
    return audit.parse(_row(**overrides), source="test")


def _grant(**overrides) -> audit.Grant:
    held = {
        "audit_id": "A",
        "recorded": "2026-10-11T00:00:00Z",
        "words": "implement and validate, limit paid inference to $5",
        "mode": "implement",
        "budget_usd": 5,
        "attempts": 3,
    }
    held.update(overrides)
    return audit.parse_grant(held, source="test")


def _lookup(experiment: audit.Experiment, state: str, superseded=False):
    read = {
        "current": audit.Staleness(),
        "stale": audit.Staleness(changed=("evals/harness/verbs.py",)),
    }[state]
    return audit.Lookup(experiment=experiment, staleness=read, superseded=superseded)


class TestAFailedCandidateIsRevertedAndKept:
    """Criterion 1: a failed change is reverted, and its record survives."""

    @pytest.mark.parametrize("outcome", ["refuted", "null", "inconclusive", "blocked"])
    def test_a_failed_candidate_cannot_be_kept(self, outcome):
        with pytest.raises(audit.ExperimentError, match="reverted"):
            _one(outcome=outcome, candidate=_candidate(verdict="kept"))

    def test_a_revert_states_its_reason(self):
        with pytest.raises(audit.ExperimentError, match="no reason"):
            _one(outcome="refuted", candidate=_candidate(reason=" "))

    def test_the_reverted_row_survives_a_write_and_a_read(self, tmp_path):
        reverted = _one(outcome="refuted", candidate=_candidate())
        audit.append(reverted, tmp_path)

        (loaded,) = audit.load(tmp_path)
        assert loaded == reverted
        assert loaded.candidate is not None
        assert loaded.candidate.reason == _candidate()["reason"]

    def test_a_supported_candidate_may_be_kept(self):
        kept = _one(outcome="supported", candidate=_candidate(verdict="kept"))

        assert kept.candidate is not None
        assert kept.candidate.verdict == "kept"


@pytest.mark.parametrize(
    "overrides",
    [
        {"candidate": _candidate(reason=5)},
        {"candidate": _candidate(), "resolves": ["A-E0"]},
        {"rerun_reason": 5},
        {"replicates": ["A-E0"]},
    ],
    ids=["reason", "resolves", "rerun_reason", "replicates"],
)
def test_a_text_field_of_another_type_is_refused(overrides):
    with pytest.raises(audit.ExperimentError, match="is not a string"):
        _one(outcome="refuted", **overrides)


class TestARerunSaysWhy:
    """Criterion 2: an audit reuses evidence and explains any rerun."""

    def test_a_current_conclusion_of_another_audit_needs_a_reason(self):
        earlier = _one(experiment_id="B-E1", audit_id="B", outcome="refuted")
        with pytest.raises(audit.ExperimentError, match="B-E1.*rerun_reason"):
            audit.admit(_one(), [earlier], (), [_lookup(earlier, "current")])

    def test_the_reason_admits_the_rerun(self):
        earlier = _one(experiment_id="B-E1", audit_id="B", outcome="refuted")
        rerun = _one(rerun_reason="the critic prompt changed in #1600")

        audit.admit(rerun, [earlier], (), [_lookup(earlier, "current")])

    @pytest.mark.parametrize(
        ("state", "superseded", "audit_id"),
        [("stale", False, "B"), ("current", True, "B"), ("current", False, "A")],
        ids=["stale", "superseded", "same-audit"],
    )
    def test_no_reason_is_owed(self, state, superseded, audit_id):
        """A stale or corrected row answers nothing, and an audit's own series is not a rerun."""
        earlier = _one(experiment_id="B-E1", audit_id=audit_id)

        audit.admit(_one(), [earlier], (), [_lookup(earlier, state, superseded)])

    def test_a_resume_reads_one_audit_whole(self):
        rows = [_one(), _one(experiment_id="B-E1", audit_id="B", signature="other")]

        found = audit.relevant(rows, audit_id="A")
        assert [one.experiment_id for one in found] == ["A-E1"]


class TestABlockedAuditWritesItsTicket:
    """Criterion 3: a blocked audit produces an actionable extension ticket."""

    def test_a_blocked_row_without_a_ticket_is_refused(self):
        with pytest.raises(audit.ExperimentError, match="extension ticket"):
            audit.admit(_one(outcome="blocked"), [], ())

    @pytest.mark.parametrize(
        "name", [one.name for one in dataclasses.fields(audit.Ticket)]
    )
    def test_every_ticket_field_is_required(self, name):
        with pytest.raises(audit.ExperimentError, match=f"ticket has no {name}"):
            _one(outcome="blocked", ticket=_ticket(**{name: " "}))

    def test_a_ticket_names_a_place_from_the_closed_set(self):
        with pytest.raises(audit.ExperimentError, match="belongs"):
            _one(outcome="blocked", ticket=_ticket(belongs="somewhere"))

    def test_the_rendered_ticket_follows_the_template(self):
        """The renderer and the template name the same sections, in one order."""
        headings = re.findall(r"^## (.+)$", TEMPLATE.read_text("utf-8"), re.MULTILINE)
        title, body = audit.render_ticket(_one(outcome="blocked", ticket=_ticket()))

        assert title == "no replay seam for the critic"
        assert re.findall(r"^## (.+)$", body, re.MULTILINE) == headings
        assert "experiment `A-E1`" in body
        assert "`resolves: A-E1`" in body

    def test_the_command_prints_it(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(audit, "EXPERIMENTS_DIR", tmp_path)
        audit.append(_one(outcome="blocked", ticket=_ticket()), tmp_path)

        assert audit.command_experiments(_arguments(ticket="A-E1")) == 0
        assert "## The missing capability" in capsys.readouterr().out


def _arguments(**fields) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    audit.arguments(parser)
    argv = []
    for name, value in fields.items():
        argv += [f"--{name}"] if value is None else [f"--{name}", value]
    return parser.parse_args(argv)


def _blocked() -> audit.Experiment:
    return _one(experiment_id="A-E1", outcome="blocked", ticket=_ticket())


def _extension(**overrides) -> audit.Experiment:
    held = {
        "experiment_id": "A-E2",
        "outcome": "supported",
        "intervention": "added run.py critic-replay",
        "candidate": _candidate(
            branch="qa/A-E2",
            verdict="kept",
            reason="",
            paths=["evals/harness/x.py"],
            regressions=["tests/test_evals_audit_loop.py"],
        ),
        "resolves": "A-E1",
    }
    held.update(overrides)
    return _one(**held)


class TestAnAuthorisedExtensionResumesTheAudit:
    """Criterion 4: an extension resolves a fixture blocker and the audit resumes."""

    def test_the_whole_path(self, tmp_path, monkeypatch, capsys):
        """Blocked, granted, extended, resumed: each step through ``record``."""
        monkeypatch.setattr(audit, "EXPERIMENTS_DIR", tmp_path)
        monkeypatch.setattr(audit, "lookup", lambda **_: [])
        monkeypatch.setattr(audit, "paths_between", lambda *_: ("evals/harness/x.py",))
        steps = [
            _blocked(),
            _extension(),
            _one(
                experiment_id="A-E3",
                outcome="supported",
                intervention="the experiment A-E1 could not run, now run",
                parent="A-E1",
            ),
        ]

        written = tmp_path / "row.json"
        written.write_text(json.dumps(steps[0].to_json()), "utf-8")
        audit.record(written)
        grant = tmp_path / "grant.json"
        grant.write_text(
            json.dumps({**vars(_grant(mode="extend")), "budget_usd": 0, "attempts": 0}),
            "utf-8",
        )
        assert audit.command_experiments(_arguments(grant=str(grant))) == 0
        for step in steps[1:]:
            written.write_text(json.dumps(step.to_json()), "utf-8")
            audit.record(written)

        held = audit.standing("A", audit.load(tmp_path), audit.load_grants("A"))
        assert held.open_blockers == ()
        assert dict(held.resolved) == {"A-E1": ("A-E2", ("A-E3",))}
        assert audit.command_experiments(_arguments(audit="A")) == 0
        assert "resolved:  A-E1 by A-E2; resumed by A-E3" in capsys.readouterr().out

    def test_an_open_blocker_is_listed_until_resolved(self):
        held = audit.standing("A", [_blocked()], ())

        assert held.open_blockers == ("A-E1",)

    def test_an_extension_records_its_change(self):
        with pytest.raises(audit.ExperimentError, match="no candidate"):
            _one(resolves="A-E1")


class TestNothingExpandsSilently:
    """Criterion 5: recursion, budgets, permissions, references and criteria."""

    # Recursion.

    def test_a_blocker_raised_under_an_extension_gets_no_further_extension(self):
        ledger = [
            _blocked(),
            _extension(),
            _one(
                experiment_id="A-E3",
                outcome="blocked",
                ticket=_ticket(),
                parent="A-E2",
            ),
        ]
        with pytest.raises(audit.ExperimentError, match="may not trigger another"):
            audit.admit(
                _extension(experiment_id="A-E4", resolves="A-E3"),
                ledger,
                [_grant(mode="extend")],
            )

    def test_a_blocker_is_resolved_once(self):
        with pytest.raises(audit.ExperimentError, match="already resolved"):
            audit.admit(
                _extension(experiment_id="A-E3"),
                [_blocked(), _extension()],
                [_grant(mode="extend")],
            )

    @pytest.mark.parametrize(
        "ledger",
        [[], [_one(experiment_id="A-E1", outcome="refuted")]],
        ids=["absent", "not-blocked"],
    )
    def test_an_extension_resolves_a_blocked_row(self, ledger):
        with pytest.raises(audit.ExperimentError, match="not a blocked row"):
            audit.admit(_extension(), ledger, [_grant(mode="extend")])

    def test_an_extension_resolves_its_own_audits_blocker(self):
        other = _one(audit_id="B", outcome="blocked", ticket=_ticket())
        with pytest.raises(audit.ExperimentError, match="not a blocked row of A"):
            audit.admit(_extension(), [other], [_grant(mode="extend")])

    # Budgets.

    def test_spend_past_the_grant_is_refused(self):
        with pytest.raises(audit.ExperimentError, match=r"\$5\.5000 of \$5\.0000"):
            audit.admit(
                _one(experiment_id="A-E2", actual_usd=1.5),
                [_one(actual_usd=4.0)],
                [_grant()],
            )

    def test_an_estimate_with_no_actual_is_reserved(self):
        with pytest.raises(audit.ExperimentError, match="would spend"):
            audit.admit(_one(estimated_usd=6.0), [], [_grant()])

    def test_extension_spend_counts_against_the_same_grant(self):
        with pytest.raises(audit.ExperimentError, match="would spend"):
            audit.admit(
                _extension(actual_usd=0.5),
                [_blocked(), _one(experiment_id="A-E0", actual_usd=4.75)],
                [_grant(mode="extend")],
            )

    @pytest.mark.parametrize("value", [-1.0, float("nan"), True, "0.5"])
    def test_a_cost_that_would_widen_the_budget_is_refused(self, value):
        with pytest.raises(audit.ExperimentError, match="dollar amount"):
            _one(actual_usd=value)

    def test_no_grant_is_a_budget_of_nothing(self):
        with pytest.raises(audit.ExperimentError, match=r"of \$0\.0000"):
            audit.admit(_one(actual_usd=0.01), [], ())

    def test_a_new_grant_widens_the_budget(self):
        """A resume buys more by a second grant in the user's words, never an edit."""
        grants = [_grant(), _grant(words="another $2 for the repeat", budget_usd=2)]

        audit.admit(
            _one(experiment_id="A-E2", actual_usd=2.5), [_one(actual_usd=4.0)], grants
        )

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("budget_usd", -1),
            ("budget_usd", float("nan")),
            ("budget_usd", float("inf")),
            ("budget_usd", True),
            ("attempts", -1),
            ("attempts", 1.5),
            ("mode", "unbounded"),
            ("words", " "),
        ],
    )
    def test_a_grant_is_checked_before_it_is_believed(self, field, value):
        with pytest.raises(audit.ExperimentError):
            _grant(**{field: value})

    def test_a_grant_file_holds_one_audit(self, tmp_path):
        audit.append_grant(_grant(audit_id="B"), tmp_path)
        (tmp_path / "grants" / "B.jsonl").rename(tmp_path / "grants" / "A.jsonl")

        with pytest.raises(audit.ExperimentError, match="not 'A'"):
            audit.load_grants("A", tmp_path)

    # Permissions.

    def test_a_candidate_needs_an_implement_grant(self):
        with pytest.raises(audit.ExperimentError, match="needs an 'implement'"):
            audit.admit(_one(outcome="refuted", candidate=_candidate()), [], ())

    def test_an_extension_needs_an_extend_grant(self):
        with pytest.raises(audit.ExperimentError, match="needs an 'extend'"):
            audit.admit(_extension(), [_blocked()], [_grant()])

    def test_attempts_stop_at_the_grant(self):
        ledger = [
            _one(experiment_id=f"A-E{n}", outcome="refuted", candidate=_candidate())
            for n in range(3)
        ]
        with pytest.raises(audit.ExperimentError, match="used 3 of 3"):
            audit.admit(
                _one(experiment_id="A-E9", outcome="refuted", candidate=_candidate()),
                ledger,
                [_grant()],
            )

    def test_an_extension_is_not_an_improvement_attempt(self):
        audit.admit(_extension(), [_blocked()], [_grant(mode="extend", attempts=0)])

    # References and quality criteria.

    @pytest.mark.parametrize(
        "path",
        [
            "evals/corpus/01-payments-checkout/claims/stride.json",
            "evals/corpus/01-payments-checkout/rulings.json",
            "evals/corpus/01-payments-checkout/case.json",
            "evals/answers/01-payments-checkout.json",
            "evals/review/votes/mstarks01.jsonl",
            "evals/harness/scorer.py",
            "evals/harness/promotion.py",
            ".github/workflows/ci.yml",
        ],
    )
    def test_a_candidate_may_not_touch_a_reference_or_a_gate(self, path):
        candidate = _candidate(paths=[path])
        with pytest.raises(audit.ExperimentError, match="touches"):
            audit.admit(_one(outcome="refuted", candidate=candidate), [], [_grant()])

    def test_every_protected_path_exists(self):
        """A protected prefix that names nothing protects nothing."""
        root = Path(audit.REPO_ROOT)
        assert [one for one in audit.PROTECTED if not (root / one).exists()] == []

    def test_a_move_out_of_a_protected_path_names_both_ends(self, tmp_path):
        def git(*argv):
            subprocess.run(
                ["git", *argv], cwd=tmp_path, check=True, capture_output=True
            )

        git("init", "-q")
        git("config", "user.email", "test@example.com")
        git("config", "user.name", "test")
        held = tmp_path / "evals/corpus/01/rulings.json"
        held.parent.mkdir(parents=True)
        held.write_text("{}\n", "utf-8")
        git("add", ".")
        git("commit", "-qm", "base")
        base = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        git("mv", "evals/corpus/01/rulings.json", "elsewhere.json")
        git("commit", "-qm", "move")

        paths = audit.paths_between(base, "HEAD", tmp_path)
        assert paths is not None
        assert audit.protected(paths)

    def test_record_reads_the_paths_from_git_not_from_the_row(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(audit, "EXPERIMENTS_DIR", tmp_path)
        monkeypatch.setattr(audit, "lookup", lambda **_: [])
        monkeypatch.setattr(
            audit, "paths_between", lambda *_: ("evals/harness/scorer.py",)
        )
        audit.append_grant(_grant(), tmp_path)
        written = tmp_path / "row.json"
        written.write_text(
            json.dumps(_row(outcome="refuted", candidate=_candidate(paths=[]))),
            "utf-8",
        )

        with pytest.raises(audit.ExperimentError, match="scorer"):
            audit.record(written)
        assert audit.load(tmp_path) == ()


class TestARuleIsARow:
    """Item 4: a diagnostic rule carries its five parts, and rests on the record."""

    def _rule(self, **overrides) -> dict:
        held = {
            "checks": "read the losses block for place and unled",
            "remedies": "none recovered a must-find outside the band",
            "limits": "STRIDE only; four Baselines; one scorer version",
            "supporting": ["A-E1"],
        }
        held.update(overrides)
        return held

    def test_a_rule_rests_on_experiments_on_record(self):
        rule = _one(experiment_id="A-E2", rule=self._rule(supporting=["Z-E9"]))
        with pytest.raises(audit.ExperimentError, match="Z-E9"):
            audit.admit(rule, [_one()], ())

    def test_a_rule_names_its_support(self):
        with pytest.raises(audit.ExperimentError, match="no supporting"):
            _one(rule=self._rule(supporting=[]))

    def test_a_well_formed_rule_is_admitted(self):
        audit.admit(_one(experiment_id="A-E2", rule=self._rule()), [_one()], ())


class TestAKeptChangeNamesItsRegressions:
    """#1092: a kept change ran the regressions it can reach, and says which."""

    @pytest.mark.parametrize("ran", [[], [" "]], ids=["none", "blank"])
    def test_a_kept_change_with_no_regressions_is_refused(self, ran):
        kept = _candidate(verdict="kept", reason="", regressions=ran)
        with pytest.raises(audit.ExperimentError, match="regressions"):
            audit.admit(_one(outcome="supported", candidate=kept), [], [_grant()])

    def test_the_regressions_survive_a_write_and_a_read(self, tmp_path):
        ran = ["tests/test_evals_audit_loop.py"]
        kept = _one(
            outcome="supported",
            candidate=_candidate(verdict="kept", reason="", regressions=ran),
        )
        audit.admit(kept, [], [_grant()])
        audit.append(kept, tmp_path)

        (loaded,) = audit.load(tmp_path)
        assert loaded.candidate is not None
        assert loaded.candidate.regressions == tuple(ran)

    def test_a_reverted_change_owes_none(self):
        audit.admit(_one(outcome="refuted", candidate=_candidate()), [], [_grant()])


class TestASharedChangeAnswersForEveryPackage:
    """#1093: a candidate says what it means for each package in ``PACKAGES``."""

    def test_a_package_left_unanswered_is_refused(self):
        held = _candidate()
        missing = min(PACKAGES)
        del held["parity"][missing]
        with pytest.raises(
            audit.ExperimentError, match=f"no parity answer for {missing}"
        ):
            audit.admit(_one(outcome="refuted", candidate=held), [], [_grant()])

    def test_a_package_not_in_packages_is_refused(self):
        held = _candidate()
        held["parity"]["strdie"] = "nothing"
        with pytest.raises(audit.ExperimentError, match="strdie"):
            audit.admit(_one(outcome="refuted", candidate=held), [], [_grant()])

    def test_a_blank_answer_is_not_an_answer(self):
        held = _candidate()
        held["parity"][min(PACKAGES)] = " "
        with pytest.raises(audit.ExperimentError, match="parity"):
            _one(outcome="refuted", candidate=held)


HOLDOUT = "15-multitenant-invoicing"


def _reader(n: int, case: str = HOLDOUT) -> audit.Experiment:
    return _one(experiment_id=f"A-E{n}", holdout_reads=[case])


class TestAHoldoutIsReadAFixedNumberOfTimes:
    """#1093: a holdout's exposure is counted, and a case read too often is retired."""

    def test_the_reads_are_counted_per_case(self):
        ledger = [_reader(1), _reader(2), _reader(3, "14-loyalty-oauth-platform")]

        assert audit.exposure(ledger) == {
            HOLDOUT: ("A-E1", "A-E2"),
            "14-loyalty-oauth-platform": ("A-E3",),
        }

    def test_the_last_permitted_read_is_admitted(self):
        ledger = [_reader(n) for n in range(audit.HOLDOUT_READS - 1)]

        audit.admit(_reader(9), ledger, (), holdouts=frozenset({HOLDOUT}))

    def test_a_read_past_the_limit_is_refused(self):
        ledger = [_reader(n) for n in range(audit.HOLDOUT_READS)]
        with pytest.raises(audit.ExperimentError, match="no longer unseen"):
            audit.admit(_reader(9), ledger, (), holdouts=frozenset({HOLDOUT}))

    def test_a_tuned_case_is_not_a_holdout_read(self):
        with pytest.raises(audit.ExperimentError, match="not a holdout case"):
            audit.admit(
                _reader(1, "01-payments-checkout"),
                [],
                (),
                holdouts=frozenset({HOLDOUT}),
            )

    def test_the_corpus_names_its_holdouts(self):
        """The reader is the corpus's own ``holdout`` flag, never a list here."""
        found = audit.holdout_cases()

        assert found
        assert HOLDOUT in found
        assert "01-payments-checkout" not in found

    def test_the_command_prints_each_holdout_with_its_readers(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setattr(audit, "EXPERIMENTS_DIR", tmp_path)
        audit.append(_reader(1), tmp_path)

        assert audit.command_experiments(_arguments(holdouts=None)) == 0
        printed = capsys.readouterr().out
        assert f"{HOLDOUT}: read 1 of {audit.HOLDOUT_READS} times: A-E1" in printed
        assert "14-loyalty-oauth-platform: read 0 of" in printed


class TestAReplayIsNotARepeat:
    """#1088: an independent repeat needs fresh model output; a replay has none."""

    def _repeat(self, **overrides) -> audit.Experiment:
        held = {"experiment_id": "A-E2", "replicates": "A-E1", "actual_usd": 0.3}
        held.update(overrides)
        return _one(**held)

    def test_a_repeat_with_a_paid_call_is_admitted(self):
        audit.admit(self._repeat(), [_one()], [_grant()])

    @pytest.mark.parametrize("actual", [None, 0.0])
    def test_a_replay_naming_a_repeat_is_refused(self, actual):
        with pytest.raises(audit.ExperimentError, match="a replay is not a repeat"):
            audit.admit(self._repeat(actual_usd=actual), [_one()], [_grant()])

    def test_a_repeat_names_an_experiment_on_record(self):
        with pytest.raises(audit.ExperimentError, match="not an earlier experiment"):
            audit.admit(self._repeat(replicates="Z-E9"), [_one()], [_grant()])

    def test_an_experiment_does_not_repeat_itself(self):
        with pytest.raises(audit.ExperimentError, match="not an earlier experiment"):
            audit.admit(self._repeat(experiment_id="A-E1"), [_one()], [_grant()])

    def test_the_repeats_are_counted_and_printed(self, tmp_path):
        audit.append(_one(), tmp_path)
        audit.append(self._repeat(), tmp_path)
        audit.append(self._repeat(experiment_id="A-E3"), tmp_path)

        assert audit.repeats(audit.load(tmp_path)) == {"A-E1": ("A-E2", "A-E3")}
        found = audit.lookup(audit_id="A", directory=tmp_path, root=tmp_path)
        printed = str(audit.render(found))
        assert "repeated:  2 independent repeat(s): A-E2, A-E3" in printed
        assert "repeats:   A-E1" in printed
