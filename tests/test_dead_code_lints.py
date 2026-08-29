"""Ruff has no rule for a private helper nobody calls, so this is that rule.

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

So the scan is ``ast`` over the names themselves, and it is deliberately
narrow:

* **Module-level only.** A method is out of scope because a framework calls
  methods by name, which is the false-positive class above.
* **Private only.** A public name is API. ``src/analysis_service/__init__.py``
  exports 102 of them, and whether an integrator calls one is not knowable
  from this tree.
* **Names, not bindings.** Two modules that both define ``_clip`` share one
  name here, so a use of either clears both. Widening that needs import
  resolution, and the narrow version already catches what ruff cannot.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE = REPO_ROOT / "src" / "analysis_service"
# Every directory that may legitimately reach into the package, including the
# tests: a helper exercised only by a test is covered, not dead.
SEARCHED = ("src", "tests", "evals", "examples", "webapp")

_DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _private_definitions() -> list[tuple[str, str]]:
    """The package's module-level private names, each with where it is defined.

    A dunder is excluded: ``__getattr__`` and friends are called by the
    interpreter, which is the same reason methods are out of scope.
    """
    defined = []
    for path in sorted(PACKAGE.glob("*.py")):
        for node in _parse(path).body:
            if not isinstance(node, _DEFINITIONS):
                continue
            if node.name.startswith("_") and not node.name.startswith("__"):
                defined.append((node.name, f"{path.name}:{node.lineno}"))
    return defined


def _used_names() -> set[str]:
    """Every identifier the repository reads, calls, decorates with or imports.

    A definition contributes nothing: ``ast.FunctionDef`` carries its name as a
    plain string rather than an ``ast.Name``, so a helper that only defines
    itself never appears here. That is the whole trick.
    """
    used: set[str] = set()
    for directory in SEARCHED:
        for path in (REPO_ROOT / directory).rglob("*.py"):
            for node in ast.walk(_parse(path)):
                if isinstance(node, ast.Name):
                    used.add(node.id)
                elif isinstance(node, ast.Attribute):
                    used.add(node.attr)
                elif isinstance(node, ast.ImportFrom):
                    used.update(alias.asname or alias.name for alias in node.names)
    return used


def test_no_private_helper_in_the_package_is_unreachable():
    """The lint itself: every private module-level name has a caller."""
    used = _used_names()
    dead = [
        f"{location} {name}"
        for name, location in _private_definitions()
        if name not in used
    ]

    assert not dead, (
        f"these private names in src/analysis_service have no caller anywhere in "
        f"the repository: {dead}. A private name no one calls is dead — delete "
        f"it. If it is a seam something reaches by string, give it a caller a "
        f"reader can follow."
    )


def test_the_lint_covers_a_real_population():
    """Guards the guard: a scan over zero names would pass vacuously."""
    assert _private_definitions(), (
        "no module-level private name found in src/analysis_service — the lint "
        "covers nothing"
    )
