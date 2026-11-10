"""Rules that one function reads, and the lints that find a second reader.

Each lint walks the Python the project runs, the named set
:data:`tests.source_tree.PYTHON`, for the expression that applies a rule, and
names each function that holds it. Every function but
the one reader calls that reader (``docs/agents/one-rule-one-reader.md``). A
function that applies the rule for another reason is declared with that
reason, and a second test fails when the declaration stops being live.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import re
from collections.abc import Callable, Iterable, Iterator
from functools import cache, partial
from itertools import pairwise
from pathlib import Path
from typing import TypeGuard, get_args

import pytest

from analysis_service.analysis import CONTROL_ATTRIBUTES
from analysis_service.claims import UNKNOWN_KEY_FIELDS, CriticStep, Ruling
from analysis_service.sql_store import DIGEST_COLUMNS
from evals.harness.prices import UnitPrices
from tests.source_tree import (
    EXPERIMENTS_LEFT_OUT,
    PYTHON,
    REPO_ROOT,
    TESTS,
    imports,
    parse,
    source_files,
    without,
)
from tests.test_five_class_read import HELD_ROWS

pytestmark = pytest.mark.lint

#: A site: the file, relative to the repository root, and the enclosing
#: function, or :data:`MODULE_OWNER`.
Site = tuple[str, str]

#: The function part of a site at the module level of its file.
MODULE_OWNER = "<module>"


#: The files each lint walks.
FILES = without(source_files(*PYTHON), EXPERIMENTS_LEFT_OUT)


def _sites(
    match: Callable[[ast.AST], bool], files: Iterable[Path] = FILES
) -> list[Site]:
    """Each site in ``files`` that holds a node ``match`` accepts, once."""
    return sorted(
        {
            (path.relative_to(REPO_ROOT).as_posix(), function)
            for path in files
            for node, function in _walk(parse(path), MODULE_OWNER)
            if match(node)
        }
    )


def _walk(node: ast.AST, function: str) -> Iterator[tuple[ast.AST, str]]:
    for child in ast.iter_child_nodes(node):
        inner = function
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
            inner = child.name
        yield child, inner
        yield from _walk(child, inner)


def _calls(node: ast.AST, name: str) -> TypeGuard[ast.Call]:
    """Whether ``node`` calls ``name``, as a function or as a method."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (isinstance(func, ast.Name) and func.id == name) or (
        isinstance(func, ast.Attribute) and func.attr == name
    )


def _is_name(node: ast.AST, *names: str) -> bool:
    return isinstance(node, ast.Name) and node.id in names


def test_each_store_choice_has_one_reader():
    """Only ``job_store_name`` and ``object_store_name`` choose a store row."""

    def chooses(table: str) -> Callable[[ast.AST], bool]:
        return lambda node: (
            _calls(node, "closed_setting")
            and len(node.args) == 3
            and _is_name(node.args[2], table)
        )

    assert _sites(chooses("JOB_STORES")) == [
        ("src/analysis_service/store_backends.py", "job_store_name")
    ]
    assert _sites(chooses("OBJECT_STORES")) == [
        ("src/analysis_service/objects.py", "object_store_name")
    ]


def test_a_case_s_requirement_records_have_one_filter():
    """Only ``GoldenCase.requirement_records`` picks a case's catalog-keyed
    records out of its reference set; every other reader calls it."""

    def names_the_record(node: ast.AST) -> bool:
        return _is_name(node, "ReferenceRequirement") or (
            isinstance(node, ast.Attribute) and node.attr == "ReferenceRequirement"
        )

    def picks(node: ast.AST) -> bool:
        if not (_calls(node, "isinstance") and len(node.args) == 2):
            return False
        kinds = node.args[1]
        if isinstance(kinds, ast.Tuple):
            return any(names_the_record(kind) for kind in kinds.elts)
        return names_the_record(kinds)

    assert _sites(picks, (*FILES, *source_files(*TESTS))) == [
        ("evals/harness/reference.py", "requirement_records")
    ]


#: Each entry point that prints a configuration refusal itself, beside
#: ``refuse_entry``, with the reason: it loads no corpus.
CONFIG_REFUSERS: dict[Site, str] = {
    ("evals/critic_review/__main__.py", "main"): "it reads the tiers and no corpus",
    ("src/analysis_service/migrations/__main__.py", "main"): (
        "it reads the database settings and no corpus"
    ),
}


def test_an_entry_point_refusal_has_one_printer():
    """Only ``refuse_entry`` prints the refusal of a corpus that does not
    load. Each entry point that loads the corpus catches ``ENTRY_ERRORS`` and
    returns it, so the pair of refusals is written once."""
    assert _sites(lambda node: _calls(node, "corpus_refusal")) == [
        ("evals/harness/entry_refusal.py", "refuse_entry")
    ]
    assert _sites(lambda node: _calls(node, "config_refusal")) == sorted(
        [("evals/harness/entry_refusal.py", "refuse_entry"), *CONFIG_REFUSERS]
    )


def _variable_definitions(path: Path) -> list[ast.expr]:
    """The value of each module-level ``*_VAR`` assignment in ``path``."""
    found = []
    for node in parse(path).body:
        match node:
            case (
                ast.Assign(targets=[ast.Name(id=name)], value=value)
                | ast.AnnAssign(target=ast.Name(id=name), value=ast.expr() as value)
            ) if name.endswith("_VAR"):
                found.append(value)
    return found


def _variable_names() -> set[str]:
    """The value of each module-level ``*_VAR`` constant. The module is
    imported, because a value can be an f-string over a prefix."""
    names = set()
    for path in FILES:
        if not _variable_definitions(path):
            continue
        parts = path.relative_to(REPO_ROOT).with_suffix("").parts
        module = importlib.import_module(
            ".".join(parts[1:] if parts[0] == "src" else parts)
        )
        names |= {
            value
            for name, value in vars(module).items()
            if name.endswith("_VAR") and isinstance(value, str)
        }
    return names


def _docstrings(tree: ast.Module) -> set[int]:
    """The id of each docstring node in ``tree``: prose, which a lint of code
    does not read."""
    return {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        )
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }


def test_each_variable_name_is_spelled_once():
    """A string spells the name of an environment variable only in the
    ``*_VAR`` constant that holds it. A message and a table read the constant,
    so a rename moves every site. A docstring is prose and is not read."""
    names = _variable_names()
    assert "ANALYSIS_OBJECT_STORE" in names
    spelled = re.compile(
        r"\b(?:"
        + "|".join(map(re.escape, sorted(names, key=len, reverse=True)))
        + r")\b"
    )
    sites = []
    for path in FILES:
        tree = parse(path)
        skipped = _docstrings(tree) | {id(node) for node in _variable_definitions(path)}
        sites += [
            f"{path.relative_to(REPO_ROOT).as_posix()}:{node.lineno}: {node.value!r}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in skipped
            and spelled.search(node.value)
        ]
    assert not sites


#: A string that names an XDG base directory or the home directory default
#: of one: an ``XDG_`` variable, or a ``.config`` or ``.local`` path part.
_XDG_PLACE = re.compile(r"^XDG_|(?:^|[/~])\.(?:config|local)(?:/|$)")


def test_an_xdg_variable_has_one_reader():
    """Only ``XDG_BASES`` names an XDG base directory variable or its default
    in the home directory, and each call of ``xdg_dir`` names a row of it."""
    from analysis_service import config_files

    sites = []
    for path in FILES:
        tree = parse(path)
        skipped = _docstrings(tree)
        if path == Path(config_files.__file__):
            skipped |= {
                id(part)
                for node in ast.walk(tree)
                if isinstance(node, ast.AnnAssign)
                and _is_name(node.target, "XDG_BASES")
                for part in ast.walk(node)
            }
        sites += [
            f"{path.relative_to(REPO_ROOT).as_posix()}:{node.lineno}"
            for node in ast.walk(tree)
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and _XDG_PLACE.search(node.value)
                and id(node) not in skipped
            )
            or (
                _calls(node, "xdg_dir")
                and not (
                    len(node.args) == 2
                    and isinstance(node.args[1], ast.Constant)
                    and node.args[1].value in config_files.XDG_BASES
                )
            )
        ]
    assert not sites


def test_the_store_defaults_have_one_merge():
    """Only ``with_store_defaults`` lays an env over the app's store defaults."""

    def merges(node: ast.AST) -> bool:
        return isinstance(node, ast.Dict) and any(
            key is None
            and isinstance(value, ast.Call)
            and _is_name(value.func, "store_defaults")
            for key, value in zip(node.keys, node.values, strict=True)
        )

    assert _sites(merges) == [("webapp/settings_view.py", "with_store_defaults")]


#: Each function that strips ``_FILE`` from a variable that names no key, and
#: why it cannot ask ``refused_var``.
NOT_A_KEY: dict[Site, str] = {
    ("webapp/settings_page.py", "_deployment_settings"): (
        "the label of every store variable, ANALYSIS_DATABASE_CA_FILE among"
        " them, which names a CA certificate and no key"
    ),
}


def _strips_file_suffix(node: ast.AST) -> bool:
    if not (_calls(node, "removesuffix") and len(node.args) == 1):
        return False
    [suffix] = node.args
    return _is_name(suffix, "SECRET_FILE_SUFFIX") or (
        isinstance(suffix, ast.Constant) and suffix.value == "_FILE"
    )


def test_the_file_suffix_of_a_key_variable_has_one_reader():
    """Only ``refused_var`` strips the suffix of a key variable."""
    readers = set(_sites(_strips_file_suffix)) - NOT_A_KEY.keys()
    assert readers == {("src/analysis_service/config_files.py", "refused_var")}


def test_each_declared_site_still_strips():
    assert NOT_A_KEY.keys() <= set(_sites(_strips_file_suffix))


def _held(node: ast.AST, match: Callable[[ast.AST], bool]) -> bool:
    """Whether ``node`` or a node inside it is one that ``match`` accepts."""
    return any(match(inner) for inner in ast.walk(node))


def _is_attribute(node: ast.AST, attr: str, *owners: str) -> TypeGuard[ast.Attribute]:
    """Whether ``node`` reads ``attr``, of a name in ``owners`` where given."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == attr
        and (not owners or _is_name(node.value, *owners))
    )


def _operands(test: ast.expr) -> Iterator[ast.expr]:
    """The expressions a test reads for their truth, through ``and`` and ``or``."""
    if isinstance(test, ast.BoolOp):
        for value in test.values:
            yield from _operands(value)
    else:
        yield test


def _tested(node: ast.AST) -> list[ast.expr]:
    """Each expression ``node`` reads only for its truth."""
    match node:
        case ast.If(test=test) | ast.IfExp(test=test) | ast.While(test=test):
            tests = [test]
        case ast.Assert(test=test):
            tests = [test]
        case ast.comprehension(ifs=ifs):
            tests = list(ifs)
        case ast.UnaryOp(op=ast.Not(), operand=operand):
            tests = [operand]
        case ast.Call(func=ast.Name(id="bool"), args=[argument]):
            tests = [argument]
        case _:
            tests = []
    return [operand for test in tests for operand in _operands(test)]


def _tests_truth_of(match: Callable[[ast.AST], bool]) -> Callable[[ast.AST], bool]:
    return lambda node: any(match(operand) for operand in _tested(node))


#: Each function in ``bundle`` that names a case file to write it, or to refuse
#: an out folder that already holds one. Only ``finished_file`` reads one.
CASE_FILE_WRITERS: list[Site] = [
    ("evals/harness/bundle.py", "check_sweep_out"),
    ("evals/harness/bundle.py", "write_assertions"),
    ("evals/harness/bundle.py", "write_extractions"),
    ("evals/harness/bundle.py", "write_failures"),
    ("evals/harness/bundle.py", "write_reports"),
]


def test_a_case_file_is_read_through_finished_file():
    """Only ``finished_file`` reads a case file: a finished case must have it."""
    assert _sites(lambda node: _calls(node, "case_file")) == sorted(
        [*CASE_FILE_WRITERS, ("evals/harness/bundle.py", "finished_file")]
    )


def test_a_claim_matcher_reads_the_flows_flows_by_case_builds():
    """No function builds a ``SubsetVerbIdentity`` beside a ``flow_endpoints`` map.

    ``flows_by_case`` decides which model resolves a cited flow: the blessed
    model, with the run's analysed model filling each gap (#949). A map read
    beside the matcher resolves an end-to-end claim against one model alone.
    """
    matchers = set(_sites(lambda node: _calls(node, "SubsetVerbIdentity")))
    inline = set(_sites(lambda node: _is_attribute(node, "flow_endpoints")))
    assert matchers
    assert matchers & inline == set()


#: The primitives the Critic's round is built from. Each builds or checks what
#: a critic reads or returned, and a second caller is a second reader of one
#: draft (#1082): a view with fewer marks, a merge with its own brief, an
#: assembly outside the round's outcome.
CRITIC_PRIMITIVES = ("critic_view", "review", "merge_retry", "assemble_claims")


def test_the_critic_round_is_the_one_caller_of_the_critic_primitives():
    """Only ``critic_round`` calls what builds a view, merges a re-ask or assembles."""
    sites = _sites(lambda node: any(_calls(node, name) for name in CRITIC_PRIMITIVES))
    assert sites
    assert {path for path, _ in sites} == {"src/analysis_service/critic_round.py"}


def test_a_flow_map_names_its_siblings_once():
    """Only a ``CaseFlows`` builder computes which flows are siblings.

    ``channels`` reads ``CaseFlows.siblings`` and nothing else, so a map built
    anywhere else would carry no siblings. ``cited_channels`` is the service's
    own reader of the same question (ADR 0061).
    """
    assert _sites(lambda node: _calls(node, "sibling_flows")) == [
        ("evals/harness/identity.py", "of_graph"),
        ("evals/harness/reference.py", "_case_flows"),
        ("src/analysis_service/claim_identity.py", "cited_channels"),
    ]


def test_a_reports_rows_have_one_reader():
    """Only ``report_rows`` reads the row controls of a **Report**.

    ``row_controls`` takes a catalog, and a caller that holds a Report reads
    its rows through ``report_rows``. ``absent_rows`` is the one other caller,
    because it holds a catalog and no Report.
    """
    assert _sites(lambda node: _calls(node, "row_controls")) == [
        ("src/analysis_service/claim_identity.py", "report_rows"),
        ("src/analysis_service/fixes.py", "absent_rows"),
    ]


def test_a_failed_case_has_one_reader():
    """Only ``_failed_cases`` reads the keys that mark a case as failed."""
    keys = {"run_failure", "grounds_failure"}

    def reads(node: ast.AST) -> bool:
        match node:
            case ast.Compare(
                left=ast.Constant(value=key), ops=[ast.In() | ast.NotIn()]
            ):
                return key in keys
            case ast.Subscript(slice=ast.Constant(value=key), ctx=ast.Load()):
                return key in keys
            case ast.Call(
                func=ast.Attribute(attr="get"), args=[ast.Constant(value=key), *_]
            ):
                return key in keys
        return False

    assert _sites(reads) == [("evals/harness/artifact.py", "_failed_cases")]


def test_a_zone_has_one_reader():
    """Only ``is_zone`` asks whether an element ID names a Trust Boundary."""

    def names_the_prefix(node: ast.AST) -> bool:
        return _is_attribute(node, "id_prefix", "TrustBoundary") or (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith("boundary:")
        )

    def tests_a_zone(node: ast.AST) -> bool:
        if _calls(node, "startswith"):
            return any(_held(argument, names_the_prefix) for argument in node.args)
        return isinstance(node, ast.Compare) and any(
            names_the_prefix(side) for side in (node.left, *node.comparators)
        )

    assert _sites(tests_a_zone) == [("src/analysis_service/system_model.py", "is_zone")]


def test_kept_text_has_one_reader():
    """Only ``keeps_text`` asks whether text keeps a character under ``normalize``."""
    tests = _tests_truth_of(lambda node: _calls(node, "normalize"))
    assert _sites(tests) == [("src/analysis_service/grounding.py", "keeps_text")]


#: Each site that splits a text on ``"\n"`` alone, with the reason. A line
#: split of an uploaded text calls
#: :func:`~analysis_service.owner_files.text_lines`.
NEWLINE_SPLITS: dict[Site, str] = {
    (".github/scripts/five_class_read.py", "rendered"): (
        "CommonMark ends a line only at \\n, \\r\\n or \\r, and the body's"
        " \\r\\n and \\r become \\n first"
    ),
    ("src/analysis_service/config_files.py", "read_settings"): (
        "a \\r or a Unicode line separator stays in a settings line, where the"
        " key pattern or the value rule refuses it"
    ),
    ("src/analysis_service/graph.py", "unfence"): (
        "the inverse of the \\n join of render_fenced"
    ),
    ("src/analysis_service/sources.py", "diagram_header"): (
        "diagram_reader/read.mjs splits a Mermaid text on the same string, and"
        " tests/test_sources.py runs both on one set of headers"
    ),
}


def _splits_on_newline(node: ast.AST) -> bool:
    if not (_calls(node, "split") or _calls(node, "rsplit")):
        return False
    separators = node.args[:1] + [
        keyword.value for keyword in node.keywords if keyword.arg == "sep"
    ]
    return any(
        isinstance(sep, ast.Constant) and sep.value == "\n" for sep in separators
    )


def test_a_text_has_one_line_split():
    r"""A split on ``"\n"`` misses ``"\r"``, U+2028 and U+2029, which an
    uploaded text may hold (``docs/agents/value-shapes.md``)."""
    assert _sites(_splits_on_newline) == sorted(NEWLINE_SPLITS)


#: The fields of :class:`~analysis_service.claims.UnknownRef` that spell a
#: kind of fact.
SPELLING_FIELDS = frozenset(
    {"element_id", "attribute", "subject", "assertion", "question", "capability"}
)

#: Each function that tests two spelling fields in one test, and why the test
#: does not decide a kind.
FIELD_PAIR_READERS: dict[Site, str] = {
    ("src/analysis_service/critic.py", "_unresolved_unknown_ref_issues"): (
        "it runs after the one-spelling check and the ``kind`` route, and asks"
        " whether the entry states any text at all"
    ),
}


def _spelling_field(operand: ast.expr) -> str | None:
    """The spelling field ``operand`` tests, through ``not`` and ``strip``."""
    match operand:
        case ast.UnaryOp(op=ast.Not(), operand=inner):
            return _spelling_field(inner)
        case ast.Call(func=ast.Attribute(attr="strip", value=inner), args=[]):
            return _spelling_field(inner)
        case ast.Attribute(attr=attr) if attr in SPELLING_FIELDS:
            return attr
    return None


def _tests_two_spelling_fields(node: ast.AST) -> bool:
    fields = {_spelling_field(operand) for operand in _tested(node)} - {None}
    return len(fields) > 1


def _tests_a_spelling(node: ast.AST) -> bool:
    return _tests_truth_of(
        lambda operand: (
            _is_attribute(operand, "capability") or _is_attribute(operand, "question")
        )
    )(node)


def test_the_kind_of_an_open_fact_has_one_reader():
    """Only ``UnknownRef.spellings`` tests a spelling field for its truth, and
    ``UnknownRef.kind`` routes on what it returns."""
    assert _sites(_tests_a_spelling) == [
        ("src/analysis_service/claims.py", "spellings")
    ]


def test_the_kind_of_an_open_fact_is_not_read_from_two_fields():
    """No test of two spelling fields decides a kind beside ``UnknownRef.kind``.

    The fields ``element_id``, ``attribute``, ``subject`` and ``assertion``
    have namesakes on other models, so a test of one of them alone is not a
    second reader. A test of two of them at once is how a kind is decided, as
    in ``names_an_element``, which asks ``kind``.
    """
    readers = set(_sites(_tests_two_spelling_fields)) - FIELD_PAIR_READERS.keys()
    assert readers == set()


#: The reason of each validator that reads the parts of a key a caller sent.
_SENT_KEY = (
    "it reads a key before validation, where a part need not hash, so the"
    " cached ``key_ref`` cannot take it"
)

#: Each function beside ``key_ref`` that reads which part of an open fact's
#: key is which, and why. Every other site asks ``key_ref``.
SENT_KEY_READERS: dict[Site, str] = {
    ("src/analysis_service/fact_answers.py", "_facet_value"): _SENT_KEY,
    ("src/analysis_service/fact_answers.py", "_one_spelling"): _SENT_KEY,
}


def _is_a_key(node: ast.AST) -> bool:
    """``x.key`` or ``x["key"]``: an open fact's key on a model or a served row."""
    match node:
        case ast.Attribute(attr="key") | ast.Subscript(slice=ast.Constant(value="key")):
            return True
    return False


def _reads_a_key_by_position(node: ast.AST) -> bool:
    match node:
        case ast.Subscript(value=value, slice=ast.Slice() | ast.Constant(value=int())):
            return _is_a_key(value)
        case ast.Assign(targets=[ast.Tuple(elts=parts) | ast.List(elts=parts)]):
            six = len(parts) == len(UNKNOWN_KEY_FIELDS) or any(
                isinstance(part, ast.Starred) for part in parts
            )
            return _is_a_key(node.value) or (_is_name(node.value, "key") and six)
        case ast.Call(func=ast.Name(id="zip"), args=args):
            return any(_is_name(arg, "UNKNOWN_KEY_FIELDS") for arg in args)
    return False


def test_the_layout_of_an_open_facts_key_has_one_reader():
    """Only ``key_ref`` says which part of a key is which; a caller reads a
    part by name.

    The lint refuses an index or a slice of a key, an unpack of a key, and a
    ``zip`` with :data:`~analysis_service.claims.UNKNOWN_KEY_FIELDS`. A local
    named ``key`` counts where it is unpacked into six names, because other
    keys share the name. The test modules stay out: a test reads a part of a
    key to state what it expects of a fixture, and no running code follows it.
    """
    sites = set(_sites(_reads_a_key_by_position))
    assert SENT_KEY_READERS.keys() <= sites
    assert sites - SENT_KEY_READERS.keys() == {
        ("src/analysis_service/fact_answers.py", "key_ref")
    }, "read a key's parts by key_ref"


def test_the_place_of_a_pydantic_error_has_one_reader():
    """Only ``error_place`` joins the ``loc`` of a pydantic error into a place."""
    readers = set(_sites(_reads_loc)) - LOC_READERS.keys()
    assert readers == {("src/analysis_service/errors.py", "error_place")}


#: The reason of each site that hands a model's own key back to that model.
_MODEL_KEY = (
    "a model wrote the key, and the place names it so that the model can drop"
    " it; no caller input reaches the message"
)

#: The reason of each site that names a key in a file its own reader wrote.
_OWN_FILE = (
    "the reader wrote the file, and the name of a key it invented is the"
    " repair; no route returns the message"
)

#: Each function that reads the ``loc`` of a pydantic error itself, and why it
#: does not ask ``error_place``. ``error_place`` drops the key of an
#: ``extra_forbidden`` error, so that a place never names an extra field.
LOC_READERS: dict[Site, str] = {
    ("src/analysis_service/claims.py", "of"): _MODEL_KEY,
    ("src/analysis_service/compact.py", "expand"): _MODEL_KEY,
    ("src/analysis_service/provider.py", "schema_errors"): _MODEL_KEY,
    ("src/analysis_service/validation.py", "parse_and_validate"): _MODEL_KEY,
    ("src/analysis_service/config_files.py", "validation_problems"): _OWN_FILE,
    ("evals/harness/reference.py", "_case_refusal"): _OWN_FILE,
    ("src/analysis_service/fact_writes.py", "_check_attribute_values"): (
        "it names the attribute an answer filled, the last part of a place"
        " that the model's own fields make"
    ),
    ("src/analysis_service/sql_store.py", "_unparsed"): (
        "the service wrote the stored row, and the log names its key so that"
        " an operator finds it"
    ),
    ("evals/harness/grounds.py", "_is_ground_mis_shape"): (
        "it asks whether the error sits on one grounds entry, by index, and"
        " makes no place"
    ),
    ("src/analysis_service/answer_bodies.py", "_question_of"): (
        "it finds the fact answer the error sits in, by index, and makes no place"
    ),
    ("src/analysis_service/answer_bodies.py", "over_a_bound"): (
        "it asks whether the error sits on a list of the body itself, and"
        " makes no place"
    ),
    ("webapp/sitting_base.py", "_mark_by_position"): (
        "it names a mark by its position where the place holds the mark's key,"
        " because the key is caller text; ``error_place`` then makes the place"
    ),
    ("src/analysis_service/api.py", "_body_refused"): (
        "it puts the reader's places under ``body``, where every other"
        " refusal of a request body sits; ``error_place`` then makes the place"
    ),
}


def _reads_loc(node: ast.AST) -> bool:
    return isinstance(node, ast.Subscript) and (
        isinstance(node.slice, ast.Constant) and node.slice.value == "loc"
    )


#: Each function that renders the entries of ``shown_errors`` itself, and why
#: it does not ask ``refusal_line``.
SHOWN_ERROR_READERS: dict[Site, str] = {
    ("src/analysis_service/api.py", "_invalid_body"): (
        "it returns each place and rule as one object of a problem response,"
        " and makes no line"
    ),
    ("evals/harness/envelope.py", "read"): (
        "it writes one line for each error with the repr of the place, because"
        " the place quotes a key of a file"
    ),
}


def test_the_line_of_a_refusal_has_one_maker():
    """Only ``refusal_line`` joins the entries of ``shown_errors`` into a line."""
    readers = set(_sites(lambda node: _calls(node, "shown_errors")))
    assert readers - SHOWN_ERROR_READERS.keys() == {
        ("src/analysis_service/errors.py", "refusal_line")
    }, "make the line of a refusal with refusal_line"


def test_an_archived_report_has_one_reader():
    """Only ``read_report`` parses a report the harness reads from a file.

    A bare parse stops a command with a pydantic traceback on an archive that
    today's :class:`~analysis_service.report.Report` refuses. ``read_report``
    refuses it in one line that names the file and the field.
    """
    readers = set(_sites(_parses_a_report)) - REPORT_PARSERS.keys()
    assert readers == {("evals/harness/bundle.py", "read_report")}


#: Each function that parses a :class:`~analysis_service.report.Report` and
#: does not ask ``read_report``, and why.
REPORT_PARSERS: dict[Site, str] = {
    ("src/analysis_service/sql_store.py", "report"): (
        "the job store reads a report from a job row, and refuses one that"
        " does not parse as UnreadableSchema (ADR 0095)"
    ),
}


def _parses_a_report(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr.startswith("model_validate")
        and _is_name(node.func.value, "Report")
    )


def test_each_declared_site_still_reads_the_rule_itself():
    assert FIELD_PAIR_READERS.keys() <= set(_sites(_tests_two_spelling_fields))
    assert LOC_READERS.keys() <= set(_sites(_reads_loc))
    assert SHOWN_ERROR_READERS.keys() <= set(
        _sites(lambda node: _calls(node, "shown_errors"))
    )
    assert CONFIG_DEFAULT_READERS.keys() <= set(_sites(_reads_a_config_default))
    assert ID_DERIVERS.keys() <= set(_sites(_derives_an_id))
    assert STORED_ANSWER_READERS.keys() <= set(_sites(_validates_an_answer))
    assert REPORT_PARSERS.keys() <= set(_sites(_parses_a_report))


def _states_a_direction(node: ast.AST) -> bool:
    """A required ``direction`` field whose ``Literal`` holds ``gap``."""
    return (
        isinstance(node, ast.AnnAssign)
        and _is_name(node.target, "direction")
        and node.value is None
        and any(_is_constant(part, "gap") for part in ast.walk(node.annotation))
    )


def _classes_holding(match: Callable[[ast.AST], bool]) -> list[Site]:
    """Each class in :data:`FILES` whose own body holds a statement ``match``
    accepts."""
    return sorted(
        (path.relative_to(REPO_ROOT).as_posix(), node.name)
        for path in FILES
        for node in ast.walk(parse(path))
        if isinstance(node, ast.ClassDef) and any(map(match, node.body))
    )


def test_a_drafts_direction_has_one_field():
    """Only ``RequirementProposal`` requires a draft's direction. The claim
    that the service resolves from it carries a copy with a default, for a
    report written before the field, so each direction that code reads comes
    from this one field."""
    assert _classes_holding(_states_a_direction) == [
        ("src/analysis_service/frameworks/asvs/record.py", "RequirementProposal")
    ]


def test_the_cell_has_one_reader():
    """Only ``Cell.read`` reads the cell for a route that needs a ``Current``.

    The Settings page serves while the cell is empty, and it reads the cell
    only to choose whether to show the startup error.
    """

    def reads_the_cell(node: ast.AST) -> bool:
        return _is_attribute(node, "current", "cell", "self") and isinstance(
            node.ctx, ast.Load
        )

    sites = _sites(reads_the_cell)
    assert [site for site in sites if site[0].startswith("webapp/")] == [
        ("webapp/main.py", "read"),
        ("webapp/main.py", "settings_page"),
    ]


def test_settings_env_has_one_writer():
    """Only the save operation writes ``settings.env``.

    The replace of the file is the one step that commits a save (ADR 0093),
    so a second caller would commit a save that skips the checks before it.
    """
    sites = _sites(lambda node: _calls(node, "write_settings_env"))
    assert sites == [("webapp/settings_save.py", "_commit")]


def test_an_evidence_file_by_id_has_one_reader():
    """Only ``ControlDocument.file`` finds an evidence file by its ID.

    A second lookup can answer for a different list, so a route and a store
    would disagree on whether a Control holds a file.
    """

    def compares(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Compare)
            and any(
                _is_attribute(side, "id") for side in (node.left, *node.comparators)
            )
            and any(
                _is_name(side, "evidence_id") for side in (node.left, *node.comparators)
            )
        )

    assert _sites(compares) == [("src/analysis_service/controls.py", "file")]


def test_a_served_report_has_one_reader():
    """Only ``served`` asks the app whether a job's report may be served.

    The event stream asks ``servable`` itself, because its frame names why a
    report is withheld, which ``served`` does not give.
    """
    sites = _sites(lambda node: _calls(node, "servable"))
    assert [site for site in sites if site[0].startswith("webapp/")] == [
        ("webapp/main.py", "_stream"),
        ("webapp/main.py", "served"),
    ]


def test_a_known_mark_has_one_reader():
    """Only ``unknown_marks`` gathers the keys that name a recorded finding."""

    def gathers_known_keys(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.SetComp)
            and any(
                _is_attribute(generator.iter, "mark_targets")
                for generator in node.generators
            )
            and _held(node, lambda inner: _is_attribute(inner, "fingerprint"))
        )

    assert _sites(gathers_known_keys) == [("evals/harness/sitting.py", "unknown_marks")]


def _config_defaults() -> frozenset[str]:
    """The name of each repo default that ``CONFIG_PATHS`` pairs with a variable."""
    tree = parse(REPO_ROOT / "src/analysis_service/deployment.py")
    [table] = (
        node.value
        for node in tree.body
        if isinstance(node, ast.AnnAssign) and _is_name(node.target, "CONFIG_PATHS")
    )
    assert isinstance(table, ast.Dict)
    return frozenset(
        row.elts[1].id
        for row in table.values
        if isinstance(row, ast.Tuple) and isinstance(row.elts[1], ast.Name)
    )


#: Each function that reads a repo default of ``CONFIG_PATHS`` without
#: ``config_path``, and why.
CONFIG_DEFAULT_READERS: dict[Site, str] = {
    ("src/analysis_service/deployment.py", MODULE_OWNER): (
        "``CONFIG_PATHS`` pairs each default with its variable here"
    ),
    ("evals/harness/exemplar_verbs.py", "lane_verbs"): (
        "it reads the shipped packages, which the corpus describes, and never"
        " a deployment's"
    ),
}


def _reads_a_config_default(node: ast.AST) -> bool:
    """Whether ``node`` names a repo default, or unpacks one from ``CONFIG_PATHS``."""
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        return node.id in _config_defaults()
    match node:
        case (
            ast.comprehension(target=target, iter=source)
            | ast.For(target=target, iter=source)
        ):
            pass
        case ast.Assign(targets=[target], value=source):
            pass
        case _:
            return False
    pairs = [
        inner
        for inner in ast.walk(target)
        if isinstance(inner, ast.Tuple) and len(inner.elts) == 2
    ]
    unpacks = any(not _is_name(pair.elts[1], "_") for pair in pairs)
    return unpacks and _held(source, lambda inner: _is_name(inner, "CONFIG_PATHS"))


def test_a_config_path_has_one_reader():
    """Each function that reads a default of ``CONFIG_PATHS`` asks ``config_path``."""
    readers = set(_sites(_reads_a_config_default)) - CONFIG_DEFAULT_READERS.keys()
    assert readers == {
        ("src/analysis_service/deployment.py", "from_env"),
        ("webapp/settings_view.py", "lookup"),
    }
    callers = set(_sites(lambda node: _calls(node, "config_path")))
    assert readers <= callers


def test_each_refusal_has_one_status_row():
    """No refusal class is a key in two status tables.

    ``JOB_REFUSAL_STATUS`` holds the rows that both drivers share, and each
    driver adds only its own rows.
    """
    rows: dict[str, list[Site]] = {}
    for path in FILES:
        for node, function in _walk(parse(path), MODULE_OWNER):
            if not isinstance(node, ast.Dict):
                continue
            pairs = list(zip(node.keys, node.values, strict=True))
            statuses = pairs and all(
                isinstance(key, ast.Name | ast.Attribute)
                and isinstance(value, ast.Constant)
                and isinstance(value.value, int)
                and 400 <= value.value < 600
                for key, value in pairs
            )
            if statuses:
                site = (path.relative_to(REPO_ROOT).as_posix(), function)
                for key, _ in pairs:
                    rows.setdefault(ast.unparse(key), []).append(site)
    assert rows
    assert {key: sites for key, sites in rows.items() if len(sites) > 1} == {}


def test_a_slug_collision_has_one_reader():
    """Only ``_by_slug_key`` gathers element IDs under a singularised slug."""

    def groups_by_slug(node: ast.AST) -> bool:
        return isinstance(node, ast.Subscript) and _calls(node.slice, "slug_key")

    assert _sites(groups_by_slug) == [("evals/harness/alignment.py", "_by_slug_key")]


#: Each function that builds an element ID from a type and a name without
#: ``derive_element_id``, and why.
ID_DERIVERS: dict[Site, str] = {
    ("evals/harness/alignment.py", "_align_nodes"): (
        "an alias is a name and the type of the element it names, not an"
        " element, and the rule asks for the ID that name derives"
    ),
    ("evals/harness/falsify.py", "_relabelled"): (
        "it builds a flow ID at the identity version of the ID it relabels,"
        " which ``derive_element_id`` does not take"
    ),
    ("src/analysis_service/carried_renames.py", "_renamed_id"): (
        "it builds a carried flow ID again with a renamed endpoint, at the"
        " identity version of the ID it rebuilds, which ``derive_element_id``"
        " does not take, and it holds no element"
    ),
    ("src/analysis_service/assertions.py", "subject_id"): (
        "a non-graph subject is no element, and its ID takes the derivation an"
        " element ID has from its type prefix and its name"
    ),
    ("src/analysis_service/controls.py", "_scope_id"): (
        "a Control's scope holds a name and aliases of an element type, not an"
        " element, and a match asks for the ID each name derives (ADR 0124)"
    ),
    ("evals/verify_corpus.py", "_check_aliases"): (
        "an alias is a name and the type of the element it names, not an"
        " element, and the corpus check asks for the ID that name derives, a"
        " flow's with the endpoints of the flow it names"
    ),
}


def _derives_an_id(node: ast.AST) -> bool:
    return _calls(node, "make_element_id") or _calls(node, "make_flow_id")


def test_an_element_id_has_one_deriver():
    """Only ``derive_element_id`` derives the ID an element's name implies."""
    derivers = set(_sites(_derives_an_id)) - ID_DERIVERS.keys()
    assert derivers == {("src/analysis_service/system_model.py", "derive_element_id")}


def _slugs_after_a_prefix(node: ast.AST) -> bool:
    """Whether ``node`` is an f-string that puts ``normalize_name`` after a colon."""
    return isinstance(node, ast.JoinedStr) and any(
        isinstance(before, ast.Constant)
        and str(before.value).endswith(":")
        and isinstance(after, ast.FormattedValue)
        and _calls(after.value, "normalize_name")
        for before, after in pairwise(node.values)
    )


#: Each function that writes a prefix, a colon and a name's slug for a value
#: that is no ID the service derives, and why.
NOT_AN_ID_SLUG: dict[Site, str] = {
    ("evals/bench/deterministic.py", "as_compact"): (
        "it writes a compact-transport ref the way a model writes one, a"
        " ``REF_TAGS`` tag before the name's slug, and the expansion derives"
        " every ID from the name and never from the ref"
    ),
}


def test_an_id_slug_is_spelled_only_in_the_system_model():
    """Only ``system_model`` writes a prefix, a colon and a name's slug.

    ``make_element_id`` writes an element ID and ``_build_v1`` a version 1 flow
    ID. Every other ID of that shape calls one of them, so the two spellings
    cannot part.
    """
    sites = _sites(_slugs_after_a_prefix)
    assert NOT_AN_ID_SLUG.keys() <= set(sites)
    assert [site for site in sites if site not in NOT_AN_ID_SLUG] == [
        ("src/analysis_service/system_model.py", "_build_v1"),
        ("src/analysis_service/system_model.py", "make_element_id"),
    ]


#: Each function that reads a password field, or tests a key for the word
#: ``password``, for another rule than whether a URL holds a password.
NOT_THE_PASSWORD_RULE: dict[Site, str] = {
    ("src/analysis_service/s3_objects.py", "_endpoint"): (
        "the object endpoint refuses any user part, a password among them,"
        " and a path, a query or a fragment"
    ),
    ("src/analysis_service/sql_store.py", "_postgres"): (
        "it calls the password row of the database credentials, which reads"
        " the password file"
    ),
}


def _reads_a_password(node: ast.AST) -> bool:
    return _is_attribute(node, "password") or (
        isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Constant)
        and node.left.value == "password"
    )


def test_a_url_password_has_one_reader():
    """Only ``holds_password`` decides whether a URL holds a password, so the
    Settings page never shows a URL that the startup refuses as one."""
    readers = set(_sites(_reads_a_password)) - NOT_THE_PASSWORD_RULE.keys()
    assert readers == {("src/analysis_service/store_settings.py", "holds_password")}


def test_each_declared_site_still_reads_a_password():
    assert NOT_THE_PASSWORD_RULE.keys() <= set(_sites(_reads_a_password))


def _words_a_connection(node: ast.AST) -> bool:
    """Whether ``node`` spells a flow as a connection between two elements."""
    return isinstance(node, ast.Constant) and any(
        words in str(node.value)
        for words in (
            "connection from",
            "connection initiated by",
            "connection between",
        )
    )


def test_a_flow_as_a_place_has_one_wording():
    """Only ``flow_words`` says how a **Data Flow** reads as a place."""
    assert _sites(_words_a_connection) == [
        ("src/analysis_service/open_facts.py", "flow_words")
    ]


def _words_a_resting_reason(node: ast.AST) -> bool:
    """Whether ``node`` spells the reason code writes for a claim on open facts."""
    return isinstance(node, ast.Constant) and "The claim rests on" in str(node.value)


def test_the_reason_a_claim_rests_on_has_one_writer():
    """Only ``rests_on`` writes the sentence, so every copy keeps its bound."""
    assert _sites(_words_a_resting_reason) == [
        ("src/analysis_service/claims.py", "rests_on")
    ]


#: The names a comparison of an attacker position uses: the parameters of
#: ``scorer.position_fate``, the reader ``losses._holding`` and
#: ``losses._refused`` take, and the loss report's two position readers.
_POSITION_NAMES = frozenset(
    {
        "ruled_position",
        "stated_position",
        "position_of",
        "_draft_position",
        "_proposal_position",
    }
)


def _names_a_position(side: ast.AST) -> bool:
    """Whether ``side`` is a draft's position, a ruled one, or a read of either."""
    if isinstance(side, ast.Attribute):
        return side.attr in {
            "attacker_position",
            "ruled_positions",
        } or _names_a_position(side.value)
    if isinstance(side, ast.Name):
        return side.id in _POSITION_NAMES
    if isinstance(side, ast.Call):
        return _names_a_position(side.func) or any(
            isinstance(arg, ast.Constant) and arg.value == "attacker_position"
            for arg in side.args
        )
    if isinstance(side, ast.Subscript):
        return _names_a_position(side.value)
    return False


def _compares_a_position(node: ast.AST) -> bool:
    """Whether ``node`` compares an attacker position: a draft's
    ``attacker_position``, a value read from ``ruled_positions``, a call to a
    position reader, or a parameter of ``position_fate``. A local name that
    holds a position, or a position reader, under another name is not seen."""
    return isinstance(node, ast.Compare) and any(
        _names_a_position(side) for side in (node.left, *node.comparators)
    )


def _spells_an_assertion_id(node: ast.AST) -> bool:
    """Whether ``node`` writes or reads the ``assertion:`` prefix of an ID."""
    return isinstance(node, ast.Constant) and str(node.value).startswith("assertion:")


def test_an_assertion_id_has_one_encoder_and_one_decoder():
    """Only ``assertion_id`` spells an ID and only ``read_assertion_id`` reads one."""
    assert _sites(_spells_an_assertion_id) == [
        ("src/analysis_service/assertions.py", "assertion_id"),
        ("src/analysis_service/assertions.py", "read_assertion_id"),
    ]


def _declares_a_max_length(node: ast.AST, *names: str) -> TypeGuard[ast.Call]:
    """Whether ``node`` is a ``Field`` call whose ``max_length`` is one of
    ``names``, or any name where ``names`` is empty."""
    return _calls(node, "Field") and any(
        keyword.arg == "max_length"
        and isinstance(keyword.value, ast.Name)
        and (not names or keyword.value.id in names)
        for keyword in node.keywords
    )


#: The modules that declare the /v1 body models: a job's submission, and the
#: answer bodies both drivers read through ``answer_bodies``.
_V1_MODELS = ("src/analysis_service/api.py", "src/analysis_service/answer_bodies.py")

#: Each name a /v1 model declares as a list's ``max_length``: the bounds the
#: web app reads through those models.
_V1_BOUNDS = frozenset(
    keyword.value.id
    for module in _V1_MODELS
    for node in ast.walk(parse(REPO_ROOT / module))
    if _declares_a_max_length(node)
    for keyword in node.keywords
    if keyword.arg == "max_length" and isinstance(keyword.value, ast.Name)
)


def _reads_a_v1_bound(node: ast.AST) -> bool:
    """Whether ``node`` compares a length to a /v1 bound, or declares the bound
    as a ``max_length`` of its own."""
    if isinstance(node, ast.Compare):
        return any(
            _is_name(operand, *_V1_BOUNDS) for operand in (node.left, *node.comparators)
        )
    return _declares_a_max_length(node, *_V1_BOUNDS)


def test_the_answer_bounds_are_read_through_the_v1_models():
    """No route compares a list's length to a /v1 bound itself, and none
    declares a bound of its own. The /v1 models declare each bound as
    ``max_length``, and ``answer_bodies`` reads each answer body through
    them, so the web app and /v1 refuse the same bodies."""
    assert {"MAX_SKIPS", "MAX_HELD_FACTS"} <= _V1_BOUNDS
    assert [
        site for site in _sites(_reads_a_v1_bound) if site[0].startswith("webapp/")
    ] == []


#: The models an answer body is read into, and the two answers inside one.
_ANSWER_MODELS = (
    "AnswersSubmission",
    "AmendmentSubmission",
    "CorrectionsSubmission",
    "LinkAnswer",
    "FactAnswer",
)


def _validates_an_answer(node: ast.AST) -> bool:
    return (
        _calls(node, "model_validate")
        and isinstance(node.func, ast.Attribute)
        and _is_name(node.func.value, *_ANSWER_MODELS)
    )


#: Each site that validates an answer the service itself wrote, and where that
#: answer comes from. No request body reaches one.
STORED_ANSWER_READERS: dict[Site, str] = {
    ("src/analysis_service/fact_answers.py", "merged_facts"): (
        "builds one facet answer from two answers admission already read"
    ),
    ("src/analysis_service/graph.py", "_resolve_assertions"): (
        "reads the answers the driver seeded in the graph state, which admission wrote"
    ),
    ("evals/harness/withheld.py", "load_answer_file"): "reads a corpus answer file",
    ("evals/harness/withheld.py", "answers_within_saves"): (
        "builds the answers a replayed save gives"
    ),
    ("evals/harness/early_rounds.py", "answer"): (
        "builds the answer an offline answerer gives"
    ),
    ("evals/harness/early_rounds.py", "dont_know"): (
        "builds the answer an offline answerer gives"
    ),
}


def test_an_answer_body_has_one_reader():
    """Only ``answer_bodies`` reads an answer out of a request body. The
    ``/v1`` routes and the first-run app both call it, so one site applies
    the keys, shapes and bounds a body meets."""
    assert set(_sites(_validates_an_answer)) == STORED_ANSWER_READERS.keys()
    assert ("src/analysis_service/answer_bodies.py", "_read") in _sites(
        partial(_calls, name="model_validate")
    )


def _reads_a_body_key(node: ast.AST) -> bool:
    """Whether ``node`` reads the ``skip`` or ``presented`` key of a body by
    hand, or builds a type adapter over a body field's type."""
    if isinstance(node, ast.Subscript):
        return isinstance(node.slice, ast.Constant) and node.slice.value in (
            "skip",
            "presented",
        )
    if _calls(node, "get"):
        return (
            bool(node.args)
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value in ("skip", "presented")
        )
    return _calls(node, "TypeAdapter") and _held(
        node, lambda inner: _is_name(inner, "SkipKey", "Revision")
    )


def test_no_site_reads_an_answer_body_field_by_hand():
    """The reader's models read each field; no route reads one off the body."""
    assert _sites(_reads_a_body_key) == []


def _spells_a_sampling_key(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.JoinedStr)
        and bool(node.values)
        and isinstance(node.values[0], ast.Constant)
        and str(node.values[0].value).startswith("sampling.")
    )


def test_a_sampling_setting_key_is_spelled_once():
    """Only ``sampling_key`` spells ``sampling.<tier>.<param>``; the page, the
    view and the startup refusal call it."""
    assert _sites(_spells_a_sampling_key) == [
        ("webapp/settings_page.py", "sampling_key")
    ]


def _spells_a_tier_key(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.JoinedStr)
        and bool(node.values)
        and isinstance(node.values[0], ast.Constant)
        and str(node.values[0].value).startswith("tiers.")
    )


def test_a_tier_setting_key_is_spelled_once():
    """Only ``tier_setting_key`` spells the ``tiers.<tier>.<field>`` key of a
    Deployment Setting; the page and the view call it. The loaders under ``src``
    spell the same text as the TOML table path an error names, which is not this
    key."""
    assert [
        site for site in _sites(_spells_a_tier_key) if site[0].startswith("webapp/")
    ] == [("webapp/settings_page.py", "tier_setting_key")]


def _mints_a_job(node: ast.AST) -> bool:
    return (
        _calls(node, "create")
        and isinstance(node.func, ast.Attribute)
        and _is_name(node.func.value, "JobRecord")
    )


def _starts_a_job(node: ast.AST) -> bool:
    return _calls(node, "admit") and any(kw.arg == "start" for kw in node.keywords)


def test_a_new_job_is_created_and_admitted_in_job_answers():
    """Only ``JobAnswers`` creates a job record and admits it with a ``start``.

    A submission, a Resumed Job and an amended job are new jobs, and the three
    operations that make them are the one admission's (ADR 0077). The engine
    builds a record for an in-process run, which no store admits.
    """
    assert _sites(_mints_a_job) == [
        ("src/analysis_service/engine.py", "_build_job"),
        ("src/analysis_service/job_answers.py", "amend"),
        ("src/analysis_service/job_answers.py", "answer"),
        ("src/analysis_service/job_answers.py", "submit"),
    ]
    assert _sites(_starts_a_job) == [
        ("src/analysis_service/job_answers.py", "amend"),
        ("src/analysis_service/job_answers.py", "answer"),
        ("src/analysis_service/job_answers.py", "submit"),
    ]


#: Each field name that holds one shared bound, and the constant that states
#: the bound. A field of one of these names reads its constant, so no model
#: admits a value that a sibling model refuses.
_SHARED_BOUNDS: dict[str, str] = {
    "source_label": "MAX_LABEL_CHARS",
    "source_excerpt": "EXCERPT_MAX_CHARS",
    "excerpt": "EXCERPT_MAX_CHARS",
    "quote": "EXCERPT_MAX_CHARS",
    "element_id": "ELEMENT_ID_MAX_CHARS",
    "element": "ELEMENT_ID_MAX_CHARS",
    "flow_id": "ELEMENT_ID_MAX_CHARS",
    **dict.fromkeys(CONTROL_ATTRIBUTES, "CONTROL_VALUE_MAX_CHARS"),
}

#: Each field of a :data:`_SHARED_BOUNDS` name that holds another bound, and
#: why.
OTHER_BOUND: dict[tuple[str, str], str] = {
    ("src/analysis_service/claims.py", "element_id"): (
        "an unresolved element mark keeps the reference as the model wrote it,"
        " which names no Element ID; the producer cuts it at"
        " ELEMENT_REF_MAX_CHARS"
    ),
}


def _bound_sites() -> Iterator[tuple[str, str, str | None]]:
    """Each ``Field`` with a ``max_length`` that a :data:`_SHARED_BOUNDS`
    field name or an ``ELEMENT_ID`` pattern asks for: the file, the field and
    the name of the bound it reads, or ``None`` for a literal."""
    for path in FILES:
        for node in ast.walk(parse(path)):
            if not (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.value is not None
                and _calls(node.value, "Field")
            ):
                continue
            keywords = {keyword.arg: keyword.value for keyword in node.value.keywords}
            bound = keywords.get("max_length")
            asked = node.target.id in _SHARED_BOUNDS or _is_name(
                keywords.get("pattern", ast.Constant(None)), "ELEMENT_ID"
            )
            if asked and bound is not None:
                name = bound.id if isinstance(bound, ast.Name) else None
                yield path.relative_to(REPO_ROOT).as_posix(), node.target.id, name


def _expected_bound(field: str) -> str:
    return _SHARED_BOUNDS.get(field, "ELEMENT_ID_MAX_CHARS")


def test_a_shared_bound_is_read_by_its_name():
    """A source label, an excerpt, an Element ID and a control value each have
    one bound, and every field that holds one reads the constant."""
    assert [
        site
        for site in _bound_sites()
        if site[2] != _expected_bound(site[1]) and site[:2] not in OTHER_BOUND
    ] == []


def test_each_declared_field_still_holds_another_bound():
    held = {site[:2] for site in _bound_sites() if site[2] != _expected_bound(site[1])}
    assert OTHER_BOUND.keys() <= held


def _spells_a_digest_shape(node: ast.AST) -> bool:
    """Whether ``node`` is a pattern string that spells a SHA-256 hex digest.
    A string with a space in it is prose that names the shape."""
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "[0-9a-f]{64}" in node.value
        and " " not in node.value
    )


def test_the_shape_of_a_digest_is_spelled_once():
    """Only ``parsing.Sha256Hex`` spells the shape, and each model field that
    holds a digest reads it."""
    assert _sites(_spells_a_digest_shape) == [
        ("src/analysis_service/parsing.py", MODULE_OWNER)
    ]


def _digests_a_source_text(node: ast.AST) -> bool:
    """Whether ``node`` hashes the ``text`` of a value, as a Source's content
    digest does."""
    return _calls(node, "sha256") and any(
        _is_attribute(child, "text") for arg in node.args for child in ast.walk(arg)
    )


def test_a_source_digest_has_one_writer():
    """Only ``sources.text_digest`` hashes a Source's text, so a report's
    source ref and a support span pin one text to one digest."""
    assert _sites(_digests_a_source_text) == []


def _spells_a_lane_doc(node: ast.AST) -> bool:
    """Whether ``node`` spells the name or the file of a lane's skill or its
    exemplars."""
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and " " not in node.value
        and (
            node.value in ("skill", "exemplars")
            or node.value.endswith(("/skill", "/exemplars", "skill.md", "exemplars.md"))
        )
    )


#: Each function that spells ``skill`` for another meaning, and the meaning.
NOT_A_LANE_DOC: dict[Site, str] = {
    ("evals/harness/audit.py", MODULE_OWNER): (
        "a finding's owner: the skill text as one of the places a fix belongs"
    ),
}


def test_a_lane_doc_name_is_spelled_once():
    """Only ``frameworks`` spells a lane's two file names; the loader names,
    the package gate and the roster writer read the declarations."""
    assert set(_sites(_spells_a_lane_doc)) - NOT_A_LANE_DOC.keys() == {
        ("src/analysis_service/frameworks/__init__.py", MODULE_OWNER)
    }


def test_each_declared_site_still_spells_skill():
    assert NOT_A_LANE_DOC.keys() <= set(_sites(_spells_a_lane_doc))


def _lists_the_ruling_fields(node: ast.AST) -> bool:
    """Whether ``node`` is a literal collection of the base ruling's fields."""
    return isinstance(node, ast.Tuple | ast.List | ast.Set) and {
        element.value for element in node.elts if isinstance(element, ast.Constant)
    } == set(Ruling.model_fields)


def test_the_base_ruling_fields_are_read_from_the_model():
    """Assembly and its replay read ``Ruling.model_fields``, so a field added
    to the base ruling reaches both."""
    assert _sites(_lists_the_ruling_fields) == []


#: The rate names of a unit price, from the dataclass that declares them.
_RATE_NAMES = frozenset(
    field.name for field in dataclasses.fields(UnitPrices) if field.name != "model"
)


def _spells_a_rate_name(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value in _RATE_NAMES


def test_a_unit_price_rate_name_is_spelled_only_where_it_is_declared():
    """The Baseline shape check reads the rates from ``UnitPrices``, so a rate
    added there is a rate the check asks for."""
    assert {path for path, _ in _sites(_spells_a_rate_name)} == {
        "evals/harness/prices.py"
    }


#: Each name that GLOSSARY.md defines, or that one module owns, and the one
#: module that declares it. A second module that declares the name gives one
#: name two meanings.
_ONE_MEANING: dict[str, str] = {
    "Candidate": "src/analysis_service/candidates.py",
    "Capability": "src/analysis_service/capabilities.py",
    "Disposition": "",
    "Revision": "src/analysis_service/answer_state.py",
    "Report": "src/analysis_service/report.py",
    "Source": "src/analysis_service/sources.py",
    "Standing": "evals/harness/roster.py",
    "Verdict": "src/analysis_service/claims.py",
    "fold": "src/analysis_service/references.py",
}


def _module_names(tree: ast.Module) -> Iterator[str]:
    """Each name a module's own top level declares."""
    for node in tree.body:
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            yield node.name
        elif isinstance(node, ast.Assign):
            yield from (
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            yield node.target.id


def test_a_defined_name_has_one_declaration():
    """A glossary term names one type, so a reader of the name reads one
    meaning. ``Disposition`` names ``reference.AsvsDisposition``, so no module
    declares the bare name."""
    declared: dict[str, set[str]] = {name: set() for name in _ONE_MEANING}
    for path in FILES:
        for name in _module_names(parse(path)):
            if name in declared:
                declared[name].add(path.relative_to(REPO_ROOT).as_posix())
    assert declared == {
        name: {owner} if owner else set() for name, owner in _ONE_MEANING.items()
    }


def _reads_an_object_digest_off_a_row(node: ast.AST) -> bool:
    """Whether ``node`` reads a digest column of :data:`DIGEST_COLUMNS` as an
    attribute of a row, and not as a column of the table."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr in DIGEST_COLUMNS.values()
        and not _is_attribute(node.value, "c", "jobs")
    )


def test_an_object_digest_is_read_through_its_column_table():
    """The object reader and ``_names`` read the digest of an object through
    ``DIGEST_COLUMNS``. A save hands ``check_save`` the report digest it
    compares the stored digest with. Other models hold a field of the same
    name, so the lint reads the store's module."""
    store = [REPO_ROOT / "src/analysis_service/sql_store.py"]
    assert _sites(_reads_an_object_digest_off_a_row, store) == [
        ("src/analysis_service/sql_store.py", "save")
    ]


def _reads_a_secret_file(node: ast.AST) -> bool:
    """Whether ``node`` opens a private file under the rule of a secret file.
    ``read_private`` passes its own ``secret`` on, which is no new read."""
    return (_calls(node, "read_private") or _calls(node, "private_fd")) and any(
        keyword.arg == "secret"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is True
        for keyword in node.keywords
    )


def test_a_secret_file_has_one_reader():
    """Each loader of a key or a password asks ``read_secret`` with its own
    error class, so the mode rule, the newline rule and the secret rule have
    one reader."""
    assert _sites(_reads_a_secret_file) == [
        ("src/analysis_service/config_files.py", "read_secret")
    ]


#: A module path under the service package, and a prompt path: the two halves
#: of a pairing that only ``.github/scripts/prompt_outcomes.toml`` states.
_MODULE_PATH = re.compile(r"^src/analysis_service/")
_PROMPT_PATH = re.compile(r"^(?:prompts|frameworks)/.*\.md$")


def _spells(node: ast.AST, pattern: re.Pattern[str]) -> bool:
    """Whether a string constant under ``node`` matches ``pattern``."""
    return any(
        isinstance(child, ast.Constant)
        and isinstance(child.value, str)
        and bool(pattern.match(child.value))
        for child in ast.walk(node)
    )


def _pairs_a_module_with_a_prompt(node: ast.AST) -> bool:
    """A dict that maps a module path to a prompt path, or a pair of the two."""
    if isinstance(node, ast.Dict):
        return any(
            key is not None
            and _spells(key, _MODULE_PATH)
            and _spells(value, _PROMPT_PATH)
            for key, value in zip(node.keys, node.values, strict=True)
        )
    if isinstance(node, ast.Tuple | ast.List) and len(node.elts) == 2:
        module, prompt = node.elts
        return _spells(module, _MODULE_PATH) and _spells(prompt, _PROMPT_PATH)
    return False


def test_a_module_to_prompt_pairing_is_written_only_in_the_table():
    """The prompt read's map derives from the table, and the outcome tests read
    the same rows, so no Python holds a second copy of a pair."""
    assert _sites(_pairs_a_module_with_a_prompt, (*FILES, *source_files(*TESTS))) == []


def _spells_a_held_phrase(node: ast.AST) -> bool:
    """Whether ``node`` is a string constant that holds a phrase of a row of
    ``.github/scripts/held_wording.toml``."""
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and any(row["phrase"] in node.value for row in HELD_ROWS)
    )


def test_a_held_phrase_is_written_only_in_its_own_file():
    """The wording lints and the prompt read take each held phrase from the
    table, so no Python but the file that holds the wording spells one."""
    own = {row["file"] for row in HELD_ROWS}
    sites = _sites(_spells_a_held_phrase, (*FILES, *source_files(*TESTS)))

    assert [site for site in sites if site[0] not in own] == []


_PATH_PARSE = re.compile(r"location\.pathname\??\.(?:split|match)\(")


def test_a_page_parses_its_own_path_once():
    """``RUN`` in ``report_view.js`` reads the run ID from the page's path.

    Each other site reads ``RUN``, so a route change is one edit.
    """
    sites = [
        (script.relative_to(REPO_ROOT).as_posix(), number)
        for script in source_files(*PYTHON, suffixes=(".js",))
        for number, line in enumerate(
            script.read_text(encoding="utf-8").splitlines(), 1
        )
        if _PATH_PARSE.search(line)
    ]
    assert [name for name, _ in sites] == ["webapp/static/report_view.js"], sites


# The facets a need names: a need that names none of a kind's facets, or a
# finding absent from a question's ``needs``, needs every facet (ADR 0112).

#: The calls whose value is every facet of a question kind, or their count.
_EVERY_FACET = ("answer_facets", "choices_of")


def _falls_back_to_every_facet(node: ast.AST) -> bool:
    """Whether ``node`` is a function with an ``or`` that falls back to every
    facet of a kind: a later operand that is a call holding a call of
    :data:`_EVERY_FACET`, or names a variable the function binds from one. A
    comparison with every facet, as in ``answered_keys``, is not a value."""
    if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        return False

    def every(value: ast.AST) -> bool:
        return any(
            _calls(inner, name) for inner in ast.walk(value) for name in _EVERY_FACET
        )

    bound = {
        target.id
        for inner in ast.walk(node)
        if isinstance(inner, ast.Assign) and every(inner.value)
        for target in inner.targets
        if isinstance(target, ast.Name)
    }
    return any(
        (isinstance(operand, ast.Call) and every(operand))
        or (isinstance(operand, ast.Name) and operand.id in bound)
        for inner in ast.walk(node)
        if isinstance(inner, ast.BoolOp) and isinstance(inner.op, ast.Or)
        for operand in inner.values[1:]
    )


def test_a_need_that_names_no_facet_has_one_reader():
    """Only ``needed_facets`` turns a need that names no facet into every facet.

    ``asked_facets`` and the order's count of a finding's choices once applied
    the rule again, each to a finding absent from a question's ``needs``.
    """
    assert _sites(_falls_back_to_every_facet) == [
        ("src/analysis_service/fact_answers.py", "needed_facets")
    ]


def _reads_a_request_body(node: ast.AST) -> bool:
    """Whether ``node`` reads a request's body or its media type by hand."""
    body_read = isinstance(node, ast.Call) and any(
        _is_attribute(node.func, method, "request")
        for method in ("json", "body", "stream", "form")
    )
    media_type = (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.lower() == "content-type"
    )
    return body_read or media_type


def test_a_request_body_has_one_reader():
    """Only ``_json_body`` reads a write route's body, so each route has the
    size cap and the media-type check."""
    assert _sites(_reads_a_request_body) == [("webapp/main.py", "_json_body")]


def test_a_dropped_claim_is_built_only_by_its_constructor():
    """``DroppedClaim(...)`` past :meth:`~analysis_service.claims.DroppedClaim.of`
    is the second reader of the bounds that ``of`` cuts each agent-written value
    to. A producer that raises on a long value costs the job the report the
    mark exists to preserve."""
    assert _sites(lambda node: _calls(node, "DroppedClaim")) == []


def _applies_the_body_slack(node: ast.AST) -> bool:
    """Whether ``node`` reads ``_BODY_SLACK`` or scales a byte budget. A string
    repeated to the budget's length is a body, not a bound."""
    slack = (
        isinstance(node, ast.Name)
        and node.id == "_BODY_SLACK"
        and isinstance(node.ctx, ast.Load)
    )
    if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult)):
        return slack
    sides = (node.left, node.right)
    budget = any(
        isinstance(side, ast.Attribute) and side.attr == "max_total_bytes"
        for side in sides
    )
    text = any(
        isinstance(side, ast.Constant) and isinstance(side.value, str | bytes)
        for side in sides
    )
    return budget and not text


def test_the_raw_body_guard_has_one_reader():
    """Only ``max_body_bytes`` derives the raw-body guard from the byte
    budget, and only the ``/v1`` app and the web app's job routes ask it, so
    the two drivers refuse one body size."""
    searched = (*FILES, *source_files(*TESTS))

    assert _sites(_applies_the_body_slack, searched) == [
        ("src/analysis_service/api.py", "max_body_bytes")
    ]
    assert _sites(lambda node: _calls(node, "max_body_bytes")) == [
        ("src/analysis_service/api.py", "create_app"),
        ("src/analysis_service/api.py", "max_control_body_bytes"),
        ("webapp/main.py", "_job_body"),
    ]
    assert _sites(lambda node: _calls(node, "max_control_body_bytes")) == [
        ("webapp/main.py", "control_body"),
    ]


# The rules a docstring calls "the one reader" of, each with its own match.


def _loads(node: ast.AST, name: str) -> bool:
    """Whether ``node`` reads the name ``name``."""
    return (
        isinstance(node, ast.Name)
        and node.id == name
        and isinstance(node.ctx, ast.Load)
    )


def _loads_attribute(node: ast.AST, attr: str) -> TypeGuard[ast.Attribute]:
    """Whether ``node`` reads an attribute named ``attr``."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == attr
        and isinstance(node.ctx, ast.Load)
    )


def _is_constant(node: ast.AST, value: object) -> bool:
    return isinstance(node, ast.Constant) and node.value == value


def _parses_a_version_segment(node: ast.AST) -> bool:
    return _calls(node, "ascii_int") and isinstance(node.args[0], ast.Subscript)


def _reads_an_answer_status(node: ast.AST) -> bool:
    """Whether ``node`` reads the answered statuses, or an unanswered count."""
    if _loads(node, "_ANSWERED"):
        return True
    if isinstance(node, ast.Subscript):
        key = node.slice
    elif _calls(node, "get") and node.args:
        key = node.args[0]
    else:
        return False
    return _is_constant(key, "unvoted") or _is_constant(key, "stale")


def _reads_a_flow_label(node: ast.AST) -> bool:
    return _loads_attribute(node, "label") and _calls(node.value, "parse_flow_id")


def _tests_the_control_step(node: ast.AST) -> bool:
    """Whether ``node`` spells the steps that reach the control step (none, and
    ``control``) or the steps that stop before it."""
    return isinstance(node, ast.Tuple | ast.Set | ast.List) and {
        getattr(item, "value", object()) for item in node.elts
    } in ({None, "control"}, set(get_args(CriticStep)) - {"control"})


#: The score fields that hold an extraction's endpoint keys.
_ENDPOINT_FIELDS = frozenset({"endpoint_missing", "missing", "matched"})


def _splits_the_zones_of_a_score(node: ast.AST) -> bool:
    """A comprehension over a score's endpoint keys that keeps the zones."""
    if not isinstance(node, ast.ListComp | ast.SetComp | ast.GeneratorExp):
        return False
    over_keys = any(
        isinstance(sub, ast.Attribute) and sub.attr in _ENDPOINT_FIELDS
        for generator in node.generators
        for sub in ast.walk(generator.iter)
    )
    keeps_zones = any(
        _calls(sub, "is_zone")
        for generator in node.generators
        for test in generator.ifs
        for sub in ast.walk(test)
    )
    return over_keys and keeps_zones


def _scores_an_assertion_run(node: ast.AST) -> bool:
    """A function that takes a signed reference and an assertion run."""
    if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        return False
    annotations = {
        ast.unparse(argument.annotation)
        for argument in (*node.args.args, *node.args.kwonlyargs)
        if argument.annotation is not None
    }
    return {"SignedReference", "AssertionResult"} <= annotations


def _keys_a_crossing(node: ast.AST) -> bool:
    return (
        _calls(node, "_endpoint_key")
        and bool(node.args)
        and _loads_attribute(node.args[0], "flow_id")
    )


def _counts_spellings(node: ast.AST) -> bool:
    """A count of a reference's spellings: ``len`` of them anywhere, or a
    comparison of them with a literal tuple."""
    if isinstance(node, ast.Call) and _calls(node, "len") and node.args:
        return _loads_attribute(node.args[0], "spellings")
    if not isinstance(node, ast.Compare):
        return False
    sides = (node.left, *node.comparators)
    return any(_loads_attribute(side, "spellings") for side in sides) and any(
        isinstance(side, ast.Tuple) for side in sides
    )


def _finds_an_assumed_zone(node: ast.AST) -> bool:
    if not isinstance(node, ast.Compare):
        return False
    sides = (node.left, *node.comparators)
    return any(_loads_attribute(side, "attribute") for side in sides) and any(
        _loads(side, "ZONE_ATTRIBUTE") or _is_constant(side, "trust_zone")
        for side in sides
    )


def _places_a_field_question(node: ast.AST) -> bool:
    return isinstance(node, ast.Subscript) and _calls(node.value, "group_of")


def _counts_a_charge(node: ast.AST) -> bool:
    return (
        _calls(node, "get")
        and isinstance(node.func, ast.Attribute)
        and _loads(node.func.value, "charged")
    )


def _renames_the_carried_answers(node: ast.AST) -> bool:
    return (
        _calls(node, "renamed")
        and bool(node.args)
        and _loads_attribute(node.args[0], "carried")
    )


def _walks_the_scoped_controls(node: ast.AST) -> bool:
    return isinstance(node, ast.For) and _loads(node.iter, "IN_SCOPE")


def _reads_a_unit_text(node: ast.AST) -> bool:
    return (
        _calls(node, "requirement_text")
        or _calls(node, "published_text")
        or _loads(node, "_TEXT_BY_ID")
        or _loads(node, "_NAME_BY_ID")
    )


def _marks_a_read_back(node: ast.AST) -> bool:
    return _is_constant(node, "stored") or (
        _loads_attribute(node, "context") and _loads(node.value, "info")
    )


def _opens_an_answered_row(node: ast.AST) -> bool:
    if not _calls(node, "model_copy"):
        return False
    return any(
        keyword.arg == "update"
        and isinstance(keyword.value, ast.Dict)
        and any(
            _is_constant(key, "value") and _loads(value, "UNKNOWN")
            for key, value in zip(keyword.value.keys, keyword.value.values)
            if key is not None
        )
        for keyword in node.keywords
    )


def _reads_a_requirement_level(node: ast.AST) -> bool:
    return _calls(node, "requirement_level") or _loads(node, "_LEVEL_BY_ID")


#: The admission bounds a job is held to.
_ADMISSION_BOUNDS = frozenset(
    {"max_jobs_per_window", "max_tokens_per_window", "global_max_tokens_per_window"}
)


def _compares_an_admission_bound(node: ast.AST) -> bool:
    return isinstance(node, ast.Compare) and any(
        isinstance(side, ast.Attribute) and side.attr in _ADMISSION_BOUNDS
        for side in (node.left, *node.comparators)
    )


def _finds_a_stated_protocol(node: ast.AST) -> bool:
    if isinstance(node, ast.Tuple):
        return len(node.elts) == 2 and _is_constant(node.elts[1], "protocol")
    if not isinstance(node, ast.Compare):
        return False
    sides = (node.left, *node.comparators)
    return any(_is_constant(side, "protocol") for side in sides) and any(
        _loads_attribute(side, "attribute") for side in sides
    )


def _counts_element_ids(node: ast.AST) -> bool:
    if not (_calls(node, "Counter") and node.args):
        return False
    counted = node.args[0]
    return (
        isinstance(counted, ast.ListComp | ast.GeneratorExp)
        and _loads_attribute(counted.elt, "id")
        and _loads(counted.elt.value, "element")
    )


def _compares_a_slug(node: ast.AST) -> bool:
    """A comparison with the part of an ID after its prefix."""

    def slug(side: ast.expr) -> bool:
        if not (isinstance(side, ast.Subscript) and isinstance(side.value, ast.Call)):
            return False
        cut = side.value
        index = side.slice
        return (_calls(cut, "partition") and _is_constant(index, 2)) or (
            _calls(cut, "split")
            and isinstance(index, ast.UnaryOp)
            and _is_constant(index.operand, 1)
        )

    return isinstance(node, ast.Compare) and any(
        slug(side) for side in (node.left, *node.comparators)
    )


def _reads_a_cache_rule(node: ast.AST) -> bool:
    return _loads_attribute(node, "prompt_cache") or _calls(node, "accepts")


def _calls_each(node: ast.AST, *names: str) -> bool:
    """Whether ``node`` holds a call of each of ``names``."""
    return all(any(_calls(part, name) for part in ast.walk(node)) for name in names)


def _joins(node: ast.AST, *names: str) -> bool:
    """A boolean expression that calls each of ``names``."""
    return isinstance(node, ast.BoolOp) and _calls_each(node, *names)


def _compares_an_origin(node: ast.AST) -> bool:
    return _compares_with(node, "answer") and any(
        _loads_attribute(part, "origin") for part in (node.left, *node.comparators)
    )


def _ends_a_job(node: ast.AST) -> bool:
    """A boolean expression that asks both halves of an ended job."""
    return _joins(node, "readable") and any(
        _loads(part, "TERMINAL_STATUSES") for part in ast.walk(node)
    )


def _opens_an_attribute(node: ast.AST) -> bool:
    """A function that asks both ways an attribute stays open."""
    return isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and _calls_each(
        node, "control_state", "assumed_zone_elements"
    )


def _merges_the_shared_tier_nodes(node: ast.AST) -> bool:
    return isinstance(node, ast.Dict) and any(
        key is None and _loads(value, "_SHARED_TIER_NODES")
        for key, value in zip(node.keys, node.values, strict=True)
    )


def _spells_an_unknown_key(node: ast.AST) -> bool:
    """A tuple of each field of an open fact's key, in the key's order."""
    return (
        isinstance(node, ast.Tuple)
        and len(node.elts) == len(UNKNOWN_KEY_FIELDS)
        and all(
            _loads_attribute(part, field)
            for part, field in zip(node.elts, UNKNOWN_KEY_FIELDS, strict=True)
        )
    )


def _digests(node: ast.AST, read: str) -> bool:
    """A sha256 over the value of a ``read`` call: ``encode`` for a text,
    ``read_bytes`` for a file."""
    return _calls(node, "sha256") and bool(node.args) and _calls(node.args[0], read)


#: The two digest functions of a produced claim.
_CLAIM_DIGESTS = frozenset(
    {"evals.harness.content.structural", "evals.harness.content.prose"}
)


@cache
def _digest_names() -> tuple[frozenset[str], frozenset[str]]:
    """The local names that an import in :data:`FILES` binds to a digest
    function, and to the module that holds the digest functions."""
    bound = [imports(parse(path)).items() for path in FILES]
    functions = {
        local for pairs in bound for local, dotted in pairs if dotted in _CLAIM_DIGESTS
    }
    modules = {
        local
        for pairs in bound
        for local, dotted in pairs
        if dotted == "evals.harness.content"
    }
    return frozenset(functions), frozenset(modules)


def _digests_a_claim(node: ast.AST) -> bool:
    """A call of a digest function of a produced claim, through any import."""
    if not isinstance(node, ast.Call):
        return False
    functions, modules = _digest_names()
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in functions
    return (
        isinstance(func, ast.Attribute)
        and f"evals.harness.content.{func.attr}" in _CLAIM_DIGESTS
        and _is_name(func.value, *modules)
    )


def _lists_a_store(node: ast.AST) -> bool:
    """A ``.list()`` call with no argument: the keys of an object store."""
    return (
        isinstance(node, ast.Call)
        and _loads_attribute(node.func, "list")
        and not node.args
        and not node.keywords
    )


def _reads_the_carried_variable(node: ast.AST) -> bool:
    """A read of ``CARRIED_VAR`` from a mapping, by ``get`` or by subscript."""
    if isinstance(node, ast.Subscript):
        return _loads(node.slice, "CARRIED_VAR")
    return _calls(node, "get") and any(_loads(arg, "CARRIED_VAR") for arg in node.args)


@dataclasses.dataclass(frozen=True)
class _OneReader:
    """A rule that one function reads.

    ``match`` finds each node that applies the rule, ``reader`` is the site
    that reads it, and ``others`` keys each other site that ``match`` finds
    to the reason it is no second reader. ``within`` is the path prefix of the
    sites the rule covers.
    """

    match: Callable[[ast.AST], bool]
    reader: Site
    others: dict[Site, str]
    within: str


#: Each rule a docstring calls "the one reader" of, by the rule's name.
ONE_READERS: dict[str, _OneReader] = {
    "the join of a page's scripts": _OneReader(
        lambda node: _calls(node, "client_script"),
        ("webapp/page.py", "client_scripts"),
        {
            ("webapp/main.py", MODULE_OWNER): (
                "it reads the one theme script, which each page carries by itself"
            ),
            ("webapp/offline_sitting.py", "build"): (
                "the offline file has one script, so it reads that script and joins none"
            ),
        },
        "webapp/",
    ),
    "the position rule": _OneReader(
        _compares_a_position,
        ("evals/harness/scorer.py", "position_fate"),
        {},
        "",
    ),
    "a version segment": _OneReader(
        _parses_a_version_segment,
        ("evals/harness/content.py", "stored_version"),
        {},
        "",
    ),
    "a rejection's steps in an eval instrument": _OneReader(
        lambda node: _loads_attribute(node, "rejected_because"),
        ("evals/harness/critic_yield.py", "rejection_steps"),
        {
            ("evals/harness/attribution.py", "_charge_miss"): (
                "the loss attribution charges a miss to the critic, a separate"
                " instrument with a rule of its own"
            ),
        },
        "evals/",
    ),
    "which statuses answer a finding": _OneReader(
        _reads_an_answer_status,
        ("evals/harness/scorer.py", "review_coverage"),
        {
            ("evals/harness/scorer.py", "unvoted_count"): (
                "counts the findings nobody voted on at all, which the review"
                " prompt asks for, and claims nothing about what was answered"
            ),
            ("evals/harness/scorer.py", "stale_count"): (
                "counts the findings whose votes judged an earlier argument, which"
                " a sweep prints beside the unvoted count, and claims no total"
            ),
        },
        "evals/",
    ),
    "a flow's label": _OneReader(
        _reads_a_flow_label,
        ("src/analysis_service/system_model.py", "flow_label"),
        {},
        "",
    ),
    "the steps that reach the control step": _OneReader(
        _tests_the_control_step,
        ("src/analysis_service/critic.py", "_reaches_control_step"),
        {},
        "",
    ),
    "the trust zones of an extraction score": _OneReader(
        _splits_the_zones_of_a_score, ("evals/harness/modes.py", "_zones"), {}, ""
    ),
    "an assertion run against its signed reference": _OneReader(
        _scores_an_assertion_run,
        ("evals/harness/replay.py", "replay_assertions"),
        {
            ("evals/harness/replay.py", "aligned_rows"): (
                "the row alignment that replay_assertions calls, and nothing else"
                " calls it"
            ),
        },
        "",
    ),
    "a decided crossing's endpoint key": _OneReader(
        _keys_a_crossing, ("evals/harness/modes.py", "_crossing_keys"), {}, ""
    ),
    "how many spellings a fact reference uses": _OneReader(
        _counts_spellings,
        ("src/analysis_service/claims.py", "sound"),
        {
            ("src/analysis_service/claims.py", "mixed"): (
                "the other count of the same spellings: more than one"
            ),
            ("src/analysis_service/critic.py", "_unresolved_unknown_ref_issues"): (
                "the refusal of a mixed entry says how many spellings it uses"
            ),
        },
        "",
    ),
    "an assumed trust zone": _OneReader(
        _finds_an_assumed_zone,
        ("src/analysis_service/system_model.py", "assumed_zone_elements"),
        {},
        "",
    ),
    "the place a question's limit counts": _OneReader(
        _places_a_field_question,
        ("src/analysis_service/answer_state.py", "limit_place"),
        {},
        "",
    ),
    "the turn rule": _OneReader(
        _counts_a_charge,
        ("src/analysis_service/answer_state.py", "whose_turn"),
        {},
        "",
    ),
    "what a job's Controls resolve to": _OneReader(
        lambda node: _calls(node, "default_resolution"),
        ("src/analysis_service/job_controls.py", "resolutions"),
        {},
        "",
    ),
    "what a draft is shown of the Controls": _OneReader(
        lambda node: _calls(node, "reaches"),
        ("src/analysis_service/job_controls.py", "shown_controls"),
        {},
        "",
    ),
    "which leads a control value may not open with": _OneReader(
        lambda node: (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id in {"AMBIGUOUS_CONTROL_LEADS", "UNCERTAIN_CONTROL_LEADS"}
        ),
        ("src/analysis_service/validation.py", "ambiguous_lead"),
        {},
        "",
    ),
    "the outcome of a Control row": _OneReader(
        lambda node: (
            isinstance(node, ast.Return)
            and any(
                isinstance(part, ast.Constant) and part.value == "no-finding"
                for part in ast.walk(node)
            )
        ),
        ("src/analysis_service/job_controls.py", "row_outcome"),
        {},
        "",
    ),
    "what a job's Controls write": _OneReader(
        lambda node: _calls(node, "_stated_value"),
        ("src/analysis_service/job_controls.py", "control_facts"),
        {},
        "",
    ),
    "the rung of a held answer": _OneReader(
        lambda node: _calls(node, "FactOrigin"),
        ("src/analysis_service/answer_state.py", "fact_origin"),
        {},
        "",
    ),
    "what a new analysis carries": _OneReader(
        _renames_the_carried_answers,
        ("src/analysis_service/jobs.py", "held_answers"),
        {},
        "",
    ),
    "whether a quote names where it sits": _OneReader(
        lambda node: _calls(node, "placements"),
        ("src/analysis_service/assertions.py", "ambiguous_quote"),
        {},
        "",
    ),
    "which values the control diagnostic measures": _OneReader(
        _walks_the_scoped_controls,
        ("src/analysis_service/basis.py", "control_measurability"),
        {},
        "",
    ),
    "a framework's own text of a unit": _OneReader(
        _reads_a_unit_text,
        ("src/analysis_service/frameworks/asvs/record.py", "text_of_unit"),
        {
            ("src/analysis_service/frameworks/asvs/catalog.py", "requirement_text"): (
                "the package's catalog reader, which text_of_unit calls"
            ),
            ("src/analysis_service/frameworks/asvs/catalog.py", "published_text"): (
                "the package's catalog reader of each grain, which text_of_unit calls"
            ),
            ("evals/build_review_docs.py", "_standard"): (
                "the review documents of one package's corpus read that"
                " package's catalog, and the neutral seam serves a ruling"
            ),
        },
        "",
    ),
    "a bare secret beside its file variable": _OneReader(
        lambda node: _calls(node, "refused_var"),
        ("src/analysis_service/config_files.py", "refuse_bare_secret"),
        {
            ("webapp/settings_page.py", "_credential_settings"): (
                "it names a key's setting on the page from the variable, and it"
                " refuses nothing"
            ),
            ("webapp/settings_save.py", "_key_stem"): (
                "it names the stem of a key file from the variable, and it"
                " refuses nothing"
            ),
        },
        "",
    ),
    "when a part waits on its parent": _OneReader(
        lambda node: _calls(node, "asked_parent"),
        ("src/analysis_service/early_questions.py", "waiting_parts"),
        {
            ("src/analysis_service/early_questions.py", "capability_questions"): (
                "it orders the capability questions under their parents, and"
                " decides no wait"
            ),
            ("src/analysis_service/early_questions.py", "depth"): (
                "it counts a question's depth for the order, and decides no wait"
            ),
        },
        "",
    ),
    "a submission or a read-back": _OneReader(
        _marks_a_read_back,
        ("src/analysis_service/fact_answers.py", "read_back"),
        {
            ("src/analysis_service/fact_answers.py", MODULE_OWNER): (
                "the context a read-back passes, which the stores write"
            ),
        },
        "",
    ),
    "the row an answer settles": _OneReader(
        _opens_an_answered_row,
        ("src/analysis_service/fact_answers.py", "answered_row"),
        {},
        "",
    ),
    "a fact's history": _OneReader(
        lambda node: _is_constant(node, "unanswered"),
        ("src/analysis_service/fact_answers.py", "fact_status"),
        {
            ("src/analysis_service/fact_answers.py", MODULE_OWNER): (
                "the FactStatus type that declares the set of histories"
            ),
            ("webapp/page_words.py", MODULE_OWNER): (
                "the word tables of the report page, keyed by FactStatus and held"
                " to it by tests/test_page_words.py"
            ),
        },
        "",
    ),
    "how a published catalog ranks a requirement": _OneReader(
        _reads_a_requirement_level,
        ("src/analysis_service/frameworks/asvs/catalog.py", "requirement_band"),
        {
            ("src/analysis_service/frameworks/asvs/catalog.py", "requirement_level"): (
                "the level lookup that requirement_band calls"
            ),
        },
        "",
    ),
    "an open protocol for a web precondition": _OneReader(
        lambda node: _calls(node, "states_a_protocol"),
        ("src/analysis_service/frameworks/asvs/rules.py", "_protocol_unsettled"),
        {
            ("evals/harness/alignment.py", "protocol_state"): (
                "it reduces a protocol to stated or silent for the scorer, and"
                " asks no precondition"
            ),
        },
        "",
    ),
    "whether a resumed job locks its parent": _OneReader(
        lambda node: _loads(node, "NON_LOCKING_STATUSES"),
        ("src/analysis_service/jobs.py", "locks_its_parent"),
        {},
        "",
    ),
    "the admission bounds": _OneReader(
        _compares_an_admission_bound,
        ("src/analysis_service/jobs.py", "check_bounds"),
        {},
        "",
    ),
    "a model's family past its gateways": _OneReader(
        lambda node: _loads(node, "_GATEWAY_PREFIX"),
        ("src/analysis_service/vendors.py", "family_identifier"),
        {},
        "",
    ),
    "whether a flow's protocol is a basis": _OneReader(
        _finds_a_stated_protocol,
        ("src/analysis_service/protocol_defaults.py", "stated_protocol_flows"),
        {},
        "",
    ),
    "the bound a pinned upstream takes": _OneReader(
        lambda node: _loads_attribute(node, "bounds_by_upstream"),
        ("src/analysis_service/resilience.py", "_pinned"),
        {
            ("src/analysis_service/resilience.py", "_check_upstream_keys"): (
                "it checks the table's keys when the file loads, and reads no bound"
            ),
        },
        "",
    ),
    "whether an element ID is unique": _OneReader(
        _counts_element_ids,
        ("src/analysis_service/system_model.py", "duplicate_ids"),
        {},
        "",
    ),
    "an endpoint that follows a slug": _OneReader(
        _compares_a_slug,
        ("src/analysis_service/system_model.py", "follow_by_slug"),
        {
            ("evals/harness/replay.py", "_node_fate"): (
                "it labels a missed reference node in an eval replay, and"
                " resolves no endpoint"
            ),
        },
        "",
    ),
    "whether a model takes the cache breakpoint": _OneReader(
        _reads_a_cache_rule,
        ("src/analysis_service/vendors.py", "caches_prompt_prefix"),
        {},
        "",
    ),
    "a cloud resource identifier": _OneReader(
        lambda node: _loads(node, "_RESOURCE_FORMS"),
        ("src/analysis_service/vendors.py", "_resource_form"),
        {},
        "",
    ),
    "a text's digest": _OneReader(
        partial(_digests, read="encode"),
        ("src/analysis_service/sources.py", "text_digest"),
        {},
        "",
    ),
    "a read file's digest": _OneReader(
        partial(_digests, read="read_bytes"),
        ("evals/harness/reference.py", "read_digest"),
        {
            ("evals/harness/artifact.py", "corpus_digest"): (
                "it folds each corpus file and its path into one digest of the"
                " corpus, and pins no read"
            ),
            ("evals/harness/baseline.py", "_file_digests"): (
                "it seals the files of a Baseline, and the same function"
                " recomputes the seal"
            ),
            ("evals/harness/run_record.py", "run_record"): (
                "it records each input file of one command in its run record,"
                " and pins no read"
            ),
        },
        "",
    ),
    "the settings a credential mode reads": _OneReader(
        lambda node: _loads_attribute(node, "mode_reads"),
        ("src/analysis_service/objects.py", "reads_in"),
        {},
        "",
    ),
    "the object credential mode": _OneReader(
        lambda node: (
            _calls(node, "closed_setting")
            and len(node.args) == 3
            and _loads(node.args[1], "OBJECT_CREDENTIALS_VAR")
        ),
        ("src/analysis_service/objects.py", "_named_mode"),
        {},
        "",
    ),
    "which process migrates a database": _OneReader(
        lambda node: _loads_attribute(node, "first_run_migrates"),
        ("src/analysis_service/sql_store.py", "migrator"),
        {},
        "",
    ),
    "a key file of the s3 store": _OneReader(
        lambda node: _loads(node, "S3_KEY_FILES"),
        ("src/analysis_service/s3_objects.py", "_key_pair"),
        {
            ("src/analysis_service/objects.py", MODULE_OWNER): (
                "the s3 row names the key files as settings it reads, and the"
                " settings that only the api_key mode reads"
            ),
            ("src/analysis_service/s3_objects.py", "_credential_mode"): (
                "it refuses a key set by its own name beside each key file, and"
                " leaves a key file of another mode to refuse_unread"
            ),
            ("src/analysis_service/settings_variables.py", "secret_vars"): (
                "it lists the key files among the settings that hold a secret,"
                " and refuses nothing"
            ),
        },
        "",
    ),
    "a case directory": _OneReader(
        lambda node: _loads(node, "CASE_FILES"),
        ("evals/harness/reference.py", "missing_case_files"),
        {},
        "",
    ),
    "whether a catalog passes": _OneReader(
        lambda node: _calls(node, "catalog_issues"),
        ("src/analysis_service/assertions.py", "over"),
        {},
        "",
    ),
    "which rows a record loses": _OneReader(
        lambda node: _loads(node, "GATE_REFUSALS"),
        ("src/analysis_service/assertions.py", "refusals"),
        {},
        "",
    ),
    "which proposed row a record loses": _OneReader(
        lambda node: _loads_attribute(node, "proposed_row"),
        ("src/analysis_service/assertions.py", "resolved"),
        {
            ("evals/harness/population.py", "population"): (
                "it copies the proposed row of each refusal into a population"
                " row, and decides no loss"
            ),
        },
        "",
    ),
    "whether a catalog row is an answer": _OneReader(
        _compares_an_origin,
        ("src/analysis_service/assertions.py", "answered"),
        {
            ("src/analysis_service/validation.py", "_citation_issues"): (
                "it reads whether an answer stated a capability statement, and"
                " reads no catalog row"
            ),
        },
        "",
    ),
    "which settled rows the Evidence Catalog carries": _OneReader(
        lambda node: _loads(node, "SETTLING_REASONS"),
        ("src/analysis_service/assertions.py", "offered"),
        {
            ("src/analysis_service/assertions.py", "catalog_coverage"): (
                "it counts the attributes that the catalog settles for a"
                " coverage figure, and chooses no row"
            ),
        },
        "",
    ),
    "the band-and-units order": _OneReader(
        lambda node: _loads_attribute(node, "framework_bands"),
        ("src/analysis_service/early_questions.py", "standing"),
        {
            ("src/analysis_service/answer_state.py", "passes_floor"): (
                "it lets a question past the floor of its kind by its band, and"
                " orders nothing"
            ),
        },
        "",
    ),
    "the facts a candidate reads": _OneReader(
        lambda node: _loads(node, "ZONE_FACTS"),
        ("src/analysis_service/early_questions.py", "reads"),
        {},
        "",
    ),
    "whether a selection reads capabilities": _OneReader(
        lambda node: _calls(node, "capability_keys"),
        ("src/analysis_service/frameworks/__init__.py", "reads_capabilities"),
        {},
        "",
    ),
    "a requirement's decision": _OneReader(
        lambda node: _calls(node, "applicability_for"),
        ("src/analysis_service/frameworks/asvs/record.py", "applicability"),
        {
            ("src/analysis_service/frameworks/asvs/record.py", "open_capabilities"): (
                "it lists the capabilities whose answer would change a decision,"
                " and decides no requirement"
            ),
        },
        "",
    ),
    "the pieces of a cut quote": _OneReader(
        lambda node: _loads(node, "_ELLIPSIS"),
        ("src/analysis_service/grounding.py", "fragments"),
        {
            ("src/analysis_service/grounding.py", "_needle_of"): (
                "it refuses a cut quote as the needle of a repair, and splits no quote"
            ),
        },
        "",
    ),
    "which kind a fact is": _OneReader(
        lambda node: _loads(node, "_KIND_ORDER"),
        ("src/analysis_service/claims.py", "kind"),
        {},
        "",
    ),
    "the schema rule a model takes": _OneReader(
        lambda node: _loads_attribute(node, "schema_rules"),
        ("src/analysis_service/vendors.py", "schema_rule"),
        {},
        "",
    ),
    "a stray store setting": _OneReader(
        lambda node: (
            _calls(node, "startswith")
            and bool(node.args)
            and _loads(node.args[0], "prefixes")
        ),
        ("src/analysis_service/store_settings.py", "refuse_unread"),
        {},
        "",
    ),
    "the frameworks a job names": _OneReader(
        lambda node: _calls(node, "NotCarried"),
        ("src/analysis_service/selection.py", "resolve_names"),
        {},
        "",
    ),
    "a claim's channels": _OneReader(
        lambda node: _loads_attribute(node, "siblings") and _loads(node.value, "flows"),
        ("evals/harness/identity.py", "channels"),
        {},
        "",
    ),
    "what an extraction loss lacked": _OneReader(
        lambda node: _loads_attribute(node, "extraction_read"),
        ("evals/harness/extraction_losses.py", "lacked"),
        {
            ("evals/harness/extraction_losses.py", "extraction_contributed"): (
                "it reads a downstream loss, which lacked does not read, and"
                " charges extraction beside the lanes"
            ),
        },
        "",
    ),
    "the tier of each graph node": _OneReader(
        _merges_the_shared_tier_nodes,
        ("src/analysis_service/graph.py", "tier_node_by_graph_node"),
        {},
        "",
    ),
    "whether a case is read": _OneReader(
        _tests_truth_of(lambda node: _calls(node, "waiting")),
        ("evals/review_submission.py", "unreviewed_cases"),
        {},
        "",
    ),
    "what a job asks about": _OneReader(
        lambda node: _calls(node, "checkpoint"),
        ("src/analysis_service/job_answers.py", "answerable"),
        {},
        "",
    ),
    "whether a job has ended": _OneReader(
        _ends_a_job, ("src/analysis_service/jobs.py", "ended"), {}, ""
    ),
    "whether an attribute is open": _OneReader(
        _opens_an_attribute,
        ("src/analysis_service/open_facts.py", "open_attribute"),
        {},
        "",
    ),
    "which listed question a list may rank": _OneReader(
        lambda node: _joins(node, "_open", "passes_floor"),
        ("src/analysis_service/answer_state.py", "_rankable"),
        {
            ("src/analysis_service/answer_state.py", "question_set"): (
                "it holds back the questions an undecided gate would ask, and"
                " the open questions below the floor, which no list ranks"
            ),
        },
        "",
    ),
    "what a proposal comes to": _OneReader(
        lambda node: _calls(node, "_resolve"),
        ("src/analysis_service/assertions.py", "resolved"),
        {},
        "",
    ),
    "the limits of an audit": _OneReader(
        lambda node: (
            isinstance(node, ast.Compare)
            and any(_loads_attribute(part, "budget_usd") for part in ast.walk(node))
        ),
        ("evals/harness/audit.py", "admit"),
        {},
        "",
    ),
    "whether two references are the same": _OneReader(
        _spells_an_unknown_key,
        ("src/analysis_service/claims.py", "key"),
        {},
        "",
    ),
    "why a part of an owned job is missing": _OneReader(
        lambda node: _calls(node, "JobDataMissing"),
        ("src/analysis_service/job_answers.py", "missing"),
        {},
        "",
    ),
    "which subjects a catalog names": _OneReader(
        lambda node: (
            isinstance(node, ast.AugAssign)
            and isinstance(node.op, ast.Sub)
            and _loads(node.value, "UNIVERSAL_TERMS")
        ),
        ("src/analysis_service/assertions.py", "subjects_of"),
        {},
        "",
    ),
    "a config path": _OneReader(
        lambda node: (
            _calls(node, "Path") and bool(node.args) and _calls(node.args[0], "strip")
        ),
        ("src/analysis_service/deployment.py", "config_path"),
        {},
        "",
    ),
    "a Report's rows": _OneReader(
        lambda node: _calls(node, "row_controls"),
        ("src/analysis_service/claim_identity.py", "report_rows"),
        {
            ("src/analysis_service/fixes.py", "absent_rows"): (
                "it holds a catalog and no Report, so it reads the row controls"
                " of the catalog itself"
            ),
        },
        "",
    ),
    "which control a row is about": _OneReader(
        lambda node: (
            isinstance(node, ast.DictComp)
            and _calls(node.key, "assertion_id")
            and (
                _calls_each(node, "parse_predicate")
                or any(_loads(part, "REGISTRY") for part in ast.walk(node))
            )
        ),
        ("src/analysis_service/claim_identity.py", "row_controls"),
        {},
        "",
    ),
    "whether a report may be served": _OneReader(
        lambda node: _calls(node, "NotCompleted"),
        ("src/analysis_service/job_answers.py", "servable"),
        {},
        "",
    ),
    "a closed setting": _OneReader(
        lambda node: (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "unknown value for " in node.value
        ),
        ("src/analysis_service/store_settings.py", "closed_setting"),
        {},
        "",
    ),
    "the app's store defaults": _OneReader(
        lambda node: _calls(node, "_default_database"),
        ("webapp/settings_view.py", "store_defaults"),
        {
            ("webapp/settings_view.py", "build_app_store"): (
                "it asks whether the store sits at the default database, to make"
                " its directory, and sets no default"
            ),
        },
        "",
    ),
    "how a Baseline's models read": _OneReader(
        lambda node: _is_constant(node, " unpinned"),
        ("evals/harness/comparison.py", "models_phrase"),
        {},
        "",
    ),
    "why a finding is still conditional": _OneReader(
        lambda node: _calls(node, "waits_on"),
        ("src/analysis_service/report_conditions.py", "conditions"),
        {
            ("src/analysis_service/report_conditions.py", "rests_on"): (
                "it asks whether a claim rests on a corrected fact, and writes"
                " no condition"
            ),
            ("src/analysis_service/questions.py", "follow_up_needs"): (
                "it lists the open facts a draft waits on as questions to ask,"
                " and writes no condition"
            ),
        },
        "",
    ),
    "what a queue item shows": _OneReader(
        lambda node: _calls(node, "Finding"),
        ("evals/harness/queue.py", "from_claim"),
        {},
        "",
    ),
    "the digests a queue item carries": _OneReader(
        _digests_a_claim,
        ("evals/harness/queue.py", "from_claim"),
        {
            ("evals/harness/scorer.py", "_status_of_unmatched"): (
                "it digests a produced threat to ask whether a vote on it is"
                " still live, and builds no queue item"
            ),
            ("evals/harness/writing.py", "measure_case"): (
                "it digests a claim's prose to ask whether a vote judged these"
                " words, and builds no queue item"
            ),
        },
        "",
    ),
    "the keys of an object store": _OneReader(
        _lists_a_store,
        ("src/analysis_service/sql_store.py", "sweep_orphans"),
        {},
        "",
    ),
    "the input bounds an eval run reads from the environment": _OneReader(
        lambda node: _calls(node, "load_resilience"),
        ("evals/harness/reference.py", "source_limits"),
        {},
        "evals/",
    ),
    "the carried frameworks variable": _OneReader(
        _reads_the_carried_variable,
        ("src/analysis_service/framework_config.py", "load_frameworks"),
        {},
        "",
    ),
    "the capabilities the sources state": _OneReader(
        lambda node: _calls(node, "with_stated_capabilities"),
        ("src/analysis_service/graph.py", "stated"),
        {},
        "",
    ),
    "the render of a job's sources": _OneReader(
        lambda node: _calls(node, "render_sources"),
        ("src/analysis_service/execution.py", "seed_state"),
        {
            ("evals/adversarial/verify.py", "fencing_issues"): (
                "it renders sources to check the fence around them, and it"
                " drives no graph and shows no job to a model"
            ),
        },
        "",
    ),
}


@pytest.mark.parametrize("rule", sorted(ONE_READERS))
def test_a_rule_with_one_reader_has_no_second(rule):
    """Each rule a docstring calls "the one reader" of is applied at its
    reader, and at each other site only for the reason the row gives."""
    row = ONE_READERS[rule]
    found = [site for site in _sites(row.match) if site[0].startswith(row.within)]

    assert found == sorted([row.reader, *row.others])


@pytest.mark.parametrize(
    ("rule", "reason"),
    [
        (rule, reason)
        for rule, row in ONE_READERS.items()
        for reason in row.others.values()
    ],
)
def test_each_other_site_of_a_rule_says_why(rule, reason):
    assert len(reason) >= 40, rule


def _compares_with(node: ast.AST, value: str) -> TypeGuard[ast.Compare]:
    """Whether ``node`` compares something with the constant ``value``."""
    return isinstance(node, ast.Compare) and any(
        _is_constant(part, value) for part in (node.left, *node.comparators)
    )


def test_which_verdict_waits_has_one_rule():
    """Only the validator of ``Verdict`` reads ``related_unknowns`` beside a
    test of the ``needs-info`` status. Each other site reads the field, which
    the validator keeps empty on every other status."""
    compared = set(_sites(lambda node: _compares_with(node, "needs-info")))
    read = set(_sites(lambda node: _loads_attribute(node, "related_unknowns")))

    assert sorted(compared & read) == [
        ("src/analysis_service/claims.py", "_check_shape")
    ]


def test_the_gate_and_done_tests_of_a_question_have_one_reader():
    """Only ``_open`` asks whether a question is not a gate."""

    def not_a_gate(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.UnaryOp)
            and isinstance(node.op, ast.Not)
            and _loads_attribute(node.operand, "gates")
        )

    assert _sites(not_a_gate) == [("src/analysis_service/answer_state.py", "_open")]


def test_an_expected_requirement_has_one_reader():
    """Only ``applies`` compares a record's disposition with
    ``not-applicable``, and only ``rules_out`` names the answers that rule a
    requirement out."""

    def compares_a_disposition(node: ast.AST) -> bool:
        return _compares_with(node, "not-applicable") and any(
            _loads_attribute(part, "disposition")
            or _loads_attribute(part, "expected")
            or _loads(part, "disposition")
            for part in (node.left, *node.comparators)
        )

    def names_the_ruled_out_answers(node: ast.AST) -> bool:
        return isinstance(node, ast.Set) and all(
            any(_is_constant(item, kind) for item in node.elts)
            for kind in ("rejected", "not-applicable")
        )

    assert _sites(compares_a_disposition) == [
        ("evals/harness/applicability.py", "applies")
    ]
    assert _sites(names_the_ruled_out_answers) == [
        ("evals/harness/applicability.py", "rules_out")
    ]


#: Each site that writes a catalog's subjects table past ``subjects_of``,
#: with the reason.
OWN_SUBJECT_TABLES: dict[Site, str] = {
    ("evals/harness/bundle.py", "redrawn"): (
        "it moves each subject of the table to its derived ID, and every row to"
        " the moved ID, so the table names what the rows name"
    ),
    **{
        ("evals/harness/falsify.py", function): (
            "a falsification probe builds a catalog that breaks a rule on purpose"
        )
        for function in ("consume_probe", "reading", "run_probe")
    },
}


def test_a_subjects_table_has_one_reader():
    """Each catalog writer derives its subjects table with ``subjects_of``."""

    def writes_a_table(node: ast.AST) -> bool:
        if _calls(node, "AssertionCatalog"):
            values = [k.value for k in node.keywords if k.arg == "subjects"]
        elif _calls(node, "model_copy"):
            values = [
                value
                for k in node.keywords
                if k.arg == "update" and isinstance(k.value, ast.Dict)
                for key, value in zip(k.value.keys, k.value.values, strict=True)
                if key is not None and _is_constant(key, "subjects")
            ]
        else:
            return False
        return any(not _calls(value, "subjects_of") for value in values)

    assert _sites(writes_a_table) == sorted(OWN_SUBJECT_TABLES)
