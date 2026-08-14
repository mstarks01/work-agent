"""Retrieval from the local corpus: selected by what fired, and by nothing else.

The mechanism is small on purpose — a closed table, a set intersection and a
cap — and each of those three is load-bearing in a way worth pinning. The
table is what keeps caller text out of file selection, the intersection is what
makes the material follow the model rather than the category, and the cap is
what keeps six parallel lanes affordable.

What no test here can check is whether the retrieved text improves an analysis.
That needs a live sweep against the corpus, and this repository has never run
one. These tests establish that the right documents arrive, deterministically,
and stop exactly there.
"""

from __future__ import annotations

from pathlib import Path

from stride_service.frameworks.stride import CASES, NOTES, STRIDE
from stride_service.knowledge import (
    MAX_CASES,
    MAX_NOTES,
    compose_cases,
    compose_notes,
    select_documents,
)
from stride_service.markdown_loader import MarkdownLoader

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# The package's own text root: a document's home follows its retrieval key,
# and these are selected by STRIDE's own fired rules (ADR 0011).
loader = MarkdownLoader(PROJECT_ROOT / "frameworks" / "stride")


def select_notes(fired) -> tuple[str, ...]:
    """STRIDE's reference notes for one lane's fired rules."""
    return select_documents(NOTES, fired, MAX_NOTES)


def select_cases(fired) -> tuple[str, ...]:
    """STRIDE's worked cases for one lane's fired rules."""
    return select_documents(CASES, fired, MAX_CASES)


class TestSelection:
    def test_a_lane_that_fired_nothing_retrieves_nothing(self):
        """Progressive disclosure, in its most important case.

        Most lanes on most models fire no rule at all. Retrieving a default
        document for them would put the whole corpus into every job by another
        route, which is the outcome the design exists to avoid.
        """
        assert select_notes(set()) == ()
        assert select_cases(set()) == ()

    def test_a_fired_rule_retrieves_the_material_written_for_it(self):
        notes = select_notes({"spoofing-unverified-boundary-auth"})
        assert "identity-at-a-boundary" in notes

    def test_an_unrelated_rule_retrieves_nothing_of_it(self):
        """The intersection is what makes this retrieval rather than inclusion."""
        notes = select_notes({"denial-of-service-shared-dependency"})
        assert "identity-at-a-boundary" not in notes

    def test_selection_is_capped_per_lane(self):
        """Every rule at once is not a realistic lane; the cap is why it is safe."""
        every_rule = {rule.rule_id for rule in STRIDE.rules}
        assert len(select_notes(every_rule)) == MAX_NOTES
        assert len(select_cases(every_rule)) == MAX_CASES

    def test_the_best_matched_document_wins_the_cap(self):
        """Ranking by matches, so the cap drops the least relevant document.

        Both notes below are selected by the fired rules; the first is named by
        both of them and the second by one, so a cap of two keeps the
        two-rule note ahead of any single-rule one.
        """
        fired = {
            "spoofing-unverified-boundary-auth",
            "spoofing-unverified-external-caller",
        }
        assert select_notes(fired)[0] == "identity-at-a-boundary"

    def test_selection_is_stable_across_calls(self):
        """Two runs over one model must send byte-identical instructions.

        Ordering falls out of a sort over a dict built from a set, so a
        selection that reordered between calls would change the prompt for two
        otherwise identical jobs — the same property
        ``select_domain_packs`` needs.
        """
        fired = {rule.rule_id for rule in STRIDE.rules[:5]}
        assert select_notes(fired) == select_notes(fired)
        assert select_cases(fired) == select_cases(fired)

    def test_an_unknown_rule_id_selects_nothing_rather_than_raising(self):
        """A rule with no material is a gap in the corpus, not a failed job.

        The lints hold the two in step, so this is the behaviour when they
        drift anyway: the lane runs with less material, which is the same
        state as a lane that fired nothing.
        """
        assert select_notes({"a-rule-that-does-not-exist"}) == ()


class TestComposition:
    def test_nothing_selected_composes_to_the_empty_string(self):
        """The block a lane with no leads renders — nothing, not a note saying so."""
        assert compose_notes(loader, ()) == ""
        assert compose_cases(loader, ()) == ""

    def test_the_composed_text_is_the_documents_themselves(self):
        composed = compose_notes(loader, ("identity-at-a-boundary",))
        assert composed.startswith("# Identity at a Trust Boundary")
        assert "## Guardrails" in composed

    def test_two_documents_compose_in_selection_order(self):
        composed = compose_notes(
            loader, ("identity-at-a-boundary", "callback-and-webhook-trust")
        )
        assert composed.index("# Identity at a Trust Boundary") < composed.index(
            "# Callbacks, Webhooks and Inbound Events"
        )


class TestTheCorpusCannotBecomeEvidence:
    """The rule the whole corpus lives under, checked where it could break."""

    def test_no_document_is_reachable_from_the_evidence_seam(self):
        """Retrieval feeds the prompt and stops there.

        The importers of this module are the graph (which composes the prompt
        blocks) and nothing else — in particular not ``evidence.py``, which
        builds the closed set of citable facts, and not ``critic.py``, which
        resolves what a finding rests on. There is no code path by which a
        retrieved document could become a Ground.

        Recursive, and keyed by path rather than bare filename: a package
        under a subpackage (``frameworks/<name>/``) must stay covered by this
        allowlist rather than escaping a flat, non-recursive glob.
        """
        package = PROJECT_ROOT / "src" / "stride_service"
        importers = {
            path.relative_to(package).as_posix()
            for path in package.rglob("*.py")
            if "from stride_service.knowledge import" in path.read_text()
        }
        assert importers == {"graph.py"}

    def test_evidence_and_critic_never_import_knowledge(self):
        """The two modules the docstring above names, checked directly.

        A narrower, harder-to-fool companion to the allowlist test: it names
        the two modules that must never import ``knowledge`` and reads them
        by path, so it keeps failing loud even if the allowlist test above is
        ever weakened or deleted.
        """
        package = PROJECT_ROOT / "src" / "stride_service"
        for module in ("evidence.py", "critic.py"):
            source = (package / module).read_text()
            assert "from stride_service.knowledge import" not in source

    def test_a_document_id_is_never_an_evidence_reference(self):
        """The two ID spaces cannot collide.

        An evidence reference is ``unknown:<element>:<attribute>`` or
        ``crossing:<flow>``; a document is ``notes/<id>`` or ``cases/<id>``.
        A model copying a document name into ``evidence_refs`` names nothing in
        the catalog and is refused by the resolution seam, rather than
        resolving to something.
        """
        for name in (*NOTES, *CASES):
            assert not name.startswith(("unknown:", "crossing:"))
            assert ":" not in name
