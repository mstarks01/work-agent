"""The standalone sitting page: what the reader gets, and what it must not carry.

The page is the one surface with no server behind it, so nothing it does can be
enforced at request time. Two things follow, and both are held here.

It must carry the whole method — every case, both parts, the marks and the
gate's own constant — because a reader who opens it offline has nothing else to
consult. And it must carry the payload safely: the corpus is prose that
describes attacks, so a case sentence spelling ``</script>`` would end the
block and the rest of the file would parse as HTML.

``tests/test_envelope.py`` holds what the tree owes the operator when the file
comes back.
"""

from __future__ import annotations

import json

import pytest

from evals.harness import sitting as sittings
from evals.harness.envelope import VERSION
from evals.harness.reference import ANONYMOUS
from tests.test_sitting_app import CASE, build_tree
from webapp.offline_sitting import build, payload


@pytest.fixture
def tree(tmp_path):
    return build_tree(tmp_path)


@pytest.fixture
def corpus(tree):
    return tree / "evals" / "corpus"


@pytest.fixture
def body(corpus):
    return payload(corpus, "ada", ANONYMOUS)


def embedded(page: str) -> dict:
    """The payload as the page's own script reads it back."""
    literal = page.split("const DATA = ", 1)[1].split(";\n", 1)[0]
    return json.loads(literal.replace("\\u003c", "<"))


class TestThePageCarriesTheWholeMethod:
    def test_every_case_rides_in_one_file(self, corpus, body):
        assert [case["case"] for case in body["cases"]] == sorted(
            path.name for path in corpus.iterdir() if path.is_dir()
        )

    def test_a_case_carries_both_parts_and_its_targets(self, body):
        case = next(c for c in body["cases"] if c["case"] == CASE)
        assert case["part_one"], "the reader meets the system here"
        assert case["part_two"], "and rules on the recorded sets here"
        assert case["targets"], "with one target per recorded finding"

    def test_every_declared_framework_reaches_the_reader(self, corpus, body):
        """A sitting rules on every set together, so the page carries them all."""
        for case in body["cases"]:
            declared = sittings.load_case(corpus / case["case"]).frameworks
            assert set(case["part_two"]) == set(declared)

    def test_the_gate_and_the_marks_come_from_the_rules_module(self, body):
        """One spelling of each, or the offline path would be a second gate."""
        assert body["min_own_list"] == sittings.MIN_OWN_LIST
        assert body["marks"] == list(sittings.MARKS)

    def test_the_digests_say_what_each_required_file_held(self, corpus, body):
        for case in body["cases"]:
            case_dir = corpus / case["case"]
            assert case["digests"] == sittings.digests(case_dir, case["files"])

    def test_the_names_are_stamped_rather_than_typed_by_the_reader(self, body):
        """An envelope cannot claim an account the operator did not offer it."""
        assert body["submitted_by"] == "ada"
        assert body["submitted_for"] == ANONYMOUS
        assert body["envelope"] == VERSION


class TestThePayloadCannotEndItsOwnBlock:
    def test_no_raw_angle_bracket_survives_into_the_script(self, corpus):
        page = build(corpus, "ada", ANONYMOUS)
        literal = page.split("const DATA = ", 1)[1].split(";\n", 1)[0]

        assert "<" not in literal
        assert page.count("<script") == 1
        assert page.count("</script>") == 1

    def test_a_case_that_spells_a_closing_tag_still_reads_back(self, tree, corpus):
        """The corpus is prose about attacks; one day a case will spell this."""
        source = corpus / CASE / "source.md"
        source.write_text(
            source.read_text("utf-8") + "\nThe field accepts </script><b>x</b>.\n",
            encoding="utf-8",
        )

        page = build(corpus, "ada", ANONYMOUS)

        assert page.count("</script>") == 1
        case = next(c for c in embedded(page)["cases"] if c["case"] == CASE)
        text = "".join(
            block.get("text", "")
            for block in case["part_one"]
            if block["kind"] == "source"
        )
        assert "</script><b>x</b>" in text


class TestThePageIsSelfContained:
    def test_it_fetches_nothing(self, corpus):
        """A reader offline gets the whole sitting or none of it."""
        page = build(corpus, "ada", ANONYMOUS)

        for reach in ("http://", "https://", "fetch(", "XMLHttpRequest", "<link"):
            assert reach not in page, f"the page reaches for {reach}"

    def test_it_carries_no_answers_of_its_own(self, body):
        """The reader's words start empty; the page ships the question only."""
        for case in body["cases"]:
            assert "own_list" not in case
            assert "marks" not in case
