"""Every closed vocabulary the schema declares, against the corpus that has to
produce it.

A ``Literal`` is a branch point: something downstream reads the value and acts
differently for each one. When no corpus case carries one of the values,
nothing in the repo proves the pipeline can reach that branch, and a rule keyed
on it reads as correct until a paid sweep counts its firings.

This is ``test_rule_coverage`` one layer deeper. A **Candidate** rule that fires
nowhere and an unproduced vocabulary value are one failure at two depths: the
dead rule is the symptom, and a value no **System Model** ever holds is the
cause.

What it proves is that the *corpus* carries a value. A ``model.json`` under
``evals/corpus/`` is agent-authored and unreviewed, so a green run here says
the corpus carries the value and says nothing about what the live extraction
agent produces.

Deterministic over the blessed ``model.json`` and free of provider calls, which
is why it gates on every PR rather than waiting for a sweep.
"""

from __future__ import annotations

import json
from typing import Literal, get_args, get_origin

import pytest

from evals import verify_corpus
from stride_service.system_model import CORE_ASSET_TAGS, Element, SystemModel
from stride_service.validation import parse_and_validate

#: The one vocabulary an annotation cannot carry. ``assets`` is ``list[str]``
#: because the validator extends it from config, so the closed core set is
#: named here rather than read off the field.
ASSET_VOCABULARY = "Element.assets"

#: Values the corpus does not produce, each with the reason it is acceptable.
#: An entry says the corpus is right to omit the value. A value the pipeline
#: *cannot* produce — no prompt rule chooses it, or the gate rejects it — does
#: not belong here; it belongs fixed.
UNEXERCISED: dict[str, str] = {
    "TrustBoundary.kind=other": (
        "`other` is the last resort prompts/extract.md defines, not a value a"
        " case should reach for: a zone typed `other` tells a downstream rule"
        " nothing. Every zone in the corpus answers one of the three specific"
        " questions instead, which is the intended outcome. Do not add a case"
        " to produce this value."
    ),
}


@pytest.fixture(scope="module")
def models() -> list[SystemModel]:
    """Every blessed model, through the shipped validity gate.

    The same gate the rules run behind: a value only counts as produced when it
    reaches a model production would accept.
    """
    loaded = []
    for case_dir in verify_corpus.case_dirs():
        model, issues = parse_and_validate(
            json.loads((case_dir / "model.json").read_text())
        )
        assert model is not None and not issues, f"{case_dir.name}: {issues}"
        loaded.append(model)
    return loaded


def vocabularies() -> dict[str, frozenset[str]]:
    """Every closed vocabulary the element classes declare, by qualified name.

    Read off the classes rather than hand-listed, so a ``Literal`` added to any
    element type is covered without anyone remembering this module.
    """
    found = {ASSET_VOCABULARY: CORE_ASSET_TAGS}
    for element_class in get_args(Element):
        for field_name, field in element_class.model_fields.items():
            if get_origin(field.annotation) is Literal:
                name = f"{element_class.__name__}.{field_name}"
                found[name] = frozenset(get_args(field.annotation))
    return found


def produced_values(models: list[SystemModel]) -> dict[str, set[str]]:
    """The values each vocabulary actually holds across the blessed models."""
    seen: dict[str, set[str]] = {name: set() for name in vocabularies()}
    for model in models:
        for element in model.elements():
            seen[ASSET_VOCABULARY].update(element.assets)
            for field_name in type(element).model_fields:
                name = f"{type(element).__name__}.{field_name}"
                if name in seen:
                    seen[name].add(getattr(element, field_name))
    return seen


def unproduced(models: list[SystemModel]) -> set[str]:
    """Every ``<class>.<field>=<value>`` no corpus case carries."""
    produced = produced_values(models)
    return {
        f"{name}={value}"
        for name, values in vocabularies().items()
        for value in values - produced[name]
    }


def test_no_vocabulary_value_is_unproduced_without_a_stated_reason(models):
    undeclared = sorted(unproduced(models) - set(UNEXERCISED))
    assert not undeclared, (
        "the corpus produces no element carrying these schema values, and"
        f" nothing says why: {undeclared}. Either the corpus lacks the shape —"
        " add the value to UNEXERCISED with the reason, and a case if the shape"
        " is worth carrying — or nothing upstream ever chooses the value, which"
        " is a defect in the extraction prompt or the gate."
    )


def test_the_exemption_list_does_not_rot(models):
    """A value that starts appearing has to leave the list, or it excuses nothing."""
    revived = sorted(set(UNEXERCISED) - unproduced(models))
    assert not revived, (
        "the corpus now produces these values and they are still exempted:"
        f" {revived}. Remove them from UNEXERCISED."
    )


def test_every_exempted_value_is_a_value_the_schema_declares(models):
    del models
    known = {
        f"{name}={value}" for name, values in vocabularies().items() for value in values
    }
    assert set(UNEXERCISED) <= known, (
        "UNEXERCISED names values no vocabulary declares:"
        f" {sorted(set(UNEXERCISED) - known)}"
    )
