"""One framework's sweep runs the instruments that read it, and no others.

## What this pins

The instrument table exists so that a measurement is declared once rather than
wired into four places. The property that makes the table worth having is
**independence**: a sweep of one **Framework Package** prints and records that
package's instruments, skips the ones that read a record it never produced, and
does not fail inside them.

Before the table, it half-held. A STRIDE-only sweep worked, because every
instrument that could fail was STRIDE's. An ASVS-only sweep raised
``EvalRunError: the report carries no stride analysis block`` from
``_score_runs``, which reaches for STRIDE's claims on every case in the sweep.
No corpus case declares ASVS alone, so nothing caught it — the same shape as
every gap in ``docs/agents/framework-parity.md``: correct when written, wrong
the moment a second package arrived, and silent either way.

## Why these run offline

Nothing here needs a provider. The instruments are folds over records, so a
report built by hand exercises the same path a sweep does, and the matcher is
scripted. That is the point of the seam: the reading is separable from the run
that produced it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.harness.instruments import (
    INSTRUMENTS,
    ModeRun,
    Sweep,
    artifact_blocks,
    render_all,
)
from evals.harness.ledger import Ledger
from evals.harness.reference import load_case
from stride_service.frameworks import PACKAGES
from stride_service.report import FrameworkName
from tests.factories import sample_report
from tests.test_asvs import _block as asvs_block

CORPUS = Path(__file__).resolve().parents[1] / "evals" / "corpus"


def empty_run(frameworks: tuple[FrameworkName, ...]) -> ModeRun:
    """A finished sweep that ran ``frameworks`` and measured nothing.

    What is under test is which instruments a sweep *consults*, and an empty
    measurement set is the cleanest way to ask that with no provider in the
    picture. The shape is ``ModeRun.empty``'s, because ``score`` re-scores a
    finished artifact through the same value and a fixture spelling its own
    would stop testing what runs.
    """
    return ModeRun.empty(frameworks)


class TestOneFrameworkRunsAlone:
    """Each package, swept on its own, consults exactly its own instruments."""

    @pytest.mark.parametrize("framework", sorted(PACKAGES))
    def test_a_lone_package_has_at_least_one_instrument(self, framework):
        """A sweep of one package measures something.

        The check that would fail if a package were carried with nothing
        declaring it — the silent half of the table.
        """
        applicable = [
            name
            for name, instrument in INSTRUMENTS.items()
            if instrument.applies_to((framework,)) and instrument.frameworks
        ]
        assert applicable, (
            f"a sweep of {framework!r} alone would consult no instrument that"
            " reads its record"
        )

    @pytest.mark.parametrize("framework", sorted(PACKAGES))
    def test_a_lone_package_skips_another_package_s_instruments(self, framework):
        """Nothing that reads a record this sweep never produced is consulted."""
        wrong = [
            name
            for name, instrument in INSTRUMENTS.items()
            if instrument.frameworks and framework not in instrument.frameworks
            if instrument.applies_to((framework,))
        ]
        assert not wrong, (
            f"a sweep of {framework!r} alone would consult {wrong}, which read"
            " another package's record"
        )

    @pytest.mark.parametrize("framework", sorted(PACKAGES))
    def test_a_lone_package_renders_without_raising(self, framework, capsys):
        """Both halves of the table print, over a sweep that measured nothing."""
        sweep = Sweep(run=empty_run((framework,)))
        render_all(sweep, scored=False)
        render_all(sweep, scored=True)
        capsys.readouterr()

    @pytest.mark.parametrize("framework", sorted(PACKAGES))
    def test_a_lone_package_writes_every_artifact_key(self, framework):
        """The artifact's shape does not depend on which frameworks ran.

        An ASVS key absent from a STRIDE-only artifact and an ASVS key holding
        an empty list are different claims to whoever compares two sweeps, and
        only the second one is true. So ``applies_to`` gates the rendering and
        never the record.
        """
        every = set(artifact_blocks(Sweep(run=empty_run(tuple(PACKAGES)))))
        alone = set(artifact_blocks(Sweep(run=empty_run((framework,)))))
        assert alone == every


class TestTheTableCoversTheArtifact:
    """The keys the table owns, against the keys a sweep writes."""

    def test_no_two_instruments_own_the_same_key(self):
        """A collision raises rather than letting the later entry win."""
        owners: dict[str, str] = {}
        for name, instrument in INSTRUMENTS.items():
            for key in instrument.artifact(Sweep(run=empty_run(tuple(PACKAGES)))):
                assert key not in owners, (
                    f"{name!r} and {owners[key]!r} both write {key!r}"
                )
                owners[key] = name

    def test_the_instrument_keys_are_the_ones_the_artifact_carried(self):
        """The 21 keys a sweep writes, pinned one by one.

        Pinned as a literal because this is the one place a silent loss would
        not show up as a failing fold: an instrument dropped from the table
        writes one key fewer, and every aggregate over it still reads as a valid
        sweep that measured nothing.
        """
        assert set(artifact_blocks(Sweep(run=empty_run(tuple(PACKAGES))))) == {
            "attribute_aggregate",
            "grounds",
            "grounds_failures",
            "grounds_aggregate",
            "coverage",
            "coverage_totals",
            "instruction",
            "instruction_totals",
            "applicability",
            "applicability_aggregate",
            "applicability_exemplar_delta",
            "over_applied_for_promotion",
            "applicability_yield",
            "applicability_yield_aggregate",
            "scores",
            "exemplar_delta",
            "unlisted_for_promotion",
            "critic_yield",
            "critic_yield_aggregate",
            "writing",
            "writing_aggregate",
        }


class TestTheScoredSplit:
    """The mechanical readings do not depend on a provider being reachable."""

    def test_the_run_level_instruments_render_before_scoring(self, capsys):
        """A sweep whose scoring pass failed still prints the run-level half."""
        sweep = Sweep(run=empty_run(tuple(PACKAGES)))
        render_all(sweep, scored=False)
        printed = capsys.readouterr().out
        assert "coverage:" in printed

    def test_the_scored_instruments_are_the_ones_that_read_scores(self):
        """The two per-package scorers, and the one that reads the ledger.

        ``writing`` is scored for a different reason from the other two: it
        needs no score, but the vote ledger is only loaded on the scored pass,
        and a reading over votes nobody loaded would report every sweep as
        unobjected-to.
        """
        scored = {name for name, i in INSTRUMENTS.items() if i.scored}
        assert scored == {"scores", "critic_yield", "writing"}


class TestScoringSkipsAPackageItDoesNotRead:
    """``_score_runs`` over a report that carries no STRIDE block."""

    def test_an_asvs_only_report_is_skipped_rather_than_raised_on(self):
        """The defect the table's ``frameworks`` declaration closes.

        This raised ``EvalRunError`` before: the STRIDE scorer asked every case
        in the sweep for a block only STRIDE produces.
        """
        from evals.harness.run import _score_runs, optional_block

        report = sample_report(analyses=[asvs_block(1)])
        assert optional_block(report, "stride") is None

        run = _AnalysisRunStub(report)
        scores, yields = _score_runs(
            [_CaseStub()], {"case": run}, _NeverAskedMatcher(), Ledger()
        )
        assert scores == ()
        assert yields == ()


class _CaseStub:
    """A case the sweep produced a run for. Nothing about it is read."""

    id = "case"


class _AnalysisRunStub:
    """Only the two attributes ``_score_runs`` reads off a run."""

    def __init__(self, report, drafts=None):
        self.report = report
        self.merged_drafts = ()
        self.drafts = drafts or {}


class _NeverAskedMatcher:
    """A matcher that fails the test if the skipped path ever consults it."""

    def equivalent(self, *args, **kwargs):
        raise AssertionError("the matcher was asked about a framework it cannot read")


class TestTheDeclaredKeysAreTheWrittenKeys:
    """``Instrument.keys`` against what ``Instrument.artifact`` actually writes.

    The declaration is what :mod:`evals.harness.artifact` builds ``DECLARED_KEYS``
    from, and a declaration that drifted from its writer would let the loader
    demand a key nothing produces — or, worse, stay quiet about one that went
    missing.
    """

    @pytest.mark.parametrize("name", sorted(INSTRUMENTS))
    def test_each_instrument_writes_the_keys_it_declares(self, name):
        instrument = INSTRUMENTS[name]
        written = instrument.artifact(Sweep(run=empty_run(tuple(PACKAGES))))
        assert set(written) == set(instrument.keys)

    def test_the_declared_set_is_the_envelope_plus_every_instrument(self):
        from evals.harness.artifact import DECLARED_KEYS, ENVELOPE_KEYS

        owned = {key for i in INSTRUMENTS.values() for key in i.keys}
        assert DECLARED_KEYS == set(ENVELOPE_KEYS) | owned

    def test_a_sweep_writes_exactly_the_declared_set(self, tmp_path):
        """``build`` produces the keys the loader is told to expect."""
        from evals.harness.artifact import DECLARED_KEYS, build
        from stride_service.certification import CertifyResult

        run = empty_run(tuple(PACKAGES))
        artifact = build(
            mode="analysis",
            cases=[],
            models={},
            certification=CertifyResult(certified=True),
            provenance=run.provenance,
            usage={},
            latency={},
            structural_failures=[],
            payloads=[],
            trusted=True,
            sweep=Sweep(run=run),
        )
        assert set(artifact) == DECLARED_KEYS


class TestReadingABlockFailsClosed:
    """``EvalArtifact.block`` over an artifact that lost one."""

    def loaded(self, raw):
        from evals.harness.artifact import EvalArtifact

        return EvalArtifact(
            path=Path("sweep.json"),
            mode="analysis",
            cases=(),
            trusted=True,
            structural_failures=(),
            provenance=empty_run(()).provenance,
            raw=raw,
        )

    def test_a_missing_block_raises_rather_than_reading_as_empty(self):
        """The defect: ``raw.get("scores") or ()`` reported a sweep that scored
        nothing, which is the number a sweep that scored and found nothing
        prints too."""
        from evals.harness.provenance import ProvenanceError

        with pytest.raises(ProvenanceError, match="carries no 'scores' block"):
            self.loaded({"applicability": []}).block("scores")

    def test_a_present_but_empty_block_is_returned(self):
        """An instrument that measured nothing is not a missing instrument."""
        assert self.loaded({"scores": []}).block("scores") == []

    def test_an_undeclared_key_is_refused_as_a_caller_error(self):
        from evals.harness.provenance import ProvenanceError

        with pytest.raises(ProvenanceError, match="not a key an artifact declares"):
            self.loaded({"nope": 1}).block("nope")


class TestOneCaseMeasuredAlone:
    """``measure_case`` over one finished run, with no sweep around it.

    The seam this pins: what a case contributes is separable from the loop that
    produced it, so a reading can be checked without building a graph.
    """

    def measured(self, analyses, drafts=None):
        """One corpus case, one hand-built report, no graph anywhere.

        A real case rather than a stub, because ASVS's scorer reads the
        reference set the case declares — which is the thing being measured.
        """
        from evals.harness.instruments import measure_case

        case = load_case(CORPUS / "01-payments-checkout")
        report = sample_report(analyses=analyses)
        return measure_case(case, _AnalysisRunStub(report, drafts or {}), ["an issue"])

    def test_the_payload_names_the_case_and_its_structural_issues(self):
        measured = self.measured([asvs_block(1)])

        assert measured.payload["case"] == "01-payments-checkout"
        assert measured.payload["structural_issues"] == ["an issue"]

    def test_a_block_contributes_its_own_grounds_row(self):
        """One grounds row per block, whichever package produced it."""
        measured = self.measured([asvs_block(1)])

        assert [entry.framework for entry in measured.grounds] == ["asvs"]

    def test_a_package_with_a_per_case_scorer_contributes_its_rows(self):
        """ASVS declares one, so both its rows arrive keyed by instrument."""
        measured = self.measured([asvs_block(1)])

        assert set(measured.rows) == {"applicability", "applicability_yield"}
        assert set(measured.payload) >= {"applicability", "applicability_yield"}

    def test_a_package_whose_scorer_is_none_contributes_no_rows(self):
        """STRIDE's entry is ``None``; the neutral instruments still read it."""
        measured = self.measured(None)

        assert measured.rows == {}
        assert [entry.framework for entry in measured.grounds] == ["stride"]
