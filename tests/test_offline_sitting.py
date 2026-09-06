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
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from evals.harness import envelope as envelopes
from evals.harness import sitting as sittings
from evals.harness.envelope import VERSION
from evals.harness.reference import ANONYMOUS, CorpusError
from tests.test_sitting_app import CASE, build_tree
from webapp.offline_sitting import build, payload
from webapp.offline_sitting import main as offline_main
from webapp.page import client_script


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
        """The page ships the digests, not the file list they were taken over:
        it renders neither, and the list is what the digests already say."""
        for case in body["cases"]:
            case_dir = corpus / case["case"]
            files = list(case["digests"])
            assert files, "a case with no digested file proves nothing"
            assert case["digests"] == sittings.digests(case_dir, files)

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
    def test_it_loads_nothing_and_sends_nothing_by_itself(self, corpus):
        """A reader offline gets the whole sitting, and it stays theirs.

        Two rules in one check. The page renders from its own bytes, so a
        reader with no network reads every case. And it sends nothing on its
        own: the only way their words leave is a press they make.
        """
        page = build(corpus, "ada", ANONYMOUS)

        for reach in ("fetch(", "XMLHttpRequest", "navigator.sendBeacon", "<link"):
            assert reach not in page, f"the page reaches for {reach}"
        assert "src=" not in page, "the page loads something of its own"

    def test_the_only_destination_is_the_pull_request_the_reader_presses(self, corpus):
        """One outward address, reached from a click handler and nowhere else.

        The publish button navigates to GitHub carrying the reader's own
        submission. That is the point of it, and it is why this holds the page
        to exactly one destination: a second one would be a place the reader
        did not agree to send their words.
        """
        page = build(corpus, "ada", ANONYMOUS)

        addresses = set(re.findall(r"https?://[^\"'\s+]+", page))
        assert addresses == {"https://github.com/"}, (
            f"the page names {sorted(addresses)}. A sitting page may offer the"
            " reader one way out and no other address at all."
        )

    def test_it_carries_no_answers_of_its_own(self, body):
        """The reader's words start empty; the page ships the question only."""
        for case in body["cases"]:
            assert "own_list" not in case
            assert "marks" not in case


class TestACorpusThatDoesNotLoad:
    def test_the_page_is_never_written(self, tmp_path, monkeypatch, capsys):
        """The same refusal `webapp/sitting.py` prints, and no half-built page
        left behind for an operator to send."""

        def refuse(corpus_dir, submitted_by, submitted_for):
            raise CorpusError("03-batch-data-pipeline: case.json: 1 validation error")

        monkeypatch.setattr("webapp.offline_sitting.build", refuse)
        monkeypatch.setattr(
            "webapp.offline_sitting.submit_spine.gh_login", lambda root: "sam"
        )
        out = tmp_path / "sitting.html"
        assert offline_main(["--out", str(out)]) == 1
        assert not out.exists()
        printed = capsys.readouterr()
        assert printed.err.startswith("cannot read the corpus:")
        assert "03-batch-data-pipeline" in printed.err


def test_a_line_holds_no_line_break():
    """One `Line`, and it refuses what its name already promised.

    The sink joins these into Markdown with `- ` in front of each, so a break
    forges headings in the committed reading document. Both producing pages
    split on newlines, so the shape cannot arrive from a browser -- but this is
    also the shape of an envelope a reader mails back, which this module calls
    untrusted.
    """
    from pydantic import TypeAdapter, ValidationError

    from evals.harness.envelope import Line

    adapter = TypeAdapter(Line)
    adapter.validate_python("an ordinary line")
    for break_char in ("\n", "\r", " ", " "):
        with pytest.raises(ValidationError):
            adapter.validate_python(f"forged{break_char}## Heading")


def test_the_webapp_and_the_envelope_share_one_line_type():
    """Two copies of one shape drift. This is the pair that held the defect."""
    from evals.harness.envelope import Line as EnvelopeLine
    from webapp.sitting_base import Line as WebappLine

    assert WebappLine is EnvelopeLine


#: Where the page's submission-naming block starts. Sliced rather than run
#: whole, because the rest of the page draws into a document node has none of.
NAMING_BLOCK = "// The bytes a submission is named by"


class TestThePageNamesASubmissionTheWayPythonDoes:
    """Two readers of one rule, held against each other under ``node``.

    A submission is named by the digest of its canonical bytes, and
    :func:`evals.review_submission.verify_pull_request` refuses a file whose
    name does not match. The page computes that name in JavaScript and the
    service computes it in Python, so the two spell one rule twice — and a
    disagreement would not show up here at all. It would show up as a
    contributor's pull request refused after they had already done the reading.

    So the page's own block runs, over an envelope Python built, and the two
    names are compared.
    """

    def envelope(self, corpus):
        """One filled envelope, in the shape the page's own builder emits."""
        case_dir = corpus / CASE
        prepared = sittings.prepare(case_dir)
        return envelopes.Envelope.model_validate(
            {
                "envelope": envelopes.VERSION,
                "submitted_by": "ada",
                "submitted_for": ANONYMOUS,
                "generated": "2026-09-06",
                "cases": {
                    CASE: {
                        # Non-ASCII on purpose: the two serializers have to
                        # agree about escaping, not only about whitespace.
                        "own_list": ["a spoofed device — reports for another"],
                        "marks": {
                            target.fingerprint: "agree"
                            for target in prepared.mark_targets[:3]
                        },
                        "missing": ["nobody rotates the fleet key"],
                        "notes": "21 agree",
                        "opened_digests": sittings.digests(case_dir, prepared.files),
                    }
                },
            }
        )

    def under_node(self, script: str) -> str:
        node = shutil.which("node")
        if node is None:
            pytest.skip("no node on PATH to run the page's own block")
        source = client_script("offline_sitting.js")
        # The naming block alone: the rest of the page draws into a document
        # node has none of. Sliced by the comment the block opens with, so a
        # rename fails here rather than silently testing nothing.
        assert NAMING_BLOCK in source, (
            f"{NAMING_BLOCK!r} is gone, so this test no longer knows where the"
            " page's naming block starts."
        )
        block = source.split(NAMING_BLOCK)[1].split("async function publish")[0]
        harness = (
            "const DATA = {repo: 'o/r', branch: 'main', submissions_dir: 'd'};\n"
            "const window = {crypto: require('node:crypto').webcrypto};\n"
            f"function canonical{block.split('function canonical', 1)[1]}\n"
            f"{script}\n"
        )
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "harness.cjs"
            path.write_text(harness, encoding="utf-8")
            done = subprocess.run(
                [node, str(path)],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        assert done.returncode == 0, done.stderr
        return done.stdout.strip()

    def test_the_canonical_bytes_are_the_same_bytes(self, corpus):
        envelope = self.envelope(corpus)
        expected = envelopes.serialize(envelope).decode("utf-8")

        printed = self.under_node(
            f"process.stdout.write(canonical({envelope.model_dump_json()}));"
        )

        assert printed + "\n" == expected

    def test_the_submission_name_is_the_same_name(self, corpus):
        envelope = self.envelope(corpus)

        printed = self.under_node(
            f"submissionName({envelope.model_dump_json()})"
            ".then(name => process.stdout.write(name));"
        )

        assert printed == envelopes.submission_name(envelope)


class TestThePressLeavesThePageInPlace:
    """The pull request opens in a new tab, and the reader's tab stays.

    The page holds the reader's answers in memory. A press that navigated the
    reader's own tab would take them to GitHub and drop every unsaved mark
    behind it. ``window.open`` with ``noopener`` in its feature string returns
    ``null`` by specification, so a fallback onto ``window.location`` was the
    path every browser took. The block runs under ``node`` with a stubbed
    window, in both states a browser can leave it.
    """

    def press(self, opened: str) -> dict:
        node = shutil.which("node")
        if node is None:
            pytest.skip("no node on PATH to run the page's own block")
        source = client_script("offline_sitting.js")
        marker = "async function publishToGitHub"
        assert marker in source, f"{marker!r} is gone; this test no longer finds it"
        block = marker + source.split(marker)[1].split("\nfunction restore")[0]
        harness = (
            f"const opened = {opened};\n"
            "const window = {open: () => opened, location: 'the page'};\n"
            "let said = '';\n"
            "const alert = text => { said = text; };\n"
            "const finished = () => ['02-iot-fleet-telemetry'];\n"
            "const envelope = () => ({});\n"
            "const contributionUrl = async () => 'https://github.com/o/r/new/main';\n"
            f"{block}\n"
            "publishToGitHub().then(() => process.stdout.write(JSON.stringify({\n"
            "  page: window.location, opened: opened && opened.location,\n"
            "  said: said, opener: opened && opened.opener,\n"
            "})));\n"
        )
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "press.cjs"
            path.write_text(harness, encoding="utf-8")
            done = subprocess.run(
                [node, str(path)],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout)

    def test_the_new_tab_carries_the_reader_and_the_page_stays(self):
        result = self.press("{opener: 'the page'}")

        assert result["opened"] == "https://github.com/o/r/new/main"
        assert result["opener"] is None, "the new tab cannot reach back"
        assert result["page"] == "the page"

    def test_a_blocked_tab_is_said_and_the_page_still_stays(self):
        result = self.press("null")

        assert result["page"] == "the page"
        assert "blocked" in result["said"]

    def test_noopener_is_never_a_feature_string(self):
        """The feature makes ``window.open`` return null, which is the defect."""
        assert '"noopener"' not in client_script("offline_sitting.js")
