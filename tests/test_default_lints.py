"""A parameter default that no production call reads is refused.

A default is a value for the calls that omit the argument. When each call in
the Python the project runs passes the argument, only a test reads the
default. Such a test runs a configuration that production never runs, so the
suite can pass on a path that production never takes. Make the parameter
required, and give each test call the value that production passes.

The lint reads each function under :data:`~tests.source_tree.PYTHON` and each
call to it there:

- **A module-level function** is found through the imports of each file, with
  each re-export of a package followed to its definition. So
  ``comparison.write(root)`` is a call of ``evals.harness.comparison.write``,
  and ``path.write(text)`` is not.
- **A method** is matched by its name against each attribute call, because the
  type of a receiver is not in the tree. An unrelated call that omits the
  argument hides a hit, and it never adds one.
- **A nested function** is matched by its name against each plain call in its
  module.

A function that production also reads as a value, such as an argument to
``functools.partial``, an entry of a table or a ``getattr`` string, has callers
that the tree does not show, so the lint does not judge it. A call with a
``*`` or ``**`` argument may omit any parameter it does not name. A function
with a decorator that a framework reads is out of scope for the same reason.
A function that no production code calls is the subject of
``tests/test_dead_code_lints.py``.

:data:`KEPT_DEFAULTS` holds each default that code outside the tree reads,
with the reason.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import pytest

from tests.source_tree import PYTHON, REPO_ROOT, SHORTEST_REASON, parse, source_files

pytestmark = pytest.mark.lint

#: The decorators that leave a function one that code calls by name. Any
#: other decorator can give the function to a framework that calls it.
_PLAIN_DECORATORS = frozenset(
    {
        "asynccontextmanager",
        "cache",
        "classmethod",
        "contextmanager",
        "lru_cache",
        "override",
        "staticmethod",
    }
)

#: The calls whose string argument names an attribute to read.
_LOOKUPS = frozenset({"attrgetter", "getattr", "hasattr", "methodcaller"})

_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)

_ADK_OVERRIDE = (
    "an override of BaseLlm.generate_content_async, whose signature declares"
    " stream=False and which the ADK runner calls"
)

#: Each default that code outside the tree reads, keyed by
#: ``(module, function, parameter)``, with the reason. ``function`` is
#: ``Class.method`` for a method, with the enclosing function first for a
#: class built inside one.
KEPT_DEFAULTS: dict[tuple[str, str, str], str] = {
    ("analysis_service.engine", "Engine.analyze_sync", "system_name"): (
        "the in-process API that an integrator's code calls, where a report"
        " with no system name is a supported call"
    ),
    (
        "analysis_service.charges",
        "charge_reporting_llm_class.ChargeReportingLlm.generate_content_async",
        "stream",
    ): _ADK_OVERRIDE,
    (
        "analysis_service.prompt_cache",
        "cache_write_reporting_llm_class.CacheWriteReportingLlm.generate_content_async",
        "stream",
    ): _ADK_OVERRIDE,
    (
        "analysis_service.provider",
        "ExecutedLlm.generate_content_async",
        "stream",
    ): _ADK_OVERRIDE,
    ("evals.harness.modes", "SignedFactsLlm.generate_content_async", "stream"): (
        _ADK_OVERRIDE
    ),
}

#: A key of :data:`KEPT_DEFAULTS`: the module, the function and the parameter.
Key = tuple[str, str, str]


@dataclass(frozen=True)
class Defaulted:
    """One parameter with a default."""

    parameter: str
    #: The parameter's index among the positional arguments a call gives, or
    #: None for a keyword-only parameter.
    index: int | None


@dataclass(frozen=True)
class Judged:
    """One function the lint judges, and how its calls are found."""

    module: str
    #: ``Class.method`` for a method, ``outer.inner`` for a nested function.
    qualified: str
    kind: str  # "function", "method" or "nested"
    line: int
    parameters: tuple[Defaulted, ...]

    @property
    def name(self) -> str:
        return self.qualified.rsplit(".", 1)[-1]


@dataclass(frozen=True)
class Call:
    """What one call gives: its positional count, its keywords and its splats."""

    positional: int
    keywords: frozenset[str]
    splat: bool

    def binds(self, parameter: Defaulted) -> bool:
        """Whether this call surely passes ``parameter``."""
        if parameter.parameter in self.keywords:
            return True
        return (
            not self.splat
            and parameter.index is not None
            and self.positional > parameter.index
        )


def module_name(path: Path) -> str:
    """The dotted name that an import gives the module at ``path``."""
    parts = list(path.relative_to(REPO_ROOT).with_suffix("").parts)
    if parts[0] == "src":
        parts = parts[1:]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


@cache
def production() -> dict[str, ast.Module]:
    """Each module of the Python the project runs, by its dotted name."""
    return {module_name(path): parse(path) for path in source_files(*PYTHON)}


def _imports(tree: ast.Module) -> dict[str, str]:
    """Each local name an import in ``tree`` binds, with its dotted target."""
    bound: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                head = alias.name.split(".")[0]
                bound[alias.asname or head] = alias.name if alias.asname else head
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                bound[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return bound


class Resolver:
    """The dotted name each expression reads, through the imports of its module."""

    def __init__(self, modules: Mapping[str, ast.Module]) -> None:
        self._aliases = {module: _imports(tree) for module, tree in modules.items()}
        self._top_level = {
            module: {node.name for node in tree.body if isinstance(node, _FUNCTIONS)}
            for module, tree in modules.items()
        }

    def _canonical(self, dotted: str) -> str:
        """``dotted`` with each re-export followed to the module that defines it."""
        seen: set[str] = set()
        while dotted not in seen:
            seen.add(dotted)
            module, _, name = dotted.rpartition(".")
            if name in self._top_level.get(module, ()):
                return dotted
            target = self._aliases.get(module, {}).get(name)
            if target is None:
                return dotted
            dotted = target
        return dotted

    def resolve(self, node: ast.expr, module: str) -> str | None:
        """The dotted name ``node`` reads in ``module``, or None for a value."""
        if isinstance(node, ast.Name):
            if node.id in self._top_level.get(module, ()):
                return f"{module}.{node.id}"
            target = self._aliases.get(module, {}).get(node.id)
            return None if target is None else self._canonical(target)
        if isinstance(node, ast.Attribute):
            owner = self.resolve(node.value, module)
            return None if owner is None else self._canonical(f"{owner}.{node.attr}")
        return None


@dataclass
class Uses:
    """Each call and each value read of a function, by how the lint matches it."""

    #: Calls of a module-level function, by its dotted name.
    calls: defaultdict[str, list[Call]] = field(
        default_factory=lambda: defaultdict(list)
    )
    #: Plain calls by bare name, per module, for a nested function.
    plain_calls: defaultdict[tuple[str, str], list[Call]] = field(
        default_factory=lambda: defaultdict(list)
    )
    #: Attribute calls by attribute name, for a method.
    attribute_calls: defaultdict[str, list[Call]] = field(
        default_factory=lambda: defaultdict(list)
    )
    #: The dotted names that code reads as a value.
    values: set[str] = field(default_factory=set)
    #: The bare names that code reads as a value.
    bare_values: set[str] = field(default_factory=set)
    #: The names that code looks up by a string.
    lookups: set[str] = field(default_factory=set)


def uses(modules: Mapping[str, ast.Module]) -> Uses:
    """Each call and each value read in ``modules``."""
    resolver = Resolver(modules)
    found = Uses()
    for module, tree in modules.items():
        called: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                called.add(id(node.func))
                _record_call(node, module, resolver, found)
        for node in ast.walk(tree):
            if id(node) in called or not isinstance(node, ast.Name | ast.Attribute):
                continue
            if isinstance(node.ctx, ast.Store):
                continue
            found.bare_values.add(node.id if isinstance(node, ast.Name) else node.attr)
            if (target := resolver.resolve(node, module)) is not None:
                found.values.add(target)
    return found


def _record_call(node: ast.Call, module: str, resolver: Resolver, found: Uses) -> None:
    """File one call under each way the lint can match it."""
    call = Call(
        positional=sum(not isinstance(a, ast.Starred) for a in node.args),
        keywords=frozenset(k.arg for k in node.keywords if k.arg),
        splat=any(isinstance(a, ast.Starred) for a in node.args)
        or any(k.arg is None for k in node.keywords),
    )
    if (target := resolver.resolve(node.func, module)) is not None:
        found.calls[target].append(call)
    if isinstance(node.func, ast.Name):
        found.plain_calls[(module, node.func.id)].append(call)
        name = node.func.id
    elif isinstance(node.func, ast.Attribute):
        found.attribute_calls[node.func.attr].append(call)
        name = node.func.attr
    else:
        return
    if name in _LOOKUPS:
        found.lookups.update(
            a.value
            for a in node.args
            if isinstance(a, ast.Constant) and isinstance(a.value, str)
        )


def _defaulted(
    node: ast.FunctionDef | ast.AsyncFunctionDef, *, bound: bool
) -> tuple[Defaulted, ...]:
    """Each defaulted parameter of ``node``.

    ``bound`` drops the first positional parameter, which a method call gives
    through its receiver.
    """
    positional = [*node.args.posonlyargs, *node.args.args][int(bound) :]
    first = len(positional) - len(node.args.defaults)
    return (
        *(
            Defaulted(argument.arg, index)
            for index, argument in enumerate(positional)
            if index >= first
        ),
        *(
            Defaulted(argument.arg, None)
            for argument, default in zip(
                node.args.kwonlyargs, node.args.kw_defaults, strict=True
            )
            if default is not None
        ),
    )


def judged(module: str, tree: ast.Module) -> Iterator[Judged]:
    """Each function in ``tree`` that has a default and no framework decorator."""

    def visit(node: ast.AST, owner: str, kind: str) -> Iterator[Judged]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                yield from visit(child, f"{owner}{child.name}.", "method")
            elif isinstance(child, _FUNCTIONS):
                decorators = {
                    ast.unparse(decorator).split("(")[0].rsplit(".", 1)[-1]
                    for decorator in child.decorator_list
                }
                bound = kind == "method" and "staticmethod" not in decorators
                parameters = _defaulted(child, bound=bound)
                if (
                    parameters
                    and decorators <= _PLAIN_DECORATORS
                    and not child.name.startswith("__")
                ):
                    yield Judged(
                        module, f"{owner}{child.name}", kind, child.lineno, parameters
                    )
                yield from visit(child, f"{owner}{child.name}.", "nested")

    yield from visit(tree, "", "function")


def calls_of(function: Judged, found: Uses) -> list[Call] | None:
    """Each call of ``function``, or None where code also reads it as a value."""
    if function.name in found.lookups:
        return None
    if function.kind == "function":
        dotted = f"{function.module}.{function.qualified}"
        return None if dotted in found.values else found.calls[dotted]
    if function.name in found.bare_values:
        return None
    if function.kind == "method":
        return found.attribute_calls[function.name]
    return found.plain_calls[(function.module, function.name)]


def unread_defaults(modules: Mapping[str, ast.Module]) -> dict[Key, int]:
    """Each default in ``modules`` that no call there reads, with its line."""
    found = uses(modules)
    unread: dict[Key, int] = {}
    for module, tree in modules.items():
        for function in judged(module, tree):
            calls = calls_of(function, found)
            if not calls:
                continue
            for parameter in function.parameters:
                if all(call.binds(parameter) for call in calls):
                    key = (module, function.qualified, parameter.parameter)
                    unread[key] = function.line
    return unread


@cache
def production_unread() -> dict[Key, int]:
    """:func:`unread_defaults` over :func:`production`, read once."""
    return unread_defaults(production())


def _place(key: Key, line: int) -> str:
    path = next(path for path in source_files(*PYTHON) if module_name(path) == key[0])
    return f"{path.relative_to(REPO_ROOT)}:{line} {key[1]}({key[2]})"


def test_every_default_has_a_production_call_that_reads_it():
    """The lint itself: no production call passes every defaulted argument."""
    unread = {
        key: line
        for key, line in production_unread().items()
        if key not in KEPT_DEFAULTS
    }

    assert not unread, (
        "each call in src, webapp, evals, examples and .github/scripts passes"
        f" these arguments, so only a test reads the default:"
        f" {[_place(key, line) for key, line in unread.items()]}. Make each"
        " parameter required and pass production's value at each test call. If"
        " code outside the tree reads the default, add it to KEPT_DEFAULTS"
        " with the reason."
    )


@pytest.mark.parametrize("key", sorted(KEPT_DEFAULTS), ids="/".join)
def test_a_kept_default_is_still_a_default_no_production_call_reads(key):
    """An entry whose parameter lost its default, or gained a production call
    that omits it, is a stale reason."""
    module, qualified, parameter = key
    defaults = {
        (function.qualified, defaulted.parameter)
        for function in judged(module, production()[module])
        for defaulted in function.parameters
    }

    assert (qualified, parameter) in defaults, (
        f"{key} is in KEPT_DEFAULTS, but the tree holds no such defaulted"
        " parameter; drop the entry"
    )
    assert key in production_unread(), (
        f"{key} is in KEPT_DEFAULTS, but a production call now omits it, so the"
        " lint does not report it; drop the entry"
    )


@pytest.mark.parametrize("key", sorted(KEPT_DEFAULTS), ids="/".join)
def test_a_kept_default_states_a_reason(key):
    assert len(KEPT_DEFAULTS[key]) >= SHORTEST_REASON, f"state why {key} stays"


#: A package and a caller of it, with one case of each way a call is matched.
_CONTROL = {
    "pkg": "from pkg.lib import unread\n",
    "pkg.lib": (
        "def unread(a, b=1): ...\n"
        "def omitted(a, b=1): ...\n"
        "def as_value(a, b=1): ...\n"
        "def by_string(a, b=1): ...\n"
        "def splatted(a, b=1): ...\n"
        "class Box:\n"
        "    def put(self, item, *, where='top'): ...\n"
        "    def take(self, n=1): ...\n"
        "def outer():\n"
        "    def inner(a, b=1): ...\n"
        "    inner(1, 2)\n"
    ),
    "app": (
        "import pkg\n"
        "from pkg import lib\n"
        "from pkg.lib import omitted as dropped\n"
        "pkg.unread(1, b=2)\n"
        "out.unread(1)\n"
        "lib.omitted(1, 2)\n"
        "dropped(1)\n"
        "handler = lib.as_value\n"
        "lib.as_value(1, b=2)\n"
        "getattr(lib, 'by_string')(1)\n"
        "lib.by_string(1, 2)\n"
        "lib.splatted(*rest)\n"
        "lib.splatted(1, b=2)\n"
        "box.put(1, where='bottom')\n"
        "box.take(2)\n"
        "queue.take()\n"
    ),
}


def test_the_lint_reports_a_default_every_call_passes():
    """Positive control: two defaults that every call passes are reported, and
    each other shape is passed.

    ``unread`` is called through the re-export of its package, and an
    unrelated ``out.unread`` call does not reach it. ``put`` is a method that
    every attribute call of its name passes. ``omitted`` has an alias call that
    omits the argument, ``as_value`` is read as a value, ``by_string`` is read
    by a ``getattr`` string, ``splatted`` has a call with a splat, ``take``
    shares its name with a call that omits the argument, and ``inner`` is a
    nested function that its one call passes.
    """
    modules = {module: ast.parse(text) for module, text in _CONTROL.items()}

    assert set(unread_defaults(modules)) == {
        ("pkg.lib", "unread", "b"),
        ("pkg.lib", "Box.put", "where"),
        ("pkg.lib", "outer.inner", "b"),
    }


def test_the_lint_covers_a_real_population():
    """Guards the guard: a scan over too few functions would pass vacuously."""
    found = uses(production())
    judged_calls = [
        calls
        for module, tree in production().items()
        for function in judged(module, tree)
        if (calls := calls_of(function, found))
    ]

    assert len(judged_calls) > 100, (
        f"only {len(judged_calls)} defaulted functions have a production call;"
        " the lint covers too little to be doing its job"
    )
