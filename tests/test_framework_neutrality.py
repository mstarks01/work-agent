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
  :data:`~analysis_service.frameworks.PACKAGES`, ``SCHEMAS``,
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
import importlib
import inspect
import pkgutil
import re
from pathlib import Path

import pytest

from analysis_service.frameworks import CONTENT_LICENSE, PACKAGES, SCHEMAS
from analysis_service.report import FRAMEWORK_NAMES
from evals.harness.calibration import IDENTITY_VALIDATION
from evals.harness.instruments import INSTRUMENTS, PACKAGE_SCORERS
from tests.factories import SCRIPTED_FRAMEWORKS

REPO_ROOT = Path(__file__).resolve().parents[1]
SEARCHED = ("src", "evals", "webapp")

#: A string literal naming a framework. Import paths and module names are not
#: matched — ``analysis_service`` is this distribution's name, not a package
#: selection, and ``frameworks.stride.record`` is a module.
#:
#: **The name may sit anywhere inside the string, and in any case.** The pattern
#: used to be ``"(?:stride|asvs)"`` — the exact quoted token — which is a
#: framework selection and nothing else. That read past every framework name
#: embedded in a sentence, and one of them was served:
#: ``FastAPI(title="STRIDE Threat-Modeling Service")`` put a package's name in
#: the OpenAPI document every caller reads, on an install carrying two. This is
#: the third blind spot in this scan, after the two #284 found, and each one was
#: a way of naming a framework the pattern could not spell.
LITERAL = re.compile(r'"[^"]*\b(?:stride|asvs)\b[^"]*"', re.IGNORECASE)

#: A framework's name anywhere inside a word, which is what :data:`LITERAL`
#: cannot see. ``Engine`` and ``analysis_pipeline`` are both a framework
#: name on a thing that serves every framework, and neither is a string
#: literal, so the scan above ran past them for two packages.
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

#: A framework's name inside a word. Nothing has to be stripped first: the
#: distribution, the package and the environment prefix all name what this is
#: rather than which framework it started as, so any hit here is a real one.
IN_WORD = re.compile(r"stride|asvs", re.IGNORECASE)

#: Where a framework literal is allowed, and why. Two readings live here and the
#: reason has to say which:
#:
#: **"a table keyed by framework"** — self-completing. A package added to
#: :data:`~analysis_service.frameworks.PACKAGES` and missing here raises at the
#: first call, so nothing can be silently half-done.
#:
#: **"this code is that framework's"** — a framework-specific instrument naming
#: its own package. Legitimate, and the thing to check when a third package
#: lands is whether the *dispatch to it* is a table or a branch.
#:
#: An entry that is neither is a gap. There are none today; if one appears, say
#: so in the reason rather than filing it beside the legitimate ones.
DECLARED: dict[str, str] = {
    "src/analysis_service/report.py": (
        "The registry. `FrameworkName` is the closed type every other module"
        " reads, so this is the one place the names are spelled at all."
    ),
    "src/analysis_service/frameworks/__init__.py": (
        "Tables keyed by framework: PACKAGES and SCHEMAS. Self-completing — a"
        " package missing from either raises at `package_for`."
    ),
    "src/analysis_service/engine.py": (
        "A module-level demo entry point selecting one framework to run, the"
        " way a caller does. Not a code path any job takes."
    ),
    "evals/calibration_labels/build_pairs.py": (
        "This code is that framework's. Labelled candidate pairs exist only for"
        " a package whose claim set is open prose, because only a labelled pair"
        " can say whether two spellings name one action — `IDENTITY_VALIDATION`"
        " carries that as `needs_candidate_pairs`. A package matching by catalog"
        " identifier contributes no pair, so there is nothing here to key."
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
    "evals/migrations/2026-08-30-asvs-verb.py": (
        "This code is ASVS's, and it is a one-time repair of archived data"
        " rather than a rule the service runs. A framework whose claims name a"
        " catalog requirement composes no action verb, so a stored verb on one"
        " of its claims is a field nothing reads; the migration nulls exactly"
        " those, and reads each claim's own `framework` rather than the file it"
        " sits in, so a package that does compose a verb keeps every one."
    ),
    "evals/harness/pairing.py": (
        "This code is ASVS's, and reads the same catalog `applicability.py`"
        " scores against. A framework whose claims name a catalog requirement"
        " can disagree with a reference set about whether a requirement"
        " applies, and settling that needs the standard's own text beside the"
        " claim's argument. A framework whose claim set is open has no catalog"
        " text to pair, so nothing here answers for one."
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
    "webapp/review.py": (
        "QUESTIONS is a table keyed by framework, checked against PACKAGES at"
        " import: a package without a reviewer question fails the app rather"
        " than asking about its records in another package's words. Each entry"
        " asks what that package's records rule on, which is the browser's half"
        " of `evals/build_review_docs.py`'s RENDERERS."
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
    "evals/harness/identity.py": (
        "This code is STRIDE's, and carries no literal — it reaches the package"
        " through `StrideCategory` on `ClaimPair`. The rule asks whether two"
        " claims name the same attacker action against the same target, which"
        " is the identity an open claim set composes. A framework whose claims"
        " name a catalog requirement is keyed by that requirement instead, and"
        " its matcher is the confusion matrix in `applicability.py`."
    ),
    "evals/harness/calibration.py": (
        "IDENTITY_VALIDATION is a table keyed by framework and self-completing:"
        " a package missing from it raises at `measure_merges` rather than"
        " answering zero collisions. Its entries follow from the claim type —"
        " an open prose claim set needs labelled candidate pairs, because only"
        " a pair says whether two spellings name one action; a claim naming a"
        " catalog requirement needs none, because the identifier decides"
        " equivalence. Every claim type declares a collision rule, since keying"
        " two distinct claims alike destroys a finding whatever the identity is"
        " composed from. The `LabelledPair` fields around it are STRIDE's pair"
        " set, which is the only one any package has."
    ),
    "evals/harness/critic_yield.py": (
        "This code is STRIDE's, through `DraftThreat` and a category. Yield is"
        " the pair of counts over drafts a critic killed and references it"
        " destroyed, which only a package whose lane agents propose an open"
        " claim set has. `INSTRUMENTS` already declares the instrument"
        ' `frameworks=("stride",)`; this is the module behind it.'
    ),
    "evals/verify_corpus.py": (
        "Five tables keyed by framework (record fields, record checks, lane"
        " accessor, ASVS-only chapter check, calibration-fixture input). All"
        " self-completing except the last, which is STRIDE's because a composed"
        " claim identity is (#167)."
    ),
}


#: A framework's name on a service-wide thing, kept **by decision** rather than
#: because it is right. Keyed by the name, not by the file, so one decision
#: costs one row however many modules spell it.
#:
#: **Empty, and that is the state to keep it in.** Nothing outside a package
#: root carries a framework's name today, so the check below is absolute rather
#: than a rule with exceptions. A row here buys time for a rename that is bigger
#: than the pull request finding it — public surface, a recorded identifier — and
#: it is a debt, not a permission.
#:
#: This is not a second exemption list for :data:`DECLARED`. An entry there says
#: the code genuinely belongs to one framework; an entry here says the code
#: belongs to all of them and carries one framework's name anyway.
OPEN_BY_DECISION: dict[str, str] = {}


def _docstrings(tree: ast.AST) -> set[int]:
    """Every string node id that is a docstring rather than a value.

    **This check reads code, not prose.** A docstring naming
    ``analyze_stride_spoofing`` shows a reader what the graph generates, which
    is the opposite of a problem: the generated name carries the framework on
    purpose. What the check is for is a *thing* — a class, a function, a
    default value — that serves every framework under one framework's name.
    Comments are skipped for the same reason, and by ``ast`` never seeing them.
    """
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                found.add(id(body[0].value))
    return found


def _framework_named_words(source: Path) -> set[str]:
    """Every declared name and value string in ``source`` carrying a framework.

    A bare framework name is not one: ``"stride"`` as a key or a value is what
    :data:`LITERAL` already reads, and it is the spelling a table is keyed by.
    """
    tree = ast.parse(source.read_text(encoding="utf-8"))
    docstrings = _docstrings(tree)
    words: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            words.add(node.name)
        elif isinstance(node, ast.Name):
            words.add(node.id)
        elif isinstance(node, ast.Attribute):
            words.add(node.attr)
        elif isinstance(node, ast.arg):
            words.add(node.arg)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            words.update(IDENTIFIER.findall(node.value))
    return {
        word for word in words if IN_WORD.search(word) and word.lower() not in PACKAGES
    }


def framework_named_identifiers() -> dict[str, set[str]]:
    """Every framework-named name or value outside a package, by file."""
    found: dict[str, set[str]] = {}
    for root in SEARCHED:
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            relative = path.relative_to(REPO_ROOT).as_posix()
            if any(f"/frameworks/{name}/" in relative for name in PACKAGES):
                continue
            words = _framework_named_words(path)
            if words:
                found[relative] = words
    return found


#: Where a browser gets its words from. Markup assigned to a module-level name,
#: the ``title=`` an app is constructed with, and the standalone templates.
PAGES = ("webapp",)

#: A comment inside a page's own script or style. Skipped for the reason the
#: Python scan skips ``#`` lines: a comment comparing two packages explains the
#: code, and no reader of the app ever sees it.
PAGE_COMMENT = re.compile(r"^\s*(?://|/\*|\*)")


def page_text() -> dict[str, list[tuple[int, str]]]:
    """Every line of text an app puts in front of a person, by file.

    **The surface the literal scan could not reach, and the one that was
    wrong.** ``webapp/`` served a heading reading "STRIDE threat model" over a
    form that offers every carried framework, so a job naming ASVS alone got its
    answer under another framework's name. No check could see it: the heading is
    prose inside a longer string, not a ``"stride"`` literal, and ``webapp/`` was
    not searched at all.

    A framework's name reaches a page through the report, never through a
    constant. Anything a package's records rule on differently — the question a
    reviewer answers, say — is a table keyed by framework, read per finding.
    """
    found: dict[str, list[tuple[int, str]]] = {}
    for root in PAGES:
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            lines = _rendered_lines(path)
            if lines:
                found[path.relative_to(REPO_ROOT).as_posix()] = lines
        for path in sorted((REPO_ROOT / root).rglob("*.html")):
            relative = path.relative_to(REPO_ROOT).as_posix()
            found[relative] = list(
                enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            )
    return found


def _rendered_lines(source: Path) -> list[tuple[int, str]]:
    """Markup constants and app titles in one module, as numbered lines."""
    tree = ast.parse(source.read_text(encoding="utf-8"))
    lines: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for value in ast.walk(node.value):
                if (
                    isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                    and "<" in value.value
                    and ">" in value.value
                ):
                    lines += [(node.lineno, line) for line in value.value.splitlines()]
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "FastAPI":
            lines += [
                (node.lineno, keyword.value.value)
                for keyword in node.keywords
                if keyword.arg == "title"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
            ]
    return lines


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


def package_importers() -> dict[str, list[str]]:
    """Every non-test module outside a package that imports that package's own.

    **The other half of the signal**, and the half `LITERAL` cannot see. A
    module reaches one framework either by naming it or by importing its
    record, and three modules did the second with no literal at all —
    `identity.py`, `calibration.py` and `critic_yield.py` were correct and
    undeclared, which is a reason nobody had written down.

    It is the same signal `test_a_lint_reads_no_single_packages_module` reads
    over the lints, asked here of the code the sweep already covers. Parsed
    rather than grepped, for the reason given there.
    """
    found: dict[str, list[str]] = {}
    for root in SEARCHED:
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            relative = path.relative_to(REPO_ROOT).as_posix()
            if any(f"/frameworks/{name}/" in relative for name in PACKAGES):
                continue
            imports = _package_imports(path)
            if imports:
                found[relative] = imports
    return found


@pytest.fixture(scope="module")
def literals():
    return framework_literals()


def test_a_module_reaching_one_package_by_import_is_declared():
    """Naming a framework and importing one are the same commitment.

    ``DECLARED`` answers both, because the question a reader has is the same:
    this module is not neutral, so why is that right? A module that reaches a
    package through its record and says nothing is the shape #276 and #280
    took one directory over.
    """
    undeclared = {
        name: imports
        for name, imports in package_importers().items()
        if name not in DECLARED
    }

    assert not undeclared, (
        f"these modules import one package's own module and are not declared: "
        f"{undeclared}. Add an entry to DECLARED saying why, as a property of "
        f"the framework rather than as its name."
    )


def test_a_new_framework_literal_is_declared(literals):
    undeclared = sorted(set(literals) - set(DECLARED))
    assert not undeclared, (
        f"these files name a framework and nothing says why: {undeclared}."
        " Prefer a table keyed by framework — it raises when a package is"
        " missing, where a constant or a branch quietly does less. If the name"
        " is right, add the file to DECLARED with which of the two readings"
        " applies. See docs/agents/framework-parity.md."
    )


def test_a_framework_named_thing_is_declared_or_open(literals):
    """A class or a value may not carry a framework's name unexplained.

    The half :data:`LITERAL` is blind to. ``Engine`` and
    ``analysis_pipeline`` are a framework's name on something every framework
    uses, and neither is a string literal, so the scan above walked past both
    while two packages shipped. A name is fine here on one of two showings: its
    file is in :data:`DECLARED`, so the code is that framework's, or the name is
    in :data:`OPEN_BY_DECISION`, so somebody decided to keep it and said why.
    """
    unexplained = {
        name: sorted(
            word
            for word in words
            if not any(word.startswith(open_) for open_ in OPEN_BY_DECISION)
        )
        for name, words in framework_named_identifiers().items()
        if name not in DECLARED
    }
    unexplained = {name: words for name, words in unexplained.items() if words}

    assert not unexplained, (
        f"these name a framework on a thing that serves every framework:"
        f" {unexplained}. Rename it, or add the file to DECLARED if the code is"
        " really that package's, or add the name to OPEN_BY_DECISION with the"
        " reason it stays. See docs/agents/framework-parity.md."
    )


def test_every_open_decision_is_still_open():
    """A name renamed away has to leave the list, or it excuses a fresh one."""
    live = set()
    for words in framework_named_identifiers().values():
        live.update(words)
    stale = sorted(
        open_
        for open_ in OPEN_BY_DECISION
        if not any(word.startswith(open_) for word in live)
    )
    assert not stale, (
        f"these names are gone and are still recorded as open: {stale}."
        " Remove the row — an open decision nobody can act on excuses the next"
        " name that spells it."
    )


def test_every_open_decision_gives_a_reason():
    thin = sorted(name for name, why in OPEN_BY_DECISION.items() if len(why) < 40)
    assert not thin, f"these open decisions need a real reason: {thin}"


def test_the_declaration_does_not_rot(literals):
    """A file that stops reaching one framework has to leave the list.

    Either way of reaching one keeps it here. A module can name a package or
    import its record, and a declaration is stale only when it does neither —
    otherwise removing a literal from a module that still imports the record
    would drop the reason and the entry together.
    """
    reaching = set(literals) | set(package_importers())
    stale = sorted(set(DECLARED) - reaching)
    assert not stale, (
        f"these files no longer name or import a framework and are still"
        f" declared: {stale}. Remove them."
    )


def test_every_declaration_gives_a_reason(literals):
    """An entry with no reason excuses nothing and teaches the next reader nothing."""
    thin = sorted(name for name, reason in DECLARED.items() if len(reason) < 40)
    assert not thin, f"these declarations need a real reason: {thin}"


def test_the_service_carries_no_framework_selection_of_its_own():
    """`src/` is the boundary the cutover got right, and it stays right.

    Everything under `src/analysis_service/` outside the two package roots names a
    framework in exactly three places: the closed type, the two registry tables,
    and one demo entry point. A fourth is a job path choosing a framework for a
    caller, which is the caller's decision.
    """
    service = {
        name
        for name in framework_literals()
        if name.startswith("src/") and name not in {"src/analysis_service/engine.py"}
    }

    assert service == {
        "src/analysis_service/report.py",
        "src/analysis_service/frameworks/__init__.py",
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
    from analysis_service.frameworks import PACKAGES

    assert set(PACKAGES) == {"stride", "asvs"}, (
        "PACKAGES has changed. Widen LITERAL and the parametrize above it, then"
        " re-read DECLARED: every entry reading 'this code is that framework's'"
        " is a dispatch a third package may need adding to."
    )


def test_every_package_has_a_scripted_fixture():
    """The offline run of the real graph covers every pair of packages.

    ``tests.factories.SCRIPTED_FRAMEWORKS`` is the table that run reads, so a
    package missing from it is a package the scheduler test never runs, and
    the timing windows between two frameworks go unexercised for it.
    """
    assert set(SCRIPTED_FRAMEWORKS) == set(PACKAGES), (
        "SCRIPTED_FRAMEWORKS and PACKAGES disagree: add one ScriptedFramework"
        " per carried package to tests/factories.py, and carry no other."
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


def test_every_package_declares_what_identity_validation_it_needs():
    """The contract runs every way rather than outward from STRIDE.

    A package that ships without an entry has no collision measurement, and a
    collision destroys a finding whatever a claim composes its identity from.
    A *missing* key is not an answer.
    """
    undeclared = sorted(set(PACKAGES) - set(IDENTITY_VALIDATION))
    assert not undeclared, (
        f"these packages are carried and IDENTITY_VALIDATION does not name"
        f" them: {undeclared}. Say what the claim type composes its identity"
        " from, whether a labelled pair set can price a rule on it, and what"
        " would make two distinct claims one finding."
    )
    unknown = sorted(set(IDENTITY_VALIDATION) - set(PACKAGES))
    assert not unknown, (
        f"IDENTITY_VALIDATION names frameworks this build does not carry: {unknown}"
    )


def test_each_identity_contract_argues_from_the_claim_type():
    """``why`` must describe the claim, never name the package.

    A reason that says "ASVS does not need pairs" answers for one package. A
    reason that says "a claim naming a catalog requirement is identified by it"
    answers for every package written after it, too.
    """
    for package, contract in IDENTITY_VALIDATION.items():
        assert contract.why, f"{package} declares no reason"
        named = sorted(name for name in PACKAGES if name in contract.why.lower())
        assert not named, (
            f"{package}'s reason names {named}. State the property of the claim"
            " type that decides it, so it answers for a package nobody has"
            " written yet."
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
# through ``from analysis_service.frameworks.stride...``, and the literal scan
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
    """Every ``analysis_service.frameworks.<package>`` module ``source`` imports.

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
            if node.module.startswith(f"analysis_service.frameworks.{name}")
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


def test_every_package_declares_a_content_license():
    """No package ships text without saying which licence governs it.

    A package that quotes a published standard inherits that standard's licence.
    Nothing in the tree makes that visible: the requirement sentences read like
    any other prompt text, and a wheel that ships them under the wrong licence
    builds and passes. So the table is the record, and a missing key is the only
    thing that can raise.
    """
    missing = sorted(set(PACKAGES) - set(CONTENT_LICENSE))

    assert not missing, (
        f"{missing} is registered and declares no content licence. Add an entry"
        " to CONTENT_LICENSE: the repo licence if the package's text is written"
        " here, or the standard's licence if it reproduces one."
    )


def test_the_content_license_table_names_no_package_this_build_lacks():
    """And a key for a package that left is stale rather than harmless."""
    stale = sorted(set(CONTENT_LICENSE) - set(PACKAGES))

    assert not stale, f"declared a content licence for unregistered: {stale}"


def test_notice_names_every_package_licensed_apart_from_the_repo():
    """A licence the repo's own does not cover needs its attribution shipped.

    CC BY-SA and CC BY both ask for attribution, and ``NOTICE`` is where this
    distribution carries it -- ``pyproject.toml`` puts that file in the wheel.
    A package whose text arrives under a different licence with no NOTICE entry
    is a distribution that breaks the terms it was given.
    """
    notice = (REPO_ROOT / "NOTICE").read_text()
    unattributed = sorted(
        name
        for name, license_id in CONTENT_LICENSE.items()
        if license_id != "Apache-2.0" and license_id not in notice
    )

    assert not unattributed, (
        f"{unattributed} ships text under a licence NOTICE does not name."
        " Add the upstream project, its copyright, its licence and the files"
        " it governs."
    )


def test_there_are_pages_to_check():
    """Guards the guard: an AST walk that finds no markup agrees with anything."""
    assert sum(len(lines) for lines in page_text().values()) > 200


def test_no_page_names_a_framework():
    """What a person reads is the report's word, never the template's.

    This is the check that was missing. Every app here serves whatever
    frameworks the install carries, so a framework's name baked into a heading,
    a title or a button is wrong for every job that did not name it.
    """
    named = {
        name: [
            (number, line.strip())
            for number, line in lines
            if IN_WORD.search(line) and not PAGE_COMMENT.search(line)
        ]
        for name, lines in page_text().items()
    }
    named = {name: hits for name, hits in named.items() if hits}

    assert not named, (
        f"these put a framework's name in front of a person: {named}. A page"
        " serves every framework the install carries, so the name has to come"
        " from the finding being shown — through a table keyed by framework"
        " where the wording differs, as webapp/review.py's QUESTIONS does."
    )


#: Every hook a package may override, and the attribute the *caller* reaches it
#: through. A package's own class is not the seam — the seam is whatever the
#: service asks for, and an override anywhere else resolves to the neutral
#: default.
NEUTRAL_HOOKS: dict[str, str] = {
    "partition_proposals": "record",
    "claim_marks": "record",
    "lane_diagnostics": "record",
    "misfiled": "record",
    "ruled_out": "record",
    "settled_by_grounds": "record",
    "unit_of": "record",
    "rating_of": "record",
    "scope_entries": "block",
    "summarize": "block",
}


def _package_modules(name: str):
    """Every module of one package, so a definition anywhere is found."""
    package = importlib.import_module(f"analysis_service.frameworks.{name}")
    yield package
    for found in pkgutil.iter_modules(package.__path__):
        yield importlib.import_module(
            f"analysis_service.frameworks.{name}.{found.name}"
        )


def orphaned_overrides() -> dict[str, list[str]]:
    """Hooks a package defines that the caller never reaches, by package.

    **The failure this exists for is silent and expensive.** A package writing
    an override on one of its own classes has said what it wants; if that is not
    the class the service asks, Python resolves the neutral default and the
    package's intent is dropped with no error, no warning and no test failure.
    It shows up only as behaviour that never happens.

    That is not hypothetical. ASVS's ``partition_proposals`` sat on its
    *analysis block* while the fan-in asks its *record*, so two live runs
    deferred nothing and read as a model that would not answer. The tests missed
    it by calling the override on the same wrong class: a test that names a
    class the caller never reaches proves the method works, not that it runs.
    """
    found: dict[str, list[str]] = {}
    for name in sorted(PACKAGES):
        reached_by = {"record": PACKAGES[name].record, "block": SCHEMAS[name].block}
        home = f"analysis_service.frameworks.{name}"
        for hook, attribute in NEUTRAL_HOOKS.items():
            defines = any(
                hook in vars(obj)
                for module in _package_modules(name)
                for _, obj in inspect.getmembers(module, inspect.isclass)
                if obj.__module__.startswith(home)
            )
            if not defines:
                continue
            reached = getattr(reached_by[attribute], hook)
            function = getattr(reached, "__func__", reached)
            module = inspect.getmodule(function)
            if module is None or not module.__name__.startswith(home):
                found.setdefault(name, []).append(hook)
    return found


def test_no_package_override_is_orphaned():
    orphaned = orphaned_overrides()

    assert not orphaned, (
        f"these packages define a hook the caller never reaches: {orphaned}."
        " The service asks `package.record` for some and the analysis block for"
        " others — see NEUTRAL_HOOKS. An override on any other class of the"
        " package resolves to the neutral default, silently, and the package's"
        " intent is dropped."
    )


def test_the_hook_table_names_only_real_hooks():
    """Guards the guard: a hook nothing declares can never be orphaned."""
    from analysis_service.report import Claim, FrameworkAnalysis

    for hook, attribute in NEUTRAL_HOOKS.items():
        base = Claim if attribute == "record" else FrameworkAnalysis
        assert hasattr(base, hook), f"{hook} is not a neutral hook on {base.__name__}"


# ---------------------------------------------------------------------------
# The shared instruction surface.
#
# Everything above reads ``*.py``. That left 93,546 tokens of prompt and skill
# text unscanned — the one surface where naming a framework does not merely
# mislead a reader but instructs a model. ``prompts/critic.md`` told every
# package's critic it was reviewing "Six category agents" and ruling on "draft
# threats" for the life of the ASVS package, and nothing here could see it.
#
# ``prompts/`` and ``domains/`` are shared: one copy reaches every framework, so
# neither may name one. ``frameworks/<name>/`` is that package's own text and is
# exempt for the same reason its modules are.

SHARED_MARKDOWN = ("prompts", "domains")

#: A word that turns a mention into a classification. A pack may say what a
#: technology does; it may not file the result under somebody's lane.
FINDING_WORD = r"(?:finding|threat|claim|category|lane|verdict)"

#: How a lane count gets written. Derived from the packages, so a package with a
#: new lane count is covered on the day it registers.
COUNT_WORDS = {
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
    17: "seventeen",
}

#: What a lane count is counting, where one appears.
COUNTED_UNIT = r"(?:lane|agent|categor|chapter)"


def shared_markdown() -> list[Path]:
    """Every shared instruction file: one copy, read by every framework."""
    found = [
        path
        for root in SHARED_MARKDOWN
        for path in sorted((REPO_ROOT / root).rglob("*.md"))
    ]
    assert found, "the shared-markdown scan found no files, so it proves nothing"
    return found


def lane_names() -> list[str]:
    return sorted({lane for package in PACKAGES.values() for lane in package.lanes})


def lane_counts() -> set[str]:
    spellings = set()
    for package in PACKAGES.values():
        count = len(package.lanes)
        spellings.add(str(count))
        if count in COUNT_WORDS:
            spellings.add(COUNT_WORDS[count])
    return spellings


def test_no_shared_instruction_file_names_a_framework():
    """A framework's name in text every framework reads.

    Self-completing: the names come from ``FRAMEWORK_NAMES``, so a package
    registered tomorrow is covered with no edit here.
    """
    pattern = re.compile(rf"\b(?:{'|'.join(FRAMEWORK_NAMES)})\b", re.IGNORECASE)
    named = [
        f"{path.relative_to(REPO_ROOT)}:{number}"
        for path in shared_markdown()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert not named, (
        f"these shared instruction lines name a framework: {named}. One copy of"
        " this text reaches every framework, so a sentence naming one is false"
        " in the others. Move it to that package's own text under frameworks/."
    )


def test_no_shared_instruction_file_files_a_finding_under_a_lane():
    """A shared pack may not assign a technology's failure to somebody's lane.

    This is the check ``analysis_service.skills`` has always described and
    nothing implemented. Its word list comes from the packages' own ``lanes``
    members rather than from a list somebody maintains, so a package that
    declares a new lane is covered on the day it registers.

    A lane name on its own is not the fault — ``authentication`` is an ordinary
    word and four packs say it. The fault is a lane name doing the work of a
    classification, which is what the adjacent finding word catches.
    """
    filed = []
    for lane in lane_names():
        word = lane.replace("-", "[- ]")
        near = re.compile(
            rf"\b{word}\b[^.]{{0,40}}\b{FINDING_WORD}\b"
            rf"|\b{FINDING_WORD}\b[^.]{{0,40}}\b{word}\b",
            re.IGNORECASE,
        )
        filed += [
            f"{path.relative_to(REPO_ROOT)}:{number} ({lane})"
            for path in shared_markdown()
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            )
            if near.search(line)
        ]
    assert not filed, (
        f"these shared instruction lines file something under a package's lane:"
        f" {filed}. State what the technology does and let each framework file"
        " it, or move the sentence into that package's own text."
    )


def test_no_shared_instruction_file_counts_a_packages_lanes():
    """A lane count in shared text is one package's shape stated as everyone's.

    This is the check that would have caught the defect: ``prompts/critic.md``
    said "Six category agents worked in parallel" to a package that runs 17
    chapters. A count is derived from ``PACKAGES``, so it moves when a package's
    lane list does.
    """
    pattern = re.compile(
        rf"\b(?:{'|'.join(sorted(lane_counts()))})\b[^.]{{0,30}}\b{COUNTED_UNIT}",
        re.IGNORECASE,
    )
    counted = [
        f"{path.relative_to(REPO_ROOT)}:{number}"
        for path in shared_markdown()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert not counted, (
        f"these shared instruction lines count one package's lanes: {counted}."
        " Every framework reads this text and they declare different numbers of"
        " lanes. Say what the agents do, not how many there are."
    )
