"""What the eval CLI claims about a sweep it certified.

``certified`` is narrow by design (see :mod:`stride_service.certification`): it
means no *observed* fingerprint went unblessed, and is therefore vacuously true
of a sweep that observed none. Printing it alone is how a run that certified
nothing came to announce "all node fingerprints blessed" and write
``"trusted": true`` into its artifact. These pin the two halves together.
"""

from __future__ import annotations

import json

import pytest

from evals.harness.modes import AttributeCheck, ExtractionScore, render_extraction
from evals.harness.run import _models_record, _print_certification
from stride_service.certification import CertifyResult, UncertifiedNode
from stride_service.deployment import Deployment
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
            == TEST_TIER_ENV["STRIDE_MODEL_BASE_VENDOR"]
        )
        assert (
            record["tiers"]["strong"]["model"]
            == TEST_TIER_ENV["STRIDE_MODEL_STRONG_MODEL"]
        )
