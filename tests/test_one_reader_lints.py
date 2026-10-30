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
import re
from collections.abc import Callable, Iterable, Iterator
from functools import partial
from itertools import pairwise
from pathlib import Path
from typing import TypeGuard

import pytest

from analysis_service.analysis import CONTROL_ATTRIBUTES
from analysis_service.claims import Ruling
from analysis_service.sql_store import DIGEST_COLUMNS
from evals.harness.prices import UnitPrices
from tests.source_tree import PYTHON, REPO_ROOT, TESTS, parse, source_files, without

pytestmark = pytest.mark.lint

#: A site: the file, relative to the repository root, and the enclosing
#: function, or ``<module>``.
Site = tuple[str, str]


#: The parts of :data:`~tests.source_tree.PYTHON` no lint here walks, with
#: the reason.
LEFT_OUT: dict[str, str] = {
    "evals/experiments": (
        "an audit script there runs one experiment as it ran on its day, so it"
        " records the rule of that day and no live code calls it"
    ),
}

#: The files each lint walks.
FILES = without(source_files(*PYTHON), LEFT_OUT)


def _sites(
    match: Callable[[ast.AST], bool], files: Iterable[Path] = FILES
) -> list[Site]:
    """Each site in ``files`` that holds a node ``match`` accepts, once."""
    return sorted(
        {
            (path.relative_to(REPO_ROOT).as_posix(), function)
            for path in files
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


#: Each function that tests a spelling field of an
#: :class:`~analysis_service.claims.UnknownRef` for its truth, and why it does
#: not ask ``kind``.
SPELLING_READERS: dict[Site, str] = {
    ("src/analysis_service/claims.py", "spellings"): (
        "it is the reader that ``kind`` reads"
    ),
}

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
    """Only ``UnknownRef.kind`` routes on which spelling a reference uses."""
    assert set(_sites(_tests_a_spelling)) == SPELLING_READERS.keys()


def test_the_kind_of_an_open_fact_is_not_read_from_two_fields():
    """No test of two spelling fields decides a kind beside ``UnknownRef.kind``.

    The fields ``element_id``, ``attribute``, ``subject`` and ``assertion``
    have namesakes on other models, so a test of one of them alone is not a
    second reader. A test of two of them at once is how a kind is decided, as
    in ``names_an_element``, which asks ``kind``.
    """
    readers = set(_sites(_tests_two_spelling_fields)) - FIELD_PAIR_READERS.keys()
    assert readers == set()


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
    ("src/analysis_service/answer_bodies.py", "_question_of"): (
        "it finds the fact answer the error sits in, by index, and makes no place"
    ),
    ("src/analysis_service/answer_bodies.py", "over_a_bound"): (
        "it asks whether the error sits on a list of the body itself, and"
        " makes no place"
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


def test_each_declared_site_still_reads_the_rule_itself():
    assert SPELLING_READERS.keys() <= set(_sites(_tests_a_spelling))
    assert FIELD_PAIR_READERS.keys() <= set(_sites(_tests_two_spelling_fields))
    assert LOC_READERS.keys() <= set(_sites(_reads_loc))
    assert CONFIG_DEFAULT_READERS.keys() <= set(_sites(_reads_a_config_default))
    assert ID_DERIVERS.keys() <= set(_sites(_derives_an_id))
    assert STORED_ANSWER_READERS.keys() <= set(_sites(_validates_an_answer))


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
    for path in FILES:
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
    ("src/analysis_service/assertions.py", "subject_id"): (
        "a non-graph subject is no element, and its ID takes the derivation an"
        " element ID has from its type prefix and its name"
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
        ("src/analysis_service/parsing.py", "<module>")
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
    ("evals/harness/audit.py", "<module>"): (
        "a finding's owner: the skill text as one of the places a fix belongs"
    ),
}


def test_a_lane_doc_name_is_spelled_once():
    """Only ``frameworks`` spells a lane's two file names; the loader names,
    the package gate and the roster writer read the declarations."""
    assert set(_sites(_spells_a_lane_doc)) - NOT_A_LANE_DOC.keys() == {
        ("src/analysis_service/frameworks/__init__.py", "<module>")
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
    """Each loader of a key or a password asks ``read_secret`` or, for the
    JSON key of the ``gcs`` row, ``read_secret_file`` with its own error class,
    so the mode rule of a secret file has one reader."""
    assert _sites(_reads_a_secret_file) == [
        ("src/analysis_service/config_files.py", "read_secret_file")
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
