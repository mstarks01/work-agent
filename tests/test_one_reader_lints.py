"""Rules that one function reads, and the lints that find a second reader.

Each lint walks ``src``, ``webapp`` and ``evals/harness`` for the expression
that applies a rule, and names each function that holds it. Every function but
the one reader calls that reader (``docs/agents/one-rule-one-reader.md``). A
function that applies the rule for another reason is declared with that
reason, and a second test fails when the declaration stops being live.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterator
from typing import TypeGuard

from tests.source_tree import REPO_ROOT, parse, source_files

#: A site: the file, relative to the repository root, and the enclosing
#: function, or ``<module>``.
Site = tuple[str, str]


#: The trees each lint walks.
ROOTS = ("src", "webapp", "evals/harness")


def _sites(match: Callable[[ast.AST], bool]) -> list[Site]:
    """Each site in :data:`ROOTS` that holds a node ``match`` accepts, once."""
    return sorted(
        {
            (path.relative_to(REPO_ROOT).as_posix(), function)
            for path in source_files(*ROOTS)
            for node, function in _walk(parse(path), "<module>")
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

    def chooses(node: ast.AST) -> bool:
        return (
            _calls(node, "closed_setting")
            and len(node.args) == 3
            and _is_name(node.args[2], "JOB_STORES", "OBJECT_STORES")
        )

    assert sorted(_sites(chooses)) == [
        ("src/analysis_service/objects.py", "object_store_name"),
        ("src/analysis_service/store_backends.py", "job_store_name"),
    ]


def test_the_object_store_variable_is_spelled_once():
    def spells(node: ast.AST) -> bool:
        return isinstance(node, ast.Constant) and node.value == "ANALYSIS_OBJECT_STORE"

    assert _sites(spells) == [("src/analysis_service/objects.py", "<module>")]


def test_the_store_defaults_have_one_merge():
    """Only ``with_store_defaults`` lays an env over the app's store defaults."""

    def merges(node: ast.AST) -> bool:
        return isinstance(node, ast.Dict) and any(
            key is None and _is_name(value, "STORE_DEFAULTS")
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
    assert readers == {("src/analysis_service/vendors.py", "refused_var")}


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


#: Each function that tests a spelling field of an
#: :class:`~analysis_service.claims.UnknownRef` for its truth, and why it does
#: not ask ``kind``.
SPELLING_READERS: dict[Site, str] = {
    ("src/analysis_service/claims.py", "spellings"): (
        "it is the reader that ``kind`` reads"
    ),
    ("src/analysis_service/claims.py", "names_an_element"): (
        "it asks whether a reference names an element, which a question kind does too"
    ),
}


def _tests_a_spelling(node: ast.AST) -> bool:
    return _tests_truth_of(
        lambda operand: (
            _is_attribute(operand, "capability") or _is_attribute(operand, "question")
        )
    )(node)


def test_the_kind_of_an_open_fact_has_one_reader():
    """Only ``UnknownRef.kind`` routes on which spelling a reference uses."""
    assert set(_sites(_tests_a_spelling)) == SPELLING_READERS.keys()


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
#: ``extra_forbidden`` error, so that a refusal never repeats a key a caller
#: sent.
LOC_READERS: dict[Site, str] = {
    ("src/analysis_service/claims.py", "of"): _MODEL_KEY,
    ("src/analysis_service/compact.py", "expand"): _MODEL_KEY,
    ("src/analysis_service/provider.py", "schema_errors"): _MODEL_KEY,
    ("src/analysis_service/validation.py", "parse_and_validate"): _MODEL_KEY,
    ("src/analysis_service/config_files.py", "validation_problems"): _OWN_FILE,
    ("evals/harness/reference.py", "_case_refusal"): _OWN_FILE,
    ("evals/harness/structural.py", "structural_issues"): _OWN_FILE,
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
}


def _reads_loc(node: ast.AST) -> bool:
    return isinstance(node, ast.Subscript) and (
        isinstance(node.slice, ast.Constant) and node.slice.value == "loc"
    )


def test_each_declared_site_still_reads_the_rule_itself():
    assert SPELLING_READERS.keys() <= set(_sites(_tests_a_spelling))
    assert LOC_READERS.keys() <= set(_sites(_reads_loc))
    assert CONFIG_DEFAULT_READERS.keys() <= set(_sites(_reads_a_config_default))
    assert ID_DERIVERS.keys() <= set(_sites(_derives_an_id))


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
    ("src/analysis_service/deployment.py", "<module>"): (
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
    for path in source_files(*ROOTS):
        for node, function in _walk(parse(path), "<module>"):
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
}


def _derives_an_id(node: ast.AST) -> bool:
    return _calls(node, "make_element_id") or _calls(node, "make_flow_id")


def test_an_element_id_has_one_deriver():
    """Only ``derive_element_id`` derives the ID an element's name implies."""
    derivers = set(_sites(_derives_an_id)) - ID_DERIVERS.keys()
    assert derivers == {("src/analysis_service/system_model.py", "derive_element_id")}
