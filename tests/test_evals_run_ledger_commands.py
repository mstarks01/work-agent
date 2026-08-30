"""The two credential-free ledger commands: ``review`` and ``rekey``.

``rekey`` is the operation the whole versioning argument rests on — a better
recogniser changes every key, and a vote stores its components so moving the
ledger is arithmetic over a file rather than a re-vote. Before these tests the
capability was claimed in three docstrings and reachable from nothing.

Deterministic and free of provider calls, so they gate on every PR.
"""

from __future__ import annotations

import json

from evals.harness.fingerprint import Components
from evals.harness.ledger import append, cast, load
from evals.harness.run import main, reports_dir


def seed(path, *, version=1):
    for target, verb in (("process:a", "read"), ("store:b", "alter")):
        append(
            cast(
                Components("stride", "information-disclosure", (target,), verb=verb),
                "01-payments-checkout",
                "up",
                "ada",
                version=version,
            ),
            path,
        )


def test_rekey_previews_without_writing(tmp_path, capsys):
    """A preview that also edited would be a preview nobody could trust."""
    led = tmp_path / "votes"
    seed(led)
    before = (led / "ada.jsonl").read_text(encoding="utf-8")

    assert main(["rekey", "--to-version", "2", "--ledger", str(led)]) == 0

    assert (led / "ada.jsonl").read_text(encoding="utf-8") == before
    out = capsys.readouterr().out
    assert "2 fingerprints move" in out
    assert "nothing written" in out


def test_rekey_moves_every_key_and_keeps_every_vote(tmp_path):
    """The property: no re-vote, no provider, and the verdicts survive."""
    led = tmp_path / "votes"
    seed(led)
    original = load(led)

    assert main(["rekey", "--to-version", "2", "--ledger", str(led), "--yes"]) == 0

    moved = load(led)
    assert all(vote.fingerprint.startswith("v2:") for vote in moved)
    assert [v.verdict for v in moved] == [v.verdict for v in original.votes]
    assert [v.voter for v in moved] == [v.voter for v in original.votes]
    assert [v.components for v in moved] == [v.components for v in original.votes]
    assert len(moved.pool()) == len(original.pool())


def test_rekey_refuses_a_move_the_components_cannot_satisfy(tmp_path, capsys):
    """Fail-closed, so a partial re-key is impossible."""
    led = tmp_path / "votes"
    append(
        cast(
            Components("asvs", "V1", ("process:a",)),
            "01-payments-checkout",
            "up",
            "ada",
            version=1,
        ),
        led,
    )
    before = (led / "ada.jsonl").read_text(encoding="utf-8")

    assert main(["rekey", "--to-version", "2", "--ledger", str(led), "--yes"]) == 1

    assert (led / "ada.jsonl").read_text(encoding="utf-8") == before, (
        "a refusal must not write"
    )
    assert "cannot re-key" in capsys.readouterr().out


def test_rekey_on_an_empty_ledger_is_not_an_error(tmp_path, capsys):
    assert main(["rekey", "--to-version", "2", "--ledger", str(tmp_path / "nil")]) == 0
    assert "no votes to re-key" in capsys.readouterr().out


def _claim(title="A finding", category="spoofing"):
    return {
        "id": "S-01",
        "category": category,
        "title": title,
        "description": "d",
        "affected_element_ids": ["entity:shopper"],
        "verb": "impersonate",
        "grounds": [{"kind": "quote", "text": "t"}],
    }


def _sweep(tmp_path, name="artifact.json", claims=None):
    """A sweep artifact and the reports directory ``run --out`` writes beside it."""
    artifact = tmp_path / name
    artifact.write_text(json.dumps({"mode": "analysis"}), encoding="utf-8")
    # From the harness's own helper: composing the name here is what let this
    # fixture agree with a review app looking in the wrong place.
    reports = reports_dir(tmp_path / name)
    reports.mkdir()
    (reports / "01-payments-checkout.report.json").write_text(
        json.dumps(
            {
                "engine_version": "test-1.0",
                "analyses": [
                    {
                        "framework": "stride",
                        "claims": [_claim()] if claims is None else claims,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return artifact


def test_review_reports_what_is_waiting(tmp_path, capsys):
    artifact = _sweep(tmp_path)
    led = tmp_path / "votes"

    assert main(["review", str(artifact), "--voter", "ada", "--ledger", str(led)]) == 0

    out = capsys.readouterr().out
    assert "1 findings waiting for ada, over 1 sweep(s)" in out
    assert "01-payments-checkout" in out
    assert "webapp/review.py" in out, "a reviewer needs to be told how to answer"


def test_review_over_several_sweeps_counts_what_they_disagree_on(tmp_path, capsys):
    """The reading ``volatile`` is for: one finding in one sweep of two."""
    steady = _sweep(tmp_path, "one.json")
    both = _sweep(
        tmp_path,
        "two.json",
        claims=[_claim(), _claim("A second finding", "tampering")],
    )
    led = tmp_path / "votes"

    code = main(
        ["review", str(steady), str(both), "--voter", "ada", "--ledger", str(led)]
    )

    assert code == 0
    out = capsys.readouterr().out
    assert "2 findings waiting for ada, over 2 sweep(s)" in out
    assert "1 found in some runs and not others" in out
    assert "--artifact" in out, "the command it prints has to name both sweeps"


def test_review_writes_nothing(tmp_path):
    """Read-only, like ``promote`` and ``stability``."""
    artifact = _sweep(tmp_path)
    led = tmp_path / "votes"

    main(["review", str(artifact), "--voter", "ada", "--ledger", str(led)])

    assert not led.exists()
