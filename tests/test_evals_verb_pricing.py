"""A verb equivalence priced before it ships, through the shipped readers.

The three error axes are the frontier ``test_evals_identity.py`` pins, and the
first test holds this module to that pin so the two cannot drift. The gain is
read off the merged Baseline sweep, which is a tracked file, so the number
#730 quotes is reproduced here rather than remembered.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from analysis_service.actions import VerbError
from evals import verify_corpus
from evals.harness.bundle import runs_from_reports, stride_threats
from evals.harness.calibration import load_pairs
from evals.harness.reference import flows_by_case, load_corpus
from evals.harness.verb_pricing import parse_groups, price
from tests.test_evals_identity import FRONTIER

BASELINE_DIR = (
    Path(__file__).resolve().parents[1]
    / "evals"
    / "baselines"
    / "352b72d-gpt-5.6-terra-2f7e336d"
)


def _baseline_sweep() -> Path:
    """The sweep the manifest names, rather than a filename typed here.

    A sweep is keyed by its own bytes, so its filename moves whenever the
    archive is migrated — and a test naming a filename fails with a missing
    file rather than with anything about pricing. The manifest is the record of
    which file is the sweep, so it is what this reads. Sorted, so a directory
    that grows a second sweep still picks the same one every run.
    """
    manifest = json.loads((BASELINE_DIR / "baseline.json").read_text(encoding="utf-8"))
    names = sorted(str(entry["artifact"]) for entry in manifest["sweeps"])
    return BASELINE_DIR / names[0]


BASELINE = _baseline_sweep()


@pytest.fixture(scope="module")
def corpus():
    return load_corpus(verify_corpus.CORPUS_DIR)


@pytest.fixture(scope="module")
def flows(corpus):
    return flows_by_case(corpus)


@pytest.fixture(scope="module")
def pairs():
    return load_pairs()


@pytest.fixture(scope="module")
def produced(corpus):
    runs = runs_from_reports(BASELINE, corpus)
    return {case_id: stride_threats(run.report) for case_id, run in runs.items()}


def test_the_shipped_rule_prices_as_the_frontier_pins_it(corpus, flows, pairs):
    """One reader for the three numbers, or the two drift apart unseen."""
    shipped = price(None, corpus, flows, pairs)
    pinned = FRONTIER["endpoint subset + verb"]
    assert shipped.false_splits == pinned["splits"]
    assert shipped.false_merges == pinned["candidate_merges"]
    assert shipped.reference_merges == pinned["reference_merges"]
    assert shipped.new_false_merges == ()
    assert shipped.new_reference_merges == ()


def test_the_shipped_group_carries_its_gain_and_a_repeat_adds_nothing(
    corpus, flows, pairs, produced
):
    """`forge` = `inject` = `plant` shipped on 2026-09-10 (#730), and naming the
    group a second time changes nothing on any axis.

    **The absolute counts are not pinned, and that is deliberate.** This prices
    a recorded sweep's claims against the corpus's reference set, and the sweep
    it reads ran against an older corpus: `evals/baselines/README.md` says
    numbers only compare inside a corpus digest group, and this comparison
    crosses one. The corpus edit of 2026-09-13 renamed six elements to what
    their own sources call them, which cost this Baseline 13 matched
    references — none of them the rule's doing. A pin would have charged that
    rename to the equivalence.

    What the shipped group means is a property, not a number: it merges pairs
    the corpus separates nowhere, so it can only add matches, and applying it
    twice is applying it once."""
    shipped = price(None, corpus, flows, pairs, produced)
    again = price(parse_groups(["forge=inject=plant"]), corpus, flows, pairs, produced)

    assert shipped.matched >= shipped.must_find > 0, (
        "the shipped rule matched nothing, so the pricing read no sweep"
    )
    assert again.false_splits == shipped.false_splits
    assert again.false_merges == shipped.false_merges
    assert again.reference_merges == shipped.reference_merges
    assert again.new_reference_merges == ()
    assert (again.matched, again.must_find) == (shipped.matched, shipped.must_find)


def test_a_merge_with_a_price_names_what_it_would_merge(corpus, flows, pairs):
    """`read` = `recover-credential` merges case 01's two disclosure findings,
    the vocabulary's own example of two actions, and the row says which."""
    candidate = price(parse_groups(["read=recover-credential"]), corpus, flows, pairs)

    assert len(candidate.new_reference_merges) == 1
    merge = candidate.new_reference_merges[0]
    assert merge.case == "01-payments-checkout"
    assert {merge.left_verb, merge.right_verb} == {"read", "recover-credential"}


def test_the_gain_is_absent_without_a_sweep(corpus, flows, pairs):
    assert price(None, corpus, flows, pairs).matched is None


def test_a_group_names_two_verbs_the_vocabulary_carries():
    with pytest.raises(ValueError, match="at least two"):
        parse_groups(["forge"])
    with pytest.raises(VerbError):
        parse_groups(["forge=fabricate"])


def test_the_command_prints_the_shipped_row_first(capsys):
    from evals.harness.run import main

    assert main(["price-verbs", "--equivalent", "forge=inject=plant"]) == 0
    out = capsys.readouterr().out
    assert out.index("shipped rule") < out.index("forge=inject=plant")
    assert "A candidate is a decision, not a fix." in out
