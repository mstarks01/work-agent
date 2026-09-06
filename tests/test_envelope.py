"""The offline sitting envelope, out and back.

The file crosses a trust boundary: it is written on a machine this project
never sees, arrives by email, and asks for a write into the corpus. So these
hold two properties rather than one. The round trip has to *work* — a reader
who spends an hour must not lose it to a schema quibble — and the import has
to refuse everything that would put words nobody read into the record.

``tests/test_offline_sitting.py`` holds what the page owes the reader. This
holds what the tree owes the operator.
"""

from __future__ import annotations

import argparse
import json
import pathlib
from pathlib import Path

import pytest

from evals import review_submission as reviews_module
from evals.harness import envelope as envelopes
from evals.harness import sitting as sittings
from evals.harness.envelope import Envelope, EnvelopeError
from evals.harness.reference import ANONYMOUS
from tests.test_sitting_app import CASE, OTHER, build_tree, drafts_root

OWN_LIST = ["a spoofed device reports for another", "nobody rotates the fleet key"]


@pytest.fixture
def tree(tmp_path):
    return build_tree(tmp_path)


def digests_for(tree: Path, case: str) -> dict[str, str]:
    case_dir = tree / "evals" / "corpus" / case
    return sittings.digests(case_dir, sittings.prepare(case_dir).files)


def every_mark(tree: Path, case: str = CASE) -> dict[str, str]:
    """A mark on every recorded finding, which a submission now needs."""
    prepared = sittings.prepare(tree / "evals" / "corpus" / case)
    return {target.fingerprint: "agree" for target in prepared.mark_targets}


def answers(tree: Path, case: str = CASE, **fields) -> dict:
    base = {
        "own_list": list(OWN_LIST),
        "marks": every_mark(tree, case),
        "missing": [],
        "notes": "",
        "opened_digests": digests_for(tree, case),
    }
    return {**base, **fields}


def envelope(tree: Path, cases: dict | None = None, **fields) -> Envelope:
    body = {
        "envelope": envelopes.VERSION,
        "submitted_by": "ada",
        "submitted_for": ANONYMOUS,
        "generated": "2026-09-01",
        "cases": cases if cases is not None else {CASE: answers(tree)},
        **fields,
    }
    return Envelope.model_validate(body)


def applied(tree: Path, env: Envelope) -> list[str]:
    return envelopes.apply(env, tree, drafts=drafts_root(tree))


def written(tree: Path) -> list[pathlib.Path]:
    """Every submission file in the tree, which a refusal leaves empty."""
    directory = tree / envelopes.SUBMISSIONS_DIR
    return sorted(directory.glob("*.json")) if directory.is_dir() else []


def submitted(tree: Path) -> dict:
    """The one submission file an import writes, as JSON."""
    files = sorted((tree / envelopes.SUBMISSIONS_DIR).glob("*.json"))
    assert len(files) == 1, f"expected one submission, found {files}"
    return json.loads(files[0].read_text(encoding="utf-8"))


class TestTheRoundTripWritesOneSubmission:
    """An offline reader and a reader at a keyboard contribute the same bytes."""

    def test_it_writes_one_submission_file_and_clears_the_case(self, tree):
        assert applied(tree, envelope(tree)) == [CASE]

        body = submitted(tree)
        assert body["submitted_by"] == "ada"
        assert body["submitted_for"] == ANONYMOUS
        assert list(body["cases"]) == [CASE]
        assert CASE not in reviews_module.unreviewed_cases(tree)

    def test_the_file_is_named_for_its_own_bytes(self, tree):
        """The name is the digest, so an edited file no longer matches it."""
        env = envelope(tree)
        applied(tree, env)

        (path,) = sorted((tree / envelopes.SUBMISSIONS_DIR).glob("*.json"))
        assert path.name == envelopes.submission_name(env)

    def test_the_digests_the_reader_saw_ride_in_the_submission(self, tree):
        """The drift check reads these, so they say which words were read."""
        applied(tree, envelope(tree))

        recorded = submitted(tree)["cases"][CASE]["opened_digests"]
        assert recorded == digests_for(tree, CASE)

    def test_the_own_list_rides_in_the_submission(self, tree):
        applied(tree, envelope(tree))

        assert submitted(tree)["cases"][CASE]["own_list"] == OWN_LIST

    def test_many_cases_ride_in_one_envelope(self, tree):
        both = {CASE: answers(tree), OTHER: answers(tree, OTHER)}

        assert applied(tree, envelope(tree, both)) == sorted([CASE, OTHER])
        assert sorted(submitted(tree)["cases"]) == sorted([CASE, OTHER])

    def test_it_writes_nothing_into_the_case_directory(self, tree):
        """A submission is one file, so an import leaves the corpus alone."""
        before = sorted(p.name for p in (tree / "evals" / "corpus" / CASE).iterdir())

        applied(tree, envelope(tree))

        assert (
            sorted(p.name for p in (tree / "evals" / "corpus" / CASE).iterdir())
            == before
        )


class TestTheImportRefusesWhatWouldRecordWordsNobodyRead:
    def test_a_case_the_corpus_does_not_hold(self, tree):
        env = envelope(tree, {"99-not-a-case": answers(tree)})

        with pytest.raises(EnvelopeError, match="does not hold"):
            applied(tree, env)

    def test_a_case_key_that_spells_a_traversal(self, tree):
        """A key is resolved against the corpus, never joined onto a path."""
        env = envelope(tree, {"../../etc": answers(tree)})

        with pytest.raises(EnvelopeError, match="does not hold"):
            applied(tree, env)
        assert not (tree.parent / "etc").exists()

    def test_an_own_list_too_short_to_have_opened_the_sets(self, tree):
        env = envelope(tree, {CASE: answers(tree, own_list=["no"])})

        with pytest.raises(EnvelopeError, match="independent list is shorter"):
            applied(tree, env)
        assert not written(tree)

    def test_a_mark_naming_no_recorded_finding(self, tree):
        env = envelope(tree, {CASE: answers(tree, marks={"stride:nope": "agree"})})

        with pytest.raises(EnvelopeError, match="names no recorded finding"):
            applied(tree, env)
        assert not written(tree)

    def test_a_file_that_moved_under_the_read(self, tree):
        """Days pass between the page and the import, and the text can move."""
        env = envelope(tree)
        source = tree / "evals" / "corpus" / CASE / "source.md"
        source.write_text(source.read_text("utf-8") + "\na later edit\n", "utf-8")

        with pytest.raises(EnvelopeError, match="changed since the reviewer opened"):
            applied(tree, env)
        assert not written(tree)

    def test_one_bad_case_writes_none_of_them(self, tree):
        """A half-applied envelope leaves the operator unable to say what is real."""
        env = envelope(
            tree, {CASE: answers(tree), OTHER: answers(tree, OTHER, own_list=["no"])}
        )

        with pytest.raises(EnvelopeError):
            applied(tree, env)
        assert not written(tree)
        assert CASE in sittings.unreviewed_cases(tree)

    def test_every_problem_arrives_in_one_message(self, tree):
        """The reader is a day away by email, so one round trip carries them all."""
        env = envelope(
            tree,
            {
                CASE: answers(tree, own_list=["no"]),
                OTHER: answers(tree, OTHER, marks={"stride:nope": "agree"}),
            },
        )

        with pytest.raises(EnvelopeError) as raised:
            applied(tree, env)
        assert CASE in str(raised.value)
        assert OTHER in str(raised.value)


class TestTheFileIsBoundedBeforeItIsBelieved:
    def test_a_mark_outside_the_closed_set(self, tree):
        with pytest.raises(ValueError):
            envelope(tree, {CASE: answers(tree, marks={"x": "looks-fine"})})

    def test_a_digest_that_is_not_a_digest(self, tree):
        with pytest.raises(ValueError):
            envelope(tree, {CASE: answers(tree, opened_digests={"source.md": "nope"})})

    def test_a_name_the_record_cannot_hold(self, tree):
        with pytest.raises(ValueError):
            envelope(tree, submitted_for="Jane Doe")

    def test_a_field_nobody_declared(self, tree):
        with pytest.raises(ValueError):
            Envelope.model_validate(
                {**envelope(tree).model_dump(), "standing": "maintainer"}
            )

    def test_a_line_longer_than_the_cap(self, tree):
        with pytest.raises(ValueError):
            envelope(tree, {CASE: answers(tree, own_list=["x" * 5000])})


class TestReadingTheFile:
    def test_a_written_envelope_reads_back(self, tree, tmp_path):
        path = tmp_path / "sitting-ada.json"
        path.write_text(envelope(tree).model_dump_json(), encoding="utf-8")

        assert envelopes.read(path).submitted_for == ANONYMOUS

    def test_a_file_that_is_not_json(self, tmp_path):
        path = tmp_path / "sitting.json"
        path.write_text("not json at all", encoding="utf-8")

        with pytest.raises(EnvelopeError, match="not readable JSON"):
            envelopes.read(path)

    def test_a_file_from_another_version(self, tree, tmp_path):
        path = tmp_path / "sitting.json"
        body = json.loads(envelope(tree).model_dump_json())
        body["envelope"] = envelopes.VERSION + 1
        path.write_text(json.dumps(body), encoding="utf-8")

        with pytest.raises(EnvelopeError, match="envelope version"):
            envelopes.read(path)

    def test_a_file_too_big_to_be_a_sitting(self, tmp_path):
        path = tmp_path / "sitting.json"
        path.write_text("[]" + " " * envelopes.MAX_BYTES, encoding="utf-8")

        with pytest.raises(EnvelopeError, match="ceiling"):
            envelopes.read(path)


class TestTheImportChecksWhoTheEnvelopeClaimsToBe:
    """Both identity fields ride back inside a file the reader holds.

    So what arrives is a claim, not the stamp the page put there, and a sitting
    record says who read a case. The operator names the account and the envelope
    has to agree -- checked before ``apply``, because apply writes the corpus,
    the reading document, the unreviewed list and the draft store, and a refusal
    after them leaves a tree only ``git checkout`` puts back.
    """

    def _args(self, tree, path, **fields):
        base = {
            "envelope": str(path),
            "submitted_by": "ada",
            "submitted_for": None,
            "root": str(tree),
        }
        return argparse.Namespace(**{**base, **fields})

    def _written(self, tree, path, monkeypatch, **fields):
        monkeypatch.setattr(sittings, "draft_root", lambda: drafts_root(tree))
        code = envelopes.command_import(self._args(tree, path, **fields))
        return code, written(tree)

    def _file(self, tree, tmp_path, **fields):
        path = tmp_path / "sitting.json"
        env = envelope(tree, submitted_for="ada", **fields)
        path.write_text(env.model_dump_json(), encoding="utf-8")
        return path

    def test_an_envelope_naming_another_account_writes_nothing(
        self, tree, tmp_path, monkeypatch, capsys
    ):
        path = self._file(tree, tmp_path, submitted_by="mallory")

        code, recorded = self._written(tree, path, monkeypatch)

        assert code == 1
        assert recorded == [], "the tree was written before the identity check"
        assert "mallory" in capsys.readouterr().out

    def test_a_forged_proxy_read_writes_nothing(
        self, tree, tmp_path, monkeypatch, capsys
    ):
        """``submitted_for`` is the field that says a read was carried for
        somebody. Nothing downstream checks it, so the import is where it has
        to be checked."""
        path = self._file(tree, tmp_path)
        body = json.loads(path.read_text(encoding="utf-8"))
        body["submitted_for"] = "maintainer"
        path.write_text(json.dumps(body), encoding="utf-8")

        code, recorded = self._written(tree, path, monkeypatch)

        assert code == 1
        assert recorded == []
        assert "--submitted-for" in capsys.readouterr().out

    def test_the_account_the_operator_names_is_recorded(
        self, tree, tmp_path, monkeypatch
    ):
        path = self._file(tree, tmp_path)

        code, recorded = self._written(tree, path, monkeypatch)

        assert code == 0
        assert recorded
        assert json.loads(recorded[-1].read_text("utf-8"))["submitted_by"] == "ada"

    def test_a_carried_read_is_recorded_when_the_operator_names_it(
        self, tree, tmp_path, monkeypatch
    ):
        path = tmp_path / "sitting.json"
        env = envelope(tree, submitted_by="ada", submitted_for=ANONYMOUS)
        path.write_text(env.model_dump_json(), encoding="utf-8")

        code, recorded = self._written(tree, path, monkeypatch, submitted_for=ANONYMOUS)

        assert code == 0
        assert recorded
        assert json.loads(recorded[-1].read_text("utf-8"))["submitted_for"] == ANONYMOUS
