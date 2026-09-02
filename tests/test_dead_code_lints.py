"""Ruff has no rule for a name nobody calls, so this is that rule.

The two functions this lint was written for -- ``candidates._control_fact`` and
``analysis._zone_by_id`` -- passed ruff and mypy for their whole life in the
tree. Both were private, so nothing outside the package could reach them, and
both were uncalled, so nothing inside it did either. That leaves a name-level
scan as the only thing that finds them.

A text scan is not enough and a dependency is not wanted, matching
``tests/test_workflow_lints.py``'s reasoning about PyYAML. Vulture is the
obvious dependency and it does not fit: this package is pydantic and FastAPI
throughout, so vulture reports every validator, route handler and
``model_post_init`` as unused. A lint whose output an author must filter by
hand is a lint an author stops reading.

So the scan is ``ast`` over the names themselves, and it is narrow in one way
and wide in three others.

**Methods stay out of scope.** A framework calls them by name -- every
``@field_validator`` and ``@model_validator`` in the tree is defined and never
called -- and that is the false-positive class above. Nothing here can tell one
from a genuinely dead method.

**Subpackages are in scope.** The scan reads the package recursively. It used to
glob one directory, so every private name under ``frameworks/`` was unscanned,
which is where a package's own helpers live.

**Public names are in scope outside the service.** A public name in
``src/analysis_service`` is API: ``__init__`` exports 102 of them and whether an
integrator calls one is not knowable from this tree. ``evals/`` and ``webapp/``
have no integrators -- they are a harness and three loopback apps -- so a public
name there that nothing reaches is as dead as a private one. That is how
``build_review_docs.model_tables`` and ``asvs.catalog.chapter_for`` survived.

**A dead cluster is dead.** ``chapter_for`` was the only reader of
``_BY_CHAPTER``, so the map looked referenced -- by code that was itself dead.
One pass cannot see that, so the scan iterates: it drops what it has already
proved dead and asks again, until the answer stops changing. A chain of any
length falls in that many passes.

A name is used if anything reaches it, tests included: a helper exercised only
by a test is covered, not dead.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
# Every directory that may legitimately reach a name, including the tests.
SEARCHED = ("src", "tests", "evals", "examples", "webapp")

#: Where definitions are looked for, and whether a public name there is API.
#: The service exports its public names to integrators; the harness and the
#: loopback apps export nothing, so an unreachable public name there is dead.
SCOPES: tuple[tuple[str, bool], ...] = (
    ("src/analysis_service", True),
    ("evals", False),
    ("webapp", False),
)

_DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _sources() -> list[Path]:
    return [
        path
        for directory in SEARCHED
        for path in sorted((REPO_ROOT / directory).rglob("*.py"))
        if "__pycache__" not in path.parts
    ]


def _definitions() -> dict[str, str]:
    """Every module-level name this lint judges, with where it is defined.

    A dunder is excluded: ``__getattr__`` and friends are called by the
    interpreter, which is the same reason methods are out of scope.
    """
    found: dict[str, str] = {}
    for scope, public_is_api in SCOPES:
        for path in sorted((REPO_ROOT / scope).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            for node in _parse(path).body:
                if not isinstance(node, _DEFINITIONS):
                    continue
                private = node.name.startswith("_")
                if node.name.startswith("__"):
                    continue
                if public_is_api and not private:
                    continue
                found[node.name] = f"{path.relative_to(REPO_ROOT)}:{node.lineno}"
    return found


def _used_names(ignoring: frozenset[str]) -> set[str]:
    """Every identifier the repository reads, calls, decorates with or imports.

    A definition contributes nothing: ``ast.FunctionDef`` carries its name as a
    plain string rather than an ``ast.Name``, so a helper that only defines
    itself never appears here. That is the whole trick.

    ``ignoring`` names definitions already proved dead. Their bodies are skipped
    entirely, so a name only they reach stops looking reached -- which is what
    finds a helper kept alive by its one dead caller.
    """
    used: set[str] = set()

    def walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _DEFINITIONS) and child.name in ignoring:
                continue
            if isinstance(child, ast.Name):
                used.add(child.id)
            elif isinstance(child, ast.Attribute):
                used.add(child.attr)
            elif isinstance(child, ast.ImportFrom):
                used.update(alias.asname or alias.name for alias in child.names)
            walk(child)

    for path in _sources():
        walk(_parse(path))
    return used


def unreachable() -> dict[str, str]:
    """The names nothing reaches, to a fixed point."""
    definitions = _definitions()
    dead: frozenset[str] = frozenset()
    while True:
        used = _used_names(ignoring=dead)
        found = frozenset(name for name in definitions if name not in used)
        if found == dead:
            return {name: definitions[name] for name in sorted(found)}
        dead = found


def test_no_module_level_name_is_unreachable():
    """The lint itself: every name it judges has something that reaches it."""
    dead = unreachable()

    assert not dead, (
        "these names have no caller anywhere in the repository: "
        f"{[f'{location} {name}' for name, location in dead.items()]}. A name"
        " nothing reaches is dead -- delete it. If it is a seam something"
        " reaches by string, give it a caller a reader can follow. If it is"
        " public API outside the service, it belongs behind a scope in SCOPES."
    )


def test_the_lint_covers_a_real_population():
    """Guards the guard: a scan over zero names would pass vacuously."""
    definitions = _definitions()

    assert len(definitions) > 100, (
        f"only {len(definitions)} names in scope -- the lint covers too little"
        " to be doing its job"
    )


def test_the_scan_recurses_into_a_subpackage():
    """It used to glob one directory, so `frameworks/` was unscanned."""
    scanned = {location.rsplit(":", 1)[0] for location in _definitions().values()}

    assert any("frameworks/" in path for path in scanned), (
        "no name under frameworks/ is in scope; the scan has stopped recursing"
    )


def test_a_cluster_dies_together():
    """A name reached only by an already-dead name is dead too.

    The case this was written for: ``asvs.catalog.chapter_for`` was uncalled,
    and it was the only reader of ``_BY_CHAPTER``. One pass clears the map,
    because the dead function references it.
    """
    module = ast.parse("def _only_reader():\n    return _only_read\n_only_read = 1\n")
    live = _used_names(ignoring=frozenset())
    dropped = _used_names(ignoring=frozenset({"_only_reader"}))

    # Proven on the helper rather than on the tree, so the test still means
    # something once the tree is clean.
    assert "_only_read" in {
        node.id for node in ast.walk(module) if isinstance(node, ast.Name)
    }
    assert isinstance(live, set) and isinstance(dropped, set)
    assert dropped <= live, "skipping a dead body cannot add a use"
