"""What the eval CLI claims about a sweep it certified.

``certified`` is narrow by design (see :mod:`analysis_service.certification`): it
means no *observed* fingerprint went unblessed, and is therefore vacuously true
of a sweep that observed none. Printing it alone is how a run that certified
nothing came to announce "all node fingerprints blessed" and write
``"trusted": true`` into its artifact. These pin the two halves together.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from analysis_service.certification import CertifyResult, UncertifiedNode
from analysis_service.deployment import Deployment
from evals.harness import (
    comparison,
    instruction_delta,
    ledger,
    queue,
    run,
    standings,
    submit,
)
from evals.harness.modes import AttributeCheck, ExtractionScore, render_extraction
from evals.harness.reference import CorpusError
from evals.harness.run import _models_record, _print_certification
from tests.factories import TEST_CREDENTIAL_ENV, TEST_TIER_ENV

BLESSED = "a" * 64


def test_a_complete_clean_run_reports_all_blessed(capsys):
    _print_certification(CertifyResult(certified=True))

    assert "all node fingerprints blessed" in capsys.readouterr().out


def test_an_empty_observation_set_is_never_reported_as_blessed(capsys):
    """The regression: certified is True here, and nothing was checked."""
    result = CertifyResult(certified=True, unexercised=("base", "strong"))
    assert result.certified and not result.complete

    _print_certification(result)

    out = capsys.readouterr().out
    assert "all node fingerprints blessed" not in out
    assert "INCOMPLETE" in out
    assert "base, strong" in out
    assert "untrusted" in out


def test_an_uncertified_run_names_the_node_and_its_hash(capsys):
    _print_certification(
        CertifyResult(
            certified=False,
            uncertified=(UncertifiedNode(node="critic", fingerprint=BLESSED),),
        )
    )

    out = capsys.readouterr().out
    assert "UNCERTIFIED" in out
    assert f"critic: {BLESSED}" in out
    assert "all node fingerprints blessed" not in out


def test_an_incomplete_and_uncertified_run_reports_both(capsys):
    _print_certification(
        CertifyResult(
            certified=False,
            uncertified=(UncertifiedNode(node="extract", fingerprint=BLESSED),),
            unexercised=("strong",),
        )
    )

    out = capsys.readouterr().out
    assert "INCOMPLETE" in out
    assert "UNCERTIFIED" in out


class TestWhatAnExtractionSweepPrints:
    """The attribute numbers are printed, and printed as an instrument.

    An extraction sweep used to print nothing about what it extracted, so the
    only reader of its numbers was whoever opened the JSON. These pin the two
    properties that make the measurement useful at the terminal: the split by
    attribute is there, and nothing on the line reads as a gate
    ([#195](https://github.com/mstarks01/work-agent/issues/195)).
    """

    def score(self, *, agreeing: bool) -> ExtractionScore:
        blessed = "network" if agreeing else "tenant"
        return ExtractionScore(
            case_id="01-payments-checkout",
            matched=("boundary:core-services",),
            missing=(),
            extra=(),
            crossings_match=True,
            attributes=(
                AttributeCheck(
                    element_id="boundary:core-services",
                    attribute="kind",
                    blessed=blessed,
                    extracted="network",
                ),
            ),
        )

    def test_a_disagreeing_attribute_is_named_without_a_verdict(self, capsys):
        render_extraction([self.score(agreeing=False)])

        out = capsys.readouterr().out
        assert "attributes 0/1" in out
        assert "kind" in out
        assert "instrument, non-gating" in out
        assert "FAIL" not in out

    def test_the_element_numbers_are_printed_beside_the_attribute_ones(self, capsys):
        render_extraction([self.score(agreeing=True)])

        out = capsys.readouterr().out
        assert "recall 1.00" in out
        assert "precision 1.00" in out
        assert "crossings match" in out
        assert "1/1 agree (100%)" in out


class TestTheArtifactCanActuallyBeWritten:
    """The artifact is built with ``json.dumps`` and only on a live sweep.

    That combination is why a plain type error survived here unseen: no offline
    test builds the artifact, and the one code path that does needs provider
    credentials. A live run then failed *after* every case had been paid for,
    with the numbers already computed and nowhere to put them.

    So the pieces the artifact assembles are checked for encodability here,
    where it costs nothing.
    """

    @pytest.fixture
    def deployment(self):
        return Deployment.from_env(env={**TEST_TIER_ENV, **TEST_CREDENTIAL_ENV})

    def test_the_models_record_is_json_encodable(self, deployment):
        """``tiers.tiers`` maps to pydantic ``TierSelection`` models, which
        ``json.dumps`` cannot encode and which reach the artifact whole unless
        this record dumps them."""
        json.dumps(_models_record(deployment))

    def test_the_record_keeps_the_pair_it_is_read_for(self, deployment):
        """Encodable is not enough: ``promote`` reads the vendor and model back
        off a finished sweep, so dumping must not flatten them away."""
        record = _models_record(deployment)

        assert (
            record["tiers"]["base"]["vendor"]
            == TEST_TIER_ENV["ANALYSIS_MODEL_BASE_VENDOR"]
        )
        assert (
            record["tiers"]["strong"]["model"]
            == TEST_TIER_ENV["ANALYSIS_MODEL_STRONG_MODEL"]
        )


class TestTheCommandTable:
    """A table nobody compares to its registry fails as quietly as a branch.

    There is no registry outside the table here — the commands *are* the
    registry — so what these check is the two ways a table can go stale: an
    entry nothing implements, and an implementation no entry reaches.
    """

    def test_every_entry_names_something_callable(self):
        for name, command in run.COMMANDS.items():
            assert callable(command.run), f"{name} runs nothing"
            assert command.help, f"{name} says nothing in --help"

    def test_every_command_function_is_in_the_table(self):
        """The quiet failure: a ``command_*`` added and never keyed.

        Walked over the harness modules the table draws from, because a command
        lives beside the subject it reads and this is what says so.
        """
        reachable = {command.run for command in run.COMMANDS.values()}
        modules = (
            run,
            comparison,
            instruction_delta,
            ledger,
            queue,
            standings,
            submit,
        )
        for module in modules:
            for attribute in dir(module):
                if not attribute.startswith("command_"):
                    continue
                function = getattr(module, attribute)
                assert function in reachable, (
                    f"{module.__name__}.{attribute} is a command nothing can"
                    " reach; add it to run.COMMANDS or delete it"
                )

    def test_the_parser_offers_exactly_the_table(self, capsys):
        """``main`` walks the table, so the two cannot drift apart."""
        with pytest.raises(SystemExit):
            run.main(["--help"])
        printed = capsys.readouterr().out
        for name in run.COMMANDS:
            assert name in printed, f"{name} is keyed but --help does not offer it"

    def test_a_name_the_table_does_not_hold_is_refused(self):
        with pytest.raises(SystemExit):
            run.main(["not-a-command"])

    def test_a_corpus_that_does_not_load_ends_in_one_line(self, monkeypatch, capsys):
        """Every command reads the corpus through one loader, so they refuse
        through one guard: the case and the field, and no traceback."""
        # A command that declares no arguments of its own, so the line under
        # test is the guard rather than the parser.
        name = next(
            key
            for key, command in sorted(run.COMMANDS.items())
            if command.arguments is None
        )

        def raise_corpus_error(args):
            raise CorpusError("03-batch-data-pipeline: case.json: 1 validation error")

        monkeypatch.setitem(
            run.COMMANDS,
            name,
            dataclasses.replace(run.COMMANDS[name], run=raise_corpus_error),
        )
        assert run.main([name]) == 1
        printed = capsys.readouterr()
        assert printed.err.startswith("cannot read the corpus:")
        assert "03-batch-data-pipeline" in printed.err

    @pytest.mark.parametrize("name", sorted(run.COMMANDS))
    def test_each_command_parses_its_own_help(self, name, capsys):
        """Every entry builds a subparser that argparse accepts."""
        with pytest.raises(SystemExit) as exit_code:
            run.main([name, "--help"])
        assert exit_code.value.code == 0
        assert capsys.readouterr().out.startswith("usage:")
