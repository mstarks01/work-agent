"""The one reader of a job's framework selection (#675 D23)."""

import pytest

from analysis_service.frameworks import PACKAGES
from analysis_service.report import FrameworkSelection
from analysis_service.selection import SelectionError, resolve_selection
from tests.factories import sample_selection

CARRIED = tuple(PACKAGES)


def test_an_empty_selection_is_refused():
    with pytest.raises(SelectionError, match="at least one framework"):
        resolve_selection(CARRIED, [])


def test_a_repeated_name_is_refused_rather_than_collapsed():
    """The report's ``analyses`` is a list so a dropped block is visible;
    de-duplicating here would be the same loss one layer earlier."""
    twice = [sample_selection()[0], sample_selection()[0]]
    with pytest.raises(SelectionError, match="repeats"):
        resolve_selection(CARRIED, twice)


def test_a_name_the_install_does_not_carry_is_refused():
    narrow = CARRIED[:1]
    with pytest.raises(SelectionError, match="does not carry"):
        resolve_selection(narrow, sample_selection())


def test_options_are_held_to_the_packages_own_model():
    """Stated as a property of the package: one whose options model requires
    a field refuses a selection without it, and the message names the field
    rather than quoting the body."""
    demanding = [
        selection
        for selection in sample_selection()
        if PACKAGES[selection.name].options.model_fields
    ]
    if not demanding:
        pytest.skip("no carried package declares an option")
    bare = FrameworkSelection(name=demanding[0].name)
    required = [
        name
        for name, field in PACKAGES[bare.name].options.model_fields.items()
        if field.is_required()
    ]
    with pytest.raises(SelectionError, match="are invalid") as caught:
        resolve_selection(CARRIED, [bare])
    for name in required:
        assert name in str(caught.value)


def test_the_callers_order_is_preserved():
    reversed_selection = list(reversed(sample_selection()))
    resolved = resolve_selection(CARRIED, reversed_selection)
    assert [entry.name for entry in resolved] == [
        entry.name for entry in reversed_selection
    ]
