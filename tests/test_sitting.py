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

import json
from pathlib import Path

import pytest

from evals.harness import sitting as sittings
from evals.harness.reference import ANONYMOUS
from evals.harness.sitting import Draft, Store
from tests.test_sitting_app import CASE, CASES, OTHER, build_tree, drafts_root
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


class TestFinishRecordsTheSitting:
    def test_it_writes_the_document_the_entry_and_clears_the_line(self, store):
        before, listed = snapshot(store), unreviewed(store)
        draft = open_draft(store)

        sittings.finish(
            store, prepared_for(store), draft, marks={}, missing=[], notes="21 agree"
        )

        assert snapshot(store) != before
        assert (store.case_dir(CASE) / store.document_name).exists()
        assert CASE not in sittings.unreviewed_cases(store.root)
        assert unreviewed(store) != listed

    def test_the_draft_says_what_the_record_left_behind(self, store):
        draft = open_draft(store)

        finished = sittings.finish(
            store,
            prepared_for(store),
            draft,
            marks={},
            missing=["no fleet key"],
            notes="21 agree",
        )

        assert finished.state == "finished"
        assert finished.recorded is not None, "the entry a withdraw takes back off"
        assert finished.unreviewed_entry, "the line a withdraw puts back"
        assert finished.missing == ["no fleet key"]
        assert finished.notes == "21 agree"

    def test_the_draft_is_saved_before_it_returns(self, store):
        """The draft is the only thing here that outlives a process."""
        sittings.finish(
            store,
            prepared_for(store),
            open_draft(store),
            marks={},
            missing=[],
            notes="",
        )

        held = sittings.load_draft(store.drafts, store.submitted_by, CASE)
        assert held is not None
        assert held.state == "finished"
        assert held.own_list == OWN_LIST

    def test_it_leaves_every_other_case_alone(self, store):
        kept = snapshot(store, OTHER)

        sittings.finish(
            store,
            prepared_for(store),
            open_draft(store),
            marks={},
            missing=[],
            notes="",
        )

        assert snapshot(store, OTHER) == kept
        assert OTHER in sittings.unreviewed_cases(store.root)


class TestASecondFinishCorrectsTheRecord:
    def test_it_replaces_the_entry_rather_than_adding_one(self, store):
        draft = open_draft(store)
        prepared = prepared_for(store)
        sittings.finish(store, prepared, draft, marks={}, missing=[], notes="first")

        sittings.finish(store, prepared, draft, marks={}, missing=[], notes="second")

        reviews = json.loads((store.case_dir(CASE) / "case.json").read_text("utf-8"))[
            "reviews"
        ]
        mine = [entry for entry in reviews if entry["submitted_by"] == "ada"]
        assert len(mine) == 1, "a submission never carries two entries by one reader"
        assert mine[0]["notes"] == "second"

    def test_the_second_record_clears_no_line(self, store):
        """The case came off the unreviewed list at the first press."""
        draft = open_draft(store)
        prepared = prepared_for(store)
        sittings.finish(store, prepared, draft, marks={}, missing=[], notes="first")
        first_entry = draft.unreviewed_entry
        listed = unreviewed(store)

        sittings.finish(store, prepared, draft, marks={}, missing=[], notes="second")

        assert draft.unreviewed_entry == first_entry, (
            "the line it still has to put back"
        )
        assert unreviewed(store) == listed

    def test_the_second_document_replaces_the_first(self, store):
        draft = open_draft(store)
        prepared = prepared_for(store)
        sittings.finish(
            store,
            prepared,
            draft,
            marks={},
            missing=["nothing about the fleet key"],
            notes="",
        )

        sittings.finish(
            store,
            prepared,
            draft,
            marks={},
            missing=["nobody rotates the key"],
            notes="",
        )

        text = (store.case_dir(CASE) / store.document_name).read_text("utf-8")
        assert "nobody rotates the key" in text
        assert "nothing about the fleet key" not in text

    def test_an_entry_this_reader_did_not_write_survives(self, store):
        """Append-only still governs the record.

        Nothing but the entry this reader appended ever comes off, which is why
        a re-record replaces by ``replaces=`` rather than by the name on it.
        """
        case_json = store.case_dir(CASE) / "case.json"
        meta = json.loads(case_json.read_text("utf-8"))
        meta["reviews"] = [
            {
                "submitted_by": "sam",
                "submitted_for": "sam",
                "date": "2026-08-01",
                "read": [{"file": "source.md", "sha256": "0" * 64}],
                "document": "REVIEW-sam.md",
                "notes": "",
            }
        ]
        case_json.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        draft = open_draft(store)
        prepared = prepared_for(store)

        sittings.finish(store, prepared, draft, marks={}, missing=[], notes="first")
        sittings.finish(store, prepared, draft, marks={}, missing=[], notes="again")

        entries = json.loads(case_json.read_text("utf-8"))["reviews"]
        assert [entry["submitted_by"] for entry in entries] == ["sam", "ada"]
        assert entries[1]["notes"] == "again"


class TestWithdrawIsTheInverse:
    def test_the_tree_comes_back_byte_for_byte(self, store):
        before, listed = snapshot(store), unreviewed(store)
        draft = open_draft(store)
        prepared = prepared_for(store)
        sittings.finish(store, prepared, draft, marks={}, missing=[], notes="21 agree")
        assert snapshot(store) != before

        sittings.withdraw(store, prepared, draft)

        assert snapshot(store) == before
        assert unreviewed(store) == listed

    def test_the_reader_keeps_every_word_they_wrote(self, store):
        """A withdraw is about this pull request, never about the read."""
        draft = open_draft(store)
        prepared = prepared_for(store)
        sittings.finish(
            store, prepared, draft, marks={}, missing=["no fleet key"], notes="21 agree"
        )

        withdrawn = sittings.withdraw(store, prepared, draft)

        assert withdrawn.own_list == OWN_LIST
        assert withdrawn.missing == ["no fleet key"]
        assert withdrawn.notes == "21 agree"
        assert withdrawn.state == "open"
        assert withdrawn.recorded is None, "the entry it took back off"
        assert withdrawn.unreviewed_entry == ""

    def test_finishing_again_writes_the_same_record_from_the_draft(self, store):
        draft = open_draft(store)
        prepared = prepared_for(store)
        sittings.finish(
            store, prepared, draft, marks={}, missing=["no fleet key"], notes="21 agree"
        )
        recorded = snapshot(store)
        sittings.withdraw(store, prepared, draft)

        sittings.finish(
            store,
            prepared,
            draft,
            marks=draft.marks,
            missing=draft.missing,
            notes=draft.notes,
        )

        assert snapshot(store) == recorded

    def test_it_leaves_every_other_case_alone(self, store):
        prepared, other = prepared_for(store), prepared_for(store, OTHER)
        mine, theirs = open_draft(store), open_draft(store, OTHER)
        sittings.finish(store, prepared, mine, marks={}, missing=[], notes="")
        sittings.finish(store, other, theirs, marks={}, missing=[], notes="")
        kept = snapshot(store, OTHER)

        sittings.withdraw(store, prepared, mine)

        assert snapshot(store, OTHER) == kept
        assert OTHER not in sittings.unreviewed_cases(store.root)

    def test_withdrawing_an_unrecorded_draft_changes_nothing(self, store):
        """The safe direction: a draft that never recorded has nothing to undo."""
        before, listed = snapshot(store), unreviewed(store)
        draft = open_draft(store)

        sittings.withdraw(store, prepared_for(store), draft)

        assert snapshot(store) == before
        assert unreviewed(store) == listed
        assert draft.state == "open"


class TestTheStoreSpellsItsOwnPaths:
    def test_the_document_name_is_the_one_submit_admits(self, store):
        assert store.document_name == sittings.document_name("ada")

    def test_a_case_directory_is_under_the_clone(self, store):
        assert store.case_dir(CASE) == store.root / "evals" / "corpus" / CASE
        assert store.corpus_dir == store.root / "evals" / "corpus"


def test_the_document_a_finish_writes_prints_the_own_list_above_the_sets(store):
    """The evidence the order protects, checked where it is written."""
    draft = open_draft(store)
    sittings.finish(store, prepared_for(store), draft, marks={}, missing=[], notes="")

    text = (store.case_dir(CASE) / store.document_name).read_text("utf-8")
    assert OWN_LIST[0] in text
    assert Path(store.document_name).suffix == ".md"


class TestTheUnreviewedHelper:
    """The list is a Python literal, so removing a line is worth testing."""

    def test_both_entry_shapes_are_removed(self, tree):
        assert sittings.clear_unreviewed(tree, CASE)
        assert sittings.clear_unreviewed(tree, "03-batch-data-pipeline")
        text = (tree / "tests" / "test_case_review.py").read_text("utf-8")
        assert text.count('": ') == 0
        assert text.endswith("}\n"), "the dict is still a dict"

    def test_a_case_not_listed_writes_nothing(self, tree):
        before = (tree / "tests" / "test_case_review.py").read_text("utf-8")
        assert sittings.clear_unreviewed(tree, "99-not-a-case") == ""
        assert (tree / "tests" / "test_case_review.py").read_text("utf-8") == before

    def test_a_cleared_entry_goes_back_where_it_came_from(self, tree):
        """The reverse of the clear, byte for byte and in key order.

        A reader who holds a recorded case back leaves it unread, so the list
        has to name it again — and the reason in the entry is prose a person
        wrote, which nothing can recompute.
        """
        listing = tree / "tests" / "test_case_review.py"
        before = listing.read_text("utf-8")
        entry = sittings.clear_unreviewed(tree, CASE)
        assert sittings.restore_unreviewed(tree, CASE, entry) is True
        assert listing.read_text("utf-8") == before

    def test_an_entry_goes_back_into_an_empty_table(self, tree):
        """The day every case is read, and the reader drops one of them."""
        entries = {case: sittings.clear_unreviewed(tree, case) for case in CASES}
        assert sittings.unreviewed_cases(tree) == []
        sittings.restore_unreviewed(tree, OTHER, entries[OTHER])
        sittings.restore_unreviewed(tree, CASE, entries[CASE])
        assert sittings.unreviewed_cases(tree) == list(CASES), "in key order"

    def test_a_case_the_list_already_names_is_left_alone(self, tree):
        before = (tree / "tests" / "test_case_review.py").read_text("utf-8")
        entry = f'    "{CASE}": "unread",\n'
        assert sittings.restore_unreviewed(tree, CASE, entry) is False
        assert (tree / "tests" / "test_case_review.py").read_text("utf-8") == before


class TestTheUnreviewedEntryIsChecked:
    """The list is a module ``pytest`` imports, and the entry is not ours.

    An entry travels back to the list through a **Draft Sitting** — a file
    outside the repository that the reader owns — so text that is anything
    but one table entry for the named case would be Python nobody wrote,
    running in everybody's checkout. The shape is checked, never the
    spelling.
    """

    #: Closes the table, runs a statement, and opens a second table so the
    #: file still parses. The whole class of attack in one value.
    INJECTION = (
        '    "{case}": "unread",\n'
        "}}\nimport pathlib\n"
        'pathlib.Path("/tmp/never").write_text("ran")\n'
        "JUNK: dict[str, str] = {{\n"
    )

    def test_text_that_closes_the_table_is_refused(self, tree):
        listing = tree / "tests" / "test_case_review.py"
        sittings.clear_unreviewed(tree, CASE)
        before = listing.read_text("utf-8")
        with pytest.raises(sittings.SittingError, match="not one table entry"):
            sittings.restore_unreviewed(tree, CASE, self.INJECTION.format(case=CASE))
        assert listing.read_text("utf-8") == before, "a refusal changed the file"

    def test_an_entry_naming_another_case_is_refused(self, tree):
        sittings.clear_unreviewed(tree, CASE)
        with pytest.raises(sittings.SittingError, match="other than this case"):
            sittings.restore_unreviewed(tree, CASE, f'    "{OTHER}": "x",\n')

    def test_a_value_that_is_not_a_reason_is_refused(self, tree):
        """A call, an f-string or a name evaluates when the module imports."""
        sittings.clear_unreviewed(tree, CASE)
        for value in ('__import__("os").system("id")', 'f"{1}"', "open"):
            with pytest.raises(sittings.SittingError):
                sittings.restore_unreviewed(tree, CASE, f'    "{CASE}": {value},\n')

    def test_every_entry_the_real_list_holds_is_accepted(self):
        """The check is over the shape, so it has to accept the shapes the
        corpus actually writes — a plain string and a parenthesised one."""
        root = Path(__file__).resolve().parents[1]
        source = (root / sittings.UNREVIEWED_FILE).read_text("utf-8")
        entries, _ = sittings._unreviewed_table(source)
        lines = source.splitlines(keepends=True)
        assert entries
        for case, start, end in entries:
            sittings._one_entry(case, "".join(lines[start:end]))


class TestOneListMeansOneTable:
    """The reader took the first `UNREVIEWED` assignment; Python binds the last.

    So a decoy empty table above the real one made this answer "no cases listed"
    about a file that lists them, and the clears check built on that answer
    passed for a case still on the list. The edits-only check refused the
    submission anyway, so nothing got through -- but a checker reading a table
    nobody imports is checking nothing, and that is the same disagreement
    between two readers of one file that the substring reader was.
    """

    def test_two_tables_are_refused_rather_than_one_being_picked(self):
        source = (
            "UNREVIEWED: dict[str, str] = {}\n"
            "UNREVIEWED: dict[str, str] = {\n"
            '    "01-payments-checkout": "unread",\n'
            "}\n"
        )

        with pytest.raises(sittings.SittingError, match="UNREVIEWED tables"):
            sittings._unreviewed_table(source)

    def test_the_one_table_a_real_file_holds_still_reads(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / sittings.UNREVIEWED_FILE).read_text("utf-8")

        entries, _ = sittings._unreviewed_table(source)

        assert entries


class TestTheTableReaderRefusesAnExecutableValue:
    """``restore_unreviewed`` checked an entry's shape; the table reader did not.

    A submission does not travel through ``restore_unreviewed`` -- a
    contributor edits the file and opens a pull request -- so the check that
    mattered for a *submitted* entry is the one the submission checks read the
    table through. It validated that every key was a string and said nothing
    about the values, and a dict value is an arbitrary expression that runs when
    ``pytest`` imports the module.
    """

    def _table(self, value: str) -> str:
        return (
            "UNREVIEWED: dict[str, str] = {\n"
            f'    "01-payments-checkout": {value},\n'
            "}\n"
        )

    def test_a_value_that_is_not_a_string_literal_is_refused(self):
        for value in ('__import__("os").system("id")', "open", 'f"{1}"', "1"):
            with pytest.raises(sittings.SittingError, match="not a string literal"):
                sittings._unreviewed_table(self._table(value))

    def test_a_string_literal_is_still_read(self):
        entries, _ = sittings._unreviewed_table(self._table('"unread"'))
        assert [case for case, _, _ in entries] == ["01-payments-checkout"]

    def test_the_real_list_reads(self):
        """Its entries are parenthesised implicit concatenations, which parse
        to one constant -- so the check must not refuse the shape in the tree."""
        root = Path(__file__).resolve().parents[1]
        source = (root / sittings.UNREVIEWED_FILE).read_text("utf-8")
        entries, _ = sittings._unreviewed_table(source)
        assert entries


class TestASittingCarriedForSomebodyElse:
    """The proxy shape: one account submits a read it did not do.

    What these hold is the split. The record carries both names, the account
    is what every rule reads, and the evidence document says whose words it
    holds — so nothing about the arrangement is inferred from the file name.
    """

    def test_the_entry_carries_both_names(self, proxy_store):
        draft = open_draft(proxy_store)

        sittings.finish(
            proxy_store,
            prepared_for(proxy_store),
            draft,
            marks={},
            missing=[],
            notes="",
        )

        entry = json.loads(
            (proxy_store.case_dir(CASE) / "case.json").read_text("utf-8")
        )["reviews"][0]
        assert entry["submitted_by"] == "ada"
        assert entry["submitted_for"] == ANONYMOUS

    def test_the_document_is_named_for_the_submitting_account(self, proxy_store):
        """``submit sitting`` admits this name and no other under the case."""
        sittings.finish(
            proxy_store,
            prepared_for(proxy_store),
            open_draft(proxy_store),
            marks={},
            missing=[],
            notes="",
        )

        assert proxy_store.document_name == "REVIEW-ada.md"
        assert (proxy_store.case_dir(CASE) / "REVIEW-ada.md").is_file()

    def test_the_document_says_whose_words_it_holds(self, proxy_store):
        """The file name is the submitter's, so the text names the reader."""
        sittings.finish(
            proxy_store,
            prepared_for(proxy_store),
            open_draft(proxy_store),
            marks={},
            missing=[],
            notes="",
        )

        text = (proxy_store.case_dir(CASE) / proxy_store.document_name).read_text(
            "utf-8"
        )
        assert f"Read by {ANONYMOUS}, submitted by ada." in text

    def test_the_read_name_takes_no_entry_off(self, proxy_store):
        """Only the submitting account may withdraw, whoever it read for."""
        case_dir = proxy_store.case_dir(CASE)
        theirs = {
            "submitted_by": "sam",
            "submitted_for": ANONYMOUS,
            "date": "2026-08-01",
            "read": [{"file": "source.md", "sha256": "0" * 64}],
            "document": "REVIEW-sam.md",
            "notes": "",
        }
        case_json = case_dir / "case.json"
        meta = json.loads(case_json.read_text("utf-8"))
        meta["reviews"] = [theirs]
        case_json.write_text(json.dumps(meta, indent=2), encoding="utf-8")

        with pytest.raises(sittings.SittingError, match="not"):
            sittings.unrecord(case_dir, proxy_store.submitted_by, theirs)

        assert json.loads(case_json.read_text("utf-8"))["reviews"] == [theirs]


class TestTheNamingPhrase:
    def test_one_name_where_a_person_reads_their_own_case(self):
        assert sittings.naming("ada", "ada") == "ada"

    def test_both_names_where_an_account_carries_another_read(self):
        assert sittings.naming("ada", ANONYMOUS) == f"ada for {ANONYMOUS}"
