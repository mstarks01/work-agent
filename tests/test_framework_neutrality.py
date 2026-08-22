"""Every place outside a package that names a framework, and why.

## What went wrong, and why nothing caught it

The frameworks cutover ([#172](https://github.com/mstarks01/work-agent/issues/172))
made the **service** framework-neutral and stopped at the service boundary.
``src/`` came out clean — every ``stride`` in it today is a module path. Everything
that *grades* the service kept its one-package shape, and when ASVS landed four
days later nothing failed. Eight PRs (#214, #215, #221, #222, #223, #224, #225 and
#210) were needed afterwards to find and fix them one at a time.

**A one-package assumption is vacuously correct when it is written and silently
wrong afterwards.** It does not raise; it reports a smaller, plausible number.
``EVAL_FRAMEWORKS = ("stride",)`` was right when there was one package.
``aggregate_coverage`` keyed by lane name was right while no two packages shared
a lane slug. ``summarize()`` pooling every framework was right with one. None of
them broke on the day a second package arrived — they just kept answering about
half the system, which is why an audit found them and a test suite did not.

## The shape that survived, and the shape that did not

Sorting the fixes by what they touched gives one usable rule:

* **A table keyed by framework was always already correct.**
  :data:`~stride_service.frameworks.PACKAGES`, ``SCHEMAS``,
  ``REFERENCE_TYPES``, and the five maps in ``evals/verify_corpus.py`` all
  needed no change when ASVS landed. A missing key raises ``KeyError`` at the
  first call, so the edit is forced.
* **A constant or a branch naming one framework was always wrong.** Every gap
  found — the eval framework list, ``stride_block`` call sites, the grounds
  fold, stability, the exemplar delta, trigger recall, the knowledge lint — was
  a name or an ``if`` rather than a lookup.

So: **prefer a table keyed by framework over a constant or a branch.** The table
is self-completing; the branch needs somebody to remember.

## What this module does

Names every framework literal left in non-test code, with the reason it is
allowed. A new one fails until it is declared, which makes the parity rule in
``docs/agents/framework-parity.md`` a check rather than a habit for the one part
of it that is mechanically decidable.

**Most test files are out of scope on purpose.** A test naming the package it
is testing is the ordinary case, and linting them all would produce an exemption
list longer than the thing it protects.

**One kind of test is in scope**, and the second half of this module is where.
A *lint* asserts a property of shipped text or config, and those properties are
almost always claims about what an artifact is rather than about which framework
wrote it. #276 and #280 were both a lint scoped to one package, and both passed
while checking half the tree. That set is seven files with an empty exemption
list, so the reasoning above does not reach it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from evals.harness.instruments import INSTRUMENTS, PACKAGE_SCORERS
from stride_service.frameworks import PACKAGES

REPO_ROOT = Path(__file__).resolve().parents[1]
SEARCHED = ("src", "evals")

#: A string literal naming a framework. Import paths and module names are not
#: matched — ``stride_service`` is this distribution's name, not a package
#: selection, and ``frameworks.stride.record`` is a module.
LITERAL = re.compile(r'"(?:stride|asvs)"')

#: Where a framework literal is allowed, and why. Two readings live here and the
#: reason has to say which:
#:
#: **"a table keyed by framework"** — self-completing. A package added to
#: :data:`~stride_service.frameworks.PACKAGES` and missing here raises at the
#: first call, so nothing can be silently half-done.
#:
#: **"this code is that framework's"** — a framework-specific instrument naming
#: its own package. Legitimate, and the thing to check when a third package
#: lands is whether the *dispatch to it* is a table or a branch.
#:
#: An entry that is neither is debt. There are none today; if one appears, say
#: so in the reason rather than filing it beside the legitimate ones.
DECLARED: dict[str, str] = {
    "src/stride_service/report.py": (
        "The registry. `FrameworkName` is the closed type every other module"
        " reads, so this is the one place the names are spelled at all."
    ),
    "src/stride_service/frameworks/__init__.py": (
        "Tables keyed by framework: PACKAGES and SCHEMAS. Self-completing — a"
        " package missing from either raises at `package_for`."
    ),
    "src/stride_service/engine.py": (
        "A module-level demo entry point selecting one framework to run, the"
        " way a caller does. Not a code path any job takes."
    ),
    "evals/harness/reference.py": (
        "REFERENCE_TYPES is a table keyed by framework. `stride_claims()` is a"
        " named accessor for the scorer that grades STRIDE's open claim set;"
        " every neutral reader goes through `references[framework]`."
    ),
    "evals/harness/fingerprint.py": (
        "VERSION_FOR, LANE_FIELD and IDENTIFIER_OF are tables keyed by"
        " framework. Self-completing — a package missing from any of them"
        " raises at `version_for`, `lane_field` or `identifier_of` on its first"
        " finding. VERSION_FOR's entries follow from what a package's claims"
        " are rather than from preference: an open claim set composes an"
        " identity from an action and a place (2), a claim carrying a catalog"
        " identifier is keyed by that identifier and the place (3)."
        " LANE_FIELD's follow from the field the graph stamps the lane in, and"
        " IDENTIFIER_OF's from whether the package owns a catalog to read."
    ),
    "evals/harness/scorer.py": (
        "This code is STRIDE's. It reads a category and two rated severity axes,"
        " which only that package's record carries (#167)."
    ),
    "evals/harness/applicability.py": (
        "This code is ASVS's. It reads the ASVS catalog, and `FRAMEWORK` is"
        " declared once at the closed type rather than spelled per call."
    ),
    "evals/harness/modes.py": (
        "EVAL_FRAMEWORKS is the fallback for a case declaring none; the sweep"
        " reads `case_frameworks()`. `merged_drafts` is a named accessor for"
        " STRIDE's scorer, over the framework-keyed `drafts` map."
    ),
    "evals/harness/instruments.py": (
        "A table keyed by instrument. Each entry declares the packages whose"
        " record it reads, so an instrument that grades one package's claims is"
        " skipped by a sweep that ran another rather than failing inside it."
        " `test_every_package_has_an_instrument` is what keeps the declarations"
        " complete."
    ),
    "evals/harness/run.py": (
        "`stride_block` is a named accessor over the neutral `framework_block`."
        " The scoring pass is that package's, because grading an open claim set"
        " is not a per-case fold; it names the block it grades so a sweep of"
        " another package skips it rather than fails in it. The"
        " per-case mechanical dispatch that used to branch here is now"
        " `PACKAGE_SCORERS`, a table keyed by framework."
    ),
    "evals/build_review_docs.py": (
        "RENDERERS is a table keyed by framework, checked against PACKAGES at"
        " import: a package without a renderer fails the generator loudly"
        " rather than dropping its reference set from a sitting. Each entry"
        " asks the question its package's records rule on."
    ),
    "evals/harness/stability.py": (
        "Reads both artifact blocks by name — `scores` is STRIDE's and"
        " `applicability` is ASVS's — because the two carry different"
        " identifiers. A third package adds a block and a reader here."
    ),
    "evals/verify_corpus.py": (
        "Five tables keyed by framework (record fields, record checks, lane"
        " accessor, ASVS-only chapter check, calibration-fixture input). All"
        " self-completing except the last, which is STRIDE's because a composed"
        " claim identity is (#167)."
    ),
}


def framework_literals() -> dict[str, list[tuple[int, str]]]:
    """Every non-test, non-package-local framework literal, by file."""
    found: dict[str, list[tuple[int, str]]] = {}
    for root in SEARCHED:
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            relative = path.relative_to(REPO_ROOT).as_posix()
            if "/frameworks/stride/" in relative or "/frameworks/asvs/" in relative:
                continue
            hits = [
                (number, line.strip())
                for number, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(), 1
                )
                if LITERAL.search(line) and not line.strip().startswith("#")
            ]
            if hits:
                found[relative] = hits
    return found


@pytest.fixture(scope="module")
def literals():
    return framework_literals()


def test_a_new_framework_literal_is_declared(literals):
    undeclared = sorted(set(literals) - set(DECLARED))
    assert not undeclared, (
        f"these files name a framework and nothing says why: {undeclared}."
        " Prefer a table keyed by framework — it raises when a package is"
        " missing, where a constant or a branch quietly does less. If the name"
        " is right, add the file to DECLARED with which of the two readings"
        " applies. See docs/agents/framework-parity.md."
    )


def test_the_declaration_does_not_rot(literals):
    """A file that stops naming a framework has to leave the list."""
    stale = sorted(set(DECLARED) - set(literals))
    assert not stale, (
        f"these files no longer name a framework and are still declared:"
        f" {stale}. Remove them."
    )


def test_every_declaration_gives_a_reason(literals):
    """An entry with no reason excuses nothing and teaches the next reader nothing."""
    thin = sorted(name for name, reason in DECLARED.items() if len(reason) < 40)
    assert not thin, f"these declarations need a real reason: {thin}"


def test_the_service_carries_no_framework_selection_of_its_own():
    """`src/` is the boundary the cutover got right, and it stays right.

    Everything under `src/stride_service/` outside the two package roots names a
    framework in exactly three places: the closed type, the two registry tables,
    and one demo entry point. A fourth is a job path choosing a framework for a
    caller, which is the caller's decision.
    """
    service = {
        name
        for name in framework_literals()
        if name.startswith("src/") and name not in {"src/stride_service/engine.py"}
    }

    assert service == {
        "src/stride_service/report.py",
        "src/stride_service/frameworks/__init__.py",
    }


@pytest.mark.parametrize("framework", ["stride", "asvs"])
def test_every_registered_package_is_a_declared_name(framework):
    """The lint's own vocabulary tracks the registry.

    :data:`LITERAL` spells the names, so a third package would be invisible to
    every check above until this fails and someone widens it.
    """
    assert LITERAL.search(f'"{framework}"'), (
        f"{framework} is registered and LITERAL does not match it, so this"
        " module is not checking it. Widen the pattern."
    )


def test_the_pattern_covers_the_whole_registry():
    """And the parametrize above covers the whole registry, not a fixed pair."""
    from stride_service.frameworks import PACKAGES

    assert set(PACKAGES) == {"stride", "asvs"}, (
        "PACKAGES has changed. Widen LITERAL and the parametrize above it, then"
        " re-read DECLARED: every entry reading 'this code is that framework's'"
        " is a dispatch a third package may need adding to."
    )


def test_every_package_has_an_instrument():
    """No package is carried without something measuring it.

    The instrument table declares which packages each entry reads. That makes a
    sweep skip an instrument it has no record for — the property that lets one
    framework run alone — but it is also the way a package could be carried and
    silently measured by nothing at all. A package that ran and reported no
    number is the failure this whole module exists for, so the declarations are
    checked against ``PACKAGES`` rather than trusted.
    """
    measured = {
        framework
        for instrument in INSTRUMENTS.values()
        for framework in instrument.frameworks
    }
    unmeasured = sorted(set(PACKAGES) - measured)
    assert not unmeasured, (
        f"these packages are carried and no instrument declares them:"
        f" {unmeasured}. A sweep that runs one would print and record nothing"
        " for it. Add an entry to evals.harness.instruments.INSTRUMENTS, or"
        " name the package on an existing entry's `frameworks`."
    )


def test_no_instrument_names_a_package_this_build_does_not_carry():
    """The other direction: a declaration that outlived its package."""
    declared = {
        framework
        for instrument in INSTRUMENTS.values()
        for framework in instrument.frameworks
    }
    unknown = sorted(declared - set(PACKAGES))
    assert not unknown, (
        f"instruments declare frameworks this build does not carry: {unknown}"
    )


def test_every_package_declares_a_scorer():
    """A carried package says what its own record is measured with.

    ``None`` is a legitimate answer — it declares that the framework-neutral
    instruments are the whole of the mechanical reading, which is true of a
    package whose per-case numbers pool rather than fold. A *missing* key is not an
    answer, and it is what an ``if`` naming one package left behind for every
    package written after it.
    """
    undeclared = sorted(set(PACKAGES) - set(PACKAGE_SCORERS))
    assert not undeclared, (
        f"these packages are carried and PACKAGE_SCORERS does not name them:"
        f" {undeclared}. Declare the per-case scorer their record earns, or"
        " `None` to say the neutral instruments are the whole of it."
    )


def test_no_scorer_names_a_package_this_build_does_not_carry():
    """The other direction: a scorer that outlived its package."""
    unknown = sorted(set(PACKAGE_SCORERS) - set(PACKAGES))
    assert not unknown, (
        f"PACKAGE_SCORERS names frameworks this build does not carry: {unknown}"
    )


# ---------------------------------------------------------------------------
# The lints, which the scan above puts out of scope.
#
# The module docstring says test files are excluded on purpose, and for tests in
# general that is right: a test naming the package it tests is the ordinary case.
# It is not right for one kind of test. A **lint** asserts a property of shipped
# text or config, and those properties are almost always claims about what an
# artifact *is* rather than about which framework wrote it — so a lint scoped to
# one package checks half the tree and reports a smaller, plausible pass.
#
# Two bugs made the case. #276: the token-cap lints walked ``frameworks/stride``,
# so ASVS's 17 lane skills and 17 exemplar files had no token lint at all. #280:
# twelve exemplar lints parametrized over ``STRIDE_CATEGORIES``, so the same 17
# exemplar files were checked for nothing else either — no reference resolution,
# no quote verification, no catalog membership. Neither raised. Both passed.
#
# **The signal is an import, not a literal.** Both bugs reached one package
# through ``from stride_service.frameworks.stride...``, and the literal scan
# above would have caught only the directory half of the first one. So this asks
# a different question of a smaller set of files, and asks it of the syntax tree
# rather than the text, because a docstring naming a framework is prose.
LINT_MODULES = sorted((REPO_ROOT / "tests").glob("test_*lints*.py"))

#: A lint importing one package's own module, and why that is right.
#:
#: **Empty, and that is the finding rather than an accident.** Every rule these
#: files assert turned out to be a rule about what an artifact is: a skill has
#: five non-empty sections, an exemplar's quotes verify, a cap alarms on drift.
#: None of them needed a package's own module once asked.
#:
#: An entry here is legitimate when the *rule itself* is one framework's — say,
#: a check over a field only one package's record declares. Write the reason as
#: a property of the framework, never as its name, exactly as ``DECLARED`` does.
DECLARED_LINT_IMPORTS: dict[str, str] = {}


def _package_imports(source: Path) -> list[str]:
    """Every ``stride_service.frameworks.<package>`` module ``source`` imports.

    Parsed rather than grepped. These files argue about frameworks by name in
    almost every docstring — the reasons #276 and #280 happened are written in
    them — and a text scan would drown the signal in its own explanation.
    """
    tree = ast.parse(source.read_text(encoding="utf-8"))
    return sorted(
        {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
            for name in PACKAGES
            if node.module.startswith(f"stride_service.frameworks.{name}")
        }
    )


def test_there_are_lint_modules_to_check():
    """Guards the guard: a glob that matches nothing agrees with anything."""
    assert len(LINT_MODULES) >= 5


@pytest.mark.parametrize("module", LINT_MODULES, ids=lambda path: path.name)
def test_a_lint_reads_no_single_packages_module(module):
    """A lint asserts what an artifact is, so it runs over every package.

    Failing here is not a demand to delete the import. It asks which of two
    things is true, and both have a home:

    * the rule holds for every package, so parametrize over ``PACKAGES`` and the
      import goes away — which is what #276 and #280 both turned out to be; or
    * the rule is genuinely one framework's, so declare it in
      :data:`DECLARED_LINT_IMPORTS` with the reason stated as a property of the
      framework rather than as its name.
    """
    relative = module.relative_to(REPO_ROOT).as_posix()
    imports = _package_imports(module)

    assert not imports or relative in DECLARED_LINT_IMPORTS, (
        f"{relative} imports {imports}, so it checks one package's tree. Either"
        f" parametrize it over PACKAGES, or declare it in"
        f" DECLARED_LINT_IMPORTS with the reason."
    )


def test_the_lint_declaration_does_not_rot():
    """A declaration for a lint that stopped importing a package is stale."""
    stale = [
        name for name in DECLARED_LINT_IMPORTS if not _package_imports(REPO_ROOT / name)
    ]
    assert not stale, f"declared but no longer package-scoped: {stale}"
