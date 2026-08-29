"""Tests for reference recognition: the fold, its guard, and what it refuses."""

import pytest

from analysis_service.references import canonical, fold

ELEMENT_IDS = frozenset(
    {"entity:customer", "process:web-app", "store:orders-db", "flow:a-to-b:x"}
)


class TestFold:
    @pytest.mark.parametrize(
        "left, right",
        [
            ("process:web-app", "Process:Web-App"),
            ("process:web-app", "  process:web-app  "),
            ("process:web-app", "process:web-app\n"),
            ("System description", "system   description"),
        ],
    )
    def test_spellings_of_one_name_share_a_key(self, left, right):
        assert fold(left) == fold(right)

    def test_different_names_do_not(self):
        assert fold("process:web-app") != fold("process:web-apps")


class TestCanonical:
    def test_an_exact_reference_is_returned_as_given(self):
        assert canonical("process:web-app", ELEMENT_IDS) == "process:web-app"

    @pytest.mark.parametrize(
        "reference", ["Process:Web-App", "PROCESS:WEB-APP", " process:web-app "]
    )
    def test_a_respelled_reference_resolves(self, reference):
        assert canonical(reference, ELEMENT_IDS) == "process:web-app"

    def test_a_reference_naming_nothing_resolves_to_nothing(self):
        assert canonical("process:ghost", ELEMENT_IDS) == ""

    def test_an_ambiguous_fold_resolves_to_nothing(self):
        """Two known spellings sharing a key mean the reference picks neither."""
        labels = {"System description", "SYSTEM DESCRIPTION"}
        assert canonical("system description", labels) == ""

    def test_an_exact_match_beats_an_ambiguous_fold(self):
        labels = {"System description", "SYSTEM DESCRIPTION"}
        assert canonical("System description", labels) == "System description"

    def test_two_ids_differing_only_in_case_resolve_to_neither(self):
        """The hole the guard is for, pinned as a case rather than an argument.

        ``normalize_name`` raises on a name that slugs to nothing and the gate
        skips ``id-mismatch`` rather than guessing, so a model naming every
        element ``"!!!"`` and ``"???"`` really can carry both of these at once.
        """
        assert canonical("process:foo", {"process:FOO", "process:foo"}) == "process:foo"
        assert canonical("Process:Foo", {"process:FOO", "process:foo"}) == ""


class TestRefusedRungs:
    """The two rungs this module deliberately does not have.

    Both would resolve here and both are unmeasured; each stays a fatal
    reference failure until a real run shows a model producing it.
    """

    def test_a_bare_slug_does_not_resolve(self):
        assert canonical("web-app", ELEMENT_IDS) == ""

    def test_the_wrong_type_prefix_does_not_resolve(self):
        assert canonical("store:web-app", ELEMENT_IDS) == ""

    def test_a_human_name_does_not_resolve(self):
        assert canonical("Web App", ELEMENT_IDS) == ""
