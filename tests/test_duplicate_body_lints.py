"""Two functions with one body are one rule with two readers.

The class this repository keeps meeting: a helper copied into a second module,
or into a test, and then the two drift, and each one's test agrees with it.
``_references`` sat in two harness modules, ``all_claims`` on a record and on
its test fake, and one migration's manifest refresh in the next. Every copy
was verbatim on the day it was made, so a lint over verbatim bodies finds
this class on that day rather than after the drift.

The comparison is the ``ast`` dump of the body with the docstring removed, so
a renamed function or a re-wrapped line still matches, and a small body --
one ``return`` of a field -- is below :data:`FLOOR` because two such bodies
are a coincidence rather than a copy. The fix is always the same: one home,
and the other site imports it.
"""

from __future__ import annotations

import ast
from collections import defaultdict

from tests.source_tree import REPO_ROOT, parse, source_files

SEARCHED = ("src", "evals", "webapp", "tests")

#: Characters of ``ast.dump`` below which a body is too small to be a copy.
FLOOR = 250

_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _body_key(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    body = node.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
    ):
        body = body[1:]
    key = ast.dump(ast.Module(body=body, type_ignores=[]))
    return key if len(key) >= FLOOR else None


def duplicates() -> list[list[str]]:
    """Every body defined verbatim in more than one file, with each location."""
    sites: dict[str, list[str]] = defaultdict(list)
    for path in source_files(*SEARCHED):
        for node in ast.walk(parse(path)):
            if isinstance(node, _FUNCTIONS) and (key := _body_key(node)):
                sites[key].append(
                    f"{path.relative_to(REPO_ROOT)}:{node.lineno} {node.name}"
                )
    return [
        found
        for found in sites.values()
        if len({site.split(":")[0] for site in found}) > 1
    ]


def test_no_function_body_is_defined_in_two_files():
    found = duplicates()

    assert not found, (
        f"these functions share one body across files: {found}. Give the rule"
        " one home and import it at the other site."
    )


def test_a_copied_body_is_seen_whatever_it_is_called():
    """Positive control on the key: the name and the docstring do not count."""
    one = ast.parse(
        'def a():\n    """doc"""\n    return {k: (v.x, v.y) for k, v in items.items() if v.z}\n'
    )
    two = ast.parse(
        "def b():\n    return {k: (v.x, v.y) for k, v in items.items() if v.z}\n"
    )

    assert _body_key(one.body[0]) == _body_key(two.body[0])
    assert _body_key(ast.parse("def c():\n    return 1\n").body[0]) is None
