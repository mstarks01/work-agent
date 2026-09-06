"""Recording a **Draft Sitting**, and taking the record back off.

The pair is one act read in two directions: :func:`~evals.harness.sitting.finish`
writes three files and says so on the draft, and
:func:`~evals.harness.sitting.withdraw` takes off exactly what it put on. What
makes them a pair is that the tree comes back byte for byte, which is what these
tests hold — a stray byte left under a case directory puts that case in the pull
request.

Held here rather than through the app, because neither direction is a web fact.
``tests/test_sitting_app.py`` still holds what the app owes a browser: the
refusals, the token, and the shape of each reply.
"""

from __future__ import annotations

import pytest

from evals.harness import sitting as sittings
from evals.harness.reference import ANONYMOUS
from evals.harness.sitting import Draft, Store
from tests.test_sitting_app import CASE, build_tree, drafts_root
from webapp.sitting import HELD

OWN_LIST = ["a spoofed device"]


@pytest.fixture
def tree(tmp_path):
    return build_tree(tmp_path)


@pytest.fixture
def store(tree):
    return Store(
        root=tree,
        submitted_by="ada",
        submitted_for="ada",
        drafts=drafts_root(tree),
        held=HELD,
    )


@pytest.fixture
def proxy_store(tree):
    """One account carrying a read somebody else did, and does not name."""
    return Store(
        root=tree,
        submitted_by="ada",
        submitted_for=ANONYMOUS,
        drafts=drafts_root(tree),
        held=HELD,
    )


def prepared_for(store: Store, case: str = CASE):
    return sittings.prepare(store.case_dir(case))


def open_draft(store: Store, case: str = CASE, **fields) -> Draft:
    """One reader's draft with their own list written, as the app leaves it."""
    prepared = prepared_for(store, case)
    return Draft(
        case=case,
        clone=str(store.root),
        own_list=list(OWN_LIST),
        opened_digests=sittings.digests(store.case_dir(case), prepared.files),
        **fields,
    )


def snapshot(store: Store, case: str = CASE) -> dict[str, bytes]:
    """Every file under one case, as it stands.

    The whole directory rather than the three files the record touches: the
    submission reads the directory, so a byte anywhere under it is a byte in
    the diff.
    """
    root = store.case_dir(case)
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def unreviewed(store: Store) -> str:
    return (store.root / sittings.UNREVIEWED_FILE).read_text(encoding="utf-8")


class TestRecordingWritesNothingIntoTheTree:
    """A sitting becomes a record by merging, and not before.

    The reason the working tree stays untouched: a submission carries one JSON
    file and nothing else, so anything written under a case would either fail
    the scope check or have to be undone before the press. It used to be both —
    :func:`~evals.harness.sitting.finish` wrote three files and the app deleted
    them again on a successful contribution.
    """

    def test_finish_leaves_every_byte_under_the_case(self, store):
        before = snapshot(store)

        sittings.finish(
            store,
            prepared_for(store),
            open_draft(store),
            marks={},
            missing=[],
            notes="21 agree",
        )

        assert snapshot(store) == before

    def test_finish_leaves_the_unreviewed_table_alone(self, store):
        before = unreviewed(store)

        sittings.finish(
            store,
            prepared_for(store),
            open_draft(store),
            marks={},
            missing=[],
            notes="",
        )

        assert unreviewed(store) == before

    def test_finish_puts_the_answers_on_the_draft(self, store):
        draft = sittings.finish(
            store,
            prepared_for(store),
            open_draft(store),
            marks={},
            missing=["nobody rotates the key"],
            notes="21 agree",
        )

        assert draft.state == "finished"
        assert draft.notes == "21 agree"
        assert draft.missing == ["nobody rotates the key"]
        assert draft.own_list == OWN_LIST

    def test_the_draft_is_saved_before_it_returns(self, store):
        """The draft outlives the process, so nothing here may hold it only."""
        sittings.finish(
            store,
            prepared_for(store),
            open_draft(store),
            marks={},
            missing=[],
            notes="x",
        )

        held = sittings.load_draft(store.drafts, store.submitted_by, CASE)
        assert held is not None
        assert held.state == "finished"
        assert held.notes == "x"

    def test_a_second_finish_corrects_rather_than_adds(self, store):
        first = sittings.finish(
            store,
            prepared_for(store),
            open_draft(store),
            marks={},
            missing=[],
            notes="one",
        )
        second = sittings.finish(
            store, prepared_for(store), first, marks={}, missing=[], notes="two"
        )

        assert second.notes == "two"
        assert (
            sittings.load_draft(store.drafts, store.submitted_by, CASE).notes == "two"
        )

    def test_a_mark_naming_no_recorded_finding_refuses_the_press(self, store):
        """The one check the press makes, shared with a merged submission."""
        with pytest.raises(sittings.SittingError, match="answers nothing"):
            sittings.finish(
                store,
                prepared_for(store),
                open_draft(store),
                marks={"v2:0000000000000000": "agree"},
                missing=[],
                notes="",
            )


class TestWithdrawIsTheInverseOfFinish:
    """What one puts on, the other takes off, and the reader keeps their words."""

    def test_it_puts_the_draft_back_to_open(self, store):
        draft = sittings.finish(
            store,
            prepared_for(store),
            open_draft(store),
            marks={},
            missing=[],
            notes="x",
        )

        back = sittings.withdraw(store, prepared_for(store), draft)

        assert back.state == "open"

    def test_the_reader_keeps_every_word_they_wrote(self, store):
        draft = sittings.finish(
            store,
            prepared_for(store),
            open_draft(store),
            marks={},
            missing=["nobody rotates the key"],
            notes="21 agree",
        )

        back = sittings.withdraw(store, prepared_for(store), draft)

        assert back.own_list == OWN_LIST
        assert back.missing == ["nobody rotates the key"]
        assert back.notes == "21 agree"

    def test_it_writes_nothing_into_the_tree_either(self, store):
        draft = sittings.finish(
            store,
            prepared_for(store),
            open_draft(store),
            marks={},
            missing=[],
            notes="x",
        )
        before = snapshot(store)

        sittings.withdraw(store, prepared_for(store), draft)

        assert snapshot(store) == before


class TestTheStoreSpellsItsOwnPaths:
    def test_the_document_name_is_the_one_submit_admits(self, store):
        assert store.document_name == sittings.document_name("ada")

    def test_a_case_directory_is_under_the_clone(self, store):
        assert store.case_dir(CASE) == store.root / "evals" / "corpus" / CASE
        assert store.corpus_dir == store.root / "evals" / "corpus"


class TestTheNamingPhrase:
    def test_one_name_where_a_person_reads_their_own_case(self):
        assert sittings.naming("ada", "ada") == "ada"

    def test_both_names_where_an_account_carries_another_read(self):
        assert sittings.naming("ada", ANONYMOUS) == f"ada for {ANONYMOUS}"
