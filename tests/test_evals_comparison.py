"""The published comparison table: what it says, and what it refuses to say.

The properties #330 settled, one test each. The generator names no framework
and no column — it walks the instruments table, so a package that brings its
own columns is printed without an edit. Rows sort by merge date and never by
score. Numbers group by the corpus they ran against. And a Baseline nobody has
voted on reads "no votes yet" rather than zero, because a zero reads as a
measured failure and an absent vote is not a measurement.

The last test is the staleness gate: the committed file must equal what the
data says today, the way the licence lints fail closed.
"""

from __future__ import annotations

import json
from pathlib import Path

from evals.harness import comparison
from evals.harness.comparison import NO_VOTES, build, is_stale, read_baseline
from evals.harness.instruments import INSTRUMENTS
from tests.test_evals_baseline import payload

IDENTITY = {
    "repo_commit": "c" * 40,
    "corpus_digest": "d" * 64,
    "models": {"base": "openai/gpt-base", "strong": "openai/gpt-5.6"},
    "sampling": {},
    "frameworks": ["stride"],
}


def merged(root, name, *, identity=None, sweeps=1, cost=0.60, scores=None, voted=False):
    """One merged Baseline on disk, with as much scored detail as asked for."""
    directory = root / "evals" / "baselines" / name
    directory.mkdir(parents=True, exist_ok=True)
    entries = []
    for index in range(sweeps):
        stem = f"ada-{index:04d}"
        raw = payload(seed=index)
        raw["scores"] = scores if scores is not None else []
        if voted:
            for score in raw["scores"]:
                score.setdefault("counts", {})["pooled"] = 1
        (directory / f"{stem}.json").write_text(json.dumps(raw), encoding="utf-8")
        entries.append(
            {
                "artifact": f"{stem}.json",
                "submitted_by": "ada",
                "cost": {"actual_usd": cost},
            }
        )
    (directory / "baseline.json").write_text(
        json.dumps(
            {
                "name": name,
                "identity": identity or IDENTITY,
                "sweeps": entries,
            }
        ),
        encoding="utf-8",
    )
    return directory


def score_row(recall=0.8, rejected=0.0, unvoted=0):
    return {
        "case_id": "01",
        "metrics": {
            "recall": recall,
            "must_find_recall": 0.9,
            "rejected_rate": rejected,
        },
        "counts": {"rejected": 0, "pooled": 0, "open": 0, "unvoted": unvoted},
    }


class TestAnEmptyTable:
    def test_it_still_writes_a_file_so_the_pointer_never_dangles(self, tmp_path):
        text = build(tmp_path)
        assert "No Baseline is merged yet" in text
        assert "Merged baselines" in text

    def test_it_states_the_comparability_rule_even_with_nothing_to_compare(
        self, tmp_path
    ):
        assert "only compare inside a group" in build(tmp_path)


class TestARow:
    def test_it_shows_the_identity_the_cost_and_the_sweep_count(self, tmp_path):
        merged(tmp_path, "one", sweeps=2, cost=0.50)
        text = build(tmp_path)
        assert "`strong`: `openai/gpt-5.6`" in text
        assert "2 sweep(s)" in text
        assert "$1.00 recorded" in text

    def test_it_names_the_submitter(self, tmp_path):
        merged(tmp_path, "one")
        assert "submitted by ada" in build(tmp_path)

    def test_a_mean_carries_its_spread_when_more_than_one_sweep_ran(self, tmp_path):
        directory = merged(tmp_path, "one", sweeps=2, scores=[score_row(recall=0.8)])
        raw = json.loads((directory / "ada-0001.json").read_text())
        raw["scores"] = [score_row(recall=0.6)]
        (directory / "ada-0001.json").write_text(json.dumps(raw), encoding="utf-8")
        assert "0.700 (0.600–0.800)" in build(tmp_path)

    def test_one_sweep_prints_no_spread(self, tmp_path):
        merged(tmp_path, "one", scores=[score_row(recall=0.8)])
        text = build(tmp_path)
        assert "0.800" in text
        assert "0.800 (" not in text

    def test_vote_coverage_prints_so_a_reader_can_weigh_the_row(self, tmp_path):
        merged(tmp_path, "one", scores=[score_row(unvoted=4)])
        assert "0 of 4 unmatched finding(s) judged" in build(tmp_path)


class TestAnUnvotedBaseline:
    def test_a_vote_dependent_number_reads_no_votes_yet_never_zero(self, tmp_path):
        merged(tmp_path, "one", scores=[score_row(rejected=0.0, unvoted=3)])
        text = build(tmp_path)
        assert NO_VOTES in text
        assert "Nobody has voted on this Baseline's findings" in text

    def test_a_rule_based_number_still_prints(self, tmp_path):
        """Recall is measured whether or not anybody voted; hiding it would lie."""
        merged(tmp_path, "one", scores=[score_row(recall=0.75, unvoted=3)])
        assert "0.750" in build(tmp_path)

    def test_a_voted_baseline_prints_the_number_instead(self, tmp_path):
        merged(tmp_path, "one", scores=[score_row(rejected=0.25)], voted=True)
        text = build(tmp_path)
        assert "0.250" in text
        assert NO_VOTES not in text


class TestGroupingAndSort:
    def test_rows_group_by_commit_and_corpus_digest(self, tmp_path):
        merged(tmp_path, "one")
        merged(
            tmp_path,
            "two",
            identity={**IDENTITY, "corpus_digest": "e" * 64},
        )
        text = build(tmp_path)
        assert text.count("## Commit ") == 2
        assert "compare with each other and with nothing above or below it" in text

    def test_one_corpus_puts_both_rows_in_one_group(self, tmp_path):
        merged(tmp_path, "one")
        merged(tmp_path, "two")
        text = build(tmp_path)
        assert text.count("## Commit ") == 1
        assert "2 baseline(s)" in text

    def test_the_sort_key_is_never_a_score(self, tmp_path):
        """A leaderboard over this corpus would reward overfitting to it."""
        merged(tmp_path, "b-low", scores=[score_row(recall=0.1)])
        merged(tmp_path, "a-high", scores=[score_row(recall=0.9)])
        text = build(tmp_path)
        assert text.index("`a-high`") < text.index("`b-low`"), (
            "with no merge dates the tie-break is the name, never the number"
        )


class TestTheColumnsComeFromTheTable:
    def test_every_published_column_is_reachable_from_the_instruments_table(self):
        """The generator names no column, so the table is the only source."""
        labels = {
            column.label
            for instrument in INSTRUMENTS.values()
            for column in instrument.published
        }
        assert labels, "no instrument publishes anything, so the table is empty"

    def test_a_framework_prints_only_the_columns_that_apply_to_it(self, tmp_path):
        merged(tmp_path, "one", scores=[score_row()])
        text = build(tmp_path)
        assert "`stride`" in text
        # ASVS is not in this Baseline's selection, so its columns stay out.
        assert "precision" not in text

    def test_a_second_framework_brings_its_own_block(self, tmp_path):
        merged(
            tmp_path,
            "one",
            identity={**IDENTITY, "frameworks": ["asvs", "stride"]},
            scores=[score_row()],
        )
        text = build(tmp_path)
        assert "`asvs`" in text
        assert "`stride`" in text


class TestTheStalenessGate:
    def test_a_hand_edited_table_reads_as_stale(self, tmp_path):
        (tmp_path / "evals" / "baselines").mkdir(parents=True)
        (tmp_path / "evals" / "baselines" / "README.md").write_text(
            "hand written\n", encoding="utf-8"
        )
        assert is_stale(tmp_path)

    def test_a_rebuilt_table_reads_as_fresh(self, tmp_path):
        merged(tmp_path, "one")
        comparison.write(tmp_path)
        assert not is_stale(tmp_path)

    def test_a_directory_with_no_manifest_is_not_a_row(self, tmp_path):
        (tmp_path / "evals" / "baselines" / "junk").mkdir(parents=True)
        assert read_baseline(tmp_path / "evals" / "baselines" / "junk") is None
        assert "No Baseline is merged yet" in build(tmp_path)


def test_the_committed_table_is_not_stale():
    """The gate itself, over the real tree — the licence-lint shape (#330).

    A stale table is a published number that no longer follows from the data
    under it, so this fails closed rather than regenerating quietly.
    """
    assert not is_stale(), (
        "evals/baselines/README.md disagrees with the merged Baselines."
        " Run `python -m evals.harness.run comparison` and commit the result."
    )


def test_the_generator_hardcodes_no_framework_name():
    """A framework named in the generator is the gap #330 exists to avoid."""
    from stride_service.frameworks import PACKAGES

    source = Path(comparison.__file__).read_text(encoding="utf-8")
    named = [name for name in PACKAGES if f'"{name}"' in source]
    assert not named, f"the generator names {named}; it must walk the table instead"
