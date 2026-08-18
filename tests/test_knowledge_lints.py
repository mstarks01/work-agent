"""CI lints over every package's local corpus.

A corpus is retrieved by rule ID out of a closed table on the **Framework
Package**, and the two halves are edited in different files — a document on
disk, its applicability in code. Nothing in the runtime notices when they
disagree: a document nobody selects is simply never read, and a table entry
naming a file that does not exist raises at the first job that fires the rule,
which is a live failure for a repository edit.

So both directions are checked here, along with the structure the composition
depends on and the token caps that keep parallel lanes affordable.

**Over :data:`~stride_service.frameworks.PACKAGES`, not over one directory.**
Only STRIDE ships a corpus today, so most of this is vacuous for ASVS — which
is exactly the shape :class:`~stride_service.frameworks.KnowledgeTables`
describes, where a package shipping none writes two empty tables and the gate
passes it vacuously. What the registry buys is that the moment any package adds
a single note, every check below already covers it. Naming one package's
directory meant a second package's first document would ship unlinted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stride_service.frameworks import PACKAGES
from stride_service.knowledge import MAX_CASES, MAX_NOTES
from stride_service.markdown_loader import (
    MarkdownLoader,
    estimate_tokens,
    split_sections,
)

FRAMEWORKS_DIR = Path(__file__).resolve().parents[1] / "frameworks"

# A note's three fixed H2 sections, in order — the same three a domain pack
# carries, because a note is the same kind of thing selected a different way:
# when it applies, what to ask, and what it may not be used for.
NOTE_SECTION_HEADINGS = ("When this applies", "What to look for", "Guardrails")

# A case's five, which are the shape the issue that asked for the library
# named: the architecture pattern, the threat considered, the ruling, the
# reasoning, and the evidence that decided it. "Ruling" is third rather than
# last on purpose — a reader who stops after two sections has the answer.
CASE_SECTION_HEADINGS = ("Pattern", "Considered", "Ruling", "Why", "What decided it")

# Per document. Lanes retrieve independently, so a note's cost is paid once per
# lane that selects it; these caps and MAX_NOTES/MAX_CASES are the two halves of
# the same budget.
NOTE_TOKEN_CAP = 700
CASE_TOKEN_CAP = 700

#: Packages shipping no corpus at all. Vacuous rather than exempt: every check
#: below runs and finds nothing to check, which is what
#: :class:`~stride_service.frameworks.KnowledgeTables` means by "the gate passes
#: it vacuously". Recorded so a reader knows the silence is the contract and not
#: a lint that stopped running.
#:
#: **ASVS is here on purpose.** Its lane skills carry the published requirement
#: text for the whole chapter — 47 verbatim requirements in the authentication
#: lane, with the chapter's scope and applicability beside them — so a note
#: restating a requirement would put the catalog in a second place to drift.
EMPTY_CORPUS = {"asvs"}


def documents(framework: str, kind: str) -> list[str]:
    """The document stems on disk for one package's notes or cases."""
    directory = FRAMEWORKS_DIR / framework / kind
    return sorted(path.stem for path in directory.glob("*.md"))


def loader_for(framework: str) -> MarkdownLoader:
    return MarkdownLoader(FRAMEWORKS_DIR / framework)


#: ``(framework, kind, name)`` for every document any package ships, so the
#: per-document checks parametrize across the registry rather than one tree.
ALL_DOCUMENTS = [
    (framework, kind, name)
    for framework in PACKAGES
    for kind in ("notes", "cases")
    for name in documents(framework, kind)
]
NOTE_DOCUMENTS = [entry for entry in ALL_DOCUMENTS if entry[1] == "notes"]
CASE_DOCUMENTS = [entry for entry in ALL_DOCUMENTS if entry[1] == "cases"]


def document_id(entry: tuple[str, str, str]) -> str:
    framework, _, name = entry
    return f"{framework}/{name}"


@pytest.mark.parametrize("framework", list(PACKAGES))
class TestTheIndexAndTheFilesAgree:
    """Neither direction fails visibly at run time, so both are checked here."""

    def test_every_indexed_note_exists(self, framework):
        assert sorted(PACKAGES[framework].knowledge.notes) == documents(
            framework, "notes"
        )

    def test_every_indexed_case_exists(self, framework):
        assert sorted(PACKAGES[framework].knowledge.cases) == documents(
            framework, "cases"
        )

    @pytest.mark.parametrize("kind", ["notes", "cases"])
    def test_every_named_rule_exists(self, framework, kind):
        """A document selected by a rule ID nobody fires is unreachable text.

        Rule IDs are edited in a package's own rule table and referenced in its
        knowledge tables, so a renamed rule silently strands whatever referenced
        it — the document stays in the tree, passes every other check, and is
        never retrieved again.
        """
        package = PACKAGES[framework]
        index = getattr(package.knowledge, kind)
        rule_ids = {rule.rule_id for rule in package.rules}
        named = {rule_id for rules in index.values() for rule_id in rules}
        assert named <= rule_ids, f"unknown rule IDs: {sorted(named - rule_ids)}"

    def test_every_rule_can_retrieve_something(self, framework):
        """A lead with no reference material behind it is a gap in the corpus.

        Not a hard requirement of the design — retrieval is allowed to return
        nothing — but a corpus is written against the rule table as it stands,
        so a rule with neither a note nor a case means one was added and its
        material was not.

        A package in :data:`EMPTY_CORPUS` ships no material for any rule, which
        is a different statement from having missed one, so it is skipped rather
        than failed. Adding one document to such a package removes it from that
        set and puts every one of its rules under this check at once — which is
        the intended cost of starting a corpus.
        """
        if framework in EMPTY_CORPUS:
            pytest.skip(f"{framework} ships no corpus; the tables are empty by design")
        package = PACKAGES[framework]
        rule_ids = {rule.rule_id for rule in package.rules}
        covered = {
            rule_id
            for index in (package.knowledge.notes, package.knowledge.cases)
            for rules in index.values()
            for rule_id in rules
        }
        assert rule_ids <= covered, f"no material for: {sorted(rule_ids - covered)}"


def test_a_package_with_an_empty_corpus_says_so(subtests):
    """The list cannot rot: a package that starts a corpus has to leave it.

    Without this, :data:`EMPTY_CORPUS` would silence
    ``test_every_rule_can_retrieve_something`` for a package that later shipped
    three notes and left a fourth rule bare.
    """
    for framework in EMPTY_CORPUS:
        with subtests.test(framework=framework):
            knowledge = PACKAGES[framework].knowledge
            assert not knowledge.notes and not knowledge.cases, (
                f"{framework} now ships a corpus and is still in EMPTY_CORPUS."
                " Remove it; every one of its rules then has to retrieve"
                " something."
            )


class TestDocumentStructure:
    """Composition concatenates these files verbatim, so shape is contract."""

    @pytest.mark.parametrize("entry", NOTE_DOCUMENTS, ids=document_id)
    def test_a_note_has_the_three_fixed_sections_in_order(self, entry):
        framework, _, name = entry
        sections = split_sections(loader_for(framework).load(f"notes/{name}"))
        assert tuple(sections) == NOTE_SECTION_HEADINGS

    @pytest.mark.parametrize("entry", CASE_DOCUMENTS, ids=document_id)
    def test_a_case_has_the_five_fixed_sections_in_order(self, entry):
        framework, _, name = entry
        sections = split_sections(loader_for(framework).load(f"cases/{name}"))
        assert tuple(sections) == CASE_SECTION_HEADINGS

    @pytest.mark.parametrize("entry", NOTE_DOCUMENTS, ids=document_id)
    def test_a_note_says_it_is_not_evidence(self, entry):
        """The one sentence every note must carry.

        A retrieved note reads exactly like the System Model block above it
        unless something says otherwise, and the prompt saying so once is
        weaker than each document saying so where it is read.
        """
        framework, _, name = entry
        sections = split_sections(loader_for(framework).load(f"notes/{name}"))
        assert "not evidence" in sections["Guardrails"].lower()

    @pytest.mark.parametrize("entry", CASE_DOCUMENTS, ids=document_id)
    def test_a_case_states_its_ruling_in_one_word(self, entry):
        """Accepted or rejected, at the top, in the section named for it."""
        framework, _, name = entry
        sections = split_sections(loader_for(framework).load(f"cases/{name}"))
        assert sections["Ruling"].lower().startswith(("accepted", "rejected"))

    @pytest.mark.parametrize("framework", list(PACKAGES))
    def test_the_library_carries_rejections_as_well_as_findings(self, framework):
        """Half the reason the library exists.

        Exemplars are finished drafts, so every one of them ends in a claim. A
        corpus that only showed accepted reasoning would teach the same lesson
        twice and leave "investigate and reject" — the outcome the candidate
        design calls the system working — with no worked example at all.

        A package shipping no cases has no library to hold to this. That is the
        vacuous reading rather than a pass: it says nothing about whether the
        package should ship one.
        """
        names = documents(framework, "cases")
        if not names:
            pytest.skip(f"{framework} ships no worked cases")
        rulings = [
            split_sections(loader_for(framework).load(f"cases/{name}"))[
                "Ruling"
            ].lower()
            for name in names
        ]
        assert any(ruling.startswith("rejected") for ruling in rulings)


class TestBudget:
    """Lanes retrieve independently; a document's cost is paid per lane."""

    @pytest.mark.parametrize("entry", NOTE_DOCUMENTS, ids=document_id)
    def test_a_note_is_within_its_cap(self, entry):
        framework, _, name = entry
        text = loader_for(framework).load(f"notes/{name}")
        assert estimate_tokens(text) <= NOTE_TOKEN_CAP

    @pytest.mark.parametrize("entry", CASE_DOCUMENTS, ids=document_id)
    def test_a_case_is_within_its_cap(self, entry):
        framework, _, name = entry
        text = loader_for(framework).load(f"cases/{name}")
        assert estimate_tokens(text) <= CASE_TOKEN_CAP

    def test_the_worst_lane_stays_under_the_domain_pack_block(self):
        """The size argument, in the units the prompt budget is argued in.

        Retrieval is capped per lane rather than per corpus, so what a job
        actually pays is this — and it is deliberately no larger than the
        domain-pack block that already rides beside it (2 x 2000). A corpus
        that grew past that would be arguing for a share of the envelope
        against material already there.

        Per lane, so it does not move when a package declares more lanes: ASVS's
        17 lanes each pay this ceiling and none of them pays it 17 times.
        """
        worst = MAX_NOTES * NOTE_TOKEN_CAP + MAX_CASES * CASE_TOKEN_CAP
        assert worst <= 4000
