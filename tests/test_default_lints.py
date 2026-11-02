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
- **The ``__init__`` of a module-level class** is matched through the calls
  of its class, which the imports resolve as they resolve a module-level
  function, and through each ``cls(...)`` and ``type(self)(...)`` call in the
  class's own body. A class that code reads as a value, or that another class
  names as a base, has construction calls that the tree does not show, so the
  lint does not judge its ``__init__``. A read of the class in an annotation,
  in an ``except`` clause, as the owner of an attribute, or in ``isinstance``
  or ``issubclass`` constructs nothing, so it is no such value read. A class
  that a ``getattr`` string names is not judged either. The lint
  does not see ``self.__class__(...)``, ``type(other)(...)`` or
  ``dataclasses.replace`` as a construction call, so a class built only those
  ways can be a false hit.

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
from typing import Literal, TypeGuard

import pytest

from tests.source_tree import (
    DEFINITIONS,
    PYTHON,
    REPO_ROOT,
    SHORTEST_REASON,
    imports,
    module_name,
    parse,
    source_files,
)

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
    (
        "evals.experiments.QA-2026-11-02-05.capture_turn",
        "Recording.generate_content_async",
        "stream",
    ): _ADK_OVERRIDE,
    ("evals.harness.modes", "SignedFactsLlm.generate_content_async", "stream"): (
        _ADK_OVERRIDE
    ),
}

#: How the lint finds the calls of a function: by its dotted name, by an
#: attribute call of its name, through its class, or by a plain call in its
#: module.
Kind = Literal["function", "method", "init", "nested"]

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
    kind: Kind
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


@cache
def production() -> dict[str, ast.Module]:
    """Each module of the Python the project runs, by its dotted name."""
    return {module_name(path): parse(path) for path in source_files(*PYTHON)}


class Resolver:
    """The dotted name each expression reads, through the imports of its module."""

    def __init__(self, modules: Mapping[str, ast.Module]) -> None:
        self._aliases = {module: imports(tree) for module, tree in modules.items()}
        self._top_level = {
            module: {node.name for node in tree.body if isinstance(node, DEFINITIONS)}
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
    #: The dotted names that code reads as a value outside an annotation, an
    #: ``except`` clause, an attribute's owner and a type test. A class among
    #: them, or named as a base, has construction calls the tree may not show.
    opaque_values: set[str] = field(default_factory=set)


#: The calls whose arguments a class may be read in without a construction.
_TYPE_TESTS: frozenset[Literal["isinstance", "issubclass"]] = frozenset(
    {"isinstance", "issubclass"}
)


def _reads_that_build_nothing(tree: ast.Module) -> set[int]:
    """The id of each node in ``tree`` that reads a class without building one:
    an annotation, a return annotation, an ``except`` clause, the owner of an
    attribute, and an argument of a type test."""
    builds_nothing: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.arg | ast.AnnAssign) and node.annotation:
            builds_nothing.update(map(id, ast.walk(node.annotation)))
        elif isinstance(node, _FUNCTIONS) and node.returns:
            builds_nothing.update(map(id, ast.walk(node.returns)))
        elif isinstance(node, ast.ExceptHandler) and node.type:
            builds_nothing.update(map(id, ast.walk(node.type)))
        elif isinstance(node, ast.Attribute):
            builds_nothing.add(id(node.value))
        elif isinstance(node, ast.Call) and _called_name(node) in _TYPE_TESTS:
            builds_nothing.update(
                id(part) for arg in node.args for part in ast.walk(arg)
            )
    return builds_nothing


def _called_name(node: ast.Call) -> str | None:
    """The name a call calls: a plain name or an attribute, else None."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    return func.attr if isinstance(func, ast.Attribute) else None


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
        for cls in tree.body:
            if isinstance(cls, ast.ClassDef):
                found.calls[f"{module}.{cls.name}"].extend(
                    _call_shape(node)
                    for node in _own_nodes(cls)
                    if _builds_its_own_type(node)
                )
        builds_nothing = _reads_that_build_nothing(tree)
        for node in ast.walk(tree):
            if id(node) in called or not isinstance(node, ast.Name | ast.Attribute):
                continue
            if isinstance(node.ctx, ast.Store):
                continue
            found.bare_values.add(node.id if isinstance(node, ast.Name) else node.attr)
            if (target := resolver.resolve(node, module)) is not None:
                found.values.add(target)
                if id(node) not in builds_nothing:
                    found.opaque_values.add(target)
    return found


def _call_shape(node: ast.Call) -> Call:
    """What one call gives, as the lint matches it."""
    return Call(
        positional=sum(not isinstance(a, ast.Starred) for a in node.args),
        keywords=frozenset(k.arg for k in node.keywords if k.arg),
        splat=any(isinstance(a, ast.Starred) for a in node.args)
        or any(k.arg is None for k in node.keywords),
    )


def _own_nodes(node: ast.AST) -> Iterator[ast.AST]:
    """Each node under ``node``, outside the classes nested in it."""
    for child in ast.iter_child_nodes(node):
        yield child
        if not isinstance(child, ast.ClassDef):
            yield from _own_nodes(child)


def _builds_its_own_type(node: ast.AST) -> TypeGuard[ast.Call]:
    """Whether ``node`` is a ``cls(...)`` or a ``type(self)(...)`` call."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (isinstance(func, ast.Name) and func.id == "cls") or (
        isinstance(func, ast.Call)
        and _called_name(func) == "type"
        and len(func.args) == 1
        and isinstance(func.args[0], ast.Name)
        and func.args[0].id == "self"
    )


def _record_call(node: ast.Call, module: str, resolver: Resolver, found: Uses) -> None:
    """File one call under each way the lint can match it."""
    call = _call_shape(node)
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

    def visit(node: ast.AST, owner: str, kind: Kind) -> Iterator[Judged]:
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
                own_kind = _kind(child.name, owner, kind)
                if parameters and decorators <= _PLAIN_DECORATORS and own_kind:
                    yield Judged(
                        module,
                        f"{owner}{child.name}",
                        own_kind,
                        child.lineno,
                        parameters,
                    )
                yield from visit(child, f"{owner}{child.name}.", "nested")

    yield from visit(tree, "", "function")


def _kind(name: str, owner: str, kind: Kind) -> Kind | None:
    """How the lint finds the calls of a function, or None where it does not
    judge it: a dunder other than the ``__init__`` of a module-level class."""
    if not name.startswith("__"):
        return kind
    top_level = kind == "method" and owner.count(".") == 1
    return "init" if name == "__init__" and top_level else None


def calls_of(function: Judged, found: Uses) -> list[Call] | None:
    """Each call of ``function``, or None where code also reads it as a value."""
    if function.kind == "init":
        owner = function.qualified.split(".")[0]
        dotted = f"{function.module}.{owner}"
        opaque = dotted in found.opaque_values or owner in found.lookups
        return None if opaque else found.calls[dotted]
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
        "class Built:\n"
        "    def __init__(self, a, *, b=1): ...\n"
        "    def named(self) -> 'Built': ...\n"
        "class Rebuilt:\n"
        "    def __init__(self, a, b=1): ...\n"
        "    @classmethod\n"
        "    def again(cls): return cls(1)\n"
        "class Handed:\n"
        "    def __init__(self, a, b=1): ...\n"
        "class Based:\n"
        "    def __init__(self, a, b=1): ...\n"
        "class Derived(Based): ...\n"
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
        "lib.Built(1, b=2)\n"
        "if isinstance(box, lib.Built): pass\n"
        "lib.Rebuilt(1, 2)\n"
        "factory = lib.Handed\n"
        "lib.Handed(1, 2)\n"
        "lib.Based(1, 2)\n"
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

    ``Built`` is a class that every call passes ``b``, and a type test and an
    annotation of it build nothing. ``Rebuilt`` builds itself with ``cls(1)``,
    ``Handed`` is read as a value, and ``Based`` is a base of another class.
    """
    modules = {module: ast.parse(text) for module, text in _CONTROL.items()}

    assert set(unread_defaults(modules)) == {
        ("pkg.lib", "unread", "b"),
        ("pkg.lib", "Box.put", "where"),
        ("pkg.lib", "outer.inner", "b"),
        ("pkg.lib", "Built.__init__", "b"),
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
