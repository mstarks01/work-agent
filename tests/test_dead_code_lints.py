"""Ruff has no rule for a name nobody calls, so this is that rule.

A private uncalled function passes ruff and mypy for its whole life in the
tree: nothing outside the package can reach it, and nothing inside it does
either. That leaves a name-level
scan as the only thing that finds them.

A text scan is not enough and a dependency is not wanted, matching
``tests/test_workflow_lints.py``'s reasoning about PyYAML. Vulture is the
obvious dependency and it does not fit: this package is pydantic and FastAPI
throughout, so vulture reports every validator, route handler and
``model_post_init`` as unused. A lint whose output an author must filter by
hand is a lint an author stops reading.

So the scan is ``ast`` over the names themselves, and it is narrow in one way
and wide in three others.

**A decorated method stays out of scope.** A framework calls it by name --
every ``@field_validator`` and ``@model_validator`` in the tree is defined and
never called -- and that is the false-positive class above. A method with no
decorator, or only ``@classmethod`` or ``@staticmethod``, is called by code, so
a third lint holds it: its name must be used somewhere. By name, not by class,
because an override is a definition and not a use. ``Claim.rating_of`` and its
STRIDE override lost their one caller and passed both earlier lints.

**Subpackages are in scope.** The scan reads the package recursively, because
``frameworks/`` is where a package's own helpers live.

**Public names are in scope.** The service's API is what its package root
exports: ``src/analysis_service/__init__.py`` imports each name in its
``__all__``, and that import is a use, so an exported name is never reported.
Every other public name is judged like a private one, in the service as in
``evals/`` and ``webapp/``. A public name in a submodule that only a test
reaches is not API, because no integrator imports it from the package root.
:data:`NAMED_BY_A_LIBRARY` holds the files whose names a library reads by name.

**A local is not a use.** A parameter, a local variable or a comprehension
target that shares a module-level name hides that name inside its scope, so a
read there is not a use of it, and neither is a keyword argument. An attribute
read is matched by its identifier, because the scan cannot tell
``module.name`` from ``obj.name``: an attribute read that shares the name of a
dead function still hides it.

**A dead cluster is dead.** A map that only a dead function reads looks
referenced, but the code that reads it is itself dead.
One pass cannot see that, so the scan iterates: it drops what it has already
proved dead and asks again, until the answer stops changing. A chain of any
length falls in that many passes.

A name is used if anything reaches it, tests included. The second lint asks the
narrower question: which names does nothing but a test reach? Such a name is
not dead, but nothing ships it either, and each one is a decision -- an
instrument the tests pin so a figure cannot rot, or a floor a rule is priced
against. :data:`TEST_ONLY` holds every one with its reason, and a name that
gains a reader outside the tests, or leaves the tree, fails the table.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from functools import cache
from pathlib import Path

import pytest

from tests.source_tree import PYTHON, REPO_ROOT, TESTS, parse, source_files

pytestmark = pytest.mark.lint

#: Every file that may legitimately reach a name, including the tests.
SEARCHED = source_files(*PYTHON, *TESTS)

#: Where definitions are looked for: the Python the project runs.
SCOPES = source_files(*PYTHON)

#: Directories under :data:`SCOPES` whose module-level names a library reads by
#: name, with the reason. No code in the tree calls these names, so the lints
#: do not judge them.
NAMED_BY_A_LIBRARY: dict[str, str] = {
    "src/analysis_service/migrations/scripts/versions/": (
        "Alembic loads each revision module and reads revision, down_revision,"
        " branch_labels, depends_on, upgrade and downgrade by name"
    ),
}

#: Where a use counts for the second lint: every searched file but the tests.
PRODUCTION = SCOPES

_PINNED = (
    "a measurement the tests pin, so the figure in the module docstring is"
    " regenerated on every pull request rather than asserted in prose"
)

#: Names only a test reaches, kept on purpose, one reason each. State the reason
#: as a property of the name, never as a ticket number. Delete the name and its
#: tests instead when the reason is only that the tests exist.
TEST_ONLY: dict[str, str] = {
    "measure_direction": (
        "the one aggregation ADR 0031 pins its direction numbers with, and the"
        " instrument a later candidate rule is priced through"
    ),
    "DirectionResult": "what measure_direction returns",
    "is_stale": (
        "backs the gate that holds evals/baselines/README.md to the merged Baselines"
    ),
    "MechanicalIdentity": (
        "version 1 of the identity rule, the floor docs/agents/claim-identity.md"
        " prices the frontier against"
    ),
    "corpus_recall": f"{_PINNED}: trigger recall over the whole corpus",
    "by_framework": f"{_PINNED}: the trigger overlap of each framework",
    "kind_of": (
        "the reader the archive encoding lint classifies every sealed file"
        " with, so a committed file whose producer nobody declared fails there"
    ),
    "KIND_NAMES": "the whole-name half of what kind_of reads",
    "KIND_SUFFIXES": "the suffix half of what kind_of reads",
    "UNREVIEWED_FILE": (
        "where the UNREVIEWED table lives, so the test that reads the table"
        " and the sitting that names it cannot point at two different files"
    ),
    "DIRTY_MARKER": (
        "the marker a dirty checkout appends to a configuration label, named"
        " so the test pins the spelling the rule table produces"
    ),
    "ARCHIVED_UNRECONCILED_KINDS": (
        "the unreconciled kinds only an archived report carries, so the test"
        " that every kind has a producing check can tell a kind no check writes"
        " on purpose from a hole"
    ),
    "COMPOSED_ANALYZE_CAP": (
        "the cap the prompt lint holds one lane's whole composed instruction"
        " to, the sum of its three part caps so it cannot bind before a part"
    ),
    "COMPOSED_EXTRACT_COMPACT_CAP": (
        "the cap the prompt lint holds the compact extraction instruction to,"
        " the sum of its two part caps"
    ),
    "covered_assets": (
        "the walk the skill lint uses to give every registered package file a"
        " TOKEN_CAPS key, so a file with no cap raises"
    ),
    "JOB_VARYING_DIRS": (
        "the package directories covered_assets leaves out, because their"
        " files ride in the job-varying block"
    ),
    "_package_key": "the TOKEN_CAPS key covered_assets gives one package file",
    "CONTENT_LICENSE": (
        "the licence of each package's text, which the licence lints hold"
        " against PACKAGES, NOTICE and the third-party register"
    ),
    "widest_fan_out": (
        "the lane count one job fires at once, which the fan-out and doc"
        " figure lints derive from PACKAGES so no stated count goes stale"
    ),
    "PROMPT_SECTION_HEADINGS": (
        "the four H2 sections the prompt lints require of every prompt body"
    ),
    "PROMPT_BODY_NAMES": (
        "the prompt bodies the prompt lints check for sections, size and"
        " content, and against the files under prompts/"
    ),
    "PROMPT_DELTA_NAMES": (
        "the prompt parts the prompt lints check for size and content but not"
        " for sections, because a delta has none"
    ),
    "REVIEWED_BY": (
        "who reviewed each ASVS applicability row, which the applicability test"
        " holds to one entry per row"
    ),
    "ROLES": (
        "every per-framework role, which the graph tests check against the"
        " declared role constants and the node name each role gives"
    ),
    "default_base_ref": (
        "the ref the repo-wide Baseline walk measures ancestry against:"
        " origin/main where the clone has a remote, as in CI, and main where it"
        " has none"
    ),
    "menu": (
        "the verb vocabulary as prompt text, which the prompt lint compares"
        " line by line with the copy in frameworks/stride/output.md"
    ),
    # evals/harness/exemplar_verbs.py, whole: the verbs the exemplars demonstrate
    # against the verbs the corpus grades, which both move often.
    **dict.fromkeys(
        (
            "Collision",
            "Exemplar",
            "LaneVerbs",
            "Undemonstrated",
            "proposal_type",
            "collisions",
            "corpus_undemonstrated",
            "lane_verbs",
            "undemonstrated",
            "verb_keyed_frameworks",
            "_JSON_BLOCK",
        ),
        _PINNED,
    ),
}

_DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

#: The assignment shapes a module-level constant takes, judged beside the
#: definitions above. A constant is a fact like any other and needs a reader
#: like any other -- and a lint that looked only at ``def`` and ``class`` left
#: the whole shape unchecked. Five sat in that gap: a prefix nothing composed,
#: a field name the code read as a literal, a tuple beside the ``Literal`` that
#: already declared it, a rank map built from a tuple nobody indexed, and a
#: version nothing stamped.
_ASSIGNMENTS = (ast.Assign, ast.AnnAssign)


def _assigned_names(node: ast.stmt) -> list[str]:
    """The module-level names one assignment binds, ignoring every other target."""
    if isinstance(node, ast.Assign):
        return [t.id for t in node.targets if isinstance(t, ast.Name)]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        # An annotation with no value declares a type and binds nothing, so it
        # is not a fact anybody could read.
        return [node.target.id] if node.value is not None else []
    return []


def _definitions() -> dict[str, str]:
    """Every module-level name this lint judges, with where it is defined.

    A dunder is excluded: ``__getattr__`` and friends are called by the
    interpreter, which is the same reason methods are out of scope.
    """
    found: dict[str, str] = {}
    for path in SCOPES:
        where = path.relative_to(REPO_ROOT).as_posix()
        if where.startswith(tuple(NAMED_BY_A_LIBRARY)):
            continue
        for node in parse(path).body:
            if isinstance(node, _DEFINITIONS):
                names = [node.name]
            elif isinstance(node, _ASSIGNMENTS):
                names = _assigned_names(node)
            else:
                continue
            for name in names:
                if not name.startswith("__"):
                    found[name] = f"{where}:{node.lineno}"
    return found


_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _parameters(args: ast.arguments) -> set[str]:
    """The names a function or a lambda takes."""
    every = (*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg)
    return {arg.arg for arg in every if arg is not None}


@cache
def _locals(function: ast.FunctionDef | ast.AsyncFunctionDef) -> frozenset[str]:
    """The names ``function`` binds in its own scope: its parameters and each
    name its body binds, less each name it declares ``global``.

    A read of one of these names inside the function reads the local, never
    the module-level name it shares.
    """
    bound = _parameters(function.args)
    declared_global: set[str] = set()
    pending: list[ast.AST] = list(function.body)
    while pending:
        node = pending.pop()
        if isinstance(node, (*_FUNCTIONS, ast.ClassDef)):
            bound.add(node.name)
            continue
        if isinstance(node, (ast.Lambda, *_COMPREHENSIONS)):
            continue
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            bound.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            bound.update(
                alias.asname or alias.name.split(".")[0] for alias in node.names
            )
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)):
            if node.name:
                bound.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            bound.add(node.rest)
        elif isinstance(node, ast.Global):
            declared_global.update(node.names)
        pending.extend(ast.iter_child_nodes(node))
    return frozenset(bound - declared_global)


def _comprehension_locals(
    node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
) -> set[str]:
    """The names the ``for`` targets of a comprehension bind."""
    return {
        name.id
        for generator in node.generators
        for name in ast.walk(generator.target)
        if isinstance(name, ast.Name)
    }


def _names_read(tree: ast.AST, ignoring: frozenset[str] = frozenset()) -> set[str]:
    """Every module-level name ``tree`` reads, calls, decorates with or imports.

    A definition contributes nothing: ``ast.FunctionDef`` carries its name as a
    plain string rather than an ``ast.Name``, so a helper that only defines
    itself never appears here. That is the whole trick.

    **A binding is not a use, which is what makes the trick hold for a
    constant.** An assignment target *is* an ``ast.Name``, so counting every
    ``Name`` would let ``X = 1`` reach itself and no constant could ever read
    as dead. Store context is excluded for that reason, and an augmented
    assignment is put back by hand: ``X += 1`` binds and reads at one target.

    **A local is not a use.** A parameter, a local variable or a
    comprehension target that shares a module-level name hides that name
    inside its scope, so a read there reads the local. A keyword argument is
    a string on its ``ast.keyword``, never a ``Name``. An attribute read is
    matched by its identifier, because ``module.name`` reads the module's
    name and the scan cannot tell a module from another object.

    ``ignoring`` names definitions already proved dead. Their bodies are skipped
    entirely, so a name only they reach stops looking reached -- which is what
    finds a helper kept alive by its one dead caller.
    """
    used: set[str] = set()

    def visit(node: ast.AST, hidden: frozenset[str]) -> None:
        if isinstance(node, _DEFINITIONS) and node.name in ignoring:
            return
        if isinstance(node, _FUNCTIONS):
            for outer in (*node.decorator_list, node.args, node.returns):
                if outer is not None:
                    visit(outer, hidden)
            inner = hidden | _locals(node)
            for statement in node.body:
                visit(statement, inner)
            return
        if isinstance(node, ast.Lambda):
            visit(node.args, hidden)
            visit(node.body, hidden | _parameters(node.args))
            return
        if isinstance(node, _COMPREHENSIONS):
            hidden = hidden | _comprehension_locals(node)
        elif isinstance(node, ast.Name):
            if not isinstance(node.ctx, ast.Store) and node.id not in hidden:
                used.add(node.id)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            if node.target.id not in hidden:
                used.add(node.target.id)
        elif isinstance(node, ast.Attribute):
            if not isinstance(node.ctx, ast.Store):
                used.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            used.update(alias.asname or alias.name for alias in node.names)
        for child in ast.iter_child_nodes(node):
            visit(child, hidden)

    visit(tree, frozenset())
    return used


def _used_names(ignoring: frozenset[str], files: Iterable[Path] = SEARCHED) -> set[str]:
    """Every module-level name the files in ``files`` read, by
    :func:`_names_read`."""
    return set().union(*(_names_read(parse(path), ignoring) for path in files))


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


def test_only() -> dict[str, str]:
    """The names only a test reaches, to a fixed point.

    What :func:`unreachable` reports is left out, so each name is reported by
    one lint. A name reached only by a test-only name is test-only too: the
    ``Sweep`` class that only ``score_sweep`` built fell with it.
    """
    definitions = _definitions()
    dead = frozenset(unreachable())
    found = dead
    while True:
        used = _used_names(ignoring=found, files=PRODUCTION)
        unshipped = frozenset(name for name in definitions if name not in used)
        if unshipped == found:
            return {name: definitions[name] for name in sorted(unshipped - dead)}
        found = unshipped


def test_no_module_level_name_is_unreachable():
    """The lint itself: every name it judges has something that reaches it."""
    dead = unreachable()

    assert not dead, (
        "these names have no caller anywhere in the repository: "
        f"{[f'{location} {name}' for name, location in dead.items()]}. A name"
        " nothing reaches is dead -- delete it. If it is a seam something"
        " reaches by string, give it a caller a reader can follow. If it is"
        " API, export it from the package root's __all__. If a library reads"
        " it by name, add its directory to NAMED_BY_A_LIBRARY."
    )


def test_a_name_only_a_test_reaches_is_declared():
    """The second lint: a name nothing ships needs a stated reason to stay."""
    undeclared = {
        name: location
        for name, location in test_only().items()
        if name not in TEST_ONLY
    }

    assert not undeclared, (
        "only a test reaches these names: "
        f"{[f'{location} {name}' for name, location in undeclared.items()]}."
        " Give each one a reader outside tests/, or delete it with its tests, or"
        " add it to TEST_ONLY with the reason it stays."
    )


def test_no_kept_name_outlives_its_reason():
    """An entry whose name gained a reader, or left the tree, is a stale reason."""
    stale = sorted(set(TEST_ONLY) - set(test_only()))

    assert not stale, (
        f"{stale} are in TEST_ONLY but something outside tests/ now reaches them,"
        " or they are gone; drop the entries"
    )


def test_a_public_service_name_only_a_test_reaches_is_reported():
    """Positive control on the scope: a public name in the service that the
    package root does not export is judged, and one it exports is not."""
    reported = test_only()

    assert reported["ROLES"].startswith("src/analysis_service/graph.py:")
    assert "Deployment" in _definitions() and "Deployment" not in reported


def test_a_use_in_a_test_is_not_a_production_use():
    """Positive control on the roots: a name only this file calls is seen by the
    full walk and not by the production walk."""
    everywhere = _used_names(ignoring=frozenset())
    shipped = _used_names(ignoring=frozenset(), files=PRODUCTION)

    assert shipped <= everywhere
    assert "unreachable" in everywhere and "unreachable" not in shipped


def test_a_local_that_shares_a_name_is_not_a_use():
    """Positive control on the scopes, on the shape that got through:
    ``structural_issues`` had no caller, and a parameter of that name read as
    one.

    A parameter, a local, a comprehension target, a closure's free name and a
    keyword argument read no module-level name. An import, a call, an
    attribute read, a ``global`` name, an annotation and a default do.
    """
    hidden = ast.parse(
        "def payload(case, structural_issues):\n"
        "    return {'issues': list(structural_issues)}\n"
        "def local():\n"
        "    gate = 1\n"
        "    return gate\n"
        "def target():\n"
        "    return [row for row in range(3)]\n"
        "def keyword():\n"
        "    return dict(spare=1)\n"
        "def closure(inner):\n"
        "    return lambda: inner\n"
    )
    read = ast.parse(
        "from m import imported\n"
        "def call():\n"
        "    return called()\n"
        "def attribute(obj):\n"
        "    return obj.attribute_read\n"
        "def declared():\n"
        "    global counter\n"
        "    counter += 1\n"
        "def annotated(value: Annotation = default):\n"
        "    return value\n"
    )

    assert not {"structural_issues", "gate", "row", "spare", "inner"} & _names_read(
        hidden
    )
    assert {
        "imported",
        "called",
        "attribute_read",
        "counter",
        "Annotation",
        "default",
    } <= _names_read(read)


def test_the_lint_covers_a_real_population():
    """Guards the guard: a scan over zero names would pass vacuously."""
    definitions = _definitions()

    assert len(definitions) > 100, (
        f"only {len(definitions)} names in scope -- the lint covers too little"
        " to be doing its job"
    )


def test_the_scan_recurses_into_a_subpackage():
    """A scan over one directory would leave every name under `frameworks/` unread."""
    scanned = {location.rsplit(":", 1)[0] for location in _definitions().values()}

    assert any("frameworks/" in path for path in scanned), (
        "no name under frameworks/ is in scope; the scan has stopped recursing"
    )


def test_a_cluster_dies_together():
    """A name reached only by an already-dead name is dead too.

    The shape: an uncalled function that is the only reader of a map. One
    pass clears the map, because the dead function references it.
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


#: The decorators that leave a method something code calls by name. Any other
#: decorator may hand the method to a framework that calls it instead.
_PLAIN_DECORATORS = frozenset({"classmethod", "staticmethod"})


def _uncalled_methods(tree: ast.Module, used: set[str]) -> list[tuple[str, int]]:
    """Each plain method in ``tree`` whose name ``used`` does not hold."""
    return [
        (f"{node.name}.{method.name}", method.lineno)
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        for method in node.body
        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not method.name.startswith("__")
        and {ast.unparse(d).split("(")[0] for d in method.decorator_list}
        <= _PLAIN_DECORATORS
        and method.name not in used
    ]


def test_no_plain_method_is_unreachable():
    """The third lint: a method code would call has a caller somewhere."""
    used = _used_names(ignoring=frozenset())
    dead = [
        f"{path.relative_to(REPO_ROOT)}:{line} {name}"
        for path in SCOPES
        for name, line in _uncalled_methods(parse(path), used)
    ]

    assert not dead, (
        f"no code calls these methods: {dead}. Delete each one, or give it the"
        " caller it was written for"
    )


def test_an_override_is_not_a_use():
    """Positive control, on the shape that got through: a base method and its
    override, and no call to either."""
    module = ast.parse(
        "class Base:\n"
        "    @classmethod\n"
        "    def rated(cls):\n"
        "        return None\n"
        "class Child(Base):\n"
        "    def rated(self):\n"
        "        return 1\n"
        "    @property\n"
        "    def shown(self):\n"
        "        return 2\n"
    )

    assert [name for name, _ in _uncalled_methods(module, set())] == [
        "Base.rated",
        "Child.rated",
    ]
    assert _uncalled_methods(module, {"rated"}) == []
