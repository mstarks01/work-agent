"""Rules that one function reads, and the lints that find a second reader.

Each lint walks ``src`` and ``webapp`` for the expression that applies a rule,
and names each function that holds it. Every function but the one reader
calls that reader (``docs/agents/one-rule-one-reader.md``).
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterator
from typing import TypeGuard

from tests.source_tree import REPO_ROOT, parse, source_files

#: A site: the file, relative to the repository root, and the enclosing
#: function, or ``<module>``.
Site = tuple[str, str]


def _sites(match: Callable[[ast.AST], bool]) -> list[Site]:
    """Each site in ``src`` and ``webapp`` that holds a node ``match`` accepts."""
    return [
        (path.relative_to(REPO_ROOT).as_posix(), function)
        for path in source_files("src", "webapp")
        for node, function in _walk(parse(path), "<module>")
        if match(node)
    ]


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
