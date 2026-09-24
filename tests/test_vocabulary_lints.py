"""A constant that enumerates a closed vocabulary, against the type that declares it.

## The pair this reads

A module declares a closed set twice. Once as a ``Literal``, which is what the
type checker reads, and once as a constant the code iterates, because a type is
not a value. ``Outcome`` and ``OUTCOMES``, ``Cause`` and ``CAUSES``, ``Verdict``
and the exit codes keyed by it. The second spelling is annotated with the first,
which is the author saying *this enumerates that* — and nothing compared them.

A member added to the type and not to the constant is silent. Whatever iterates
the constant then answers for fewer members, and every test of that constant
agrees with it, because each side is tested against its own expectation. That is
the shape ``docs/agents/code-review.md`` calls one rule with two readers, and the
repair it names is to delete a reader: ``tuple(get_args(TheType))`` needs no
lint at all, and several constants here are already written that way.

Where the constant stays spelled out — an order the type does not carry, a
comment per member — this is the fallback the guide allows: the two are tested
against each other rather than each against itself.

## Found, never listed

The pairs come from the annotations, so a constant written tomorrow is read the
day it lands. Listing them here would be the second reader this exists to
remove.

## A subset is legitimate and says why

Some constants name part of a vocabulary on purpose: the terminal job statuses,
the labels a rule is graded on, the entries that end in a catalog. Those are
declared in :data:`PARTIAL` with the reason, stated as a property of the subset
rather than as its name. :func:`test_no_declaration_outlives_its_subset` refuses
an entry whose constant has since grown to the whole vocabulary, so the table
cannot rot into coverage it no longer provides.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from typing import get_args

import pytest

from tests.source_tree import REPO_ROOT, parse, source_files

#: Container annotations whose first parameter names the vocabulary. A mapping
#: is included because a table keyed by the vocabulary answers for it exactly as
#: a tuple of its members does -- ``EXIT_CODES`` is one, and a verdict with no
#: exit code raises in the command that reports it.
_CONTAINERS = frozenset({"tuple", "frozenset", "set", "list", "dict", "Mapping"})

#: A constant that names part of its vocabulary on purpose, and why.
#:
#: **The reason is a property of the subset**, never its name, so it answers for
#: a member nobody has added yet. An entry here is a declaration and not a
#: suppression: the constant still has to stay a subset, which
#: :func:`test_no_declaration_outlives_its_subset` checks.
PARTIAL: dict[tuple[str, str], str] = {
    ("analysis_service.claims", "ARCHIVED_UNRECONCILED_KINDS"): (
        "the kinds only archived reports carry, which is fewer than the kinds"
        " a live review writes"
    ),
    ("analysis_service.graph", "CATALOGUING_ENTRIES"): (
        "the entries whose graph ends in a resolved catalog, which is fewer"
        " than the entries that exist"
    ),
    ("analysis_service.graph", "EXTRACTING_ENTRIES"): (
        "the entries that extract a model, which an entry starting after"
        " extraction does not"
    ),
    ("analysis_service.graph", "PREPARING_ENTRIES"): (
        "the entries that reach the routing half, which an entry stopping"
        " before it does not"
    ),
    ("analysis_service.jobs", "TERMINAL_STATUSES"): (
        "the statuses a job stops in, so a status it passes through is absent"
        " by construction"
    ),
    ("evals.harness.calibration", "SCORED_LABELS"): (
        "the labels carrying an answer an identity rule can be graded against;"
        " a disposition states why a pair is unscorable and stays out of the"
        " denominator"
    ),
    ("evals.harness.grounds", "_KINDS"): (
        "the ground kinds this instrument counts, which excludes the kinds it"
        " records under a shape of their own"
    ),
    ("evals.harness.modes", "EVAL_FRAMEWORKS"): (
        "the fallback for a case declaring no framework; which frameworks run"
        " is a property of the case, read through case_frameworks"
    ),
    ("evals.verify_corpus", "UNMEASURED_LANES"): (
        "the packages holding a lane no case exercises, which a package whose"
        " every lane is exercised does not appear in"
    ),
}


def _module_name(path: Path) -> str:
    """The import name for one source file under the repository root."""
    parts = list(path.relative_to(REPO_ROOT).with_suffix("").parts)
    if parts[0] == "src":
        parts = parts[1:]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _annotated_vocabularies() -> list[tuple[str, str, str]]:
    """Every ``NAME: container[TheType, ...]`` at a module's top level.

    Read off the annotation rather than named, so the pair a module grows
    tomorrow is covered with no edit here.
    """
    found: list[tuple[str, str, str]] = []
    for path in source_files("src", "evals", "webapp"):
        try:
            tree = parse(path)
        except SyntaxError:  # pragma: no cover - the tree parses
            continue
        for node in tree.body:
            if not isinstance(node, ast.AnnAssign):
                continue
            if not isinstance(node.target, ast.Name) or node.value is None:
                continue
            annotation = node.annotation
            if not isinstance(annotation, ast.Subscript):
                continue
            if getattr(annotation.value, "id", "") not in _CONTAINERS:
                continue
            inside = annotation.slice
            first = inside.elts[0] if isinstance(inside, ast.Tuple) else inside
            name = getattr(first, "id", "")
            if name and name[0].isupper():
                found.append((_module_name(path), node.target.id, name))
    return found


def _pairs() -> list[tuple[str, str, str, frozenset[str], frozenset[str]]]:
    """Each annotated constant beside the members its type declares.

    Resolved by importing, because the type may be declared in another module
    and an alias is still the same vocabulary. A pair whose type is not a
    ``Literal`` of strings is not one of these and is dropped here.
    """
    resolved = []
    for module_name, constant, type_name in _annotated_vocabularies():
        module = importlib.import_module(module_name)
        if not hasattr(module, constant) or not hasattr(module, type_name):
            continue
        members = get_args(getattr(module, type_name))
        if not members or not all(isinstance(one, str) for one in members):
            continue
        resolved.append(
            (
                module_name,
                constant,
                type_name,
                frozenset(getattr(module, constant)),
                frozenset(members),
            )
        )
    return resolved


PAIRS = _pairs()


def test_the_sweep_finds_something():
    """Guards the guard: an empty discovery reads exactly like a clean tree."""
    assert len(PAIRS) > 20, (
        f"only {len(PAIRS)} annotated vocabularies found, so the check below"
        " is close to vacuous. The annotation shape it reads has probably moved."
    )


@pytest.mark.parametrize(
    "module,constant,type_name,have,want",
    PAIRS,
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_a_constant_enumerates_the_type_it_is_annotated_with(
    module, constant, type_name, have, want
):
    """The constant and its type name the same members, or the subset says why."""
    if have == want:
        return

    reason = PARTIAL.get((module, constant))
    assert reason and have < want, (
        f"{module}.{constant} is annotated with {type_name} and does not"
        f" enumerate it: missing {sorted(want - have)},"
        f" extra {sorted(have - want)}. Either add the member, derive the"
        f" constant with get_args({type_name}), or declare the subset in"
        " tests/test_vocabulary_lints.py PARTIAL with the reason it is one."
    )


def test_no_declaration_outlives_its_subset():
    """A declared subset that has grown to the whole vocabulary answers nothing.

    The reason it carries is what a later reader trusts, and a constant that now
    enumerates its type has stopped being the thing that reason describes.
    """
    whole = {
        (module, constant) for module, constant, _, have, want in PAIRS if have == want
    }
    stale = sorted(key for key in PARTIAL if key in whole)

    assert not stale, (
        f"these are declared partial and now enumerate their whole type:"
        f" {stale}. Delete the PARTIAL entry."
    )


def test_every_declaration_names_a_constant_that_exists():
    """A declaration for a constant nobody discovers is coverage that checks nothing."""
    known = {(module, constant) for module, constant, _, _, _ in PAIRS}
    unknown = sorted(key for key in PARTIAL if key not in known)

    assert not unknown, (
        f"PARTIAL names {unknown}, which this sweep does not find. The constant"
        " was renamed, moved, or lost its annotation."
    )
