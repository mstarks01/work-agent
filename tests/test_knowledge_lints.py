"""CI lints over the real ``knowledge/`` corpus.

The corpus is retrieved by rule ID out of a closed table in
:mod:`stride_service.knowledge`, and the two halves are edited in different
files — a document on disk, its applicability in code. Nothing in the runtime
notices when they disagree: a document nobody selects is simply never read, and
a table entry naming a file that does not exist raises at the first job that
fires the rule, which is a live failure for a repository edit.

So both directions are checked here, along with the structure the composition
depends on and the token caps that keep six parallel lanes affordable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stride_service.frameworks.stride import CASES, NOTES, STRIDE
from stride_service.knowledge import MAX_CASES, MAX_NOTES
from stride_service.markdown_loader import (
    MarkdownLoader,
    estimate_tokens,
    split_sections,
)

KNOWLEDGE_DIR = Path(__file__).resolve().parents[1] / "frameworks" / "stride"

# A note's three fixed H2 sections, in order — the same three a domain pack
# carries, because a note is the same kind of thing selected a different way:
# when it applies, what to ask, and what it may not be used for.
NOTE_SECTION_HEADINGS = ("When this applies", "What to look for", "Guardrails")

# A case's five, which are the shape the issue that asked for the library
# named: the architecture pattern, the threat considered, the ruling, the
# reasoning, and the evidence that decided it. "Ruling" is third rather than
# last on purpose — a reader who stops after two sections has the answer.
CASE_SECTION_HEADINGS = ("Pattern", "Considered", "Ruling", "Why", "What decided it")

# Per document. Six lanes retrieve independently, so a note's cost is paid up
# to six times on one job; these caps and MAX_NOTES/MAX_CASES are the two
# halves of the same budget.
NOTE_TOKEN_CAP = 700
CASE_TOKEN_CAP = 700

RULE_IDS = {rule.rule_id for rule in STRIDE.rules}

loader = MarkdownLoader(KNOWLEDGE_DIR)
note_files = sorted(path.stem for path in (KNOWLEDGE_DIR / "notes").glob("*.md"))
case_files = sorted(path.stem for path in (KNOWLEDGE_DIR / "cases").glob("*.md"))


class TestTheIndexAndTheFilesAgree:
    """Neither direction fails visibly at run time, so both are checked here."""

    def test_every_indexed_note_exists(self):
        assert sorted(NOTES) == note_files

    def test_every_indexed_case_exists(self):
        assert sorted(CASES) == case_files

    @pytest.mark.parametrize("index", [NOTES, CASES], ids=["notes", "cases"])
    def test_every_named_rule_exists(self, index):
        """A document selected by a rule ID nobody fires is unreachable text.

        Rule IDs are edited in ``candidates.py`` and referenced here, so a
        renamed rule silently strands whatever referenced it — the document
        stays in the tree, passes every other check, and is never retrieved
        again.
        """
        named = {rule_id for rules in index.values() for rule_id in rules}
        assert named <= RULE_IDS, f"unknown rule IDs: {sorted(named - RULE_IDS)}"

    def test_every_rule_can_retrieve_something(self):
        """A lead with no reference material behind it is a gap in the corpus.

        Not a hard requirement of the design — retrieval is allowed to return
        nothing — but the corpus was written against the twelve rules, so a
        rule with neither a note nor a case means one was added and its
        material was not.
        """
        covered = {
            rule_id
            for index in (NOTES, CASES)
            for rules in index.values()
            for rule_id in rules
        }
        assert RULE_IDS <= covered, f"no material for: {sorted(RULE_IDS - covered)}"


class TestDocumentStructure:
    """Composition concatenates these files verbatim, so shape is contract."""

    @pytest.mark.parametrize("name", note_files)
    def test_a_note_has_the_three_fixed_sections_in_order(self, name):
        sections = split_sections(loader.load(f"notes/{name}"))
        assert tuple(sections) == NOTE_SECTION_HEADINGS

    @pytest.mark.parametrize("name", case_files)
    def test_a_case_has_the_five_fixed_sections_in_order(self, name):
        sections = split_sections(loader.load(f"cases/{name}"))
        assert tuple(sections) == CASE_SECTION_HEADINGS

    @pytest.mark.parametrize("name", note_files)
    def test_a_note_says_it_is_not_evidence(self, name):
        """The one sentence every note must carry.

        A retrieved note reads exactly like the System Model block above it
        unless something says otherwise, and the prompt saying so once is
        weaker than each document saying so where it is read.
        """
        guardrails = split_sections(loader.load(f"notes/{name}"))["Guardrails"]
        assert "not evidence" in guardrails.lower()

    @pytest.mark.parametrize("name", case_files)
    def test_a_case_states_its_ruling_in_one_word(self, name):
        """Accepted or rejected, at the top, in the section named for it."""
        ruling = split_sections(loader.load(f"cases/{name}"))["Ruling"]
        assert ruling.lower().startswith(("accepted", "rejected"))

    def test_the_library_carries_rejections_as_well_as_findings(self):
        """Half the reason the library exists.

        Exemplars are finished drafts, so every one of them ends in a threat.
        A corpus that only showed accepted reasoning would teach the same
        lesson twice and leave "investigate and reject" — the outcome the
        candidate design calls the system working — with no worked example at
        all.
        """
        rulings = [
            split_sections(loader.load(f"cases/{name}"))["Ruling"].lower()
            for name in case_files
        ]
        assert any(ruling.startswith("rejected") for ruling in rulings)


class TestBudget:
    """Six lanes retrieve independently; a document's cost is paid per lane."""

    @pytest.mark.parametrize("name", note_files)
    def test_a_note_is_within_its_cap(self, name):
        assert estimate_tokens(loader.load(f"notes/{name}")) <= NOTE_TOKEN_CAP

    @pytest.mark.parametrize("name", case_files)
    def test_a_case_is_within_its_cap(self, name):
        assert estimate_tokens(loader.load(f"cases/{name}")) <= CASE_TOKEN_CAP

    def test_the_worst_lane_stays_under_the_domain_pack_block(self):
        """The size argument, in the units the prompt budget is argued in.

        Retrieval is capped per lane rather than per corpus, so what a job
        actually pays is this — and it is deliberately no larger than the
        domain-pack block that already rides beside it (2 x 2000). A corpus
        that grew past that would be arguing for a share of the envelope
        against material already there.
        """
        worst = MAX_NOTES * NOTE_TOKEN_CAP + MAX_CASES * CASE_TOKEN_CAP
        assert worst <= 4000
