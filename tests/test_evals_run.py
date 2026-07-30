"""What the eval CLI claims about a sweep it certified.

``certified`` is narrow by design (see :mod:`stride_service.certification`): it
means no *observed* fingerprint went unblessed, and is therefore vacuously true
of a sweep that observed none. Printing it alone is how a run that certified
nothing came to announce "all node fingerprints blessed" and write
``"trusted": true`` into its artifact. These pin the two halves together.
"""

from __future__ import annotations

from evals.harness.run import _print_certification
from stride_service.certification import CertifyResult, UncertifiedNode

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
