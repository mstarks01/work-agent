"""A constant that enumerates a closed vocabulary, against the type that declares it.

## The pair this reads

A module declares a closed set twice. Once as a ``Literal``, which is what the
type checker reads, and once as a constant the code iterates, because a type is
not a value. ``Outcome`` and ``OUTCOMES``, ``Cause`` and ``CAUSES``,
``VerificationOutcome`` and the exit codes keyed by it. The second spelling is
annotated with the first, which is the author saying *this enumerates that*, and
this lint compares them.

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

## A closed set states its Literal

A module-level tuple, set, frozenset or dict of string literals that code reads
as a set, by ``in`` or ``not in``, or a dict by a key that is not a literal, is
a closed set. :func:`test_a_closed_set_states_its_literal` asks each one for the
annotation that names its ``Literal``, and the pair check above then holds the
members to the type. A table that is only iterated, or read by literal keys, is
not asked. A fixed list that filters free text, file names, provider errors or
corpus data is open by design and is declared in :data:`OPEN` with the reason.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import re
from collections.abc import Container, Iterable, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Literal, get_args

import pytest

from tests.source_tree import (
    PYTHON,
    REPO_ROOT,
    SHORTEST_REASON,
    imports,
    module_name,
    parse,
    source_files,
)

pytestmark = pytest.mark.lint

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
    ("evals.experiments.QA-2026-11-02-05.control_ballot", "CONTROL_CHECKS"): (
        "the checks of the item 4 control arm, which has no spoofing runs"
    ),
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
    ("analysis_service.graph", "READING_ENTRIES"): (
        "the entries that extract a model and pass it on, which an entry that"
        " stops at extraction or starts after it does not"
    ),
    ("analysis_service.graph", "PREPARING_ENTRIES"): (
        "the entries that reach the routing half, which an entry stopping"
        " before it does not"
    ),
    ("analysis_service.sources", "KIND_FORMATS"): (
        "the kinds that an upload allows, so a kind that only the page or the"
        " service writes is absent by construction"
    ),
    ("analysis_service.jobs", "TERMINAL_STATUSES"): (
        "the statuses a job stops in, so a status it passes through is absent"
        " by construction"
    ),
    ("analysis_service.jobs", "NON_LOCKING_STATUSES"): (
        "the statuses a resumed job ends in without a report, so every status"
        " that runs or ends in one is absent by construction"
    ),
    ("analysis_service.validation", "DIAGRAM_CODES"): (
        "the codes the rule that reads a diagram's structure writes, which is"
        " fewer than the codes every gate rule writes"
    ),
    ("evals.harness.bundle", "KEPT_WHEN_FAILED"): (
        "the case files a failed case may carry, which a report and the files"
        " written beside a report are not"
    ),
    ("evals.harness.calibration", "SCORED_LABELS"): (
        "the labels carrying an answer an identity rule can be graded against;"
        " a disposition states why a pair is unscorable and stays out of the"
        " denominator"
    ),
    ("evals.harness.sitting", "DONE_STATES"): (
        "the states a row needs nothing more in, so a state that still waits on"
        " its reader is absent by construction"
    ),
    ("evals.verify_corpus", "UNMEASURED_LANES"): (
        "the packages holding a lane no case exercises, which a package whose"
        " every lane is exercised does not appear in"
    ),
    ("analysis_service.api", "_REFUSALS"): (
        "the outcomes that refuse a submission, so the outcome that admits one "
        "is absent by construction, and so is the one a minted ID cannot reach"
    ),
    ("webapp.main", "_ADMISSION_MESSAGES"): (
        "the outcomes that refuse a page's submission, so the outcome that "
        "admits one is absent by construction, and so is the one a minted ID "
        "cannot reach"
    ),
    ("analysis_service.assertions", "ANSWER_ISSUES"): (
        "the codes a submitter's answer draws when it places nothing, so a code"
        " that refuses a row, or that a row draws whatever the answers, is"
        " absent by construction"
    ),
    ("analysis_service.assertions", "CATALOG_REFUSALS"): (
        "the codes that refuse a whole catalog, so a code that refuses one row "
        "of it is absent by construction"
    ),
    ("analysis_service.claims", "CONDITIONAL_GROUNDS"): (
        "the ground kinds that say a fact is unstated, so a ground that states "
        "a fact or its absence is absent by construction"
    ),
    ("analysis_service.claims", "ASSERTION_GROUNDS"): (
        "the ground kinds whose reference is a row of the assertion catalog, so"
        " a ground whose reference is not a catalog row is absent by"
        " construction"
    ),
    ("analysis_service.open_facts", "_ATTRIBUTE_QUESTIONS"): (
        "the control attributes whose plain question reads badly, so an "
        "attribute whose plain question reads well is absent by construction"
    ),
    ("analysis_service.frameworks.stride.rules", "ENTERED_ZONE_KINDS"): (
        "the boundary kinds that a crossing enters to transfer authority, so a"
        " kind that is no authority boundary is absent by construction"
    ),
    ("analysis_service.frameworks.stride.rules", "LEFT_ZONE_KINDS"): (
        "the boundary kinds that separate parties rather than authority levels,"
        " so a kind that separates authority levels within one party, or that is"
        " no authority boundary, is absent by construction"
    ),
    ("analysis_service.sampling", "OFFERED_PARAMS"): (
        "the sampling params an environment variable may override, so a "
        "reserved or a forbidden param is absent by construction"
    ),
    ("analysis_service.validation", "CITATION_FIELDS"): (
        "the source fields the gate checks against the job's sources, so a"
        " field that is never gated is absent by construction"
    ),
    ("analysis_service.validation", "RENAMING_CODES"): (
        "the codes whose repair may rename an element, so a code whose repair "
        "keeps the element's ID is absent by construction"
    ),
    ("evals.harness.applicability", "APPLIES"): (
        "the verdicts that assert a requirement applies, so the verdict that "
        "rules it out is absent by construction"
    ),
    ("evals.harness.certify", "_FLOAT_PARAMS"): (
        "the sampling params a sweep re-pins as a float, so a param of another "
        "type or outside promotion is absent by construction"
    ),
    ("evals.harness.certify", "_INT_PARAMS"): (
        "the sampling params a sweep re-pins as an integer, so a param of "
        "another type or outside promotion is absent by construction"
    ),
    ("evals.harness.certify", "_STR_PARAMS"): (
        "the sampling params a sweep re-pins as a string, so a param of another "
        "type or outside promotion is absent by construction"
    ),
    ("evals.harness.certify", "_NOT_PROMOTABLE"): (
        "the sampling params that record a property of the deployment, so a "
        "decoding value a sweep can tune is absent by construction"
    ),
    ("evals.harness.modes", "REPORTING_MODES"): (
        "the modes whose graph ends in a report, so a mode that stops at "
        "extraction or at the catalog is absent by construction"
    ),
    ("evals.harness.near_misses", "ASKABLE"): (
        "the vote statuses a displaced draft may carry and still be asked "
        "about, so the status of a draft a person refused is absent by "
        "construction"
    ),
    ("evals.harness.population", "REACHES"): (
        "the routes that put a row in front of a lane, so a route that leaves a "
        "row open, set aside or refused is absent by construction"
    ),
    ("evals.harness.replay", "ROW_LOSSES"): (
        "the row fates a prompt change can aim at, so a fate that keeps the"
        " fact, spells it another way, or records a row no producer should"
        " write is absent by construction"
    ),
    ("evals.harness.replay", "BIND_LOSSES"): (
        "the fates that lose a binding, so a fate that binds the row, falls on"
        " a subject no graph decides, or refuses the row for a reason that is"
        " not a binding is absent by construction"
    ),
    ("evals.harness.replay", "ROUTES_OF"): (
        "the modes whose sweep runs a reading node, so the instructions it"
        " carries place it on an arm; a mode that starts from the blessed model"
        " runs no reading node and is absent by construction"
    ),
    ("evals.harness.replay", "KEEPS"): (
        "the modes whose sweep keeps an emission or a catalog a replay reads, "
        "so a mode that keeps only reports is absent by construction"
    ),
}


def _annotated_vocabularies() -> list[tuple[Path, str, str]]:
    """Every ``NAME: container[TheType, ...]`` at a module's top level.

    Read off the annotation rather than named, so the pair a module grows
    tomorrow is covered with no edit here.
    """
    found: list[tuple[Path, str, str]] = []
    for path in source_files(*PYTHON):
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
            if not isinstance(first, ast.Name | ast.Attribute):
                continue
            name = ast.unparse(first)
            if name.rpartition(".")[2][0].isupper():
                found.append((path, node.target.id, name))
    return found


def _resolved(module: ModuleType, dotted: str) -> object | None:
    """The object that ``dotted``, such as ``sittings.RowState``, names in
    ``module``, or None."""
    found: object | None = module
    for part in dotted.split("."):
        found = getattr(found, part, None)
    return found


def _module(path: Path) -> ModuleType:
    """The module at ``path``. A CI script sits in no package, so it is loaded
    from its file."""
    if not module_name(path).startswith("."):
        return importlib.import_module(module_name(path))
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pairs() -> list[tuple[str, str, str, frozenset[str], frozenset[str]]]:
    """Each annotated constant beside the members its type declares.

    Resolved by importing, because the type may be declared in another module
    and an alias is still the same vocabulary. A pair whose type is not a
    ``Literal`` of strings is not one of these and is dropped here.
    """
    resolved = []
    for path, constant, type_name in _annotated_vocabularies():
        dotted = module_name(path)
        module = _module(path)
        vocabulary = _resolved(module, type_name)
        if not hasattr(module, constant) or vocabulary is None:
            continue
        members = get_args(vocabulary)
        if not members or not all(isinstance(one, str) for one in members):
            continue
        resolved.append(
            (
                dotted,
                constant,
                type_name,
                frozenset(getattr(module, constant)),
                frozenset(members),
            )
        )
    return resolved


PAIRS = _pairs()

#: Each constant annotated with a vocabulary, by module and name.
ANNOTATED = {(module, constant) for module, constant, *_ in PAIRS}


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
    unknown = sorted(key for key in PARTIAL if key not in ANNOTATED)

    assert not unknown, (
        f"PARTIAL names {unknown}, which this sweep does not find. The constant"
        " was renamed, moved, or lost its annotation."
    )


# A closed set states its Literal ("The classes that come back in fixes" in
# docs/agents/code-review.md).

#: The calls whose one argument lists the members of a set.
_SET_CALLS = frozenset({"frozenset", "set", "tuple"})

#: What a module-level constant of string literals is: a ``set`` of members (a
#: tuple, a set or a frozenset), or a ``table`` keyed by them (a dict).
Shape = Literal["set", "table"]

#: A module's dotted name and its parsed tree.
ModuleTree = tuple[str, ast.Module]

#: A table that code reads as a set and that is open by design, and why. The
#: reason is a property of the table: it is a fixed list that filters free text,
#: file names, provider errors or corpus data, so no value in the code carries
#: its members as a type. :func:`test_no_open_declaration_outlives_its_table`
#: refuses an entry that names no such table.
OPEN: dict[tuple[str, str], str] = {
    ("analysis_service.grounding", "_NEGATIONS"): (
        "a list of words that filters the tokens of a quoted span of free text"
    ),
    ("analysis_service.grounding", "_TYPOGRAPHIC_FOLDS"): (
        "a list of characters that filters each character of a model's quote"
    ),
    ("analysis_service.retry", "_RATE_LIMIT_CLEARS_WITH_TIME"): (
        "the limit dimensions a provider error names, which a provider can"
        " extend with a word the table has not met"
    ),
    ("analysis_service.sampling", "_BOOL_LITERALS"): (
        "the spellings that filter the free text of an environment variable"
    ),
    ("analysis_service.sources", "FORMATTING_CATEGORIES"): (
        "the Unicode categories that filter each character of free text"
    ),
    ("analysis_service.token_caps", "JOB_VARYING_DIRS"): (
        "directory names that filter the file names under a framework package"
    ),
    ("analysis_service.vendors", "_ENDPOINT_VARIANTS"): (
        "words that filter the parts of a model identifier that a deployment writes"
    ),
    ("analysis_service.vendors", "_FLOATING_WORDS"): (
        "words that filter the parts of a model identifier that a deployment writes"
    ),
    ("evals.build_review_docs", "HAND_WRITTEN"): (
        "case directory names, which the corpus holds as data and not as a type"
    ),
    ("evals.harness.archive", "KIND_NAMES"): (
        "whole file names that filter the files an archive directory holds"
    ),
    ("evals.harness.artifact", "UNGRADED"): (
        "file names that filter the files a case directory holds"
    ),
    ("evals.harness.ledger_isolation", "LEDGER_DIRS"): (
        "directory paths that filter the file paths a change touches"
    ),
    ("evals.harness.promotion", "RECALL_CASES"): (
        "case directory names, which the corpus holds as data and not as a type"
    ),
    ("examples.sync_docs", "FENCE_LANGUAGES"): (
        "file suffixes that filter the paths a document includes"
    ),
}


def _strings(nodes: Sequence[ast.expr | None]) -> bool:
    """Whether ``nodes`` is one string literal or more."""
    return bool(nodes) and all(
        isinstance(node, ast.Constant) and isinstance(node.value, str) for node in nodes
    )


def _shape(value: ast.expr) -> Shape | None:
    """The shape of a constant whose members are string literals, or None."""
    match value:
        case ast.Dict(keys=keys) if _strings(keys):
            return "table"
        case ast.Tuple(elts=members) | ast.Set(elts=members) if _strings(members):
            return "set"
        case ast.Call(
            func=ast.Name(id=call),
            args=[
                ast.Tuple(elts=members) | ast.Set(elts=members) | ast.List(elts=members)
            ],
        ) if call in _SET_CALLS and _strings(members):
            return "set"
    return None


def _string_constants(modules: Iterable[ModuleTree]) -> dict[tuple[str, str], Shape]:
    """Every module-level constant of string literals, by module and name."""
    found: dict[tuple[str, str], Shape] = {}
    for module, tree in modules:
        for node in tree.body:
            match node:
                case (
                    ast.Assign(targets=[ast.Name(id=name)], value=value)
                    | ast.AnnAssign(target=ast.Name(id=name), value=ast.expr() as value)
                ):
                    if shape := _shape(value):
                        found[(module, name)] = shape
    return found


def _referent(
    node: ast.expr,
    module: str,
    bound: Mapping[str, str],
    constants: Container[tuple[str, str]],
) -> tuple[str, str] | None:
    """The module and the name of the constant ``node`` reads, or None."""
    match node:
        case ast.Name(id=name) if (module, name) in constants:
            return (module, name)
        case ast.Name(id=name) if name in bound:
            source, _, constant = bound[name].rpartition(".")
            return (source, constant)
        case ast.Attribute(value=ast.Name(id=name), attr=constant) if name in bound:
            return (bound[name], constant)
    return None


def _set_reads(
    modules: Iterable[ModuleTree], constants: Mapping[tuple[str, str], Shape]
) -> set[tuple[str, str]]:
    """Each constant of ``constants`` that code reads as a set: by ``in`` or
    ``not in``, or a table by a key that is not a literal."""
    read: set[tuple[str, str]] = set()
    for module, tree in modules:
        bound = imports(tree)
        for node in ast.walk(tree):
            match node:
                case ast.Compare(ops=ops, comparators=rights):
                    asked = [
                        right
                        for op, right in zip(ops, rights, strict=True)
                        if isinstance(op, ast.In | ast.NotIn)
                    ]
                    shapes: tuple[Shape, ...] = ("set", "table")
                case ast.Subscript(value=table, slice=key) if not isinstance(
                    key, ast.Constant
                ):
                    asked, shapes = [table], ("table",)
                case ast.Call(
                    func=ast.Attribute(value=table, attr="get"), args=[key, *_]
                ) if not isinstance(key, ast.Constant):
                    asked, shapes = [table], ("table",)
                case _:
                    continue
            for one in asked:
                found = _referent(one, module, bound, constants)
                if found and constants.get(found) in shapes:
                    read.add(found)
    return read


TREES = [(module_name(path), parse(path)) for path in source_files(*PYTHON)]
CONSTANTS = _string_constants(TREES)
READ_AS_SETS = _set_reads(TREES, CONSTANTS)


def test_the_closed_set_sweep_finds_something():
    """Guards the guard: an empty discovery reads exactly like a clean tree."""
    assert len(READ_AS_SETS) > 50, (
        f"only {len(READ_AS_SETS)} constants read as sets found, so the check"
        " below is close to vacuous. The shape it reads has probably moved."
    )


def test_a_closed_set_states_its_literal():
    """Each constant that code reads as a set is annotated with its Literal.

    A set that code reads by membership or by a key is a vocabulary, and the
    ``Literal`` lets the type checker refuse a member it does not hold. The
    annotation is the claim :func:`test_a_constant_enumerates_the_type_it_is_annotated_with`
    holds to the members.
    """
    bare = sorted(READ_AS_SETS - ANNOTATED - set(OPEN))

    assert not bare, (
        f"{bare} are read as sets and carry no Literal. Annotate each with the"
        " Literal of its members, such as tuple[Kind, ...] = get_args(Kind) or"
        " dict[Kind, ...], or declare an open table in"
        " tests/test_vocabulary_lints.py OPEN with the reason it is open."
    )


def test_no_open_declaration_outlives_its_table():
    """An entry of :data:`OPEN` names a constant read as a set, with no Literal."""
    stale = sorted(key for key in OPEN if key not in READ_AS_SETS or key in ANNOTATED)
    thin = sorted(key for key, reason in OPEN.items() if len(reason) < SHORTEST_REASON)

    assert not stale, f"{stale} are no open table read as a set; delete each entry"
    assert not thin, f"state a reason for {thin}"


_CONTROL = """
from typing import Literal, get_args

Kind = Literal["a", "b"]
KINDS: tuple[Kind, ...] = get_args(Kind)
CHECKED = frozenset({"a", "b"})
KEYED = {"a": 1, "b": 2}
ITERATED = ("a", "b")
BY_LITERAL = {"a": 1}
NUMBERS = frozenset({1, 2})


def use(name, index):
    for one in ITERATED:
        pass
    return name in CHECKED, KEYED[name], BY_LITERAL["a"], ITERATED[index], 1 in NUMBERS
"""

_IMPORTER = """
import control
from control import BY_LITERAL, ITERATED


def use(name):
    return name not in ITERATED, BY_LITERAL.get(name), control.KEYED.get(name)
"""


def test_the_sweep_reads_membership_and_variable_keys_only():
    """A set read by ``in``, and a table read by a variable key, are found. A
    set only iterated or indexed, a table read by a literal key, and a set of
    numbers are not."""
    trees = [("control", ast.parse(_CONTROL))]

    assert _set_reads(trees, _string_constants(trees)) == {
        ("control", "CHECKED"),
        ("control", "KEYED"),
    }


def test_the_sweep_follows_an_import():
    """A read in another module, through either form of import, is found."""
    trees = [("control", ast.parse(_CONTROL)), ("user", ast.parse(_IMPORTER))]

    assert _set_reads(trees, _string_constants(trees)) == {
        ("control", "CHECKED"),
        ("control", "KEYED"),
        ("control", "ITERATED"),
        ("control", "BY_LITERAL"),
    }


# A claim of review needs the field that records it (docs/agents/provenance.md).

#: A sentence that says the maintainer reviews, or reviewed, a text.
_REVIEW_CLAIM = re.compile(r"\bthe maintainer review(?:s|ed)\b", re.IGNORECASE)

#: The names of a field that records who reviewed a row.
_REVIEW_FIELDS = frozenset({"reviewed_by", "REVIEWED_BY"})


def _defines_a_review_field(tree: ast.Module) -> bool:
    """Whether ``tree`` declares a field or a constant named in :data:`_REVIEW_FIELDS`."""
    return any(
        (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name))
        and node.target.id in _REVIEW_FIELDS
        or isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id in _REVIEW_FIELDS
            for target in node.targets
        )
        for node in ast.walk(tree)
    )


def test_a_review_claim_sits_beside_the_field_that_records_it():
    """A file that says the maintainer reviews its text defines ``reviewed_by``.

    A sentence is true only while somebody remembers it; a field that names
    the reviewer, or ``None``, is a value the code can count.
    """
    claims = [
        path
        for path in source_files(*PYTHON)
        if _REVIEW_CLAIM.search(path.read_text(encoding="utf-8"))
    ]
    assert claims, "no review claim found; the sentence shape has probably moved"
    unrecorded = sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for path in claims
        if not _defines_a_review_field(parse(path))
    )
    assert not unrecorded, (
        f"{unrecorded} say the maintainer reviews a text and define no"
        " reviewed_by or REVIEWED_BY field; record the review in a field, or"
        " remove the sentence"
    )
