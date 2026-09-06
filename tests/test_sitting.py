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

from typing import get_args

import pytest
from pydantic import ValidationError

from evals.harness import sitting as sittings
from evals.harness.reference import ANONYMOUS
from evals.harness.sitting import Draft, Store
from tests.test_sitting_app import CASE, build_tree, drafts_root

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
    )


@pytest.fixture
def proxy_store(tree):
    """One account carrying a read somebody else did, and does not name."""
    return Store(
        root=tree,
        submitted_by="ada",
        submitted_for=ANONYMOUS,
        drafts=drafts_root(tree),
    )


def prepared_for(store: Store, case: str = CASE):
    return sittings.prepare(store.case_dir(case))


def every_mark(store: Store, case: str = CASE) -> dict[str, str]:
    """A mark on every recorded finding, which a record now needs."""
    prepared = prepared_for(store, case)
    return {target.fingerprint: "agree" for target in prepared.mark_targets}


def open_draft(store: Store, case: str = CASE, **fields) -> Draft:
    """One reader's draft with their own list written, as the app leaves it."""
    prepared = prepared_for(store, case)
    return Draft(
        case=case,
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
            marks=every_mark(store),
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
            marks=every_mark(store),
            missing=[],
            notes="",
        )

        assert unreviewed(store) == before

    def test_finish_puts_the_answers_on_the_draft(self, store):
        draft = sittings.finish(
            store,
            prepared_for(store),
            open_draft(store),
            marks=every_mark(store),
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
            marks=every_mark(store),
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
            marks=every_mark(store),
            missing=[],
            notes="one",
        )
        second = sittings.finish(
            store,
            prepared_for(store),
            first,
            marks=every_mark(store),
            missing=[],
            notes="two",
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
            marks=every_mark(store),
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
            marks=every_mark(store),
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
            marks=every_mark(store),
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


class TestUnsureIsAnAnswer:
    """The fourth mark, and the rule it exists to make honest.

    Answering every recorded finding is the bar. A bar that forces an answer
    needs an answer for "I cannot decide", or a reader who cannot judge one
    entry picks one of the other three to get past it — and the safe pick is
    ``agree``, which inflates the agreement these numbers publish.
    """

    def test_it_is_in_the_closed_set(self):
        assert "unsure" in sittings.MARKS

    def test_it_is_the_word_a_vote_already_spells(self):
        """One word, one meaning, over the key both are filed under.

        A **Ledger** vote and a sitting mark share a fingerprint. The two mean
        the same thing — the reader read the finding and cannot decide — so a
        second word for it would be a second meaning waiting to happen.
        """
        from evals.harness.ledger import Verdict

        assert "unsure" in get_args(Verdict)

    def test_a_sitting_of_nothing_but_unsure_records(self, store):
        """A reader who cannot judge one entry still finishes their sitting."""
        draft = sittings.finish(
            store,
            prepared_for(store),
            open_draft(store),
            marks=dict.fromkeys(every_mark(store), "unsure"),
            missing=[],
            notes="I could not settle any of them",
        )

        assert draft.state == "finished"

    def test_a_value_outside_the_set_is_still_refused(self, store):
        with pytest.raises(ValidationError):
            sittings.Draft(case=CASE, marks={"v3:abc": "maybe"})
