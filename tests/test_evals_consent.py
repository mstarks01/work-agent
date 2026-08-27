"""The estimate gate: what it offers, and what it accepts as consent.

There is no ceiling here, so nothing tests one. What is tested is #334's
actual mechanism: every amount carries one of three labels, an amount exists
exactly when one can be stated, acceptance is typing the amount back, and a
run that outspends what was accepted re-prompts a person or stops a script.

The one thing a test must never do is let a rote answer through, so the
refusals are asserted one by one: an enter, a ``y``, a nearly-right number.
"""

from __future__ import annotations

import json

import pytest

from evals.harness import consent, prices
from evals.harness.artifact import RepoCommit
from evals.harness.consent import UNKNOWN, Estimate, Refused, gate, hold
from evals.harness.prices import UnitPrices
from stride_service.report import NodeRun, TokenUsage

ARTIFACT_COMMIT = RepoCommit(commit="c" * 40, clean=True)

IDENTITY = {
    "repo_commit": "c" * 40,
    "corpus_digest": "d" * 64,
    "models": {"base": "openai/gpt-base", "strong": "openai/gpt-5.6"},
    "sampling": {"strong": {"temperature": 0.2}},
    "frameworks": ["stride"],
}
ROUTES = {"base": "openai/gpt-base", "strong": "openai/gpt-5.6"}


@pytest.fixture
def priced(monkeypatch):
    """A price map the tests own; ``mystery/model`` is the one it misses."""
    rates = {
        "openai/gpt-base": UnitPrices("openai/gpt-base", 1e-6, 4e-6, 1e-7),
        "openai/gpt-5.6": UnitPrices("openai/gpt-5.6", 2e-6, 8e-6, 2e-7),
    }
    monkeypatch.setattr(prices, "unit_prices", rates.get)
    monkeypatch.setattr(consent, "unit_prices", rates.get)
    return rates


def merged(root, name, identity, actual, output_rate=8e-6, sweeps=1):
    """A merged Baseline on disk, priced as its manifest records.

    ``sweeps`` repeats the entry, which is what a Baseline gaining runs looks
    like: every sweep of one configuration prices the same models, so the
    price rows repeat and the recorded actual stays a per-sweep figure.
    """
    directory = root / "evals" / "baselines" / name
    directory.mkdir(parents=True)
    entries = [
        {
            "artifact": f"ada-{index:04d}.json",
            "submitted_by": "ada",
            "cost": {
                "actual_usd": actual,
                "unit_prices": [
                    {
                        "model": "openai/gpt-5.6",
                        "input_per_token": 2e-6,
                        "output_per_token": output_rate,
                        "cache_read_per_token": 2e-7,
                    }
                ],
                "unpriced": [],
                "fallbacks": {},
            },
        }
        for index in range(sweeps)
    ]
    (directory / "baseline.json").write_text(
        json.dumps({"name": name, "identity": identity, "sweeps": entries}),
        encoding="utf-8",
    )
    return directory


def node(model="openai/gpt-5.6", completion=1000):
    return NodeRun(
        node="critic",
        model=model,
        requested_model=model,
        duration_ms=10,
        usage=TokenUsage(prompt_tokens=0, completion_tokens=completion),
    )


class TestTheEstimate:
    def test_an_exact_configuration_match_is_a_recorded_number(self, tmp_path, priced):
        merged(tmp_path, "one", IDENTITY, actual=0.60)
        estimate = consent.estimate(IDENTITY, ROUTES, tmp_path)
        assert estimate.label == "recorded"
        assert estimate.amount_usd == pytest.approx(0.60)
        assert "exact configuration" in " ".join(estimate.lines)

    def test_another_baseline_lends_its_tokens_as_a_guess(self, tmp_path, priced):
        merged(tmp_path, "other", {**IDENTITY, "repo_commit": "e" * 40}, actual=0.60)
        estimate = consent.estimate(IDENTITY, ROUTES, tmp_path)
        assert estimate.label == "estimated"
        assert estimate.amount_usd is not None
        assert "borrowed" in " ".join(estimate.lines)

    def test_the_borrowed_guess_does_not_move_with_the_lender_sweep_count(
        self, tmp_path, priced
    ):
        """The ratio is a property of the two configurations, not of run count.

        A lent actual is a mean over the lender's sweeps, so a divisor that
        counted every sweep's price rows divided a per-sweep number by a
        per-directory one: a ten-sweep Baseline lent a tenth of its own cost,
        and the contributor consented to that.
        """
        amounts = []
        for sweeps in (1, 2, 5, 10):
            root = tmp_path / f"root-{sweeps}"
            merged(
                root,
                "other",
                {**IDENTITY, "repo_commit": "e" * 40},
                actual=0.60,
                sweeps=sweeps,
            )
            amounts.append(consent.estimate(IDENTITY, ROUTES, root).amount_usd)

        assert amounts[0] is not None
        assert all(amount == pytest.approx(amounts[0]) for amount in amounts)

    def test_a_dearer_model_makes_the_borrowed_guess_dearer(self, tmp_path, priced):
        merged(
            tmp_path,
            "other",
            {**IDENTITY, "repo_commit": "e" * 40},
            actual=0.60,
            output_rate=4e-6,
        )
        estimate = consent.estimate(IDENTITY, ROUTES, tmp_path)
        # Their sweep recorded a 4e-6 output rate; this run's models total
        # 1.2e-5, so the guess scales up rather than repeating their number.
        assert estimate.amount_usd is not None
        assert estimate.amount_usd > 0.60

    def test_no_merged_baseline_states_no_amount(self, tmp_path, priced):
        estimate = consent.estimate(IDENTITY, ROUTES, tmp_path)
        assert estimate.label == "unpriced"
        assert estimate.amount_usd is None
        assert "no merged Baseline" in " ".join(estimate.lines)

    def test_a_model_the_map_misses_states_no_amount(self, tmp_path, priced):
        routes = {**ROUTES, "strong": "mystery/model"}
        estimate = consent.estimate(IDENTITY, routes, tmp_path)
        assert estimate.label == "unpriced"
        assert estimate.amount_usd is None
        assert "no price for strong (mystery/model)" in " ".join(estimate.lines)

    def test_an_amount_exists_exactly_when_one_can_be_stated(self):
        """The invariant #334 forbids breaking: no zero standing in for no idea."""
        with pytest.raises(ValueError, match="disagree"):
            Estimate(label="unpriced", amount_usd=0.0, lines=())
        with pytest.raises(ValueError, match="disagree"):
            Estimate(label="estimated", amount_usd=None, lines=())


class TestTypedAcceptance:
    def stated(self, amount=4.80):
        return Estimate(label="estimated", amount_usd=amount, lines=())

    def test_typing_the_amount_accepts(self, capsys):
        assert gate(self.stated(), None, lambda _: "4.80") == pytest.approx(4.80)

    def test_a_dollar_sign_is_tolerated(self):
        assert gate(self.stated(), None, lambda _: "$4.80") == pytest.approx(4.80)

    @pytest.mark.parametrize("rote", ["", " ", "y", "yes", "Y", "\n"])
    def test_no_rote_answer_ever_proceeds(self, rote):
        """The whole point: a habit cannot spend money for you."""
        with pytest.raises(Refused, match="not"):
            gate(self.stated(), None, lambda _: rote)

    def test_a_nearly_right_number_is_refused(self):
        with pytest.raises(Refused):
            gate(self.stated(), None, lambda _: "4.8")
        with pytest.raises(Refused):
            gate(self.stated(), None, lambda _: "48.00")

    def test_an_unpriced_estimate_is_accepted_by_naming_the_unknown(self):
        estimate = Estimate(label="unpriced", amount_usd=None, lines=())
        assert gate(estimate, None, lambda _: UNKNOWN) is None
        with pytest.raises(Refused):
            gate(estimate, None, lambda _: "0")

    def test_the_prompt_carries_the_amount(self):
        asked = []
        gate(self.stated(), None, lambda prompt: asked.append(prompt) or "4.80")
        assert "4.80" in asked[0]


class TestTheScriptFlag:
    def stated(self, amount=4.80):
        return Estimate(label="estimated", amount_usd=amount, lines=())

    def test_a_number_at_or_over_the_estimate_proceeds(self):
        assert gate(self.stated(), "5") == pytest.approx(5.0)
        assert gate(self.stated(), "4.80") == pytest.approx(4.80)

    def test_a_number_under_the_estimate_refuses(self):
        with pytest.raises(Refused, match="raise --accept-cost"):
            gate(self.stated(), "1.00")

    def test_unknown_does_not_answer_a_stated_estimate(self):
        with pytest.raises(Refused, match="does not answer"):
            gate(self.stated(), UNKNOWN)

    def test_a_number_does_not_answer_an_unpriced_estimate(self):
        estimate = Estimate(label="unpriced", amount_usd=None, lines=())
        with pytest.raises(Refused, match="answers nothing"):
            gate(estimate, "50")
        assert gate(estimate, UNKNOWN) is None

    def test_a_value_that_is_neither_refuses(self):
        with pytest.raises(Refused, match="neither a number"):
            gate(self.stated(), "cheap")


class TestTheHold:
    def test_a_run_inside_what_was_accepted_continues(self, priced):
        assert hold(10.0, [node(completion=1000)], ["02"], None, ran=1) == 10.0

    def test_a_script_that_outspends_its_acceptance_stops(self, priced):
        with pytest.raises(Refused, match="no hand here"):
            hold(0.001, [node(completion=1_000_000)], ["02", "03"], None, ran=1)

    def test_the_stop_names_the_spend_and_the_cases_not_run(self, priced, capsys):
        with pytest.raises(Refused):
            hold(0.001, [node(completion=1_000_000)], ["02", "03"], None, ran=1)
        printed = capsys.readouterr().out
        assert "02, 03" in printed
        assert "2 case(s) have not run" in printed

    def test_a_person_may_accept_the_larger_amount_and_continue(self, priced):
        executions = [node(completion=1_000_000)]
        so_far = consent.spent(executions)
        projected = so_far * 2  # one case ran, one remains, at the same rate
        accepted = hold(0.001, executions, ["02"], lambda _: f"{projected:.2f}", ran=1)
        assert accepted == pytest.approx(projected)

    def test_a_person_who_declines_stops_the_sweep(self, priced):
        with pytest.raises(Refused):
            hold(0.001, [node(completion=1_000_000)], ["02"], lambda _: "", ran=1)

    def test_an_accepted_unknown_has_nothing_to_measure_against(self, priced):
        """Accepting ``unknown`` is a real acceptance, so the hold never fires."""
        assert hold(None, [node(completion=1_000_000)], ["02"], None, ran=1) is None

    def test_the_re_prompt_offers_the_whole_sweep_and_not_the_sunk_spend(self, priced):
        """One overrun costs one prompt, not one at every case that follows.

        Accepting what is already spent puts the run over its own figure again
        on the very next case. The offer is the projection instead, so the
        amount in force covers the cases still to run.
        """
        executions = [node(completion=1_000_000)]
        so_far = consent.spent(executions)
        offered: list[str] = []

        def answer(prompt: str) -> str:
            offered.append(prompt)
            return prompt.split("(")[1].split(")")[0]

        accepted = hold(0.001, executions, ["02", "03", "04"], answer, ran=1)

        assert accepted == pytest.approx(so_far * 4)
        assert accepted is not None and accepted > so_far
        # The next boundary, at the same rate, is inside the new figure.
        assert hold(accepted, executions * 2, ["03", "04"], None, ran=2) == accepted

    def test_a_run_with_nothing_recorded_yet_offers_what_it_spent(self, priced):
        """``ran`` at zero has no rate to project from, so the spend stands."""
        executions = [node(completion=1_000_000)]
        so_far = consent.spent(executions)
        accepted = hold(0.001, executions, ["02"], lambda _: f"{so_far:.2f}", ran=0)
        assert accepted == pytest.approx(so_far)


class TestADirtyTreeIsNamedBeforeTheSpend:
    """A sweep over uncommitted edits is legitimate, and cannot be contributed.

    ``TUNING.md``'s ordinary loop sweeps over uncommitted prompt edits, so the
    run must not refuse one. What it must not do either is let the money go and
    leave the contributor to discover at ``submit`` that the artifact can never
    become a Baseline — the identity needs a clean commit. So the fact rides in
    the block being accepted.
    """

    def gate_lines(self, clean, capsys):
        from dataclasses import replace

        estimate = Estimate(label="estimated", amount_usd=1.0, lines=("a line",))
        if clean is not True:
            estimate = replace(
                estimate, lines=(*estimate.lines, "never contributed as a Baseline")
            )
        gate(estimate, "1.00")
        return capsys.readouterr().out

    def test_a_dirty_tree_says_so_in_the_accepted_block(self, capsys):
        assert "never contributed as a Baseline" in self.gate_lines(False, capsys)

    def test_a_clean_tree_says_nothing_extra(self, capsys):
        assert "never contributed as a Baseline" not in self.gate_lines(True, capsys)

    def test_the_note_is_a_seam_the_gate_reads(self):
        """The real wiring, not a grep: what command_run folds into the block."""
        from evals.harness.artifact import UNRECORDED, RepoCommit
        from evals.harness.run import _contribution_note

        clean = RepoCommit(commit="c" * 40, clean=True)
        dirty = RepoCommit(commit="c" * 40, clean=False)
        unrecorded = RepoCommit(commit=UNRECORDED, clean=None)

        assert _contribution_note(clean) == ()
        assert "never contributed as a Baseline" in _contribution_note(dirty)[0]
        assert _contribution_note(unrecorded), (
            "a sweep that cannot name its commit cannot be contributed either"
        )


class TestAStoppedSweepSaysSo:
    """A partial record must not read as a whole one (#334)."""

    def artifact(self, stopped, cases):
        from dataclasses import replace

        from evals.harness.artifact import build
        from evals.harness.instruments import ModeRun, Sweep
        from stride_service.certification import CertifyResult

        run = replace(ModeRun.empty(("stride",)), stopped_before=stopped)
        return build(
            mode="end-to-end",
            cases=cases,
            models={},
            certification=CertifyResult(certified=False),
            provenance=run.provenance,
            usage={},
            latency={},
            structural_failures=[],
            payloads=[],
            trusted=False,
            sweep=Sweep(run=run),
            commit=ARTIFACT_COMMIT,
            corpus="d" * 64,
        )

    def test_a_finished_sweep_stopped_nothing(self):
        assert self.artifact((), ["01", "02"])["stopped"] == []

    def test_a_stopped_sweep_names_the_cases_it_never_attempted(self):
        artifact = self.artifact(("02", "03"), ["01"])
        assert artifact["stopped"] == ["02", "03"]
        # And the cases it claims are only the ones that ran, which is what
        # makes it fail the Baseline full-corpus rule rather than pass it.
        assert artifact["cases"] == ["01"]
