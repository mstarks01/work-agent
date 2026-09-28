"""Old commit IDs resolve through the history rewrite maps."""

from __future__ import annotations

from evals.harness.history import HISTORY_DIR, current_commit

OLD_MAIN = "15a33e66be82b22186a17db9830a67114e448b5c"
NEW_MAIN = "dc32fe92746ee21acc7f62b07bba8ba98b9640ba"


def test_a_renamed_commit_resolves_to_its_new_id():
    assert current_commit(OLD_MAIN) == NEW_MAIN


def test_an_id_no_rewrite_renamed_is_itself():
    assert current_commit(NEW_MAIN) == NEW_MAIN


def test_two_rewrites_chain(tmp_path):
    (tmp_path / "a-2026-01-01.tsv").write_text("old\tnew\naaa\tbbb\n", encoding="utf-8")
    (tmp_path / "b-2026-02-01.tsv").write_text("old\tnew\nbbb\tccc\n", encoding="utf-8")
    assert current_commit("aaa", tmp_path) == "ccc"


def test_every_map_is_two_full_ids_a_line():
    for path in HISTORY_DIR.glob("*.tsv"):
        header, *rows = path.read_text(encoding="utf-8").splitlines()
        assert header == "old\tnew"
        for row in rows:
            old, new = row.split("\t")
            assert len(old) == len(new) == 40 and old != new
