"""The ledger holds the only human judgements this repository has.

So the properties tested here are the ones an auditor would ask about: nothing
is ever overwritten, a correction is visible as a correction, taste cannot move
an analysis number, and a better recogniser costs no re-vote.

Deterministic and free of provider calls, so it gates on every PR.
"""

from __future__ import annotations

import json

import pytest

from evals.harness.fingerprint import Components, fingerprint
from evals.harness.ledger import (
    REASON_GLOSS,
    REASONS,
    STYLE_REASONS,
    SUBSTANCE_REASONS,
    Ledger,
    LedgerError,
    Vote,
    append,
    cast,
    load,
    rekey,
    write_all,
)


def components(target="process:a", verb="read"):
    return Components("stride", "information-disclosure", (target,), verb=verb)


def test_every_reason_has_a_gloss_and_one_home():
    """A reason with no gloss is one two reviewers read two ways."""
    assert set(REASON_GLOSS) == set(REASONS)
    assert not (SUBSTANCE_REASONS & STYLE_REASONS), "a reason in both is undecidable"


def test_a_style_downvote_keeps_the_finding_and_spares_the_score():
    """The whole control for personal preference, as two booleans."""
    style = cast(components(), "01", "down", "sam", reason="poorly-written")
    assert style.joins_the_pool
    assert not style.counts_against_analysis


def test_a_substance_downvote_does_the_opposite():
    substance = cast(components(), "01", "down", "sam", reason="not-a-threat")
    assert not substance.joins_the_pool
    assert substance.counts_against_analysis


def test_unsure_and_needs_evidence_move_nothing():
    for verdict in ("unsure", "needs-evidence"):
        vote = cast(components(), "01", verdict, "sam")
        assert not vote.joins_the_pool
        assert not vote.counts_against_analysis


def test_an_upvote_joins_the_pool():
    assert cast(components(), "01", "up", "sam").joins_the_pool


def test_a_downvote_without_a_reason_is_refused():
    """The reason decides which number moves, so it cannot be optional."""
    with pytest.raises(LedgerError, match="needs a reason"):
        cast(components(), "01", "down", "sam")


def test_an_anonymous_vote_is_refused():
    """This file is the supply chain of every published number."""
    with pytest.raises(LedgerError, match="never anonymous"):
        cast(components(), "01", "up", "   ")


def test_an_invented_reason_is_refused():
    with pytest.raises(LedgerError, match="is not a reason code"):
        cast(components(), "01", "down", "sam", reason="i-just-dont-like-it")


def test_an_invented_verdict_is_refused():
    """Construction is the gate, so no caller can build one and skip a check."""
    with pytest.raises(LedgerError, match="is not a verdict"):
        Vote(
            fingerprint="v1:00",
            components=components(),
            case="01",
            verdict="maybe",
            voter="sam",
            recorded="2026-08-19T00:00:00+00:00",
        )


def test_a_correction_is_a_new_event_and_the_old_one_survives(tmp_path):
    """Append-only: history stays reconstructible at any past date."""
    path = tmp_path / "votes"
    append(cast(components(), "01", "up", "sam"), path)
    append(cast(components(), "01", "down", "sam", reason="not-a-threat"), path)

    ledger = load(path)
    assert len(ledger) == 2, "the first vote was overwritten"
    key = (fingerprint(components()), "sam")
    assert ledger.current()[key].verdict == "down"
    assert [vote.verdict for vote in ledger.for_fingerprint(key[0])] == ["up", "down"]


def test_the_pool_is_derived_from_the_live_verdicts(tmp_path):
    """Never stored, so it cannot disagree with the ledger it came from."""
    path = tmp_path / "votes"
    append(cast(components("process:a"), "01", "up", "sam"), path)
    append(cast(components("process:b"), "01", "down", "sam", reason="too-vague"), path)
    append(
        cast(components("process:c"), "01", "down", "sam", reason="not-a-threat"), path
    )

    pool = load(path).pool()
    assert fingerprint(components("process:a")) in pool
    assert fingerprint(components("process:b")) in pool, "style down left the pool"
    assert fingerprint(components("process:c")) not in pool


def test_a_retracted_upvote_leaves_the_pool(tmp_path):
    path = tmp_path / "votes"
    append(cast(components(), "01", "up", "sam"), path)
    assert load(path).pool()

    append(cast(components(), "01", "down", "sam", reason="not-a-threat"), path)
    assert not load(path).pool()


def test_double_voted_findings_are_the_agreement_sample(tmp_path):
    path = tmp_path / "votes"
    append(cast(components("process:a"), "01", "up", "sam"), path)
    append(
        cast(components("process:a"), "01", "down", "ada", reason="not-a-threat"), path
    )
    append(cast(components("process:b"), "01", "up", "sam"), path)

    ledger = load(path)
    assert ledger.double_voted() == (fingerprint(components("process:a")),)
    assert ledger.voters() == ("ada", "sam")


def test_a_rekey_needs_no_revote(tmp_path):
    """The property that stops this ledger expiring when the rule changes.

    Cast at version 1 and re-keyed to the current default, which is the shape a
    row written before :class:`~analysis_service.report.Claim` carried a verb
    takes today: the components were stored, so the new key is a recomputation
    rather than a re-vote.
    """
    path = tmp_path / "votes"
    append(cast(components(), "01", "up", "sam", version=1), path)
    original = load(path)
    assert original.votes[0].fingerprint.startswith("v1:")

    moved = rekey(original.votes)
    assert [vote.fingerprint for vote in moved] != [
        vote.fingerprint for vote in original.votes
    ]
    assert all(vote.fingerprint.startswith("v2:") for vote in moved)
    assert [vote.verdict for vote in moved] == [vote.verdict for vote in original.votes]

    write_all(moved, path)
    assert len(load(path)) == 1


def test_a_missing_ledger_is_empty_rather_than_an_error(tmp_path):
    """Before the first sitting there are no votes; that is a start, not a fault."""
    ledger = load(tmp_path / "nothing-here")
    assert len(ledger) == 0
    assert ledger.pool() == frozenset()


def test_a_malformed_row_fails_closed_and_names_its_line(tmp_path):
    """A row nobody can read is worse than a missing one: it counts in a denominator."""
    path = tmp_path / "votes"
    append(cast(components(), "01", "up", "sam"), path)
    with (path / "sam.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"fingerprint": "v1:aa"}\n')

    with pytest.raises(LedgerError, match=r":2: malformed vote"):
        load(path)


def test_a_row_that_is_not_json_names_its_line(tmp_path):
    path = tmp_path / "votes"
    path.mkdir()
    (path / "sam.jsonl").write_text("not json at all\n", encoding="utf-8")
    with pytest.raises(LedgerError, match=r":1: invalid JSON"):
        load(path)


def test_blank_lines_are_tolerated(tmp_path):
    path = tmp_path / "votes"
    path.mkdir()
    vote = cast(components(), "01", "up", "sam")
    (path / "sam.jsonl").write_text(
        "\n" + json.dumps(vote.to_json(), sort_keys=True) + "\n\n", encoding="utf-8"
    )
    assert len(load(path)) == 1


def test_a_row_in_the_wrong_voters_file_fails_closed(tmp_path):
    """The filename is the binding: one voter's history lives in one file."""
    path = tmp_path / "votes"
    path.mkdir()
    vote = cast(components(), "01", "up", "ada")
    (path / "sam.jsonl").write_text(
        json.dumps(vote.to_json(), sort_keys=True) + "\n", encoding="utf-8"
    )
    with pytest.raises(LedgerError, match="one voter's history lives in one file"):
        load(path)


def test_a_single_file_ledger_is_refused(tmp_path):
    """The one-file shape is dropped, and reading it as empty would eat votes."""
    path = tmp_path / "votes.jsonl"
    vote = cast(components(), "01", "up", "sam")
    path.write_text(json.dumps(vote.to_json(), sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(LedgerError, match="not a shape this loader reads"):
        load(path)


def test_files_load_in_filename_order(tmp_path):
    """Order between voters carries no meaning, so it must at least be stable."""
    path = tmp_path / "votes"
    append(cast(components(), "01", "up", "sam"), path)
    append(cast(components(), "01", "up", "ada"), path)
    assert [vote.voter for vote in load(path)] == ["ada", "sam"]


def test_a_voter_that_is_not_a_login_is_refused():
    """The voter names this voter's file, so a voter must never carry a path."""
    for voter in ("../sam", "sam smith", "sam/", "-sam", "sam--i-am"):
        with pytest.raises(LedgerError, match="is not a GitHub login"):
            cast(components(), "01", "up", voter)


def test_an_empty_ledger_answers_every_question(tmp_path):
    """No branch may assume a vote exists; a first sitting starts here."""
    ledger = Ledger()
    assert ledger.current() == {}
    assert ledger.pool() == frozenset()
    assert ledger.voters() == ()
    assert ledger.double_voted() == ()
    assert ledger.voted_fingerprints() == frozenset()


def test_every_package_keys_its_own_votes(tmp_path):
    """``cast`` took its version from a default, which is a single rule for a
    table that holds one row per package.

    So an ASVS claim was keyed under STRIDE's rule, which reads an action verb
    an ASVS claim does not carry, and every ASVS vote raised instead of
    recording. Checked against the registry rather than a fixed pair: a package
    added to ``VERSION_FOR`` is covered here the day it is added.
    """
    from evals.harness.fingerprint import VERSION_FOR

    for framework, version in VERSION_FOR.items():
        recorded = cast(
            _components_for(framework),
            "01-payments-checkout",
            "up",
            "ada",
        )
        assert recorded.fingerprint.startswith(f"v{version}:"), (
            f"{framework} keyed under the wrong rule"
        )


def _components_for(framework):
    """One package's claim components, each satisfying its own version."""
    if framework == "asvs":
        return Components("asvs", "V6", ("process:a",), identifier="6.2.1")
    return Components("stride", "spoofing", ("process:a",), verb="read")


def test_a_rekey_moves_each_row_under_its_own_frameworks_rule(tmp_path):
    """One version for the file stopped being a coherent request when the table
    grew its second row: either value raised on the other package's rows."""
    path = tmp_path / "votes"
    append(cast(_components_for("stride"), "01", "up", "sam"), path)
    append(cast(_components_for("asvs"), "01", "up", "sam"), path)

    moved = rekey(load(path).votes)

    assert sorted(vote.fingerprint.split(":")[0] for vote in moved) == ["v2", "v3"]
