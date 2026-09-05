"""The estimate gate: what it offers, and what it accepts as consent.

There is no ceiling here, so nothing tests one. What is tested is #334's
actual mechanism: every amount carries one of three labels, an amount exists
exactly when one can be stated, acceptance is typing the amount back, and a
run that outspends what was accepted re-prompts a person or stops a script.

The one thing a test must never do is let a rote answer through, so the
refusals are asserted one by one: an enter, a ``y``, a nearly-right number.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from analysis_service.budgets import measured_tokens
from analysis_service.identity import IDENTITY_VERSION, build_identity
from analysis_service.report import NodeRun, TokenUsage
from analysis_service.sampling import TierSampling
from evals.harness import consent, modes, prices, run
from evals.harness.artifact import ARTIFACT_VERSION, RepoCommit
from evals.harness.consent import UNKNOWN, Estimate, Refused, gate, hold
from evals.harness.prices import UnitPrices
from evals.harness.provenance import RunProvenance
from tests.factories import SAMPLE_INSTRUCTIONS, sample_fingerprint

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


#: One node per tier, so a test can weight the tiers against each other. The
#: estimate reprices these counts, so what they are matters and the recorded
#: cost beside them does not.
TIER_NODES = {"extract": "base", "critic": "strong"}


def artifact_document(usage, seed=1):
    """One admissible artifact whose nodes carry the tiers ``ROUTES`` names.

    ``usage`` is ``{node: (prompt, cached, completion)}``. The borrowed
    estimate prices exactly these counts, so they are the whole point of the
    fixture — the recorded actual is no longer read for a guess.
    """
    tiers = {tier: TierSampling(temperature=0.2, seed=7) for tier in ROUTES}
    node_runs = {
        node: [
            {
                "node": node,
                "tier": tier,
                "requested_model": ROUTES[tier],
                "served_model": ROUTES[tier],
                "instruction_sha256": SAMPLE_INSTRUCTIONS,
                "generation_fingerprint": sample_fingerprint(ROUTES[tier], tiers[tier]),
            }
        ]
        for node, tier in TIER_NODES.items()
        if node in usage
    }
    provenance = RunProvenance.model_validate(
        {
            "identity_version": IDENTITY_VERSION,
            "build": dict(build_identity()),
            "sampling_config_version": 1,
            "tiers_config_version": 1,
            "sampling": tiers,
            "node_runs": node_runs,
        }
    )
    return {
        "artifact_version": ARTIFACT_VERSION,
        "mode": "end-to-end",
        "cases": ["01-a-case"],
        "trusted": False,
        "structural_failures": [],
        "repo_commit": {"commit": "c" * 40, "clean": True},
        "corpus_digest": "d" * 64,
        "frameworks": ["stride"],
        "certification": {"verdict": "uncertified", "seed": seed},
        "node_usage": {
            node: {
                "prompt_tokens": prompt,
                "cached_prompt_tokens": cached,
                "completion_tokens": completion,
            }
            for node, (prompt, cached, completion) in usage.items()
        },
        "provenance": provenance.to_json(),
    }


#: Even weight across the two tiers, which most tests do not care about.
EVEN_USAGE = {"extract": (1000, 200, 300), "critic": (1000, 200, 300)}


def merged(root, name, identity, actual, sweeps=1, usage=None, recorded_prices=()):
    """A merged Baseline on disk: the manifest, and one artifact per sweep.

    The artifacts are what a borrowed estimate reads; the manifest's recorded
    actual answers only an exact-identity match. ``sweeps`` repeats the pair,
    which is what a Baseline gaining runs looks like. ``recorded_prices`` is
    what this Baseline was priced at when it ran, which the drift disclosure
    compares against the map of today.
    """
    directory = root / "evals" / "baselines" / name
    directory.mkdir(parents=True)
    entries = []
    for index in range(sweeps):
        filename = f"ada-{index:04d}.json"
        (directory / filename).write_text(
            json.dumps(artifact_document(usage or EVEN_USAGE, seed=index)),
            encoding="utf-8",
        )
        entries.append(
            {
                "artifact": filename,
                "submitted_by": "ada",
                "cost": {
                    "actual_usd": actual,
                    "unit_prices": [prices.to_json() for prices in recorded_prices],
                    "unpriced": [],
                    "fallbacks": {},
                },
            }
        )
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

    def test_a_dearer_route_for_this_run_makes_the_guess_dearer(
        self, tmp_path, priced, monkeypatch
    ):
        """The lender supplies counts; this run's own routes supply the prices."""
        merged(tmp_path, "other", {**IDENTITY, "repo_commit": "e" * 40}, actual=0.60)
        before = consent.estimate(IDENTITY, ROUTES, tmp_path).amount_usd

        dearer = {
            **priced,
            "openai/gpt-5.6": UnitPrices("openai/gpt-5.6", 4e-6, 1.6e-5, 4e-7),
        }
        monkeypatch.setattr(consent, "unit_prices", dearer.get)
        after = consent.estimate(IDENTITY, ROUTES, tmp_path).amount_usd

        assert before is not None and after is not None
        assert after > before

    def test_the_guess_follows_the_tier_that_carries_the_tokens(
        self, tmp_path, monkeypatch
    ):
        """Token mix decides the guess, which a ratio over unit prices cannot.

        The lender's base tier is dearer **per token** while its strong tier
        ran a thousand times as many. Making each route ten times dearer in
        turn must therefore move the estimate more for the strong one, because
        that is where the tokens are.

        The ratio this replaced summed one rate per model and divided, so it
        answered the opposite way round: it moved by +5.35 for the base route
        and +0.05 for the strong. The two implementations disagree on the
        sign here, which is what makes this a test rather than a restatement.
        """
        usage = {"extract": (0, 0, 100), "critic": (0, 0, 100_000)}
        merged(
            tmp_path,
            "other",
            {**IDENTITY, "repo_commit": "e" * 40},
            actual=0.60,
            usage=usage,
        )

        def amount_at(base_out: float, strong_out: float) -> float:
            rates = {
                "openai/gpt-base": UnitPrices("openai/gpt-base", 0.0, base_out, 0.0),
                "openai/gpt-5.6": UnitPrices("openai/gpt-5.6", 0.0, strong_out, 0.0),
            }
            monkeypatch.setattr(consent, "unit_prices", rates.get)
            got = consent.estimate(IDENTITY, ROUTES, tmp_path).amount_usd
            assert got is not None
            return got

        flat = amount_at(1e-4, 1e-6)
        dear_base = amount_at(1e-3, 1e-6)
        dear_strong = amount_at(1e-4, 1e-5)

        assert flat == pytest.approx(0.11)
        assert dear_strong - flat == pytest.approx(0.90)
        assert dear_base - flat == pytest.approx(0.09)
        assert dear_strong - flat > dear_base - flat

    def test_the_lender_is_chosen_for_what_it_ran_not_the_directory_order(
        self, tmp_path, priced
    ):
        """Token counts are counts of work, so the workload has to match.

        A selection naming fewer packages fires a lane agent for fewer lanes on
        every case. Lending its counts to a run that selected more understates
        by about that ratio, and sorting by directory name picks the lender by
        an accident of the commit hash its directory is named for.
        """
        mine = {**IDENTITY, "frameworks": ["stride", "asvs"]}
        merged(
            tmp_path,
            "aaa-stride-only",
            {**IDENTITY, "frameworks": ["stride"]},
            actual=0.10,
            usage={"extract": (0, 0, 1_000), "critic": (0, 0, 1_000)},
        )
        merged(
            tmp_path,
            "zzz-both",
            {**mine, "repo_commit": "e" * 40},
            actual=10.0,
            usage={"extract": (0, 0, 100_000), "critic": (0, 0, 100_000)},
        )

        estimate = consent.estimate(mine, ROUTES, tmp_path)

        assert "borrowed from zzz-both" in " ".join(estimate.lines)
        assert estimate.amount_usd == pytest.approx(1.2)

    def test_the_table_order_is_the_precedence_not_the_count(self, tmp_path, priced):
        """A mismatched selection never trades against a mismatched commit.

        Both candidates differ on exactly one component, so counting them ties
        and the name breaks it. The framework selection decides how many agents
        run and the commit only how long their instructions are, so the tie is
        wrong and ``COMPARABLE_ON``'s order settles it.
        """
        mine = {**IDENTITY, "frameworks": ["stride", "asvs"]}
        wrong_selection = {**IDENTITY, "frameworks": ["stride"]}
        wrong_commit = {**mine, "repo_commit": "e" * 40}

        by_selection = consent._mismatches(mine, wrong_selection)
        by_commit = consent._mismatches(mine, wrong_commit)

        # Counting ties them, which is the reading that got this wrong.
        assert sum(by_selection) == sum(by_commit) == 1
        # The table's order does not: sharing the selection wins outright.
        assert by_commit < by_selection

    def test_a_block_reordering_is_not_a_difference(self, tmp_path, priced):
        """``frameworks`` is a selection; its order is the report's, not work."""
        mine = {**IDENTITY, "frameworks": ["stride", "asvs"]}
        reordered = {**IDENTITY, "frameworks": ["asvs", "stride"]}

        assert consent._differences(mine, reordered) == ()

    def test_a_borrowed_number_names_what_the_lender_did_not_share(
        self, tmp_path, priced
    ):
        merged(
            tmp_path,
            "other",
            {**IDENTITY, "corpus_digest": "f" * 64},
            actual=0.60,
        )
        estimate = consent.estimate(IDENTITY, ROUTES, tmp_path)

        joined = " ".join(estimate.lines)
        assert "not this configuration" in joined
        assert "different corpus" in joined

    def test_no_caveat_when_the_lender_shares_everything_that_matters(
        self, tmp_path, priced
    ):
        """Only the models differ, and the repricing is what answers for those."""
        merged(
            tmp_path,
            "other",
            {**IDENTITY, "models": {"base": "old/base", "strong": "old/strong"}},
            actual=0.60,
        )
        estimate = consent.estimate(IDENTITY, ROUTES, tmp_path)

        assert "not this configuration" not in " ".join(estimate.lines)

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


class TestTheDriftDisclosure:
    """#331: the estimate names a price that moved since the Baseline ran.

    The repo pins litellm exactly, so the map moves when somebody bumps the
    pin and every older Baseline drifts at once. A CI check would fail honest
    history the day after; this is disclosed to the one person it bears on.
    """

    RECORDED = (UnitPrices("openai/gpt-5.6", 1e-6, 4e-6, 1e-7),)

    def test_a_moved_price_is_named_with_both_figures(self, tmp_path, priced):
        merged(
            tmp_path,
            "one",
            IDENTITY,
            actual=0.60,
            recorded_prices=self.RECORDED,
        )
        estimate = consent.estimate(IDENTITY, ROUTES, tmp_path)

        drift = [line for line in estimate.lines if "the price map now says" in line]
        assert len(drift) == 1
        assert "openai/gpt-5.6" in drift[0]
        assert "$1.00 in / $4.00 out" in drift[0]  # what the Baseline recorded
        assert "$2.00 in / $8.00 out" in drift[0]  # what the map says today

    def test_a_moved_cache_rate_shows_as_its_own_figure(self, tmp_path, priced):
        """The line prints every rate the comparison reads, cache rate included.

        A Baseline recorded before the map stated a cache rate carries ``None``
        there; one recorded as ``0.0`` differs from a map that now says
        ``None``. Either way the line has to show where the difference is,
        not two equal input/output pairs.
        """
        known = priced["openai/gpt-5.6"]
        recorded = UnitPrices(
            known.model, known.input_per_token, known.output_per_token, None
        )
        merged(tmp_path, "one", IDENTITY, actual=0.60, recorded_prices=(recorded,))
        estimate = consent.estimate(IDENTITY, ROUTES, tmp_path)

        (drift,) = [line for line in estimate.lines if "the price map now says" in line]
        assert "no cache rate" in drift
        assert f"${known.cache_read_per_token * 1e6:.2f} cached" in drift

    def test_an_unmoved_price_says_nothing(self, tmp_path, priced):
        merged(
            tmp_path,
            "one",
            IDENTITY,
            actual=0.60,
            recorded_prices=(priced["openai/gpt-5.6"],),
        )
        estimate = consent.estimate(IDENTITY, ROUTES, tmp_path)

        assert not [line for line in estimate.lines if "price map now" in line]

    def test_the_borrowed_path_discloses_its_lender_drift_too(self, tmp_path, priced):
        """The line names the Baseline it calibrated from, whichever path found it."""
        merged(
            tmp_path,
            "other",
            {**IDENTITY, "repo_commit": "e" * 40},
            actual=0.60,
            recorded_prices=self.RECORDED,
        )
        estimate = consent.estimate(IDENTITY, ROUTES, tmp_path)

        assert estimate.label == "estimated"
        assert any("the price map now says" in line for line in estimate.lines)


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

    def test_a_bigger_denominator_projects_a_smaller_amount(self, priced):
        """Why a skipped case must never count toward ``ran``.

        The rate is the spend over the cases that produced it. Counting a case
        that spent nothing divides by a larger number and offers less than the
        sweep will cost — the one direction a consent gate must not err in. The
        sweep loop therefore passes ``position - len(skipped)``.
        """
        executions = [node(completion=1_000_000)]
        offered: list[float] = []

        def answer(prompt: str) -> str:
            typed = prompt.split("(")[1].split(")")[0]
            offered.append(float(typed))
            return typed

        hold(0.001, executions, ["02", "03"], answer, ran=1)
        hold(0.001, executions, ["02", "03"], answer, ran=2)

        assert offered[0] > offered[1]

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

        from analysis_service.certification import CertifyResult
        from evals.harness.artifact import build
        from evals.harness.instruments import ModeRun, Sweep

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


class TestTheSweepLoopPassesTheCasesThatRan:
    """The call site of :func:`hold`, driven with no provider.

    ``_run_mode`` reaches its seams through the ``modes`` module, so a sweep
    runs offline with those stubbed. What is under test is one expression:
    the loop passes ``position - len(skipped)``, because a case skipped under
    ``--framework`` spent nothing, and counting it divides the spend by a
    larger number and offers less than the sweep will cost.
    """

    class Stop(Exception):
        """Ends the sweep at the prompt; the tail is not what this tests."""

    def offer_at_the_hold(self, monkeypatch, selected):
        """Sweep cases that run or skip per ``selected``; return what was offered.

        Every case that runs spends the same amount, so the projection the
        hold offers is a plain multiple of one case and the arithmetic under
        test is legible in the assertion.
        """
        cases = [SimpleNamespace(id=f"{index:02d}") for index in range(len(selected))]

        monkeypatch.setattr(
            modes,
            "select_frameworks",
            lambda case, only=(): ("stride",) if selected[int(case.id)] else (),
        )
        monkeypatch.setattr(
            modes, "build_eval_pipeline", lambda *args, **kwargs: SimpleNamespace()
        )
        monkeypatch.setattr(
            modes,
            "score_extraction",
            lambda case, result: SimpleNamespace(to_json=dict),
        )

        async def one_extraction(case, pipeline):
            return modes.ExtractionResult(
                case_id=case.id,
                extracted=None,
                issues=(),
                node_runs=(node(completion=1_000_000),),
            )

        monkeypatch.setattr(modes, "run_extraction", one_extraction)

        prompts: list[str] = []

        def ask(prompt: str) -> str:
            prompts.append(prompt)
            raise self.Stop

        with pytest.raises(self.Stop):
            asyncio.run(
                run._run_mode(
                    cases,
                    "extraction",
                    deployment=SimpleNamespace(),
                    accepted=0.0001,
                    ask=ask,
                )
            )
        return float(prompts[-1].split("(")[1].split(")")[0])

    def test_a_skipped_case_does_not_dilute_the_rate(self, priced, monkeypatch):
        """Two cases skipped, then one that spends, with one still to run.

        One case produced the spend, so the offer is that spend plus one more
        case at the same rate. Counting the two skipped cases would divide by
        three and offer a third of a case instead.
        """
        one_case = consent.spent([node(completion=1_000_000)])

        offered = self.offer_at_the_hold(monkeypatch, [False, False, True, True])

        assert offered == pytest.approx(one_case * 2, rel=1e-3)
        assert offered > one_case * (1 + 1 / 3)  # what the diluted rate gave

    def test_no_skipped_case_leaves_the_rate_alone(self, priced, monkeypatch):
        """The subtraction is a no-op when the selection skips nothing."""
        one_case = consent.spent([node(completion=1_000_000)])

        offered = self.offer_at_the_hold(monkeypatch, [True, True, True])

        assert offered == pytest.approx(one_case * 3, rel=1e-3)


class TestTheAcceptedAmountIsTheAmountBilled:
    """The two readers of what a retry costs, tested against each other.

    Not each against its own expectation, which is how they came to disagree:
    `test_budgets.py` asserted the charge and this file never mentioned
    `attempts`, so a sweep offered $9.75 against a $25.35 bill.
    """

    def _nodes(self, attempts):
        return [
            NodeRun(
                node=f"n{i}",
                model="claude-opus-4-5",
                requested_model="claude-opus-4-5",
                duration_ms=100,
                attempts=attempts,
                usage=TokenUsage(
                    prompt_tokens=10_000, completion_tokens=500, total_tokens=10_500
                ),
            )
            for i in range(26)
        ]

    @pytest.mark.parametrize("attempts", [1, 2, 3, 5])
    def test_consent_prices_the_tokens_the_budget_charges(self, attempts):
        nodes = self._nodes(attempts)

        billed = measured_tokens(nodes)
        priced = sum(usage.total_tokens for _, _, usage in consent._calls_of(nodes))

        assert priced == billed

    def test_a_retried_sweep_costs_more_than_an_unretried_one(self):
        """The property the equality above cannot show: that both moved."""
        assert consent.spent(self._nodes(3)) > consent.spent(self._nodes(1))


class TestAnAcceptedAmountIsAnAmountOfMoney:
    @pytest.mark.parametrize("flag", ["inf", "-inf", "nan", "Infinity", "-1"])
    def test_a_value_that_is_not_dollars_is_refused(self, flag):
        """`float()` takes all of these. `x > inf` and `x > nan` are False, so
        either satisfies any estimate and disables `hold` for the whole sweep."""
        offer = consent.Estimate(label="recorded", amount_usd=50.00, lines=())

        with pytest.raises(consent.Refused):
            consent._accept_from_flag(offer, flag)

    def test_a_real_amount_still_accepts(self):
        offer = consent.Estimate(label="recorded", amount_usd=50.00, lines=())

        assert consent._accept_from_flag(offer, "50.00") == 50.00


class TestABorrowedArtifactStaysInsideItsBaseline:
    """`artifact` comes off a committed manifest, and a join is not a bound."""

    def test_an_absolute_artifact_name_is_refused(self, tmp_path):
        """`Path("/baselines/x") / "/etc/passwd"` is `/etc/passwd`: an absolute
        right-hand side replaces the left rather than extending it. The same
        shape was found in `sitting.moved` and `markdown_loader` this round."""
        directory = tmp_path / "baseline"
        directory.mkdir()

        assert consent._reprice(directory, "/etc/hostname", {}) is None

    def test_a_traversing_artifact_name_is_refused(self, tmp_path):
        directory = tmp_path / "baseline"
        directory.mkdir()
        (tmp_path / "elsewhere.json").write_text("{}", encoding="utf-8")

        assert consent._reprice(directory, "../elsewhere.json", {}) is None
